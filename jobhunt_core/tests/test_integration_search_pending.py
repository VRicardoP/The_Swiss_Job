"""Pending import is complete, identity-safe and preserves sent aliases."""

import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.import_swissjob_searches import SearchMigrationError, resolve_pending
from jobhunt_core.tests.test_integration_search_execution import (
    db,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    corpus,
    _vacancy_state,
)


def snapshot(items, sent=()):
    sid = str(uuid.uuid4())
    return {
        "version": 1,
        "rows": [{"id": sid}],
        "candidates": {
            sid: [{"hash": item.external_id, "url": item.url} for item in items]
        },
        "sent": {sid: {item.external_id: item.external_id in sent for item in items}},
    }, sid


def test_resolves_only_pending_and_preserves_sent(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    _, items = corpus(factory, made, "Python owed", "Python sent")
    frozen, sid = snapshot(items, [items[1].external_id])
    expected = str(_vacancy_state(factory, items[0].external_id).vac)

    async def go():
        async with factory() as session:
            assert await resolve_pending(session, frozen) == {sid: [expected]}

    asyncio.run(go())


def test_missing_pending_aborts_but_missing_sent_does_not_create_a_new_alert(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, _ = db
    sid = str(uuid.uuid4())
    frozen = {
        "version": 1,
        "rows": [{"id": sid}],
        "candidates": {sid: [{"hash": "old", "url": "https://absent.invalid/job"}]},
        "sent": {sid: {"old": False}},
    }

    async def go():
        async with factory() as session:
            with pytest.raises(SearchMigrationError, match="missing or ambiguous"):
                await resolve_pending(session, frozen)
            frozen["sent"][sid]["old"] = True
            assert await resolve_pending(session, frozen) == {sid: []}

    asyncio.run(go())


def test_ambiguous_url_is_not_resolved_by_row_order(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    _, items = corpus(factory, made, "Python one", "Python two")
    frozen, _ = snapshot(items[:1])
    second = _vacancy_state(factory, items[1].external_id).vac

    async def go():
        async with factory() as session:
            await session.execute(
                sa.text(
                    "UPDATE source_listing_incarnations SET url=:url WHERE vacancy_id=:v"
                ),
                {"url": items[0].url, "v": second},
            )
            with pytest.raises(SearchMigrationError, match="ambiguous"):
                await resolve_pending(session, frozen)

    asyncio.run(go())


def test_sent_merged_alias_suppresses_pending_winner(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    _, items = corpus(factory, made, "Python winner", "Python sent loser")
    frozen, sid = snapshot(items, [items[1].external_id])
    winner, loser = (_vacancy_state(factory, item.external_id).vac for item in items)

    async def go():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE vacancies SET merged_into=:w WHERE id=:l"),
                {"w": winner, "l": loser},
            )
            assert await resolve_pending(session, frozen) == {sid: []}

    asyncio.run(go())


@pytest.mark.parametrize(
    "defect",
    ["missing_marker", "string_marker", "repeated_candidate", "missing_search"],
)
def test_incomplete_or_malformed_snapshot_is_rejected(db, defect):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    _, items = corpus(factory, made, "Python owed")
    frozen, sid = snapshot(items)
    if defect == "missing_marker":
        frozen["sent"][sid].clear()
    elif defect == "string_marker":
        frozen["sent"][sid][items[0].external_id] = "false"
    elif defect == "repeated_candidate":
        frozen["candidates"][sid] *= 2
    else:
        frozen["candidates"].clear()

    async def go():
        async with factory() as session:
            with pytest.raises(SearchMigrationError):
                await resolve_pending(session, frozen)

    asyncio.run(go())
