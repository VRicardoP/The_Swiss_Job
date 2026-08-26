"""G6/P3-4 · Recalcula `jobs.fuzzy_hash` con la fórmula VIGENTE.

POR QUÉ EXISTE
--------------
`fuzzy_hash` es un valor DERIVADO de `title`+`company` que se PERSISTE, y hasta
ahora nadie lo rellenaba hacia atrás: la migración `b6b766fb5c35` creó la
columna sin backfill y `services/job_repository` solo lo refresca cuando el
portal RE-LISTA la oferta. Cada cambio de `_normalize_title`/`_normalize_company`
—y ha habido varios: los marcadores de diversidad, la puntuación, la guarda de
identidad degenerada de G3/P2-12— deja el corpus partido en dos algoritmos, sin
rastro de la partición.

Y eso importa porque las TRES consultas que comparan un `fuzzy_hash` recién
calculado contra los ALMACENADOS —`find_fuzzy_duplicate`,
`find_same_source_clone` y el prefiltro de `find_semantic_duplicates`— dejan de
ver a las filas del algoritmo viejo.

Medido en producción 2026-08-26 (SOLO LECTURA): **610 de 10.524 filas activas**
llevan un hash que el código de hoy no produciría; 570 son identidades
degeneradas (`company` vacía) para las que G3/P2-12 decidió emitir `""`, y las
otras 40 cambiaron por ajustes de normalización (espacio final, `®`).

USO
---
    docker compose exec -T backend python scripts/g6_backfill_fuzzy_hash.py
    docker compose exec -T backend python scripts/g6_backfill_fuzzy_hash.py --apply

Sin `--apply` NO escribe nada: cuenta, agrupa por fuente y muestra ejemplos. Es
un recálculo idempotente de una columna derivada, así que puede repetirse.

INVARIANTE: si se toca `Deduplicator.compute_fuzzy_hash` o alguna de sus dos
normalizaciones, hay que ejecutar esto después (o incluir el UPDATE en la misma
migración). Está escrito también en el docstring de `compute_fuzzy_hash`.
"""

import argparse
import asyncio
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from database import task_session  # noqa: E402
from services.deduplicator import Deduplicator  # noqa: E402


async def _run(apply: bool) -> int:
    async with task_session() as db:
        filas = (
            await db.execute(
                text(
                    "SELECT hash, source, title, company, fuzzy_hash "
                    "FROM jobs WHERE is_active"
                )
            )
        ).all()

        desfasadas = [
            (h, src, title, company, fh or "", nuevo)
            for h, src, title, company, fh in filas
            for nuevo in [Deduplicator.compute_fuzzy_hash(title or "", company or "")]
            if nuevo != (fh or "")
        ]

        por_fuente = collections.Counter(src for _, src, *_ in desfasadas)
        print(f"filas activas: {len(filas)}")
        print(f"con fuzzy_hash almacenado != recalculado: {len(desfasadas)}")
        for src, n in por_fuente.most_common():
            print(f"   {src:<24} {n}")
        for h, src, title, company, viejo, nuevo in desfasadas[:5]:
            print(
                f"   ej. {src}: {title[:40]!r} / {company!r} "
                f"{viejo[:8] or '(vacio)'} -> {nuevo[:8] or '(vacio)'}"
            )

        if not apply:
            print("\nENSAYO: no se ha escrito nada. Repetir con --apply.")
            return 0

        for h, _src, _title, _company, _viejo, nuevo in desfasadas:
            await db.execute(
                text("UPDATE jobs SET fuzzy_hash = :fh WHERE hash = :h"),
                {"fh": nuevo, "h": h},
            )
        await db.commit()
        print(f"\nAPLICADO: {len(desfasadas)} filas actualizadas.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="escribe de verdad; sin este flag solo informa",
    )
    return asyncio.run(_run(parser.parse_args().apply))


if __name__ == "__main__":
    raise SystemExit(main())
