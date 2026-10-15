"""The watched school boards: zero vacancies is a RESULT, not a failure."""

import asyncio

import httpx
import pytest

from jobhunt_core.harvest.providers.native_swiss_schools import (
    BOARDS_BY_SOURCE,
    SwissSchoolProvider,
    board_rows,
)

ZIS = """<html><head><title>Employment - Zurich International School | ZIS</title></head>
<body><main>
  <a href="https://zurichinternational.schoolspring.com/?jobid=4201">Secondary Maths Teacher</a>
  <a href="https://zurichinternational.schoolspring.com/?jobid=4202">Learning&nbsp;Support Assistant</a>
  <a href="https://zurichinternational.schoolspring.com/?jobid=4201">Secondary Maths Teacher</a>
  <a href="https://www.zis.ch/about">About us</a>
</main></body></html>"""

ZIS_EMPTY = """<html><head><title>Employment - Zurich International School | ZIS</title>
</head><body><main><p>There are no current openings.</p></main></body></html>"""

ISB = """<html><head><title>News - International School Basel</title></head><body>
<div class="fsPostElement">
  <article class="fsArticle"><div class="fsPostTitle">
    <a href="/connect/news/post/primary-teacher">Primary Teacher</a></div></article>
  <div class="fsPost"><h2>
    <a href="https://www.isbasel.ch/connect/news/post/it-manager">IT Manager</a></h2></div>
</div></body></html>"""

ISB_EMPTY = """<html><head><title>News - International School Basel</title></head><body>
<div class="fsPostElement"><div class="fsElementEmpty">No post to display.</div></div>
</body></html>"""

ECOLINT = """<html><head><title>Job Opportunities | Ecolint</title></head><body>
<article class="node-job-teaser">
  <span class="f-n-title">Teacher of Biology</span>
  <a href="/en/about/careers/teacher-of-biology">Apply</a>
</article>
<article class="node-article"><span class="f-n-title">Not a job</span>
  <a href="/en/news/x">x</a></article>
</body></html>"""

HAUTLAC = """<html><head><title>Jobs at Haut-Lac</title></head><body>
<div class="hs_cos_wrapper_type_rich_text">
  <h3><span style="background-color: #eee">Science Laboratory Technician</span></h3>
</div>
<div class="hs_cos_wrapper_type_rich_text">
  <div style="background-color:#fafafa; padding:20px">
    <p style="font-size:20px"><strong>Head of Primary</strong></p></div>
</div>
<div class="hs_cos_wrapper_type_rich_text">
  <h3><span style="background-color: #eee">Profile</span></h3>
</div></body></html>"""

ISCS = """<html><head><title>Employment | ISCS</title></head><body><ul>
  <li><strong>Primary Teacher</strong> — full time</li>
  <li><strong>Maths Coordinator</strong></li>
  <li>Some navigation link</li>
  <li><strong>Primary Teacher</strong> — duplicate</li>
</ul></body></html>"""


def fetch(source, pages, *, params=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_swiss_schools as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
        monkeypatch.setattr(module, "RETRY_BACKOFF_S", 0)
    provider = SwissSchoolProvider(source, module.boards_by_source()[source])
    requests = []

    async def run():
        def transport(request):
            requests.append(str(request.url))
            body = (
                pages[min(len(requests), len(pages)) - 1]
                if isinstance(pages, list)
                else pages
            )
            if isinstance(body, int):
                return httpx.Response(body, text="nope")
            return httpx.Response(200, text=body)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await provider.fetch_new(
                {} if params is None else params, None, client
            )
        return result, requests

    return asyncio.run(run())


def test_a_board_with_vacancies_keeps_the_producers_shape(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch("swiss_schools_zis", ZIS, monkeypatch=monkeypatch)

    assert result.complete and len(result.listings) == 2
    assert requests == ["https://www.zis.ch/one-zis-community/employment"]
    listing = result.listings[0]
    assert listing.external_id == "zis_zurich:4201"
    assert listing.url == "https://zurichinternational.schoolspring.com/?jobid=4201"
    assert normalize_offer("swiss_schools_zis", listing.payload) == {
        "title": "Secondary Maths Teacher",
        "company": "Zurich International School",
        "description": None,
        "location": "Zurich, CH",
        "remote": False,
        "tags": ["education", "international school", "zis_zurich"],
        "salary": None,
        "language": "en",
        # Derived from the school's own city, which the producer never used.
        "canton": "ZH",
    }
    # The non-breaking space the board emits is normalised, and the repeated
    # link is one vacancy.
    assert result.listings[1].payload["title"] == "Learning Support Assistant"


def test_an_empty_board_is_a_verified_empty_not_a_failure(monkeypatch):
    """MUERDE con la regla de los demás adaptadores: seis de ocho tableros de
    esta familia están vacíos hoy, y lanzar los haría fallar todos los días."""
    result, _requests = fetch("swiss_schools_zis", ZIS_EMPTY, monkeypatch=monkeypatch)

    assert result.listings == ()
    assert result.complete and result.error is None
    assert result.next_cursor["verified_empty"] is True


def test_the_portal_saying_it_is_empty_is_also_verified(monkeypatch):
    result, _requests = fetch("swiss_schools_isb", ISB_EMPTY, monkeypatch=monkeypatch)

    assert result.listings == () and result.complete


@pytest.mark.parametrize(
    "source",
    [
        "swiss_schools_zis",
        "swiss_schools_isb",
        "swiss_schools_ecolint",
        "swiss_schools_hautlac",
        "swiss_schools_iscs",
    ],
)
def test_a_page_that_is_not_the_board_is_a_visible_failure(source, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    # A redirect, an error page or a redesign: never "no vacancies".
    with pytest.raises(ProviderResponseError):
        fetch(
            source,
            "<html><head><title>Page not found</title></head></html>",
            monkeypatch=monkeypatch,
        )


def test_the_isb_board_container_disappearing_is_a_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    html = "<html><head><title>News - International School Basel</title></head><body></body></html>"
    with pytest.raises(ProviderResponseError, match="fsPostElement"):
        fetch("swiss_schools_isb", html, monkeypatch=monkeypatch)


def test_the_hautlac_widgets_disappearing_is_a_failure(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    html = (
        "<html><head><title>Jobs at Haut-Lac</title></head><body><p>x</p></body></html>"
    )
    with pytest.raises(ProviderResponseError, match="rich-text"):
        fetch("swiss_schools_hautlac", html, monkeypatch=monkeypatch)


def test_isb_reads_both_post_shapes():
    rows = board_rows(BOARDS_BY_SOURCE["swiss_schools_isb"], ISB)

    assert [r["title"] for r in rows] == ["Primary Teacher", "IT Manager"]
    assert rows[0]["url"] == "https://www.isbasel.ch/connect/news/post/primary-teacher"
    assert rows[0]["source_id"] == "primary-teacher"


def test_ecolint_only_takes_job_articles():
    rows = board_rows(BOARDS_BY_SOURCE["swiss_schools_ecolint"], ECOLINT)

    assert [r["title"] for r in rows] == ["Teacher of Biology"]
    assert (
        rows[0]["url"] == "https://www.ecolint.ch/en/about/careers/teacher-of-biology"
    )


def test_hautlac_reads_both_templates_and_drops_section_headings():
    rows = board_rows(BOARDS_BY_SOURCE["swiss_schools_hautlac"], HAUTLAC)

    assert [r["title"] for r in rows] == [
        "Science Laboratory Technician",
        "Head of Primary",
    ]
    # Synthetic anchor per vacancy: a shared board URL left only one alive.
    assert rows[0]["url"].endswith("#science-laboratory-technician")
    assert rows[1]["url"].endswith("#head-of-primary")


def test_iscs_needs_a_keyword_and_a_bold_title():
    rows = board_rows(BOARDS_BY_SOURCE["swiss_schools_iscs"], ISCS)

    assert [r["title"] for r in rows] == ["Primary Teacher", "Maths Coordinator"]
    assert rows[0]["url"] == "https://iscs-zug.ch/employment/#primary-teacher"


def test_ecolint_pagination_stops_when_a_page_adds_nothing(monkeypatch):
    result, requests = fetch(
        "swiss_schools_ecolint", [ECOLINT, ECOLINT], monkeypatch=monkeypatch
    )

    # The second page repeats the first, so the sweep stops there.
    assert len(requests) == 2
    assert [url.endswith("page=0") for url in requests][0]
    assert len(result.listings) == 1


def test_an_invalid_scope_fails_before_the_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch("swiss_schools_zis", ZIS, params={"school": "x"}, monkeypatch=monkeypatch)


def test_every_board_declares_a_title_marker_and_a_school():
    from jobhunt_core.harvest.providers.native_swiss_schools import (
        ALL_BOARDS,
        SOURCE_NAMES,
        boards_by_source,
    )

    # The literal census the ownership ratchet reads must match the sources.
    assert tuple(boards_by_source()) == SOURCE_NAMES
    assert len(ALL_BOARDS) == 10
    BOARDS = ALL_BOARDS
    for board in BOARDS:
        assert board.title_marker and board.school_id and board.school_name
        assert board.listing_url.startswith("https://")


# ---------------------------------------------------------------------------
# The two shared career portals (Nord Anglia and Inspired)
# ---------------------------------------------------------------------------

SF_PAGE = """<html><head><title>Champittet - Nord Anglia Education Jobs</title></head>
<body><ul>
  <li class="job-tile" data-url="/job/Nyon-Teacher-of-Maths/1234/">
    <a class="jobTitle-link" href="/job/Nyon-Teacher-of-Maths/1234/">Teacher of Maths</a>
    <div id="job-1234-desktop-section-customfield3-value">Collège Champittet</div>
    <div id="job-1234-desktop-section-city-value">Pully</div>
    <div id="job-1234-desktop-section-country-value">CH</div>
  </li>
  <li class="job-tile" data-url="/job/Shanghai-Teacher/9999/">
    <a class="jobTitle-link" href="/job/Shanghai-Teacher/9999/">Teacher of Science</a>
    <div id="job-9999-desktop-section-customfield3-value">Nord Anglia Shanghai</div>
    <div id="job-9999-desktop-section-city-value">Shanghai</div>
  </li>
  <li class="job-tile" data-url="/job/no-field/7777/">
    <a class="jobTitle-link" href="/job/no-field/7777/">Unattributed</a>
  </li>
</ul></body></html>"""

SF_EMPTY = """<html><head><title>Inspired Education Jobs</title></head>
<body><p>No results found.</p></body></html>"""


def _board(school_id):
    from jobhunt_core.harvest.providers.native_swiss_schools import ATS_BOARDS

    return next(board for board in ATS_BOARDS if board.school_id == school_id)


def test_the_shared_portal_filters_by_the_tile_s_own_school():
    """La búsqueda del portal es difusa: sin el filtro, una consulta de un
    colegio devuelve las vacantes de todos."""
    rows = board_rows(_board("champittet_nyon"), SF_PAGE)

    assert [row["title"] for row in rows] == ["Teacher of Maths"]
    assert rows[0]["source_id"] == "1234"
    assert rows[0]["url"] == (
        "https://careers.nordangliaeducation.com/job/Nyon-Teacher-of-Maths/1234/"
    )
    # The tile's own city wins over the school's configured one.
    assert rows[0]["location"] == "Pully, CH"


def test_a_tile_without_the_school_field_is_attributed_to_nobody():
    rows = board_rows(_board("champittet_nyon"), SF_PAGE)

    assert "Unattributed" not in [row["title"] for row in rows]


def test_an_empty_shared_portal_is_a_verified_empty(monkeypatch):
    result, requests = fetch(
        "swiss_schools_inspired", SF_EMPTY, monkeypatch=monkeypatch
    )

    # Two schools, two pages, zero vacancies and a healthy run.
    assert len(requests) == 2
    assert result.listings == () and result.complete
    assert result.next_cursor == {
        "pages": 2,
        "boards": 2,
        "vacancies": 0,
        "verified_empty": True,
    }


def test_the_shared_portal_sweeps_every_school_of_the_source(monkeypatch):
    result, requests = fetch("swiss_schools_nae", SF_PAGE, monkeypatch=monkeypatch)

    # Three schools; only Champittet's board matches this fixture's tiles, and
    # the other two recognise the page and find nothing of theirs.
    assert len(requests) == 3
    assert [listing.external_id for listing in result.listings] == [
        "champittet_nyon:1234"
    ]
    assert result.next_cursor["boards"] == 3


def test_a_shared_portal_page_that_is_not_the_portal_fails(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch(
            "swiss_schools_nae",
            "<html><head><title>Login</title></head></html>",
            monkeypatch=monkeypatch,
        )
