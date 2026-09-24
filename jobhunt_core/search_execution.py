"""Transactional execution of explicitly transferred saved searches.

The corpus query, observations, counters and outbox share one transaction.
No Redis marker and no advancing timestamp cursor: a late commit is observed
on the next pass, even if its creation timestamp predates the previous run.
All presentable vacancies are observed, including nonmatches, so a later
filter edit does not manufacture novelty from old offers.

No search executes merely because it was imported or has is_active=true.
The handover must configure its dialect/floor and explicitly enable it only
after the old executor is drained and the consumer's inbox is verified.
"""

from datetime import datetime, timedelta
from collections.abc import Collection

import sqlalchemy as sa

from jobhunt_core import outbox
from jobhunt_core.saved_search_query import SwissJobSearchFilters, matching_query

CONTRACT = "swissjob-v1"
MATCH_EVENT = "saved_search.matches"
INTERVALS = {
    "realtime": timedelta(minutes=5),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


async def _lock_search(session, search_id):
    """Profile root -> search -> execution, same root order as writes/erase.

    The initial owner lookup is only a hint; ownership and active state are
    rechecked after acquiring the root. No corpus row locks are acquired.
    """
    pid = await session.scalar(
        sa.text("SELECT profile_id FROM saved_searches WHERE id=:id"), {"id": search_id}
    )
    if pid is None:
        return None
    owner = (
        await session.execute(
            sa.text("""
        SELECT p.id, p.projection_active, c.name AS destination, c.active
        FROM profiles p JOIN consumers c ON c.id=p.consumer_id
        WHERE p.id=:pid FOR SHARE OF p
    """),
            {"pid": pid},
        )
    ).one_or_none()
    if owner is None or not owner.active or not owner.projection_active:
        return None
    search = (
        await session.execute(
            sa.text("""
        SELECT * FROM saved_searches WHERE id=:id AND profile_id=:pid FOR UPDATE
    """),
            {"id": search_id, "pid": pid},
        )
    ).one_or_none()
    return (search, owner.destination) if search is not None else None


async def configure_execution(
    session, search_id, *, contract: str, notify_since: datetime, enabled: bool = False
):
    """Cutover primitive: explicit authority, immutable floor, no implicit commit.

    Import must preserve aliases/history and seed already observed identities
    before enabling. Reconfiguring the same contract/floor is idempotent;
    disabling preserves observations. It never resets state on a retry.
    """
    if contract != CONTRACT:
        raise ValueError("unsupported saved-search execution contract")
    if not isinstance(notify_since, datetime) or notify_since.tzinfo is None:
        raise ValueError("notify_since must have a timezone")
    if type(enabled) is not bool:
        raise ValueError("enabled must be boolean")
    locked = await _lock_search(session, search_id)
    if locked is None:
        raise ValueError("saved search or active owner not found")
    search, _ = locked
    SwissJobSearchFilters.model_validate(search.filters)
    now = await session.scalar(sa.text("SELECT clock_timestamp()"))
    if notify_since > now:
        raise ValueError("notify_since cannot be in the future")
    previous = (
        await session.execute(
            sa.text("""
        SELECT contract, notify_since FROM saved_search_execution
        WHERE saved_search_id=:id FOR UPDATE
    """),
            {"id": search_id},
        )
    ).one_or_none()
    if previous is not None:
        if previous.contract != contract or previous.notify_since != notify_since:
            raise ValueError(
                "execution contract/floor already fixed; reconcile explicitly"
            )
        await session.execute(
            sa.text("""
            UPDATE saved_search_execution SET enabled=:enabled WHERE saved_search_id=:id
        """),
            {"id": search_id, "enabled": enabled},
        )
    else:
        await session.execute(
            sa.text("""
            INSERT INTO saved_search_execution(saved_search_id,contract,notify_since,enabled)
            VALUES(:id,:contract,:since,:enabled)
        """),
            {
                "id": search_id,
                "contract": contract,
                "since": notify_since,
                "enabled": enabled,
            },
        )


async def execute_search(
    session, search_id, *, destinations: Collection[str], force: bool = False
) -> dict:
    """Execute once; caller commits or rolls back the ENTIRE result.

    `force` bypasses frequency only, never disabled authority/inactive owners.
    Serializes with edits, delete, reconfiguration and a second executor.
    min_score stays inert as in the existing SwissJob filter-only search.
    """
    locked = await _lock_search(session, search_id)
    if type(force) is not bool:
        raise ValueError("force must be boolean")
    if locked is None:
        return {"status": "not_found", "observed": 0, "matches": 0}
    search, destination = locked
    config = (
        await session.execute(
            sa.text("""
        SELECT * FROM saved_search_execution WHERE saved_search_id=:id FOR UPDATE
    """),
            {"id": search_id},
        )
    ).one_or_none()
    if config is None or not config.enabled or not search.is_active:
        return {"status": "disabled", "observed": 0, "matches": 0}
    if config.contract != CONTRACT:
        raise ValueError("unsupported saved-search execution contract")
    if destination not in destinations:
        raise ValueError("saved-search destination has no HTTP inbox configured")
    now = await session.scalar(sa.text("SELECT clock_timestamp()"))
    interval = INTERVALS[str(search.notify_frequency)]
    if (
        not force
        and search.last_run_at is not None
        and now - search.last_run_at < interval
    ):
        return {"status": "not_due", "observed": 0, "matches": 0}
    classified, params = matching_query(search.filters, include_nonmatches=True)
    # The INSERT and classification use ONE statement snapshot. No separate
    # scan can consume a vacancy while another scan fails to classify it.
    counts = (
        await session.execute(
            sa.text(f"""
        WITH RECURSIVE consumed(vacancy_id) AS (
            SELECT v.merged_into FROM saved_search_observations seen
            JOIN vacancies v ON v.id=seen.vacancy_id
            WHERE seen.saved_search_id=:sid AND v.merged_into IS NOT NULL
            UNION
            SELECT v.merged_into FROM consumed c JOIN vacancies v ON v.id=c.vacancy_id
            WHERE v.merged_into IS NOT NULL
        ), classified AS ({classified}), inserted AS (
            INSERT INTO saved_search_observations(saved_search_id,vacancy_id,matched)
            SELECT :sid, vacancy_id, matches AND created_at > :since
                AND NOT EXISTS (SELECT 1 FROM consumed c WHERE c.vacancy_id=classified.vacancy_id)
            FROM classified WHERE true ON CONFLICT DO NOTHING RETURNING matched
        )
        SELECT count(*) AS observed, count(*) FILTER (WHERE matched) AS matches FROM inserted
    """),
            {**params, "sid": search.id, "since": config.notify_since},
        )
    ).one()
    await session.execute(
        sa.text("""
        UPDATE saved_searches SET last_run_at=:now, total_matches=total_matches+:matches,
            revision=revision+1, updated_at=:now WHERE id=:id
    """),
        {"id": search.id, "now": now, "matches": counts.matches},
    )
    run = config.run_number + 1
    await session.execute(
        sa.text("""
        UPDATE saved_search_execution SET run_number=:run,last_attempt_at=:now
        WHERE saved_search_id=:id
    """),
        {"id": search.id, "run": run, "now": now},
    )
    if counts.matches:
        await outbox.emit(
            session,
            event_type=MATCH_EVENT,
            natural_key=f"{search.id}:run:{run}",
            aggregate="saved_search",
            aggregate_id=str(search.id),
            subject_profile_id=search.profile_id,
            version=1,
            payload={
                "search_id": str(search.id),
                "profile_id": str(search.profile_id),
                "search_name": search.name,
                "match_count": counts.matches,
                "notify_push": search.notify_push,
            },
            destination=destination,
        )
    return {"status": "ok", "observed": counts.observed, "matches": counts.matches}


async def due_search_ids(session, *, limit: int = 100):
    """Fair bounded sweep; locks are taken only when each aggregate executes."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid search batch limit")
    return (
        (
            await session.execute(
                sa.text("""
        SELECT ss.id FROM saved_searches ss
        JOIN saved_search_execution e ON e.saved_search_id=ss.id
        JOIN profiles p ON p.id=ss.profile_id JOIN consumers c ON c.id=p.consumer_id
        WHERE e.enabled AND ss.is_active AND p.projection_active AND c.active
          AND (ss.last_run_at IS NULL OR ss.last_run_at <= clock_timestamp() -
            CASE ss.notify_frequency
              WHEN 'realtime' THEN interval '5 minutes'
              WHEN 'daily' THEN interval '1 day'
              WHEN 'weekly' THEN interval '7 days' END)
        ORDER BY e.last_attempt_at NULLS FIRST, ss.id LIMIT :limit
    """),
                {"limit": limit},
            )
        )
        .scalars()
        .all()
    )


async def record_failed_attempt(session, search_id):
    """Fairness only, after rollback; never consumes data or advances last_run.

    No payload or error text is retained. A concurrent delete safely yields 0.
    This metadata update takes no profile/corpus lock and cannot invert them.
    """
    await session.execute(
        sa.text("""
        UPDATE saved_search_execution SET last_attempt_at=clock_timestamp()
        WHERE saved_search_id=:id
    """),
        {"id": search_id},
    )
