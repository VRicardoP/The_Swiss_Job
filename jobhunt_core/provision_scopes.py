"""Idempotent provisioning for a source's verified exact scope catalog."""

import argparse
import asyncio
import json
import uuid

import sqlalchemy as sa

from jobhunt_core.database import SessionLocal


def scope_id(source: str, params: dict[str, str]) -> uuid.UUID:
    value = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return uuid.uuid5(uuid.NAMESPACE_URL, f"jobhunt:{source}:{value}")


async def provision_exact(
    session,
    source: str,
    scope_params: tuple[dict[str, str], ...],
    enabled: bool | None = None,
) -> tuple[uuid.UUID, ...]:
    """Create the exact catalog; only change activation when explicitly asked."""
    await session.execute(
        sa.text(
            "INSERT INTO sources (id,name,tier) VALUES (:id,:name,0) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "name": source},
    )
    source_id = await session.scalar(
        sa.text("SELECT id FROM sources WHERE name=:name"), {"name": source}
    )
    scope_ids = tuple(scope_id(source, params) for params in scope_params)
    for item_id, params in zip(scope_ids, scope_params, strict=True):
        await session.execute(
            sa.text(
                "INSERT INTO harvest_scopes (id,source_id,params,tier,enabled) "
                "VALUES (:id,:source_id,CAST(:params AS jsonb),0,false) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": item_id,
                "source_id": source_id,
                "params": json.dumps(params),
            },
        )

    rows = (
        await session.execute(
            sa.text(
                "SELECT id,source_id,params FROM harvest_scopes "
                "WHERE source_id=:source_id ORDER BY id"
            ),
            {"source_id": source_id},
        )
    ).all()
    actual = {row.id: row for row in rows}
    expected = dict(zip(scope_ids, scope_params, strict=True))
    if set(actual) != set(expected) or any(
        row.source_id != source_id or row.params != expected[item_id]
        for item_id, row in actual.items()
    ):
        raise RuntimeError(
            f"{source} scopes differ from the verified {len(scope_ids)}-scope catalog"
        )

    if enabled is not None:
        await session.execute(
            sa.text("UPDATE harvest_scopes SET enabled=:enabled WHERE id=ANY(:ids)"),
            {"enabled": enabled, "ids": list(scope_ids)},
        )
    return scope_ids


def run_cli(source: str, scope_params: tuple[dict[str, str], ...]) -> None:
    parser = argparse.ArgumentParser()
    activation = parser.add_mutually_exclusive_group()
    activation.add_argument(
        "--enable", action="store_const", const=True, dest="enabled"
    )
    activation.add_argument(
        "--disable", action="store_const", const=False, dest="enabled"
    )
    parser.set_defaults(enabled=None)
    args = parser.parse_args()

    async def run() -> None:
        async with SessionLocal() as session:
            scopes = await provision_exact(session, source, scope_params, args.enabled)
            await session.commit()
        state = (
            "unchanged"
            if args.enabled is None
            else "enabled"
            if args.enabled
            else "disabled"
        )
        print(f"{source}: {len(scopes)} scopes provisioned; activation={state}")

    asyncio.run(run())
