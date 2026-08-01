"""Reconciliación INDEPENDIENTE + manifiesto durable de la migración (C-4).

Cierra el hueco de la reconciliación del cutover (P2/P1 rev. externa): los
checksums de import_portfolio_migrate se computan DESDE el destino, así que un
error DETERMINISTA (p.ej. min_score=60→0) pasaría rerun Y comparación cross-BD
porque AMBOS destinos fallan igual. Aquí se CLASIFICA el ORIGEN por una vía
INDEPENDIENTE de la migración (segunda implementación que lee los campos crudos
del durable) para las CUATRO tablas de tracking + el staging, se contrasta con el
DESTINO, y `migrate_and_reconcile` lo orquesta todo en UNA transacción: captura el
pre-estado (para colisiones y para las identidades de rollback), migra, reconcilia,
construye el manifiesto con las PK exactas insertadas/reutilizadas y lo PERSISTE
antes del commit. El llamador confirma SOLO si el veredicto es 'ok'.
"""

import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.sink import MAX_URL_LEN, normalize_url
from jobhunt_core.import_portfolio import PORTFOLIO_IMPORT_SOURCE, _persisted_urls
from jobhunt_core.import_portfolio_durables import (
    APPLICATION_STATUSES,
    PORTFOLIO_CONSUMER,
    SAVED_SEARCH_NAME_MAX,
    SAVED_STATUS,
    _as_date,
    _as_datetime,
    _recency_key,
)
from jobhunt_core.import_portfolio_migrate import migrate_portfolio, table_checksums

logger = logging.getLogger(__name__)

# Tabla → EXPRESIÓN de PK (texto) cuyas filas NUEVAS de esta ejecución registra el
# manifiesto para un rollback FK-safe. Incluye las 4 tablas hijas que el sink escribe
# por cada vacante-sombra (source_listing_revisions, offer_revision_sources,
# link_evidence, dedup_candidates) — sus FK son NO ACTION, así que sin registrarlas el
# borrado por identidades en orden FK-inverso quedaría bloqueado (P1 rev. externa).
_ROLLBACK_TABLES = {
    "applications": "id::text",
    "application_status_events": "id::text",
    "saved_searches": "id::text",
    "vacancies": "id::text",
    "offer_revisions": "id::text",
    "offer_revision_sources": "offer_revision_id::text || ':' || source_listing_revision_id::text",
    "source_listings": "id::text",
    "source_listing_incarnations": "id::text",
    "source_listing_revisions": "id::text",
    "link_evidence": "id::text",
    "dedup_candidates": "id::text",
    "profile_vacancy_state": "profile_id::text || ':' || vacancy_id::text",
    "profiles": "id::text",
    "consumers": "id::text",
}


def _canon(value) -> str:
    """JSON canónico (claves ordenadas) para comparar filters origen↔destino."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _ts_key(dt: datetime | None) -> str | None:
    """Clave CANÓNICA de un timestamp: wall-clock UTC con microsegundos ('...T..:...US').
    Ambos lados (esperado en Python, destino con to_char AT TIME ZONE 'UTC') producen
    la MISMA cadena para el mismo instante — sin el desajuste truncado(int)↔redondeado
    (::bigint) del epoch que daba falso divergent con sub-segundos (P1 rev. externa 2)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _sink_quarantines(row: dict, url_normalized: str) -> bool:
    """Replica la cuarentena por LÍMITES DE ESQUEMA del sink (_limit_violations):
    url/url_normalized > MAX_URL_LEN o NUL en url/title/company/description. Un durable
    así NO se sintetiza → resolve→None → 'unresolved'; el clasificador debe modelarlo
    o daría falso divergent en applications/events/staging (P2 rev. externa 2)."""
    url = row.get("url") or ""
    if len(url) > MAX_URL_LEN or len(url_normalized) > MAX_URL_LEN:
        return True
    return any(
        v and "\x00" in v
        for v in (url, row.get("title"), row.get("company"), row.get("description"))
    )


def _classify_expected(users: list[dict], corpus: dict[str, set[str]]) -> dict:
    """Clasifica el ORIGEN por una vía INDEPENDIENTE de la migración: reproduce las
    reglas C-4 (síntesis, colisión, consolidación, coalesce, dedup) para derivar lo
    que el destino DEBE contener. `corpus` = {url_normalized: {urls}} del estado
    PRE-migración (colisiones cross-run/cross-source). Devuelve las claves materiales
    esperadas por tabla + el recuento de staging por razón."""
    # --- Colisiones: agrupar TODAS las urls del lote por url_normalized y unir con el
    # corpus; una clave con >1 url ORIGINAL distinta colisiona (stage-all).
    batch_by_key: dict[str, set[str]] = {}
    for user in users:
        for row in user.get("applications") or []:
            url = row.get("url")
            if not url:
                continue
            try:
                key = normalize_url(url)
            except ValueError:
                continue
            batch_by_key.setdefault(key, set()).add(url)
    collided_keys = {
        k for k, urls in batch_by_key.items()
        if len(urls | corpus.get(k, set())) > 1
    }

    apps: set[tuple] = set()
    events: set[tuple] = set()
    bookmarks: set[tuple] = set()
    staged: Counter = Counter()

    for user in users:
        ref = str(user["external_ref"])
        groups: dict[str, list[dict]] = {}
        for row in user.get("applications") or []:
            if row.get("status") not in APPLICATION_STATUSES:
                staged["invalid_status"] += 1
                continue
            url = row.get("url")
            if not url:
                staged["unresolved"] += 1
                continue
            try:
                key = normalize_url(url)
            except ValueError:
                staged["unresolved"] += 1
                continue
            if key in collided_keys:
                staged["collision"] += 1
                continue
            if _sink_quarantines(row, key):
                # No persistible por el esquema → el sink lo cuarentena → unresolved.
                staged["unresolved"] += 1
                continue
            groups.setdefault(key, []).append(row)

        for key, group in groups.items():
            saved_rows = [r for r in group if r.get("status") == SAVED_STATUS]
            real = [r for r in group if r.get("status") != SAVED_STATUS]
            saved_fu = [r for r in saved_rows if _as_date(r.get("follow_up_date"))]
            candidates = real + saved_fu
            winner = max(real or saved_fu, key=_recency_key) if candidates else None

            ordered_saved = sorted(saved_rows, key=_recency_key, reverse=True)
            bookmark_note = next(
                (r.get("notes") for r in ordered_saved if r.get("notes")), None
            )
            ordered = (
                [winner, *sorted(candidates, key=_recency_key, reverse=True)]
                if winner else []
            )
            follow_up = next(
                (d for d in (_as_date(r.get("follow_up_date")) for r in ordered) if d),
                None,
            )

            if winner is not None:
                apps.add((ref, key, winner["status"]))
                events.add((ref, key, winner["status"]))
                for r in real:
                    if r is not winner:
                        staged["consolidated_real"] += 1
            if saved_rows:
                bookmarks.add((ref, key))
            for r in saved_rows:
                note = r.get("notes")
                fu = _as_date(r.get("follow_up_date"))
                if (note and note != bookmark_note) or (fu is not None and fu != follow_up):
                    staged["consolidated_saved"] += 1

    # --- saved_searches: tupla material COMPLETA (incl. last_run epoch — P1 rev.
    # externa 2), invalid → {}+desactivada+staged, sin name → staged, dedup.
    searches: set[tuple] = set()
    for user in users:
        ref = str(user["external_ref"])
        for row in user.get("saved_searches") or []:
            name = row.get("name")
            if not name or not isinstance(name, str):
                staged["no_name"] += 1
                continue
            name = name[:SAVED_SEARCH_NAME_MAX]
            raw = row.get("filters")
            try:
                filters = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                filters = None
            invalid = not isinstance(filters, dict)
            if invalid:
                staged["invalid_filters"] += 1
                filters = {}
            min_score = int(row.get("min_score") or 0)
            is_active = False if invalid else bool(row.get("is_active", True))
            last_run = _ts_key(_as_datetime(row.get("last_notified_at")))
            searches.add((ref, name, _canon(filters), min_score, is_active, last_run))

    return {
        "applications": apps, "events": events, "bookmarks": bookmarks,
        "saved_searches": searches, "staged": staged,
    }


# Subconsulta: url_normalized portfolio-import de la incarnación activa de vacancy.
_VAC_URLN = (
    "(SELECT sl.url_normalized FROM source_listing_incarnations i "
    "JOIN source_listings sl ON sl.id = i.source_listing_id "
    f"JOIN sources s ON s.id = sl.source_id AND s.name = '{PORTFOLIO_IMPORT_SOURCE}' "
    "WHERE i.vacancy_id = {col} AND i.ended_at IS NULL LIMIT 1)"
)
_SCOPE = (
    "JOIN profiles p ON p.id = {alias}.profile_id "
    f"JOIN consumers c ON c.id = p.consumer_id AND c.name = '{PORTFOLIO_CONSUMER}' "
)


async def _actual(session: AsyncSession) -> dict:
    """Claves materiales REALES del destino (scopeadas al consumer portfolio)."""
    apps = {
        (r.external_ref, r.urln, r.status)
        for r in await session.execute(sa.text(
            f"SELECT p.external_ref, {_VAC_URLN.format(col='a.vacancy_id')} AS urln, "
            "a.status FROM applications a " + _SCOPE.format(alias="a")))
    }
    events = {
        (r.external_ref, r.urln, r.status)
        for r in await session.execute(sa.text(
            f"SELECT p.external_ref, {_VAC_URLN.format(col='a.vacancy_id')} AS urln, "
            "e.status FROM application_status_events e "
            "JOIN applications a ON a.id = e.application_id " + _SCOPE.format(alias="a")))
    }
    bookmarks = {
        (r.external_ref, r.urln)
        for r in await session.execute(sa.text(
            f"SELECT p.external_ref, {_VAC_URLN.format(col='pvs.vacancy_id')} AS urln "
            "FROM profile_vacancy_state pvs " + _SCOPE.format(alias="pvs")
            + "WHERE pvs.saved_at IS NOT NULL"))
    }
    searches = {
        (r.external_ref, r.name, _canon(r.filters), int(r.min_score),
         bool(r.is_active), r.last_run)
        for r in await session.execute(sa.text(
            "SELECT p.external_ref, ss.name, ss.filters, ss.min_score, ss.is_active, "
            "to_char(ss.last_run_at AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US') AS last_run "
            "FROM saved_searches ss " + _SCOPE.format(alias="ss")))
    }
    return {"applications": apps, "events": events, "bookmarks": bookmarks,
            "saved_searches": searches}


async def _reused_vacancies(session: AsyncSession, pre_vacancies: set) -> list[str]:
    """vacancy_id PREEXISTENTES (en pre) que ahora tienen una incarnación
    portfolio-import → REUTILIZADAS por el sink (attach cross-source), no NUEVAS —
    inventario preciso con la elegibilidad real del attach (P2 rev. externa 2)."""
    if not pre_vacancies:
        return []
    rows = await session.execute(
        sa.text(
            "SELECT DISTINCT i.vacancy_id FROM source_listing_incarnations i "
            "JOIN source_listings sl ON sl.id = i.source_listing_id "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "WHERE i.ended_at IS NULL AND i.vacancy_id IN :pre"
        ).bindparams(sa.bindparam("pre", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "pre": list(pre_vacancies)},
    )
    return [str(r.vacancy_id) for r in rows]


def _diff(expected: set, actual: set) -> dict:
    return {"missing": sorted(map(str, expected - actual)),
            "extra": sorted(map(str, actual - expected))}


async def reconcile(
    session: AsyncSession, users: list[dict], corpus: dict[str, set[str]],
    report: dict,
) -> dict:
    """Contrasta el DESTINO (ya migrado en esta sesión) contra el ESPERADO del
    ORIGEN para las 4 tablas de tracking + el staging, y devuelve el manifiesto con
    veredicto. `corpus` = pre-estado (colisiones); `report` = salida de
    migrate_portfolio (para cotejar su staging con el esperado)."""
    expected = _classify_expected(users, corpus)
    actual = await _actual(session)

    divergences: list[str] = []
    tables = {}
    for name in ("applications", "events", "bookmarks", "saved_searches"):
        d = _diff(expected[name], actual[name])
        tables[name] = d
        if d["missing"] or d["extra"]:
            divergences.append(f"{name}: faltan {d['missing']} sobran {d['extra']}")

    # Staging: el producido por la migración (report) DEBE coincidir con el esperado
    # (independiente). Una colisión/degradación no contabilizada rompe el veredicto.
    actual_staged = Counter(r["reason"] for r in report.get("staged", []))
    expected_staged = expected["staged"]
    if actual_staged != expected_staged:
        divergences.append(
            f"staging: esperado {dict(expected_staged)} vs migrado {dict(actual_staged)}"
        )

    manifest = {
        "verdict": "ok" if not divergences else "divergent",
        "tables": tables,
        "staging": {"expected": dict(expected_staged), "actual": dict(actual_staged)},
        "divergences": divergences,
    }
    if divergences:
        # DETALLE completo en el log (nivel ERROR): en 'divergent' el llamador hará
        # rollback y el manifiesto persistido se revierte con él — el log es la
        # evidencia DURABLE del cutover fallido para el post-mortem (P3 rev. externa).
        logger.error(
            "import_portfolio_manifest: reconciliación DIVERGENT — %s",
            " | ".join(divergences),
        )
    else:
        logger.info("import_portfolio_manifest: reconciliación ok")
    return manifest


async def _snapshot_ids(session: AsyncSession) -> dict[str, set]:
    """PK actuales por tabla de rollback (para calcular las NUEVAS = post − pre).
    El valor de PK es una EXPRESIÓN de texto (single o compuesta) definida en
    _ROLLBACK_TABLES."""
    snap: dict[str, set] = {}
    for table, pk_expr in _ROLLBACK_TABLES.items():
        rows = await session.execute(sa.text(f"SELECT {pk_expr} AS k FROM {table}"))
        snap[table] = {r.k for r in rows}
    return snap


async def migrate_and_reconcile(session: AsyncSession, users: list[dict]) -> dict:
    """ENTRYPOINT transaccional del cutover (P1 rev. externa 3): captura el
    pre-estado (corpus para colisiones + PK para rollback), MIGRA, RECONCILIA las 4
    tablas + staging contra el esperado del origen, construye el manifiesto con las
    identidades EXACTAS (PK insertadas por esta ejecución + vacantes reutilizadas) y
    los checksums, y lo PERSISTE antes del commit. NO commitea: el llamador confirma
    SOLO si manifest['verdict'] == 'ok' (si es 'divergent', rollback). En 'divergent'
    el manifiesto persistido se revierte con el rollback: el DETALLE de las
    divergencias queda en el log (ERROR) como evidencia durable, y el dict devuelto
    permite al llamador volcarlo fuera de la transacción antes de revertir."""
    # Pre-estado ANTES de migrar: corpus elegible para attach (colisiones cross-run/
    # cross-source) + PK previas (identidades de rollback).
    corpus_keys = []
    for user in users:
        for row in user.get("applications") or []:
            url = row.get("url")
            if not url:
                continue
            try:
                corpus_keys.append(normalize_url(url))
            except ValueError:
                pass
    corpus = await _persisted_urls(session, corpus_keys)
    pre_ids = await _snapshot_ids(session)

    report = await migrate_portfolio(session, users)
    manifest = await reconcile(session, users, corpus, report)

    post_ids = await _snapshot_ids(session)
    inserted = {t: sorted(post_ids[t] - pre_ids[t]) for t in post_ids}
    reused = await _reused_vacancies(session, pre_ids["vacancies"])

    manifest["report"] = {
        "users": report["users"], "applications": report["applications"],
        "saved_searches": report["saved_searches"],
    }
    manifest["staged"] = report["staged"]
    manifest["identities"] = {"inserted": inserted, "reused_vacancies": reused}
    manifest["checksums"] = await table_checksums(session)
    manifest["id"] = str(await persist_manifest(session, manifest))
    return manifest


async def persist_manifest(session: AsyncSession, manifest: dict) -> uuid.UUID:
    """Persiste el manifiesto en portfolio_migration_manifest (core0013) DENTRO de la
    transacción del llamador → atómico con la migración (se revierte con ella en un
    dry-run). El informe (esperado, real, identidades de rollback, checksums,
    veredicto) sobrevive aunque el proceso muera tras el commit — P1 rev. externa 3."""
    manifest_id = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO portfolio_migration_manifest (id, verdict, manifest) "
            "VALUES (:id, :v, CAST(:m AS jsonb))"
        ),
        {"id": manifest_id, "v": manifest["verdict"],
         "m": json.dumps(manifest, ensure_ascii=False, default=str)},
    )
    return manifest_id
