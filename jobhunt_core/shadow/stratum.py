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
  INVARIANTE (G7-N-6): ese ref tiene que VOLVER a la misma vacante. Sobre un
  slot RECICLADO no vuelve —la vuelta gana por `seq DESC`— y el par acabaría
  puntuado contra otra oferta o desaparecido en el ON CONFLICT; se comprueba
  y se falla fuerte nombrando los pares (`_resolve_legacy_refs`); los que
  salgan nombrados se excluyen con `--excluir` (contador propio
  `excluidos_reciclado`), NUNCA editando el acta ratificada.
  G8-N-7, RIESGO DIFERIDO que este guard NO cubre y que conviene que esté en
  el acta de la cohorte: la comprobación es una FOTO del momento de la carga,
  y el espacio de nombres `job_ref = external_id` identifica al SLOT, no a la
  vacante. De los 187 pares cargables del acta ratificada, 30 se apoyan en
  uno de los 22 slots legacy que YA tienen más de una vacante: el PRÓXIMO
  reciclado de cualquiera de esos slots vuelve a re-apuntar el par en
  silencio. Y no hay lock entre el SELECT del guard y el INSERT (mismo READ
  COMMITTED), así que una cosecha concurrente puede reciclar en la ventana.
  El cierre real —ref por `vacancy_id`, o por `(external_id, seq)`— es otro
  trabajo y otra cohorte.
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
      candidatos.json etiquetas.json [--excluir B-17,C-05,...]
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.shadow import labels

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
    # G1 H-14b: un modo del miner fuera de _MODE_ORDER desaparecía en SILENCIO
    # (sus pares jamás se numeraban) — error fuerte, nunca una hoja incompleta.
    unknown = sorted(set(candidates) - set(_MODE_ORDER))
    if unknown:
        raise ValueError(
            f"modos de candidatos fuera de '{_MODE_ORDER}': {unknown} — "
            "la numeración de la hoja no los cubre"
        )
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
    manual_excluded: frozenset[str] = frozenset(),
) -> dict:
    """Carga idempotente del estrato etiquetado a la cohorte `cohort`.

    Registra la cohorte en labeled_dedup_cohorts SIN congelar (frozen_at
    NULL — el freeze con manifest es acto del operador) e inserta los pares
    con veredicto real, excluyendo ambiguous-owner y sintéticos. Devuelve el
    resumen de la pasada (conteos auditables). NO commitea.

    G8-P3-4 — `manual_excluded` (bandera `--excluir` del CLI) es la vía LIMPIA
    para los pares que las guardas fail-closed de `_resolve_legacy_refs`
    nombran: sin ella el operador solo podía editar el JSON del acta
    RATIFICADA —que es el artefacto de trazabilidad— o colarlos por
    `synthetic_excluded`, donde se contarían como `excluidos_sinteticos`, que
    es una etiqueta FALSA en un resumen auditable (no son los sintéticos
    B-26/27/28 del corpus de test: son slots reciclados). Salen con contador
    propio, `excluidos_reciclado`, para que la exclusión quede registrada como
    lo que es.
    """
    await _require_unfrozen(session, cohort)
    pair_refs = pair_ids_from_candidates(candidates)
    loadable, excluded = _classify_labels(
        labels_by_pair, pair_refs, synthetic_excluded, manual_excluded
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
                # Unicidad POR COHORTE (core0031, G1-P3-3): un par presente en
                # otra cohorte (holdout/seed/curado) SÍ entra en esta;
                # `ya_presentes` cuenta solo los que ya estaban en ESTA.
                "INSERT INTO labeled_dedup_pairs "
                "(job_ref_a, job_ref_b, verdict, source) "
                "VALUES (:a, :b, :v, :src) "
                "ON CONFLICT (LEAST(job_ref_a, job_ref_b), "
                "GREATEST(job_ref_a, job_ref_b), source) DO NOTHING"
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
        "excluidos_reciclado": excluded["reciclado"],
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
    manual_excluded: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], dict[str, int]]:
    """(cargables {pair_id: verdict}, conteos de exclusión). Falla FUERTE con
    pair_ids desconocidos o etiquetas fuera de vocabulario: un desajuste
    entre actas jamás debe cargarse a medias en silencio.

    La exclusión MANUAL se aplica antes que ninguna otra (es un acto explícito
    del operador) y también falla fuerte si nombra un pair_id que no está
    etiquetado: una exclusión con una errata que no excluye nada sería peor
    que no tenerla, porque el par entra igual y el resumen dice que se
    excluyó."""
    unknown = sorted(set(labels_by_pair) - set(pair_refs))
    if unknown:
        raise ValueError(
            f"pair_ids sin candidato en la hoja (¿actas desparejadas?): {unknown}"
        )
    fantasma = sorted(set(manual_excluded) - set(labels_by_pair))
    if fantasma:
        raise ValueError(
            f"--excluir nombra pair_ids que no están etiquetados: {fantasma} — "
            "no excluirían nada y el resumen diría lo contrario"
        )
    loadable: dict[str, str] = {}
    excluded = {AMBIGUOUS_OWNER_LABEL: 0, "sintetico": 0, "reciclado": 0}
    for pair_id, label in labels_by_pair.items():
        if pair_id in manual_excluded:
            excluded["reciclado"] += 1
        elif pair_id in synthetic_excluded:
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
    FUERTE si alguna vacante no resuelve a un slot legacy, si ambos lados
    colapsan en el mismo ref (violaría el CHECK a<>b), si el ref no VUELVE a
    su vacante o si dos pares distintos canonizan al mismo par.

    G7-N-6 — «1 vacante = 1 identidad primaria» es cierto, pero NO implica
    «1 identidad = 1 vacante», que es lo que este loader necesitaba. Sobre un
    slot RECICLADO (`harvest/sink.py:495-509`: cambia la identidad de empresa
    ⇒ se cierra la encarnación y se abre otra `seq+1` con vacante NUEVA sobre
    el MISMO `source_listing`) un `external_id` legacy mapea a N vacantes,
    cada una primaria de la suya. La dirección de VUELTA
    —`labels.map_job_refs_to_vacancies`, la que usan las métricas— resuelve
    por `ORDER BY i.seq DESC`, así que las dos direcciones NO eran inversas.
    Medido en el clúster el 2026-08-26: 569 `source_listings` `legacy:*` con
    más de una vacante y 569 vacantes cuyo round-trip no vuelve a sí misma;
    sobre el acta ratificada, 12 pares de 200 se evaporaban en el
    `ON CONFLICT DO NOTHING` contados como `ya_presentes` y SIN un solo log
    (este módulo no tiene logger), y 6 más quedaban puntuados contra una
    vacante distinta de la juzgada.

    El guard NO reimplementa el desempate: pregunta a la función REAL de
    vuelta, así que si un día cambia su orden total el invariante la sigue.
    Una vacante que no vuelve se convierte en un ERROR que NOMBRA sus pares
    —el operador los excluye o re-etiqueta sobre la vacante vigente— en vez
    de en una pérdida silenciosa; es la misma dirección fail-closed que el
    resto de la función."""
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
    # G7-N-6: el round-trip tiene que volver a la MISMA vacante. Se pregunta a
    # la función que usan las métricas (`map_job_refs_to_vacancies`), no a una
    # copia de su ORDER BY.
    de_vuelta = await labels.map_job_refs_to_vacancies(
        session, sorted(set(by_vid.values()))
    )
    no_vuelven = {
        vid for vid, ref in by_vid.items() if str(de_vuelta.get(ref)) != vid
    }
    resolved: dict[str, tuple[str, str]] = {}
    bad: list[str] = []
    reciclados: list[str] = []
    for pair_id in loadable:
        id_a, id_b = pair_refs[pair_id]
        ref_a, ref_b = by_vid.get(id_a), by_vid.get(id_b)
        if ref_a is None or ref_b is None or ref_a == ref_b:
            bad.append(pair_id)
        elif id_a in no_vuelven or id_b in no_vuelven:
            reciclados.append(pair_id)
        else:
            resolved[pair_id] = (ref_a, ref_b)
    if bad:
        raise ValueError(
            "pares sin job_ref legacy resoluble (vacante inexistente, sin "
            f"slot legacy, o refs colapsados): {sorted(bad)}"
        )
    if reciclados:
        raise ValueError(
            "pares sobre un slot legacy RECICLADO: su job_ref ya no resuelve a "
            "la vacante JUZGADA sino a la de mayor seq del mismo slot "
            "(G7-N-6), así que el par se puntuaría contra otra oferta — o, "
            "menos probable pero indistinguible desde aquí, su external_id lo "
            "comparten DOS slots legacy distintos y el DISTINCT ON de la "
            "vuelta colapsa uno (G8-N-5; 0 casos en el clúster el "
            "2026-08-26). Excluir con --excluir o re-etiquetar sobre la "
            f"vacante vigente: {sorted(reciclados)}"
        )
    # Dos pair_ids DISTINTOS que canonizan al mismo (LEAST, GREATEST): el
    # segundo INSERT moriría en el ON CONFLICT DO NOTHING contado como
    # `ya_presentes`, y si los veredictos se contradicen gana el primero por
    # orden alfabético del pair_id. Duplicado del acta, no dato: error fuerte.
    canonicos: dict[tuple[str, str], list[str]] = {}
    for pair_id, refs in resolved.items():
        canonicos.setdefault(tuple(sorted(refs)), []).append(pair_id)
    # G8-N-6: se ordenan también las listas INTERNAS (como `sorted(bad)` y
    # `sorted(reciclados)`), o el error sale como [['B-31','B-29'], ...].
    chocan = sorted(sorted(v) for v in canonicos.values() if len(v) > 1)
    if chocan:
        raise ValueError(
            "pares DISTINTOS del acta que canonizan al MISMO par de job_refs "
            f"(el segundo se perdería sin rastro en el ON CONFLICT): {chocan}"
        )
    return resolved


def parse_excluidos(texto: str) -> frozenset[str]:
    """`--excluir` → conjunto de pair_ids. Función aparte (y no un lambda en
    el `main`, que es `pragma: no cover`) para que el troceado tenga su propia
    regresión: los espacios de un copiar-pegar del error de la guarda —que
    imprime `['B-17', 'C-05']`— no pueden convertirse en pair_ids fantasma."""
    return frozenset(p.strip() for p in texto.split(",") if p.strip())


def main() -> None:  # pragma: no cover — envoltorio fino del CLI
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("candidates_json", help="payload de JSON_CANDIDATOS (minería)")
    ap.add_argument("labels_json", help="JSON pair_id → label (acta de etiquetado)")
    ap.add_argument("--cohorte", default=POSITIVE_STRATUM_COHORT)
    ap.add_argument(
        "--excluir", default="",
        help="pair_ids separados por coma a excluir de la carga (los que las "
             "guardas de round-trip o colisión hayan nombrado). Se contabilizan "
             "como `excluidos_reciclado`: el acta ratificada NO se toca",
    )
    args = ap.parse_args()
    manual = parse_excluidos(args.excluir)
    candidates = json.loads(Path(args.candidates_json).read_text())
    labels_by_pair = json.loads(Path(args.labels_json).read_text())

    async def run() -> dict:
        from jobhunt_core.database import task_session_factory

        async with task_session_factory() as factory:
            async with factory() as s:
                summary = await load_positive_stratum(
                    s, candidates, labels_by_pair, cohort=args.cohorte,
                    manual_excluded=manual,
                )
                await s.commit()
                return summary

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
