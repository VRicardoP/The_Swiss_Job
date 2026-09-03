"""Pipeline REPRODUCIBLE de fine-tuning del cross-encoder (P1-4, revisión
2026-09-03).

Un checkout limpio reconstruye la misma ENTRADA (dataset canónico + split por
grupos, ambos sellados por sha256) y el manifiesto explica cualquier
diferencia de salida (versiones de librerías registradas). Los pesos NO van a
Git: el artefacto se escribe en un directorio content-addressed
(<out>/<fingerprint>/) y P1-1 lo verifica en runtime.

    python -m jobhunt_core.train_cross_encoder \
        --judgments juicios.csv --profiles-content perfiles.json \
        --docs docs.jsonl --base cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 \
        --revision 1427fd65… --out /ruta/artefactos

Hiperparámetros FIJOS (predeclaración ee7e754): seed 20260903 (Python, NumPy,
Torch), batch 8, lr 2e-5, AdamW por defecto del CrossEncoder, warmup 10,
max_length 512, BCE-with-logits (num_labels=1), objetivo = rel/2 ∈ {0,.5,1},
épocas candidatas {1,2,3} elegidas por MSE en la validación POR GRUPOS
(sha1(vacancy) mod 5 == 0). Cambiar cualquiera es OTRA receta.
"""
import argparse
import asyncio  # noqa: F401 — homogéneo con el resto de CLIs
import hashlib
import io
import json
import os
import random
import sys

SEED = 20260903
BATCH = 8
LR = 2e-5
WARMUP = 10
MAX_LEN = 512
EPOCAS_CANDIDATAS = (1, 2, 3)
VAL_MOD = 5

_trainer_factory = None


def set_trainer_factory(factory) -> None:
    """Inyección para tests: factory(base, revision) → objeto con
    fit(train, epochs) -> None, predict(pairs) -> logits y
    save_pretrained(dir)."""
    global _trainer_factory
    _trainer_factory = factory


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_dataset(judgments_path: str, profiles_content: dict,
                  docs_path: str) -> list[dict]:
    """Dataset CANÓNICO y determinista: una fila por juicio, con la consulta
    v1 del perfil y el documento v1 de la oferta; grupo = sha1(vacante)."""
    from jobhunt_core import cross_encoder as ce

    docs = {}
    for linea in io.open(docs_path, encoding="utf-8"):
        d = json.loads(linea)
        if "vac" in d:
            docs[d["vac"]] = d
    filas = []
    for n, linea in enumerate(io.open(judgments_path, encoding="utf-8"), 1):
        linea = linea.strip()
        if not linea:
            continue
        per, vac, rel = linea.split(",")
        if rel not in {"0", "1", "2"}:
            raise ValueError(f"juicios línea {n}: rel inválida {rel!r}")
        if per not in profiles_content:
            raise ValueError(f"juicios línea {n}: perfil desconocido {per!r}")
        m = docs.get(vac)
        if m is None:
            raise ValueError(f"juicios línea {n}: vacante sin documento {vac}")
        grupo = int(hashlib.sha1(vac.encode()).hexdigest(), 16) % VAL_MOD
        filas.append({
            "q": ce.build_queries(profiles_content[per])[0],
            "d": ce.build_document(m["t"], m["l"], m["d"]),
            "y": int(rel) / 2.0,
            "vac": vac, "grupo": grupo,
        })
    filas.sort(key=lambda f: (f["vac"], f["q"]))  # orden canónico
    return filas


def split_by_group(filas: list[dict]) -> tuple[list, list]:
    """Validación = grupo 0 (~20 %); una vacante JAMÁS en ambos lados."""
    train = [f for f in filas if f["grupo"] != 0]
    val = [f for f in filas if f["grupo"] == 0]
    if not train or not val:
        raise ValueError(
            f"split degenerado: train={len(train)} val={len(val)}")
    return train, val


def _seed_all():
    import numpy as np
    import torch

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)


def _real_trainer(base: str, revision: str):
    from sentence_transformers import CrossEncoder, InputExample
    from torch.utils.data import DataLoader
    import torch

    class _T:
        def __init__(self):
            self.m = CrossEncoder(base, revision=revision, device="cpu",
                                  max_length=MAX_LEN, num_labels=1)

        def fit(self, train, epochs):
            ejemplos = [InputExample(texts=[f["q"], f["d"]], label=f["y"])
                        for f in train]
            g = torch.Generator()
            g.manual_seed(SEED)
            dl = DataLoader(ejemplos, shuffle=True, batch_size=BATCH,
                            generator=g)
            self.m.fit(train_dataloader=dl, epochs=epochs,
                       optimizer_params={"lr": LR}, warmup_steps=WARMUP,
                       show_progress_bar=False)

        def predict(self, pares):
            return list(self.m.predict(pares, batch_size=16))

        def save_pretrained(self, ruta):
            self.m.save_pretrained(ruta)

    return _T()


def _val_mse(modelo, val) -> float:
    import math

    logits = modelo.predict([(f["q"], f["d"]) for f in val])
    total = 0.0
    for f, logit in zip(val, logits):
        p = 1.0 / (1.0 + math.exp(-float(logit)))
        total += (p - f["y"]) ** 2
    return total / len(val)


def run_training(judgments_path: str, profiles_content_path: str,
                 docs_path: str, base: str, revision: str, out_dir: str,
                 epocas=EPOCAS_CANDIDATAS) -> dict:
    from jobhunt_core import cross_encoder as ce

    with io.open(profiles_content_path, encoding="utf-8") as fh:
        perfiles = json.load(fh)
    filas = build_dataset(judgments_path, perfiles, docs_path)
    dataset_canon = json.dumps(filas, ensure_ascii=False, sort_keys=True)
    train, val = split_by_group(filas)

    resultados = {}
    mejor = None
    tmp = os.path.join(out_dir, "_candidato")
    for n_epocas in epocas:
        _seed_all()
        modelo = (_trainer_factory or _real_trainer)(base, revision)
        modelo.fit(train, n_epocas)
        mse = _val_mse(modelo, val)
        resultados[n_epocas] = round(mse, 6)
        if mejor is None or mse < resultados[mejor]:
            mejor = n_epocas
            os.makedirs(tmp, exist_ok=True)
            modelo.save_pretrained(tmp)

    huella = ce.model_fingerprint(os.path.abspath(tmp))
    destino = os.path.join(out_dir, huella)
    if os.path.exists(destino):
        raise ValueError(f"artefacto ya existe (content-addressed): {destino}")
    os.replace(tmp, destino)

    manifiesto = {
        "base": base, "base_revision": revision,
        "seed": SEED, "batch": BATCH, "lr": LR, "warmup": WARMUP,
        "max_length": MAX_LEN, "loss": "bce-with-logits",
        "objetivo": "rel/2", "val_grupo": f"sha1(vac) % {VAL_MOD} == 0",
        "epocas_candidatas": list(epocas), "epocas_elegidas": mejor,
        "val_mse": resultados,
        "n_total": len(filas), "n_train": len(train), "n_val": len(val),
        "dataset_sha256": _sha256_bytes(dataset_canon.encode()),
        "judgments_sha256": _sha256_bytes(
            io.open(judgments_path, "rb").read()),
        "profiles_sha256": _sha256_bytes(
            io.open(profiles_content_path, "rb").read()),
        "model_fingerprint": huella,
        "artifact_dir": destino,
        "artifact_manifest": ce.model_manifest(os.path.abspath(destino)),
        "versions": _library_versions(),
    }
    ruta_man = os.path.join(destino, "TRAIN_MANIFEST.json")
    cuerpo = json.dumps(manifiesto, ensure_ascii=False, sort_keys=True)
    with open(ruta_man + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(cuerpo)
    os.replace(ruta_man + ".tmp", ruta_man)
    return manifiesto


def _library_versions() -> dict:
    out = {"python": sys.version.split()[0]}
    for lib in ("torch", "sentence_transformers", "transformers", "numpy"):
        try:
            out[lib] = __import__(lib).__version__
        except Exception:  # pragma: no cover — entorno de stub
            out[lib] = None
    return out


def _main(argv) -> None:
    ap = argparse.ArgumentParser(prog="jobhunt_core.train_cross_encoder")
    ap.add_argument("--judgments", required=True)
    ap.add_argument("--profiles-content", required=True,
                    help="JSON {perfil: content}")
    ap.add_argument("--docs", required=True, help="JSONL {vac,t,l,d}")
    ap.add_argument("--base", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    man = run_training(args.judgments, args.profiles_content, args.docs,
                       args.base, args.revision, args.out)
    print(json.dumps(man, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    _main(sys.argv[1:])
