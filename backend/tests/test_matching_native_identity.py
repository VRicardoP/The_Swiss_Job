"""A native feed must not turn a known vacancy into an ambiguous old alias."""

import uuid
from unittest.mock import AsyncMock

import pytest

from config import settings
from services.matching.core_client import CoreMatching
from services.matching.identity import set_profile_link
from services.routing import set_routing
from tests.test_matching_contract import _register, _match_dto


@pytest.mark.asyncio
@pytest.mark.parametrize("source,keep_school_ref", [
    ("legacy:remotive", False),
    ("legacy:swiss_schools_ecolint", True),
])
async def test_native_feed_uses_canonical_id_except_school_commands(
    client, db_session, monkeypatch, source, keep_school_ref,
):
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    uid, _ = await _register(client)
    await set_profile_link(db_session, uid, uuid.uuid4())
    await set_routing(db_session, "matching", "core_primary", profile_id=uid)
    item = _match_dto("python_zurich", legacy=True)
    item["vacancy"]["primary_listing"]["source"] = source
    original = item["vacancy"]["primary_listing"]["external_id"]
    matcher = CoreMatching(db_session, client_factory=lambda: None)
    # Devuelve (items, total): el total lo informa el core desde el punto 5,
    # y sin el el consumidor vuelve a contar recorriendo el feed.
    monkeypatch.setattr(matcher, "_fetch_full_feed", AsyncMock(return_value=([item], 1)))
    rows, total = await matcher.results(uid)
    assert total == 1
    assert rows[0]["match"].job_hash == (
        original if keep_school_ref else item["vacancy"]["id"]
    )
