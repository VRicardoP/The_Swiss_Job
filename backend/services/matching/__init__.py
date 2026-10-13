"""Core matching authority and profile identity link."""

from .core_client import CoreMatching, clear_feed_cache
from .identity import resolve_core_profile_id, set_profile_link
from .port import (
    CoreUnavailableError,
    MatchingError,
    MatchingPort,
    MatchingUnsupportedError,
)
from .seam import resolve_matching

__all__ = [
    "CoreMatching",
    "CoreUnavailableError",
    "MatchingError",
    "MatchingPort",
    "MatchingUnsupportedError",
    "clear_feed_cache",
    "resolve_core_profile_id",
    "resolve_matching",
    "set_profile_link",
]
