"""Capacidad PERFILES detras de costura — A.SEAM (plan §15bis).

API publica del paquete: el puerto, sus errores y el resolver que decide
implementacion (local|core) via jobhunt_routing. El vinculo de identidad
usuario legacy -> perfil core se comparte con matching
(services/matching/identity.py: es POR USUARIO, no por capacidad).
"""

from .core_client import CoreProfile, clear_profile_cache
from .local import LocalProfile
from .port import (
    CoreUnavailableError,
    ProfileError,
    ProfilePort,
    ProfileUnsupportedError,
)
from .seam import FallbackProfile, resolve_profiles

__all__ = [
    "CoreProfile",
    "CoreUnavailableError",
    "FallbackProfile",
    "LocalProfile",
    "ProfileError",
    "ProfilePort",
    "ProfileUnsupportedError",
    "clear_profile_cache",
    "resolve_profiles",
]
