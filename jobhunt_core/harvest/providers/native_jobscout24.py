"""JobScout24's bounded keyword sweep of server-rendered listing pages.

The portal has no API: the search pages are plain HTML (26 cards per page) and
the cards carry no description and only a relative date ("2 d"). The producer
being retired therefore scrapes a fixed set of IT keywords, two pages each, and
fills a bounded number of missing descriptions from the detail pages. All three
bounds are kept: more keywords, more pages or unbounded detail fetches would be
a different harvest, not a migration.

Cards are read with the standard library's HTML parser — `select()` is
reproduced selector by selector in `_Cards`, and no parsing dependency enters
the core.
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    SOFT_BLOCK_STATUS,
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
    SoftBlocked,
)
from jobhunt_core.harvest.providers.browser_headers import BROWSER_HEADERS
from jobhunt_core.harvest.providers.html_response import (
    TagStackParser,
    job_posting_description,
    looks_like_soft_block,
)
from jobhunt_core.harvest.providers.jobicy_metadata import (
    extract_tech_skills,
    normalize_location,
)
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "jobscout24"
BASE_URL = "https://www.jobscout24.ch"
# The portal is generalist (~91k offers); the producer narrows it to IT with
# keywords in the PATH because `?term=` does not filter. SortIndex=1 orders by
# recency, which is what makes two pages per keyword meaningful.
KEYWORDS = ("informatik", "software", "python", "javascript", "devops")
MAX_PAGES_PER_KEYWORD = 2
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DETAIL_BYTES = 2 * 1024 * 1024
MAX_RECENT_JOBS = 300
MAX_DETAIL_FETCHES = 20
MAX_DESCRIPTION_LENGTH = 500
MAX_TAGS_PER_JOB = 10
REQUEST_TIMEOUT_S = 30
REQUEST_PAUSE_S = 0.5
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}

CARD_CLASS = "job-list-item"
# Sponsored badge — a listing position, not an attribute of the job.
TOP_LISTING_TAG = "Top Listing"
# Relative card dates: "10 min", "5 h", "2 d", "1 w", "30+ d".
_RELATIVE_DATE_RE = re.compile(r"(\d+)\s*\+?\s*([hdwm])", re.IGNORECASE)
_RELATIVE_DATE_UNITS = {"h": "hours", "d": "days", "w": "weeks", "m": "minutes"}
# Workload badges ("100%", "80% - 100%") are the employment type, not a tag.
_WORKLOAD_TAG_RE = re.compile(r"^\d+\s*%(?:\s*-\s*\d+\s*%)?$")
logger = logging.getLogger(__name__)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _listing_url(keyword: str, page: int) -> str:
    return f"{BASE_URL}/en/jobs/{quote(keyword, safe='')}/?p={page}&SortIndex=1"


def relative_date_to_iso(text: str, now: datetime | None = None) -> str:
    """A card's relative date as an ISO day.

    A non-empty but unparseable marker ("just now", "New") becomes today:
    with newest-first ordering an empty date would bury the freshest jobs.
    """
    if not text:
        return ""
    reference = now or datetime.now(timezone.utc)
    match = _RELATIVE_DATE_RE.search(text)
    if not match:
        return reference.date().isoformat()
    unit = _RELATIVE_DATE_UNITS[match.group(2).lower()]
    return (reference - timedelta(**{unit: int(match.group(1))})).date().isoformat()


class _Cards(TagStackParser):
    """`li.job-list-item` cards, one dict each, in document order.

    Reproduces the retiring producer's selectors: `a.job-link-detail` for the
    title, the spans of `p.job-attributes` for company and location,
    `div.job-tags span.tag` for the badges and `p.job-date` for the date. Text
    is joined the way BeautifulSoup's `get_text(strip=True)` joins it.
    """

    def __init__(self):
        super().__init__()
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._card_depth = 0
        self._field: tuple[str, int] | None = None
        self._chunks: list[str] = []

    def on_open(self, tag, classes, attrs, depth):
        if self._card is None:
            if tag == "li" and CARD_CLASS in classes:
                self._card = {
                    "id": attrs.get("data-job-id", ""),
                    "title": "",
                    "company": "",
                    "location": "",
                    "detail_path": attrs.get("data-job-detail-url", ""),
                    "badges": [],
                    "date": "",
                }
                self._card_depth = depth
            return
        if self._field is None:
            field = self._field_for(tag, classes)
            if field is not None:
                self._field = (field, depth)
                self._chunks = []

    def on_close(self, tag, depth):
        if self._field is not None and self._field[1] >= depth:
            self._store(self._field[0], "".join(self._chunks))
            self._field = None
        if self._card is not None and self._card_depth >= depth:
            self.cards.append(self._card)
            self._card = None

    def on_text(self, data):
        if self._field is not None and data.strip():
            self._chunks.append(data.strip())

    # -- selectors -------------------------------------------------------
    def _field_for(self, tag: str, classes: list[str]) -> str | None:
        if tag == "a" and "job-link-detail" in classes:
            return "title"
        if tag == "p" and "job-date" in classes:
            return "date"
        if tag == "span":
            if self.has_ancestor_class("job-attributes"):
                return "attribute"
            if "tag" in classes and self.has_ancestor_class("job-tags"):
                return "badge"
        return None

    def _store(self, field: str, value: str) -> None:
        card = self._card
        if card is None:
            return
        if field == "title":
            card["title"] = card["title"] or value
        elif field == "date":
            card["date"] = card["date"] or value
        elif field == "badge":
            card["badges"].append(value)
        elif not card["company"]:
            card["company"] = value
        elif not card["location"]:
            card["location"] = value


def _cards(html: str, now: datetime | None = None) -> list[dict]:
    """Parsed cards, or a failure when an empty page looks like a block."""
    parser = _Cards()
    parser.feed(html)
    parser.close()
    cards = []
    for card in parser.cards:
        if not card["id"] or not card["title"]:
            continue
        card["badges"] = [
            badge for badge in card["badges"] if badge and badge != TOP_LISTING_TAG
        ]
        card["date"] = relative_date_to_iso(card["date"], now)
        cards.append(card)
    # Only an empty parse can be a block: a page that produced jobs may
    # legitimately mention "captcha" in its own content.
    if not cards and looks_like_soft_block(html):
        # `SoftBlocked`, no `ValueError`: `_read` reintentaba cuatro veces
        # contra una página que nos pide parar, y al agotarse perdía el motivo
        # (HTTP 200 ⇒ ningún bloqueo contabilizado) — DT-148 A8.
        raise SoftBlocked("JobScout24 returned a soft-block/CAPTCHA page")
    return cards


def _description(html: str) -> str:
    return job_posting_description(html, max_length=MAX_DESCRIPTION_LENGTH)


def _workload(raw: dict) -> str:
    badges = raw.get("badges")
    if not isinstance(badges, list):
        return ""
    return next(
        (
            badge
            for badge in badges
            if isinstance(badge, str) and _WORKLOAD_TAG_RE.match(badge)
        ),
        "",
    )


def _badge_tags(raw: dict) -> list[str]:
    workload = _workload(raw)
    badges = raw.get("badges") if isinstance(raw.get("badges"), list) else []
    return [
        badge
        for badge in badges
        if isinstance(badge, str) and badge and badge != workload
    ]


def _tags(raw: dict) -> list[str]:
    """Badges plus the technologies the title and description mention.

    Cards carry no skill list, so the producer enriches the badges with
    word-boundary matches over title + description.
    """
    extracted = extract_tech_skills(
        _text(raw.get("title")),
        _text(raw.get("_core_description")),
        word_boundaries=True,
    )
    return sorted(set(_badge_tags(raw) + extracted))


def _location(value) -> str:
    """The card's location, or Switzerland when the card carries none.

    A present-but-blank component folds to Worldwide, exactly as the producer's
    shared location helper does.
    """
    text = value if isinstance(value, str) else ""
    if not text:
        return "Switzerland"
    return normalize_location(text) or "Worldwide"


def _remote(raw: dict) -> bool:
    lowered = [badge.lower() for badge in _badge_tags(raw)]
    return any(
        "home office" in badge or "homeoffice" in badge or "remote" in badge
        for badge in lowered
    )


def _url(raw: dict) -> str:
    path = raw.get("detail_path")
    return f"{BASE_URL}{path}" if isinstance(path, str) and path else ""


def _content(raw: dict) -> dict:
    return {
        "title": _text(raw.get("title")),
        "company": _text(raw.get("company")),
        "description": _text(raw.get("_core_description")),
        "location": _location(raw.get("location")),
        "tags": _tags(raw),
        "remote": _remote(raw),
        "salary": None,
        "employment_type": _workload(raw) or None,
    }


def register_handlers() -> None:
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(
        SOURCE_NAME,
        lambda raw: (_text(raw.get("title")), _text(raw.get("company"))),
    )


async def _pause() -> None:
    await asyncio.sleep(random.uniform(REQUEST_PAUSE_S, REQUEST_PAUSE_S * 2))


async def _read(http, url: str, byte_limit: int, parser=None):
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
                    if size > byte_limit:
                        raise ProviderResponseError(
                            f"{SOURCE_NAME}: response exceeds byte budget"
                        )
                    chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8")
            return parser(text) if parser else text
        except ProviderResponseError:
            raise
        except (httpx.HTTPError, UnicodeError, ValueError) as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code in RETRY_STATUSES
            )
            if not retryable or attempt == MAX_RETRIES:
                raise ProviderResponseError(
                    f"{SOURCE_NAME}: request failed ({type(exc).__name__})"
                ) from None
            await asyncio.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


def _cached_descriptions(cursor) -> dict[str, str]:
    if cursor is None:
        return {}
    if not isinstance(cursor, dict):
        # DT-148 A18: el cursor es CACHÉ que escribe este mismo código, no
        # configuración del scope. Tratarlo como `ProviderConfigError` dejaba el
        # scope fallando en CADA ventana sin contabilizar el fallo (el runner
        # relanza ese error antes de registrarlo), el dispatcher lo reencolaba
        # siempre y nadie podía reiniciarlo sin tocar un parámetro semántico.
        # Un cursor ilegible se DESCARTA con un grito y se parte de cero.
        logger.error("%s: cursor ilegible, se descarta y se parte de cero", SOURCE_NAME)
        return {}
    values = cursor.get("descriptions", {})
    if not isinstance(values, dict) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        or not value
        or len(value) > MAX_DESCRIPTION_LENGTH
        for key, value in values.items()
    ):
        logger.error(
            "%s: caché de descripciones ilegible, se descarta y se vuelve a "
            "pedir el detalle",
            SOURCE_NAME,
        )
        return {}
    return values


async def _fill_descriptions(http, retained, cached):
    """Completa las descripciones que falten, dentro del tope de detalles.

    Devuelve `(descriptions, attempts, fetched, soft_block)`. Vive fuera de
    `fetch_new` porque es otra responsabilidad —el barrido del listado ya
    terminó— y porque dejarla dentro llevó la función a grado E de radon al
    añadirle el corte por desafío (DT-148 A8).
    """
    retained_ids = {card["id"] for card in retained}
    descriptions = {key: value for key, value in cached.items() if key in retained_ids}
    attempts = fetched = 0
    soft_block = None
    for card in retained:
        identity = card["id"]
        url = _url(card)
        if identity in descriptions or not url or attempts >= MAX_DETAIL_FETCHES:
            continue
        attempts += 1
        await _pause()
        try:
            description = await _read(http, url, MAX_DETAIL_BYTES, _description)
        except SoftBlocked:
            # El portal nos está diciendo que paremos: se deja de pedir
            # detalles AHORA (antes se seguía hasta los 20 del tope) y el
            # bloqueo viaja al runner (DT-148 A8).
            logger.error(
                "%s: desafío anti-bot en el detalle de %r — se detiene el "
                "pase de detalles",
                SOURCE_NAME,
                identity,
            )
            soft_block = SOFT_BLOCK_STATUS
            break
        except (httpx.HTTPError, ProviderResponseError):
            logger.warning("%s: detail fetch failed for %r", SOURCE_NAME, identity)
            continue
        if description:
            descriptions[identity] = description
            fetched += 1
    return descriptions, attempts, fetched, soft_block


class JobScout24Provider(BaseProvider):
    name = SOURCE_NAME

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or params:
            raise ProviderConfigError("JobScout24 scope has no query parameters")
        cached = _cached_descriptions(cursor)
        rows, seen, invalid = [], set(), 0
        pages = 0

        for keyword in KEYWORDS:
            for page in range(1, MAX_PAGES_PER_KEYWORD + 1):
                if pages:
                    await _pause()
                pages += 1
                cards = await _read(
                    http, _listing_url(keyword, page), MAX_RESPONSE_BYTES, _cards
                )
                if not cards:
                    if page == 1:
                        logger.warning(
                            "%s: keyword %r returned no cards (check the "
                            "li.job-list-item selectors)",
                            SOURCE_NAME,
                            keyword,
                        )
                    break
                for card in cards:
                    if card["id"] in seen:
                        continue
                    seen.add(card["id"])
                    if not _url(card):
                        # The producer cached such a card under an empty URL.
                        # Here the URL is listing identity: two of them would
                        # normalize to the same empty value and the sink would
                        # merge two different jobs into one vacancy.
                        invalid += 1
                        continue
                    rows.append(card)

        if not rows:
            # The producer kept its cache when a sweep processed nothing
            # ("No jobs processed from jobscout24"). Five IT searches on a
            # generalist board returning nothing is a redesign or a block, so
            # the run must fail instead of silently emptying the corpus.
            raise ProviderResponseError(f"{SOURCE_NAME}: sweep produced no cards")
        if invalid:
            logger.warning("%s: %d cards without a detail URL", SOURCE_NAME, invalid)

        retained = rows[:MAX_RECENT_JOBS]
        descriptions, attempts, fetched, soft_block = await _fill_descriptions(
            http, retained, cached
        )

        # The producer sorts the served cache by date, descending, with a stable
        # sort — so equal dates keep sweep order. That final order is what
        # `_core_order` carries.
        recent_order = {
            card["id"]: index
            for index, card in enumerate(
                sorted(retained, key=lambda card: card["date"], reverse=True)
            )
        }
        next_order = len(recent_order)
        listings = []
        for card in rows:
            identity = card["id"]
            payload = {**card, "_core_description": descriptions.get(identity, "")}
            order = recent_order.get(identity)
            if order is None:
                order, next_order = next_order, next_order + 1
            payload["_core_order"] = order
            payload["_core_statistics_tags"] = [tag.lower() for tag in _tags(payload)]
            listings.append(
                RawListing(external_id=identity, url=_url(card), payload=payload)
            )
        return FetchResult(
            tuple(listings),
            {
                "items_seen": len(seen),
                "recent_items": len(retained),
                "details_cached": len(descriptions),
                "detail_attempts": attempts,
                "details_fetched": fetched,
                "descriptions": descriptions,
            },
            pages_fetched=pages,
            complete=not invalid and soft_block is None,
            error=("invalid_jobscout24_cards" if invalid else None)
            or ("soft_block" if soft_block is not None else None),
            blocked_with=soft_block,
        )
