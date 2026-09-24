"""The Swiss native sources must keep exposing the canton saved searches filter on.

The retiring producers fill `jobs.canton` for 2.424/2.966 live ostjob rows,
1.329/1.329 publicjobs and 1.087/1.093 zentraljob (measured on the R5 database,
2026-09-21). Dropping it at handover would silently return zero results for any
saved search filtering by canton on the Swiss portals — the ones this project
exists for. Each rule below mirrors its retiring writer exactly:

    CH Media   backend/providers/base_chmedia.py:74
    PublicJobs backend/providers/publicjobs.py:151
    Zebis      backend/providers/zebis.py:194 (extract_canton over the description)

`scripts/test_search_handover_contract.py` already pins the shared SWISS_CANTONS
table to the legacy one, so only the per-source rule is asserted here.
"""

import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.providers.native_chmedia import CHMediaProvider
from jobhunt_core.harvest.providers.native_publicjobs import PublicJobsProvider
from jobhunt_core.tests.test_native_rss_providers import fetch as rss_fetch
from jobhunt_core.tests.test_native_zebis import body as zebis_body


@pytest.mark.parametrize("source", ["ostjob", "zentraljob"])
@pytest.mark.parametrize(
    "cantons,city,expected",
    [
        (["St. Gallen"], "Wil", "SG"),  # full name resolved through the table
        (["LU"], "Luzern", "LU"),  # already a two-letter portal code
        (["Graubünden"], "", "GR"),  # no city: the canton alone still resolves
        ([], "Wil", None),  # unknown location stays empty, never guessed
        (None, "", None),  # malformed payload must not raise
    ],
)
def test_chmedia_exposes_the_same_canton_as_the_retiring_writer(
    source, cantons, city, expected
):
    CHMediaProvider(source)
    raw = {
        "id": 1,
        "title": "Lehrperson",
        "company": {"name": "Schule"},
        "workplaceCity": city,
        "cantons": cantons,
        "activity": "<p>Unterricht</p>",
    }
    content = normalize_offer(source, raw)
    assert content.get("canton") == expected
    # Absent, never an empty string: `content->>'canton'` must be NULL in
    # SQL exactly as the legacy column is when the writer found no canton.
    assert ("canton" in content) is (expected is not None)


@pytest.mark.parametrize(
    "region,expected",
    [
        ("GE", "GE"),  # the portal already ships the code
        ("Genève", None),  # legacy only accepts a two-letter region
        ("", None),
        (None, None),
    ],
)
def test_publicjobs_exposes_only_the_two_letter_region(region, expected):
    PublicJobsProvider()
    raw = {
        "path": "/job/1",
        "title": "Sachbearbeiter",
        "contactCompany": "Gemeinde",
        "workingAddressCity": "Genève",
        "workingAddressRegion": region,
    }
    content = normalize_offer("publicjobs", raw)
    assert content.get("canton") == expected
    assert ("canton" in content) is (expected is not None)


@pytest.mark.parametrize(
    "description,expected",
    [
        ("<p>Die Schule in Zürich sucht eine Lehrperson.</p>", "ZH"),
        ("<p>Primarschule Wil, Kanton St. Gallen</p>", "SG"),
        ("<p>Eine Lehrperson gesucht.</p>", None),
        ("", None),
    ],
)
def test_zebis_extracts_the_canton_from_its_description(description, expected):
    listing = rss_fetch(
        "zebis",
        zebis_body(
            "https://www.zebis.ch/stellen/lehrperson-1", description=description
        ),
    ).listings[0]
    content = normalize_offer("zebis", listing.payload)
    assert content.get("canton") == expected
    assert ("canton" in content) is (expected is not None)


def test_canton_never_leaks_into_the_embedded_text():
    """Filters travel in their own field; the embedded description is untouched."""
    CHMediaProvider("ostjob")
    content = normalize_offer(
        "ostjob",
        {
            "id": 1,
            "title": "Lehrperson",
            "company": {"name": "Schule"},
            "workplaceCity": "Wil",
            "cantons": ["St. Gallen"],
            "activity": "<p>Unterricht</p>",
        },
    )
    assert content["canton"] == "SG"
    assert "SG" not in content["description"]
