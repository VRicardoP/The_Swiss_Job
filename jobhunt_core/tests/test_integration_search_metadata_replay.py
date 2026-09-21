"""Upgrading normalization repairs an unchanged raw through the ordinary sink."""

from jobhunt_core.harvest import normalize
from jobhunt_core.harvest.providers import legacy_shadow
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.tests.test_integration_offer import (
    db, pytestmark, _seed, _sink, _vacancy_state, _rows,
)


def test_existing_raw_replays_to_enriched_canonical_without_duplicate_raw(db, monkeypatch):
    factory, made = db
    name = "legacy:search-old-raw"
    legacy_shadow.ensure_registered(name)
    scope = _seed(factory, made, name)
    raw = RawListing("search-old-raw", "https://search.test/old", {
        "title": "Analyst", "company_name": "Example", "description": "Data",
        "canton": "ZH", "language": "de", "salary_max_chf": 100000,
    })
    current_picker = normalize._NORMALIZERS[name]

    def old_picker(payload):
        return {k: v for k, v in current_picker(payload).items() if k in normalize.CONTENT_FIELDS}

    with monkeypatch.context() as previous:
        previous.setitem(normalize._NORMALIZERS, name, old_picker)
        _sink(factory, scope, [raw])
    before = _vacancy_state(factory, raw.external_id)
    assert "canton" not in before.content
    _sink(factory, scope, [raw])
    after = _vacancy_state(factory, raw.external_id)
    assert after.cur != before.cur and after.text_hash == before.text_hash
    assert after.content["canton"] == "ZH" and after.content["language"] == "de"
    assert after.content["salary_max_chf"] == 100000
    counts = _rows(factory, """
        SELECT count(*) AS n FROM source_listing_revisions r
        JOIN source_listing_incarnations i ON i.id=r.incarnation_id
        WHERE i.vacancy_id=:v
    """, v=after.vac)
    assert counts[0].n == 1
    _sink(factory, scope, [raw])
    assert _vacancy_state(factory, raw.external_id).cur == after.cur
