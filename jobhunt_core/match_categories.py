"""Authoritative match taxonomy shared by Core facets and category pages."""

from __future__ import annotations

import json
import re
from importlib.resources import files


_CATEGORIES = json.loads(
    files("jobhunt_core").joinpath("match_categories.json").read_text(encoding="utf-8")
)
_PATTERNS = {
    category["id"]: tuple(
        re.compile(keyword) if ".*" in keyword else keyword
        for keyword in category["keywords"]
    )
    for category in _CATEGORIES
}
CATEGORY_IDS = frozenset(category["id"] for category in _CATEGORIES) | {"otros"}


def categories() -> list[dict]:
    """Public category metadata; matching keywords remain an internal rule."""
    return [
        {
            "id": category["id"],
            "label": category["label"],
            "short_label": category["shortLabel"],
        }
        for category in _CATEGORIES
    ]


def classify(title: str | None, tags: list | None) -> str:
    """Port of ``frontend/src/utils/jobCategories.js::classifyMatch``."""
    text = " ".join(
        [title or "", *(tag for tag in (tags or []) if isinstance(tag, str))]
    ).lower()
    for category in _CATEGORIES:
        for pattern in _PATTERNS[category["id"]]:
            matches = (
                pattern in text
                if isinstance(pattern, str)
                else pattern.search(text) is not None
            )
            if matches:
                return category["id"]
    return "otros"
