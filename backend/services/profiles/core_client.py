"""Implementacion CORE de la capacidad perfiles — A.SEAM (plan §15bis).

Cliente de GET /v1/profiles/{id} del core. Contrato REAL: jobhunt_core/api/
v1.py + api/schemas.py — ProfileDTO {id, external_ref, created_at,
current_revision:{content, content_hash, text_hash}}; ETag de representacion
con If-None-Match -> 304; auth Bearer key_id.secret, scope profiles:read;
ownership por consumer con 404 indistinguible de ausente.

DECISIONES (documentadas; las fija test_profiles_contract.py):

IDENTIDAD DE PERFIL. El /v1 solo busca perfiles por SU UUID. El consumer del
BFF ES `swissjob-shadow` (DECISION DELEGADA sobre CONTRATOS §3: es el
consumer de SwissJob — el mismo bajo el que la sombra proyecta
external_ref=str(users.id); el flip de Fase C lo consolida), de modo que los
perfiles proyectados SON de nuestro tenant y visibles con CORE_CONSUMER_KEY.
Como el /v1 no expone lookup por external_ref, el vinculo usuario legacy ->
perfil core se resuelve en la tabla LOCAL `jobhunt_profile_map`
(services/matching/identity.py — el vinculo es POR USUARIO, no por
capacidad: la misma fila sirve a matching y a perfiles). La puebla el
operador al enrolar el canary. Sin vinculo o sin CORE_CONSUMER_KEY:
CoreUnavailableError SIN emitir peticiones (como catalogo).

MAPEO DTO HONESTO. El core solo tiene del perfil legacy lo que la sombra
proyecta: content = {title, cv_text, skills} EXACTO (PF.5, proyector B-02).
Esos TRES campos se sirven del CORE (es la lectura que el canary valida).
TODO lo demas (experience_years, languages, locations, salary_min/max,
remote_pref, score_weights, watchlist_schools_enabled, cv_embedding,
updated_at e ids del contrato legacy) es estado del ESCRITOR LOCAL que el
/v1 no expone: se sirve de la fila local (criterio unificador — docstring de
services/profiles/seam.py: ningun estado local puede ser inaccesible).
Cota registrada: esos campos entraran al core cuando el flip de escritor de
Fase C amplie el contenido del perfil; mientras, NO se inventan.

RESPALDO LOCAL OBLIGATORIO. Sin fila `user_profiles` no hay overlay ni nada
editable (el PUT local devolveria 404): se responde None (404 legacy) SIN
tocar la red — un perfil visible solo-core seria no-accionable.

REVISION VIGENTE OBLIGATORIA. `current_revision` null o sin alguna de las
tres claves proyectadas = la proyeccion sombra no aterrizo o es anomala
(PF.5 garantiza las claves): servir huecos ocultaria estado local =>
CoreUnavailableError (senal WARNING accionable del canary, no un perfil
vacio en silencio).

FRESCURA. El escritor sigue siendo local: tras un PUT, el core sirve
{title, cv_text, skills} proyectados con el lag del CDC. Ese lag es la
METRICA del canary (GATE-SOMBRA), no un defecto a ocultar; el recibo
inmediato del escritor es la respuesta del propio PUT (local, fuera de la
costura). ETag por perfil: en el refresco tipico el core responde 304 sin
cuerpo y se reutiliza la representacion cacheada.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.user import User
from models.user_profile import UserProfile
from services.matching.identity import resolve_core_profile_id

from .port import CoreUnavailableError

logger = logging.getLogger(__name__)

# Claves del content proyectado por la sombra (PF.5; proyector B-02).
PROJECTED_FIELDS = ("title", "cv_text", "skills")

# Errores de FORMA de un 200 del core (JSON ilegible, esquema incompatible,
# tipos inesperados): se traducen a CoreUnavailableError — payload invalido =
# tan inutilizable como el core caido => fallback REAL en core_read
# (hallazgo P2 rev. externa A.SEAM; mismo criterio en catalogo y matching).
_PAYLOAD_ERRORS = (ValueError, KeyError, TypeError, AttributeError, IndexError)

# Cache de representaciones por ETag: clave str(core_profile_id) ->
# (etag, body). En proceso y acotada (mismo espiritu que la cache del feed
# de matching): en el refresco tipico el core responde 304 sin cuerpo.
_ETAG_CACHE_MAX = 512
_etag_cache: dict[str, tuple[str, dict]] = {}


def clear_profile_cache() -> None:
    """Vacia la cache de representaciones del perfil (tests / operacion)."""
    _etag_cache.clear()


def default_client_factory() -> httpx.AsyncClient:
    """Cliente httpx contra el /v1 del core por la red interna de compose
    (plan §21: puerto dedicado, solo red interna, nunca ngrok)."""
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


@dataclass
class CoreProfileView:
    """Vista duck-type de models.user_profile.UserProfile para el router.

    {title, cv_text, skills} vienen del CORE (revision vigente proyectada);
    el resto del ESCRITOR LOCAL (mapeo honesto — docstring del modulo)."""

    id: uuid.UUID
    user_id: uuid.UUID
    title: str | None
    skills: list
    cv_text: str | None
    experience_years: int | None
    languages: list
    locations: list
    salary_min: int | None
    salary_max: int | None
    remote_pref: object
    score_weights: dict | None
    watchlist_schools_enabled: bool
    cv_embedding: object
    updated_at: datetime


def _view(local: UserProfile, content: dict) -> CoreProfileView:
    return CoreProfileView(
        # Ids del CONTRATO LEGACY (ProfileResponse.id/user_id): los locales.
        # El UUID del perfil core es interno a la costura y no se filtra.
        id=local.id,
        user_id=local.user_id,
        title=content["title"],
        skills=content["skills"] or [],
        cv_text=content["cv_text"],
        experience_years=local.experience_years,
        languages=local.languages,
        locations=local.locations,
        salary_min=local.salary_min,
        salary_max=local.salary_max,
        remote_pref=local.remote_pref,
        score_weights=local.score_weights,
        watchlist_schools_enabled=local.watchlist_schools_enabled,
        # El embedding 384-d es un artefacto del motor LOCAL (el core tiene
        # los suyos bajo su propia receta y el /v1 no los expone): local.
        cv_embedding=local.cv_embedding,
        updated_at=local.updated_at,
    )


class CoreProfile:
    """Cliente de GET /v1/profiles/{id} detras del puerto ProfilePort."""

    def __init__(
        self,
        db: AsyncSession,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        # La sesion sirve para el vinculo de identidad y el overlay local;
        # factory inyectable para tests (MockTransport), como en matching.
        self._db = db
        self._client_factory = client_factory or default_client_factory

    async def get(self, user: User) -> CoreProfileView | None:
        # RESPALDO LOCAL OBLIGATORIO (docstring): sin fila local, 404 legacy
        # y CERO peticiones — no hay nada accionable que servir.
        await self._db.refresh(user, ["profile"])
        local = user.profile
        if local is None:
            return None

        core_profile_id = await resolve_core_profile_id(self._db, user.id)
        if core_profile_id is None:
            # Enrutado a core sin enrolar el vinculo: error de operacion =>
            # misma senal que el core caido, y CERO peticiones de red.
            raise CoreUnavailableError(
                f"usuario {user.id} sin vinculo en jobhunt_profile_map"
            )
        if (
            self._client_factory is default_client_factory
            and not settings.CORE_CONSUMER_KEY
        ):
            # Sin credencial no se hace ni una peticion (mismo trato que caida).
            raise CoreUnavailableError("CORE_CONSUMER_KEY no configurada")

        body = await self._fetch_profile(core_profile_id)
        try:
            revision = body.get("current_revision")
            incomplete = revision is None or any(
                key not in (revision.get("content") or {}) for key in PROJECTED_FIELDS
            )
        except _PAYLOAD_ERRORS as exc:
            # 200 con forma incompatible (revision no-dict, content no
            # indexable...): payload invalido => misma senal que caida.
            raise CoreUnavailableError(
                f"payload invalido del core para el perfil {core_profile_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if incomplete:
            # REVISION VIGENTE OBLIGATORIA (docstring): proyeccion ausente o
            # anomala — señal accionable, nunca un perfil con huecos.
            raise CoreUnavailableError(
                f"perfil core {core_profile_id} sin revision vigente completa "
                f"(proyeccion sombra no aterrizada o anomala)"
            )
        try:
            return _view(local, revision["content"])
        except _PAYLOAD_ERRORS as exc:
            raise CoreUnavailableError(
                f"payload invalido del core para el perfil {core_profile_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    async def _fetch_profile(self, core_profile_id: uuid.UUID) -> dict:
        cache_key = str(core_profile_id)
        cached = _etag_cache.get(cache_key)
        headers = {"If-None-Match": cached[0]} if cached else {}
        try:
            async with self._client_factory() as client:
                resp = await client.get(f"/profiles/{core_profile_id}", headers=headers)
        except httpx.HTTPError as exc:
            raise CoreUnavailableError(f"core /v1 inaccesible: {exc}") from exc
        if resp.status_code == 304:
            if cached is None:  # defensivo: 304 sin haber mandado If-None-Match
                raise CoreUnavailableError("core /v1 devolvio 304 sin cache previa")
            return cached[1]
        if resp.status_code == 404:
            # Perfil desconocido para ESTE consumer (vinculo obsoleto o
            # credencial de otro tenant): configuracion, no datos.
            raise CoreUnavailableError(
                f"perfil core {core_profile_id} inexistente para este consumer"
            )
        if resp.status_code != 200:
            raise CoreUnavailableError(
                f"core /v1 devolvio {resp.status_code} para el perfil {core_profile_id}"
            )
        try:
            body = resp.json()
        except ValueError as exc:  # JSON ilegible en un 200
            raise CoreUnavailableError(
                f"JSON invalido del core para el perfil {core_profile_id}: {exc}"
            ) from exc
        if not isinstance(body, dict):
            # ProfileDTO es un objeto: cualquier otra forma es incompatible.
            raise CoreUnavailableError(
                f"payload no-objeto del core para el perfil {core_profile_id}"
            )
        etag = resp.headers.get("etag")
        if etag:
            if len(_etag_cache) >= _ETAG_CACHE_MAX:
                _etag_cache.clear()  # acotado y simple; se rellena con 200s
            _etag_cache[cache_key] = (etag, body)
        return body
