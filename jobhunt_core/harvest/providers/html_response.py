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
"""

import json
import re
from json import JSONDecodeError

from jobhunt_core.harvest.providers.rss_text import strip_html_tags

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

    Raises ValueError on a soft block so the caller's retry/failure path sees a
    real failure instead of an empty description.
    """
    if looks_like_soft_block(html):
        raise ValueError("detail page is a soft block")
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
