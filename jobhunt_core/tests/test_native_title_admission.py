"""Preserve the legacy provider filter for new entries, never lose refreshes."""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
import sqlalchemy as sa

from jobhunt_core.harvest.provider import BaseProvider, ProviderConfigError
from jobhunt_core.harvest.providers import arbeitnow  # registers raw title extraction
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.types import FetchResult, RawListing
from jobhunt_core.tests.test_integration_harvest import (
    db,
    pytestmark,
    _seed_scopes,
    _state,
    CollectSink,
)

POLICY = "legacy_title_filter"


class Provider(BaseProvider):
    name = "arbeitnow"

    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    async def fetch_new(self, params, cursor, http):
        self.calls += 1
        assert POLICY not in params, "internal policy escaped to upstream"
        return FetchResult(self.rows, {})


def row(ref, title, date=True):
    return RawListing(
        ref,
        f"https://example.test/{ref}",
        {
            "title": title,
            "company_name": "Fixture",
            "created_at": datetime.now(timezone.utc).timestamp() if date else None,
        },
    )


def config(factory, sid, value):
    async def update():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:id"
                ),
                {
                    "p": json.dumps({POLICY: value, "admission_window_days": 7}),
                    "id": sid,
                },
            )
            await s.commit()

    asyncio.run(update())


def test_filter_blocks_only_new_titles_and_keeps_known_refresh(db):
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    config(factory, sid, True)
    known, new, good = (
        row("known", "Software Engineer"),
        row("new", "Software Engineer"),
        row("ok", "Content Editor"),
    )

    async def check():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO source_listings(source_id,external_id,url_normalized) VALUES(:s,'known',:u)"
                ),
                {"s": made["source"], "u": known.url},
            )
            await s.commit()
        sink = CollectSink()
        async with httpx.AsyncClient() as http:
            result = await run_scope(
                sid, Provider((known, new, good)), sink, http, factory
            )
        assert result.status == "ok"
        assert sink.batches[0][1] == (known, good)
        assert result.detail["title_excluded"] == 1

    asyncio.run(check())


def test_all_deliberately_excluded_is_not_missing_date_failure(db):
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    config(factory, sid, True)

    async def check():
        sink = CollectSink()
        async with httpx.AsyncClient() as http:
            result = await run_scope(
                sid,
                Provider((row("new", "Software Engineer", False),)),
                sink,
                http,
                factory,
            )
        assert result.status == "ok" and result.listings == 0
        assert result.detail["title_excluded"] == 1

    asyncio.run(check())
    assert _state(factory, sid).consecutive_failures == 0


@pytest.mark.parametrize("bad", [None, "true", 1, [], {}])
def test_invalid_title_policy_is_rejected_before_io(db, bad):
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    config(factory, sid, bad)
    provider = Provider(())

    async def check():
        async with httpx.AsyncClient() as http:
            await run_scope(sid, provider, CollectSink(), http, factory)

    with pytest.raises(ProviderConfigError):
        asyncio.run(check())
    assert provider.calls == 0


def test_title_policy_is_semantic_but_does_not_change_old_fingerprint():
    import hashlib

    provider = Provider(())
    assert provider.params_fingerprint({}) == hashlib.sha256(b"{}").hexdigest()
    assert provider.params_fingerprint({POLICY: True}) != provider.params_fingerprint(
        {POLICY: False}
    )


def test_title_policy_change_during_fetch_discards_result(db):
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)
    config(factory, sid, False)

    class Changing(Provider):
        async def fetch_new(self, params, cursor, http):
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:id"
                    ),
                    {"p": json.dumps({POLICY: True}), "id": sid},
                )
                await s.commit()
            return await super().fetch_new(params, cursor, http)

    async def check():
        sink = CollectSink()
        async with httpx.AsyncClient() as http:
            result = await run_scope(
                sid, Changing((row("new", "Software Engineer"),)), sink, http, factory
            )
        assert result.status == "stale" and sink.batches == []

    asyncio.run(check())
    assert _state(factory, sid) is None


@pytest.mark.parametrize("params", [{}, {POLICY: False}])
def test_filter_is_opt_in_and_preserves_previous_scope(db, params):
    factory, made = db
    (sid,) = _seed_scopes(factory, made, 1)

    async def check():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:id"
                ),
                {"p": json.dumps(params), "id": sid},
            )
            await s.commit()
        sink = CollectSink()
        listing = row("new", "Software Engineer", False)
        async with httpx.AsyncClient() as http:
            result = await run_scope(sid, Provider((listing,)), sink, http, factory)
        assert result.status == "ok" and sink.batches[0][1] == (listing,)
        assert result.detail == {}

    asyncio.run(check())


def test_frozen_title_rule_matches_handover_snapshot():
    import hashlib
    from jobhunt_core.harvest.legacy_title_policy import (
        LEGACY_TITLE_KEYWORDS,
        excluded_title,
    )

    raw = json.dumps(
        sorted(LEGACY_TITLE_KEYWORDS), ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert len(LEGACY_TITLE_KEYWORDS) == 72
    assert (
        hashlib.sha256(raw).hexdigest()
        == "0ee1f83fd569d622490ad53bb7de71526194f922efcfbd8ad0812acd06e8a883"
    )
    assert excluded_title("Senior SOFTWARE ENGINEER")
    assert excluded_title("Logopädin")
    assert not excluded_title("Content Editor")
    assert not excluded_title(None)


def test_unknown_title_policy_source_fails_closed():
    from jobhunt_core.harvest.admission import title_filter_enabled

    with pytest.raises(ProviderConfigError):
        title_filter_enabled("unknown", {POLICY: True})
