"""Reranker cross-encoder (Fase 2 cierre definitivo) — unidad, sin BD.

Entrada versionada y determinista, score absoluto por pareja (máximo sobre
roles, sigmoide fija), invariancia al batch y al resto del lote, fail-closed.
"""

import math

import pytest

from jobhunt_core import cross_encoder as ce


class _StubEngine:
    """Logit determinista por PAR (hash del texto, no del orden/lote)."""

    def __init__(self):
        self.calls = 0
        self.pairs_seen = []

    def predict(self, pares, batch_size=16):
        self.calls += 1
        self.pairs_seen.extend(pares)
        return [((hash(q + "|" + d) % 1000) - 500) / 100.0 for q, d in pares]


@pytest.fixture()
def stub():
    motor = _StubEngine()
    ce.set_engine_factory(lambda model, revision: motor)
    yield motor
    ce.set_engine_factory(None)


PERFIL = {
    "title": "Bilingual Content Specialist",
    "skills": ["Localization", "LQA", "Customer Success"],
    "languages": ["English", "Spanish"],
    "locations": ["Remote", "Spain"],
    "remote_pref": "remote_only",
    "target_roles": [],
}


def test_sin_roles_usa_el_titulo_y_con_roles_una_consulta_por_rol():
    q = ce.build_queries(PERFIL)
    assert len(q) == 1 and q[0].startswith("Bilingual Content Specialist.")
    con_roles = dict(PERFIL, target_roles=["Localization QA", "Content Reviewer"])
    q2 = ce.build_queries(con_roles)
    assert len(q2) == 2
    assert q2[0].startswith("Localization QA.")
    assert q2[1].startswith("Content Reviewer.")
    # sin CV completo: la consulta lleva intención, no texto libre
    assert "cv" not in q2[0].lower()


def test_truncados_deterministas_y_unicode():
    raro = dict(
        PERFIL,
        target_roles=["ñandú Ærøskøbing 中文" * 50],
        skills=["s" * 999] * 50,
    )
    q = ce.build_queries(raro)
    assert len(q) == 1
    assert len(q[0]) < 1000  # cotas aplicadas
    assert q[0] == ce.build_queries(raro)[0]  # determinista
    doc = ce.build_document("t" * 999, "l" * 999, "d" * 99999)
    assert len(doc) == 200 + 100 + 1200 + len(". ") * 2
    assert ce.build_document(None, None, None) == ". . "


def test_score_es_maximo_sobre_roles_y_sigmoide(stub):
    docs = ["doc A", "doc B"]
    con_roles = dict(PERFIL, target_roles=["rol1", "rol2", "rol3"])
    consultas = ce.build_queries(con_roles)
    probs = ce.score_documents("m", "r", consultas, docs)
    assert len(probs) == 2
    for i, d in enumerate(docs):
        logits = [((hash(q + "|" + d) % 1000) - 500) / 100.0 for q in consultas]
        esperado = 1.0 / (1.0 + math.exp(-max(logits)))
        assert probs[i] == pytest.approx(esperado)
        assert 0.0 < probs[i] < 1.0


def test_mismo_par_mismo_score_con_otro_batch_y_otro_lote(stub):
    consultas = ce.build_queries(PERFIL)
    solo = ce.score_documents("m", "r", consultas, ["doc X"], batch_size=1)[0]
    ajenos = [f"doc ajeno {i}" for i in range(7)]
    mezcla = ce.score_documents("m", "r", consultas, ajenos + ["doc X"], batch_size=32)
    assert mezcla[-1] == pytest.approx(solo)  # el lote no cambia la pareja


def test_lote_vacio_y_salidas_invalidas(stub):
    assert ce.score_documents("m", "r", ["q"], []) == []

    class _Corto:
        def predict(self, pares, batch_size=16):
            return [0.1]  # menos scores que pares

    ce.set_engine_factory(lambda m, r: _Corto())
    with pytest.raises(ValueError, match="scores"):
        ce.score_documents("m", "r", ["q1", "q2"], ["d"])

    class _NaN:
        def predict(self, pares, batch_size=16):
            return [float("nan")] * len(pares)

    ce.set_engine_factory(lambda m, r: _NaN())
    with pytest.raises(ValueError, match="finito"):
        ce.score_documents("m", "r", ["q"], ["d"])


def test_el_motor_se_carga_una_vez_por_modelo(stub):
    ce.score_documents("m", "r", ["q"], ["d1"])
    ce.score_documents("m", "r", ["q"], ["d2"])
    assert stub.calls == 2  # dos predict, un solo motor (el mismo stub)


def test_el_contrato_de_2_decimales_no_destruye_el_orden_del_top():
    """Defecto real medido (P2): con consultas anchas los logits del top
    rondan 9-13, la sigmoide plana los aplasta en 0.9999+ y el score
    persistido NUMERIC(6,2) los empata en 99.99 — el feed ordenaría por
    vacancy_id. La activación sigmoid_t4 (σ(logit/4), fija y monótona)
    conserva el orden del modelo tras el redondeo del contrato."""

    class _LogitsAltos:
        def predict(self, pares, batch_size=16):
            # dos documentos con logits 12 y 10: el modelo SÍ los distingue
            return [12.0 if "mejor" in d else 10.0 for _, d in pares]

    ce.set_engine_factory(lambda m, r: _LogitsAltos())
    try:
        a, b = ce.score_documents(
            "m", "r", ["q"], ["doc mejor", "doc bueno"], activation="sigmoid_t4"
        )
        assert round(a * 100, 2) > round(b * 100, 2), (
            "t4 debe separar el top tras redondear a 2 decimales"
        )
        # y la plana los empata: esa es la causa del defecto
        a0, b0 = ce.score_documents("m", "r", ["q"], ["doc mejor", "doc bueno"])
        assert round(a0 * 100, 2) == round(b0 * 100, 2)  # empatados (100.0)
        # monótona: mismo ORDEN que la plana (solo cambia la escala)
        assert (a > b) == (a0 > b0)
    finally:
        ce.set_engine_factory(None)


def test_activacion_desconocida_falla_cerrado():
    with pytest.raises(ValueError, match="activaci"):
        ce.score_documents("m", "r", ["q"], ["d"], activation="softmax")


# --- P1-1 revisión 2026-09-03: identidad EFECTIVA del modelo


def test_sustituir_un_archivo_del_artefacto_falla_antes_de_puntuar(tmp_path):
    """La huella de la receta debe compararse con los archivos REALMENTE
    cargados: sustituir los pesos bajo la misma ruta/receta cambia el modelo
    efectivo sin cambiar policy_id/eval_key — dos regímenes bajo una política.
    La carga falla CERRADO antes de construir el motor."""
    d = tmp_path / "modelo"
    d.mkdir()
    (d / "config.json").write_text('{"architectures": ["Fake"]}')
    (d / "model.safetensors").write_bytes(b"PESOS-A")
    (d / "tokenizer.json").write_text("{}")
    huella = ce.model_fingerprint(str(d))
    # con la huella correcta, la verificación pasa
    ce.verify_model_identity(str(d), None, huella)
    # sustitución de pesos bajo la MISMA ruta ⇒ fallo cerrado
    (d / "model.safetensors").write_bytes(b"PESOS-B")
    with pytest.raises(ValueError, match="huella|fingerprint"):
        ce.verify_model_identity(str(d), None, huella)
    # y la CARGA REAL con receta (fingerprint) muerde ANTES de construir el
    # motor (jamás llega a abrir el modelo falso)
    with pytest.raises(ValueError, match="huella|fingerprint"):
        ce.score_documents(str(d), None, ["q"], ["doc"], fingerprint=huella)
    # un archivo de runtime AÑADIDO también rompe la identidad
    (d / "model.safetensors").write_bytes(b"PESOS-A")
    (d / "vocab.txt").write_text("extra")
    with pytest.raises(ValueError, match="huella|fingerprint"):
        ce.verify_model_identity(str(d), None, huella)


def test_la_huella_es_manifiesto_canonico_documentado(tmp_path):
    """Formato exacto: sha256 agregada de líneas «sha256  nombre\n» ordenadas
    de los archivos de runtime (allowlist), generable por comando."""
    import hashlib

    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text("c")
    (d / "model.safetensors").write_bytes(b"w")
    (d / "README.md").write_text("no-runtime: fuera del manifiesto")
    man = ce.model_manifest(str(d))
    assert sorted(man) == ["config.json", "model.safetensors"]
    canon = "".join(f"{h}  {n}\n" for n, h in sorted(man.items()))
    assert ce.model_fingerprint(str(d)) == hashlib.sha256(canon.encode()).hexdigest()


def test_receta_ranknet_backend_falla_cerrado_bajo_binario_torch(monkeypatch):
    """P7-b: la receta declara backend onnx-cpu; un runtime torch NO puede
    evaluarla en silencio con otro motor — la validación falla cerrado. Bajo
    el binario correcto, valida."""
    import pytest

    from jobhunt_core import cross_encoder as ce
    from jobhunt_core import matching

    monkeypatch.setattr(ce, "BACKEND", "torch-cpu")
    with pytest.raises(ValueError, match="backend"):
        matching._validated_cross_encoder_recipe(matching.XENC_RANKNET_POLICY_WEIGHTS)
    monkeypatch.setattr(ce, "BACKEND", "onnx-cpu")
    receta = matching._validated_cross_encoder_recipe(
        matching.XENC_RANKNET_POLICY_WEIGHTS
    )
    assert receta["backend"] == "onnx-cpu"
    assert receta["model"].startswith("/models/")
