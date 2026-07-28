"""Puerto de la capacidad PERFILES — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). La operacion es la
LECTURA del perfil que hoy consume el router (`routers/profile.py` GET
/api/v1/profile): `get` devuelve un objeto validable como `ProfileResponse`
o None si el usuario no tiene fila local (el router traduce None a 404).

ESCRITURAS FUERA DEL PUERTO — cota REGISTRADA, no implementada:
PUT /profile (preferencias y score_weights), POST/DELETE /profile/cv y el
autofill del CV (tasks/profile_tasks.analyze_cv_and_autofill) quedan SIEMPRE
en local en esta etapa. Matriz de escritor del plan §15bis: el legacy es el
escritor autoritativo de `user_profiles` hasta el cutover; la sombra (CDC ->
proyector B-02) replica {title, cv_text, skills} al core como revisiones de
perfil. El cambio de escritor llega en Fase C como escritura sincrona contra
el escritor activo + idempotency key. Consecuencia LEIDA en esta etapa: los
campos que el core NO tiene se sirven del escritor local (overlay en
CoreProfile — criterio unificador: ningun estado local puede ser
inaccesible), y la respuesta del PUT es el recibo del escritor local, nunca
pasa por la costura.

GDPR (export / delete-all) TAMPOCO pasa por la costura: exporta y borra lo
que ESTE sistema almacena — el almacen del escritor local.

Dos implementaciones detras del mismo puerto:
- `LocalProfile` (services/profiles/local.py): lectura actual, sin cambios.
- `CoreProfile` (services/profiles/core_client.py): cliente de
  GET /v1/profiles/{id} del core (ETag) + overlay local.
La eleccion la decide `jobhunt_routing` (services/profiles/seam.py).
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
