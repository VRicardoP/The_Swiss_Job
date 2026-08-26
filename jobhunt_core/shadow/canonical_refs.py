"""Re-mapeo de los `job_ref` del core al hash CANÓNICO de la canonización legacy.

Las etiquetas del oráculo de la sombra viven en el espacio de nombres del
`hash` legacy: `labeled_judgments.job_ref` se siembra de
`match_results.job_hash` y `labeled_dedup_pairs.job_ref_a/b` de
`jobs.hash`/`jobs.duplicate_of` (`shadow/labels.py`). `map_job_refs_to_
vacancies` los resuelve contra `jobhunt.source_listings.external_id` de las
fuentes `legacy:%`, y los refs sin slot quedan FUERA del dict SIN error.

La canonización de identidad (`backend/scripts/g3_…`, `g6_…`) reescribe ese
hash: el PASO 7 lo cambia en `jobs` y arrastra a mano `match_results`,
`job_applications`, `generated_documents` y `jobs.duplicate_of` (el FK es
`ON UPDATE NO ACTION`), y el PASO 7c reapunta `jobhunt.source_listings.
external_id`. Lo ÚNICO que queda con la clave vieja son las etiquetas del
core, que no tienen FK ni viven en el esquema legacy — y su ruptura es
SILENCIOSA por el contrato de `map_job_refs_to_vacancies`. Medido contra
producción el 2026-08-26 (SOLO SELECT): de 91 juicios de los 3 sets
congelados, 10 dejaban de resolver —8 de ellos del MISMO set, y 6 con
`relevance > 0` de sus 20 relevantes—, y de 260 pares mapeables se perdía 1.
Este módulo es la otra mitad del PASO 7c.

CÓMO SE RECONSTRUYE EL MAPA (y por qué se ejecuta DESPUÉS de la maniobra).
No se duplica la lógica de canonización de URL de los scripts: los scripts NO
tocan `jobs.url`, así que tras la maniobra el hash VIEJO de una fila
canonizada es exactamente `md5(lower(btrim(title))|lower(btrim(company))|url)`
—la misma fórmula de `BaseJobProvider.compute_hash` que los scripts usan como
salvaguarda— y el NUEVO es su `jobs.hash`. Una fila NO canonizada reproduce su
propio hash y no entra en el mapa. Medido el 2026-08-26 ANTES de la maniobra:
de 10.805 filas de `jobs`, **0** no reproducen su hash, así que tras la
maniobra el conjunto «no reproduce» es EXACTAMENTE el de las canonizadas y la
reconstrucción no tiene falsos positivos. El guard de ambigüedad está de todos
modos: si dos filas reconstruyeran el mismo hash viejo, se aborta.

ORDEN OPERATIVO. Con los workers PARADOS (el paso 2 del ORDEN de los scripts),
DESPUÉS del COMMIT de los dos scripts y ANTES de `docker compose start`. Así
ningún ciclo de métricas observa el estado intermedio, y si la maniobra
legacy aborta —va entera en una transacción— este módulo simplemente no se
ejecuta y no hay nada que deshacer.

IDEMPOTENTE: re-ejecutarlo no hace nada (los refs ya canónicos no están en el
mapa). NO commitea: el llamador decide (patrón `stratum.py`); el CLI sí.

CLI:
  docker compose run --rm core-migrate python -m jobhunt_core.shadow.canonical_refs
  docker compose run --rm core-migrate python -m jobhunt_core.shadow.canonical_refs --dry-run
"""

import argparse
import asyncio
import json

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.shadow.labels import _check_legacy_schema

# md5("titulo|empresa|url") con título y empresa en minúsculas y sin espacios
# en los extremos — BaseJobProvider.compute_hash, y la misma expresión que los
# scripts de canonización usan como salvaguarda (`check_hash`).
_HASH_EXPR = (
    "md5(lower(btrim(j.title)) || '|' || lower(btrim(j.company)) || '|' || j.url)"
)


class FrozenCohortError(RuntimeError):
    """Hay una cohorte dedup CONGELADA: sus pares son inmutables (core0025) y
    el re-mapeo NO puede completarse. Se aborta antes de tocar nada."""


async def remap_canonical_refs(
    session: AsyncSession, *, legacy_schema: str = "public", dry_run: bool = False
) -> dict:
    """Reapunta los `job_ref` del core al hash canónico. Devuelve el resumen
    auditable. NO commitea.

    Falla FUERTE (y antes de escribir nada) si el mapa es ambiguo, si alguna
    cohorte dedup está congelada, o si el re-mapeo produciría una colisión:
    un `(set_id, job_ref)` ya existente, un par con `a = b` (viola el CHECK) o
    dos pares del acta que canonizan al mismo par. Ninguna de las tres puede
    resolverse adivinando.
    """
    _check_legacy_schema(legacy_schema)
    mapa = await _build_map(session, legacy_schema)
    if not mapa:
        return _resumen(0, 0, 0, dry_run, len(mapa))
    await _require_no_frozen_affected(session)
    await _require_no_collisions(session)
    antes = await _conteos(session)
    if dry_run:
        juicios = await _scalar(
            session,
            "SELECT count(*) FROM labeled_judgments j "
            "JOIN canon_map m ON m.old_hash = j.job_ref",
        )
        pares = await _scalar(
            session,
            "SELECT count(*) FROM labeled_dedup_pairs p JOIN canon_map m "
            "ON m.old_hash IN (p.job_ref_a, p.job_ref_b)",
        )
        return _resumen(juicios, pares, len(mapa), dry_run, len(mapa)) | antes
    juicios = await _remap_judgments(session)
    pares = await _remap_pairs(session)
    despues = await _conteos(session)
    # El re-mapeo es una RE-EXPRESIÓN de la clave, no una edición del juicio:
    # ni el número de juicios por set ni el de pares por cohorte puede moverse.
    # Los sets congelados se tocan a propósito y esta invariante es lo que
    # hace que eso sea legítimo (`relevance`, `verdict` y `source` intactos).
    if antes != despues:
        raise RuntimeError(
            f"el re-mapeo cambió los conteos ({antes} → {despues}): es una "
            "re-expresión de clave, jamás una edición del juicio"
        )
    return _resumen(juicios, pares, len(mapa), dry_run, len(mapa)) | despues


def _resumen(juicios, pares, filas_mapa, dry_run, total) -> dict:
    return {
        "filas_canonizadas_en_legacy": total,
        "juicios_remapeados": juicios,
        "pares_remapeados": pares,
        "dry_run": dry_run,
    }


async def _scalar(session: AsyncSession, sql: str) -> int:
    return (await session.execute(sa.text(sql))).scalar_one()


async def _require_no_frozen_affected(session: AsyncSession) -> None:
    """Aborta si una cohorte SELLADA tiene pares que este re-mapeo tendría que
    tocar: el trigger de core0025 los hace inmutables y el UPDATE moriría a
    mitad.

    El filtro es «cohortes AFECTADAS», no «cohortes selladas», y la diferencia
    importa: el plan es sellar el holdout, y un guard global convertiría el
    primer sello en un veto PERMANENTE sobre cualquier re-mapeo futuro, aunque
    esa cohorte no tuviera un solo ref canonizable. Cuando sí los tenga, la
    salida NO es forzar el sello —existe para que el acta no se reescriba—
    sino cargar una cohorte NUEVA con los refs canónicos y retirar la vieja
    del gate."""
    afectadas = (
        (
            await session.execute(
                sa.text(
                    "SELECT DISTINCT p.source FROM labeled_dedup_pairs p "
                    "JOIN labeled_dedup_cohorts c ON c.source = p.source "
                    "JOIN canon_map m "
                    "  ON m.old_hash IN (p.job_ref_a, p.job_ref_b) "
                    "WHERE c.frozen_at IS NOT NULL ORDER BY p.source"
                )
            )
        )
        .scalars()
        .all()
    )
    if afectadas:
        raise FrozenCohortError(
            f"cohortes dedup CONGELADAS con pares a re-mapear: {list(afectadas)}"
            " — sus pares son inmutables (core0025) y el re-mapeo no puede "
            "completarse. Una cohorte sellada NO se reescribe: se carga una "
            "cohorte NUEVA con los refs canónicos y la vieja se retira del gate"
        )


async def _build_map(session: AsyncSession, legacy_schema: str) -> list:
    """Tabla TEMPORAL `canon_map` (old_hash → new_hash) reconstruida de
    `jobs`. Temporal y no CTE porque la consultan cuatro sentencias distintas
    y una de ellas es el guard que decide si se escribe."""
    # `pg_temp.` explícito: un `DROP TABLE IF EXISTS canon_map` a secas
    # resuelve por search_path y podría borrar una tabla REAL homónima.
    await session.execute(sa.text("DROP TABLE IF EXISTS pg_temp.canon_map"))
    await session.execute(
        sa.text(
            "CREATE TEMP TABLE canon_map AS "
            f"SELECT {_HASH_EXPR} AS old_hash, j.hash AS new_hash "
            f"FROM {legacy_schema}.jobs j "
            f"WHERE {_HASH_EXPR} <> j.hash"
        )
    )
    ambiguos = (
        (
            await session.execute(
                sa.text(
                    "SELECT old_hash FROM canon_map GROUP BY old_hash "
                    "HAVING count(*) > 1 ORDER BY old_hash"
                )
            )
        )
        .scalars()
        .all()
    )
    if ambiguos:
        raise ValueError(
            "mapa AMBIGUO: estos hashes viejos se reconstruyen desde más de "
            f"una fila legacy y no se puede elegir: {list(ambiguos)}"
        )
    await session.execute(sa.text("CREATE UNIQUE INDEX ON canon_map (old_hash)"))
    return (
        (await session.execute(sa.text("SELECT old_hash FROM canon_map"))).scalars().all()
    )


async def _require_no_collisions(session: AsyncSession) -> None:
    """Las tres formas en que el re-mapeo perdería una etiqueta en silencio.
    Fail-closed y NOMBRANDO, misma dirección que `stratum._resolve_legacy_refs`."""
    choque_juicios = (
        await session.execute(
            sa.text(
                "SELECT j.set_id, m.new_hash FROM labeled_judgments j "
                "JOIN canon_map m ON m.old_hash = j.job_ref "
                "WHERE EXISTS (SELECT 1 FROM labeled_judgments y "
                "  WHERE y.set_id = j.set_id AND y.job_ref = m.new_hash) "
                "ORDER BY j.set_id, m.new_hash"
            )
        )
    ).all()
    if choque_juicios:
        raise ValueError(
            "el re-mapeo chocaría con juicios ya presentes en el mismo set "
            f"(viola UNIQUE(set_id, job_ref)): {[(str(a), b) for a, b in choque_juicios]}"
        )
    # Un par cuyos DOS lados canonizan al mismo hash: `a = b` viola el CHECK.
    colapsan = (
        (
            await session.execute(
                sa.text(
                    "SELECT p.job_ref_a || '/' || p.job_ref_b "
                    "FROM labeled_dedup_pairs p "
                    "LEFT JOIN canon_map ma ON ma.old_hash = p.job_ref_a "
                    "LEFT JOIN canon_map mb ON mb.old_hash = p.job_ref_b "
                    "WHERE COALESCE(ma.new_hash, p.job_ref_a) "
                    "    = COALESCE(mb.new_hash, p.job_ref_b) "
                    "ORDER BY 1"
                )
            )
        )
        .scalars()
        .all()
    )
    if colapsan:
        raise ValueError(
            "pares cuyos DOS lados canonizan al MISMO hash (violarían el "
            f"CHECK job_ref_a <> job_ref_b): {list(colapsan)}"
        )
    # Dos pares DISTINTOS que acaban en el mismo par canónico: el segundo
    # UPDATE moriría en el índice de expresión por cohorte (core0031).
    duplican = (
        (
            await session.execute(
                sa.text(
                    "SELECT source || ':' || a || '/' || b FROM ("
                    "  SELECT p.source, "
                    "    LEAST(COALESCE(ma.new_hash, p.job_ref_a), "
                    "          COALESCE(mb.new_hash, p.job_ref_b)) AS a, "
                    "    GREATEST(COALESCE(ma.new_hash, p.job_ref_a), "
                    "             COALESCE(mb.new_hash, p.job_ref_b)) AS b "
                    "  FROM labeled_dedup_pairs p "
                    "  LEFT JOIN canon_map ma ON ma.old_hash = p.job_ref_a "
                    "  LEFT JOIN canon_map mb ON mb.old_hash = p.job_ref_b "
                    ") q GROUP BY source, a, b HAVING count(*) > 1 ORDER BY 1"
                )
            )
        )
        .scalars()
        .all()
    )
    if duplican:
        raise ValueError(
            "pares DISTINTOS que canonizan al MISMO par de job_refs (el "
            f"segundo moriría en el índice por cohorte): {list(duplican)}"
        )


async def _conteos(session: AsyncSession) -> dict:
    """Los invariantes que el re-mapeo NO puede mover: cuántos juicios tiene
    cada set y cuántos pares cada cohorte."""
    juicios = (
        await session.execute(
            sa.text(
                "SELECT set_id::text, count(*) FROM labeled_judgments "
                "GROUP BY set_id ORDER BY 1"
            )
        )
    ).all()
    pares = (
        await session.execute(
            sa.text(
                "SELECT source, count(*) FROM labeled_dedup_pairs "
                "GROUP BY source ORDER BY 1"
            )
        )
    ).all()
    return {
        "juicios_por_set": {k: n for k, n in juicios},
        "pares_por_cohorte": {k: n for k, n in pares},
    }


async def _remap_judgments(session: AsyncSession) -> int:
    result = await session.execute(
        sa.text(
            "UPDATE labeled_judgments j SET job_ref = m.new_hash "
            "FROM canon_map m WHERE m.old_hash = j.job_ref"
        )
    )
    return result.rowcount


async def _remap_pairs(session: AsyncSession) -> int:
    """Los DOS lados en UNA sentencia, por `id`. En dos pasadas, un par con
    ambos lados canonizables pasaría por un estado intermedio que puede chocar
    en el índice de expresión por cohorte (core0031) contra su propia forma
    final.

    Se RE-NORMALIZA a LEAST/GREATEST: el par canónico no cambia al reordenar
    (el índice es de expresión y las métricas comparan `frozenset`), pero
    `seed_dedup_pairs` guarda siempre el menor primero y un re-mapeo que
    deje `a > b` rompería esa convención sin que nada lo delate."""
    result = await session.execute(
        sa.text(
            "UPDATE labeled_dedup_pairs p "
            "SET job_ref_a = LEAST(q.a, q.b), job_ref_b = GREATEST(q.a, q.b) "
            "FROM ("
            "  SELECT p2.id, "
            "    COALESCE(ma.new_hash, p2.job_ref_a) AS a, "
            "    COALESCE(mb.new_hash, p2.job_ref_b) AS b "
            "  FROM labeled_dedup_pairs p2 "
            "  LEFT JOIN canon_map ma ON ma.old_hash = p2.job_ref_a "
            "  LEFT JOIN canon_map mb ON mb.old_hash = p2.job_ref_b "
            "  WHERE ma.old_hash IS NOT NULL OR mb.old_hash IS NOT NULL"
            ") q WHERE p.id = q.id"
        )
    )
    return result.rowcount


def main() -> None:  # pragma: no cover — envoltorio fino del CLI
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--legacy-schema", default="public")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="mide sin escribir (no commitea nada)",
    )
    args = ap.parse_args()

    async def run() -> dict:
        from jobhunt_core.database import task_session_factory

        async with task_session_factory() as factory:
            async with factory() as s:
                resumen = await remap_canonical_refs(
                    s, legacy_schema=args.legacy_schema, dry_run=args.dry_run
                )
                if args.dry_run:
                    await s.rollback()
                else:
                    await s.commit()
                return resumen

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
