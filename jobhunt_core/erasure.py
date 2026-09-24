"""Consumer-owned live erasure. Receipts survive profile deletion and retries.

This module does not claim that offline backups or another service are erased.
The caller commits the receipt and graph deletion in ONE transaction.
"""

import sqlalchemy as sa


async def scrub_portfolio_manifests(session, external_ref, profile_id):
    """Remove this subject's material diagnostics, not other users or corpus.

    Free-text divergence summaries duplicate the structured diagnostics; discard
    those summaries on affected reports. Preserve PK provenance for investigation,
    but invalidate the old attestation/rollback after an intentional erasure.
    """
    import ast
    import copy
    import json

    def belongs(encoded, position):
        value = ast.literal_eval(encoded)
        if not isinstance(value, tuple) or len(value) <= position:
            raise ValueError("unrecognized migration diagnostic identity")
        return str(value[position]) == external_ref

    rows = (
        await session.execute(
            sa.text(
                "SELECT id,manifest FROM portfolio_migration_manifest ORDER BY id FOR UPDATE"
            )
        )
    ).all()
    for row in rows:
        original = row.manifest
        if not isinstance(original, dict):
            raise ValueError("unrecognized migration manifest")
        value = copy.deepcopy(original)
        if "staged" in value:
            value["staged"] = [
                entry
                for entry in value["staged"]
                if str(entry["external_ref"]) != external_ref
            ]
        for name in ("applications", "bookmarks", "saved_searches"):
            table = value.get("tables", {}).get(name, {})
            for direction in ("missing", "extra"):
                if direction in table:
                    table[direction] = [
                        item for item in table[direction] if not belongs(item, 0)
                    ]
        for field, positions in (
            ("events", {"expected": 0, "actual": 0}),
            ("staging", {"expected": 1, "actual_ids": 0}),
        ):
            for side, position in positions.items():
                if side in value.get(field, {}):
                    value[field][side] = {
                        key: count
                        for key, count in value[field][side].items()
                        if not belongs(key, position)
                    }
        # Profiles are usually provisioned before migration and absent from
        # provenance. Material diagnostics above identify those subjects too.
        named = str(profile_id) in original.get("provenance", {}).get("profiles", [])
        if value != original or named:
            value["divergences"] = ["personal diagnostics redacted after erasure"]
            value["verdict"] = "redacted"
            await session.execute(
                sa.text(
                    "UPDATE portfolio_migration_manifest SET manifest=CAST(:body AS jsonb), "
                    "status='unknown',verdict='redacted' WHERE id=:id"
                ),
                {"id": row.id, "body": json.dumps(value)},
            )


class ProfileErasedError(ValueError):
    """A delayed import/CDC event may not recreate an erased identity."""


async def lock_erasure_fence(session, *, exclusive=False):
    # NOWAIT avoids lock upgrades/deadlocks when a projector batch has already
    # enrolled another identity. A failed erasure stays queued for retry.
    mode = "UPDATE" if exclusive else "SHARE"
    await session.execute(
        sa.text(f"SELECT generation FROM profile_erasure_fence FOR {mode} NOWAIT")
    )


async def lock_identity(session, consumer_id, external_ref):
    await lock_erasure_fence(session)
    await session.execute(
        sa.text(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            "CAST(CAST(:cid AS uuid) AS text) || ':' || :ref, 73146))"
        ),
        {"cid": consumer_id, "ref": external_ref},
    )


async def assert_not_erased(session, consumer_id, external_ref):
    await lock_identity(session, consumer_id, external_ref)
    erased = await session.scalar(
        sa.text(
            "SELECT 1 FROM profile_erasure_receipts "
            "WHERE consumer_id=:cid AND external_ref=:ref"
        ),
        {"cid": consumer_id, "ref": external_ref},
    )
    if erased:
        raise ProfileErasedError("profile has been erased")


async def scrub_capture(session, profile_id, external_ref):
    # Capture takes staging locks only. Wait for in-flight INSERTs, then scrub
    # their committed contents; future INSERTs are redacted by the DB trigger.
    await session.execute(
        sa.text("LOCK TABLE shadow_change_log IN SHARE ROW EXCLUSIVE MODE")
    )
    params = {"pid": profile_id, "ref": external_ref}
    await session.execute(
        sa.text(
            "UPDATE profile_erasure_receipts SET source_profile_pks=ARRAY("
            "SELECT unnest(source_profile_pks) UNION SELECT pk FROM shadow_change_log "
            "WHERE src_table='user_profiles' AND payload->>'user_id'=:ref) "
            "WHERE profile_id=:pid"
        ),
        params,
    )
    await session.execute(
        sa.text(
            "UPDATE shadow_change_log SET payload='{}'::jsonb WHERE src_table='user_profiles' "
            "AND (payload->>'user_id'=:ref OR pk=ANY(SELECT unnest(source_profile_pks) "
            "FROM profile_erasure_receipts WHERE profile_id=:pid))"
        ),
        params,
    )


async def erase_external_identity(session, consumer_id, external_ref):
    """Also fence a not-yet-projected account: missing enrollment is not safety."""
    import uuid

    await lock_erasure_fence(session, exclusive=True)
    await lock_identity(session, consumer_id, external_ref)
    params = {"cid": consumer_id, "ref": external_ref}
    pid = await session.scalar(
        sa.text("SELECT id FROM profiles WHERE consumer_id=:cid AND external_ref=:ref"),
        params,
    )
    if pid is not None:
        return await erase_owned_profile(session, consumer_id, pid)
    # The user can disappear locally BEFORE its first CDC projection. Reserve
    # an erasure identity without ever creating a profile or a CV.
    await session.execute(
        sa.text(
            "INSERT INTO profile_erasure_receipts(profile_id,consumer_id,external_ref) "
            "VALUES(:pid,:cid,:ref) ON CONFLICT(consumer_id,external_ref) DO NOTHING"
        ),
        {**params, "pid": uuid.uuid4()},
    )
    receipt = dict(
        (
            await session.execute(
                sa.text(
                    "SELECT profile_id,erased_at FROM profile_erasure_receipts "
                    "WHERE consumer_id=:cid AND external_ref=:ref"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    consumer = await session.scalar(
        sa.text("SELECT name FROM consumers WHERE id=:cid"), {"cid": consumer_id}
    )
    if consumer == "swissjob-shadow":
        await scrub_capture(session, receipt["profile_id"], external_ref)
    return receipt


async def erase_owned_profile(session, consumer_id, profile_id):
    """Return an owned receipt, or None for unknown/cross-tenant identities.

    Lock order: identity -> profile -> children, matching enrollment. Retries
    require a persisted receipt, never treat an arbitrary 404 as confirmation.
    """
    from jobhunt_core.shadow.projector import _erase_profile_graph

    await lock_erasure_fence(session, exclusive=True)
    params = {"cid": consumer_id, "pid": profile_id}
    receipt = (
        (
            await session.execute(
                sa.text(
                    "SELECT profile_id, erased_at FROM profile_erasure_receipts "
                    "WHERE consumer_id=:cid AND profile_id=:pid"
                ),
                params,
            )
        )
        .mappings()
        .one_or_none()
    )
    if receipt:
        return dict(receipt)
    owner = (
        await session.execute(
            sa.text(
                "SELECT p.external_ref, c.name FROM profiles p "
                "JOIN consumers c ON c.id=p.consumer_id "
                "WHERE p.id=:pid AND p.consumer_id=:cid"
            ),
            params,
        )
    ).one_or_none()
    if owner is None:
        return None
    await lock_identity(session, consumer_id, owner.external_ref)
    still_owned = await session.scalar(
        sa.text(
            "SELECT id FROM profiles WHERE id=:pid AND consumer_id=:cid "
            "AND external_ref=:ref FOR UPDATE"
        ),
        {**params, "ref": owner.external_ref},
    )
    if still_owned is None:
        # Reassignment/deletion may have committed while waiting for the lock.
        receipt = (
            (
                await session.execute(
                    sa.text(
                        "SELECT profile_id, erased_at FROM profile_erasure_receipts "
                        "WHERE consumer_id=:cid AND profile_id=:pid"
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(receipt) if receipt else None
    await session.execute(
        sa.text(
            "INSERT INTO profile_erasure_receipts (profile_id, consumer_id, external_ref) "
            "VALUES (:pid,:cid,:ref) ON CONFLICT DO NOTHING"
        ),
        {**params, "ref": owner.external_ref},
    )
    if owner.name == "swissjob-shadow":
        await scrub_capture(session, profile_id, owner.external_ref)
    if owner.name == "portfolio":
        await scrub_portfolio_manifests(session, owner.external_ref, profile_id)
    await _erase_profile_graph(session, owner.external_ref, owner.name)
    receipt = (
        (
            await session.execute(
                sa.text(
                    "SELECT profile_id, erased_at FROM profile_erasure_receipts "
                    "WHERE consumer_id=:cid AND profile_id=:pid"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    return dict(receipt)
