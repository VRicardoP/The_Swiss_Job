"""Synchronous core feedback writer for the explicit F cutover.

No fallback or local dual-write. Enabling this writer requires migrating and
reconciling existing marks under a freeze; merely deploying the client does not
change the active writer. Old upstream aliases resolve in the core, not jobs.
"""

import re
import uuid
from datetime import datetime
from typing import Literal

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, FiniteFloat, ValidationError

from config import settings
from services.matching.identity import resolve_core_profile_id
from services.matching.port import CoreUnavailableError
from services.matching.core_client import (
    _FeedItemDTO,
    _job_view,
    _match_view,
    default_client_factory,
)


async def block_feedback_writes(request: Request):
    if settings.FEEDBACK_WRITES_FROZEN and request.method not in {
        "GET",
        "HEAD",
        "OPTIONS",
    }:
        raise HTTPException(
            status_code=503, detail="Feedback writes are frozen for cutover"
        )


async def require_core_feedback_route(db, user_id):
    from services.routing import CAPABILITY_MATCHING, resolve_mode

    mode = await resolve_mode(db, CAPABILITY_MATCHING, user_id)
    if mode not in {"core_primary", "rollback_pending"}:
        raise CoreUnavailableError(
            "feedback core exige routing autoritativo, sin fallback local"
        )


def feedback_writer(db):
    if settings.CORE_FEEDBACK_ENABLED:
        return CoreFeedback(db)
    from services.match_result_service import MatchResultService

    return MatchResultService(db)


class _SavedItem(BaseModel):
    vacancy_id: uuid.UUID | None
    school_job_id: uuid.UUID | None = None
    job_ref: str | None = None
    feedback: Literal["thumbs_up", "applied"]
    updated_at: datetime
    score_final: FiniteFloat
    scores: dict[str, FiniteFloat]
    explanation: str | None
    content: dict | None
    source: str | None
    url: str | None


class _SavedPage(BaseModel):
    items: list[_SavedItem]
    total: int = Field(ge=0, strict=True)


class _ContextItem(BaseModel):
    identity: str
    title: str
    company: str
    tags: list[str]
    feedback: Literal["thumbs_up", "applied", "thumbs_down", "dismissed"] | None


class _FeedbackContext(BaseModel):
    items: list[_ContextItem] = Field(max_length=50000)


class CoreFeedback:
    def __init__(self, db, client_factory=None):
        self._db = db
        self._client_factory = client_factory or default_client_factory

    async def _profile(self, user_id):
        if settings.CORE_FEEDBACK_ENABLED:
            await require_core_feedback_route(self._db, user_id)
        pid = await resolve_core_profile_id(self._db, user_id)
        if pid is None:
            raise CoreUnavailableError("usuario sin vínculo core para feedback")
        if (
            self._client_factory is default_client_factory
            and not settings.CORE_CONSUMER_KEY
        ):
            raise CoreUnavailableError("CORE_CONSUMER_KEY no configurada")
        return pid

    async def _request(self, method, path, **kwargs):
        try:
            async with self._client_factory() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CoreUnavailableError("core inaccesible para feedback") from exc
        if response.status_code not in (200, 404):
            raise CoreUnavailableError(
                f"core devolvió {response.status_code} para feedback"
            )
        return response

    async def _target(self, job_hash):
        try:
            parsed = uuid.UUID(job_hash)
        except ValueError:
            return None
        if str(parsed) == job_hash.lower():
            return "vacancies", parsed
        if not re.fullmatch(r"[a-fA-F0-9]{32}", job_hash):
            return None
        # School marks remain actionable before a corpus vacancy exists. Use
        # the consumer-scoped observation identity, never fabricate a vacancy.
        response = await self._request(
            "GET", "/school-jobs", params={"dedup_key": job_hash, "limit": 2}
        )
        if response.status_code == 200:
            try:
                page = response.json()
                if (
                    not isinstance(page["items"], list)
                    or page.get("next_cursor") is not None
                ):
                    raise ValueError("ambiguous school identity")
                if len(page["items"]) > 1:
                    raise ValueError("ambiguous school identity")
                if page["items"]:
                    row = page["items"][0]
                    if row["source_ref"] != job_hash:
                        raise ValueError("unexpected school reference")
                    return "school-jobs", uuid.UUID(row["id"])
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise CoreUnavailableError("identidad escolar core inválida") from exc
        response = await self._request(
            "GET", "/listing-references", params={"external_id": job_hash}
        )
        if response.status_code == 404:
            return None
        try:
            return "vacancies", uuid.UUID(response.json()["vacancy_id"])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise CoreUnavailableError("identidad core inválida") from exc

    async def _write(self, user_id, job_hash, kind, body):
        pid = await self._profile(user_id)
        target = await self._target(job_hash)
        if target is None:
            return None
        domain, vid = target
        response = await self._request(
            "PUT" if kind == "feedback" else "POST",
            f"/profiles/{pid}/{domain}/{vid}/{kind}",
            json=body,
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        # El recorrido cacheado por version lleva `state.feedback` dentro:
        # tras esta escritura ya no describe lo que el core sirve, y la
        # version del core (que cubre `updated_at`) cambiara igualmente. Se
        # invalida aqui ademas, para no depender de la siguiente consulta de
        # version — y tambien en el 404, que no prueba que nada cambiara.
        from services.matching.core_client import clear_feed_cache

        clear_feed_cache(pid)
        if response.status_code == 404:
            return None
        try:
            ack = response.json()
            field = "feedback" if kind == "feedback" else "action"
            if uuid.UUID(ack["profile_id"]) != pid or ack[field] != body[field]:
                raise ValueError("unexpected feedback acknowledgement")
            identity = "school_job_id" if domain == "school-jobs" else "vacancy_id"
            acknowledged = uuid.UUID(ack[identity])
            if domain == "school-jobs" and acknowledged != vid:
                raise ValueError("unexpected school acknowledgement")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise CoreUnavailableError("respuesta core inválida para feedback") from exc
        return ack

    async def submit_feedback(self, user_id, job_hash, feedback):
        return await self._write(user_id, job_hash, "feedback", {"feedback": feedback})

    async def clear_feedback(self, user_id, job_hash):
        return await self._write(user_id, job_hash, "feedback", {"feedback": None})

    async def record_implicit_feedback(
        self, user_id, job_hash, action, duration_ms=None
    ):
        body = {"action": action}
        if duration_ms is not None:
            body["duration_ms"] = duration_ms
        return await self._write(user_id, job_hash, "implicit", body)

    async def context(self, user_id):
        """Complete, tenant-scoped evidence for the existing pattern analyzer."""
        pid = await self._profile(user_id)
        # Unlike a feed page this reads the complete history (12k measured on NAS).
        # Core bounds its SQL to 40 s; do not extend timeouts of interactive writes.
        response = await self._request(
            "GET",
            f"/profiles/{pid}/feedback-context",
            timeout=httpx.Timeout(45.0, connect=5.0),
        )
        if response.status_code == 404:
            raise CoreUnavailableError("perfil core no disponible")
        try:
            context = _FeedbackContext.model_validate(response.json())
            seen = set()
            for item in context.items:
                kind, identifier = item.identity.split(":", 1)
                if (
                    kind not in {"vacancy", "school"}
                    or str(uuid.UUID(identifier)) != identifier
                ):
                    raise ValueError("invalid context identity")
                if item.identity in seen:
                    raise ValueError("duplicate context identity")
                seen.add(item.identity)
            return [item.model_dump(exclude={"identity"}) for item in context.items]
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise CoreUnavailableError("payload core inválido para análisis") from exc

    async def saved(self, user_id, limit=100, offset=0):
        pid = await self._profile(user_id)
        response = await self._request(
            "GET",
            f"/profiles/{pid}/feedback",
            params={"limit": limit, "offset": offset},
        )
        if response.status_code == 404:
            raise CoreUnavailableError("perfil core no disponible")
        try:
            page = _SavedPage.model_validate(response.json())
            result = []
            for row in page.items:
                if row.school_job_id is not None:
                    if not row.job_ref or not re.fullmatch(
                        r"[a-fA-F0-9]{32}", row.job_ref
                    ):
                        raise ValueError("missing school reference")
                    job_ref = row.job_ref
                elif row.vacancy_id is not None:
                    job_ref = str(row.vacancy_id)
                else:
                    raise ValueError("saved item without identity")
                vacancy = {
                    **(row.content or {}),
                    "id": str(row.vacancy_id) if row.vacancy_id else None,
                    "primary_listing": {
                        "source": row.source,
                        "url": row.url,
                        "first_seen_at": row.updated_at.isoformat(),
                    },
                }
                item = {
                    "vacancy": vacancy,
                    "state": {"feedback": row.feedback},
                    "evaluation": {
                        "eval_key": f"saved:{pid}:{job_ref}",
                        "score_final": row.score_final,
                        "scores": row.scores,
                        "explanation": row.explanation,
                    },
                }
                _FeedItemDTO.model_validate(item)
                source = row.source.removeprefix("legacy:") if row.source else None
                result.append(
                    {
                        "match": _match_view(item, job_ref, None),
                        "job": _job_view(vacancy, source),
                    }
                )
            if len(result) > limit or len(result) > page.total:
                raise ValueError("invalid feedback page size")
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise CoreUnavailableError("payload core inválido para guardados") from exc
        return result, page.total
