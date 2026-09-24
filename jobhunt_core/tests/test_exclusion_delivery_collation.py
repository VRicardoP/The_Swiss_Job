"""Same rules must ACK regardless of the database's text ordering."""
import asyncio
from types import SimpleNamespace

import sqlalchemy as sa

from jobhunt_core.api import schemas, v1
from jobhunt_core.tests.test_integration_matching import db, _setup  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_duplicate_version_compares_sets_not_database_collation(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, _, _, _ = _setup(factory, created, ["developer"])

    async def check():
        async with factory() as session:
            owner = (await session.execute(sa.text(
                "UPDATE profiles SET exclusions_version=1 WHERE id=:p RETURNING consumer_id"
            ), {"p": pid})).scalar_one()
            # Real PostgreSQL ordering, isolated to this connection. Using an
            # explicit collation keeps the reproduction independent of CI locale.
            await session.execute(sa.text(
                'CREATE TEMP TABLE profile_exclusions ('
                'profile_id uuid, kind text, pattern text COLLATE "en-US-x-icu") '
                'ON COMMIT DROP'
            ))
            await session.execute(sa.text(
                "INSERT INTO profile_exclusions VALUES (:p,'title_contains',:v)"
            ), [{"p": pid, "v": p} for p in ("z", "ä")])
            order = (await session.execute(sa.text(
                "SELECT pattern FROM profile_exclusions ORDER BY kind,pattern"
            ))).scalars().all()
            assert order != sorted(order), "reproduction must exercise a different ordering"
            result = await v1.put_profile_exclusions(
                pid, schemas.ExclusionsWriteDTO(version=1, exclusions=[
                    {"kind": "title_contains", "pattern": p} for p in ("z", "ä")]),
                session=session, principal=SimpleNamespace(consumer_id=owner),
            )
            assert result.version == 1
            assert {r.pattern for r in result.exclusions} == {"z", "ä"}

    asyncio.run(check())
