"""Administrative frozen source snapshots and transactional reverse synchronization.

No routing changes and no core deletion: after reverse synchronization the caller
must flip the frozen BFF back to local before releasing the freeze. Retained core
copies remain inert; they are not a substitute for an owner-erasure policy.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from jobhunt_core.import_documents import (
    DocumentMigrationError,
    _FIELDS,
    digest,
    prepare_batch,
    restore_rows,
)


async def lock_source(session, *, origin, bindings, schema="public", authority=None):
    """Lock owner roots before the collection; inspect constraints, including empty DBs."""
    if origin not in _FIELDS or not bindings:
        raise DocumentMigrationError("origin and nonempty bindings required")
    connection = await session.connection()
    metadata = sa.MetaData(schema=schema)

    def reflect(sync):
        users = sa.Table("users", metadata, autoload_with=sync)
        docs = sa.Table("generated_documents", metadata, autoload_with=sync)
        journal = sa.Table("document_deliveries", metadata, autoload_with=sync)
        return users, docs, journal

    users, docs, journal = await connection.run_sync(reflect)
    if (
        set(docs.columns.keys()) != _FIELDS[origin]
        or [column.name for column in docs.primary_key] != ["id"]
        or not any(
            fk.parent.name == "user_id"
            and fk.column.table.name == "users"
            and fk.column.name == "id"
            for fk in docs.foreign_keys
        )
    ):
        raise DocumentMigrationError(
            "source schema or constraints differ from contract"
        )
    owners = sorted(
        (
            uuid.UUID(owner) if origin == "swissjob" else int(owner)
            for owner in bindings
        ),
        key=str,
    )
    await session.execute(sa.text("SET LOCAL lock_timeout='5s'"))
    routing = await connection.run_sync(
        lambda sync: sa.Table("jobhunt_routing", metadata, autoload_with=sync)
    )
    formatter = connection.dialect.identifier_preparer
    await session.execute(
        sa.text(f"LOCK TABLE {formatter.format_table(routing)} IN SHARE MODE")
    )
    modes = dict(
        (
            await session.execute(
                sa.select(routing.c.profile_id, routing.c.mode).where(
                    routing.c.consumer_id == origin, routing.c.capability == "documents"
                )
            )
        ).all()
    )
    if authority is not None:
        allowed = (
            {"local", "shadow", "core_read"}
            if authority == "local"
            else {"core_primary", "rollback_pending"}
        )
        for owner, pid in bindings.items():
            key = uuid.UUID(owner) if origin == "swissjob" else uuid.UUID(str(pid))
            if modes.get(key, modes.get(uuid.UUID(int=0), "local")) not in allowed:
                raise DocumentMigrationError(
                    "document authority does not match migration direction"
                )
    found = (
        (
            await session.execute(
                sa.select(users.c.id)
                .where(users.c.id.in_(owners))
                .order_by(users.c.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if set(found) != set(owners):
        raise DocumentMigrationError("source owner missing")
    if origin == "swissjob":
        mapping = await connection.run_sync(
            lambda sync: sa.Table("jobhunt_profile_map", metadata, autoload_with=sync)
        )
        actual = dict(
            (
                await session.execute(
                    sa.select(mapping.c.user_id, mapping.c.core_profile_id)
                    .where(mapping.c.user_id.in_(owners))
                    .order_by(mapping.c.user_id)
                    .with_for_update(read=True)
                )
            ).all()
        )
        wanted = {
            uuid.UUID(owner): uuid.UUID(str(pid)) for owner, pid in bindings.items()
        }
        if actual != wanted:
            raise DocumentMigrationError("source profile binding changed")
    # Table names are reflected identifiers, never SQL from the input snapshot.
    formatter = connection.dialect.identifier_preparer
    await session.execute(
        sa.text(
            f"LOCK TABLE {formatter.format_table(docs)} IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    await session.execute(
        sa.text(
            f"LOCK TABLE {formatter.format_table(journal)} IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    pending = await session.scalar(
        sa.select(sa.func.count())
        .select_from(journal)
        .where(journal.c.user_id.in_(owners), journal.c.delivered_at.is_(None))
    )
    if pending:
        raise DocumentMigrationError(
            "pending document deliveries must be drained before freeze"
        )
    return docs, owners


async def source_rows(session, table, owners):
    return [
        dict(row)
        for row in (
            await session.execute(
                sa.select(table).where(table.c.user_id.in_(owners)).order_by(table.c.id)
            )
        ).mappings()
    ]


async def verify_source(session, batch, *, schema="public"):
    """Hold source locks until the core import commits; detect drift from sealed input."""
    table, owners = await lock_source(
        session,
        origin=batch["origin"],
        bindings=batch["bindings"],
        schema=schema,
        authority="local",
    )
    current = prepare_batch(
        batch_id=batch["batch_id"],
        origin=batch["origin"],
        consumer=batch["consumer"],
        bindings=batch["bindings"],
        rows=await source_rows(session, table, owners),
    )
    if current["seal"] != batch["seal"]:
        raise DocumentMigrationError("source changed after snapshot sealing")


async def reverse_sync(session, core_rows, *, origin, bindings, schema="public"):
    """Reconcile the whole OWNED collection, including creates/deletes after cutover.

    A foreign UUID or any material mismatch rolls back every write even when the
    caller catches the error. Other owners are never updated or deleted.
    """
    expected = restore_rows(core_rows, origin=origin, bindings=bindings)
    async with session.begin_nested():
        table, owners = await lock_source(
            session, origin=origin, bindings=bindings, schema=schema, authority="core"
        )
        ids = [row["id"] for row in expected]
        foreign = await session.scalar(
            sa.select(sa.func.count())
            .select_from(table)
            .where(table.c.id.in_(ids), table.c.user_id.not_in(owners))
        )
        if foreign:
            raise DocumentMigrationError("reverse UUID belongs to another source owner")
        removed = await session.execute(
            sa.delete(table).where(table.c.user_id.in_(owners), table.c.id.not_in(ids))
        )
        for row in expected:
            stmt = insert(table).values(**row)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[table.c.id],
                    set_={key: stmt.excluded[key] for key in row if key != "id"},
                )
            )
        actual = await source_rows(session, table, owners)
        if digest(actual) != digest(expected):
            raise DocumentMigrationError("reverse material reconciliation failed")
    return {
        "verdict": "verified",
        "documents": len(expected),
        "removed_local": removed.rowcount,
        "sha256": digest(expected),
    }
