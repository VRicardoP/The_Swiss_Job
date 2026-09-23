"""Implementacion CORE de la capacidad matching — A.SEAM (plan §15bis).

Cliente del feed /v1/profiles/{id}/matches del core. Contrato REAL:
jobhunt_core/api/v1.py + api/schemas.py (A-08/A-09) — aqui, a diferencia de
catalogo, el core SI sirve la capacidad completa de lectura:

- GET /v1/profiles/{pid}/matches?limit&cursor -> MatchesPageDTO
  {items[{vacancy, evaluation, state}], next_cursor}. Orden score_final DESC,
  keyset opaco; ETag de pagina (If-None-Match -> 304); solo vacantes ACTIVAS
  y sin dismissed; auth Bearer key_id.secret, scope matches:read; ownership
  por consumer con 404 indistinguible de ausente.

DECISIONES (documentadas; las fija test_matching_contract.py):

IDENTIDAD DE PERFIL. El /v1 solo busca perfiles por SU UUID; el vinculo
usuario legacy -> perfil core se resuelve en la tabla LOCAL
`jobhunt_profile_map` (racional en models/jobhunt_profile_map.py). Sin
vinculo o sin credencial: CoreUnavailableError SIN emitir peticiones.

IDENTIDAD DE VACANTE (leccion del MD5). Cada item del feed se presenta con
el `job_hash` LEGACY cuando la vacante tiene ALGUN listing de la sombra: el
proyector B-02 crea fuentes `legacy:<source>` con `external_id` = hash MD5
del job, y ese external_id viaja en el listing (primary o no — con orden de
ingestion core→legacy el attach por URL deja el listing legacy como
NO-primary; P2 rev. externa). La resolucion es DETERMINISTA: candidatos =
external_id del primary si es `legacy:*`, luego los de los demas listings
`legacy:*` ordenados por (source, external_id); se sirve el PRIMER candidato
con Job local de respaldo — asi las ESCRITURAS (feedback, status), que
siguen en local, operan sobre la misma identidad que el usuario esta viendo.
Si la vacante no tiene listing legacy (core-nativa), su identidad
presentable seria el UUID en FORMA CANONICA con guiones — round-trip
`str(UUID(x))` — nunca una forma que colisione con un MD5 de 32 hex (lo
fija `legacy_job_ref`); pero en esta etapa esos items NO llegan al feed
(ver EXCLUSION POR ACCIONABILIDAD).

OVERLAY DEL ESCRITOR LOCAL. Hasta Fase C el BFF es el escritor de
feedback/status/borradores y la sombra NO proyecta match_results: leer ese
estado del core seria leer a un no-escritor. El feed del core aporta orden,
scores y contenido; el estado local se superpone por job_hash y los items
con feedback negativo local se EXCLUYEN (misma semantica que el motor
local: "not for me" desaparece de inmediato aunque el core aun lo sirva).
Corolario: 'saved' es proyeccion PURA de ese estado local (feedback
positivo) => `saved` se sirve SIEMPRE del escritor local, tambien detras de
este cliente (criterio unificador, docstring de services/matching/seam.py:
ningun estado local puede ser inaccesible por el routing).

EXCLUSION POR ACCIONABILIDAD (criterio unificador: nada visible puede ser
no-accionable mientras el escritor sea local). Un item sin fila local en
`jobs` — core-nativo, o legacy cuyo Job ya no existe aqui — no admite
feedback del escritor local (404): se EXCLUYE del feed en esta etapa. Cota
registrada: esos items reapareceran en Fase C, cuando el flip de escritor
(escritura sincrona contra el escritor activo + idempotency key) los haga
accionables. Los huerfanos CON Job local pero sin fila MatchResult si se
sirven (defaults del modelo) y su feedback upserta la fila minima
(MatchResultService.submit_feedback).

PAGINACION/TOTAL. El contrato legacy exige `total` exacto y la exclusion por
feedback local puede tocar cualquier pagina => se recorre el feed COMPLETO
(paginas de MAX_PAGE_LIMIT, cota MAX_FEED_PAGES contra bucles de cursor) y
se pagina localmente. El cache de ETag por pagina hace barato el refresco
(304 sin cuerpo). Los scores conservan la escala del CORE (Fase A: coseno
en scores.similarity) — la equivalencia de escala NO la exige el contrato.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, FiniteFloat, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.job import Job
from models.match_result import NEGATIVE_FEEDBACK, MatchResult
from services.match_result_service import MatchResultService

from .identity import resolve_core_profile_id
from .port import CoreUnavailableError

logger = logging.getLogger(__name__)

# Cotas del recorrido del feed (contrato /v1: MAX_PAGE_LIMIT=100 por pagina).
FEED_PAGE_LIMIT = 100
MAX_FEED_PAGES = 100  # 10k items; por encima => cursor en bucle o feed anomalo

# Prefijo de las fuentes sombra del proyector B-02 (jobhunt_core/shadow).
_LEGACY_SOURCE_PREFIX = "legacy:"

# Errores de FORMA de un 200 del core (JSON ilegible, esquema incompatible,
# tipos inesperados): se traducen a CoreUnavailableError — payload invalido =
# tan inutilizable como el core caido => fallback REAL en core_read
# (hallazgo P2 rev. externa A.SEAM; mismo criterio en catalogo y perfiles).
_PAYLOAD_ERRORS = (ValueError, KeyError, TypeError, AttributeError, IndexError)

# Cache de paginas por ETag: clave (perfil core, cursor) -> (etag, body).
# En proceso y acotado (mismo espiritu que la cache de routing): en el 99%
# de refrescos el core responde 304 sin cuerpo y se reutiliza la pagina.
_ETAG_CACHE_MAX = 512
_etag_cache: dict[tuple[str, str], tuple[str, dict]] = {}


_cache_generation = 0
_profile_generations: dict[str, int] = {}

# Recorrido COMPLETO del feed por perfil, indexado por la VERSION que declara
# el core: {profile_id: (version, items, total)}.
#
# Por que existe. La pantalla principal pide 3.000 ofertas (MatchPage.jsx:40) y
# el feed trae 1.800, asi que el recorrido entero es obligatorio. Medido en el
# NAS, ese recorrido es el 87-89 % del coste: 47,5 s en frio y 11,5 s en
# caliente, en 18 paginas. Caliente sigue costando porque un `If-None-Match`
# NO ahorra trabajo: el ETag se deriva del payload, o sea que el core
# construye la pagina igual para contestar 304.
#
# Con la version, una carga sin cambios cuesta UNA consulta barata en vez de
# 18 paginas. Es correcto por construccion: si el feed servido cambia, la
# version cambia (jobhunt_core.matching.feed_version_sql), y aqui solo se
# reutiliza lo cacheado cuando la version coincide EXACTAMENTE.
#
# Solo se cachea el recorrido COMPLETO: uno cortado por `needed` no sirve para
# responder a otro llamante que pida mas. Y solo la parte INMUTABLE — lo que
# sirve el core. El estado local del usuario (feedback, candidatura, urgencia,
# borrador) se relee en CADA peticion, que es lo que hace que esta cache no
# pueda servir nada rancio de lo que el usuario acaba de tocar.
#
# INVARIANTE que sostiene todo esto: los items cacheados se leen y JAMAS se
# mutan. `_match_view`, `_job_view` y `legacy_job_refs` solo leen, y `results`
# construye dicts NUEVOS. Se devuelve una copia de la LISTA para que nadie la
# vacie desde fuera; los dicts se comparten a proposito, porque copiarlos en
# profundidad 1.800 veces por peticion costaria lo que esta cache ahorra. Si
# algun dia hace falta mutarlos, copia primero.
_FEED_CACHE_MAX = 32
_feed_cache: dict[str, tuple[str, list[dict], int]] = {}


def clear_feed_cache(profile_id=None) -> None:
    """Invalidate a deleted subject without evicting other users' pages."""
    global _cache_generation
    if profile_id is None:
        _cache_generation += 1
        _profile_generations.clear()
        _etag_cache.clear()
        _feed_cache.clear()
        return
    pid = str(profile_id)
    if pid not in _profile_generations and len(_profile_generations) >= _ETAG_CACHE_MAX:
        clear_feed_cache()  # bounded state; global epoch fences older in-flight requests
    _profile_generations[pid] = _profile_generations.get(pid, 0) + 1
    _feed_cache.pop(pid, None)
    for key in list(_etag_cache):
        if key[0] == pid:
            _etag_cache.pop(key, None)


# ------------------------------------------------------------- DTOs privados
# DTOs Pydantic PRIVADOS y ESTRECHOS del feed (P2 rev. externa): validan
# TIPOS ademas de presencia, SOLO en los campos que este cliente consume del
# MatchesPageDTO del /v1 — sin importar nada de jobhunt_core (frontera
# estricta, plan §21; el contrato se replica aqui como en los tests). Un 200
# bien formado en JSON pero con tipos rotos (next_cursor no-str,
# matching_skills/tags no-lista, scores no finitos) reventaba FUERA del
# fallback (TypeError del guard de cursores, o el response model del router):
# validado AQUI se traduce a CoreUnavailableError (fallback real).


class _ListingDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str | None = None
    external_id: str | None = None
    url: str | None = None
    first_seen_at: str | None = None


class _VacancyDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    company: str | None = None
    description: str | None = None
    tags: list | None = None
    location: str | None = None
    primary_listing: _ListingDTO | None = None
    listings: list[_ListingDTO] | None = None


class _EvaluationDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    eval_key: str
    # FiniteFloat: NaN/inf pasarian float() y romperian la serializacion
    # JSON de la respuesta del router fuera del fallback.
    score_final: FiniteFloat
    scores: dict[str, FiniteFloat] | None = None
    explanation: str | None = None
    matching_skills: list | None = None
    missing_skills: list | None = None


class _StateDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    feedback: str | None = None


class _FeedItemDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vacancy: _VacancyDTO
    evaluation: _EvaluationDTO
    state: _StateDTO | None = None


class _FeedPageDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[_FeedItemDTO] | None = None
    next_cursor: str | None = None
    # Aditivo: los cores anteriores no lo mandan y el consumidor vuelve a
    # contar recorriendo. Se valida como entero no negativo, no se confia.
    total: int | None = Field(default=None, ge=0)


def default_client_factory() -> httpx.AsyncClient:
    """Cliente httpx contra el /v1 del core por la red interna de compose
    (plan §21: puerto dedicado, solo red interna, nunca ngrok)."""
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


@dataclass
class CoreJobView:
    """Vista duck-type de models.job.Job con los campos que consume el
    mapeo del router (_to_match_response / traduccion de titulos)."""

    title: str
    company: str | None
    url: str
    location: str | None
    description_snippet: str | None
    tags: list = field(default_factory=list)
    source: str | None = None
    # Lo sirve el core desde la canonica (_language_of); si viene ausente
    # —o con una forma invalida— el router lo detecta por titulo.
    language: str | None = None
    category: str | None = None  # no expuesto por el /v1 (cota, como catalogo)
    salary_min_chf: int | None = None  # idem: el core sirve salario en texto
    salary_max_chf: int | None = None


@dataclass
class CoreMatchView:
    """Vista duck-type de models.match_result.MatchResult para el router.

    Scores/explicacion/skills vienen del CORE (lectura del feed); feedback,
    status, urgencia y borrador del ESCRITOR LOCAL (overlay) o defaults del
    modelo legacy si el item aun no existe localmente.
    """

    id: uuid.UUID
    job_hash: str
    score_final: float
    score_embedding: float
    score_salary: float
    score_location: float
    score_recency: float
    score_llm: float
    explanation: str | None
    matching_skills: list
    missing_skills: list
    feedback: str | None
    application_status: str
    urgency_score: float
    draft_letter: str | None
    created_at: datetime


def _parse_dt(value) -> datetime | None:
    """ISO-8601 del JSON del core -> datetime consciente de zona."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def legacy_job_refs(vacancy: dict) -> list[tuple[str, str]]:
    """Candidatos (external_id MD5, source sombra) de un VacancyDTO,
    DETERMINISTAS.

    Orden: el primary_listing si es fuente sombra `legacy:*`; despues el
    resto de listings `legacy:*` ordenados por (source, external_id) — con
    orden de ingestion core→legacy el attach por URL deja el listing legacy
    como NO-primary y su MD5 sigue siendo la identidad accionable (P2 rev.
    externa). Sin external_id duplicados."""
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    primary = vacancy.get("primary_listing") or {}
    primary_source = primary.get("source") or ""
    if primary_source.startswith(_LEGACY_SOURCE_PREFIX) and primary.get("external_id"):
        candidates.append((primary["external_id"], primary_source))
        seen.add(primary["external_id"])
    others = sorted(
        ((listing.get("source") or "", listing.get("external_id") or ""))
        for listing in (vacancy.get("listings") or [])
        if (listing.get("source") or "").startswith(_LEGACY_SOURCE_PREFIX)
    )
    for source, external_id in others:
        if external_id and external_id not in seen:
            candidates.append((external_id, source))
            seen.add(external_id)
    return candidates


def legacy_job_ref(vacancy: dict) -> tuple[str, bool]:
    """(job_ref presentable, es_identidad_legacy) de un VacancyDTO.

    Identidad legacy = primer candidato de `legacy_job_refs` (hash MD5 del
    job en CUALQUIER listing sombra `legacy:*`, no solo el primary). En su
    defecto, la identidad core en forma CANONICA del UUID (leccion del MD5:
    jamas presentar una forma no canonica que parsee ambigua)."""
    candidates = legacy_job_refs(vacancy)
    if candidates:
        return candidates[0][0], True
    return str(uuid.UUID(str(vacancy["id"]))), False


def _language_of(vacancy: dict) -> str | None:
    """Idioma servido por el core, sólo si es una cadena utilizable.

    Misma disciplina que el resto del mapeo: un 200 con una forma inesperada
    no debe romper la pagina. Ausente => el router lo detecta, como siempre."""
    value = vacancy.get("language")
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def _job_view(vacancy: dict, job_ref_source: str | None) -> CoreJobView:
    primary = vacancy.get("primary_listing") or {}
    listings = vacancy.get("listings") or []
    url = primary.get("url") or (listings[0]["url"] if listings else "")
    description = vacancy.get("description")
    return CoreJobView(
        title=vacancy.get("title") or "",
        company=vacancy.get("company"),
        url=url,
        location=vacancy.get("location"),
        # El modelo legacy guarda un snippet de 500 (models/job.py); el core
        # sirve la description completa de la revision vigente.
        description_snippet=description[:500] if description else None,
        tags=vacancy.get("tags") or [],
        source=job_ref_source,
        # El core lo sirve desde la canonica (VacancyDTO.language). Sin esta
        # linea el campo existia y era SIEMPRE None, y el router detectaba el
        # idioma por titulo en cada oferta servida.
        language=_language_of(vacancy),
    )


def _match_view(item: dict, job_ref: str, local: MatchResult | None) -> CoreMatchView:
    ev = item["evaluation"]
    scores = ev.get("scores") or {}
    state = item.get("state") or {}
    primary = item["vacancy"].get("primary_listing") or {}
    created_at = (
        local.created_at
        if local is not None
        else (_parse_dt(primary.get("first_seen_at")) or datetime.now(timezone.utc))
    )
    return CoreMatchView(
        # id estable: el del escritor local si existe; si no, derivado del
        # eval_key del core (deterministico entre peticiones).
        id=(
            local.id
            if local is not None
            else uuid.uuid5(uuid.NAMESPACE_URL, f"jobhunt-core-eval:{ev['eval_key']}")
        ),
        job_hash=job_ref,
        score_final=float(ev["score_final"]),
        # Fase A del core: score = coseno puro en scores.similarity; el resto
        # de factores no existe alli => 0.0 (forma del breakdown legacy).
        score_embedding=float(scores.get("similarity", scores.get("embedding", 0.0))),
        score_salary=float(scores.get("salary", 0.0)),
        score_location=float(scores.get("location", 0.0)),
        score_recency=float(scores.get("recency", 0.0)),
        score_llm=float(scores.get("llm", 0.0)),
        explanation=ev.get("explanation"),
        matching_skills=ev.get("matching_skills") or [],
        missing_skills=ev.get("missing_skills") or [],
        # Estado del ESCRITOR LOCAL (overlay); el `state` del core no tiene
        # escritor en esta etapa (la sombra no proyecta match_results).
        feedback=(local.feedback if local is not None else state.get("feedback")),
        application_status=(
            local.application_status if local is not None else "detected"
        ),
        urgency_score=(local.urgency_score if local is not None else 0.0),
        draft_letter=(local.draft_letter if local is not None else None),
        created_at=created_at,
    )


class CoreMatching:
    """Cliente del feed /v1 del core detras del puerto MatchingPort."""

    def __init__(
        self,
        db: AsyncSession,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        # La sesion sirve para el mapeo de identidad y el overlay del estado
        # local; inyectable para tests (MockTransport) como en catalogo.
        self._db = db
        self._client_factory = client_factory or default_client_factory

    async def results(
        self, user_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict], int]:
        if settings.CORE_FEEDBACK_ENABLED:
            from .feedback import require_core_feedback_route
            await require_core_feedback_route(self._db, user_id)
        core_profile_id = await resolve_core_profile_id(self._db, user_id)
        if core_profile_id is None:
            # Enrutado a core sin enrolar el vinculo de identidad: error de
            # operacion => misma senal WARNING/fallback que el core caido,
            # y CERO peticiones de red.
            raise CoreUnavailableError(
                f"usuario {user_id} sin vinculo en jobhunt_profile_map"
            )
        if (
            self._client_factory is default_client_factory
            and not settings.CORE_CONSUMER_KEY
        ):
            # Sin credencial no se hace ni una peticion (mismo trato que caida).
            raise CoreUnavailableError("CORE_CONSUMER_KEY no configurada")

        # La rama de feedback core no excluye ningun item, asi que basta con
        # las paginas que cubren la ventana pedida; la rama local si excluye
        # (accionabilidad y feedback negativo) y su total exige el recorrido.
        needed = offset + limit if settings.CORE_FEEDBACK_ENABLED else None
        items, core_total = await self._fetch_full_feed(core_profile_id, needed)
        # Identidad por item (candidatos DETERMINISTAS: cualquier listing
        # `legacy:*`, no solo el primary) + respaldo accionable + overlay
        # local en lotes. Los errores de FORMA del payload se traducen a
        # CoreUnavailableError (P2: fallback real, nunca un 500).
        try:
            candidates_per_item = [legacy_job_refs(it["vacancy"]) for it in items]
        except _PAYLOAD_ERRORS as exc:
            raise CoreUnavailableError(
                f"payload invalido del feed del core: {type(exc).__name__}: {exc}"
            ) from exc
        # E.15 school producers do not manufacture a legacy CDC listing.
        # Their scoped extension supplies the original actionable local hash.
        if any(not candidates for candidates in candidates_per_item):
            from services.schools.presentation import school_job_refs
            from services.schools.port import CoreUnavailableError as SchoolUnavailable

            try:
                school_refs = await school_job_refs(
                    self._db,
                    user_id,
                    [
                        it["vacancy"]["id"]
                        for it, candidates in zip(items, candidates_per_item)
                        if not candidates
                    ],
                )
                candidates_per_item = [
                    candidates or school_refs.get(str(it["vacancy"]["id"]), [])
                    for it, candidates in zip(items, candidates_per_item)
                ]
            except SchoolUnavailable as exc:
                raise CoreUnavailableError(
                    "school corpus identity unavailable"
                ) from exc
        legacy_refs = [ref for cands in candidates_per_item for ref, _source in cands]
        if settings.CORE_FEEDBACK_ENABLED:
            # School commands still address their own stable source_ref; keep
            # that identity. Ordinary items use the UUID we already know: an
            # upstream alias can name multiple historical clones, so resolving
            # it again would make an otherwise actionable item ambiguous.
            try:
                results = []
                for item, candidates in zip(items, candidates_per_item):
                    primary = item["vacancy"].get("primary_listing") or {}
                    school = next((candidate for candidate in candidates
                                   if candidate[1].removeprefix("legacy:").startswith("swiss_schools_")), None)
                    ref, source = school if school else (
                        str(uuid.UUID(item["vacancy"]["id"])), primary.get("source") or "core",
                    )
                    results.append({
                        "match": _match_view(item, ref, None),
                        "job": _job_view(item["vacancy"], source.removeprefix("legacy:")),
                    })
            except _PAYLOAD_ERRORS as exc:
                raise CoreUnavailableError("identidad core inválida en matching") from exc
            # El total describe el feed ENTERO. Si el core lo informo, es el
            # suyo; si no, `results` viene de un recorrido completo y su
            # longitud es el mismo numero.
            return results[offset:offset + limit], (
                core_total if core_total is not None else len(results))
        local_by_hash: dict[str, MatchResult] = {}
        actionable_hashes: set[str] = set()
        if legacy_refs:
            rows = (
                await self._db.execute(
                    select(MatchResult).where(
                        MatchResult.user_id == user_id,
                        MatchResult.job_hash.in_(legacy_refs),
                    )
                )
            ).scalars()
            local_by_hash = {mr.job_hash: mr for mr in rows}
            # Respaldo accionable = existe el Job local (el escritor local
            # puede registrar/upsertar feedback sobre el).
            actionable_hashes = set(
                (
                    await self._db.execute(
                        select(Job.hash).where(Job.hash.in_(legacy_refs))
                    )
                ).scalars()
            )

        results: list[dict] = []
        excluded_not_actionable = 0
        try:
            for item, candidates in zip(items, candidates_per_item):
                # Resolucion DETERMINISTA: primer candidato con Job local de
                # respaldo (primary legacy primero; despues los listings
                # legacy no-primary en orden (source, external_id)).
                chosen = next(
                    (c for c in candidates if c[0] in actionable_hashes), None
                )
                if chosen is None:
                    # EXCLUSION POR ACCIONABILIDAD (docstring del modulo):
                    # sin Job local el feedback devolveria 404. Cota:
                    # reaparecen en Fase C con el flip de escritor +
                    # idempotency key.
                    excluded_not_actionable += 1
                    continue
                job_ref, ref_source = chosen
                local = local_by_hash.get(job_ref)
                # El escritor local manda: "not for me" desaparece aunque el
                # core (sin escritor de feedback) aun lo sirva. G1/P3-27: el
                # feedback negativo se busca en TODOS los candidatos del item,
                # no solo en el elegido — un `dismissed` registrado bajo el
                # MD5 de OTRO listing de la misma vacante tambien la descarta
                # (edge multi-listing: antes reaparecia).
                if any(
                    ref in local_by_hash
                    and local_by_hash[ref].feedback in NEGATIVE_FEEDBACK
                    for ref, _src in candidates
                ):
                    continue
                # Presentar la fuente ORIGINAL del listing resuelto, no el
                # prefijo interno sombra.
                source = ref_source.removeprefix(_LEGACY_SOURCE_PREFIX) or None
                results.append(
                    {
                        "match": _match_view(item, job_ref, local),
                        "job": _job_view(item["vacancy"], source),
                    }
                )
        except _PAYLOAD_ERRORS as exc:
            raise CoreUnavailableError(
                f"payload invalido del feed del core: {type(exc).__name__}: {exc}"
            ) from exc

        total = len(results)
        self._log_exclusions(user_id, total, excluded_not_actionable, len(items))
        return results[offset : offset + limit], total

    @staticmethod
    def _log_exclusions(
        user_id: uuid.UUID, served: int, excluded: int, fetched: int
    ) -> None:
        """Observabilidad de la EXCLUSION POR ACCIONABILIDAD (canary §15bis).

        Sin esta senal, un canary cuyo feed core fuese mayormente core-nativo
        se quedaria vacio EN SILENCIO. Por peticion: INFO con servidos/
        excluidos cuando hay exclusion; WARNING especifico si el feed queda
        VACIO solo por exclusiones (todo lo que el core sirvio era
        no-accionable) — la senal del canary vacuo.
        """
        if excluded == 0:
            return
        if served == 0 and excluded == fetched:
            logger.warning(
                "matching core: feed VACIO solo por exclusion de accionabilidad "
                "para user %s — %d items del core sin respaldo local "
                "(canary sin senal util; cota Fase C)",
                user_id,
                excluded,
            )
        else:
            logger.info(
                "matching core: user %s — %d items servidos, %d excluidos "
                "por accionabilidad (sin respaldo local; cota Fase C)",
                user_id,
                served,
                excluded,
            )

    async def saved(
        self, user_id: uuid.UUID, limit: int = 100, offset: int = 0
    ) -> tuple[list[dict], int]:
        # Proyeccion PURA del estado del escritor LOCAL (feedback positivo):
        if settings.CORE_FEEDBACK_ENABLED:
            from .feedback import CoreFeedback
            return await CoreFeedback(self._db, self._client_factory).saved(
                user_id, limit=limit, offset=offset,
            )
        # se sirve de local en TODOS los modos (criterio unificador — ningun
        # estado local puede ser inaccesible por el routing). Sin red: no
        # depende de que el core este arriba.
        return await MatchResultService(self._db).get_saved_jobs(
            user_id=user_id, limit=limit, offset=offset
        )

    # ------------------------------------------------------------------ feed

    async def _fetch_full_feed(
        self, core_profile_id: uuid.UUID, needed: int | None = None
    ) -> tuple[list[dict], int | None]:
        """Recorre el feed por keyset; cache de paginas por ETag.

        `needed` = cuantos items necesita el llamante (offset + limit). Cuando
        lo indica Y el core informa del total de su feed, se deja de paginar al
        cubrirlo: servir 20 ofertas costaba 18 peticiones y ~9 s porque el
        recorrido completo era la UNICA forma de saber el total
        (PREDECLARACION_PUNTO5_2026-09-22.md §5-6).

        Devuelve (items, total_del_core). `total` es None si el core no lo
        informa — un core anterior a ese campo, o una pagina intermedia —, y
        entonces se recorre entero como antes: perder la optimizacion es
        aceptable, dar un total equivocado no.
        """
        pid = str(core_profile_id)
        items: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        total: int | None = None
        # Epoca al ENTRAR: un `clear_feed_cache` durante el recorrido (borrado
        # de perfil, ACK de feedback) invalida lo que se este cosiendo, y
        # `_remember_feed` no debe reinsertarlo (auditoria 2026-09-23).
        generation = (_cache_generation, _profile_generations.get(pid, 0))
        async with self._client_factory() as client:
            # Version del feed ANTES de recorrerlo: si no cambio, el recorrido
            # completo que ya tenemos sigue siendo valido y nos ahorramos 18
            # paginas. Si el core no la sirve, se recorre como siempre.
            #
            # Solo se pregunta cuando puede PAGAR. La consulta de version
            # recorre las mismas filas que el recuento del feed (0,4-0,9 s
            # medidos), asi que pedirla para servir una pagina de 20 —que el
            # corte temprano ya resuelve en UNA peticion— seria cambiar una
            # peticion barata por dos. Por encima de una pagina, el recorrido
            # cuesta 18 peticiones y 11,5 s: ahi compensa de sobra.
            version: str | None = None
            version_total: int | None = None
            if needed is None or needed > FEED_PAGE_LIMIT:
                declarada = await self._feed_version(client, core_profile_id)
                if declarada is not None:
                    version, version_total = declarada
            if version is not None:
                cacheado = _feed_cache.get(pid)
                if cacheado is not None and cacheado[0] == version:
                    return list(cacheado[1]), cacheado[2]
            for _ in range(MAX_FEED_PAGES):
                page = await self._fetch_page(client, core_profile_id, cursor)
                page_items = page.get("items") or []
                if not isinstance(page_items, list):
                    # MatchesPageDTO.items es una lista: otra forma es
                    # payload incompatible (P2), no una pagina servible.
                    raise CoreUnavailableError(
                        "feed del core con 'items' no-lista (payload invalido)"
                    )
                # Validacion de FORMA con el DTO privado ANTES de consumir la
                # pagina: tipos rotos (next_cursor no-str, skills/tags
                # no-lista, scores no finitos) => CoreUnavailableError, nunca
                # un TypeError/ValidationError fuera del fallback (P2).
                try:
                    page_dto = _FeedPageDTO.model_validate(page)
                except _PAYLOAD_ERRORS as exc:
                    raise CoreUnavailableError(
                        f"payload invalido del feed del core: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                items.extend(page_items)
                if total is None:
                    total = page_dto.total
                cursor = page_dto.next_cursor
                if cursor is None:
                    # Recorrido COMPLETO: es el unico que se cachea. Uno
                    # cortado por `needed` no puede servir a quien pida mas.
                    if version is not None:
                        await self._maybe_remember(
                            client, core_profile_id, version, version_total,
                            items, total, generation,
                        )
                    return items, total
                # Corte temprano: solo si el core ya dijo cuantas ofertas tiene
                # su feed. Sin ese dato el total lo da el recorrido, y cortar
                # aqui lo falsearia.
                if needed is not None and total is not None and len(items) >= needed:
                    return items, total
                if cursor in seen_cursors:
                    raise CoreUnavailableError(
                        f"feed del core con cursor repetido: {cursor[:64]}"
                    )
                seen_cursors.add(cursor)
        raise CoreUnavailableError(
            f"feed del core excede {MAX_FEED_PAGES} paginas (cota anti-bucle)"
        )

    async def _feed_version(
        self, client: httpx.AsyncClient, core_profile_id: uuid.UUID
    ) -> tuple[str, int | None] | None:
        """(version, total) del feed segun el core, o None si no se puede obtener.

        None significa «no lo se», y el llamante recorre el feed entero como
        siempre: un core anterior a este endpoint, un fallo de red o un
        payload raro degradan el RENDIMIENTO, nunca la correccion. Es la misma
        disciplina que `MatchesPageDTO.total`, y por el mismo motivo: servir
        algo rancio es peor que servirlo lento.
        """
        try:
            resp = await client.get(f"/profiles/{core_profile_id}/matches/version")
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        # Misma disciplina que el resto del cliente: un 200 con OTRA forma es
        # un payload invalido, no una excepcion que escape del fallback. Un
        # cuerpo no-objeto reventaba aqui con AttributeError.
        if not isinstance(body, dict):
            return None
        version = body.get("version")
        if not isinstance(version, str) or not version:
            return None
        total = body.get("total")
        return version, (total if isinstance(total, int) and not isinstance(total, bool) else None)

    async def _maybe_remember(
        self, client, core_profile_id, version, version_total, items, total, generation,
    ) -> None:
        """Decide si un recorrido completo puede cachearse bajo `version`.

        Tres comprobaciones, cada una un hueco que encontro la auditoria del
        2026-09-23; ninguna cambia lo que se SIRVE, solo lo que se guarda:

        1. El total que declaro el endpoint de version debe coincidir con el
           de la primera pagina: si no, algo cambio entre las dos lecturas.
        2. Un recorrido de 18 paginas no es atomico. Se relee la version al
           terminar y solo se guarda si es EXACTAMENTE la de antes; si el
           feed se movio a mitad, las paginas cosidas no describen ningun
           estado real (lectura desgarrada).
        3. `_remember_feed` verifica la epoca capturada al entrar.
        """
        pid = str(core_profile_id)
        if version_total is not None and total is not None and version_total != total:
            logger.warning(
                "feed de %s: la version declara %s items y la primera pagina %s — no se cachea",
                pid, version_total, total,
            )
            return
        confirmada = await self._feed_version(client, core_profile_id)
        if confirmada is None or confirmada[0] != version:
            logger.info("feed de %s cambio durante el recorrido — no se cachea", pid)
            return
        self._remember_feed(pid, version, items, total, generation)

    def _remember_feed(
        self, pid: str, version: str, items: list[dict], total, generation=None,
    ) -> None:
        """Guarda un recorrido completo. Acotado y sin LRU a proposito: este
        despliegue tiene tres perfiles, y vaciar del todo es mas simple y mas
        facil de razonar que desalojar por uso."""
        if total is None or len(items) != total:
            # El total del core y lo recorrido deben cuadrar; si no cuadran,
            # no se cachea nada. Cachear una discrepancia la perpetuaria. Y
            # se DICE: un descarte mudo apagaba la optimizacion para ese
            # perfil sin dejar rastro.
            logger.warning(
                "feed de %s: recorrido de %d items frente a total %s — no se cachea "
                "(¿vacantes vivas sin revision canonica?)", pid, len(items), total,
            )
            return
        if generation is not None and generation != (
            _cache_generation, _profile_generations.get(pid, 0)
        ):
            logger.info("feed de %s invalidado durante el recorrido — no se cachea", pid)
            return
        if len(_feed_cache) >= _FEED_CACHE_MAX and pid not in _feed_cache:
            _feed_cache.clear()
        _feed_cache[pid] = (version, list(items), total)

    async def _fetch_page(
        self, client: httpx.AsyncClient, core_profile_id: uuid.UUID, cursor: str | None
    ) -> dict:
        cache_key = (str(core_profile_id), cursor or "")
        generation = (_cache_generation, _profile_generations.get(cache_key[0], 0))
        cached = _etag_cache.get(cache_key)
        headers = {"If-None-Match": cached[0]} if cached else {}
        params: dict = {"limit": FEED_PAGE_LIMIT}
        if cursor is not None:
            params["cursor"] = cursor
        try:
            resp = await client.get(
                f"/profiles/{core_profile_id}/matches",
                params=params,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise CoreUnavailableError(f"core /v1 inaccesible: {exc}") from exc
        if generation != (_cache_generation, _profile_generations.get(cache_key[0], 0)):
            raise CoreUnavailableError("core representation invalidated during request")
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
                f"core /v1 devolvio {resp.status_code} para el feed de "
                f"{core_profile_id}"
            )
        try:
            body = resp.json()
        except ValueError as exc:  # JSON ilegible en un 200
            raise CoreUnavailableError(
                f"JSON invalido del core en el feed de {core_profile_id}: {exc}"
            ) from exc
        if not isinstance(body, dict):
            # MatchesPageDTO es un objeto: cualquier otra forma es incompatible.
            raise CoreUnavailableError(
                f"payload no-objeto del core en el feed de {core_profile_id}"
            )
        etag = resp.headers.get("etag")
        if etag:
            if len(_etag_cache) >= _ETAG_CACHE_MAX:
                _etag_cache.clear()  # acotado y simple; se rellena con 200s
            _etag_cache[cache_key] = (etag, body)
        return body
