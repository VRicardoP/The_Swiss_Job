"""Provision the verified Jobicy scopes without enabling them by accident."""

import argparse
import asyncio
import json
import uuid

import sqlalchemy as sa

from jobhunt_core.database import SessionLocal

SOURCE = "jobicy"
SCOPE_PARAMS = (
    {},
    {"tag": "python"},
    {"tag": "javascript"},
    {"tag": "react"},
    {"tag": "customer-success"},
    {"tag": "copywriting"},
    {"tag": "technical-writing"},
    {"tag": "translation"},
)


def _scope_id(params: dict[str, str]) -> uuid.UUID:
    value = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return uuid.uuid5(uuid.NAMESPACE_URL, f"jobhunt:{SOURCE}:{value}")


async def provision(session, enabled: bool | None = None) -> tuple[uuid.UUID, ...]:
    """Create the exact scope set; only change activation when explicitly asked."""
    await session.execute(
        sa.text(
            "INSERT INTO sources (id,name,tier) VALUES (:id,:name,0) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "name": SOURCE},
    )
    source_id = await session.scalar(
        sa.text("SELECT id FROM sources WHERE name=:name"), {"name": SOURCE}
    )
    scope_ids = tuple(_scope_id(params) for params in SCOPE_PARAMS)
    for scope_id, params in zip(scope_ids, SCOPE_PARAMS, strict=True):
        await session.execute(
            sa.text(
                "INSERT INTO harvest_scopes (id,source_id,params,tier,enabled) "
                "VALUES (:id,:source_id,CAST(:params AS jsonb),0,false) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": scope_id,
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
    expected = dict(zip(scope_ids, SCOPE_PARAMS, strict=True))
    if set(actual) != set(expected) or any(
        row.source_id != source_id or row.params != expected[scope_id]
        for scope_id, row in actual.items()
    ):
        raise RuntimeError("Jobicy scopes differ from the verified eight-scope catalog")

    if enabled is not None:
        await session.execute(
            sa.text("UPDATE harvest_scopes SET enabled=:enabled WHERE id=ANY(:ids)"),
            {"enabled": enabled, "ids": list(scope_ids)},
        )
    return scope_ids


async def _main(enabled: bool | None) -> None:
    async with SessionLocal() as session:
        scopes = await provision(session, enabled)
        await session.commit()
    state = "unchanged" if enabled is None else "enabled" if enabled else "disabled"
    print(f"{SOURCE}: {len(scopes)} scopes provisioned; activation={state}")


def main() -> None:
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
    asyncio.run(_main(args.enabled))


if __name__ == "__main__":
    main()
