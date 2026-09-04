"""Evaluador CANÓNICO de desarrollo para políticas sombra.

Corrección P1 (revisión externa 2026-09-03): la primera versión reconstruía el
feed desde el almacén append-only, que tras una cosecha es la UNIÓN de
generaciones del corpus (eval_key no lleva generación; RRF persiste rangos
relativos; ON CONFLICT conserva scores viejos — feed 1800→1814 observado): un
ranking que ninguna ejecución produjo. Esta versión mide DIRECTAMENTE
matching.compute_policy_feed — el mismo cálculo que persiste la evaluación
productiva — dentro de una fotografía transaccional REPEATABLE READ de solo
lectura; el corpus_generation del manifiesto pertenece a esa misma fotografía.

    python -m jobhunt_core.dev_eval --policy hybrid-rrf:v4 \
        --judgments /tmp/juicios.csv [--unsure /tmp/unsure.json] \
        [--model <uuid>] --profile P1=<uuid> --profile P2=<uuid>

- Fórmula del gate importada de shadow.metrics (_dcg, NDCG_K) — jamás copiada.
- Semántica del feed canónico (exclude_dismissed): una vacante descartada no
  cuenta; una sin fila de estado sí.
- Falla CERRADO: política/perfil/modelo irresolubles, juicios duplicados o
  malformados, vacante juzgada inexistente, varios modelos activos sin
  --model, perfil sin vector, RELEASE_SHA no identificable (salvo
  allow_unknown_release explícito, solo para tests). IDCG<=0 ⇒ `no_medible`.
- `unsure` es exactamente dict[perfil, lista de UUID]; extras/escalares/UUID
  inválidos abortan.
- Salida = {"payload": <reproducible>, "payload_sha256", "generated_at"}: el
  payload es determinista y su hash lo sella; los metadatos volátiles viven
  fuera.

Formato de juicios: CSV `perfil,vacancy_uuid,rel` con rel ∈ {0,1,2}.
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

EVALUATOR_VERSION = "dev-eval-v3"


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


def load_unsure(path: str | None, profiles: dict, juicios: dict) -> dict:
    """`unsure` = EXACTAMENTE dict[perfil, list[UUID]]; extras, escalares o
    solapes con juicios abortan."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        crudo = json.load(fh)
    if not isinstance(crudo, dict):
        raise ValueError(f"unsure: debe ser un objeto, no {type(crudo).__name__}")
    out: dict[str, list] = {}
    for perfil, vacs in crudo.items():
        if perfil not in profiles:
            raise ValueError(f"unsure: perfil desconocido {perfil!r}")
        if not isinstance(vacs, list):
            raise ValueError(
                f"unsure[{perfil}]: debe ser una lista, no "
                f"{type(vacs).__name__}"
            )
        for v in vacs:
            if not isinstance(v, str):
                raise ValueError(f"unsure[{perfil}]: elemento no textual {v!r}")
            uuid_mod.UUID(v)  # valida o revienta
        solapa = set(vacs) & set(juicios.get(perfil, ()))
        if solapa:
            raise ValueError(
                f"unsure: {perfil}/{sorted(solapa)} también juzgado con rel "
                "— juicio ambiguo"
            )
        out[perfil] = sorted(vacs)
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


async def build_universe_manifest(
    session, profiles: dict, model_id: str | None = None,
    allow_unknown_release: bool = False,
) -> dict:
    """Manifiesto INMUTABLE del universo de un examen (Fase 4): cada pareja
    elegible (vacancy_id, offer_revision_id, text_hash) del corpus en esta
    fotografía, más modelo, revisiones de perfil, corpus_generation y release.
    El sha256 del manifiesto lo sella; un examen posterior queda LIGADO a él
    o se declara inelegible — jamás varía en silencio."""
    release = os.environ.get("RELEASE_SHA", "unknown")
    if release == "unknown" and not allow_unknown_release:
        raise ValueError("RELEASE_SHA=unknown: universo no auditable")
    await session.execute(
        sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    )
    mid = await _resolve_model(session, model_id)
    corpus_gen = (
        await session.execute(sa.text(matching.CORPUS_GENERATION_SQL))
    ).scalar_one_or_none()
    if corpus_gen is None:
        raise ValueError("corpus_generation ausente")
    pares = [
        {"vacancy_id": str(r.vid), "offer_revision_id": str(r.orid),
         "text_hash": r.th}
        for r in (
            await session.execute(
                sa.text(
                    "SELECT v.id AS vid, orv.id AS orid, orv.text_hash AS th "
                    + matching.ELIGIBLE_CORPUS_FROM.format(model=":mid")
                    + " ORDER BY v.id"
                ),
                {"mid": mid},
            )
        ).all()
    ]
    revisiones = {}
    for nombre in sorted(profiles):
        rid = (
            await session.execute(
                sa.text(
                    "SELECT revision_id FROM profile_revision_activations "
                    "WHERE profile_id = :p ORDER BY seq DESC LIMIT 1"
                ),
                {"p": profiles[nombre]},
            )
        ).scalar_one_or_none()
        if rid is None:
            raise ValueError(f"perfil {nombre} sin revisión vigente")
        revisiones[nombre] = str(rid)
    cuerpo = {
        "release": release, "model_id": str(mid),
        "corpus_generation": corpus_gen,
        "profile_revisions": revisiones, "pairs": pares,
    }
    canon = json.dumps(cuerpo, ensure_ascii=False, sort_keys=True)
    return {"universe": cuerpo,
            "universe_sha256": hashlib.sha256(canon.encode()).hexdigest()}


def _universe_body_sha(body: dict) -> str:
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()


async def _validate_universe_seal(
    session, wrapper: dict, profiles: dict, model_id, release: str,
) -> dict:
    """Validación ÚNICA del sello (P1-2, usada por evaluación y pool):
    esquema, SHA, release, modelo, TODAS las revisiones de perfil y el
    conjunto EXACTO de parejas elegibles (vacancy, offer_revision, text_hash).
    Cualquier deriva invalida el examen COMPLETO — jamás nDCG parcial."""
    if not isinstance(wrapper, dict) or set(wrapper) < {
            "universe", "universe_sha256"}:
        raise ValueError(
            "sello de universo malformado: se espera "
            "{universe, universe_sha256}")
    body = wrapper["universe"]
    esperadas = {"release", "model_id", "corpus_generation",
                 "profile_revisions", "pairs"}
    if not isinstance(body, dict) or set(body) != esperadas:
        raise ValueError(
            f"universo malformado: claves {sorted(body) if isinstance(body, dict) else type(body).__name__}")
    if _universe_body_sha(body) != wrapper["universe_sha256"]:
        raise ValueError(
            "SHA del universo no coincide con su cuerpo — sello adulterado")
    if body["release"] != release:
        raise ValueError(
            f"universo sellado en release {body['release']!r}, "
            f"evaluando en {release!r}")
    if str(body["model_id"]) != str(model_id):
        raise ValueError(
            f"universo sellado para modelo {body['model_id']}, "
            f"evaluando con {model_id}")
    for nombre in sorted(profiles):
        actual = (
            await session.execute(
                sa.text(
                    "SELECT revision_id FROM profile_revision_activations "
                    "WHERE profile_id = :p ORDER BY seq DESC LIMIT 1"
                ),
                {"p": profiles[nombre]},
            )
        ).scalar_one_or_none()
        sellada = body["profile_revisions"].get(nombre)
        if sellada is None or str(actual) != sellada:
            raise ValueError(
                f"la revisión del perfil {nombre} derivó: sellada "
                f"{sellada}, vigente {actual}"
            )
    actuales = {
        (str(r.vid), str(r.orid), r.th)
        for r in (
            await session.execute(
                sa.text(
                    "SELECT v.id AS vid, orv.id AS orid, orv.text_hash AS th "
                    + matching.ELIGIBLE_CORPUS_FROM.format(model=":mid")
                ),
                {"mid": model_id},
            )
        ).all()
    }
    selladas = {
        (p["vacancy_id"], p["offer_revision_id"], p["text_hash"])
        for p in body["pairs"]
    }
    if actuales != selladas:
        retiradas = len(selladas - actuales)
        nuevas = len(actuales - selladas)
        raise ValueError(
            f"el universo derivó del sello: {retiradas} pareja(s) "
            f"retirada(s), {nuevas} nueva(s) — examen INELEGIBLE completo"
        )
    # Revisión 2026-09-04 P1 — ÚLTIMA comprobación (los errores específicos
    # de identidad son más diagnósticos): los vectores pueden cambiar
    # (re-embed) sin alterar ninguna identidad, el conjunto de parejas queda
    # idéntico y el examen YA NO sería la fotografía sellada. La generación
    # es parte del sello y se contrasta como el resto.
    gen_actual = (
        await session.execute(sa.text(matching.CORPUS_GENERATION_SQL))
    ).scalar_one()
    if int(body["corpus_generation"]) != int(gen_actual):
        raise ValueError(
            f"la generación del corpus derivó: sellada "
            f"{body['corpus_generation']}, vigente {gen_actual}")
    return body


async def build_blind_pool(
    session, profiles: dict, baseline_spec: str, candidate_spec: str,
    judgments_path: str | None = None, k: int = 20,
    model_id: str | None = None, universe: dict | None = None,
    allow_unknown_release: bool = False,
) -> dict:
    """Pool CIEGO del examen (Fase 4): unión de los top-K de la baseline y de
    la candidata por perfil, deduplicada de forma determinista, con los
    juicios previos aplicables separados de lo pendiente de etiquetar."""
    release = os.environ.get("RELEASE_SHA", "unknown")
    if release == "unknown" and not allow_unknown_release:
        raise ValueError("RELEASE_SHA=unknown: pool no auditable")
    await session.execute(
        sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    )
    mid = await _resolve_model(session, model_id)
    if universe is not None:
        # El pool queda LIGADO al mismo sello que el examen (P1-2).
        await _validate_universe_seal(session, universe, profiles, mid, release)
    base_id, _ = await _resolve_policy(session, baseline_spec)
    cand_id, _ = await _resolve_policy(session, candidate_spec)
    juicios = (
        load_judgments(judgments_path, profiles) if judgments_path
        else {n: {} for n in profiles}
    )
    pool = {}
    for nombre in sorted(profiles):
        pid = profiles[nombre]
        union: dict[str, dict] = {}
        for spid in (base_id, cand_id):
            computed = await matching.compute_policy_feed(
                session, pid, mid, spid,
                limit=matching.CANONICAL_EVAL_LIMIT, exclude_dismissed=True,
            )
            if computed["status"] != "ok":
                raise ValueError(f"pool: perfil {nombre} no computable")
            for f in computed["rows"][:k]:
                union.setdefault(str(f["vacancy_id"]), {
                    "vacancy_id": str(f["vacancy_id"]),
                    "offer_revision_id": str(f["offer_revision_id"]),
                })
        ya = juicios.get(nombre, {})
        pool[nombre] = {
            "pendientes": sorted(
                (v for v in union.values() if v["vacancy_id"] not in ya),
                key=lambda x: x["vacancy_id"],
            ),
            "juzgados_aplicables": sorted(
                v for v in union if v in ya
            ),
        }
    return pool


async def evaluate_dev(
    session, policy_spec: str, profiles: dict, judgments_path: str,
    unsure_path: str | None = None, model_id: str | None = None,
    limit: int = matching.CANONICAL_EVAL_LIMIT,
    allow_unknown_release: bool = False,
    allow_uncovered: bool = False,
    universe: dict | None = None,
) -> dict:
    """Métricas de desarrollo de UNA política con la fórmula del gate, sobre
    el feed COHERENTE de una ejecución actual (compute_policy_feed) bajo una
    fotografía REPEATABLE READ de solo lectura.

    `profiles` = {nombre: profile_uuid}. La sesión debe llegar SIN transacción
    empezada (el SET TRANSACTION debe ser la primera sentencia).
    """
    release = os.environ.get("RELEASE_SHA", "unknown")
    if release == "unknown" and not allow_unknown_release:
        raise ValueError(
            "RELEASE_SHA=unknown: una medición sin release identificable no "
            "es auditable (allow_unknown_release solo para tests)"
        )
    # Fotografía transaccional: todo lo que sigue —candidatos, generación,
    # juicios verificados— se lee del MISMO snapshot.
    await session.execute(
        sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    )
    policy_id, weights = await _resolve_policy(session, policy_spec)
    mid = await _resolve_model(session, model_id)
    juicios = load_judgments(judgments_path, profiles)
    unsure = load_unsure(unsure_path, profiles, juicios)
    await _verify_judged_vacancies(session, juicios)

    pares_universo = None
    if universe is not None:
        cuerpo = await _validate_universe_seal(
            session, universe, profiles, mid, release)
        pares_universo = {
            (p["vacancy_id"], p["offer_revision_id"])
            for p in cuerpo["pairs"]
        }
    por_perfil = {}
    revisiones = {}
    corpus_gen = None
    for nombre in sorted(profiles):
        pid = profiles[nombre]
        computed = await matching.compute_policy_feed(
            session, pid, mid, policy_id, limit=limit,
            exclude_dismissed=True, with_corpus_generation=True,
        )
        if computed["status"] != "ok":
            raise ValueError(
                f"perfil {nombre} no medible: status={computed['status']!r}"
            )
        if corpus_gen is None:
            corpus_gen = computed["corpus_generation"]
        elif corpus_gen != computed["corpus_generation"]:
            raise ValueError(
                "corpus_generation cambió dentro de la fotografía: "
                f"{corpus_gen} → {computed['corpus_generation']}"
            )
        revisiones[nombre] = str(computed["profile_revision_id"])
        filas = computed["rows"]
        vac_rel = juicios[nombre]
        rels_top = [
            vac_rel.get(str(f["vacancy_id"]), 0) for f in filas[:NDCG_K]
        ]
        dcg = _dcg(rels_top)
        idcg = _dcg(sorted(vac_rel.values(), reverse=True)[:NDCG_K])
        en_feed = {str(f["vacancy_id"]) for f in filas}
        # Fase 4 — contrato estable: (a) el examen queda LIGADO a su universo
        # (una pareja del top fuera del manifiesto ⇒ INELEGIBLE, jamás variar
        # en silencio); (b) cobertura 100% del top-10 (un no-juzgado NO es
        # relevancia 0 ⇒ INELEGIBLE con la lista de lo que falta).
        fuera_de_universo = []
        if pares_universo is not None:
            fuera_de_universo = sorted(
                str(f["vacancy_id"]) for f in filas[:NDCG_K]
                if (str(f["vacancy_id"]), str(f["offer_revision_id"]))
                not in pares_universo
            )
        sin_juzgar = sorted(
            str(f["vacancy_id"]) for f in filas[:NDCG_K]
            if str(f["vacancy_id"]) not in vac_rel
            and str(f["vacancy_id"]) not in set(unsure.get(nombre, ()))
        )
        inelegible = bool(fuera_de_universo) or (
            bool(sin_juzgar) and not allow_uncovered
        )
        por_perfil[nombre] = {
            "elegible": not inelegible,
            "fuera_de_universo": fuera_de_universo,
            "sin_juzgar_en_top10": sin_juzgar,
            "feed_n": len(filas),
            "top10": [
                {
                    "rank": i + 1, "vacancy_id": str(f["vacancy_id"]),
                    "score": str(f["score"]), "rel": rel,
                }
                for i, (f, rel) in enumerate(zip(filas[:NDCG_K], rels_top))
            ],
            "dcg": round(dcg, 6),
            "idcg": round(idcg, 6),
            "ndcg10": (
                None if inelegible
                else (round(dcg / idcg, 6) if idcg > 0 else 0.0)
            ),
            "no_medible": idcg <= 0,
            "causa_no_medible": (
                "IDCG=0: sin juicios con relevancia positiva" if idcg <= 0 else None
            ),
            "juicios_usados": len(vac_rel),
            "excluidos_unsure": unsure.get(nombre, []),
            "rel2_fuera_del_feed": sorted(
                v for v, rel in vac_rel.items() if rel == 2 and v not in en_feed
            ),
        }
    if corpus_gen is None:
        raise ValueError("corpus_generation ausente: corpus no identificable")

    payload = {
        "evaluator": EVALUATOR_VERSION,
        "release": release,
        "policy": policy_spec,
        "policy_id": str(policy_id),
        "recipe": weights,
        "model_id": str(mid),
        "limit": limit,
        "profile_revisions": revisiones,
        "corpus_generation": corpus_gen,
        "judgments_sha256": _sha256_file(judgments_path),
        "unsure_sha256": _sha256_file(unsure_path) if unsure_path else None,
        "profiles": por_perfil,
    }
    canónico = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {
        "payload": payload,
        "payload_sha256": hashlib.sha256(canónico.encode()).hexdigest(),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def _parse_profiles(specs) -> dict:
    profiles = {}
    for spec in specs:
        nombre, _, pid = spec.partition("=")
        if not pid:
            raise SystemExit(f"--profile debe ser nombre=uuid, no {spec!r}")
        profiles[nombre] = pid
    return profiles


def _write_atomic(path: str, data: dict) -> str:
    """Escritura atómica (tmp + rename) con el sha256 del contenido visible."""
    cuerpo = json.dumps(data, ensure_ascii=False, sort_keys=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(cuerpo)
    os.replace(tmp, path)
    return hashlib.sha256(cuerpo.encode()).hexdigest()


def _load_universe(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


async def _main(argv) -> None:
    """CLI OPERATIVO del examen (P1-2): estricto — sin allow_unknown_release
    ni allow_uncovered. seal-universe / build-pool / evaluate comparten el
    mismo sello."""
    ap = argparse.ArgumentParser(prog="jobhunt_core.dev_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_seal = sub.add_parser("seal-universe")
    p_seal.add_argument("--profile", action="append", required=True)
    p_seal.add_argument("--model", default=None)
    p_seal.add_argument("--out", required=True)

    p_pool = sub.add_parser("build-pool")
    p_pool.add_argument("--profile", action="append", required=True)
    p_pool.add_argument("--baseline", required=True)
    p_pool.add_argument("--candidate", required=True)
    p_pool.add_argument("--universe", required=True)
    p_pool.add_argument("--judgments", default=None)
    p_pool.add_argument("--k", type=int, default=20)
    p_pool.add_argument("--model", default=None)
    p_pool.add_argument("--out", required=True)

    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--policy", required=True, help="name:version")
    p_eval.add_argument("--judgments", required=True)
    p_eval.add_argument("--unsure", default=None)
    p_eval.add_argument("--model", default=None)
    p_eval.add_argument("--universe", default=None)
    p_eval.add_argument("--profile", action="append", required=True)

    args = ap.parse_args(argv)
    profiles = _parse_profiles(args.profile)
    async with task_session_factory() as factory:
        async with factory() as s:
            if args.cmd == "seal-universe":
                sello = await build_universe_manifest(
                    s, profiles, model_id=args.model)
                sha = _write_atomic(args.out, sello)
                print(json.dumps({
                    "out": args.out, "file_sha256": sha,
                    "universe_sha256": sello["universe_sha256"],
                    "pairs": len(sello["universe"]["pairs"]),
                }, sort_keys=True))
            elif args.cmd == "build-pool":
                pool = await build_blind_pool(
                    s, profiles, args.baseline, args.candidate,
                    judgments_path=args.judgments, k=args.k,
                    model_id=args.model,
                    universe=_load_universe(args.universe),
                )
                sha = _write_atomic(args.out, pool)
                print(json.dumps({"out": args.out, "file_sha256": sha},
                                 sort_keys=True))
            else:
                out = await evaluate_dev(
                    s, args.policy, profiles, args.judgments,
                    unsure_path=args.unsure, model_id=args.model,
                    universe=(_load_universe(args.universe)
                              if args.universe else None),
                )
                print(json.dumps(out, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
