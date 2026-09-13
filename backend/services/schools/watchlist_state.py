"""Core-owned watchlist state; public job data remains the BFF presentation input."""

from types import SimpleNamespace
from datetime import datetime

from sqlalchemy import select

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
        job = (
            await self._db.execute(select(Job).where(Job.hash == job_hash))
        ).scalar_one_or_none()
        pid = await self._profile(user_id)
        rows = await self._client.states(pid, job_hash)
        if job is None:
            if not rows:
                return None
            state = rows[0]
            monitor = next(
                (m for m in await self._client.monitors() if m.id == state.monitor_id),
                None,
            )
            if monitor is None or not state.context.get("job_title"):
                raise CoreUnavailableError("school history presentation is incomplete")
            job = SimpleNamespace(
                title=state.context["job_title"],
                company=state.context.get("job_company"),
                url=state.context.get("job_url"),
                location=None,
                language=None,
                tags=[monitor.external_ref],
            )
        if rows:
            row = rows[0]
            detected = row.context.get("detected_at")
            try:
                created_at = (
                    datetime.fromisoformat(detected) if detected else row.created_at
                )
                if created_at.tzinfo is None:
                    raise ValueError("naive timestamp")
            except (ValueError, TypeError):
                raise CoreUnavailableError(
                    "invalid school detection timestamp"
                ) from None
            match = SimpleNamespace(
                application_status=row.status,
                draft_letter=row.draft_content,
                created_at=created_at,
            )
        else:
            local = (
                await self._db.execute(
                    select(MatchResult).where(
                        MatchResult.user_id == user_id, MatchResult.job_hash == job_hash
                    )
                )
            ).scalar_one_or_none()
            # Status/draft NEVER fall back to local after the cutover. The old
            # detection time is presentation only and is preserved on first write.
            match = SimpleNamespace(
                application_status="detected",
                draft_letter=None,
                created_at=local.created_at if local else job.first_seen_at,
            )
        return match, job

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
