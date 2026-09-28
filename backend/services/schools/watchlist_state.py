"""Core-owned watchlist state; public job data remains the BFF presentation input."""

from types import SimpleNamespace
from datetime import datetime
import uuid

from sqlalchemy import select

from config import settings
from models.job import Job
from models.match_result import MatchResult
from services.matching.identity import resolve_core_profile_id
from .http_client import SchoolClient
from .port import CoreUnavailableError


class CoreWatchlist:
    def __init__(self, db, client=None):
        self._db = db
        self._client = client or SchoolClient()

    async def _profile(self, user_id):
        pid = await resolve_core_profile_id(self._db, user_id)
        if pid is None:
            raise CoreUnavailableError("school profile is not enrolled")
        return pid

    async def get_match(self, user_id, job_hash):
        job = None
        if not settings.CORE_FEEDBACK_ENABLED:
            job = (
                await self._db.execute(select(Job).where(Job.hash == job_hash))
            ).scalar_one_or_none()
        pid = await self._profile(user_id)
        rows = await self._client.states(pid, job_hash)
        if job is None and not rows:
            job = await self._job_from_native_observation(job_hash)
            if job is None:
                return None
        elif job is None:
            job = await self._job_from_state(rows[0])
        if rows:
            match = self._match_from_row(rows[0])
        else:
            match = await self._match_detected(user_id, job_hash, job)
        return match, job

    async def _monitor(self, monitor_id):
        return next(
            (m for m in await self._client.monitors() if m.id == monitor_id), None
        )

    async def _job_from_native_observation(self, job_hash):
        """Oferta escolar que NO tiene fila local ni estado: se resuelve por su
        `dedup_key` con una sola petición (A18-02). `None` = no existe."""
        response = await self._client.request(
            "GET", "/school-jobs", params={"dedup_key": job_hash, "limit": 2}
        )
        if response.status_code == 404:
            return None
        try:
            page = response.json()
            if (
                not isinstance(page["items"], list)
                or page.get("next_cursor") is not None
                or len(page["items"]) > 1
            ):
                raise ValueError("ambiguous school reference")
            if not page["items"]:
                return None
            observation = page["items"][0]
            if observation["source_ref"] != job_hash:
                raise ValueError("unexpected school reference")
            jid, mid = (
                uuid.UUID(observation["id"]),
                uuid.UUID(observation["monitor_id"]),
            )
            monitor = await self._monitor(mid)
            if monitor is None:
                raise ValueError("missing school monitor")
            metadata = observation["metadata"]
            title, url = metadata["title"], metadata.get("url")
            if (
                not isinstance(title, str)
                or not title
                or (url is not None and not isinstance(url, str))
            ):
                raise ValueError("invalid school presentation")
            detected = datetime.fromisoformat(
                metadata.get("date_detected") or observation["created_at"]
            )
            if detected.tzinfo is None:
                raise ValueError("naive school timestamp")
            return SimpleNamespace(
                title=title,
                company=monitor.settings.get("name"),
                url=url,
                location=None,
                language=None,
                tags=[monitor.external_ref],
                first_seen_at=detected,
                school_job_id=jid,
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CoreUnavailableError("invalid native school observation") from None

    async def _job_from_state(self, state):
        """Oferta escolar reconstruida desde el contexto de su estado (sin fila
        local): el historial tiene que traer título, o no es presentable."""
        monitor = await self._monitor(state.monitor_id)
        if monitor is None or not state.context.get("job_title"):
            raise CoreUnavailableError("school history presentation is incomplete")
        return SimpleNamespace(
            title=state.context["job_title"],
            company=state.context.get("job_company"),
            url=state.context.get("job_url"),
            location=None,
            language=None,
            tags=[monitor.external_ref],
            school_job_id=state.school_job_id,
        )

    @staticmethod
    def _match_from_row(row):
        detected = row.context.get("detected_at")
        try:
            created_at = (
                datetime.fromisoformat(detected) if detected else row.created_at
            )
            if created_at.tzinfo is None:
                raise ValueError("naive timestamp")
        except (ValueError, TypeError):
            raise CoreUnavailableError("invalid school detection timestamp") from None
        return SimpleNamespace(
            application_status=row.status,
            draft_letter=row.draft_content,
            created_at=created_at,
        )

    async def _match_detected(self, user_id, job_hash, job):
        """Sin estado en el core: la candidatura está «detectada», con la fecha
        del match local si existe (modo legacy) o la de detección de la oferta."""
        local = None
        if not settings.CORE_FEEDBACK_ENABLED:
            local = (
                await self._db.execute(
                    select(MatchResult).where(
                        MatchResult.user_id == user_id,
                        MatchResult.job_hash == job_hash,
                    )
                )
            ).scalar_one_or_none()
        return SimpleNamespace(
            application_status="detected",
            draft_letter=None,
            created_at=local.created_at if local else job.first_seen_at,
        )

    async def _write(self, user_id, job_hash, changes):
        pair = await self.get_match(user_id, job_hash)
        if pair is None:
            return False
        match, job = pair
        monitors = await self._client.monitors()
        by_ref = {m.external_ref: m for m in monitors}
        monitor = next((by_ref[tag] for tag in (job.tags or []) if tag in by_ref), None)
        if monitor is None:
            return False
        pid = await self._profile(user_id)
        await self._client.write_state(
            pid,
            job_hash,
            monitor.id,
            changes,
            school_job_id=getattr(job, "school_job_id", None),
            context={
                "detected_at": match.created_at.isoformat(),
                "job_title": job.title,
                "job_company": job.company,
                "job_url": job.url,
            },
        )
        return True

    async def set_match_status(self, user_id, job_hash, application_status):
        return await self._write(user_id, job_hash, {"status": application_status})

    async def save_draft(self, user_id, job_hash, draft):
        return await self._write(user_id, job_hash, {"draft_content": draft})

    async def get_draft(self, user_id, job_hash):
        rows = await self._client.states(await self._profile(user_id), job_hash)
        return rows[0].draft_content if rows else None
