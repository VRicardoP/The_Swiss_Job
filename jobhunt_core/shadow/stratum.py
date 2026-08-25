"""Carga one-shot del ESTRATO POSITIVO como cohorte adicional de dedup.

Materializa la propuesta §4.1 de `ESTRATO_POSITIVO_CANDIDATOS_DEV_2026-08-25.md`:
los pares etiquetados del estrato entran en `labeled_dedup_pairs` con
`source='positive-stratum-v1'` — la columna `source` ES el mecanismo de
cohortes de core0025, así que una vez el OPERADOR la congele con
`labels.freeze_dedup_cohort` (acto APARTE: este loader JAMÁS congela) recibe
la MISMA inmutabilidad física que el holdout (trigger-guard core0025/core0026).

Entradas (dos actas, dos ficheros):
- `candidates`: el payload del bloque `<!-- JSON_CANDIDATOS ... -->` del acta
  de minería ({modo: [candidato, ...]}, ids = vacancy_id del core).
- `labels_by_pair`: el JSON pair_id → label del acta de etiquetado (§5),
  con pair_ids 'B-01'... que refieren a la NUMERACIÓN de la hoja renderizada.

Decisiones (no obvias):
- La numeración de la hoja se REPRODUCE aquí con el mismo orden del render
  del miner (sorted por -confianza, sort ESTABLE de Python): el JSON de
  candidatos conserva el orden de minería, no el de la tabla — sin re-derivar
  la numeración el pair_id no apunta a nada.
- Los refs se persisten como job_ref LEGACY (external_id del listing de la
  encarnación PRIMARIA de cada vacante): el MISMO espacio de nombres que el
  holdout/development. Así `map_job_refs_to_vacancies` los resuelve sin
  código nuevo (la fila informativa de metrics usa la misma consulta) y las
  pasadas futuras de minería los EXCLUYEN por refs como al resto.
- EXCLUSIONES del acta de etiquetado: los `ambiguous-owner` (§3 — la decisión
  es del propietario, no entran hasta que adjudique) y los pares SINTÉTICOS
  B-26/27/28 («SkipDedup Collapse Test», Clera — registros de test, no
  ofertas reales; §4 del acta manda excluirlos antes de congelar).
- IDEMPOTENTE: ON CONFLICT sobre el par canónico (LEAST/GREATEST) DO NOTHING
  — re-ejecutar no duplica ni pisa. Cohorte ya CONGELADA ⇒ error claro aquí
  (y aunque este check pierda una carrera, el trigger de core0025 corta el
  INSERT en la BD: el guard real no es este código).
- SIN commit en la función: el llamador decide (patrón import_portfolio_
  migrate). El main() del CLI sí commitea.

CLI (patrón migrate.py / miner):
  docker compose run --rm core-migrate python -m jobhunt_core.shadow.stratum \
      candidatos.json etiquetas.json
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# Cohorte v1 del estrato (§4.1). Las rondas futuras crean cohortes NUEVAS
# (positive-stratum-v2, ...) — nunca se añade a una cohorte congelada.
POSITIVE_STRATUM_COHORT = "positive-stratum-v1"
# Etiqueta del acta que NO es veredicto: pares reservados al propietario (§3).
AMBIGUOUS_OWNER_LABEL = "ambiguous-owner"
# Pares sintéticos de la hoja DEV 2026-08-25 («SkipDedup Collapse Test»,
# Clera): registros de TEST del corpus, no ofertas reales (§4 del acta).
SYNTHETIC_PAIR_IDS = frozenset({"B-26", "B-27", "B-28"})
# Orden de modos del miner (prioridad A>B>...>M): fija la numeración.
_MODE_ORDER = "ABCDEFM"
_VALID_VERDICTS = ("duplicate", "distinct")


class StratumFrozenError(RuntimeError):
    """La cohorte destino ya está CONGELADA: el estrato no se toca."""


def pair_ids_from_candidates(candidates: dict) -> dict[str, tuple[str, str]]:
    """pair_id ('B-01', ...) → (id_a, id_b) reproduciendo la numeración de la
    hoja: por modo, orden del render del miner (sorted por -confianza, sort
    estable ⇒ empates en el orden del JSON) y numeración 1..n con zero-pad."""
    out: dict[str, tuple[str, str]] = {}
    for mode in _MODE_ORDER:
        cands = sorted(candidates.get(mode, []), key=lambda c: -c["confianza"])
        for i, cand in enumerate(cands, 1):
            out[f"{mode}-{i:02d}"] = (cand["id_a"], cand["id_b"])
    return out


async def load_positive_stratum(
    session: AsyncSession,
    candidates: dict,
    labels_by_pair: dict[str, str],
    cohort: str = POSITIVE_STRATUM_COHORT,
    synthetic_excluded: frozenset[str] = SYNTHETIC_PAIR_IDS,
) -> dict:
    """Carga idempotente del estrato etiquetado a la cohorte `cohort`.

    Registra la cohorte en labeled_dedup_cohorts SIN congelar (frozen_at
    NULL — el freeze con manifest es acto del operador) e inserta los pares
    con veredicto real, excluyendo ambiguous-owner y sintéticos. Devuelve el
    resumen de la pasada (conteos auditables). NO commitea.
    """
    await _require_unfrozen(session, cohort)
    pair_refs = pair_ids_from_candidates(candidates)
    loadable, excluded = _classify_labels(
        labels_by_pair, pair_refs, synthetic_excluded
    )
    vacancy_refs = await _resolve_legacy_refs(session, loadable, pair_refs)
    await session.execute(
        sa.text(
            "INSERT INTO labeled_dedup_cohorts (source) VALUES (:src) "
            "ON CONFLICT (source) DO NOTHING"
        ),
        {"src": cohort},
    )
    inserted = 0
    for pair_id, verdict in sorted(loadable.items()):
        ref_a, ref_b = vacancy_refs[pair_id]
        result = await session.execute(
            sa.text(
                "INSERT INTO labeled_dedup_pairs "
                "(job_ref_a, job_ref_b, verdict, source) "
                "VALUES (:a, :b, :v, :src) "
                "ON CONFLICT (LEAST(job_ref_a, job_ref_b), "
                "GREATEST(job_ref_a, job_ref_b)) DO NOTHING"
            ),
            {"a": ref_a, "b": ref_b, "v": verdict, "src": cohort},
        )
        inserted += result.rowcount
    return {
        "cohorte": cohort,
        "total_hoja": len(pair_refs),
        "etiquetados": len(labels_by_pair),
        "cargables": len(loadable),
        "insertados": inserted,
        "ya_presentes": len(loadable) - inserted,
        "excluidos_ambiguous_owner": excluded[AMBIGUOUS_OWNER_LABEL],
        "excluidos_sinteticos": excluded["sintetico"],
        "congelada": False,  # el freeze es acto APARTE del operador (§4.1)
    }


async def _require_unfrozen(session: AsyncSession, cohort: str) -> None:
    """Error CLARO si la cohorte ya está sellada. Se mira frozen_at CRUDO
    (sin el filtro fail-closed de manifest de dedup_cohort_frozen_at): un
    sello, con o sin acta, bloquea la escritura vía trigger igualmente."""
    frozen_at = (
        await session.execute(
            sa.text(
                "SELECT frozen_at FROM labeled_dedup_cohorts WHERE source = :src"
            ),
            {"src": cohort},
        )
    ).scalar_one_or_none()
    if frozen_at is not None:
        raise StratumFrozenError(
            f"cohorte {cohort!r} CONGELADA desde {frozen_at}: el estrato es "
            "inmutable (core0025) — una ronda nueva va a una cohorte nueva"
        )


def _classify_labels(
    labels_by_pair: dict[str, str],
    pair_refs: dict[str, tuple[str, str]],
    synthetic_excluded: frozenset[str],
) -> tuple[dict[str, str], dict[str, int]]:
    """(cargables {pair_id: verdict}, conteos de exclusión). Falla FUERTE con
    pair_ids desconocidos o etiquetas fuera de vocabulario: un desajuste
    entre actas jamás debe cargarse a medias en silencio."""
    unknown = sorted(set(labels_by_pair) - set(pair_refs))
    if unknown:
        raise ValueError(
            f"pair_ids sin candidato en la hoja (¿actas desparejadas?): {unknown}"
        )
    loadable: dict[str, str] = {}
    excluded = {AMBIGUOUS_OWNER_LABEL: 0, "sintetico": 0}
    for pair_id, label in labels_by_pair.items():
        if pair_id in synthetic_excluded:
            excluded["sintetico"] += 1
        elif label == AMBIGUOUS_OWNER_LABEL:
            excluded[AMBIGUOUS_OWNER_LABEL] += 1
        elif label in _VALID_VERDICTS:
            loadable[pair_id] = label
        else:
            raise ValueError(
                f"etiqueta desconocida {label!r} en {pair_id} "
                f"(esperaba {_VALID_VERDICTS} o {AMBIGUOUS_OWNER_LABEL!r})"
            )
    return loadable, excluded


async def _resolve_legacy_refs(
    session: AsyncSession,
    loadable: dict[str, str],
    pair_refs: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    """pair_id → (job_ref_a, job_ref_b) legacy, vía la encarnación PRIMARIA
    de cada vacante (determinista: 1 vacante = 1 identidad primaria). Falla
    FUERTE si alguna vacante no resuelve a un slot legacy o si ambos lados
    colapsan en el mismo ref (violaría el CHECK a<>b)."""
    vids = sorted({v for p in loadable for v in pair_refs[p]})
    rows = (
        await session.execute(
            sa.text(
                "SELECT v.id AS vid, l.external_id AS ref "
                "FROM vacancies v "
                "JOIN source_listing_incarnations i "
                "  ON i.id = v.primary_incarnation_id "
                "JOIN source_listings l ON l.id = i.source_listing_id "
                "JOIN sources s ON s.id = l.source_id "
                "WHERE s.name LIKE 'legacy:%' AND v.id = ANY(:ids)"
            ),
            {"ids": [uuid.UUID(v) for v in vids]},
        )
    ).all()
    by_vid = {str(r.vid): r.ref for r in rows}
    resolved: dict[str, tuple[str, str]] = {}
    bad: list[str] = []
    for pair_id in loadable:
        id_a, id_b = pair_refs[pair_id]
        ref_a, ref_b = by_vid.get(id_a), by_vid.get(id_b)
        if ref_a is None or ref_b is None or ref_a == ref_b:
            bad.append(pair_id)
        else:
            resolved[pair_id] = (ref_a, ref_b)
    if bad:
        raise ValueError(
            "pares sin job_ref legacy resoluble (vacante inexistente, sin "
            f"slot legacy, o refs colapsados): {sorted(bad)}"
        )
    return resolved


def main() -> None:  # pragma: no cover — envoltorio fino del CLI
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("candidates_json", help="payload de JSON_CANDIDATOS (minería)")
    ap.add_argument("labels_json", help="JSON pair_id → label (acta de etiquetado)")
    ap.add_argument("--cohorte", default=POSITIVE_STRATUM_COHORT)
    args = ap.parse_args()
    candidates = json.loads(Path(args.candidates_json).read_text())
    labels_by_pair = json.loads(Path(args.labels_json).read_text())

    async def run() -> dict:
        from jobhunt_core.database import task_session_factory

        async with task_session_factory() as factory:
            async with factory() as s:
                summary = await load_positive_stratum(
                    s, candidates, labels_by_pair, cohort=args.cohorte
                )
                await s.commit()
                return summary

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
