"""The frozen plan must not issue a corpus lookup for every legacy match."""

import asyncio
import uuid

from jobhunt_core.tests.test_integration_api import db, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_api_feedback import seed
from jobhunt_core.tests.test_integration_feedback_handover import plan_for, source_row


def test_plan_query_count_does_not_grow_with_unmarked_history(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, pid, vid, _ = seed(db)
    first = source_row(factory, pid, vid)
    source = [first] + [
        {
            **first,
            "id": str(uuid.uuid4()),
            "job_hash": uuid.uuid4().hex,
            "url": f"https://not-in-corpus.example/{i}",
            "feedback": None,
        }
        for i in range(20)
    ]

    async def check():
        async with factory() as session:
            calls = []
            execute = session.execute

            async def counted(statement, *args, **kwargs):
                calls.append(str(statement))
                return await execute(statement, *args, **kwargs)

            session.execute = counted
            plan = await plan_for(session, pid, source)
            assert len(plan["changes"]) == 1
            assert plan["changes"][0]["after"]["feedback"] == "thumbs_up"
            assert len(calls) <= 8, f"{len(calls)} statements for 21 rows"

    asyncio.run(check())
