import math

import pytest

from jobhunt_core import embedding_recipes


class FakeBackend:
    def __init__(self):
        self.calls = []

    def encode_batch(self, texts):
        self.calls.append(list(texts))
        return [
            [1.0, 0.0] if "fields" in text else [0.0, 1.0]
            for text in texts
        ]


def test_role_recipe_prioritizes_fields_and_keeps_title_view():
    offer = {
        "title": "title",
        "company": "company",
        "description": "description",
        "tags": ["fields"],
    }
    profile = {
        "title": "title",
        "cv_text": "cv",
        "skills": ["fields"],
    }

    assert embedding_recipes.offer_views(
        offer, embedding_recipes.ROLE_COMPOSITE_V2
    ) == (("title fields company description", 0.60), ("title", 0.40))
    assert embedding_recipes.profile_views(
        profile, embedding_recipes.ROLE_COMPOSITE_V2
    ) == (("title fields cv", 0.60), ("title", 0.40))
    assert embedding_recipes.profile_views(
        {"cv_text": "fields cv"}, embedding_recipes.ROLE_COMPOSITE_V2
    ) == (("fields cv", 0.60), ("fields cv", 0.40))


def test_composite_recipe_encodes_each_view_and_normalizes_once():
    backend = FakeBackend()
    rows = [
        (("fields-a", 0.60), ("title-a", 0.40)),
        (("fields-b", 0.60), ("title-b", 0.40)),
    ]

    vectors = embedding_recipes.encode_views(backend, rows)

    assert backend.calls == [["fields-a", "fields-b"], ["title-a", "title-b"]]
    expected = [
        0.60 / math.hypot(0.60, 0.40),
        0.40 / math.hypot(0.60, 0.40),
    ]
    for vector in vectors:
        assert vector == pytest.approx(expected)


def test_legacy_recipe_preserves_single_backend_call():
    backend = FakeBackend()

    vectors = embedding_recipes.encode_views(
        backend, [(("legacy fields", 1.0),)]
    )

    assert backend.calls == [["legacy fields"]]
    assert vectors == [[1.0, 0.0]]


class ShortBackend:
    """Devuelve MENOS vectores que filas (p.ej. [] — 'pierde' filas)."""

    def encode_batch(self, texts):
        return []


class NonFiniteBackend:
    def encode_batch(self, texts):
        return [[float("nan"), 1.0] for _ in texts]


def test_encode_views_rejects_short_backend_single_view():
    """REGRESIÓN P2 rev. externa integral: en el camino de UNA vista, un backend que devuelve menos
    vectores que filas (p.ej. []) hacía que encode_views devolviera [] SIN error → el drenador
    tomaría la cola por VACÍA con filas pendientes. Ahora falla por cardinalidad."""
    with pytest.raises(ValueError, match="vectores para"):
        embedding_recipes.encode_views(
            ShortBackend(), [(("legacy", 1.0),), (("otra", 1.0),)]
        )


def test_encode_views_rejects_non_finite_vector():
    """REGRESIÓN P2 rev. externa integral: un vector con NaN/Inf (envenenaría la ANN) se rechaza."""
    with pytest.raises(ValueError, match="no finitos"):
        embedding_recipes.encode_views(NonFiniteBackend(), [(("legacy", 1.0),)])


def test_unknown_recipe_is_rejected():
    with pytest.raises(ValueError, match="receta de embedding desconocida"):
        embedding_recipes.profile_views({}, "future_v99")


class RaggedBackend:
    """2ª vista con MENOS componentes que la 1ª (dimensiones incompatibles)."""

    def __init__(self):
        self.calls = 0

    def encode_batch(self, texts):
        self.calls += 1
        ancho = 3 if self.calls == 1 else 2
        return [[0.5] * ancho for _ in texts]


class ZeroBackend:
    def encode_batch(self, texts):
        return [[0.0, 0.0] for _ in texts]


def _combina_en_python(rows, encoded):
    """La implementación ANTERIOR a O-5, tal cual, como oráculo del banco."""
    out = []
    for row_index, row in enumerate(rows):
        weights = [view[1] for view in row]
        vectors = [batch[row_index] for batch in encoded]
        dim = len(vectors[0])
        combined = [
            sum(w * float(v[i]) for w, v in zip(weights, vectors, strict=True))
            for i in range(dim)
        ]
        norm = math.sqrt(sum(x * x for x in combined))
        out.append([x / norm for x in combined])
    return out


def test_la_combinacion_en_numpy_da_el_mismo_vector_que_la_de_python():
    """O-5: el cambio a numpy es una optimización, no un cambio de resultado.
    Banco medido: 133,06 ms -> 9,98 ms por lote de 200x384 (13,3x), con una
    desviación máxima de 2,776e-17 — redondeo de coma flotante. Aquí se fija
    ese contrato contra la implementación anterior, escrita como oráculo."""
    import random

    random.seed(11)
    dim, filas = 384, 25
    rows = [(("a", 0.60), ("b", 0.40)) for _ in range(filas)]
    encoded = [
        [[random.uniform(-1, 1) for _ in range(dim)] for _ in range(filas)]
        for _ in range(2)
    ]

    obtenido = embedding_recipes._combine_views(rows, encoded)
    esperado = _combina_en_python(rows, encoded)

    assert len(obtenido) == filas
    for fila_o, fila_e in zip(obtenido, esperado, strict=True):
        assert fila_o == pytest.approx(fila_e, abs=1e-12)


def test_encode_views_rejects_views_with_different_dimensions():
    """Guarda CONSERVADA en el paso a numpy: numpy también fallaría, pero con
    un ValueError sobre "inhomogeneous shape" que no dice qué ocurrió."""
    with pytest.raises(ValueError, match="dimensiones distintas"):
        embedding_recipes.encode_views(
            RaggedBackend(), [(("a", 0.6), ("b", 0.4))]
        )


def test_encode_views_rejects_null_combination():
    """Un vector nulo no se puede normalizar y envenenaría la ANN: fail-closed
    con el mismo mensaje que antes de O-5."""
    with pytest.raises(ValueError, match="vector nulo"):
        embedding_recipes.encode_views(
            ZeroBackend(), [(("a", 0.6), ("b", 0.4))]
        )
