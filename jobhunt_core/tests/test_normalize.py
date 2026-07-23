"""Normalización canónica y text_hash (A-06): unit sin BD."""

import asyncio
import uuid

import pytest

from jobhunt_core import embeddings
from jobhunt_core.harvest import normalize


def _register_tmp(name, fn):
    normalize.register_normalizer(name, fn)
    return lambda: normalize._NORMALIZERS.pop(name, None)


def test_normalize_unknown_source_or_broken_picker_returns_none():
    assert normalize.normalize_offer("sin-registrar", {"title": "x"}) is None
    undo = _register_tmp("rota", lambda raw: raw["no-existe"])
    try:
        assert normalize.normalize_offer("rota", {}) is None  # jamás rompe
    finally:
        undo()


def test_normalize_coerces_types_defensively():
    """Lección A-05 #2: el feed puede traer CUALQUIER tipo — degrada, no revienta."""
    undo = _register_tmp(
        "tipos",
        lambda raw: {
            "title": raw.get("title"), "company": raw.get("company"),
            "description": raw.get("description"), "tags": raw.get("tags"),
            "salary": raw.get("salary"), "location": raw.get("location"),
            "remote": raw.get("remote"),
        },
    )
    try:
        c = normalize.normalize_offer(
            "tipos",
            {
                "title": "  Dev Backend  ", "company": 42,
                "description": ["no", "string"], "tags": ["a", 7, " b ", None],
                "salary": {"min": 1}, "location": True, "remote": "yes",
            },
        )
        assert c == {
            "title": "Dev Backend", "company": None, "description": None,
            "tags": ["a", "b"], "salary": None, "location": None, "remote": None,
        }
        # Sin título no hay oferta presentable.
        assert normalize.normalize_offer("tipos", {"title": 99}) is None
    finally:
        undo()


def test_text_hash_ignores_salary_and_location_but_not_text():
    base = {
        "title": "Dev", "company": "ACME", "description": "d", "tags": ["a"],
        "salary": "50k", "location": "Zurich", "remote": True,
    }
    other_salary = {**base, "salary": "90k", "location": "Ginebra", "remote": False}
    other_title = {**base, "title": "Dev Senior"}
    assert normalize.offer_text_hash(base) == normalize.offer_text_hash(other_salary)
    assert normalize.offer_text_hash(base) != normalize.offer_text_hash(other_title)


def test_build_offer_text_mirrors_legacy_composition():
    content = {"title": "Dev", "company": "ACME", "description": "backend", "tags": ["py", "sql"]}
    assert normalize.build_offer_text(content) == "Dev ACME backend py sql"
    assert normalize.build_offer_text({"title": "Dev", "tags": []}) == "Dev"


def test_store_embeddings_rejects_wrong_dimension():
    with pytest.raises(ValueError, match="384"):
        asyncio.run(
            embeddings.store_offer_embeddings(
                None, uuid.uuid4(), [{"text_hash": "x" * 64, "vector": [0.1] * 10}]
            )
        )
