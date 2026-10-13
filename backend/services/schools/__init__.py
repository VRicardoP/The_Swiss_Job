"""Core school authority."""

from .core_client import CoreSchools
from .port import (
    CoreUnavailableError,
    SchoolsError,
    SchoolsPort,
    SchoolsUnsupportedError,
)
from .seam import resolve_schools

__all__ = [
    "CoreSchools",
    "CoreUnavailableError",
    "SchoolsError",
    "SchoolsPort",
    "SchoolsUnsupportedError",
    "resolve_schools",
]
