"""Provision the watched school boards without enabling them.

One scope per board, all disabled. They are provisioned together because they
are one watchlist: enabling a single school and leaving the rest off would be a
partial watch nobody declared.
"""

import asyncio

from jobhunt_core.database import SessionLocal
from jobhunt_core.harvest.providers.native_swiss_schools import BOARDS
from jobhunt_core.provision_scopes import provision_exact

SCOPE_PARAMS = ({},)


async def provision(session, enabled: bool | None = None) -> list:
    scopes = []
    for board in BOARDS:
        scopes.extend(
            await provision_exact(session, board.source, SCOPE_PARAMS, enabled)
        )
    return scopes


def main() -> None:
    async def run() -> None:
        async with SessionLocal() as session:
            scopes = await provision(session)
            await session.commit()
            print(f"{len(scopes)} school scopes provisioned (disabled)")

    asyncio.run(run())


if __name__ == "__main__":
    main()
