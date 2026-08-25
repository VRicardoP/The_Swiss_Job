"""Candidaturas y bookmarks: lógica de negocio de C-4 (DISEÑO v2.1).

El endpoint /v1 es una capa fina; aquí vive:
- La CASCADA de vínculo a vacante (Decisión 3): (a) vacancy_id directo
  validado presentable + cadena merged_into seguida al ganador; (b) resolución
  por URL en TODAS las fuentes (sin scope de fuente — el helper del import
  queda scopeado a SU contexto); (c) síntesis en `portfolio-import` por el
  camino ya entregado del import; (d) candidatura manual SIN url con
  external_id determinista alternativo y URL sintética interna del sink.
- La creación de application con evento inicial VIVO en
  `application_status_events` + outbox `application.status_changed` y
  `revision` monotónica (Decisión 6), y el upsert de saved_at cuando el
  status es `saved` (Decisión 4: bookmark = application con status=saved).
- La composición del GET (applications + bookmarks PUROS) con UN reloj y UNA
  identidad (Decisión 10): ts = created_at | saved_at, id = application.id |
  vacancy_id, orden (ts DESC, id DESC), rama bookmark con NOT EXISTS.
- La precedencia snapshot-primero de los campos presentables (Decisión 5):
  clave PRESENTE en snapshot prima aunque valga null; ausente → corpus.
"""

import hashlib
import json
import logging
import uuid

import sqlalchemy as sa

from jobhunt_core import matching, outbox
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.import_portfolio import (
    durable_synthesizable,
    ensure_import_scope,
    normalized_key,
    resolve_vacancy_by_url,
    synthesize_vacancies,
)

logger = logging.getLogger(__name__)

STATUS_EVENT = "application.status_changed"
SAVED_STATUS = "saved"
# Claves del snapshot (Decisión 3/5): lo que el usuario vio, inmutable.
SNAPSHOT_KEYS = ("title", "company", "url", "source", "description")
# Cota del bucle de merged_into (Decisión 3a: "bucle acotado").
MERGE_CHAIN_MAX = 20
# URL sintética interna del sink para la candidatura manual (Decisión 3d):
# solo clave de idempotencia del slot; JAMÁS se presenta (snapshot url: null).
MANUAL_URL_PREFIX = "https://portfolio-import.invalid/manual/"


class LinkError(Exception):
    """La cascada no pudo producir un vacancy_id (url no sintetizable,
    colisión de clave normalizada...). El endpoint la mapea a 400."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- ownership


async def profile_owner(session, profile_id, consumer_id) -> str | None:
    """Nombre del consumer si el perfil es del tenant; None si cross-tenant o
    ausente (el endpoint responde 404 INDISTINGUIBLE — Decisión 7)."""
    return (
        await session.execute(
            sa.text(
                "SELECT c.name FROM profiles p "
                "JOIN consumers c ON c.id = p.consumer_id "
                "WHERE p.id = :pid AND p.consumer_id = :cid"
            ),
            {"pid": profile_id, "cid": consumer_id},
        )
    ).scalar_one_or_none()


# ------------------------------------------------------- cascada Decisión 3


async def _merge_winner(session, vacancy_id) -> uuid.UUID | None:
    """Sigue la cadena merged_into (bucle ACOTADO) hasta un ganador
    presentable; None si la cadena muere, se pasa de cota o el ganador está
    archivado — jamás se enlaza a ciegas."""
    vid = vacancy_id
    for _ in range(MERGE_CHAIN_MAX):
        row = (
            await session.execute(
                sa.text(
                    "SELECT merged_into, archived_at FROM vacancies WHERE id = :v"
                ),
                {"v": vid},
            )
        ).one_or_none()
        if row is None:
            return None
        if row.merged_into is None:
            return vid if row.archived_at is None else None
        vid = row.merged_into
    logger.warning(
        "applications: cadena merged_into > %d saltos desde %s — no se enlaza",
        MERGE_CHAIN_MAX, vacancy_id,
    )
    return None


async def resolve_direct(session, vacancy_id) -> uuid.UUID | None:
    """(a) vacancy_id directo: existe y PRESENTABLE (archived_at IS NULL); si
    tiene merged_into se sigue la cadena al ganador. None → 404 del endpoint
    (indistinguible de cross-tenant)."""
    row = (
        await session.execute(
            sa.text("SELECT merged_into, archived_at FROM vacancies WHERE id = :v"),
            {"v": vacancy_id},
        )
    ).one_or_none()
    if row is None or row.archived_at is not None:
        return None
    if row.merged_into is None:
        return vacancy_id
    return await _merge_winner(session, row.merged_into)


async def resolve_by_url_any_source(session, url: str) -> uuid.UUID | None:
    """(b) resolución por URL en TODAS las fuentes (R2-1: sin scope): misma
    guarda que el import — incarnación ACTIVA + vacante no archivada; una
    fundida sigue merged_into al ganador. Varias filas (misma URL en fuentes
    distintas, pre-dedup) → gana la incarnación activa más reciente
    (determinista: last_seen_at DESC, id DESC)."""
    urln = normalized_key(url)
    if urln is None:
        return None
    row = (
        await session.execute(
            sa.text(
                "SELECT i.vacancy_id, v.merged_into FROM source_listings sl "
                "JOIN source_listing_incarnations i "
                "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
                "JOIN vacancies v "
                "  ON v.id = i.vacancy_id AND v.archived_at IS NULL "
                "WHERE sl.url_normalized = :urln "
                "ORDER BY i.last_seen_at DESC, i.id DESC LIMIT 1"
            ),
            {"urln": urln},
        )
    ).first()
    if row is None:
        return None
    if row.merged_into is None:
        return row.vacancy_id
    return await _merge_winner(session, row.merged_into)


async def _synthesize_by_url(session, item: dict) -> uuid.UUID:
    """(c) síntesis en portfolio-import — camino YA ENTREGADO del import
    (external_id = sha256(url normalizada)), en la MISMA tx del POST."""
    scope_id = await ensure_import_scope(session)
    collided = await synthesize_vacancies(session, scope_id, [item])
    vid = await resolve_vacancy_by_url(session, item["url"])
    if vid is None:
        raise LinkError("collision" if collided else "unsynthesizable")
    return vid


def manual_external_id(profile_id, title: str, company: str | None) -> str:
    """external_id determinista alternativo de la candidatura manual
    (Decisión 3d): sha256("manual:" + profile_id + título_normalizado +
    company_normalizada). Normalización = colapso de espacios + lower —
    reintento del mismo POST ⇒ mismo slot."""
    t = " ".join((title or "").split()).lower()
    c = " ".join((company or "").split()).lower()
    return hashlib.sha256(f"manual:{profile_id}:{t}:{c}".encode()).hexdigest()


async def _synthesize_manual(
    session, profile_id, title: str, company: str | None, description: str | None
) -> uuid.UUID:
    """(d) candidatura manual SIN url: síntesis con el external_id alternativo
    y la URL sintética interna del sink (jamás presentada)."""
    scope_id = await ensure_import_scope(session)
    external_id = manual_external_id(profile_id, title, company)
    url = MANUAL_URL_PREFIX + external_id
    listing = RawListing(
        external_id=external_id,
        url=url,
        payload={
            "title": title, "company_name": company,
            "description": description, "tags": [],
        },
    )
    await RawListingSink().handle(session, str(scope_id), (listing,))
    vid = await resolve_vacancy_by_url(session, url)
    if vid is None:
        raise LinkError("unsynthesizable")
    return vid


async def link_vacancy(
    session,
    profile_id,
    *,
    vacancy_id=None,
    url: str | None = None,
    title: str,
    company: str | None = None,
    description: str | None = None,
) -> uuid.UUID | None:
    """Cascada completa de la Decisión 3. None SOLO en el camino (a) (el
    endpoint responde 404 indistinguible); los caminos (b)-(d) resuelven o
    lanzan LinkError (→ 400). Con vacancy_id NO se consulta la URL."""
    if vacancy_id is not None:
        return await resolve_direct(session, vacancy_id)
    if url is not None:
        item = {"url": url, "title": title, "company": company,
                "description": description}
        # FRONTERA del sink ANTES de tocar la BD (misma partición que el
        # import): una url con NUL/surrogate jamás vive en source_listings
        # (el sink la cuarentena) y como bind-param reventaría la query.
        ok, reason = durable_synthesizable(item)
        if not ok:
            raise LinkError(reason or "malformed")
        vid = await resolve_by_url_any_source(session, url)
        if vid is not None:
            return vid
        return await _synthesize_by_url(session, item)
    return await _synthesize_manual(session, profile_id, title, company, description)


# ------------------------------------------------- eventos vivos (Decisión 6)


async def record_status_event(
    session, *, application_id, profile_id, vacancy_id, status: str,
    revision: int, destination: str,
) -> None:
    """Evento VIVO en application_status_events + outbox del catálogo
    (`application.status_changed`, event_id = uuid5(application_id + status +
    version)) — SIEMPRE en la misma tx que la mutación (R2-7). `notes` es PII:
    jamás viaja en el payload ni en logs."""
    await session.execute(
        sa.text(
            "INSERT INTO application_status_events (id, application_id, status) "
            "VALUES (:id, :aid, :st)"
        ),
        {"id": uuid.uuid4(), "aid": application_id, "st": status},
    )
    await outbox.emit(
        session,
        event_type=STATUS_EVENT,
        natural_key=f"{application_id}:{status}:{revision}",
        aggregate="application",
        aggregate_id=str(application_id),
        subject_profile_id=profile_id,
        version=revision,
        payload={
            "application_id": str(application_id),
            "profile_id": str(profile_id),
            "vacancy_id": str(vacancy_id),
            "status": status,
            "revision": revision,
        },
        destination=destination,
    )


async def create_application(
    session, *, profile_id, vacancy_id, status: str, notes, follow_up_date,
    snapshot: dict, destination: str,
) -> uuid.UUID | None:
    """INSERT (revision=1, el INSERT cuenta como primera) + evento inicial +
    outbox; con status=saved además upsert de saved_at en la MISMA tx
    (Decisión 4). None si UNIQUE(profile_id, vacancy_id) ya tiene fila (el
    endpoint responde 409 application_exists)."""
    application_id = (
        await session.execute(
            sa.text(
                "INSERT INTO applications "
                "(id, profile_id, vacancy_id, snapshot, status, notes, "
                " follow_up_date, revision) "
                "VALUES (:id, :pid, :vid, CAST(:snap AS jsonb), :st, :n, :fud, 1) "
                "ON CONFLICT (profile_id, vacancy_id) DO NOTHING RETURNING id"
            ),
            {
                "id": uuid.uuid4(), "pid": profile_id, "vid": vacancy_id,
                "snap": json.dumps(snapshot, ensure_ascii=False, default=str),
                "st": status, "n": notes, "fud": follow_up_date,
            },
        )
    ).scalar_one_or_none()
    if application_id is None:
        return None
    await record_status_event(
        session, application_id=application_id, profile_id=profile_id,
        vacancy_id=vacancy_id, status=status, revision=1,
        destination=destination,
    )
    if status == SAVED_STATUS:
        await matching.set_saved(session, profile_id, vacancy_id, True)
    return application_id


# --------------------------------------------------- composición (Decisión 5)


async def corpus_fields(session, vacancy_ids) -> dict:
    """{vacancy_id: {title, company, url, source, description}} desde el
    corpus (canónica vigente + primary listing) — fallback de las claves
    AUSENTES en snapshot y campos del bookmark puro. SIN filtro de
    presentabilidad: una vacante archivada tras la candidatura sigue
    componiendo su DTO (el snapshot es lo primario)."""
    if not vacancy_ids:
        return {}
    ids = sorted(set(vacancy_ids), key=str)
    rows = (
        await session.execute(
            sa.text(
                "SELECT v.id, o.content, s.name AS source, i.url "
                "FROM vacancies v "
                "LEFT JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
                "LEFT JOIN source_listing_incarnations i "
                "  ON i.id = v.primary_incarnation_id "
                "LEFT JOIN source_listings sl ON sl.id = i.source_listing_id "
                "LEFT JOIN sources s ON s.id = sl.source_id "
                "WHERE v.id = ANY(:ids)"
            ),
            {"ids": ids},
        )
    ).all()
    out = {}
    for r in rows:
        content = r.content or {}
        out[r.id] = {
            "title": content.get("title"),
            "company": content.get("company"),
            "url": r.url,
            "source": r.source,
            "description": content.get("description"),
        }
    return out


def compose_application(row, corpus: dict) -> dict:
    """Item kind=application: claves PRESENTES en snapshot priman aunque
    valgan null (Decisión 3d/5); ausentes → corpus de la vacante enlazada."""
    snap = row.snapshot or {}
    fields = {
        k: (snap[k] if k in snap else corpus.get(k)) for k in SNAPSHOT_KEYS
    }
    return {
        "id": row.id, "profile_id": row.profile_id,
        "vacancy_id": row.vacancy_id, "kind": "application",
        "status": row.status, "notes": row.notes,
        "follow_up_date": row.follow_up_date,
        "created_at": row.created_at, "updated_at": row.updated_at,
        **fields,
    }


def compose_bookmark(row, corpus: dict) -> dict:
    """Item kind=bookmark (bookmark PURO, R2-2): id = vacancy_id
    (direccionable), status=saved, notes de profile_vacancy_state, campos del
    corpus; ts del feed = saved_at."""
    return {
        "id": row.vacancy_id, "profile_id": row.profile_id,
        "vacancy_id": row.vacancy_id, "kind": "bookmark",
        "status": SAVED_STATUS, "notes": row.notes, "follow_up_date": None,
        "created_at": row.saved_at, "updated_at": row.updated_at,
        **{k: corpus.get(k) for k in SNAPSHOT_KEYS},
    }


async def application_item(session, application_id) -> dict | None:
    """Item compuesto de UNA application (respuesta de POST/PATCH)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT id, profile_id, vacancy_id, status, notes, "
                "follow_up_date, created_at, updated_at, snapshot "
                "FROM applications WHERE id = :id"
            ),
            {"id": application_id},
        )
    ).one_or_none()
    if row is None:
        return None
    corpus = await corpus_fields(session, [row.vacancy_id])
    return compose_application(row, corpus.get(row.vacancy_id, {}))


async def bookmark_item(session, profile_id, vacancy_id) -> dict | None:
    """Item compuesto de UN bookmark puro (representación cuyo hash es su ETag)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT profile_id, vacancy_id, saved_at, notes, updated_at "
                "FROM profile_vacancy_state "
                "WHERE profile_id = :pid AND vacancy_id = :vid "
                "AND saved_at IS NOT NULL"
            ),
            {"pid": profile_id, "vid": vacancy_id},
        )
    ).one_or_none()
    if row is None:
        return None
    corpus = await corpus_fields(session, [vacancy_id])
    return compose_bookmark(row, corpus.get(vacancy_id, {}))


# ------------------------------------------------- GET compuesto (Decisión 10)

# UN reloj (ts = created_at | saved_at) y UNA identidad (id = application.id |
# vacancy_id); la rama bookmark EXCLUYE vacantes con fila application del
# perfil (NOT EXISTS — sin item doble). Índices de core0029.
_FEED_UNION_SQL = (
    "SELECT u.item_id, u.kind, u.ts FROM ("
    "SELECT a.id AS item_id, 'application' AS kind, a.created_at AS ts "
    "FROM applications a WHERE a.profile_id = :pid "
    "UNION ALL "
    "SELECT s.vacancy_id AS item_id, 'bookmark' AS kind, s.saved_at AS ts "
    "FROM profile_vacancy_state s "
    "WHERE s.profile_id = :pid AND s.saved_at IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM applications a2 "
    "WHERE a2.profile_id = s.profile_id AND a2.vacancy_id = s.vacancy_id)"
    ") u {cursor} ORDER BY u.ts DESC, u.item_id DESC LIMIT :lim"
)


async def _feed_keys(session, profile_id, limit: int, cursor) -> tuple[list, bool]:
    """Claves (item_id, kind, ts) de la página del feed compuesto. Patrón
    limit+1: la fila extra solo decide has_more."""
    params = {"pid": profile_id, "lim": limit + 1}
    cursor_sql = ""
    if cursor is not None:
        cursor_sql = "WHERE (u.ts < :cts OR (u.ts = :cts AND u.item_id < :cid))"
        params["cts"], params["cid"] = cursor
    rows = (
        await session.execute(
            sa.text(_FEED_UNION_SQL.format(cursor=cursor_sql)), params
        )
    ).all()
    return rows[:limit], len(rows) > limit


async def _feed_rows(session, profile_id, page_rows) -> tuple[dict, dict]:
    """Detalle por LOTE de ambas ramas: ({application.id: fila},
    {vacancy_id: fila pvs})."""
    app_ids = [r.item_id for r in page_rows if r.kind == "application"]
    bm_vids = [r.item_id for r in page_rows if r.kind == "bookmark"]
    app_rows = {}
    if app_ids:
        app_rows = {
            r.id: r
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT id, profile_id, vacancy_id, status, notes, "
                        "follow_up_date, created_at, updated_at, snapshot "
                        "FROM applications WHERE id = ANY(:ids)"
                    ),
                    {"ids": app_ids},
                )
            ).all()
        }
    bm_rows = {}
    if bm_vids:
        bm_rows = {
            r.vacancy_id: r
            for r in (
                await session.execute(
                    sa.text(
                        # saved_at IS NOT NULL (G1-P3-1): entre _feed_keys y
                        # este SELECT otra tx pudo des-marcar el bookmark
                        # (READ COMMITTED); sin re-filtrar, compose_bookmark
                        # produce created_at=None y el DTO revienta (500). El
                        # item se OMITE de la página, como el resto de
                        # carreras toleradas del feed.
                        "SELECT profile_id, vacancy_id, saved_at, notes, "
                        "updated_at FROM profile_vacancy_state "
                        "WHERE profile_id = :pid AND vacancy_id = ANY(:vids) "
                        "AND saved_at IS NOT NULL"
                    ),
                    {"pid": profile_id, "vids": bm_vids},
                )
            ).all()
        }
    return app_rows, bm_rows


async def feed_page(session, profile_id, limit: int, cursor) -> tuple[list, tuple | None]:
    """Página del GET compuesto: (items compuestos en orden, next_cursor
    (ts, id) | None)."""
    page_rows, has_more = await _feed_keys(session, profile_id, limit, cursor)
    app_rows, bm_rows = await _feed_rows(session, profile_id, page_rows)
    vac_ids = [r.vacancy_id for r in app_rows.values()] + list(bm_rows)
    corpus = await corpus_fields(session, vac_ids)
    items = []
    for r in page_rows:
        if r.kind == "application" and r.item_id in app_rows:
            row = app_rows[r.item_id]
            items.append(compose_application(row, corpus.get(row.vacancy_id, {})))
        elif r.kind == "bookmark" and r.item_id in bm_rows:
            items.append(
                compose_bookmark(bm_rows[r.item_id], corpus.get(r.item_id, {}))
            )
    next_cur = (
        (page_rows[-1].ts, page_rows[-1].item_id) if has_more and page_rows else None
    )
    return items, next_cur
