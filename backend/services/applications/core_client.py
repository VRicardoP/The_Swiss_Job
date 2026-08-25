"""Implementacion CORE de la capacidad candidaturas — C-4 (escrituras /v1).

Cliente HTTP de los endpoints C-4 del core (DISENO_C4_ESCRITURAS_V2_1;
jobhunt_core/api/v1_applications.py — contrato replicado aqui como DTOs
privados, sin importar jobhunt_core: frontera estricta, plan §21):

- GET  /v1/applications?profile=&limit=&cursor=  (scope applications:read):
  feed COMPUESTO applications + bookmarks puros, keyset (ts DESC, id DESC).
- POST /v1/applications (scope applications:write): vincula la vacante en la
  misma tx (cascada Decision 3, aqui siempre por URL del Job local); status
  ausente → saved; 409 application_exists si el par (perfil, vacante) existe.
- PATCH/DELETE /v1/applications/{id}: direccionamiento DUAL (application.id o
  bookmark puro =vacancy_id, con promocion idempotente) — este cliente solo
  emite ids que el propio feed/alta le devolvio.

IDENTIDAD DE PERFIL (multi-usuario, a diferencia del portfolio): el vinculo
usuario legacy -> perfil core es POR USUARIO via la tabla LOCAL
`jobhunt_profile_map` (services/matching/identity.py — la misma fila que
consumen matching y perfiles; NO comodin de config). Sin vinculo o sin
credencial: CoreUnavailableError SIN emitir peticiones.

IDENTIDAD DE VACANTE (leccion del MD5, heredada de matching): el contrato
legacy presenta `job_hash` (MD5) y el DTO C-4 no lo transporta. Resolucion
DETERMINISTA por item: (1) el Job local cuya `url` (UNIQUE en `jobs`) coincide
con la url del snapshot — toda alta de ESTE cliente viaja con la url del Job
local, asi que el round-trip recupera el hash exacto; (2) en su defecto (Job
podado o item no escrito por este BFF), `BaseJobProvider.compute_hash` sobre
el snapshot (title|company|url) — la MISMA funcion de identidad del pipeline
de ingesta, nunca una forma inventada.

ENUM DE STATUS: los 8 estados del core (core0011) son EXACTAMENTE los 8 de
`models.enums.ApplicationStatus` → identidad en ambas direcciones (sin
degradacion, a diferencia del portfolio 6⊂8). Lo fija el contract test.

COTAS honestas (nunca una respuesta/escritura silenciosamente mal):
- `applied_at`/`applied_url` NO existen en el contrato /v1: mutar
  `applied_url` → ApplicationsUnsupportedError; en lectura ambos van a None
  (la auto-transicion applied_at del motor local no es representable).
- `follow_up_date` es `date` en el core y datetime en el modelo legacy: se
  trunca al dia en escritura y se sirve a medianoche UTC en lectura.
- bookmarks puros del feed compuesto (kind=bookmark) se EXCLUYEN: en el
  modelo SwissJob 'saved' es estado de MATCHING (feedback positivo en
  match_results, escritor local — criterio del overlay de matching), no una
  candidatura.
- el state machine sobre match_results (status/draft de watchlist) NO tiene
  superficie C-4 → ApplicationsUnsupportedError aqui; la costura (seam.py)
  lo sirve SIEMPRE del escritor local (criterio unificador).
- `job_location` sin Job local de respaldo: None (el DTO C-4 no modela
  location); con Job local, los campos job_* salen del JOIN local (paridad
  byte con el motor local).
- stats: el /v1 no expone agregados — se derivan del MISMO feed drenado con
  las MISMAS formulas del motor local (by_source con la semantica del INNER
  JOIN local: fuente del Job local si existe; si no, la del snapshot).

CONCURRENCIA e IDEMPOTENCIA: cada usuario es el unico escritor de su perfil
core — no se envia If-Match (opcional en el /v1; last-write-wins = semantica
del motor local, sin control de concurrencia). Toda escritura viaja con
Idempotency-Key nueva (uuid4): opcional en applications, y asi el cliente
queda uniforme con el contrato C-4 completo (Decision 1).

RESILIENCIA (criterio A.SEAM): transporte roto, 2xx con payload fuera de
contrato, status inesperado (400/401/403/412/5xx) o 404 de PERFIL
(configuracion, no datos) → CoreUnavailableError; el 404 de RECURSO se mapea
al contrato del puerto (update→None, delete→False → 404 del router).
"""

import logging
import uuid
from datetime import date, datetime, time, timezone
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.enums import ApplicationStatus
from models.job import Job
from schemas.applications import (
    ApplicationResponse,
    ApplicationsListResponse,
    ApplicationStatsResponse,
    ApplicationUpdate,
)
from services.job_service import BaseJobProvider
from services.matching.identity import resolve_core_profile_id

from .port import (
    ApplicationJobNotFoundError,
    ApplicationsUnsupportedError,
    CoreUnavailableError,
    DuplicateApplicationError,
)

logger = logging.getLogger(__name__)

_MAX_PAGE_LIMIT = 100  # jobhunt_core/api/v1.py MAX_PAGE_LIMIT
_MAX_FEED_PAGES = 100  # 10k items; por encima => cursor en bucle o feed anomalo

_STATE_MACHINE_MSG = (
    "el state machine sobre match_results no tiene superficie C-4 en el /v1 "
    "(estado co-propiedad de matching, escritor local) — lo sirve la costura "
    "desde local"
)

# Los 8 estados del contrato C-4 (core0011) — identidad con models.enums.
_CoreStatus = Literal[
    "saved",
    "applied",
    "phone_screen",
    "technical",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
]


# ------------------------------------------------------------- DTOs privados
# Replicas ESTRECHAS del contrato C-4 (solo los campos que este cliente
# consume), con tipos validados: un 200 bien formado en JSON pero fuera de
# contrato se traduce a CoreUnavailableError (fallo cerrado, nunca un 500).


class _ApplicationDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    kind: Literal["application", "bookmark"]
    status: _CoreStatus
    notes: str | None = None
    follow_up_date: date | None = None
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    company: str | None = None
    url: str | None = None
    source: str | None = None


class _ApplicationsPageDTO(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[_ApplicationDTO]
    next_cursor: str | None = None


def default_client_factory() -> httpx.AsyncClient:
    """Cliente httpx contra el /v1 del core por la red interna de compose
    (plan §21: puerto dedicado, solo red interna, nunca ngrok)."""
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


def _payload_unavailable(context: str, exc: Exception) -> CoreUnavailableError:
    """Un 2xx ilegible o fuera de contrato es tan inservible como el core
    caido (mismo criterio que catalogo/matching/perfiles)."""
    if isinstance(exc, ValidationError):
        detail = f"{exc.error_count()} errores de contrato"
    else:
        detail = f"{type(exc).__name__}: {exc}"
    return CoreUnavailableError(f"payload invalido del core ({context}): {detail}")


def _error_code(resp: httpx.Response) -> str:
    """`code` del sobre de errores {code,message,details} del /v1, o ""."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    return body.get("code", "") if isinstance(body, dict) else ""


def _date_to_datetime(value: date | None) -> datetime | None:
    """Cota follow_up_date: el core modela date; el legacy, datetime — se
    sirve a medianoche UTC (documentado en el docstring del modulo)."""
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _to_response(
    dto: _ApplicationDTO, user_id: uuid.UUID, job_hash: str, job: Job | None
) -> ApplicationResponse:
    """Item C-4 → contrato legacy. job_* del JOIN local si el Job existe
    (paridad byte con el motor local); si no, del snapshot (location None —
    cota del DTO). applied_at/applied_url: None (cota, sin superficie /v1)."""
    return ApplicationResponse(
        id=dto.id,
        user_id=user_id,
        job_hash=job_hash,
        status=ApplicationStatus(dto.status),
        notes=dto.notes,
        applied_at=None,
        applied_url=None,
        follow_up_date=_date_to_datetime(dto.follow_up_date),
        created_at=dto.created_at,
        updated_at=dto.updated_at,
        job_title=job.title if job else dto.title,
        job_company=job.company if job else dto.company,
        job_location=job.location if job else None,
        job_source=job.source if job else dto.source,
    )


class CoreApplications:
    """Cliente de los endpoints C-4 del core detras del puerto ApplicationsPort."""

    def __init__(
        self,
        db: AsyncSession,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        # La sesion sirve para la identidad (jobhunt_profile_map + resolucion
        # de job_hash contra `jobs`); factory inyectable para tests
        # (MockTransport), como en matching.
        self._db = db
        self._client_factory = client_factory or default_client_factory

    # ------------------------------------------------------------- plomeria

    async def _require_profile(self, user_id: uuid.UUID) -> uuid.UUID:
        """Perfil core del USUARIO (jobhunt_profile_map) o CoreUnavailableError
        SIN red — enrutar a core sin enrolar el vinculo es error de operacion
        (mismo trato que el core caido), igual que en matching."""
        core_profile_id = await resolve_core_profile_id(self._db, user_id)
        if core_profile_id is None:
            raise CoreUnavailableError(
                f"usuario {user_id} sin vinculo en jobhunt_profile_map"
            )
        if (
            self._client_factory is default_client_factory
            and not settings.CORE_CONSUMER_KEY
        ):
            # Sin credencial no se hace ni una peticion (solo aplica a la
            # factory de produccion; los tests inyectan transporte).
            raise CoreUnavailableError("CORE_CONSUMER_KEY no configurada")
        return core_profile_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        write: bool = False,
    ) -> httpx.Response:
        """Una llamada al /v1. Toda escritura viaja con Idempotency-Key nueva
        (uuid4) — el candado de reintento del contrato C-4 (Decision 1)."""
        headers = {"Idempotency-Key": str(uuid.uuid4())} if write else None
        try:
            async with self._client_factory() as client:
                resp = await client.request(
                    method, path, params=params, json=json_body, headers=headers
                )
        except httpx.HTTPError as exc:
            raise CoreUnavailableError(f"core /v1 inaccesible: {exc}") from exc
        return resp

    def _unexpected(self, resp: httpx.Response, context: str) -> CoreUnavailableError:
        """Status fuera del contrato esperado para la operacion (incl. 401/403
        de credencial, 400 de la cascada de vinculo y 412/409 que este cliente
        no provoca): indisponibilidad honesta, nunca un 500."""
        return CoreUnavailableError(
            f"core /v1 devolvio {resp.status_code} ({_error_code(resp)}) para {context}"
        )

    def _parse_item(self, resp: httpx.Response, context: str) -> _ApplicationDTO:
        try:
            return _ApplicationDTO.model_validate(resp.json())
        except (ValueError, ValidationError) as exc:
            raise _payload_unavailable(context, exc) from exc

    async def _drain_applications(
        self, core_profile_id: uuid.UUID
    ) -> list[_ApplicationDTO]:
        """Drena el feed compuesto por keyset y devuelve SOLO las applications
        (los bookmarks puros son estado de matching en el modelo SwissJob —
        cota del docstring del modulo)."""
        items: list[_ApplicationDTO] = []
        excluded_bookmarks = 0
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(_MAX_FEED_PAGES):
            params: dict = {"profile": str(core_profile_id), "limit": _MAX_PAGE_LIMIT}
            if cursor is not None:
                params["cursor"] = cursor
            resp = await self._request("GET", "/applications", params=params)
            if resp.status_code == 404:
                # Perfil desconocido para ESTE consumer (vinculo obsoleto o
                # credencial de otro tenant): configuracion, no datos.
                raise CoreUnavailableError(
                    f"perfil core {core_profile_id} inexistente para este consumer"
                )
            if resp.status_code != 200:
                raise self._unexpected(resp, "el feed de candidaturas")
            try:
                page = _ApplicationsPageDTO.model_validate(resp.json())
            except (ValueError, ValidationError) as exc:
                raise _payload_unavailable("feed de candidaturas", exc) from exc
            for dto in page.items:
                if dto.kind == "application":
                    items.append(dto)
                else:
                    excluded_bookmarks += 1
            if page.next_cursor is None:
                if excluded_bookmarks:
                    logger.debug(
                        "candidaturas core: %d bookmarks puros excluidos del "
                        "feed compuesto (estado de matching, cota documentada)",
                        excluded_bookmarks,
                    )
                return items
            if page.next_cursor in seen_cursors:
                raise CoreUnavailableError(
                    f"feed del core con cursor repetido: {page.next_cursor[:64]}"
                )
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        raise CoreUnavailableError(
            f"feed del core excede {_MAX_FEED_PAGES} paginas (cota anti-bucle)"
        )

    async def _jobs_by_url(self, dtos: list[_ApplicationDTO]) -> dict[str, Job]:
        """Jobs locales de respaldo, en lote, por la url del snapshot (UNIQUE
        en `jobs`) — la resolucion determinista de identidad del modulo."""
        urls = {dto.url for dto in dtos if dto.url}
        if not urls:
            return {}
        rows = (await self._db.execute(select(Job).where(Job.url.in_(urls)))).scalars()
        return {job.url: job for job in rows}

    async def _owned_by_profile(
        self, core_profile_id: uuid.UUID, application_id: uuid.UUID
    ) -> bool:
        """Scoping de ownership POR PERFIL para las escrituras (G1/P1-3).

        El PATCH/DELETE del /v1 solo acota por CONSUMER (`p.consumer_id` en el
        WHERE de `_lock_target`), y la credencial CORE_CONSUMER_KEY es UNA y
        compartida por todos los usuarios de este BFF: sin este check, el
        usuario B podia mutar/borrar la candidatura de A conociendo su UUID
        (IDOR), incumpliendo el contrato del puerto («None si no existe PARA
        ESE USUARIO»). El perfil no viaja en la escritura porque el contrato
        /v1 no lo modela — el scoping se aplica AQUI, verificando que el id
        aparece en el feed DEL PERFIL del usuario antes de emitir la
        escritura. TOCTOU aceptado: cada usuario es el unico escritor de su
        perfil (docstring del modulo), y el candado real seguiria siendo el
        consumer del core.
        """
        dtos = await self._drain_applications(core_profile_id)
        return any(dto.id == application_id for dto in dtos)

    @staticmethod
    def _job_hash_for(dto: _ApplicationDTO, by_url: dict[str, Job]) -> str:
        """(1) hash del Job local por url; (2) compute_hash del snapshot —
        la MISMA funcion de identidad del pipeline de ingesta."""
        job = by_url.get(dto.url) if dto.url else None
        if job is not None:
            return job.hash
        return BaseJobProvider.compute_hash(
            dto.title or "", dto.company or "", dto.url or ""
        )

    # ------------------------------------------------------------ operaciones

    async def list(
        self,
        user_id: uuid.UUID,
        status: ApplicationStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ApplicationsListResponse:
        """Drena el feed y reproduce el contrato local: filtro de status en
        cliente (el GET /v1 no lo modela), orden updated_at DESC (el feed core
        ordena por created_at), by_status sobre TODAS las candidaturas del
        usuario (semantica del motor local) y paginacion tras el filtro."""
        core_profile_id = await self._require_profile(user_id)
        dtos = await self._drain_applications(core_profile_id)
        by_url = await self._jobs_by_url(dtos)
        responses = [
            _to_response(
                dto,
                user_id,
                self._job_hash_for(dto, by_url),
                by_url.get(dto.url) if dto.url else None,
            )
            for dto in dtos
        ]
        by_status: dict[str, int] = {}
        for resp in responses:
            by_status[resp.status.value] = by_status.get(resp.status.value, 0) + 1
        filtered = [r for r in responses if status is None or r.status == status]
        filtered.sort(key=lambda r: r.updated_at, reverse=True)
        return ApplicationsListResponse(
            data=filtered[offset : offset + limit],
            total=len(filtered),
            by_status=by_status,
        )

    async def create(
        self, user_id: uuid.UUID, job_hash: str, notes: str | None = None
    ) -> ApplicationResponse:
        """Alta contra el POST /v1: la oferta se resuelve PRIMERO en local
        (misma precedencia que el motor local: sin Job → 404 SIN red); el
        snapshot viaja con los campos del Job local (url incluida — el
        round-trip de identidad del docstring). Status omitido → saved."""
        job = (
            await self._db.execute(select(Job).where(Job.hash == job_hash))
        ).scalar_one_or_none()
        if job is None:
            raise ApplicationJobNotFoundError("Job not found")
        core_profile_id = await self._require_profile(user_id)
        body = {
            "profile_id": str(core_profile_id),
            "url": job.url,
            "title": job.title,
            "company": job.company,
            # Snapshot = "lo que el usuario vio": el snippet legacy de 500
            # (la description completa podria exceder la cota 100k del /v1).
            "description": job.description_snippet,
            "source": job.source,
            "notes": notes,
        }
        resp = await self._request("POST", "/applications", json_body=body, write=True)
        if resp.status_code == 409:
            # Candado UNIQUE(perfil, vacante) del core — el mismo contrato que
            # el chequeo (user_id, job_hash) local (url UNIQUE en `jobs`).
            raise DuplicateApplicationError("Application already exists for this job")
        if resp.status_code == 404:
            raise CoreUnavailableError(
                f"perfil core {core_profile_id} inexistente para este consumer"
            )
        if resp.status_code != 201:
            raise self._unexpected(resp, "el alta de candidatura")
        dto = self._parse_item(resp, "alta de candidatura")
        return _to_response(dto, user_id, job_hash, job)

    async def stats(self, user_id: uuid.UUID) -> ApplicationStatsResponse:
        """Sin endpoint /v1 de agregados: se derivan del MISMO feed drenado
        con las formulas EXACTAS del motor local (cota del docstring)."""
        core_profile_id = await self._require_profile(user_id)
        dtos = await self._drain_applications(core_profile_id)
        by_url = await self._jobs_by_url(dtos)

        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for dto in dtos:
            by_status[dto.status] = by_status.get(dto.status, 0) + 1
            job = by_url.get(dto.url) if dto.url else None
            source = job.source if job else dto.source
            if source:
                by_source[source] = by_source.get(source, 0) + 1

        # Formulas verbatim de LocalApplications.stats (contract test mediante).
        total = sum(by_status.values())
        conversion_rates: dict[str, float] = {}
        if total > 0:
            applied = sum(
                v for k, v in by_status.items() if k != ApplicationStatus.saved.value
            )
            conversion_rates["saved_to_applied"] = round(applied / total, 3)
            interviews = sum(
                v
                for k, v in by_status.items()
                if k
                in {ApplicationStatus.interview.value, ApplicationStatus.offer.value}
            )
            if applied > 0:
                conversion_rates["applied_to_interview"] = round(
                    interviews / applied, 3
                )
            offers = by_status.get(ApplicationStatus.offer.value, 0)
            if interviews > 0:
                conversion_rates["interview_to_offer"] = round(offers / interviews, 3)

        return ApplicationStatsResponse(
            by_status=by_status,
            conversion_rates=conversion_rates,
            by_source=by_source,
        )

    async def update(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
        changes: ApplicationUpdate,
    ) -> ApplicationResponse | None:
        """PATCH parcial /v1 (solo status/notes/follow_up_date). COTA:
        `applied_url` no existe en el contrato C-4 → Unsupported ANTES de
        emitir red; la auto-transicion applied_at del motor local no es
        representable (lectura a None, docstring del modulo)."""
        provided = changes.model_fields_set
        if "applied_url" in provided:
            raise ApplicationsUnsupportedError(
                "update de applied_url: sin equivalente en el contrato C-4 "
                "(el /v1 solo muta status/notes/follow_up_date)"
            )
        # identidad+credencial antes de red; y ownership por PERFIL antes de
        # la escritura (G1/P1-3): un id ajeno se responde como inexistente
        # (404 del router), indistinguible — mismo contrato que el motor local.
        core_profile_id = await self._require_profile(user_id)
        if not await self._owned_by_profile(core_profile_id, application_id):
            return None
        body: dict = {}
        if "status" in provided and changes.status is not None:
            body["status"] = changes.status.value
        if "notes" in provided:
            body["notes"] = changes.notes
        if "follow_up_date" in provided:
            # Cota date-vs-datetime del docstring: se trunca al dia.
            body["follow_up_date"] = (
                changes.follow_up_date.date().isoformat()
                if changes.follow_up_date is not None
                else None
            )
        resp = await self._request(
            "PATCH", f"/applications/{application_id}", json_body=body, write=True
        )
        if resp.status_code == 404:
            return None  # el router lo mapea a 404 (mismo contrato que local)
        if resp.status_code != 200:
            raise self._unexpected(resp, f"el PATCH de {application_id}")
        dto = self._parse_item(resp, f"PATCH de {application_id}")
        by_url = await self._jobs_by_url([dto])
        job = by_url.get(dto.url) if dto.url else None
        return _to_response(dto, user_id, self._job_hash_for(dto, by_url), job)

    async def delete(self, user_id: uuid.UUID, application_id: uuid.UUID) -> bool:
        core_profile_id = await self._require_profile(user_id)
        # Ownership por PERFIL antes de emitir el DELETE (G1/P1-3, ver
        # _owned_by_profile): un id ajeno = inexistente para este usuario.
        if not await self._owned_by_profile(core_profile_id, application_id):
            return False
        resp = await self._request(
            "DELETE", f"/applications/{application_id}", write=True
        )
        if resp.status_code == 404:
            return False
        if resp.status_code != 204:
            raise self._unexpected(resp, f"el DELETE de {application_id}")
        return True

    # ── State machine sobre match_results: SIN superficie C-4 (cota) ────

    async def set_match_status(
        self, user_id: uuid.UUID, job_hash: str, application_status: str
    ) -> bool:
        raise ApplicationsUnsupportedError(_STATE_MACHINE_MSG)

    async def get_match(self, user_id: uuid.UUID, job_hash: str):
        raise ApplicationsUnsupportedError(_STATE_MACHINE_MSG)

    async def save_draft(self, user_id: uuid.UUID, job_hash: str, draft: str) -> bool:
        raise ApplicationsUnsupportedError(_STATE_MACHINE_MSG)

    async def get_draft(self, user_id: uuid.UUID, job_hash: str) -> str | None:
        raise ApplicationsUnsupportedError(_STATE_MACHINE_MSG)
