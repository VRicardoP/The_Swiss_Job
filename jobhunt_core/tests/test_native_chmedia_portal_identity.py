"""Real CH Media employers reuse both ATS ids and generic application URLs."""
from jobhunt_core.tests.test_native_chmedia import fetch, raw


def test_portal_id_separates_employer_id_and_apply_url_collisions():
    rows = [raw(id=1098816, companyId=1825, externalId="691", urlApplication="https://example.org/careers"),
            raw(id=1084785, companyId=1548, externalId="691", urlApplication="https://example.org/careers")]
    result, _ = fetch(pages=[{"items": rows}])
    assert result.complete
    assert len({r.external_id for r in result.listings}) == 2
    assert len({r.url for r in result.listings}) == 2
    assert [r.url for r in result.listings] == ["https://ostjob.ch/stelle/1098816", "https://ostjob.ch/stelle/1084785"]
    assert all(r.apply_url == "https://example.org/careers" for r in result.listings)


def test_portal_identity_ignores_mutable_ats_identifier_and_apply_url():
    old, _ = fetch(pages=[{"items": [raw(id=1098816, externalId="691", urlApplication="https://example.org/old")]}])
    new, _ = fetch(pages=[{"items": [raw(id=1098816, externalId="new691", urlApplication="https://example.org/new")]}])
    assert old.listings[0].external_id == new.listings[0].external_id
    assert old.listings[0].url == new.listings[0].url
    assert old.listings[0].apply_url != new.listings[0].apply_url
