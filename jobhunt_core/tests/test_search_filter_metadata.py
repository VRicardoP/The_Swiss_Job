"""The source handover must not discard fields used by saved searches."""

import pytest

from jobhunt_core.harvest import normalize
from jobhunt_core.harvest.providers import legacy_shadow


def normalized(**metadata):
    name = "legacy:search-metadata-test"
    legacy_shadow.ensure_registered(name)
    return normalize.normalize_offer(name, {
        "title": "Python developer", "company_name": "Example",
        "description": "Backend development", **metadata,
    })


@pytest.mark.parametrize("field,value", [
    ("canton", "ZH"), ("language", "de"), ("seniority", "senior"),
    ("contract_type", "full_time"), ("salary_min_chf", 80000),
    ("salary_max_chf", 120000), ("salary_min_chf", 0),
])
def test_search_metadata_survives_canonicalization(field, value):
    base = normalized()
    content = normalized(**{field: value})
    assert content[field] == value
    assert normalize.offer_content_hash(content) != normalize.offer_content_hash(base)
    assert normalize.offer_text_hash(content) == normalize.offer_text_hash(base)
    assert normalize.build_offer_text(content) == normalize.build_offer_text(base)


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "80000", {}, [], 2**31])
def test_invalid_salary_metadata_is_not_fabricated(value):
    content = normalized(salary_min_chf=value, salary_max_chf=value)
    assert "salary_min_chf" not in content
    assert "salary_max_chf" not in content


@pytest.mark.parametrize("value", [None, True, 123, {}, [], "", "  "])
def test_invalid_text_metadata_does_not_break_normalization(value):
    content = normalized(canton=value, language=value, seniority=value, contract_type=value)
    assert set(content) == {
        "title", "company", "description", "tags", "salary", "location", "remote",
    }


def test_empty_metadata_keeps_previous_content_hash():
    content = normalized()
    expected = {
        "title": "Python developer", "company": "Example",
        "description": "Backend development", "tags": [], "salary": None,
        "location": None, "remote": None,
    }
    assert content == expected
    assert normalize.offer_content_hash(content) == normalize.offer_content_hash(expected)
