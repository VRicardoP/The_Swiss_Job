"""The `__NEXT_DATA__` payload of a Next.js page, read defensively.

Two portals embed their offers in it (financejobs and tes), and both were fixed
for the same defect: an unreadable payload used to come out as "0 offers" and
the broken source reported itself as a dry one. Every failure here is VISIBLE.
"""

import json
import re

from jobhunt_core.harvest.provider import ProviderResponseError

_NEXT_DATA = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def next_data(html: str, source: str) -> dict:
    """The parsed `__NEXT_DATA__` object, or a failure that says which step broke."""
    match = _NEXT_DATA.search(html)
    if match is None:
        raise ProviderResponseError(f"{source}: no __NEXT_DATA__")
    try:
        data = json.loads(match.group(1))
    except (ValueError, RecursionError) as exc:
        # ValueError, not JSONDecodeError: a number of more than 4300 digits
        # raises the base class.
        raise ProviderResponseError(f"{source}: unreadable __NEXT_DATA__") from exc
    if not isinstance(data, dict):
        raise ProviderResponseError(f"{source}: __NEXT_DATA__ is not an object")
    return data
