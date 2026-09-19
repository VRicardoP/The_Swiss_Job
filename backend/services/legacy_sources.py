"""Validate startup handover lists before constructing any legacy producer.

This does not cancel tasks already running on an old image. Cutover must first
drain/restart legacy workers with these settings, then activate native scopes.
"""

from collections.abc import Collection


def disabled_sources(configured: list[str], registered: Collection[str]) -> set[str]:
    disabled = set(configured)
    if disabled - set(registered):
        # Do not expose arbitrary environment contents in logs.
        raise ValueError(
            "Unknown disabled legacy sources; check handover configuration"
        )
    return disabled
