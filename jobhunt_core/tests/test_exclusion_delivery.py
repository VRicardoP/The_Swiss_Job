"""Version fencing at the real core endpoint, on PostgreSQL."""

import asyncio
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from jobhunt_core.api import schemas, v1
from jobhunt_core.api.deps import ApiError
from jobhunt_core.tests.test_integration_matching import db, _setup  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_out_of_order_and_duplicate_exclusion_deliveries(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    pid, _, _, _ = _setup(factory, created, ["python developer"])

    async def check():
        async with factory() as s:
            owner = (
                await s.execute(
                    sa.text("SELECT consumer_id FROM profiles WHERE id=:p"), {"p": pid}
                )
            ).scalar_one()
        principal = SimpleNamespace(consumer_id=owner)

        async def put(version, patterns):
            async with factory() as s:
                return await v1.put_profile_exclusions(
                    pid,
                    schemas.ExclusionsWriteDTO(
                        version=version,
                        exclusions=[
                            {"kind": "title_contains", "pattern": p} for p in patterns
                        ],
                    ),
                    session=s,
                    principal=principal,
                )

        assert (await put(2, [])).version == 2
        assert (await put(1, ["python"])).exclusions == []
        async with factory() as s:
            generation = (
                await s.execute(sa.text("SELECT generation FROM corpus_generation"))
            ).scalar_one()
        assert (await put(2, [])).version == 2
        async with factory() as s:
            assert (
                await s.execute(sa.text("SELECT generation FROM corpus_generation"))
            ).scalar_one() == generation
        with pytest.raises(ApiError):
            await put(2, ["python"])
        assert len((await put(3, ["python"])).exclusions) == 1
        async with factory() as s:
            assert (
                await s.execute(sa.text("SELECT generation FROM corpus_generation"))
            ).scalar_one() > generation

    asyncio.run(check())
