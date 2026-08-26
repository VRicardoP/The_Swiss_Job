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

Y eso importa porque las DOS consultas que comparan un `fuzzy_hash` recién
calculado contra los ALMACENADOS —`find_fuzzy_duplicate` y
`find_same_source_clone`— dejan de ver a las filas del algoritmo viejo.

G7/P3-5 — aquí decía «las TRES consultas» e incluía «el prefiltro de
`find_semantic_duplicates`». Ese prefiltro NO existe: `find_semantic_duplicates`
no menciona `fuzzy_hash` en ninguna parte y su único llamante
(`tasks/maintenance_tasks.py`) tampoco prefiltra. La afirmación inflaba en un
50 % la superficie afectada.

QUÉ REPARA HOY: NADA. Medido contra producción el 2026-08-26 (SOLO LECTURA,
recalculando con el `compute_fuzzy_hash` vivo): **610 de 10.524 filas activas**
llevan el hash de un algoritmo anterior, pero **0 de ellas oculta un duplicado**
— ni cross-source ni same-source. Las 570 degeneradas llevan `MD5("titulo|")`,
que el algoritmo de hoy NO PUEDE producir (con `company` normalizada a vacío
devuelve `""`): son inalcanzables antes y después del backfill. Y las 40
restantes no ocultan ni un par. **Decisiones de dedup que este script cambiaría
hoy: 0.** Se conserva porque el PRÓXIMO cambio de fórmula sí las cambiará, no
porque hoy repare algo.

De dónde salen esas 40, re-derivando cada variante histórica de la fórmula y
comparando contra el hash almacenado (medido, 26 + 14 = 40 exactas): **26 por la
falta de `_DIVERSITY_RE`** (el marcador `(gn)`, `(m/w/d)`… sobrevivía a trozos)
y **14 por filtrar la seniority por SUBSTRING en vez de por token**. Cero por
espacio sobrante y cero por `®`: a `®` lo elimina `_PUNCT_RE` desde siempre, en
las dos versiones. (La atribución anterior —«ajustes de normalización (espacio
final, `®`)»— era falsa en sus dos mitades.)

Y el invariante «quien toque la fórmula DEBE ejecutar el backfill» ya no depende
de que alguien lea este párrafo: `tests/test_g7_fix_deduplicador.py` fija el hash
de cuatro pares `(title, company)` conocidos, así que tocar la fórmula ROMPE la
suite con el mensaje que dice qué hacer.

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
