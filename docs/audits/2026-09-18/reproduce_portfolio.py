"""Offline reproductions of observed defects, not passing product regressions.

Run with Portfolio's venv and PYTHONPATH=ReactPortfolio/backend. No real DB,
SMTP, HTTP, personal data or credentials are used. Assertions describe CURRENT
defects; their failure after a fix is expected and requires regression inversion.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from config import settings
from services.schools_core import CoreSchools, CoreSchoolError
from services import school_alert


async def pagination():
    requests = []
    mid = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()
    monitor = {
        "id": str(mid),
        "school_id": str(uuid.uuid4()),
        "external_ref": "audit-school",
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "settings": {
            "name": "Audit school",
            "country": "CH",
            "group_tier": "A",
            "monitoring_mode": "scrape",
            "policy": "portal_only",
            "scraping_method": "groq_extract",
            "is_active": True,
        },
    }

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/schools"):
            return httpx.Response(200, json={"items": [monitor], "next_cursor": None})
        # 101 legitimate entries, page size=1; fetching ONE should need one page.
        page = int(request.url.params.get("cursor", "0"))
        row = {
            "id": str(uuid.UUID(int=page + 1)),
            "monitor_id": str(mid),
            "vacancy_id": None,
            "quarantine_reason": "awaiting_corpus",
            "metadata": {},
            "notified_at": None,
            "created_at": now,
            "updated_at": now,
        }
        return httpx.Response(
            200,
            json={"items": [row], "next_cursor": str(page + 1) if page < 100 else None},
        )

    client = CoreSchools(
        lambda: httpx.AsyncClient(
            base_url="https://core.invalid/v1", transport=httpx.MockTransport(handler)
        )
    )
    with patch.object(settings, "CORE_CONSUMER_KEY", "audit-not-a-real-key"):
        try:
            await client.jobs(limit=1)
        except CoreSchoolError as exc:
            assert str(exc) == "School page budget exhausted", str(exc)
        else:
            raise AssertionError("Defect no longer reproduced: bounded jobs succeeded")
    pages = sum(p.endswith("/school-jobs") for p in requests)
    assert pages == 100, pages
    return {
        "requested_items": 1,
        "corpus_items": 101,
        "http_pages": pages,
        "result": "CoreSchoolError (503)",
    }


async def concurrent_notifications():
    jid = uuid.uuid4()
    school = SimpleNamespace(
        school_id="audit",
        name="Audit school",
        contact_email=None,
        policy=SimpleNamespace(value="portal_only"),
    )

    def job():
        return SimpleNamespace(
            id=jid,
            _core_school_job=True,
            notified=False,
            role_score=1,
            urgency_score=50,
            title="Synthetic vacancy",
            date_detected=datetime.now(timezone.utc),
            url=None,
        )

    arrived = 0
    both = asyncio.Event()

    async def fake_threadpool(*args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 2)
        return True

    store = SimpleNamespace(notified=AsyncMock())
    with (
        patch.object(settings, "writes_frozen", False),
        patch.object(settings, "EMAIL_RECEIVER", "audit@example.invalid"),
        patch.object(settings, "SMTP_HOST", "smtp.invalid"),
        patch.object(school_alert, "run_in_threadpool", fake_threadpool),
        patch.object(school_alert.sse_manager, "broadcast", AsyncMock()),
        patch.object(school_alert, "school_store", AsyncMock(return_value=store)),
    ):
        await asyncio.gather(
            school_alert.dispatch_alert(job(), school, AsyncMock()),
            school_alert.dispatch_alert(job(), school, AsyncMock()),
        )
    assert arrived == 2 and store.notified.await_count == 2
    return {"same_job": True, "attempted_emails": arrived, "real_emails": 0}


async def main():
    print(
        json.dumps(
            {
                "pagination": await pagination(),
                "concurrent_notifications": await concurrent_notifications(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
