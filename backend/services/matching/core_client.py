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
el `job_hash` LEGACY cuando la vacante procede de la sombra: el proyector
B-02 crea fuentes `legacy:<source>` con `external_id` = hash MD5 del job, y
ese external_id viaja en `primary_listing`. Asi las ESCRITURAS (feedback,
status), que siguen en local, operan sobre la misma identidad que el usuario
esta viendo. Si la vacante no tiene listing legacy (core-nativa), su
identidad presentable seria el UUID en FORMA CANONICA con guiones —
round-trip `str(UUID(x))` — nunca una forma que colisione con un MD5 de
32 hex (lo fija `legacy_job_ref`); pero en esta etapa esos items NO llegan
al feed (ver EXCLUSION POR ACCIONABILIDAD).

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

# Cache de paginas por ETag: clave (perfil core, cursor) -> (etag, body).
# En proceso y acotado (mismo espiritu que la cache de routing): en el 99%
# de refrescos el core responde 304 sin cuerpo y se reutiliza la pagina.
_ETAG_CACHE_MAX = 512
_etag_cache: dict[tuple[str, str], tuple[str, dict]] = {}


def clear_feed_cache() -> None:
    """Vacia la cache de paginas del feed (tests / operacion)."""
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
    language: str | None = None  # el router lo detecta por titulo si falta
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


def legacy_job_ref(vacancy: dict) -> tuple[str, bool]:
    """(job_ref presentable, es_identidad_legacy) de un VacancyDTO.

    Identidad legacy = external_id del primary_listing de una fuente sombra
    `legacy:*` (hash MD5 del job). En su defecto, la identidad core en forma
    CANONICA del UUID (leccion del MD5: jamas presentar una forma no
    canonica que parsee ambigua)."""
    primary = vacancy.get("primary_listing") or {}
    source = primary.get("source") or ""
    external_id = primary.get("external_id")
    if source.startswith(_LEGACY_SOURCE_PREFIX) and external_id:
        return external_id, True
    return str(uuid.UUID(str(vacancy["id"]))), False


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

        items = await self._fetch_full_feed(core_profile_id)

        # Identidad por item + respaldo accionable + overlay local en lotes.
        refs = [legacy_job_ref(it["vacancy"]) for it in items]
        legacy_refs = [ref for ref, is_legacy in refs if is_legacy]
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
        for item, (job_ref, is_legacy) in zip(items, refs):
            if not is_legacy or job_ref not in actionable_hashes:
                # EXCLUSION POR ACCIONABILIDAD (docstring del modulo): sin
                # Job local el feedback devolveria 404. Cota: reaparecen en
                # Fase C con el flip de escritor + idempotency key.
                excluded_not_actionable += 1
                continue
            local = local_by_hash.get(job_ref)
            if local is not None and local.feedback in NEGATIVE_FEEDBACK:
                # El escritor local manda: "not for me" desaparece aunque el
                # core (sin escritor de feedback) aun lo sirva.
                continue
            source = (item["vacancy"].get("primary_listing") or {}).get("source")
            if source and source.startswith(_LEGACY_SOURCE_PREFIX):
                # Presentar la fuente ORIGINAL, no el prefijo interno sombra.
                source = source[len(_LEGACY_SOURCE_PREFIX) :]
            results.append(
                {
                    "match": _match_view(item, job_ref, local),
                    "job": _job_view(item["vacancy"], source),
                }
            )

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
        # se sirve de local en TODOS los modos (criterio unificador — ningun
        # estado local puede ser inaccesible por el routing). Sin red: no
        # depende de que el core este arriba.
        return await MatchResultService(self._db).get_saved_jobs(
            user_id=user_id, limit=limit, offset=offset
        )

    # ------------------------------------------------------------------ feed

    async def _fetch_full_feed(self, core_profile_id: uuid.UUID) -> list[dict]:
        """Recorre el feed completo por keyset; cache de paginas por ETag."""
        items: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        async with self._client_factory() as client:
            for _ in range(MAX_FEED_PAGES):
                page = await self._fetch_page(client, core_profile_id, cursor)
                items.extend(page.get("items") or [])
                cursor = page.get("next_cursor")
                if cursor is None:
                    return items
                if cursor in seen_cursors:
                    raise CoreUnavailableError(
                        f"feed del core con cursor repetido: {cursor[:64]}"
                    )
                seen_cursors.add(cursor)
        raise CoreUnavailableError(
            f"feed del core excede {MAX_FEED_PAGES} paginas (cota anti-bucle)"
        )

    async def _fetch_page(
        self, client: httpx.AsyncClient, core_profile_id: uuid.UUID, cursor: str | None
    ) -> dict:
        cache_key = (str(core_profile_id), cursor or "")
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
        body = resp.json()
        etag = resp.headers.get("etag")
        if etag:
            if len(_etag_cache) >= _ETAG_CACHE_MAX:
                _etag_cache.clear()  # acotado y simple; se rellena con 200s
            _etag_cache[cache_key] = (etag, body)
        return body
