"""Reconciliación (scaffold LOCAL) + inventario de rollback de la migración (C-4).

Los checksums de import_portfolio_migrate se computan DESDE el destino, así que un
error DETERMINISTA (min_score=60→0, notas corruptas…) pasaría rerun y comparación
cross-BD porque AMBOS destinos fallan igual. Este módulo lo ATAJA verificando VALORES
MATERIALES contra el ORIGEN.

ALCANCE — DIVISIÓN C-4 / ENSAYO §4 (gated NAS), decidida con el propietario:
- AQUÍ (scaffold local): _classify_expected compara los VALORES materiales del destino
  contra el origen (status/notes/follow_up/snapshot de applications, cardinalidad de
  eventos, notes de bookmark, contenido canónico de oferta —normalizado—, tupla de
  saved_searches) → atrapa el bug determinista. La ESTRUCTURA (qué durable resolvió) se
  LEE del estado final; el staging se coteja CONTABLEMENTE por identidad. Es un scaffold
  para el ensayo, con límites EXPLÍCITOS (ver reconcile y _captured_identities).
- EN §4 (gated NAS): la verificación INDEPENDIENTE COMPLETA (que distingue una cuarentena
  legítima de un fallo del sink que pierda listings válidos — exige un LEDGER verificable
  del sink) y el MANIFIESTO DE PROCEDENCIA EXACTA + SCRIPT de rollback FK-safe (RUNBOOK
  §3/§4). El artefacto de corpus en colisión SÍ se corrige aquí (savepoint, es integridad
  de datos real — ver import_portfolio._synthesize_pruning_collisions).
"""

import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.sink import normalize_url
from jobhunt_core.import_portfolio import PORTFOLIO_IMPORT_SOURCE, normalized_key
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


def _norm_text(value) -> str | None:
    """Replica normalize._text del sink: strip + vacío→None. El oráculo de oferta
    compara contra la canónica YA normalizada, así que sin esto un salto de línea o
    espacio final (habitual en descripciones) daría falso divergent (rev. adversarial)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _ident(value) -> str:
    """Identidad de staging ROBUSTA y hashable, IDÉNTICA en ambos lados: None→'',
    str tal cual, no-str→str(). Evita el falso divergent None↔'' y el crash del
    Counter con un name no-str (rev. adversarial)."""
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Estructura del ESTADO FINAL (post-migración): qué se sintetizó/adjuntó realmente.
# Se LEE el resultado (no se replica la lógica del sink: cuarentena UTF-8/NUL/long.,
# cross-run y consolidación quedan cubiertas por construcción).
# ---------------------------------------------------------------------------
async def _incarnation_urls(
    session: AsyncSession, keys: set[str], all_sources: bool
) -> dict[str, set[str]]:
    """{url_normalized: {urls activas}} de las vacantes-sombra portfolio-import de
    `keys`: solo las incarnaciones portfolio-import (all_sources=False) o TODAS las de
    la vacante (all_sources=True, para detectar colisión cross-source)."""
    if not keys:
        return {}
    join = (
        "JOIN source_listing_incarnations u ON u.vacancy_id = v.id AND u.ended_at IS NULL "
        if all_sources else ""
    )
    url_col = "u.url" if all_sources else "i.url"
    rows = await session.execute(
        sa.text(
            f"SELECT sl.url_normalized, {url_col} AS url FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
            "JOIN vacancies v ON v.id = i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            + join
            + "WHERE sl.url_normalized IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "keys": list(keys)},
    )
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r.url_normalized, set()).add(r.url)
    return out


async def _reused_keys(session: AsyncSession, keys: set[str]) -> set[str]:
    """De `keys`, las cuya vacante-sombra tiene ADEMÁS una incarnación de OTRA fuente =
    REUTILIZADAS (la oferta canónica no la creó C-4; se excluyen del oráculo de oferta)."""
    if not keys:
        return set()
    rows = await session.execute(
        sa.text(
            "SELECT DISTINCT sl.url_normalized FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
            "JOIN vacancies v ON v.id = i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            "WHERE sl.url_normalized IN :keys AND EXISTS ("
            "  SELECT 1 FROM source_listing_incarnations o "
            "  JOIN source_listings osl ON osl.id = o.source_listing_id "
            "  JOIN sources os ON os.id = osl.source_id AND os.name <> :src "
            "  WHERE o.vacancy_id = v.id AND o.ended_at IS NULL)"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "keys": list(keys)},
    )
    return {r.url_normalized for r in rows}


# ---------------------------------------------------------------------------
# Clasificador: ESTRUCTURA del estado final + VALORES del origen.
# ---------------------------------------------------------------------------
async def _classify_expected(
    session: AsyncSession, users: list[dict]
) -> dict:
    """Deriva lo que el destino DEBE contener. La ESTRUCTURA (qué durable resolvió a una
    vacante, cuál colisionó/quedó unresolved) se lee del ESTADO FINAL — `synth`
    (incarnaciones portfolio-import por clave) refleja lo que REALMENTE se sintetizó, así
    que cuarentena (UTF-8/NUL/longitud), cross-run y consolidación quedan cubiertas sin
    replicar el sink. Los VALORES materiales (status/notes/follow_up/snapshot/oferta) se
    comparan contra el ORIGEN (independencia para el bug determinista)."""
    batch_by_key: dict[str, set[str]] = {}
    for user in users:
        for row in user.get("applications") or []:
            url = row.get("url")
            if not url:
                continue
            # normalized_key excluye las urls no usables como clave (malformadas O con
            # mojibake no codificable): así NUNCA entran en `keys` → ninguna query recibe un
            # bind-param tóxico (asyncpg DataError). El durable cae a 'unresolved' en _route.
            key = normalized_key(url)
            if key is None:
                continue
            batch_by_key.setdefault(key, set()).add(url)
    intra = {k for k, urls in batch_by_key.items() if len(urls) > 1}
    keys = set(batch_by_key)
    synth = await _incarnation_urls(session, keys, all_sources=False)
    allsrc = await _incarnation_urls(session, keys, all_sources=True)
    reused = await _reused_keys(session, keys)

    def _route(row):
        """Devuelve ('grouped', key) o ('staged', (reason, ref, ident))-parcial."""
        url = row.get("url")
        if not url:
            return ("staged", "unresolved", _ident(None))
        try:
            key = normalize_url(url)
        except ValueError:
            return ("staged", "unresolved", _ident(url))
        if key in intra:
            return ("staged", "collision", _ident(url))
        if url not in synth.get(key, set()):
            # No se sintetizó esta url: cross-run (hay otra portfolio con la clave) o
            # cuarentena/irresoluble (nada portfolio con la clave).
            return ("staged", "collision" if synth.get(key) else "unresolved", _ident(url))
        if len(allsrc.get(key, {url})) > 1:  # cross-source: la vacante tiene otra url
            return ("staged", "collision", _ident(url))
        return ("grouped", key)

    apps: set[tuple] = set()
    events: Counter = Counter()
    bookmarks: set[tuple] = set()
    staged: Counter = Counter()

    # Oferta canónica: primer durable (orden GLOBAL) cuya url SÍ se sintetizó (no
    # colisión/cuarentena/reutilizada) → su payload NORMALIZADO (strip). SIN título tras
    # normalizar el sink no crea revisión → sin oferta.
    offer_first: dict[str, dict] = {}
    for user in users:
        for row in user.get("applications") or []:
            if _route(row)[0] == "grouped" and normalize_url(row["url"]) not in reused:
                offer_first.setdefault(normalize_url(row["url"]), row)
    offer = {
        (key, _norm_text(r.get("title")) or "", _norm_text(r.get("company")) or "",
         _norm_text(r.get("description")) or "")
        for key, r in offer_first.items() if _norm_text(r.get("title"))
    }

    for user in users:
        ref = str(user["external_ref"])
        groups: dict[str, list[dict]] = {}
        for row in user.get("applications") or []:
            if row.get("status") not in APPLICATION_STATUSES:
                staged[("invalid_status", ref, _ident(row.get("url")))] += 1
                continue
            routed = _route(row)
            if routed[0] == "staged":
                staged[(routed[1], ref, routed[2])] += 1
                continue
            groups.setdefault(routed[1], []).append(row)

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
                        staged[("consolidated_real", ref, _ident(r.get("url")))] += 1
            if saved_rows:
                bookmarks.add((ref, key, bookmark_note or ""))
            for r in saved_rows:
                note = r.get("notes")
                fu = _as_date(r.get("follow_up_date"))
                if (note and note != bookmark_note) or (fu is not None and fu != follow_up):
                    staged[("consolidated_saved", ref, _ident(r.get("url")))] += 1

    # saved_searches: tupla material COMPLETA (incl. last_run canónico). La identidad
    # de staging usa el name CRUDO (_ident) — igual que reconcile lee durable.get('name'),
    # sin truncar y sin None↔'' (rev. adversarial).
    searches: set[tuple] = set()
    for user in users:
        ref = str(user["external_ref"])
        for row in user.get("saved_searches") or []:
            name = row.get("name")
            if not name or not isinstance(name, str):
                staged[("no_name", ref, _ident(row.get("name")))] += 1
                continue
            raw = row.get("filters")
            try:
                filters = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                filters = None
            invalid = not isinstance(filters, dict)
            if invalid:
                staged[("invalid_filters", ref, _ident(name))] += 1
                filters = {}
            name = name[:SAVED_SEARCH_NAME_MAX]
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
    """Contrasta el DESTINO (ya migrado en esta sesión) contra el ESPERADO del ORIGEN.

    ALCANCE (scaffold LOCAL — la verificación INDEPENDIENTE COMPLETA es del ensayo §4,
    gated NAS): comprueba VALORES MATERIALES contra el ORIGEN (applications con notes/
    follow_up/snapshot, cardinalidad de eventos, notes de bookmark, contenido canónico
    de oferta, tupla de saved_searches) — atrapa el bug determinista (min_score 60→0,
    notas corruptas…). La ESTRUCTURA (qué durable resolvió) se lee del estado final, así
    que un fallo del SINK que pierda listings válidos NO lo distingue de una cuarentena
    legítima: eso exige el LEDGER verificable del sink, que produce el §4. El staging se
    coteja por IDENTIDAD (external_ref, url|name) — CONTABLE (nada sin auditar), no por
    razón (la razón exacta la verifica el ledger del §4)."""
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
    # Staging CONTABLE por IDENTIDAD (external_ref, url|name), reason-agnóstico: garantiza
    # que ningún durable queda sin auditar (ni tracking ni staging). Un durable
    # colisionado cuya cadena se revirtió (savepoint) se ve 'unresolved' en el estado
    # final pero 'collision' en el report — la razón la fija el §4; la IDENTIDAD coincide.
    expected_ids = Counter((ref, ident) for _, ref, ident in expected["staged"].elements())
    actual_ids = Counter(
        (r["external_ref"],
         _ident(r["durable"].get("url") if r["kind"] == "application"
                else r["durable"].get("name")))
        for r in report.get("staged", [])
    )
    if expected_ids != actual_ids:
        divergences.append(
            f"staging (identidades): esperado {dict(expected_ids)} vs {dict(actual_ids)}"
        )

    manifest = {
        "verdict": "ok" if not divergences else "divergent",
        "tables": tables,
        "events": {
            "expected": {str(k): v for k, v in expected["events"].items()},
            "actual": {str(k): v for k, v in actual["events"].items()},
        },
        "staging": {"expected": {str(k): v for k, v in expected["staged"].items()},
                    "actual_ids": {str(k): v for k, v in actual_ids.items()}},
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
# INVENTARIO SCOPEADO del alcance de C-4 (NO procedencia exacta — eso es del §4).
# ---------------------------------------------------------------------------
async def _scoped(session: AsyncSession, sql: str, params: dict) -> list[str]:
    return [r.k for r in await session.execute(sa.text(sql), params)]


async def _captured_identities(session: AsyncSession) -> dict:
    """INVENTARIO SCOPEADO del alcance de C-4 (RUNBOOK §3), por consulta scopeada
    (consumer portfolio para tracking; fuente portfolio-import para el corpus, incl.
    dedup_candidates/link_evidence que el sink escribió por este import). new_vacancies
    (incarnación portfolio-import y ninguna de otra fuente) vs reused_vacancies (con
    incarnación de otra fuente → solo se borra el ENLACE).

    ALCANCE (scaffold — la PROCEDENCIA EXACTA es del §4, gated NAS): es un inventario por
    SCOPE, NO la procedencia exacta 'insertado por ESTE run'. En un re-run idempotente
    reaparecerían las filas del run previo; un offer_revision REUTILIZADO (preexistente de
    otra fuente sobre una vacante que C-4 reutilizó) puede aparecer aunque C-4 no lo creara.
    El manifiesto de PROCEDENCIA EXACTA (RETURNING de cada INSERT + ledger 'created/reused/
    link' del sink) y el SCRIPT de borrado FK-safe (orden child→parent, punteros circulares,
    merge, abort-on-RESTRICT) los produce y PRUEBA el ensayo §4. Este inventario le sirve de
    partida y de cross-check."""
    src = {"src": PORTFOLIO_IMPORT_SOURCE}
    cons = {"cons": PORTFOLIO_CONSUMER}
    both = {**src, **cons}
    # Corpus: alcanzable desde la fuente portfolio-import (source_listings → incarnations
    # → revisions → offer_revision_sources → offer_revisions). dedup_candidates/
    # link_evidence: los que el import escribió (referencian una vacante/listing
    # portfolio-import); en el freeze single-writer son los de C-4.
    corpus_join = (
        "FROM source_listings sl "
        "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
    )
    ident = {
        "source": await _scoped(session,
            "SELECT id::text k FROM sources WHERE name = :src", src),
        "consumer": await _scoped(session,
            "SELECT id::text k FROM consumers WHERE name = :cons", cons),
        "applications": await _scoped(session,
            "SELECT a.id::text k FROM applications a JOIN profiles p ON p.id = a.profile_id "
            "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons", cons),
        "application_status_events": await _scoped(session,
            "SELECT e.id::text k FROM application_status_events e "
            "JOIN applications a ON a.id = e.application_id "
            "JOIN profiles p ON p.id = a.profile_id "
            "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons", cons),
        "profile_vacancy_state": await _scoped(session,
            "SELECT (pvs.profile_id::text || ':' || pvs.vacancy_id::text) k "
            "FROM profile_vacancy_state pvs JOIN profiles p ON p.id = pvs.profile_id "
            "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons", cons),
        "saved_searches": await _scoped(session,
            "SELECT ss.id::text k FROM saved_searches ss JOIN profiles p ON p.id = ss.profile_id "
            "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons", cons),
        "source_listings": await _scoped(session,
            "SELECT sl.id::text k " + corpus_join, src),
        "source_listing_incarnations": await _scoped(session,
            "SELECT i.id::text k " + corpus_join
            + "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id", src),
        "source_listing_revisions": await _scoped(session,
            "SELECT r.id::text k " + corpus_join
            + "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id "
            "JOIN source_listing_revisions r ON r.incarnation_id = i.id", src),
        "offer_revision_sources": await _scoped(session,
            "SELECT (ors.offer_revision_id::text || ':' || ors.source_listing_revision_id::text) k "
            + corpus_join
            + "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id "
            "JOIN source_listing_revisions r ON r.incarnation_id = i.id "
            "JOIN offer_revision_sources ors ON ors.source_listing_revision_id = r.id", src),
        "offer_revisions": await _scoped(session,
            "SELECT DISTINCT ors.offer_revision_id::text k " + corpus_join
            + "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id "
            "JOIN source_listing_revisions r ON r.incarnation_id = i.id "
            "JOIN offer_revision_sources ors ON ors.source_listing_revision_id = r.id", src),
        "link_evidence": await _scoped(session,
            "SELECT le.id::text k FROM link_evidence le "
            "JOIN source_listings sl ON sl.id = le.source_listing_id "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src", src),
        "dedup_candidates": await _scoped(session,
            "SELECT dc.id::text k FROM dedup_candidates dc WHERE EXISTS ("
            "  SELECT 1 FROM source_listing_incarnations i "
            "  JOIN source_listings sl ON sl.id = i.source_listing_id "
            "  JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "  WHERE i.ended_at IS NULL AND i.vacancy_id IN (dc.vacancy_a, dc.vacancy_b))", src),
    }
    # new = C-4 la sintetizó (sin incarnación de otra fuente); reused = preexistente
    # que C-4 solo enganchó (tiene incarnación de otra fuente).
    portfolio_vac = (
        "FROM vacancies v "
        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id AND i.ended_at IS NULL "
        "JOIN source_listings sl ON sl.id = i.source_listing_id "
        "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
        "WHERE v.merged_into IS NULL AND v.archived_at IS NULL "
    )
    other_source = (
        "EXISTS (SELECT 1 FROM source_listing_incarnations oi "
        "  JOIN source_listings osl ON osl.id = oi.source_listing_id "
        "  JOIN sources os ON os.id = osl.source_id AND os.name <> :src "
        "  WHERE oi.vacancy_id = v.id AND oi.ended_at IS NULL)"
    )
    ident["new_vacancies"] = await _scoped(session,
        "SELECT DISTINCT v.id::text k " + portfolio_vac + "AND NOT " + other_source, src)
    ident["reused_vacancies"] = await _scoped(session,
        "SELECT DISTINCT v.id::text k " + portfolio_vac + "AND " + other_source, src)
    return ident


async def migrate_and_reconcile(session: AsyncSession, users: list[dict]) -> dict:
    """ENTRYPOINT transaccional del cutover (scaffold LOCAL): MIGRA, RECONCILIA los
    VALORES materiales contra el origen (la verificación estructural INDEPENDIENTE y la
    procedencia EXACTA son del ensayo §4, gated NAS — ver reconcile/_captured_identities),
    captura el inventario scopeado de rollback + new/reused vacancies, y PERSISTE el
    manifiesto antes del commit.
    NO commitea: el llamador confirma SOLO si verdict=='ok' (si 'divergent', rollback —
    el DETALLE queda en el log ERROR y en el dict devuelto para volcar fuera de la tx).

    SINGLE-CALL: el cutover migra TODOS los durables en UNA llamada; la reconciliación
    compara el destino COMPLETO (scope portfolio) contra TODO `users`. No es para
    migración incremental multi-tanda (una 2ª tanda vería la 1ª como 'extra')."""
    report = await migrate_portfolio(session, users)
    manifest = await reconcile(session, users, report)
    manifest["report"] = {
        "users": report["users"], "applications": report["applications"],
        "saved_searches": report["saved_searches"],
    }
    manifest["staged"] = report["staged"]
    # Ledger del sink (§4): disposición verificable por url de la síntesis (created/reused/
    # quarantine+razón+vacancy_id). Base del verificador independiente (§4, parte 3).
    manifest["ledger"] = report["ledger"]
    manifest["identities"] = await _captured_identities(session)
    manifest["checksums"] = await table_checksums(session)
    manifest["id"] = str(await persist_manifest(session, manifest))
    return manifest


async def persist_manifest(session: AsyncSession, manifest: dict) -> uuid.UUID:
    """Persiste el manifiesto en portfolio_migration_manifest (core0013) DENTRO de la
    transacción del llamador → atómico con la migración (se revierte con ella en un
    dry-run o en 'divergent'; el log ERROR conserva el detalle). Devuelve el id."""
    manifest_id = uuid.uuid4()
    # Los durables staged pueden traer contenido TÓXICO (surrogates UTF-8 no
    # codificables, del mismo mojibake que el sink cuarentena). Se SANEA el JSON del
    # manifiesto (→ carácter de reemplazo) para que el propio informe sea persistible.
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    payload = payload.encode("utf-8", "replace").decode("utf-8")
    await session.execute(
        sa.text(
            "INSERT INTO portfolio_migration_manifest (id, verdict, manifest) "
            "VALUES (:id, :v, CAST(:m AS jsonb))"
        ),
        {"id": manifest_id, "v": manifest["verdict"], "m": payload},
    )
    return manifest_id
