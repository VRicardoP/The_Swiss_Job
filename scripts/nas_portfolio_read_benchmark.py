"""Bounded read-only NAS latency probe. Execute inside portfolio_backend.

No login/password dump, LLM, scrape, POST, or data output. A short-lived token
stays in this process. These measurements cover served reads, not write/PDF
throughput or a capacity certification. Do not extrapolate empty collections.
"""
import asyncio
from collections import Counter
from datetime import timedelta, datetime, timezone
import json
import math
import os
import time

import httpx
from sqlalchemy import text
from config import settings
from core.security import create_access_token
from database import AsyncSessionLocal, async_engine
from models.user import User


async def main():
    async with AsyncSessionLocal() as db:
        owner = await db.get(User, settings.CORE_DOCUMENT_OWNER_USER_ID)
        if owner is None or not owner.is_active or not owner.is_admin:
            raise RuntimeError("verified active operator binding required")
        token = create_access_token({"sub": owner.username}, timedelta(minutes=10))
        version = await db.scalar(text("SELECT version_num FROM alembic_version"))
    output = {
        "at": datetime.now(timezone.utc).isoformat(),
        "release": os.environ.get("PORTFOLIO_RELEASE"), "revision": version,
        "concurrency": 2, "samples_per_endpoint": 12, "measurements": {},
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": "Bearer " + token}) as client:
        for path in ("/health", "/api/v1/cv-generation/", "/api/v1/applications/", "/api/v1/saved-searches/"):
            values, statuses, lengths = [], Counter(), set()
            async def request():
                start = time.perf_counter()
                try:
                    response = await client.get("http://127.0.0.1:8000" + path)
                except httpx.TimeoutException:
                    print(json.dumps({"failed_path": path, "reason": "timeout_30s",
                                      "completed": output}), flush=True)
                    raise RuntimeError(f"read timeout: {path}") from None
                duration = (time.perf_counter() - start) * 1000
                data = response.json()
                if isinstance(data, list):
                    lengths.add(len(data))
                return duration, response.status_code
            cold, status = await request()
            if status != 200:
                raise RuntimeError(f"preflight failed: {path} status={status}")
            started = time.perf_counter()
            for _ in range(6):
                for elapsed, status in await asyncio.gather(request(), request()):
                    values.append(elapsed)
                    statuses[status] += 1
                if any(s != 200 for s in statuses):
                    raise RuntimeError(f"stopping on non-200: {path}")
                await asyncio.sleep(0.1)  # deliberately gentle, not a stress test
            total = time.perf_counter() - started
            ordered = sorted(values)
            output["measurements"][path] = {
                "cold_ms": round(cold, 2),
                "p50_ms": round(ordered[math.ceil(.50 * len(ordered)) - 1], 2),
                "p95_ms": round(ordered[math.ceil(.95 * len(ordered)) - 1], 2),
                "max_ms": round(max(values), 2),
                "wall_seconds_including_pacing": round(total, 3),
                "statuses": dict(statuses), "collection_sizes": sorted(lengths),
            }
            print(json.dumps({"endpoint": path, **output["measurements"][path]}), flush=True)
    print(json.dumps(output, sort_keys=True))


async def run():
    try:
        await main()
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
