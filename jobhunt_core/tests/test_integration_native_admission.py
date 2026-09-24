"""Native admission is fenced with persistence; known items are always refreshed."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import sqlalchemy as sa

from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.types import FetchResult, RawListing
from jobhunt_core.tests.test_integration_harvest import (
    db,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    _seed_scopes,
    _state,
    CollectSink,
    FailingSink,
)

WINDOW = "admission_window_days"


class WindowProvider(BaseProvider):
    name = "arbeitnow"

    def __init__(self, listings):
        self.listings = listings
        self.calls = 0

    async def fetch_new(self, params, cursor, http):
        assert WINDOW not in params  # never sent to the upstream provider
        self.calls += 1
        return FetchResult(self.listings, {"complete_feed": True})


def listing(ref, published):
    return RawListing(
        ref,
        f"https://example.test/{ref}",
        {"created_at": published, "raw": "unchanged"},
    )


def configure(factory, sid, days):
    async def run():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"
                ),
                {"p": json.dumps({WINDOW: days}), "s": sid},
            )
            await s.commit()

    asyncio.run(run())


def test_window_applies_before_sink_and_counts_are_persisted(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    configure(factory, sid, 7)
    now = datetime.now(timezone.utc)
    recent, old, absent = (
        listing("recent", now.timestamp()),
        listing("old", (now - timedelta(days=9)).timestamp()),
        listing("absent", None),
    )
    p, sink = WindowProvider((recent, old, absent)), CollectSink()

    async def run():
        async with httpx.AsyncClient() as http:
            return await run_scope(sid, p, sink, http, factory)

    result = asyncio.run(run())
    assert result.status == "ok" and result.listings == 1
    assert sink.batches[0][1] == (recent,)
    admission = _state(factory, sid).cursor["_admission"]
    assert admission == {
        "accepted": 1,
        "refreshed": 0,
        "stale": 1,
        "missing_date": 1,
        "date_present": 2,
    }


@pytest.mark.parametrize("days", [True, 0, -1, "7", None, 10**30])
def test_invalid_window_fails_before_fetch(db, days):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    configure(factory, sid, days)
    p = WindowProvider(())

    async def run():
        async with httpx.AsyncClient() as http:
            await run_scope(sid, p, CollectSink(), http, factory)

    with pytest.raises(ProviderConfigError):
        asyncio.run(run())
    assert p.calls == 0 and _state(factory, sid) is None


def test_absent_dates_refresh_known_items_but_never_report_healthy_empty(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    configure(factory, sid, 7)

    async def run():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO source_listings(source_id,external_id,url_normalized) VALUES (:s,'known','https://example.test/known')"
                ),
                {"s": made["source"]},
            )
            await s.commit()
        sink = CollectSink()
        known = listing("known", None)
        async with httpx.AsyncClient() as http:
            result = await run_scope(
                sid, WindowProvider((known, listing("new", None))), sink, http, factory
            )
        assert sink.batches[0][1] == (known,)
        assert result.status == "partial" and result.error == "admission_missing_dates"

    asyncio.run(run())
    state = _state(factory, sid)
    assert state.last_complete_at is None and state.consecutive_failures == 1
    assert state.cursor["_admission"]["refreshed"] == 1


def test_window_is_semantic_and_omission_keeps_old_fingerprint():
    import hashlib

    p = WindowProvider(())
    assert p.params_fingerprint({}) == hashlib.sha256(b"{}").hexdigest()
    assert p.params_fingerprint({WINDOW: 7}) != p.params_fingerprint({WINDOW: 14})
    assert p.params_fingerprint({WINDOW: 7}) != p.params_fingerprint({})


def test_legacy_known_url_is_exact_and_source_scoped(db):  # noqa: F811  (la fixture, no una redefinición)
    import uuid

    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    configure(factory, sid, 7)
    known, fragment, foreign = (
        listing(ref, None) for ref in ("known", "split#B", "foreign")
    )

    async def run():
        async with factory() as s:
            sources = {}
            for source, url in (
                ("legacy:arbeitnow", known.url),
                ("legacy:arbeitnow", "https://example.test/split#A"),
                ("legacy:other", foreign.url),
            ):
                if source not in sources:
                    sources[source] = uuid.uuid4()
                    made["extra_sources"].append(sources[source])
                    await s.execute(
                        sa.text("INSERT INTO sources(id,name,tier) VALUES (:s,:n,0)"),
                        {"s": sources[source], "n": source},
                    )
                src, sl, vac = sources[source], uuid.uuid4(), uuid.uuid4()
                await s.execute(
                    sa.text("INSERT INTO vacancies(id) VALUES (:v)"), {"v": vac}
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listings(id,source_id,external_id,url_normalized) VALUES (:i,:s,:ref,:u)"
                    ),
                    {"i": sl, "s": src, "u": url, "ref": sl.hex},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listing_incarnations(source_listing_id,vacancy_id,seq,url) VALUES (:l,:v,1,:u)"
                    ),
                    {"l": sl, "v": vac, "u": url},
                )
            await s.commit()
        sink = CollectSink()
        async with httpx.AsyncClient() as http:
            result = await run_scope(
                sid, WindowProvider((known, fragment, foreign)), sink, http, factory
            )
        assert result.status == "partial"
        assert sink.batches[0][1] == (known,)
        assert result.detail["missing_date"] == 2

    asyncio.run(run())


def test_window_change_during_fetch_discards_old_result(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    configure(factory, sid, 7)

    class Changing(WindowProvider):
        async def fetch_new(self, params, cursor, http):
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"
                    ),
                    {"s": sid, "p": json.dumps({WINDOW: 14})},
                )
                await s.commit()
            return await super().fetch_new(params, cursor, http)

    async def run():
        sink = CollectSink()
        async with httpx.AsyncClient() as http:
            result = await run_scope(
                sid, Changing((listing("new", None),)), sink, http, factory
            )
        assert result.status == "stale" and sink.batches == []

    asyncio.run(run())
    assert _state(factory, sid) is None


def test_admission_sink_failure_does_not_commit_counters(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    configure(factory, sid, 7)

    async def run():
        async with httpx.AsyncClient() as http:
            result = await run_scope(
                sid,
                WindowProvider((listing("new", None),)),
                FailingSink(),
                http,
                factory,
            )
        assert result.status == "error"

    asyncio.run(run())
    state = _state(factory, sid)
    assert state.cursor is None and state.last_complete_at is None
    assert state.consecutive_failures == 1


def test_admission_empty_feed_stays_healthy_and_omission_keeps_full_feed(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    with_window, without_window = _seed_scopes(factory, made, 2)
    configure(factory, with_window, 7)

    async def run():
        sink = CollectSink()
        async with httpx.AsyncClient() as http:
            empty = await run_scope(
                with_window, WindowProvider(()), sink, http, factory
            )
            full = await run_scope(
                without_window,
                WindowProvider((listing("old", None),)),
                sink,
                http,
                factory,
            )
        assert empty.status == full.status == "ok"
        assert full.listings == 1 and full.detail == {}

    asyncio.run(run())


def test_cutoff_inclusive_and_fast_path_does_not_query():
    from jobhunt_core.harvest.admission import admit_listings

    now = datetime.now(timezone.utc)
    item = listing("boundary", (now - timedelta(days=7)).isoformat())

    class NoQuery:
        async def execute(self, *args, **kwargs):
            pytest.fail("all recent or policy off must not query known identities")

    async def run():
        result = FetchResult((item,), {})
        admitted = await admit_listings(
            NoQuery(), "arbeitnow", result, timedelta(days=7), now=now
        )
        assert (
            admitted.listings == (item,)
            and admitted.next_cursor["_admission"]["accepted"] == 1
        )
        assert await admit_listings(NoQuery(), "unknown", result, None) is result

    asyncio.run(run())


def test_unknown_window_source_fails_closed_and_internal_cursor_is_not_forwarded():
    from jobhunt_core.harvest.admission import admission_window
    from jobhunt_core.harvest.runner import _provider_cursor

    with pytest.raises(ProviderConfigError):
        admission_window("unknown", {WINDOW: 7})
    stored = {"_params_fp": "same", "_admission": {"stale": 100}, "page": 2}
    assert _provider_cursor(stored, "same", "test") == {"page": 2}
    assert _provider_cursor(stored, "changed", "test") is None
