"""Capacidad MATCHING detras de costura — A.SEAM (plan §15bis).

API publica del paquete: el puerto, sus errores, el resolver que decide
implementacion (local|core) via jobhunt_routing y el vinculo de identidad
usuario legacy -> perfil core (jobhunt_profile_map).
"""

from .core_client import CoreMatching, clear_feed_cache
from .identity import resolve_core_profile_id, set_profile_link
from .local import LocalMatching
from .port import (
    CoreUnavailableError,
    MatchingError,
    MatchingPort,
    MatchingUnsupportedError,
)
from .seam import FallbackMatching, resolve_matching

__all__ = [
    "CoreMatching",
    "CoreUnavailableError",
    "FallbackMatching",
    "LocalMatching",
    "MatchingError",
    "MatchingPort",
    "MatchingUnsupportedError",
    "clear_feed_cache",
    "resolve_core_profile_id",
    "resolve_matching",
    "set_profile_link",
]
