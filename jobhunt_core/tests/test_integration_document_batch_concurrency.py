"""The same generation in flight must not insert CV+letter twice."""

import asyncio

import httpx

from jobhunt_core.tests.test_integration_api_documents import _seed, _path
from jobhunt_core.tests.test_integration_api_saved_searches import db, _rows, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_document_batches import body


def test_batch_concurrent_retry_waits_and_gets_same_pair(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    from jobhunt_core import documents, outbox
    from jobhunt_core.api import deps
    from jobhunt_core.api.main import app

    f, made = db
    token, _, pid = _seed(f, made)
    owner, emit = documents.owner, outbox.emit

    async def run():
        in_handler, second_at_owner, release = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        owner_calls = 0
        emissions = 0

        async def watched_owner(*args, **kwargs):
            nonlocal owner_calls
            owner_calls += 1
            if owner_calls == 2:
                second_at_owner.set()
            return await owner(*args, **kwargs)

        async def paused_emit(*args, **kwargs):
            nonlocal emissions
            emissions += 1
            if emissions == 1:
                in_handler.set()
                await release.wait()
            return await emit(*args, **kwargs)

        async def session():
            async with f() as s:
                yield s

        monkeypatch.setattr(documents, "owner", watched_owner)
        monkeypatch.setattr(outbox, "emit", paused_emit)
        app.dependency_overrides[deps.get_session] = session
        tasks = []
        try:
            async with asyncio.timeout(10):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as c:

                    async def send():
                        return await c.post(
                            _path(pid) + "/batch",
                            json=body(),
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Idempotency-Key": "parallel-batch",
                            },
                        )

                    tasks.append(asyncio.create_task(send()))
                    await in_handler.wait()
                    tasks.append(asyncio.create_task(send()))
                    await second_at_owner.wait()
                    assert not tasks[1].done()
                    release.set()
                    first, second = await asyncio.gather(*tasks)
                    assert first.status_code == second.status_code == 201
                    assert first.content == second.content
                    assert emissions == 2
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            app.dependency_overrides.pop(deps.get_session, None)

    asyncio.run(run())
    assert (
        len(_rows(f, "SELECT id FROM generated_documents WHERE profile_id=:p", p=pid))
        == 2
    )
    assert (
        len(
            _rows(
                f,
                "SELECT event_id FROM integration_outbox WHERE subject_profile_id=:p",
                p=pid,
            )
        )
        == 2
    )
    assert (
        len(_rows(f, "SELECT key FROM idempotency_records WHERE key='parallel-batch'"))
        == 1
    )
