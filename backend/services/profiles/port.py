"""Port for the editable profile owned by SwissJob.

The API reads and writes the local profile. Core receives durable snapshots
through profile_sync and owns the capabilities transferred separately.
"""

from typing import Protocol

from models.user import User


class ProfileError(Exception):
    """Base de errores de la capacidad perfiles."""


class CoreUnavailableError(ProfileError):
    """El core no responde, fallo, o falta credencial/vinculo de identidad."""


class ProfileUnsupportedError(ProfileError):
    """La operacion no puede servirla el core con el contrato vigente.

    Hoy SIN emisor: el /v1 sirve la lectura completa de {title, cv_text,
    skills} y el resto es overlay del escritor local. Se conserva por
    simetria con catalogo/matching y para la separacion de severidades del
    canary (seam.FallbackProfile)."""


class ProfilePort(Protocol):
    """Operacion de LECTURA del perfil de un usuario.

    Devuelve un objeto con los atributos de `UserProfile` que consume el
    router (validable como ProfileResponse, incluido `cv_embedding` para
    calcular has_cv_embedding), o None si el usuario no tiene perfil local.
    """

    async def get(self, user: User):
        """Perfil del usuario autenticado, o None (el router lo hace 404)."""
        ...
