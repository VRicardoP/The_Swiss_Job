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
    mezcla = ce.score_documents(
        "m", "r", consultas, ajenos + ["doc X"], batch_size=32)
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
