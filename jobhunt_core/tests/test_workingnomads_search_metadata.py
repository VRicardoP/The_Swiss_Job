"""WorkingNomads must keep the search filters supplied by its previous writer."""
import pytest

from jobhunt_core.harvest.normalize import normalize_offer, offer_text_hash
from jobhunt_core.harvest.providers.native_json import register_handlers


def content(**fields):
    register_handlers()
    return normalize_offer("workingnomads", {
        "title": "English teacher", "company_name": "School",
        "description": "Language instruction", **fields,
    })


@pytest.mark.parametrize("location,canton", [
    ("Zürich, Switzerland", "ZH"), ("GE", "GE"), ("Fribourg", "FR"),
    ("remote", None), (None, None), ({}, None), ("Basel-Landschaft", "BL"),
])
def test_canton_and_language_survive(location, canton):
    result = content(location=location)
    assert result.get("canton") == canton
    assert result["language"] == "en"


@pytest.mark.parametrize("title,description,level,contract", [
    ("Head of teaching", "part time", "head", "part_time"),
    ("Senior English teacher", "full-time", "senior", "full_time"),
    ("Junior teacher", "freelance", "junior", "contract"),
    ("Teacher", "apprenticeship", None, "apprenticeship"),
    ("Teacher", "Temporary", None, "temporary"),
    ("Trainee teacher", "full-time", "intern", "internship"),
    ("Teacher", "x" * 201 + " freelance", None, None),
    ("Teacher", None, None, None),
])
def test_existing_priority_and_description_snippet_boundary(title, description, level, contract):
    result = content(title=title, description=description)
    assert result.get("seniority") == level
    assert result.get("contract_type") == contract
    previous = {key: value for key, value in result.items()
                if key not in {"language", "canton", "seniority", "contract_type"}}
    assert offer_text_hash(result) == offer_text_hash(previous)


def test_only_workingnomads_receives_this_source_contract():
    register_handlers()
    for source in ("remotive", "jobicy"):
        result = normalize_offer(source, {"title": "Senior teacher", "jobTitle": "Senior teacher"})
        assert not {"canton", "language", "seniority", "contract_type"} & result.keys()
