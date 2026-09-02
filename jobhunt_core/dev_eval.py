"""Evaluador CANÓNICO de desarrollo para políticas sombra (Fase 1, cierre v5).

Una sola métrica de desarrollo, reproducible desde el repo:

    python -m jobhunt_core.dev_eval --policy hybrid-rrf:v4 \
        --judgments /tmp/juicios.csv [--unsure /tmp/unsure.json] \
        [--model <uuid>] --profile P1=<uuid> --profile P2=<uuid>

- La fórmula es LA DEL GATE, importada de shadow.metrics (_dcg, NDCG_K): la
  desviación lineal 0.557/0.617 vs 0.440/0.538 ya produjo un falso avance —
  este módulo existe para que no pueda repetirse.
- El feed sombra se reconstruye con matching.shadow_feed (única definición:
  revisión vigente del perfil, revisión canónica vigente de cada vacante,
  vacante activa, modelo indicado).
- Falla CERRADO: política/perfil/modelo irresolubles, juicios duplicados o
  malformados, vacante juzgada inexistente, o varios modelos activos sin
  --model, abortan con error nombrable. IDCG<=0 ⇒ `no_medible`, jamás verde.
- `unsure` se excluye de forma EXPLÍCITA (fichero aparte) y se reporta.
- La salida es determinista (claves ordenadas); el manifiesto lleva release,
  política+receta, modelo, revisiones, corpus_generation y hashes de insumos.

Formato de juicios: CSV `perfil,vacancy_uuid,rel` con rel ∈ {0,1,2}; el
fichero unsure es JSON {perfil: [vacancy_uuid, ...]}.
"""
import argparse
import asyncio
import datetime
import hashlib
import json
import os
import sys
import uuid as uuid_mod

import sqlalchemy as sa

from jobhunt_core import matching
from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow.metrics import NDCG_K, _dcg

EVALUATOR_VERSION = "dev-eval-v1"


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_judgments(path: str, profiles: dict) -> dict:
    """{perfil: {vacancy_uuid: rel}} — falla cerrado ante duplicados con rel
    distinto, rel fuera de {0,1,2}, perfil desconocido o UUID inválido."""
    out: dict[str, dict[str, int]] = {name: {} for name in profiles}
    with open(path, encoding="utf-8") as fh:
        for n, linea in enumerate(fh, 1):
            linea = linea.strip()
            if not linea:
                continue
            partes = linea.split(",")
            if len(partes) != 3:
                raise ValueError(f"juicios línea {n}: formato inválido {linea!r}")
            perfil, vac, rel = partes
            if perfil not in out:
                raise ValueError(f"juicios línea {n}: perfil desconocido {perfil!r}")
            uuid_mod.UUID(vac)  # valida o revienta
            if rel not in {"0", "1", "2"}:
                raise ValueError(
                    f"juicios línea {n}: rel {rel!r} inválida (unsure va en "
                    "su fichero, no aquí)"
                )
            previo = out[perfil].get(vac)
            if previo is not None and previo != int(rel):
                raise ValueError(
                    f"juicios línea {n}: {perfil}/{vac} ya juzgado como "
                    f"{previo}, ahora {rel} — juicio ambiguo"
                )
            out[perfil][vac] = int(rel)
    return out


async def _resolve_policy(session, spec: str):
    name, _, version = spec.partition(":")
    if not version:
        raise ValueError(f"--policy debe ser name:version, no {spec!r}")
    fila = (
        await session.execute(
            sa.text(
                "SELECT id, weights FROM scoring_policies "
                "WHERE name = :n AND prompt_version = :v"
            ),
            {"n": name, "v": version},
        )
    ).one_or_none()
    if fila is None:
        raise ValueError(f"política inexistente: {spec}")
    return fila.id, fila.weights


async def _resolve_model(session, model_id: str | None):
    if model_id is not None:
        fila = (
            await session.execute(
                sa.text("SELECT id FROM embedding_models WHERE id = :m"),
                {"m": model_id},
            )
        ).scalar_one_or_none()
        if fila is None:
            raise ValueError(f"modelo inexistente: {model_id}")
        return fila
    filas = (
        await session.execute(
            sa.text("SELECT id FROM embedding_models WHERE active ORDER BY id")
        )
    ).scalars().all()
    if len(filas) != 1:
        raise ValueError(
            f"{len(filas)} modelos activos: indica --model explícitamente"
        )
    return filas[0]


async def _verify_judged_vacancies(session, juicios: dict) -> None:
    """Toda vacante juzgada debe EXISTIR — un typo de UUID no puede degradar
    en silencio a 'no está en el feed'."""
    todas = sorted({v for por_perfil in juicios.values() for v in por_perfil})
    if not todas:
        return
    existentes = {
        str(r[0]) for r in (
            await session.execute(
                sa.text("SELECT id FROM vacancies WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": todas},
            )
        ).all()
    }
    perdidas = [v for v in todas if v not in existentes]
    if perdidas:
        raise ValueError(f"vacantes juzgadas inexistentes en la BD: {perdidas}")


async def evaluate_dev(
    session, policy_spec: str, profiles: dict, judgments_path: str,
    unsure_path: str | None = None, model_id: str | None = None,
) -> dict:
    """Métricas de desarrollo de UNA política sombra con la fórmula del gate.

    `profiles` = {nombre: profile_uuid}. Devuelve un dict determinista con
    manifiesto + métricas por perfil.
    """
    policy_id, weights = await _resolve_policy(session, policy_spec)
    mid = await _resolve_model(session, model_id)
    juicios = load_judgments(judgments_path, profiles)
    unsure: dict[str, list] = {}
    if unsure_path:
        with open(unsure_path, encoding="utf-8") as fh:
            unsure = json.load(fh)
        for perfil, vacs in unsure.items():
            if perfil not in profiles:
                raise ValueError(f"unsure: perfil desconocido {perfil!r}")
            solapa = set(vacs) & set(juicios.get(perfil, ()))
            if solapa:
                raise ValueError(
                    f"unsure: {perfil}/{sorted(solapa)} también juzgado con "
                    "rel — juicio ambiguo"
                )
    await _verify_judged_vacancies(session, juicios)
    corpus_gen = (
        await session.execute(sa.text(matching.CORPUS_GENERATION_SQL))
    ).scalar_one_or_none()
    if corpus_gen is None:
        raise ValueError("corpus_generation ausente: corpus no identificable")

    por_perfil = {}
    revisiones = {}
    for nombre in sorted(profiles):
        pid = profiles[nombre]
        filas, prid = await matching.shadow_feed(session, pid, policy_id, mid)
        revisiones[nombre] = str(prid)
        vac_rel = juicios[nombre]
        rels_top = [vac_rel.get(str(f.vacancy_id), 0) for f in filas[:NDCG_K]]
        dcg = _dcg(rels_top)
        idcg = _dcg(sorted(vac_rel.values(), reverse=True)[:NDCG_K])
        en_feed = {str(f.vacancy_id) for f in filas}
        por_perfil[nombre] = {
            "feed_n": len(filas),
            "top10": [
                {
                    "rank": i + 1, "vacancy_id": str(f.vacancy_id),
                    "score": str(f.score_final), "rel": rel,
                }
                for i, (f, rel) in enumerate(zip(filas[:NDCG_K], rels_top))
            ],
            "dcg": round(dcg, 6),
            "idcg": round(idcg, 6),
            "ndcg10": round(dcg / idcg, 6) if idcg > 0 else 0.0,
            "no_medible": idcg <= 0,
            "causa_no_medible": (
                "IDCG=0: sin juicios con relevancia positiva" if idcg <= 0 else None
            ),
            "juicios_usados": len(vac_rel),
            "excluidos_unsure": sorted(unsure.get(nombre, [])),
            "rel2_fuera_del_feed": sorted(
                v for v, rel in vac_rel.items() if rel == 2 and v not in en_feed
            ),
        }

    return {
        "manifest": {
            "evaluator": EVALUATOR_VERSION,
            "release": os.environ.get("RELEASE_SHA", "unknown"),
            "policy": policy_spec,
            "policy_id": str(policy_id),
            "recipe": weights,
            "model_id": str(mid),
            "profile_revisions": revisiones,
            "corpus_generation": corpus_gen,
            "judgments_sha256": _sha256_file(judgments_path),
            "unsure_sha256": _sha256_file(unsure_path) if unsure_path else None,
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        },
        "profiles": por_perfil,
    }


async def _main(argv) -> None:
    ap = argparse.ArgumentParser(prog="jobhunt_core.dev_eval")
    ap.add_argument("--policy", required=True, help="name:version")
    ap.add_argument("--judgments", required=True)
    ap.add_argument("--unsure", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument(
        "--profile", action="append", required=True,
        help="nombre=uuid (repetible)",
    )
    args = ap.parse_args(argv)
    profiles = {}
    for spec in args.profile:
        nombre, _, pid = spec.partition("=")
        if not pid:
            raise SystemExit(f"--profile debe ser nombre=uuid, no {spec!r}")
        profiles[nombre] = pid
    async with task_session_factory() as factory:
        async with factory() as s:
            out = await evaluate_dev(
                s, args.policy, profiles, args.judgments,
                unsure_path=args.unsure, model_id=args.model,
            )
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
