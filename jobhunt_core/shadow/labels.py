"""Set etiquetado por perfil (B-03, CONTRATOS_FASE_B.md §4).

Flujo: `create_set` → `seed_labels`/`seed_dedup_pairs` (semillas trazables,
leídas READ-ONLY del esquema legacy) → CURACIÓN MANUAL con `add_judgment` →
`freeze_set`. Congelado el set (frozen_at NOT NULL), el oráculo es INMUTABLE:
toda escritura de juicios se rechaza — y el guard vive EN LA MISMA sentencia
SQL que el INSERT (cero TOCTOU: no hay ventana entre comprobar y escribir).

El esquema legacy es PARÁMETRO (`legacy_schema`): los tests usan un esquema
desechable; producción usará 'public' (GRANTs RO enumerados en §1, B-01).
El core JAMÁS escribe en el esquema legacy — aquí solo se hace SELECT.
"""

import json
import re
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Mapeo feedback legacy → relevancia (§4): fuente única de verdad — el CASE
# del SQL de seed se genera de aquí (valores constantes de módulo, no input).
FEEDBACK_RELEVANCE: dict[str, int] = {
    "applied": 3,
    "thumbs_up": 2,
    "thumbs_down": 0,
    "dismissed": 0,
}
SOURCE_SEED = "seed_feedback"
SOURCE_MANUAL = "manual"
# Origen trazable de los pares dedup sembrados (DoD B-03: "seeds trazables").
DEDUP_SEED_SOURCE = "seed_duplicate_of"
# Cohorte que el GATE evalúa (auditoría Nº2 BLOQUEANTE 1): el holdout ciego
# del protocolo (PROTOCOLO_HOLDOUT_DEDUP.md). Todo lo demás (seed, curado)
# es DEVELOPMENT: sirve para ajustar el detector, JAMÁS para puntuar el gate.
DEDUP_EVAL_COHORT = "holdout-dedup-2026-08-23"

# `legacy_schema` se interpola como identificador (no admite bind param):
# misma validación que migrate.py antes de interpolar DDL.
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

_CASE_FEEDBACK = " ".join(
    f"WHEN '{fb}' THEN {rel}" for fb, rel in FEEDBACK_RELEVANCE.items()
)
_IN_FEEDBACK = ", ".join(f"'{fb}'" for fb in FEEDBACK_RELEVANCE)


class LabeledSetNotFoundError(LookupError):
    """El set etiquetado no existe."""


class LabeledSetFrozenError(RuntimeError):
    """El set está CONGELADO (frozen_at NOT NULL): el oráculo no se toca."""


def _check_legacy_schema(legacy_schema: str) -> None:
    if not _IDENT_RE.match(legacy_schema):
        raise ValueError(f"legacy_schema inválido: {legacy_schema!r}")


async def create_set(
    session: AsyncSession, profile_id: uuid.UUID, name: str
) -> uuid.UUID:
    """Alta idempotente del set por (profile_id, name) — patrón upsert_profile."""
    await session.execute(
        sa.text(
            "INSERT INTO labeled_sets (id, profile_id, name) "
            "VALUES (:id, :pid, :name) "
            "ON CONFLICT (profile_id, name) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "pid": profile_id, "name": name},
    )
    return (
        await session.execute(
            sa.text(
                "SELECT id FROM labeled_sets "
                "WHERE profile_id = :pid AND name = :name"
            ),
            {"pid": profile_id, "name": name},
        )
    ).scalar_one()


async def add_judgment(
    session: AsyncSession,
    set_id: uuid.UUID,
    job_ref: str,
    relevance: int,
    source: str = SOURCE_MANUAL,
) -> None:
    """Juicio de CURACIÓN MANUAL: upsert — pisa un seed anterior (al revés no:
    el seed lleva DO NOTHING y jamás pisa curación).

    Guard de congelado EN LA MISMA sentencia (INSERT ... SELECT ... WHERE
    frozen_at IS NULL): 0 filas ⇒ set congelado o inexistente — sin ventana
    TOCTOU entre comprobar y escribir. La lectura posterior es solo para
    distinguir el TIPO de error, nunca decide si se escribe.
    """
    if not 0 <= relevance <= 3:
        raise ValueError(f"relevance fuera de 0..3: {relevance}")
    result = await session.execute(
        sa.text(
            "INSERT INTO labeled_judgments (set_id, job_ref, relevance, source) "
            "SELECT s.id, :ref, :rel, :src FROM labeled_sets s "
            "WHERE s.id = :sid AND s.frozen_at IS NULL "
            "ON CONFLICT (set_id, job_ref) DO UPDATE SET "
            "relevance = EXCLUDED.relevance, source = EXCLUDED.source, "
            "labeled_at = now()"
        ),
        {"sid": set_id, "ref": job_ref, "rel": relevance, "src": source},
    )
    if result.rowcount == 0:
        await _raise_frozen_or_missing(session, set_id)


async def seed_labels(
    session: AsyncSession, set_id: uuid.UUID, legacy_schema: str = "public"
) -> int:
    """Siembra el set desde el feedback legacy del usuario del perfil del set.

    `profiles.external_ref` del core = `user_id` legacy (§3): se leen sus
    `match_results.feedback` (read-only) y se mapean con FEEDBACK_RELEVANCE;
    otros feedbacks (NULL, valores desconocidos) no siembran. ON CONFLICT DO
    NOTHING: re-sembrar JAMÁS pisa la curación manual. Devuelve nº sembrados.

    Guard de congelado doble: FOR UPDATE sobre el set (bloquea un freeze
    concurrente hasta el commit y da el tipo de error exacto) + el MISMO
    predicado frozen_at IS NULL dentro del INSERT (cinturón y tirantes).
    La comparación user_id::text evita reventar si external_ref no es un
    UUID válido (simplemente no matchea nada).
    """
    _check_legacy_schema(legacy_schema)
    await _lock_and_require_unfrozen(session, set_id)
    result = await session.execute(
        sa.text(
            f"INSERT INTO labeled_judgments (set_id, job_ref, relevance, source) "
            f"SELECT s.id, mr.job_hash, "
            f"CASE mr.feedback {_CASE_FEEDBACK} END, '{SOURCE_SEED}' "
            f"FROM labeled_sets s "
            f"JOIN profiles p ON p.id = s.profile_id "
            f"JOIN {legacy_schema}.match_results mr "
            f"  ON mr.user_id::text = p.external_ref "
            f"WHERE s.id = :sid AND s.frozen_at IS NULL "
            f"  AND mr.feedback IN ({_IN_FEEDBACK}) "
            f"ON CONFLICT (set_id, job_ref) DO NOTHING"
        ),
        {"sid": set_id},
    )
    return result.rowcount


async def seed_dedup_pairs(
    session: AsyncSession, legacy_schema: str = "public", limit: int | None = None
) -> int:
    """Siembra pares verdict='duplicate' desde `{legacy_schema}.jobs.duplicate_of`.

    Normalizados (menor primero: a = LEAST, b = GREATEST) — (a,b) y (b,a) son
    el MISMO par y colisionan en el índice de expresión; DISTINCT absorbe el
    caso dentro del propio lote y ON CONFLICT DO NOTHING el re-seed. Los
    self-duplicados (hash = duplicate_of) violarían el CHECK a<>b: se filtran.
    ORDER BY estable: con `limit`, el recorte es determinista. Devuelve nº
    de pares insertados.
    """
    _check_legacy_schema(legacy_schema)
    result = await session.execute(
        sa.text(
            f"INSERT INTO labeled_dedup_pairs "
            f"(job_ref_a, job_ref_b, verdict, source) "
            f"SELECT pair.a, pair.b, 'duplicate', '{DEDUP_SEED_SOURCE}' "
            f"FROM (SELECT DISTINCT "
            f"        LEAST(j.hash, j.duplicate_of) AS a, "
            f"        GREATEST(j.hash, j.duplicate_of) AS b "
            f"      FROM {legacy_schema}.jobs j "
            f"      WHERE j.duplicate_of IS NOT NULL "
            f"        AND j.duplicate_of <> j.hash "
            f"      ORDER BY a, b LIMIT :lim) pair "
            f"ON CONFLICT (LEAST(job_ref_a, job_ref_b), "
            f"GREATEST(job_ref_a, job_ref_b), source) DO NOTHING"
        ),
        {"lim": limit},  # LIMIT NULL = sin límite (semántica Postgres)
    )
    return result.rowcount


async def freeze_dedup_cohort(
    session: AsyncSession, source: str, manifest: dict
) -> datetime:
    """Congela la cohorte de pares dedup `source` (auditoría Nº2 B-1;
    endurecido en las rondas 1-3 de la revisión solo-código):

    - `manifest` OBLIGATORIO y no vacío (ronda 2: el CHECK exige además
      jsonb_typeof = 'object').
    - La SERIALIZACIÓN con los escritores de pares vive en el TRIGGER de
      core0026 (frontera común: LOCK TABLE + canonicalización del instante
      efectivo post-lock — rondas 2 B-3 y 3 P-1). El helper NO toma locks
      propios: el lock explícito que llevaba invertía el orden respecto al
      UPDATE directo (fila de cohorte → pares) y PostgreSQL detectaba
      deadlock (ronda 3 P-2).
    - ORDEN DE ADQUISICIÓN consistente con el DML directo: primero UPDATE
      de la fila existente sin sellar (fila → pares, como cualquier UPDATE);
      si no hay fila, INSERT (pares → índice, como cualquier INSERT). Un
      upsert ON CONFLICT mezclaría ambos (BEFORE INSERT toma pares ANTES de
      la arbitración de la fila) y reabriría la inversión.
    - IDEMPOTENTE también en concurrencia: el perdedor de la carrera relee
      el sello confirmado y devuelve su timestamp (savepoint para que la
      violación de unicidad no aborte la tx del llamador).
    """
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError(
            "freeze_dedup_cohort exige un manifest dict NO vacío (los "
            "SHA-256 del pre-registro) — sin él, el sello no vale como "
            "corte de elegibilidad"
        )
    for _ in range(2):
        ya = await dedup_cohort_frozen_at(session, source)
        if ya is not None:
            return ya
        sellado = (
            await session.execute(
                sa.text(
                    "UPDATE labeled_dedup_cohorts "
                    "SET frozen_at = statement_timestamp(), "
                    "    manifest = CAST(:m AS jsonb) "
                    "WHERE source = :src AND frozen_at IS NULL "
                    "RETURNING frozen_at"
                ),
                {"src": source, "m": json.dumps(manifest)},
            )
        ).scalar_one_or_none()
        if sellado is not None:
            return sellado
        # Fila inexistente (o sellada por otro justo ahora): INSERT bajo
        # savepoint — si otro congelador gana la creación, se relee.
        try:
            async with session.begin_nested():
                return (
                    await session.execute(
                        sa.text(
                            "INSERT INTO labeled_dedup_cohorts "
                            "(source, frozen_at, manifest) "
                            "VALUES (:src, statement_timestamp(), "
                            "CAST(:m AS jsonb)) RETURNING frozen_at"
                        ),
                        {"src": source, "m": json.dumps(manifest)},
                    )
                ).scalar_one()
        except IntegrityError:
            continue  # otro congelador creó la fila: releer y devolver
    raise RuntimeError(
        f"freeze_dedup_cohort({source!r}): la cohorte no converge tras la "
        "carrera — estado inesperado en labeled_dedup_cohorts"
    )


async def dedup_cohort_frozen_at(
    session: AsyncSession, source: str
) -> datetime | None:
    """frozen_at de la cohorte, o None si no existe o no está congelada.
    FAIL-CLOSED (revisión B-3 + ronda 2 B-2): un sello sin manifest de
    pre-registro REAL no cuenta como congelado — ni jsonb vacío ni un tipo
    no-objeto ('null'::jsonb, arrays, strings, escalares: JSON null NO es
    NULL SQL y pasaba el filtro anterior). Solo posible en filas anteriores
    a core0026; la elegibilidad no se activa con un freeze sin acta."""
    return (
        await session.execute(
            sa.text(
                "SELECT frozen_at FROM labeled_dedup_cohorts "
                "WHERE source = :src AND jsonb_typeof(manifest) = 'object' "
                "  AND manifest <> '{}'::jsonb"
            ),
            {"src": source},
        )
    ).scalar_one_or_none()


async def freeze_set(session: AsyncSession, set_id: uuid.UUID) -> datetime:
    """Congela el set y devuelve frozen_at. IDEMPOTENTE: si ya estaba
    congelado devuelve el timestamp EXISTENTE sin error (COALESCE en una
    sola sentencia — sin ventana entre leer y escribir)."""
    frozen_at = (
        await session.execute(
            sa.text(
                "UPDATE labeled_sets SET frozen_at = COALESCE(frozen_at, now()) "
                "WHERE id = :sid RETURNING frozen_at"
            ),
            {"sid": set_id},
        )
    ).scalar_one_or_none()
    if frozen_at is None:
        raise LabeledSetNotFoundError(f"labeled_set inexistente: {set_id}")
    return frozen_at


async def map_job_refs_to_vacancies(
    session: AsyncSession, job_refs: Sequence[str]
) -> dict[str, uuid.UUID]:
    """Mapeo job_ref (hash legacy) → vacancy_id del core para MÉTRICAS (§4).

    Resuelve por `source_listings.external_id` en fuentes `legacy:%` y por
    CUALQUIER encarnación del slot — activa O CERRADA: la vacante persiste
    aunque el job legacy se desactive/borre (los pares de duplicate_of apuntan
    por definición a jobs ya desactivados). Determinista si hay varias: gana
    la de mayor seq (desempates por first_seen_at/id, orden total fijo).
    Los refs sin slot legacy quedan FUERA del dict (el llamador decide).
    """
    refs = list(job_refs)
    if not refs:
        return {}
    rows = (
        await session.execute(
            sa.text(
                "SELECT DISTINCT ON (l.external_id) "
                "  l.external_id AS job_ref, i.vacancy_id "
                "FROM source_listings l "
                "JOIN sources src ON src.id = l.source_id "
                "JOIN source_listing_incarnations i ON i.source_listing_id = l.id "
                "WHERE src.name LIKE 'legacy:%' AND l.external_id = ANY(:refs) "
                "ORDER BY l.external_id, i.seq DESC, i.first_seen_at DESC, i.id"
            ),
            {"refs": refs},
        )
    ).all()
    return {row.job_ref: row.vacancy_id for row in rows}


async def _lock_and_require_unfrozen(
    session: AsyncSession, set_id: uuid.UUID
) -> None:
    """SELECT ... FOR UPDATE del set: excepción exacta (inexistente/congelado)
    y bloqueo de un freeze concurrente hasta el commit de la siembra."""
    row = (
        await session.execute(
            sa.text("SELECT frozen_at FROM labeled_sets WHERE id = :sid FOR UPDATE"),
            {"sid": set_id},
        )
    ).one_or_none()
    if row is None:
        raise LabeledSetNotFoundError(f"labeled_set inexistente: {set_id}")
    if row.frozen_at is not None:
        raise LabeledSetFrozenError(
            f"labeled_set {set_id} congelado desde {row.frozen_at}: el oráculo no se toca"
        )


async def _raise_frozen_or_missing(session: AsyncSession, set_id: uuid.UUID) -> None:
    """Diagnóstico tras 0 filas del INSERT guardado: SOLO decide el tipo de
    excepción (la decisión de no escribir ya la tomó la sentencia atómica)."""
    row = (
        await session.execute(
            sa.text("SELECT frozen_at FROM labeled_sets WHERE id = :sid"),
            {"sid": set_id},
        )
    ).one_or_none()
    if row is None:
        raise LabeledSetNotFoundError(f"labeled_set inexistente: {set_id}")
    raise LabeledSetFrozenError(
        f"labeled_set {set_id} congelado desde {row.frozen_at}: el oráculo no se toca"
    )
