"""Reconciliación INDEPENDIENTE + manifiesto durable de la migración (C-4).

Cierra el hueco de la reconciliación del cutover (rev. externa): los checksums de
import_portfolio_migrate se computan DESDE el destino, así que un error DETERMINISTA
(min_score=60→0, notas corruptas, snapshot equivocado…) pasaría rerun y comparación
cross-BD porque AMBOS destinos fallan igual. Aquí:

- _classify_expected CLASIFICA el ORIGEN por una vía INDEPENDIENTE (segunda
  implementación que lee los campos crudos del durable) para las 4 tablas de tracking
  + la canónica sintetizada + el staging, con MATERIAL COMPLETO (no solo claves):
  status/notes/follow_up/snapshot de applications, cardinalidad de eventos, notes del
  bookmark, título/empresa/desc de la oferta, tupla material de saved_searches, e
  IDENTIDADES del staging (no solo conteos). Las colisiones se determinan del ESTADO
  FINAL (post-attach), no de un chequeo previo con carrera.
- migrate_and_reconcile orquesta todo en UNA transacción: migra, reconcilia, captura
  las IDENTIDADES exactas por CONSULTA SCOPEADA (consumer portfolio + fuente
  portfolio-import — no snapshot-diff global, que atribuiría escrituras concurrentes
  ajenas), y persiste el manifiesto antes del commit. El llamador confirma SOLO si
  verdict=='ok'.
"""

import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.sink import MAX_URL_LEN, normalize_url
from jobhunt_core.import_portfolio import PORTFOLIO_IMPORT_SOURCE
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


def _canon(value) -> str:
    """JSON canónico (claves ordenadas) para comparar filters origen↔destino."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _ts_key(dt: datetime | None) -> str | None:
    """Clave CANÓNICA de un timestamp: wall-clock UTC con microsegundos. Ambos lados
    (Python y to_char AT TIME ZONE 'UTC') dan la MISMA cadena para el mismo instante —
    sin el desajuste trunc(int)↔round(::bigint) del epoch (rev. externa)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _sink_quarantines(row: dict, url_normalized: str) -> bool:
    """Replica la cuarentena por LÍMITES DE ESQUEMA del sink (_limit_violations):
    url/url_normalized > MAX_URL_LEN o NUL en url/title/company/description. Un durable
    así NO se sintetiza → resolve→None → 'unresolved'; el clasificador debe modelarlo
    o daría falso divergent (rev. externa)."""
    url = row.get("url") or ""
    if len(url) > MAX_URL_LEN or len(url_normalized) > MAX_URL_LEN:
        return True
    return any(
        v and "\x00" in v
        for v in (url, row.get("title"), row.get("company"), row.get("description"))
    )


# ---------------------------------------------------------------------------
# Consultas del ESTADO FINAL (post-migración) — colisión y reutilización se leen
# del resultado confirmado, no se infieren de un pre-estado con carrera.
# ---------------------------------------------------------------------------
async def _final_collided_keys(session: AsyncSession, keys: set[str]) -> set[str]:
    """De `keys`, las url_normalized cuya vacante-sombra portfolio-import tiene >1 url
    ORIGINAL activa (colisión cross-source/cross-run detectada en el estado FINAL)."""
    if not keys:
        return set()
    rows = await session.execute(
        sa.text(
            "SELECT sl.url_normalized FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
            "JOIN vacancies v ON v.id = i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            "JOIN source_listing_incarnations all_i "
            "  ON all_i.vacancy_id = v.id AND all_i.ended_at IS NULL "
            "WHERE sl.url_normalized IN :keys "
            "GROUP BY sl.url_normalized HAVING count(DISTINCT all_i.url) > 1"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "keys": list(keys)},
    )
    return {r.url_normalized for r in rows}


async def _reused_keys(session: AsyncSession) -> set[str]:
    """url_normalized de vacantes-sombra portfolio-import que ADEMÁS tienen una
    incarnación activa de OTRA fuente = vacantes REUTILIZADAS (la oferta canónica no
    la creó C-4; se excluyen del oráculo de contenido de oferta)."""
    rows = await session.execute(
        sa.text(
            "SELECT DISTINCT sl.url_normalized FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
            "JOIN vacancies v ON v.id = i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            "WHERE EXISTS (SELECT 1 FROM source_listing_incarnations o "
            "  JOIN source_listings osl ON osl.id = o.source_listing_id "
            "  JOIN sources os ON os.id = osl.source_id AND os.name <> :src "
            "  WHERE o.vacancy_id = v.id AND o.ended_at IS NULL)"
        ),
        {"src": PORTFOLIO_IMPORT_SOURCE},
    )
    return {r.url_normalized for r in rows}


# ---------------------------------------------------------------------------
# Clasificador INDEPENDIENTE del origen (material completo).
# ---------------------------------------------------------------------------
async def _classify_expected(
    session: AsyncSession, users: list[dict]
) -> dict:
    """Deriva del ORIGEN lo que el destino DEBE contener (4 tablas + oferta + staging)
    con material COMPLETO. Las colisiones (intra-lote + cross-source/cross-run) se
    toman del estado FINAL. reused_keys excluye del oráculo de oferta las vacantes cuya
    canónica no creó C-4."""
    # Claves candidatas del lote (por url_normalized).
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
    intra = {k for k, urls in batch_by_key.items() if len(urls) > 1}
    cross = await _final_collided_keys(session, set(batch_by_key) - intra)
    collided_keys = intra | cross
    reused = await _reused_keys(session)

    apps: set[tuple] = set()
    events: Counter = Counter()
    bookmarks: set[tuple] = set()
    offer: set[tuple] = set()
    staged: Counter = Counter()

    # Oferta canónica: primer durable (orden GLOBAL, status-agnóstico) por
    # url_normalized que SÍ se sintetiza (no colisión, no cuarentena, no reutilizada) —
    # su title/company/description es el payload que el sink usa. SIN título el sink NO
    # crea revisión canónica → sin oferta (se excluye, igual que el destino).
    offer_first: dict[str, dict] = {}
    for user in users:
        for row in user.get("applications") or []:
            url = row.get("url")
            if not url:
                continue
            try:
                key = normalize_url(url)
            except ValueError:
                continue
            if key in collided_keys or key in reused or _sink_quarantines(row, key):
                continue
            offer_first.setdefault(key, row)
    offer = {
        (key, r.get("title") or "", r.get("company") or "", r.get("description") or "")
        for key, r in offer_first.items() if r.get("title")
    }

    for user in users:
        ref = str(user["external_ref"])
        groups: dict[str, list[dict]] = {}
        for row in user.get("applications") or []:
            if row.get("status") not in APPLICATION_STATUSES:
                staged[("invalid_status", ref, row.get("url"))] += 1
                continue
            url = row.get("url")
            if not url:
                staged[("unresolved", ref, None)] += 1
                continue
            try:
                key = normalize_url(url)
            except ValueError:
                staged[("unresolved", ref, url)] += 1
                continue
            if key in collided_keys:
                staged[("collision", ref, url)] += 1
                continue
            if _sink_quarantines(row, key):
                staged[("unresolved", ref, url)] += 1
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
            notes = next((r.get("notes") for r in ordered if r.get("notes")), None)

            if winner is not None:
                apps.add((
                    ref, key, winner["status"], notes or "",
                    str(follow_up or ""), winner.get("title") or "",
                    winner.get("company") or "", winner.get("url") or "",
                    winner.get("description") or "",
                ))
                events[(ref, key, winner["status"])] += 1
                for r in real:
                    if r is not winner:
                        staged[("consolidated_real", ref, r.get("url"))] += 1
            if saved_rows:
                bookmarks.add((ref, key, bookmark_note or ""))
            for r in saved_rows:
                note = r.get("notes")
                fu = _as_date(r.get("follow_up_date"))
                if (note and note != bookmark_note) or (fu is not None and fu != follow_up):
                    staged[("consolidated_saved", ref, r.get("url"))] += 1

    # saved_searches: tupla material COMPLETA (incl. last_run canónico).
    searches: set[tuple] = set()
    for user in users:
        ref = str(user["external_ref"])
        for row in user.get("saved_searches") or []:
            name = row.get("name")
            if not name or not isinstance(name, str):
                staged[("no_name", ref, None)] += 1
                continue
            name = name[:SAVED_SEARCH_NAME_MAX]
            raw = row.get("filters")
            try:
                filters = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                filters = None
            invalid = not isinstance(filters, dict)
            if invalid:
                staged[("invalid_filters", ref, name)] += 1
                filters = {}
            min_score = int(row.get("min_score") or 0)
            is_active = False if invalid else bool(row.get("is_active", True))
            last_run = _ts_key(_as_datetime(row.get("last_notified_at")))
            searches.add((ref, name, _canon(filters), min_score, is_active, last_run))

    return {"applications": apps, "events": events, "bookmarks": bookmarks,
            "offer": offer, "saved_searches": searches, "staged": staged}


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
    """Estado REAL del destino con MATERIAL COMPLETO (scope consumer portfolio)."""
    apps = {
        (r.external_ref, r.urln, r.status, r.notes or "", str(r.fud or ""),
         r.title or "", r.company or "", r.url or "", r.description or "")
        for r in await session.execute(sa.text(
            "SELECT p.external_ref, " + _VAC_URLN.format(col="a.vacancy_id") + " AS urln, "
            "a.status, a.notes, a.follow_up_date AS fud, a.snapshot->>'title' AS title, "
            "a.snapshot->>'company' AS company, a.snapshot->>'url' AS url, "
            "a.snapshot->>'description' AS description "
            "FROM applications a " + _SCOPE.format(alias="a")))
    }
    events: Counter = Counter(
        (r.external_ref, r.urln, r.status)
        for r in await session.execute(sa.text(
            "SELECT p.external_ref, " + _VAC_URLN.format(col="a.vacancy_id") + " AS urln, "
            "e.status FROM application_status_events e "
            "JOIN applications a ON a.id = e.application_id " + _SCOPE.format(alias="a")))
    )
    bookmarks = {
        (r.external_ref, r.urln, r.notes or "")
        for r in await session.execute(sa.text(
            "SELECT p.external_ref, " + _VAC_URLN.format(col="pvs.vacancy_id") + " AS urln, "
            "pvs.notes FROM profile_vacancy_state pvs " + _SCOPE.format(alias="pvs")
            + "WHERE pvs.saved_at IS NOT NULL"))
    }
    # Oferta canónica de vacantes-sombra NUEVAS (no reutilizadas: su canónica la creó C-4).
    offer = {
        (r.urln, r.title or "", r.company or "", r.description or "")
        for r in await session.execute(sa.text(
            "SELECT sl.url_normalized AS urln, o.content->>'title' AS title, "
            "o.content->>'company' AS company, o.content->>'description' AS description "
            "FROM vacancies v "
            "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
            "JOIN source_listing_incarnations i ON i.vacancy_id = v.id AND i.ended_at IS NULL "
            "JOIN source_listings sl ON sl.id = i.source_listing_id "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "WHERE v.merged_into IS NULL AND v.archived_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM source_listing_incarnations oi "
            "  JOIN source_listings osl ON osl.id = oi.source_listing_id "
            "  JOIN sources os ON os.id = osl.source_id AND os.name <> :src "
            "  WHERE oi.vacancy_id = v.id AND oi.ended_at IS NULL)"),
            {"src": PORTFOLIO_IMPORT_SOURCE})
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
            "offer": offer, "saved_searches": searches}


def _diff(expected: set, actual: set) -> dict:
    return {"missing": sorted(map(str, expected - actual)),
            "extra": sorted(map(str, actual - expected))}


async def reconcile(session: AsyncSession, users: list[dict], report: dict) -> dict:
    """Contrasta el DESTINO (ya migrado en esta sesión) contra el ESPERADO del ORIGEN
    para las 4 tablas de tracking + oferta canónica + staging (identidades), con
    material completo. Devuelve el manifiesto con veredicto."""
    expected = await _classify_expected(session, users)
    actual = await _actual(session)

    divergences: list[str] = []
    tables = {}
    for name in ("applications", "bookmarks", "offer", "saved_searches"):
        d = _diff(expected[name], actual[name])
        tables[name] = d
        if d["missing"] or d["extra"]:
            divergences.append(f"{name}: faltan {d['missing']} sobran {d['extra']}")
    # Eventos por CARDINALIDAD (Counter): un evento duplicado o faltante diverge.
    if expected["events"] != actual["events"]:
        divergences.append(
            f"application_status_events: esperado {dict(expected['events'])} "
            f"vs {dict(actual['events'])}"
        )
    # Staging por IDENTIDAD (reason, external_ref, url|name), no solo conteo.
    actual_staged = Counter(
        (r["reason"], r["external_ref"],
         r["durable"].get("url") if r["kind"] == "application" else r["durable"].get("name"))
        for r in report.get("staged", [])
    )
    if expected["staged"] != actual_staged:
        divergences.append(
            f"staging: esperado {dict(expected['staged'])} vs {dict(actual_staged)}"
        )

    manifest = {
        "verdict": "ok" if not divergences else "divergent",
        "tables": tables,
        "events": {
            "expected": {str(k): v for k, v in expected["events"].items()},
            "actual": {str(k): v for k, v in actual["events"].items()},
        },
        "staging": {"expected": {str(k): v for k, v in expected["staged"].items()},
                    "actual": {str(k): v for k, v in actual_staged.items()}},
        "divergences": divergences,
    }
    if divergences:
        logger.error(
            "import_portfolio_manifest: reconciliación DIVERGENT — %s",
            " | ".join(divergences),
        )
    else:
        logger.info("import_portfolio_manifest: reconciliación ok")
    return manifest


# ---------------------------------------------------------------------------
# Identidades EXACTAS por CONSULTA SCOPEADA (no snapshot-diff global).
# ---------------------------------------------------------------------------
async def _scoped(session: AsyncSession, sql: str, params: dict) -> list[str]:
    return [r.k for r in await session.execute(sa.text(sql), params)]


async def _captured_identities(session: AsyncSession) -> dict:
    """Identidades de lo que C-4 escribió, por CONSULTA SCOPEADA al consumer portfolio
    (tracking) y a la fuente portfolio-import (corpus) — ambos EXCLUSIVOS de C-4, así
    que no atribuyen escrituras concurrentes ajenas ni cuestan O(corpus) como el
    snapshot-diff (rev. externa). new_vacancies/reused_vacancies del estado final:
    reused = vacante con también incarnación de OTRA fuente (C-4 la reutilizó, no borra
    en rollback). El rollback es un borrado SCOPEADO (fuente + consumer) determinista."""
    src = {"src": PORTFOLIO_IMPORT_SOURCE}
    cons = {"cons": PORTFOLIO_CONSUMER}
    ident = {
        "applications": await _scoped(session,
            "SELECT a.id::text k FROM applications a "
            "JOIN profiles p ON p.id = a.profile_id "
            "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons", cons),
        "saved_searches": await _scoped(session,
            "SELECT ss.id::text k FROM saved_searches ss "
            "JOIN profiles p ON p.id = ss.profile_id "
            "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons", cons),
        "source": await _scoped(session,
            "SELECT id::text k FROM sources WHERE name = :src", src),
        "consumer": await _scoped(session,
            "SELECT id::text k FROM consumers WHERE name = :cons", cons),
    }
    ident["new_vacancies"] = await _scoped(session,
        "SELECT v.id::text k FROM vacancies v "
        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id AND i.ended_at IS NULL "
        "JOIN source_listings sl ON sl.id = i.source_listing_id "
        "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
        "WHERE v.merged_into IS NULL AND v.archived_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM source_listing_incarnations oi "
        "  JOIN source_listings osl ON osl.id = oi.source_listing_id "
        "  JOIN sources os ON os.id = osl.source_id AND os.name <> :src "
        "  WHERE oi.vacancy_id = v.id AND oi.ended_at IS NULL)", src)
    ident["reused_vacancies"] = await _scoped(session,
        "SELECT DISTINCT v.id::text k FROM vacancies v "
        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id AND i.ended_at IS NULL "
        "JOIN source_listings sl ON sl.id = i.source_listing_id "
        "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
        "WHERE v.merged_into IS NULL AND v.archived_at IS NULL "
        "AND EXISTS (SELECT 1 FROM source_listing_incarnations oi "
        "  JOIN source_listings osl ON osl.id = oi.source_listing_id "
        "  JOIN sources os ON os.id = osl.source_id AND os.name <> :src "
        "  WHERE oi.vacancy_id = v.id AND oi.ended_at IS NULL)", src)
    return ident


async def migrate_and_reconcile(session: AsyncSession, users: list[dict]) -> dict:
    """ENTRYPOINT transaccional del cutover (rev. externa): MIGRA, RECONCILIA las 4
    tablas + oferta + staging (material completo) contra el esperado del origen,
    captura las IDENTIDADES exactas por consulta scopeada (consumer portfolio + fuente
    portfolio-import; new/reused vacancies), y PERSISTE el manifiesto antes del commit.
    NO commitea: el llamador confirma SOLO si verdict=='ok' (si 'divergent', rollback —
    el DETALLE queda en el log ERROR y en el dict devuelto para volcar fuera de la tx)."""
    report = await migrate_portfolio(session, users)
    manifest = await reconcile(session, users, report)
    manifest["report"] = {
        "users": report["users"], "applications": report["applications"],
        "saved_searches": report["saved_searches"],
    }
    manifest["staged"] = report["staged"]
    manifest["identities"] = await _captured_identities(session)
    manifest["checksums"] = await table_checksums(session)
    manifest["id"] = str(await persist_manifest(session, manifest))
    return manifest


async def persist_manifest(session: AsyncSession, manifest: dict) -> uuid.UUID:
    """Persiste el manifiesto en portfolio_migration_manifest (core0013) DENTRO de la
    transacción del llamador → atómico con la migración (se revierte con ella en un
    dry-run o en 'divergent'; el log ERROR conserva el detalle). Devuelve el id."""
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
