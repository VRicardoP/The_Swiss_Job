"""Existing pattern endpoint on isolated copies, including core failure safety."""

import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

ROOT = Path("/evidence")
state = json.loads((ROOT / "feedback-context.private.json").read_text())
assert (
    state["env"]["DATABASE_URL"]
    == "postgresql+asyncpg://swissjob@127.0.0.1:5432/source_copy"
)
assert state["env"]["CORE_API_BASE_URL"] == "http://127.0.0.1:18080/v1"
os.environ.update(state["env"])

# Estos imports van DESPUÉS del os.environ.update de arriba a propósito, y por eso
# llevan el noqa: `config`, `database` y `main` leen el entorno AL IMPORTARSE.
# Subirlos al principio del fichero los dejaría mirando un entorno vacío, que es
# justo lo contrario de lo que el ensayo quiere comprobar.
import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from config import settings  # noqa: E402
from core.security import create_access_token  # noqa: E402
from database import async_session, engine  # noqa: E402
from main import app  # noqa: E402
from services.matching.feedback import CoreFeedback  # noqa: E402


async def fingerprint(table):
    assert table in {"match_results", "pattern_suggestions"}
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id")
                )
            )
            .scalars()
            .all()
        )
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


async def main():
    assert settings.CORE_FEEDBACK_ENABLED
    before = await fingerprint("match_results")
    counts = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://copy-bff"
    ) as client:
        for uid, _ in state["bindings"]:
            headers = {"Authorization": "Bearer " + create_access_token(uuid.UUID(uid))}
            async with async_session() as session:
                jobs = await CoreFeedback(session).context(uuid.UUID(uid))
            rejected = sum(j["feedback"] in {"thumbs_down", "dismissed"} for j in jobs)
            response = await client.post(
                "/api/v1/analytics/analyze", headers=headers, json={"min_rejected": 2}
            )
            assert response.status_code == 200, "pattern analysis unavailable"
            assert response.json()["rejected_jobs_analyzed"] == rejected
            counts.append({"context": len(jobs), "rejected": rejected})
        assert any(row["rejected"] > 0 for row in counts), "vacuous rejection check"
        proposals = await fingerprint("pattern_suggestions")
        settings.CORE_API_BASE_URL = "http://127.0.0.1:18081/v1"
        response = await client.post(
            "/api/v1/analytics/analyze", headers=headers, json={"min_rejected": 2}
        )
        assert response.status_code == 503, "unavailable core silently fell back"
        assert await fingerprint("pattern_suggestions") == proposals, (
            "failure changed proposals"
        )
    assert await fingerprint("match_results") == before, "local feedback changed"
    print(
        json.dumps(
            {
                "context": counts,
                "failure_preserves_proposals": True,
                "local_feedback_unchanged": True,
                "production_changed": False,
            }
        )
    )


async def run():
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "verified": False,
                    "type": type(exc).__name__,
                    "check": str(exc)
                    if type(exc) is AssertionError
                    else "copy-only check failed",
                }
            )
        )
        raise SystemExit(1) from None
