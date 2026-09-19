"""Frozen feedback handover, with a BEFORE-image sealed before any mutation.

No network, commits, routing changes or corpus writes. The cutover command must
freeze/drain both writers, lock the source, persist the returned plan privately,
then apply it under the same source freeze. Replay accepts only the complete
before or after image, never partial drift. Reversal here is PRE-ACTIVATION:
subsequent user edits are deliberately not discarded by restoring an old plan.
"""

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from jobhunt_core.import_schools import canonical, digest
from jobhunt_core.import_swissjob_durables import resolve_vacancies_by_incarnation_url


class FeedbackMigrationError(ValueError):
    pass


_TABLES = {
    "profile_vacancy_state": (
        ("profile_id", "vacancy_id"),
        ("feedback", "dismissed_at", "feedback_recorded_at"),
    ),
    "school_applications": (
        ("id",), ("feedback", "feedback_recorded_at", "feedback_implicit"),
    ),
    "profile_vacancy_events": (
        ("id",), ("profile_id", "vacancy_id", "kind", "data", "created_at"),
    ),
}
_JSON = {"feedback_implicit", "data"}
_DATES = {"dismissed_at", "feedback_recorded_at", "created_at"}
_UUIDS = {"id", "profile_id", "vacancy_id"}


def _wire(value):
    return json.loads(canonical(value))


async def _lock_profiles(session, consumer, profile_ids):
    for pid in sorted(profile_ids):
        found = await session.scalar(sa.text(
            "SELECT p.id FROM profiles p JOIN consumers c ON c.id=p.consumer_id "
            "WHERE p.id=:p AND c.name=:c FOR UPDATE OF p"
        ), {"p": uuid.UUID(pid), "c": consumer})
        if found is None:
            raise FeedbackMigrationError("profile ownership changed")


def _predicate(keys):
    return " AND ".join(f"{key}=:{key}" for key in keys)


def _parameters(values):
    return {
        key: (json.dumps(value) if key in _JSON else
              datetime.fromisoformat(value) if key in _DATES and value is not None else
              uuid.UUID(value) if key in _UUIDS else value)
        for key, value in values.items()
    }


async def _read(session, table, identity):
    keys, fields = _TABLES[table]
    result = (await session.execute(sa.text(
        f"SELECT {','.join(fields)} FROM {table} WHERE {_predicate(keys)} FOR UPDATE"
    ), _parameters(identity))).mappings().one_or_none()
    return _wire(dict(result)) if result is not None else None


async def prepare_plan(session, *, consumer, bindings, rows, recorded_at):
    """rows are the COMPLETE frozen MatchResult collection, joined to job URL.

    Required keys: id, user_id, job_hash, url, feedback, feedback_implicit.
    The existing E.15 school migration must already contain every marked school
    state. Conflicting legacy aliases abort; absent timestamps cannot justify
    guessing which explicit decision is newer.
    """
    stamp = datetime.fromisoformat(recorded_at)
    if stamp.tzinfo is None:
        raise FeedbackMigrationError("aware handover timestamp required")
    stamp = stamp.astimezone(timezone.utc).isoformat()
    bindings = {str(uuid.UUID(u)): str(uuid.UUID(p)) for u, p in bindings.items()}
    if not bindings or len(set(bindings.values())) != len(bindings):
        raise FeedbackMigrationError("invalid profile bindings")
    await _lock_profiles(session, consumer, bindings.values())
    requested, source_ids, source_keys, by_url = {}, set(), set(), {}

    def expect(table, identity, after):
        key = (table, tuple(identity.values()))
        if key in requested and requested[key][1] != after:
            raise FeedbackMigrationError("conflicting source aliases")
        requested[key] = (identity, after)

    for row in rows:
        rid = uuid.UUID(str(row["id"]))
        uid = str(uuid.UUID(str(row["user_id"])))
        key = (uid, row["job_hash"])
        if rid in source_ids or key in source_keys or uid not in bindings:
            raise FeedbackMigrationError("duplicate or unbound source identity")
        source_ids.add(rid)
        source_keys.add(key)
        feedback = row["feedback"]
        implicit = [] if row["feedback_implicit"] is None else row["feedback_implicit"]
        if feedback not in {None, "thumbs_up", "thumbs_down", "applied", "dismissed"} or not isinstance(implicit, list):
            raise FeedbackMigrationError("invalid source feedback")
        if any(not isinstance(event, dict) for event in implicit):
            raise FeedbackMigrationError("invalid implicit event")
        pid = bindings[uid]
        if row["url"] not in by_url:
            by_url[row["url"]] = await resolve_vacancies_by_incarnation_url(session, row["url"])
        vids = by_url[row["url"]]
        schools = (await session.execute(sa.text(
            "SELECT a.id FROM school_applications a JOIN school_job_details j "
            "ON j.id=a.school_job_id WHERE a.profile_id=:p AND j.source_ref=:ref"
        ), {"p": uuid.UUID(pid), "ref": row["job_hash"]})).scalars().all()
        if len(schools) > 1:
            raise FeedbackMigrationError("ambiguous school source identity")
        if not vids and not schools and (feedback is not None or implicit):
            raise FeedbackMigrationError("marked source row has no core identity")
        for sid in schools:
            expect("school_applications", {"id": str(sid)}, {
                "feedback": feedback, "feedback_recorded_at": stamp,
                "feedback_implicit": implicit,
            })
        for vid in vids:
            vid = str(vid)
            expect("profile_vacancy_state", {"profile_id": pid, "vacancy_id": vid}, {
                "feedback": feedback,
                "dismissed_at": stamp if feedback in {"thumbs_down", "dismissed"} else None,
                "feedback_recorded_at": stamp,
            })
            if not schools:
                for index, event in enumerate(implicit):
                    eid = uuid.uuid5(rid, f"core-feedback:{vid}:{index}")
                    expect("profile_vacancy_events", {"id": str(eid)}, {
                        "profile_id": pid, "vacancy_id": vid, "kind": "implicit",
                        "data": event, "created_at": stamp,
                    })
    existing = (await session.execute(sa.text(
        "SELECT profile_id::text,vacancy_id::text FROM profile_vacancy_state "
        "WHERE profile_id=ANY(:pids) AND (feedback IS NOT NULL OR dismissed_at IS NOT NULL)"
    ), {"pids": [uuid.UUID(p) for p in bindings.values()]})).all()
    if any(("profile_vacancy_state", (pid, vid)) not in requested for pid, vid in existing):
        raise FeedbackMigrationError("core mark absent from complete source snapshot")
    changes = []
    for (table, _), (identity, after) in sorted(requested.items()):
        before = await _read(session, table, identity)
        # Unmarked matches are not themselves durable feedback. Only material
        # existing marks need clearing; do not manufacture thousands of states.
        if table == "profile_vacancy_state" and after["feedback"] is None and (
            before is None or (before["feedback"] is None and before["dismissed_at"] is None)
        ):
            continue
        if table == "school_applications" and before is None:
            raise FeedbackMigrationError("school state must be migrated first")
        changes.append({"table": table, "identity": identity, "before": before, "after": after})
    plan = {"consumer": consumer, "bindings": bindings, "source_sha256": digest(rows),
            "changes": changes}
    return {**plan, "seal": digest(plan)}


async def apply_plan(session, raw, *, reverse=False):
    """Atomic pre-activation import/rollback; caller commits only after success."""
    plan = {key: value for key, value in raw.items() if key != "seal"}
    if digest(plan) != raw.get("seal"):
        raise FeedbackMigrationError("feedback plan seal mismatch")
    # Validate before generating SQL; a plan is data, never SQL authority.
    identities = set()
    pids = set(plan["bindings"].values())
    for change in plan["changes"]:
        table = change["table"]
        if table not in _TABLES:
            raise FeedbackMigrationError("unknown feedback table")
        keys, fields = _TABLES[table]
        if set(change["identity"]) != set(keys) or any(
            value is not None and set(value) != set(fields)
            for value in (change["before"], change["after"])
        ) or change["after"] is None:
            raise FeedbackMigrationError("invalid feedback image")
        identity = (table, tuple(change["identity"][k] for k in keys))
        if identity in identities:
            raise FeedbackMigrationError("duplicate feedback target")
        identities.add(identity)
        if table == "profile_vacancy_state" and change["identity"]["profile_id"] not in pids:
            raise FeedbackMigrationError("unbound feedback target")
        if table == "profile_vacancy_events" and any(
            image is not None and image["profile_id"] not in pids
            for image in (change["before"], change["after"])
        ):
            raise FeedbackMigrationError("unbound feedback event")
    async with session.begin_nested():
        await _lock_profiles(session, plan["consumer"], plan["bindings"].values())
        for change in plan["changes"]:
            if change["table"] == "school_applications":
                owner = await session.scalar(sa.text(
                    "SELECT profile_id::text FROM school_applications WHERE id=:id"
                ), _parameters(change["identity"]))
                if owner not in pids:
                    raise FeedbackMigrationError("unbound school feedback target")
        actual = [await _read(session, c["table"], c["identity"]) for c in plan["changes"]]
        start, finish = ("after", "before") if reverse else ("before", "after")
        if actual == [c[finish] for c in plan["changes"]]:
            return {"verdict": "verified", "replayed": True, "changed": 0}
        if actual != [c[start] for c in plan["changes"]]:
            raise FeedbackMigrationError("feedback changed since sealed snapshot")
        for change in plan["changes"]:
            table, identity = change["table"], change["identity"]
            keys, fields = _TABLES[table]
            target = change[finish]
            if target is None:
                # PVS may now own evaluator/bookmark state. Delete only when
                # the whole row is still disposable, otherwise fail closed.
                if table == "profile_vacancy_state":
                    busy = await session.scalar(sa.text(
                        "SELECT current_eval_id IS NOT NULL OR saved_at IS NOT NULL OR notes IS NOT NULL "
                        f"FROM profile_vacancy_state WHERE {_predicate(keys)}"
                    ), _parameters(identity))
                    if busy:
                        raise FeedbackMigrationError("new state prevents pre-activation rollback")
                await session.execute(sa.text(f"DELETE FROM {table} WHERE {_predicate(keys)}"), _parameters(identity))
            else:
                values = {**identity, **target}
                expr = {k: f"CAST(:{k} AS jsonb)" if k in _JSON else f":{k}" for k in values}
                if change[start] is None:
                    statement = f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join(expr.values())})"
                else:
                    statement = f"UPDATE {table} SET " + ",".join(f"{k}={expr[k]}" for k in fields) + f" WHERE {_predicate(keys)}"
                await session.execute(sa.text(statement), _parameters(values))
        after = [await _read(session, c["table"], c["identity"]) for c in plan["changes"]]
        if after != [c[finish] for c in plan["changes"]]:
            raise FeedbackMigrationError("feedback read-back differs")
    return {"verdict": "verified", "replayed": False, "changed": len(plan["changes"])}
