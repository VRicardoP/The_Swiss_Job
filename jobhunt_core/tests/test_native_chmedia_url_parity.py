"""Portal identity must not inherit ambiguous employer identifiers."""

import pytest

from jobhunt_core.tests.test_native_chmedia import fetch, raw


@pytest.mark.parametrize(
    "identity", ["MA Service IV", "Senior Controller FP&A (m/w/d)"]
)
def test_employer_id_does_not_determine_detail_url(identity):
    result, _ = fetch(pages=[{"items": [raw(externalId=identity)]}])
    assert result.listings[0].url == "https://ostjob.ch/stelle/1"


@pytest.mark.parametrize(
    "identity", ["../admin", "foo?other=1", "foo#bar", "foo\\bar", "foo\x00bar"]
)
def test_unsafe_portal_identifier_rejected(identity):
    result, _ = fetch(pages=[{"items": [raw(), raw(id=identity)]}])
    # DT-148 E1: el ilegible se cuenta en el cursor, el barrido sigue completo.
    assert len(result.listings) == 1 and result.complete
    assert result.next_cursor["invalid"] == 1
