"""Pipeline reproducible de entrenamiento (P1-4) — unidad + e2e con stub."""

import json

import pytest

from jobhunt_core import train_cross_encoder as tce

PERFILES = {"P1": {"title": "Dev", "skills": ["python"],
                   "languages": ["English"], "locations": ["Remote"],
                   "remote_pref": "remote_only", "target_roles": []}}


def _docs(tmp_path, vacs):
    p = tmp_path / "docs.jsonl"
    p.write_text("".join(
        json.dumps({"vac": v, "t": f"t{v}", "l": "Remote", "d": "desc"}) + "\n"
        for v in vacs))
    return str(p)


def _judgments(tmp_path, filas):
    p = tmp_path / "j.csv"
    p.write_text("".join(f"{per},{vac},{rel}\n" for per, vac, rel in filas))
    return str(p)


def test_dataset_canonico_y_split_deterministas(tmp_path):
    vacs = [f"0000000{i}-0000-0000-0000-00000000000{i}" for i in range(8)]
    j = _judgments(tmp_path, [("P1", v, i % 3) for i, v in enumerate(vacs)])
    d = _docs(tmp_path, vacs)
    a = tce.build_dataset(j, PERFILES, d)
    b = tce.build_dataset(j, PERFILES, d)
    assert a == b  # canónico: mismo orden y contenido
    assert all(f["y"] in (0.0, 0.5, 1.0) for f in a)
    ta, va = tce.split_by_group(a)
    tb, vb = tce.split_by_group(b)
    assert ta == tb and va == vb
    # una vacante jamás en ambos lados
    assert not ({f["vac"] for f in ta} & {f["vac"] for f in va})


def test_dataset_falla_cerrado(tmp_path):
    v = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(ValueError, match="rel"):
        tce.build_dataset(_judgments(tmp_path, [("P1", v, 7)]),
                          PERFILES, _docs(tmp_path, [v]))
    with pytest.raises(ValueError, match="documento"):
        tce.build_dataset(_judgments(tmp_path, [("P1", v, 1)]),
                          PERFILES, _docs(tmp_path, []))
    with pytest.raises(ValueError, match="perfil"):
        tce.build_dataset(_judgments(tmp_path, [("PX", v, 1)]),
                          PERFILES, _docs(tmp_path, [v]))


def test_e2e_con_stub_produce_artefacto_sellado(tmp_path):
    """Extremo a extremo con entrenador inyectado: artefacto en ruta
    content-addressed, manifiesto con hashes y selección por MSE de grupos."""
    vacs = [f"0000000{i}-0000-0000-0000-00000000000{i}" for i in range(8)]
    j = _judgments(tmp_path, [("P1", v, i % 3) for i, v in enumerate(vacs)])
    d = _docs(tmp_path, vacs)
    perfiles_path = tmp_path / "perfiles.json"
    perfiles_path.write_text(json.dumps(PERFILES))

    class _Stub:
        def __init__(self, base, revision):
            self.epochs = 0

        def fit(self, train, epochs):
            self.epochs = epochs

        def predict(self, pares):
            # más épocas ⇒ mejor MSE (elige 3), determinista
            return [self.epochs * 0.1] * len(pares)

        def save_pretrained(self, ruta):
            import os
            os.makedirs(ruta, exist_ok=True)
            (tmp_path / "nada").write_text("")  # noqa
            open(f"{ruta}/config.json", "w").write('{"stub": true}')
            open(f"{ruta}/model.safetensors", "wb").write(
                b"stub-" + str(self.epochs).encode())

    tce.set_trainer_factory(lambda b, r: _Stub(b, r))
    try:
        man = tce.run_training(j, str(perfiles_path), d,
                               "base/modelo", "0" * 40, str(tmp_path / "out"))
    finally:
        tce.set_trainer_factory(None)
    assert man["epocas_elegidas"] in (1, 2, 3)
    assert set(man["val_mse"]) == {1, 2, 3}
    assert man["dataset_sha256"] and man["judgments_sha256"]
    # content-addressed: el directorio es la huella y P1-1 puede verificarlo
    from jobhunt_core import cross_encoder as ce
    assert man["artifact_dir"].endswith(man["model_fingerprint"])
    ce.verify_model_identity(man["artifact_dir"], None,
                             man["model_fingerprint"])
    assert json.load(open(f"{man['artifact_dir']}/TRAIN_MANIFEST.json"))[
        "model_fingerprint"] == man["model_fingerprint"]
