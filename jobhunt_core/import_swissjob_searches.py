"""Frozen search handover preserving public IDs and every mutable preference.

The caller freezes/drains source writers, seals this plan with private_write,
then revalidates the source before apply. A retry accepts only the WHOLE before
or after image. Revert is pre-activation only, never a rollback over new alerts.
No corpus writes, network, routing changes or implicit commits.
"""

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from jobhunt_core.import_schools import canonical, digest
from jobhunt_core.saved_search_query import SwissJobSearchFilters, matching_vacancies


class SearchMigrationError(ValueError):
    pass


FIELDS = (
    "id",
    "profile_id",
    "name",
    "filters",
    "min_score",
    "notify_frequency",
    "notify_push",
    "is_active",
    "last_run_at",
    "total_matches",
    "created_at",
    "updated_at",
    "revision",
)
DATES = {"last_run_at", "created_at", "updated_at"}
FLOOR = "1970-01-01T00:00:00+00:00"


def _wire(value):
    return json.loads(canonical(value))


async def _lock_profiles(session, consumer, bindings):
    for pid in sorted(bindings.values()):
        found = await session.scalar(
            sa.text(
                "SELECT p.id FROM profiles p JOIN consumers c ON c.id=p.consumer_id "
                "WHERE p.id=:p AND c.name=:c FOR UPDATE OF p"
            ),
            {"p": uuid.UUID(pid), "c": consumer},
        )
        if found is None:
            raise SearchMigrationError("profile ownership changed")


async def _read(session, pids):
    rows = (
        (
            await session.execute(
                sa.text(
                    "SELECT " + ",".join(FIELDS) + " FROM saved_searches "
                    "WHERE profile_id=ANY(:pids) ORDER BY id FOR UPDATE"
                ),
                {"pids": [uuid.UUID(p) for p in pids]},
            )
        )
        .mappings()
        .all()
    )
    return _wire([dict(row) for row in rows])


async def prepare_plan(
    session, *, consumer, bindings, rows, targets, pending, recorded_at
):
    """targets explicitly maps each public source UUID to its existing core UUID.

    No name-based matching here. `pending` is source search UUID -> exact core
    vacancies still owed an alert by the frozen legacy query/Redis markers.
    Seed the current corpus minus those pending identities; its original core
    insertion timestamps are NOT the legacy first_seen_at. The fixed epoch floor
    preserves owed alerts even when their core timestamp predates migration.
    """
    stamp = datetime.fromisoformat(recorded_at)
    if stamp.tzinfo is None:
        raise SearchMigrationError("aware handover timestamp required")
    bindings = {str(uuid.UUID(u)): str(uuid.UUID(p)) for u, p in bindings.items()}
    targets = {str(uuid.UUID(u)): str(uuid.UUID(p)) for u, p in targets.items()}
    if not bindings or len(set(bindings.values())) != len(bindings):
        raise SearchMigrationError("invalid bindings")
    if len(set(targets.values())) != len(targets):
        raise SearchMigrationError("ambiguous target mapping")
    await _lock_profiles(session, consumer, bindings)
    before = await _read(session, bindings.values())
    by_id = {row["id"]: row for row in before}
    if set(targets.values()) != set(by_id):
        raise SearchMigrationError("target inventory is not complete")
    configured = await session.scalar(
        sa.text(
            "SELECT count(*) FROM saved_search_execution e JOIN saved_searches s ON s.id=e.saved_search_id "
            "WHERE s.profile_id=ANY(:pids)"
        ),
        {"pids": [uuid.UUID(p) for p in bindings.values()]},
    )
    if configured:
        raise SearchMigrationError("search execution already configured")
    corpus = sorted(
        str(v)
        for v in (
            await session.execute(
                sa.text(
                    "SELECT id FROM vacancies WHERE archived_at IS NULL AND merged_into IS NULL "
                    "AND current_offer_revision_id IS NOT NULL"
                )
            )
        ).scalars()
    )
    corpus_set = set(corpus)
    after, seen, normalized_pending = [], set(), {}
    for source in rows:
        sid, uid = (
            str(uuid.UUID(str(source["id"]))),
            str(uuid.UUID(str(source["user_id"]))),
        )
        if sid in seen or sid not in targets or uid not in bindings:
            raise SearchMigrationError("duplicate or unbound source search")
        seen.add(sid)
        previous = by_id[targets[sid]]
        if previous["profile_id"] != bindings[uid]:
            raise SearchMigrationError("target search belongs to another profile")
        if sid in by_id and sid != targets[sid]:
            raise SearchMigrationError("source identity collides with another target")
        SwissJobSearchFilters.model_validate(source["filters"])
        name = source["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 200 or "\x00" in name:
            raise SearchMigrationError("invalid source name")
        name.encode("utf-8")
        if source["notify_frequency"] not in {"daily", "weekly", "realtime"}:
            raise SearchMigrationError("invalid source frequency")
        for field in ("notify_push", "is_active"):
            if type(source[field]) is not bool:
                raise SearchMigrationError("invalid source flag")
        for field, maximum in (("min_score", 100), ("total_matches", 2**31 - 1)):
            if type(source[field]) is not int or not 0 <= source[field] <= maximum:
                raise SearchMigrationError("invalid source counter")
        wanted = sorted({str(uuid.UUID(str(v))) for v in pending.get(sid, [])})
        if wanted:
            eligible = {
                str(row.vacancy_id)
                for row in await matching_vacancies(session, source["filters"])
            }
            if not set(wanted) <= corpus_set & eligible:
                raise SearchMigrationError(
                    "pending source alerts do not match eligible core corpus"
                )
        normalized_pending[sid] = wanted
        row = {
            key: source[key]
            for key in (
                "name",
                "filters",
                "min_score",
                "notify_frequency",
                "notify_push",
                "is_active",
                "last_run_at",
                "total_matches",
                "created_at",
            )
        }
        row.update(
            id=sid,
            profile_id=bindings[uid],
            revision=previous["revision"] + 1,
            updated_at=stamp.astimezone(timezone.utc),
        )
        after.append(_wire(row))
    if seen != set(targets) or set(pending) != seen:
        raise SearchMigrationError("source/pending inventory is not complete")
    plan = {
        "version": 1,
        "consumer": consumer,
        "bindings": bindings,
        "source_sha256": digest(rows),
        "before": before,
        "after": sorted(after, key=lambda r: r["id"]),
        "targets": targets,
        "baseline_corpus": corpus,
        "pending": normalized_pending,
        "notify_since": FLOOR,
    }
    plan["seal"] = digest(plan)
    return plan


async def _execution_image(session, search_ids):
    configs = (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM saved_search_execution WHERE saved_search_id=ANY(:ids) ORDER BY saved_search_id FOR UPDATE"
                ),
                {"ids": search_ids},
            )
        )
        .mappings()
        .all()
    )
    observed = (
        await session.execute(
            sa.text(
                "SELECT saved_search_id,vacancy_id,matched FROM saved_search_observations "
                "WHERE saved_search_id=ANY(:ids) ORDER BY saved_search_id,vacancy_id"
            ),
            {"ids": search_ids},
        )
    ).all()
    return _wire([dict(row) for row in configs]), _wire([list(row) for row in observed])


async def apply_plan(session, plan, *, reverse=False):
    """Compare-and-swap complete images; after activation ANY drift rejects revert."""
    if plan.get("version") != 1 or digest(
        {k: v for k, v in plan.items() if k != "seal"}
    ) != plan.get("seal"):
        raise SearchMigrationError("invalid sealed search plan")
    if plan["notify_since"] != FLOOR:
        raise SearchMigrationError("invalid notification floor")
    ids = [uuid.UUID(row["id"]) for row in plan["after"]]
    expected_config = [
        {
            "saved_search_id": str(sid),
            "contract": "swissjob-v1",
            "notify_since": FLOOR,
            "enabled": False,
            "run_number": 0,
            "last_attempt_at": None,
        }
        for sid in ids
    ]
    pending = {sid: set(vids) for sid, vids in plan["pending"].items()}
    expected_observed = [
        [str(sid), vid, False]
        for sid in ids
        for vid in plan["baseline_corpus"]
        if vid not in pending[str(sid)]
    ]
    async with session.begin_nested():
        await _lock_profiles(session, plan["consumer"], plan["bindings"])
        current = await _read(session, plan["bindings"].values())
        configs, observations = await _execution_image(session, ids)
        is_before = current == plan["before"] and not configs and not observations
        is_after = (
            current == plan["after"]
            and configs == expected_config
            and observations == expected_observed
        )
        if (reverse and is_before) or (not reverse and is_after):
            return {"verdict": "verified", "replayed": True, "changed": 0}
        if not (is_after if reverse else is_before):
            raise SearchMigrationError("search state changed since sealed snapshot")
        # No intermediate state escapes this savepoint; the public IDs can
        # replace old imported UUIDs only while no executor owns either set.
        if reverse:
            await session.execute(
                sa.text(
                    "DELETE FROM saved_search_execution WHERE saved_search_id=ANY(:ids)"
                ),
                {"ids": ids},
            )
        pairs = {row["id"]: row for row in plan["before"]}
        for row in plan["after"]:
            original = pairs[plan["targets"][row["id"]]]
            old, new = (row, original) if reverse else (original, row)
            params = {
                key: (
                    uuid.UUID(value)
                    if key in {"id", "profile_id"}
                    else datetime.fromisoformat(value)
                    if key in DATES and value is not None
                    else json.dumps(value)
                    if key == "filters"
                    else value
                )
                for key, value in new.items()
            }
            params["old_id"] = uuid.UUID(old["id"])
            expressions = [
                f"{key}="
                + (f"CAST(:{key} AS jsonb)" if key == "filters" else f":{key}")
                for key in FIELDS
            ]
            result = await session.execute(
                sa.text(
                    "UPDATE saved_searches SET "
                    + ",".join(expressions)
                    + " WHERE id=:old_id"
                ),
                params,
            )
            if result.rowcount != 1:
                raise SearchMigrationError("search identity disappeared")
            if not reverse:
                await session.execute(
                    sa.text(
                        "INSERT INTO saved_search_execution(saved_search_id,contract,notify_since) VALUES(:id,'swissjob-v1',:since)"
                    ),
                    {
                        "id": uuid.UUID(row["id"]),
                        "since": datetime.fromisoformat(FLOOR),
                    },
                )
                observed = sorted(
                    set(plan["baseline_corpus"]) - set(plan["pending"][row["id"]])
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO saved_search_observations(saved_search_id,vacancy_id,matched) "
                        "SELECT :sid, unnest(CAST(:ids AS uuid[])), false"
                    ),
                    {
                        "sid": uuid.UUID(row["id"]),
                        "ids": [uuid.UUID(v) for v in observed],
                    },
                )
        target = plan["before"] if reverse else plan["after"]
        if await _read(session, plan["bindings"].values()) != target:
            raise SearchMigrationError("search read-back differs")
        actual = await _execution_image(session, ids)
        if actual != (([], []) if reverse else (expected_config, expected_observed)):
            raise SearchMigrationError("execution read-back differs")
    return {"verdict": "verified", "replayed": False, "changed": len(ids)}


async def resolve_pending(session, snapshot):
    """Map the frozen legacy query + sent markers; never pick a URL clone.

    Exact URL resolution follows existing merge chains in one corpus query.
    Archived winners cannot receive a pending alert. A known sent alias wins
    over an unsent alias of the same canonical vacancy (one logical offer).
    The caller still checks freshness/freeze and prepare_plan checks filters.
    """
    from jobhunt_core.import_swissjob_durables import (
        resolve_vacancies_by_incarnation_urls,
    )

    if snapshot.get("version") != 1:
        raise SearchMigrationError("invalid search snapshot version")
    ids = {str(uuid.UUID(str(row["id"]))) for row in snapshot["rows"]}
    if (
        len(ids) != len(snapshot["rows"])
        or ids != set(snapshot["candidates"])
        or ids != set(snapshot["sent"])
    ):
        raise SearchMigrationError("incomplete candidate inventory")
    for sid, rows in snapshot["candidates"].items():
        hashes = {row["hash"] for row in rows}
        if len(hashes) != len(rows) or hashes != set(snapshot["sent"][sid]):
            raise SearchMigrationError("incomplete sent-marker inventory")
        if any(type(value) is not bool for value in snapshot["sent"][sid].values()):
            raise SearchMigrationError("invalid sent marker")
    urls = {row["url"] for rows in snapshot["candidates"].values() for row in rows}
    mapping = await resolve_vacancies_by_incarnation_urls(session, urls)
    vids = {vid for winners in mapping.values() for vid in winners}
    presentable = (
        set(
            (
                await session.execute(
                    sa.text(
                        "SELECT id FROM vacancies WHERE id=ANY(:ids) AND archived_at IS NULL "
                        "AND merged_into IS NULL AND current_offer_revision_id IS NOT NULL"
                    ),
                    {"ids": list(vids)},
                )
            ).scalars()
        )
        if vids
        else set()
    )
    result = {}
    for sid, rows in snapshot["candidates"].items():
        sent, pending = set(), set()
        for row in rows:
            winners = set(mapping[row["url"]]) & presentable
            if snapshot["sent"][sid][row["hash"]]:
                sent.update(winners)
            else:
                if len(winners) != 1:
                    raise SearchMigrationError(
                        "pending offer is missing or ambiguous in core"
                    )
                pending.update(winners)
        result[sid] = sorted(str(vid) for vid in pending - sent)
    return result
