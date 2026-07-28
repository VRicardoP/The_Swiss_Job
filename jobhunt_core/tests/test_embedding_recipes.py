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


def test_unknown_recipe_is_rejected():
    with pytest.raises(ValueError, match="receta de embedding desconocida"):
        embedding_recipes.profile_views({}, "future_v99")
