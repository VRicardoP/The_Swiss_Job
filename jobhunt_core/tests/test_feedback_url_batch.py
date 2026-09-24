"""Batch lookup keeps URL boundaries, merge winners and cycle exclusion."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.import_swissjob_durables import resolve_vacancies_by_incarnation_urls
from jobhunt_core.tests.test_integration_api import db, _seed_matches, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_batch_url_resolution_preserves_merges_and_url_boundaries(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, created = db
    _, vacancies, _ = _seed_matches(
        factory, created, titles=("python backend", "data engineer", "systems engineer")
    )

    async def check():
        async with factory() as session:
            pairs = (
                await session.execute(
                    sa.text(
                        "SELECT vacancy_id,url FROM source_listing_incarnations "
                        "WHERE vacancy_id=ANY(:ids) ORDER BY vacancy_id"
                    ),
                    {"ids": list(vacancies.values())},
                )
            ).all()
            assert len(pairs) >= 3
            (a, ua), (b, ub), (c, uc) = pairs[:3]
            await session.execute(
                sa.text("UPDATE vacancies SET merged_into=:winner WHERE id=:loser"),
                {"winner": b, "loser": a},
            )
            got = await resolve_vacancies_by_incarnation_urls(
                session, [ua, ub, uc, ua, None, "absent"]
            )
            assert got[ua] == got[ub] == [b]
            assert got[uc] == [c]
            assert got[None] == got["absent"] == []
            # A malformed historical cycle must not hang or pick a loser.
            await session.execute(
                sa.text("UPDATE vacancies SET merged_into=:winner WHERE id=:loser"),
                {"winner": a, "loser": b},
            )
            got = await resolve_vacancies_by_incarnation_urls(session, [ua, ub, uc])
            assert got == {ua: [], ub: [], uc: [c]}
            await session.rollback()

    asyncio.run(check())
