"""Defensive reading of a portal's HTML response.

Two adapters scrape server-rendered pages, and both need the same two things
before they can believe a 200: that the body is not a bot challenge, and that
the JSON-LD `JobPosting` it carries is the description. Both behaviours are
copied from the producers being retired (`services/job_service.py` and
`services/job_detail.py` in ReactPortfolio), so the handover keeps detecting
exactly what they detected.

The soft-block list is the producer's COMPLETE one: the earlier DynamiteJobs
port had dropped `cf-chl-bypass` and the standalone-captcha rule, which would
have let a challenge page through as an empty description.

`TagStackParser` is the shared element stack those scrapes need. Portal markup
leaves `<p>` and `<li>` unclosed and emits stray end tags; a parser that
assumed balanced tags cut one myScience description seven paragraphs short.
"""

import json
import re
from html.parser import HTMLParser
from json import JSONDecodeError

from jobhunt_core.harvest.providers.rss_text import strip_html_tags
from jobhunt_core.harvest.provider import SoftBlocked

# Strong markers of a soft block: HTTP 200 carrying a CAPTCHA/challenge page
# instead of data. Deliberately conservative — a false positive turns a healthy
# source into a failing one.
SOFT_BLOCK_MARKERS = (
    "are you a human",
    "are you a robot",
    "verify you are human",
    "unusual traffic",
    "access denied",
    "request unsuccessful",
    "/cdn-cgi/challenge",
    "cf-chl-bypass",
    "attention required! | cloudflare",
)
# "captcha" only as a standalone word: a bare substring also matches the
# embedded reCAPTCHA/hCaptcha widgets ("recaptcha", "/recaptcha/api.js") that
# legitimate application forms carry.
_STANDALONE_CAPTCHA_RE = re.compile(r"(?<![a-z0-9/-])captcha")
# Challenge pages front-load the notice; scanning the whole body only adds
# false positives from page content.
SOFT_BLOCK_SCAN_CHARS = 5000

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_JSON_LD_TYPE_RE = re.compile(r"\btype\s*=\s*(['\"])application/ld\+json\1", re.I)


def looks_like_soft_block(text: str | None) -> bool:
    """True when the body reads as a bot challenge rather than a page."""
    if not text:
        return False
    lowered = text[:SOFT_BLOCK_SCAN_CHARS].lower()
    if any(marker in lowered for marker in SOFT_BLOCK_MARKERS):
        return True
    return bool(_STANDALONE_CAPTCHA_RE.search(lowered))


def job_posting_description(html: str, *, max_length: int) -> str:
    """Plain-text description of the page's JSON-LD `JobPosting`.

    Raises `SoftBlocked` on a soft block: un desafío no se reintenta (era un
    `ValueError`, que `_read` clasificaba como reintentable) y cuenta como
    bloqueo del portal (DT-148 A8).
    """
    if looks_like_soft_block(html):
        raise SoftBlocked("detail page is a soft block")
    queue = []
    for attributes, body in _SCRIPT_RE.findall(html):
        if not _JSON_LD_TYPE_RE.search(attributes):
            continue
        try:
            value = json.loads(body)
        except JSONDecodeError:
            continue
        queue.extend(value if isinstance(value, list) else [value])
    while queue:
        value = queue.pop(0)
        if not isinstance(value, dict):
            continue
        graph = value.get("@graph")
        if isinstance(graph, list):
            queue.extend(graph)
        kind = value.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            description = value.get("description")
            return (
                strip_html_tags(description)[:max_length]
                if isinstance(description, str)
                else ""
            )
    return ""


# Text inside these never belongs to the page's content, and BeautifulSoup's
# `get_text()` leaves it out too.
NON_CONTENT_TAGS = frozenset({"script", "style", "template", "noscript"})
# Elements that never have an end tag.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class TagStackParser(HTMLParser):
    """An element stack that survives real portal markup.

    Subclasses override `on_open`, `on_close` and `on_text`. Depths are 1-based
    positions in the stack, so a subclass can remember "the region that started
    at depth 4" and be told exactly when it ends.

    An end tag closes the nearest matching open element and everything left
    open inside it (what a browser and lxml both do with an unclosed `<p>`); an
    end tag with nothing to close is noise and is dropped.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._open: list[tuple[str, list[str], dict]] = []
        self._suppressed = 0

    # -- subclass hooks --------------------------------------------------
    def on_open(self, tag: str, classes: list[str], attrs: dict, depth: int) -> None:
        """An element opened; it is already on the stack at `depth`."""

    def on_close(self, tag: str, depth: int) -> None:
        """The element at `depth` closed; called innermost first."""

    def on_text(self, data: str) -> None:
        """Content text, never from a script or a stylesheet."""

    # -- parsing ---------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        mapping = dict(attrs)
        classes = (mapping.get("class") or "").split()
        if tag in VOID_TAGS:
            self.on_void(tag, classes, mapping)
            return
        if tag in NON_CONTENT_TAGS:
            self._suppressed += 1
        self._open.append((tag, classes, mapping))
        self.on_open(tag, classes, mapping, len(self._open))

    def handle_startendtag(self, tag, attrs):
        mapping = dict(attrs)
        self.on_void(tag, (mapping.get("class") or "").split(), mapping)

    def on_void(self, tag: str, classes: list[str], attrs: dict) -> None:
        """A void or self-closed element; it never enters the stack."""

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] != tag:
                continue
            while len(self._open) > index:
                closing, _classes, _attrs = self._open.pop()
                if closing in NON_CONTENT_TAGS:
                    self._suppressed = max(0, self._suppressed - 1)
                self.on_close(closing, len(self._open) + 1)
            return
        # Stray end tag: nothing it could close.

    def handle_data(self, data):
        if not self._suppressed:
            self.on_text(data)

    def close(self):
        super().close()
        while self._open:
            closing, _classes, _attrs = self._open.pop()
            if closing in NON_CONTENT_TAGS:
                self._suppressed = max(0, self._suppressed - 1)
            self.on_close(closing, len(self._open) + 1)

    # -- helpers ---------------------------------------------------------
    def has_ancestor_class(self, class_name: str) -> bool:
        return any(class_name in classes for _tag, classes, _attrs in self._open)
