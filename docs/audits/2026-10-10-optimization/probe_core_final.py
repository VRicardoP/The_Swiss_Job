#!/usr/bin/env python3
"""Read-only final Core gate; run through stdin inside core-api."""

import asyncio
import json
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import text

from jobhunt_core.database import SessionLocal
from jobhunt_core.delivery import stats as delivery_stats
from jobhunt_core.tasks.harvest import _check_harvest_health_impl


JSEARCH_WAIVER_AT = datetime(2026, 10, 15, 15, 54, 15, tzinfo=timezone.utc)


async def main() -> int:
    with urllib.request.urlopen(
        "http://127.0.0.1:8000/v1/ready", timeout=10
    ) as response:
        ready = json.load(response)
    health = await _check_harvest_health_impl()
    async with SessionLocal() as session:
        delivery = await delivery_stats(session)
        pending_staging = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL"
                    )
                )
            ).scalar_one()
        )
        capture_slot_exists = bool(
            (
                await session.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_replication_slots "
                        "WHERE slot_name='jobhunt_shadow_r5_rehearsal')"
                    )
                )
            ).scalar_one()
        )
        jsearch = (
            await session.execute(
                text(
                    "SELECT count(DISTINCT hs.id) AS scopes, "
                    "count(DISTINCT hs.id) FILTER (WHERE hs.enabled) AS enabled, "
                    "count(DISTINCT hs.id) FILTER (WHERE NOT hs.enabled "
                    "  AND sss.disabled_by_blocks_at IS NULL) AS manually_disabled, "
                    "count(DISTINCT hs.id) FILTER "
                    "  (WHERE sss.disabled_by_blocks_at IS NOT NULL) "
                    "  AS disabled_by_blocks, "
                    "count(shr.run_id) FILTER (WHERE shr.started_at >= :waiver_at) "
                    "  AS runs_since_waiver "
                    "FROM sources s "
                    "JOIN harvest_scopes hs ON hs.source_id = s.id "
                    "LEFT JOIN source_scope_state sss ON sss.scope_id = hs.id "
                    "LEFT JOIN source_harvest_runs shr ON shr.scope_id = hs.id "
                    "WHERE s.name = 'jsearch'"
                ),
                {"waiver_at": JSEARCH_WAIVER_AT},
            )
        ).one()

    result = {
        "ready": {
            "status": ready.get("status"),
            "release": ready.get("release"),
            "alembic": ready.get("alembic"),
            "authoritative": ready.get("authoritative"),
        },
        "harvest": {
            "observed": health.get("scopes"),
            "enabled": health.get("censo", {}).get("habilitados"),
            "with_state": health.get("censo", {}).get("con_estado"),
            "disabled_by_blocks": health.get("censo", {}).get("apagados_por_bloqueo"),
            "alerts": health.get("alertas", []),
        },
        "jsearch_waiver": {
            "scopes": int(jsearch.scopes),
            "enabled": int(jsearch.enabled),
            "manually_disabled": int(jsearch.manually_disabled),
            "disabled_by_blocks": int(jsearch.disabled_by_blocks),
            "runs_since_waiver": int(jsearch.runs_since_waiver),
        },
        "delivery": delivery,
        "pending_staging": pending_staging,
        "capture_slot_exists": capture_slot_exists,
    }
    failures = []
    if ready.get("status") != "ready" or ready.get("authoritative") is not True:
        failures.append("core_not_ready_or_authoritative")
    if result["harvest"] != {
        "observed": 49,
        "enabled": 49,
        "with_state": 49,
        "disabled_by_blocks": 0,
        "alerts": [],
    }:
        failures.append("harvest_not_49_of_49")
    if result["jsearch_waiver"] != {
        "scopes": 1,
        "enabled": 0,
        "manually_disabled": 1,
        "disabled_by_blocks": 0,
        "runs_since_waiver": 0,
    }:
        failures.append("jsearch_waiver_not_enforced")
    if delivery.get("dead_total") != 0 or any(
        delivery.get("by_state", {}).get(state, 0) for state in ("pending", "inflight")
    ):
        failures.append("delivery_not_drained")
    if pending_staging:
        failures.append("capture_staging_not_drained")
    if capture_slot_exists:
        failures.append("capture_slot_still_exists")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
