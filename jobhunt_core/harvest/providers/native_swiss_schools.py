"""The Swiss international-school watchlist, board by board.

Eight sources of this family publish a vacancy every few months, so **an empty
board is the NORMAL state** — the opposite of every other adapter here, where a
sweep that finds nothing means a broken parser. The rule the retiring engine
calls "vacío verificado" is what must survive: fetch the page, RECOGNISE it,
and only then report zero as healthy. A page we cannot recognise is a visible
failure, never "no vacancies".

Recognition is a per-board marker measured against the live boards on
2026-10-15 (`2026-10-15-dt144-swiss-schools-preflight.json`): the page title of
the intended school, plus the board container where the portal has one. ISB
goes further and says it itself — its board renders `fsElementEmpty` ("No post
to display"), which is a verified empty straight from the portal.

Each board keeps the producer's own quirks, including the two synthetic URLs:
haut-lac and ISCS have no per-vacancy link, and sharing the board URL left only
ONE vacancy per school alive against the unique-URL constraint (class VD.1), so
the anchor is part of the identity.
"""

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Callable

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.providers.browser_headers import (
    BROWSER_HEADERS,
    MAX_PAGES_PARAM,
    page_budget,
)
from jobhunt_core.harvest.providers.html_response import TagStackParser
from jobhunt_core.harvest.providers.search_metadata import swiss_canton
from jobhunt_core.harvest.types import FetchResult, RawListing

PAGE_PAUSE_S = 3.0  # = SwissSchool*Scraper.RATE_LIMIT_SECONDS
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
JITTER_RATIO = 0.5
TAGS = ("education", "international school")
SOURCE_PREFIX = "swiss_schools"

logger = logging.getLogger(__name__)


class StructureUnknown(Exception):
    """The page is not the board we know — never "no vacancies"."""


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# A tiny document the board parsers can query, built once per page.
# ---------------------------------------------------------------------------


@dataclass
class Element:
    tag: str
    classes: tuple[str, ...]
    attrs: dict
    parent: "Element | None"
    chunks: list

    @property
    def text(self) -> str:
        """Joined like BeautifulSoup's `get_text(strip=True)`."""
        return "".join(self.chunks)

    @property
    def spaced_text(self) -> str:
        """Joined like `get_text(" ", strip=True)`."""
        return " ".join(self.chunks)

    def inside(self, ancestor: "Element") -> bool:
        node = self.parent
        while node is not None:
            if node is ancestor:
                return True
            node = node.parent
        return False

    def style(self) -> str:
        return self.attrs.get("style") or ""


class _Document(TagStackParser):
    """Every element with its own text, its classes and its parent.

    The boards are small pages with shallow selectors, so one element list is
    simpler — and far easier to read — than five hand-rolled state machines.
    """

    def __init__(self):
        super().__init__()
        self.elements: list[Element] = []
        self.title = ""
        self._open_elements: list[Element] = []
        self._title_depth = 0

    def on_open(self, tag, classes, attrs, depth):
        parent = self._open_elements[-1] if self._open_elements else None
        element = Element(tag, tuple(classes), attrs, parent, [])
        self.elements.append(element)
        self._open_elements.append(element)
        if tag == "title" and not self._title_depth:
            self._title_depth = depth

    def on_void(self, tag, classes, attrs):
        parent = self._open_elements[-1] if self._open_elements else None
        self.elements.append(Element(tag, tuple(classes), attrs, parent, []))

    def on_close(self, tag, depth):
        # The base closes innermost first, one call per level.
        if self._open_elements:
            self._open_elements.pop()
        if self._title_depth and self._title_depth >= depth:
            self._title_depth = 0

    def on_text(self, data):
        stripped = data.strip()
        if not stripped:
            return
        if self._title_depth:
            self.title += stripped
        # Text belongs to every open ancestor, which is what get_text() means.
        for element in self._open_elements:
            element.chunks.append(stripped)

    # -- queries ---------------------------------------------------------
    def find(self, tag=None, class_name=None, attr=None, within=None):
        """Elements matching tag/class/attribute, optionally inside another."""
        for element in self.elements:
            if tag is not None and element.tag != tag:
                continue
            if class_name is not None and class_name not in element.classes:
                continue
            if attr is not None and attr not in element.attrs:
                continue
            if within is not None and not element.inside(within):
                continue
            yield element

    def first(self, **query):
        return next(self.find(**query), None)

    def has(self, **query) -> bool:
        return self.first(**query) is not None


def parse_document(html: str) -> _Document:
    parser = _Document()
    parser.feed(html)
    parser.close()
    return parser


# ---------------------------------------------------------------------------
# The boards. One function per school, each keeping its producer's selectors.
# ---------------------------------------------------------------------------

_JOBID_RE = re.compile(r"jobid=(\d+)")
_ISCS_KEYWORDS = (
    "TEACHER",
    "COORDINATOR",
    "PROGRAMME",
    "PROGRAM",
    "ACADEMY",
    "DIRECTOR",
    "PRINCIPAL",
    "ASSISTANT",
)
_HAUTLAC_NOT_A_TITLE = {"profile", "profil", "mission", "responsibilities"}


@dataclass(frozen=True)
class Board:
    source: str
    school_id: str
    school_name: str
    city: str
    base_url: str
    listing_url: str
    title_marker: str
    parse: Callable[[_Document, "Board"], list[dict]]
    pages: int = 1
    page_url: Callable[["Board", int], str] | None = None


def _stub(board: Board, source_id: str, title: str, url: str) -> dict:
    return {
        "source_id": source_id,
        "title": title,
        "company": board.school_name,
        "location": f"{board.city}, CH",
        "url": url,
        "school_id": board.school_id,
        "language": "en",
    }


def _zis(document: _Document, board: Board) -> list[dict]:
    """Vacancies link straight to SchoolSpring with a numeric job id."""
    out, seen = [], set()
    for link in document.find(tag="a", attr="href"):
        href = link.attrs["href"]
        if "schoolspring.com" not in href:
            continue
        match = _JOBID_RE.search(href)
        if not match or match.group(1) in seen:
            continue
        title = link.text.replace("\xa0", " ").strip()
        if not title:
            continue
        seen.add(match.group(1))
        out.append(_stub(board, match.group(1), title, href))
    return out


def _isb(document: _Document, board: Board) -> list[dict]:
    container = document.first(class_name="fsPostElement")
    if container is None:
        raise StructureUnknown("the fsPostElement board is gone")
    if document.first(class_name="fsElementEmpty") is not None:
        # The portal says it itself: "No post to display".
        return []
    out = []
    for post in document.elements:
        if not post.inside(container):
            continue
        if not (
            (post.tag == "article" and "fsArticle" in post.classes)
            or "fsPost" in post.classes
            or (
                post.tag == "li"
                and post.parent
                and "fsConstituentList" in post.parent.classes
            )
        ):
            continue
        stub = _isb_post(document, post, board)
        if stub:
            out.append(stub)
    return out


def _isb_post(document: _Document, post: Element, board: Board) -> dict | None:
    link = None
    for class_name in ("fsPostTitle", "fsArticleTitle"):
        holder = document.first(class_name=class_name, within=post)
        if holder is not None:
            link = document.first(tag="a", attr="href", within=holder)
            if link is not None:
                break
    if link is None:
        for class_name in ("fsPostLink", "fsArticleLink"):
            link = document.first(
                tag="a", attr="href", class_name=class_name, within=post
            )
            if link is not None:
                break
    if link is None:
        for tag in ("h2", "h3"):
            holder = document.first(tag=tag, within=post)
            if holder is not None:
                link = document.first(tag="a", attr="href", within=holder)
                if link is not None:
                    break
    if link is None:
        return None
    title, href = link.text, link.attrs.get("href", "")
    if not title or not href:
        return None
    url = href if href.startswith("http") else f"{board.base_url}{href}"
    return _stub(board, href.rstrip("/").split("/")[-1] or url, title, url)


def _ecolint(document: _Document, board: Board) -> list[dict]:
    out = []
    for article in document.find(tag="article"):
        if not {"node-job-teaser", "node-job"} & set(article.classes):
            continue
        title_el = document.first(
            class_name="f-n-title", within=article
        ) or document.first(class_name="field-name-title-field", within=article)
        link = next(
            (
                element
                for element in document.find(tag="a", attr="href", within=article)
                if "/about/careers/" in element.attrs["href"]
            ),
            None,
        )
        if title_el is None or link is None:
            continue
        title, href = title_el.text, link.attrs["href"]
        if not title or not href:
            continue
        url = href if href.startswith("http") else f"{board.base_url}{href}"
        out.append(_stub(board, href.rstrip("/").split("/")[-1] or url, title, url))
    return out


def _hautlac(document: _Document, board: Board) -> list[dict]:
    """A HubSpot page: the title is a highlighted heading, or a card's lead."""
    widgets = list(document.find(class_name="hs_cos_wrapper_type_rich_text"))
    if not widgets:
        raise StructureUnknown("the rich-text widgets are gone")
    out, seen = [], set()
    for widget in widgets:
        title = _hautlac_title(document, widget)
        if not title or len(title) < 5 or title.lower() in _HAUTLAC_NOT_A_TITLE:
            continue
        if title in seen:
            continue
        seen.add(title)
        source_id = title.lower().replace(" ", "-")[:80]
        # Synthetic anchor: the board has no per-vacancy link and a shared URL
        # left only one vacancy per school alive (VD.1).
        out.append(_stub(board, source_id, title, f"{board.listing_url}#{source_id}"))
    return out


def _hautlac_title(document: _Document, widget: Element) -> str:
    for heading in document.find(tag="h3", within=widget):
        for span in document.find(tag="span", within=heading):
            if "background-color" in span.style():
                return span.text
    for card in document.find(tag="div", within=widget):
        style = card.style()
        if "background-color" not in style or "padding" not in style:
            continue
        for paragraph in document.find(tag="p", within=card):
            if "font-size" not in paragraph.style():
                continue
            strong = document.first(tag="strong", within=paragraph)
            if strong is not None:
                return strong.text
    return ""


def _iscs(document: _Document, board: Board) -> list[dict]:
    """A plain page: a short list item naming a role, in bold."""
    out, seen = [], set()
    for item in document.find(tag="li"):
        text = item.spaced_text
        if not text or len(text) > 200:
            continue
        if not any(keyword in text.upper() for keyword in _ISCS_KEYWORDS):
            continue
        strong = document.first(tag="strong", within=item) or document.first(
            tag="b", within=item
        )
        if strong is None:
            continue
        title = strong.spaced_text
        if len(title) < 5 or title.lower() in seen:
            continue
        seen.add(title.lower())
        source_id = title.lower().replace(" ", "-")[:80]
        out.append(
            _stub(board, source_id, title.title(), f"{board.listing_url}#{source_id}")
        )
    return out


def _ecolint_page(board: Board, page: int) -> str:
    # Drupal counts from zero.
    return f"{board.listing_url}?page={page - 1}"


BOARDS: tuple[Board, ...] = (
    Board(
        source="swiss_schools_zis",
        school_id="zis_zurich",
        school_name="Zurich International School",
        city="Zurich",
        base_url="https://www.zis.ch",
        listing_url="https://www.zis.ch/one-zis-community/employment",
        title_marker="employment - zurich international school",
        parse=_zis,
    ),
    Board(
        source="swiss_schools_isb",
        school_id="isb_basel",
        school_name="International School Basel",
        city="Basel",
        base_url="https://www.isbasel.ch",
        listing_url=(
            "https://www.isbasel.ch/connect/news/?board=employment-public-job-postings"
        ),
        title_marker="international school basel",
        parse=_isb,
    ),
    Board(
        source="swiss_schools_ecolint",
        school_id="ecolint_geneva",
        school_name="International School of Geneva (Ecolint)",
        city="Geneva",
        base_url="https://www.ecolint.ch",
        listing_url="https://www.ecolint.ch/en/job-opportunities",
        title_marker="job opportunities",
        parse=_ecolint,
        pages=5,
        page_url=_ecolint_page,
    ),
    Board(
        source="swiss_schools_hautlac",
        school_id="hautlac_stlegier",
        school_name="Haut-Lac Bilingual School",
        city="Saint-Légier-La Chiésaz",
        base_url="https://info.haut-lac.ch",
        listing_url="https://info.haut-lac.ch/jobs-and-career",
        title_marker="haut-lac",
        parse=_hautlac,
    ),
    Board(
        source="swiss_schools_iscs",
        school_id="iscs_zug",
        school_name="ISCS — International School of Central Switzerland",
        city="Cham/Zug",
        base_url="https://iscs-zug.ch",
        listing_url="https://iscs-zug.ch/employment/",
        title_marker="employment",
        parse=_iscs,
    ),
)
BOARDS_BY_SOURCE = {board.source: board for board in BOARDS}
# Literal census for `scripts/check_source_ownership.py`: this module owns more
# than one source, so there is no single SOURCE_NAME for it to discover.
SOURCE_NAMES = (
    "swiss_schools_zis",
    "swiss_schools_isb",
    "swiss_schools_ecolint",
    "swiss_schools_hautlac",
    "swiss_schools_iscs",
)


def board_rows(board: Board, html: str) -> list[dict]:
    """The board's vacancies, or a failure when the page is not the board.

    Recognition first: a page whose title is not the school's is an error page,
    a redirect or a redesign, and reporting zero for it would be the silent
    loss this family exists to avoid.
    """
    document = parse_document(html)
    if board.title_marker not in document.title.lower():
        raise StructureUnknown(
            f"page title {document.title[:60]!r} is not {board.school_name}'s board"
        )
    return board.parse(document, board)


# ---------------------------------------------------------------------------
# Canonical content and the provider the eight boards share.
# ---------------------------------------------------------------------------


def _content(raw: dict) -> dict:
    location = _text(raw.get("location")) or "Switzerland"
    school_id = _text(raw.get("school_id"))
    content = {
        "title": _text(raw.get("title")),
        "company": _text(raw.get("company")) or "Unknown",
        # The boards publish a title and a link, never a description.
        "description": None,
        "location": location,
        "remote": False,
        "tags": [*TAGS, school_id] if school_id else list(TAGS),
        "salary": None,
    }
    language = _text(raw.get("language"))
    if language:
        content["language"] = language
    canton = swiss_canton(location)
    if canton:
        # Derived from the school's own city, which the producer already knew
        # and never used.
        content["canton"] = canton
    return content


def register_handlers() -> None:
    for board in BOARDS:
        register_normalizer(board.source, _content)
        register_extractor(
            board.source,
            lambda raw: (_text(raw.get("title")), _text(raw.get("company"))),
        )


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds + random.uniform(0, seconds * JITTER_RATIO))


async def _read(http, url: str) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with http.stream(
                "GET",
                url,
                headers=BROWSER_HEADERS,
                timeout=REQUEST_TIMEOUT_S,
                follow_redirects=True,
            ) as response:
                if response.status_code in RETRY_STATUSES:
                    raise httpx.HTTPStatusError(
                        "retryable response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ProviderResponseError(
                            f"{SOURCE_PREFIX}: response exceeds byte budget"
                        )
                    chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", "replace")
        except ProviderResponseError:
            raise
        except httpx.HTTPError as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code in RETRY_STATUSES
            )
            if not retryable or attempt == MAX_RETRIES:
                raise
            await _pause(RETRY_BACKOFF_S * (2**attempt))
    raise AssertionError("unreachable")


class SwissSchoolProvider(BaseProvider):
    """One watched board. Zero vacancies is a RESULT, not a failure."""

    SEMANTIC_PARAMS = ()

    def __init__(self, board: Board):
        self.board = board
        self.name = board.source
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        board = self.board
        if not isinstance(params, dict) or set(params) - {MAX_PAGES_PARAM}:
            raise ProviderConfigError(f"{board.source} scope takes no query parameters")
        budget = page_budget(params, board.pages) or board.pages
        by_identity: dict[str, dict] = {}
        pages = 0

        for page in range(1, budget + 1):
            if pages:
                await _pause(PAGE_PAUSE_S)
            url = board.page_url(board, page) if board.page_url else board.listing_url
            try:
                rows = board_rows(board, await _read(http, url))
            except StructureUnknown as exc:
                # A page we cannot recognise is NOT an empty board.
                raise ProviderResponseError(f"{board.source}: {exc}") from None
            pages += 1
            fresh = 0
            for row in rows:
                if row["source_id"] not in by_identity:
                    by_identity[row["source_id"]] = row
                    fresh += 1
            if not fresh:
                break

        listings = tuple(
            RawListing(
                external_id=f"{board.school_id}:{row['source_id']}",
                url=row["url"],
                payload=row,
            )
            for row in by_identity.values()
        )
        if not listings:
            # VERIFIED empty: the page was fetched and recognised as this
            # school's board. In a watchlist that is the normal state, and
            # reporting it as a failure would make most of the family fail
            # every day.
            logger.info("%s: board recognised with no vacancies", board.source)
        return FetchResult(
            listings,
            {
                "pages": pages,
                "vacancies": len(listings),
                "verified_empty": not listings,
            },
            pages_fetched=pages,
            complete=True,
        )


def providers() -> list[SwissSchoolProvider]:
    return [SwissSchoolProvider(board) for board in BOARDS]
