"""Procedencia EXACTA de la importación del portfolio (§4, parte 2; adelantada en LOCAL,
ejecución sobre datos reales gated al NAS).

`_captured_identities` (scaffold C-4) es un INVENTARIO SCOPEADO, NO procedencia: en un re-run
reaparecen las filas del run previo, y un offer_revision REUTILIZADO (preexistente de otra
fuente que C-4 solo enganchó) aparece aunque C-4 no lo creara. Aquí se produce la PROCEDENCIA
EXACTA — las filas que insertó ESTE run — vía SNAPSHOT ANTES/DESPUÉS de los PK-sets.

ALCANCE Y LÍMITE DE CONCURRENCIA (honesto): la fuente portfolio-import es single-writer (scope
deshabilitado) → las tablas OWNED son race-free. Las REUSABLE (vacancies/offer_revisions/
dedup_candidates) se snapshotean FULL-id, y el CORE COMPARTIDO SIGUE COSECHANDO durante el
cutover (el freeze congela el BFF del portfolio, no el harvest del core — RUNBOOK §0): una
vacante AJENA insertada por el core entre `antes` y `después` se COLARÍA en `después − antes`.
Esto NO es un borrado silencioso: (a) el ensayo §4 y los tests corren sobre una COPIA DESECHABLE
del core (sin harvest → single-writer real), y (b) toda sobre-captura la DETECTA el cross-check
del verificador (parte 3: created del ledger ≠ procedencia de vacancies → discrepant), jamás en
silencio. El artefacto INMUNE a concurrencia (procedencia por RETURNING en cada INSERT) es el
entregable del §4-REAL (gated NAS, RUNBOOK §4); este snapshot-diff es el ADELANTO en LOCAL.

SCOPING por tabla (clave para la exactitud):
- OWNED portfolio-import (scoped): source_listings/incarnations/revisions/offer_revision_sources/
  link_evidence/dedup_candidates/sources/harvest_scopes. Una fila de estas es portfolio-import
  PARA SIEMPRE (su scope NO cambia entre antes y después) → el diff scopeado es exacto.
- REUSABLE (FULL id-set): vacancies, offer_revisions. Un row REUTILIZADO (de otra fuente) ENTRA
  en el scope portfolio-import ESTE run, así que un snapshot scopeado lo daría como falso-nuevo.
  El id-set COMPLETO evita ese error: el row preexistía en `antes` aunque no fuera reachable.
  COSTE: O(tabla) — trivial en fixtures locales; en el NAS es un SELECT id de una pasada
  (optimizable con watermark/created_at o cruzando vacancies con el ledger `created`).
- DURABLES (consumer portfolio scoped): applications/application_status_events/saved_searches/
  profile_vacancy_state. Bajo el freeze la migración es el único escritor del consumer.

Captura INSERTS (después−antes). Un UPDATE sobre una fila PREEXISTENTE (p.ej. set_saved sobre
un profile_vacancy_state ya presente) NO es un insert → no aparecería en procedencia y el
rollback no lo desharía; por eso `migrate_applications` hace un PREFLIGHT fail-closed que ABORTA
(`PreexistingStateError`) si un profile_vacancy_state a bookmarkear ya existe — C-4 solo INSERTA
durables frescos (cota mono-piloto), jamás muta una fila que no creó (P1 rev. externa integral).

INTENCIONADAMENTE FUERA: `consumers` y `profiles` NO se capturan — son IDENTIDAD compartida
(el consumer/perfil los provisiona C-0/C-3 y los escribe el push de CV); el rollback de C-4
borra su DATO (durables + corpus), nunca la identidad. `vacancies` full-id es un oráculo
INDEPENDIENTE del ledger (parte 1): la parte 3 los cruza (created del ledger == procedencia de
vacancies). El módulo NO importa import_portfolio (recibe source_name/consumer_name) — sin ciclo.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Corpus alcanzable desde la fuente portfolio-import (el join base de las tablas OWNED).
_CORPUS_JOIN = "FROM source_listings sl JOIN sources s ON s.id = sl.source_id AND s.name = :src "


async def _ids(session: AsyncSession, sql: str, params: dict) -> set[str]:
    return {r.k for r in await session.execute(sa.text(sql), params)}


async def snapshot_row_ids(
    session: AsyncSession, source_name: str, consumer_name: str
) -> dict[str, set[str]]:
    """PK-sets (como str; claves compuestas unidas por ':') de cada tabla que C-4 escribe,
    en un instante. `después − antes` da la procedencia exacta. Ver el scoping por tabla en
    el docstring del módulo: OWNED scopeado, REUSABLE full-id, DURABLES por consumer."""
    src = {"src": source_name}
    cons = {"cons": consumer_name}
    snap: dict[str, set[str]] = {}

    # --- DURABLES (consumer portfolio) ---
    snap["applications"] = await _ids(
        session,
        "SELECT a.id::text k FROM applications a JOIN profiles p ON p.id = a.profile_id "
        "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons",
        cons,
    )
    snap["application_status_events"] = await _ids(
        session,
        "SELECT e.id::text k FROM application_status_events e "
        "JOIN applications a ON a.id = e.application_id "
        "JOIN profiles p ON p.id = a.profile_id "
        "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons",
        cons,
    )
    snap["profile_vacancy_state"] = await _ids(
        session,
        "SELECT (pvs.profile_id::text || ':' || pvs.vacancy_id::text) k "
        "FROM profile_vacancy_state pvs JOIN profiles p ON p.id = pvs.profile_id "
        "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons",
        cons,
    )
    snap["saved_searches"] = await _ids(
        session,
        "SELECT ss.id::text k FROM saved_searches ss JOIN profiles p ON p.id = ss.profile_id "
        "JOIN consumers c ON c.id = p.consumer_id AND c.name = :cons",
        cons,
    )

    # --- OWNED portfolio-import (scopeado; el scope de estas filas no cambia) ---
    snap["source_listings"] = await _ids(session, "SELECT sl.id::text k " + _CORPUS_JOIN, src)
    snap["source_listing_incarnations"] = await _ids(
        session,
        "SELECT i.id::text k " + _CORPUS_JOIN
        + "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id",
        src,
    )
    snap["source_listing_revisions"] = await _ids(
        session,
        "SELECT r.id::text k " + _CORPUS_JOIN
        + "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id "
        "JOIN source_listing_revisions r ON r.incarnation_id = i.id",
        src,
    )
    snap["offer_revision_sources"] = await _ids(
        session,
        "SELECT (ors.offer_revision_id::text || ':' || ors.source_listing_revision_id::text) k "
        + _CORPUS_JOIN
        + "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id "
        "JOIN source_listing_revisions r ON r.incarnation_id = i.id "
        "JOIN offer_revision_sources ors ON ors.source_listing_revision_id = r.id",
        src,
    )
    snap["link_evidence"] = await _ids(
        session,
        "SELECT le.id::text k FROM link_evidence le "
        "JOIN source_listings sl ON sl.id = le.source_listing_id "
        "JOIN sources s ON s.id = sl.source_id AND s.name = :src",
        src,
    )
    snap["sources"] = await _ids(
        session, "SELECT id::text k FROM sources WHERE name = :src", src
    )
    snap["harvest_scopes"] = await _ids(
        session,
        "SELECT hs.id::text k FROM harvest_scopes hs "
        "JOIN sources s ON s.id = hs.source_id AND s.name = :src",
        src,
    )

    # --- REUSABLE (FULL id-set: un row reutilizado preexistía aunque no fuera reachable) ---
    snap["vacancies"] = await _ids(session, "SELECT id::text k FROM vacancies", {})
    snap["offer_revisions"] = await _ids(session, "SELECT id::text k FROM offer_revisions", {})
    # dedup_candidates referencia vacantes REUTILIZABLES (vacancy_a/vacancy_b): un dc
    # PREEXISTENTE de otra fuente entra en cualquier scope portfolio-import cuando esa vacante
    # gana una incarnación este run → un snapshot scopeado lo daría como falso-nuevo. Full-id
    # (la síntesis no crea dedup_candidates —eso es el pipeline async— así que suele ser vacío).
    snap["dedup_candidates"] = await _ids(
        session, "SELECT id::text k FROM dedup_candidates", {}
    )
    return snap


def exact_provenance(
    before: dict[str, set[str]], after: dict[str, set[str]]
) -> dict[str, list[str]]:
    """{tabla: [ids insertados por ESTE run]} = después − antes, por tabla (ordenado para un
    manifiesto determinista). Las claves son las de `after` (superset del esquema)."""
    return {table: sorted(after.get(table, set()) - before.get(table, set())) for table in after}


async def scope_dedup_provenance(
    session: AsyncSession, provenance: dict[str, list[str]]
) -> dict[str, list[str]]:
    """G1 H-2: acota `dedup_candidates` del diff a los pares que TOCAN vacantes de la
    PROPIA procedencia. La síntesis jamás crea dedup_candidates (eso es el pipeline
    async): todo dc del diff es de un ESCRITOR CONCURRENTE del core. Los que tocan
    vacantes creadas por este run DEBEN quedarse (el rollback los necesita antes de
    borrar esas vacantes — FK); los que tocan solo vacantes AJENAS se EXCLUYEN — el
    rollback los borraría en silencio sin FK ni cross-check que lo parara (vacancies/
    offer_revisions sí tienen el cross-check del verificador, parte 3). Devuelve una
    COPIA con la lista filtrada; sin dc en el diff, no consulta nada."""
    dc_ids = provenance.get("dedup_candidates", [])
    if not dc_ids:
        return provenance
    own_vacancies = set(provenance.get("vacancies", []))
    rows = await session.execute(
        sa.text(
            "SELECT id::text AS k, vacancy_a::text AS a, vacancy_b::text AS b "
            "FROM dedup_candidates WHERE id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": dc_ids},
    )
    kept, foreign = [], []
    for r in rows:
        (kept if (r.a in own_vacancies or r.b in own_vacancies) else foreign).append(r.k)
    if foreign:
        logger.warning(
            "provenance: %d dedup_candidate(s) CONCURRENTES ajenos excluidos de la "
            "procedencia (no tocan vacantes de este run — G1 H-2): %s",
            len(foreign), foreign[:5],
        )
    out = dict(provenance)
    out["dedup_candidates"] = sorted(kept)
    return out
