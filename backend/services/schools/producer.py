"""Existing Swiss scrapers consume core monitors and publish school observations.

No new crawler or scheduler. Only hashes persisted from THIS live extraction
may publish missing vacancies. Historical rows may link to an existing corpus.
Delivery precedes the local cursor commit: an RPC failure rolls that cursor back
and the next fetch replays safely against the core's observation identity.
"""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import load_only

from config import settings
from models.job import Job
from scrapers.swiss_schools_config import WatchedSchool
from .http_client import SchoolClient
from .port import CoreUnavailableError
from .state import state_on_core

_GROUPS = {
    "swiss_schools_nae": "nae_central",
    "swiss_schools_isp": "isp_workday",
    "swiss_schools_inspired": "inspired_sf",
}


class SchoolProducer:
    def __init__(self, db, client=None):
        self.db = db
        self.client = client or SchoolClient()
        self.monitors = None
        self.observations = None

    async def prepare(self, scraper):
        if not getattr(scraper, "WATCHLIST_SOURCE", False):
            return True
        if settings.SCHOOL_WRITES_FROZEN:
            return False
        if not await state_on_core(self.db):
            return True
        if self.monitors is None:
            self.monitors = await self.client.monitors()
        active = []
        try:
            for row in self.monitors:
                data = row.settings
                if not data.get("is_active") or data.get("monitoring_mode") != "scrape":
                    continue
                active.append(
                    WatchedSchool(
                        id=row.external_ref,
                        name=data["name"],
                        city=data.get("city") or "",
                        careers_url=data.get("jobs_page_url") or "",
                        strategy=data.get("scraping_method") or "manual",
                        params=data.get("scraping_params"),
                        group_tier=data["group_tier"],
                        policy=data["policy"],
                        contact_email=data.get("contact_email"),
                        contact_name=data.get("contact_name"),
                        template_id=data.get("template_letter") or "A",
                        application_url=data.get("portal_url"),
                        notes=data.get("notes"),
                    )
                )
        except (KeyError, TypeError, ValueError):
            raise CoreUnavailableError("invalid school scraper configuration") from None
        strategy = _GROUPS.get(scraper.get_source_name())
        if strategy:
            scraper._schools = [
                school for school in active if school.strategy == strategy
            ]
            scraper._core_school_configuration = True
            return bool(scraper._schools)
        original = getattr(scraper, "_school", None)
        scraper._school = next(
            (school for school in active if original and school.id == original.id), None
        )
        if scraper._school is None:
            return False
        scraper.LISTING_URL = scraper._school.careers_url
        return True

    async def reconcile(self, scraper, *, live_hashes=frozenset()):
        if not getattr(scraper, "WATCHLIST_SOURCE", False) or self.monitors is None:
            return
        if settings.SCHOOL_WRITES_FROZEN or not await state_on_core(
            self.db, write=True
        ):
            raise CoreUnavailableError("school observation authority changed")
        by_ref = {m.external_ref: m for m in self.monitors}
        if self.observations is None:
            self.observations = {
                row["source_ref"]
                for row in await self.client.pages("/school-jobs")
                if row.get("quarantine_reason")
                not in ("source_inactive", "awaiting_corpus")
            }
        rows = (
            (
                await self.db.execute(
                    select(Job)
                    .options(
                        load_only(
                            Job.hash,
                            Job.tags,
                            Job.title,
                            Job.url,
                            Job.description,
                            Job.source,
                            Job.first_seen_at,
                            Job.published_at,
                        )
                    )
                    .where(
                        Job.source == scraper.get_source_name(), Job.is_active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        # Routing lock remains held during the short, bounded RPCs. Freeze/drain
        # precedes flips; no crawler or LLM runs inside this critical section.
        for job in rows:
            if job.hash in self.observations and job.hash not in live_hashes:
                continue
            monitor = next(
                (by_ref[tag] for tag in job.tags or [] if tag in by_ref), None
            )
            if monitor is None:
                raise CoreUnavailableError("persisted school job has no monitor")
            payload = {
                "publish_missing": job.hash in live_hashes,
                "title": job.title,
                "url": job.url,
                "description_snippet": (job.description or "")[:65536],
                "content_hash": hashlib.sha256(
                    (job.title + (job.description or "")).encode()
                ).hexdigest(),
                "dedup_key": job.hash,
                "source": job.source,
                "date_detected": job.first_seen_at.isoformat(),
                "date_posted": (
                    job.published_at.isoformat() if job.published_at else None
                ),
            }
            response = await self.client.request(
                "POST",
                f"/schools/{monitor.id}/jobs",
                json=payload,
                headers={"Idempotency-Key": "school-job:" + uuid.uuid4().hex},
            )
            body = response.json()
            item = body.get("item", {})
            if (
                type(body.get("created")) is not bool
                or item.get("source_ref") != job.hash
                or item.get("monitor_id") != str(monitor.id)
            ):
                raise CoreUnavailableError("school observation identity mismatch")
            self.observations.add(job.hash)
