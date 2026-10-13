"""CV autofill must not overwrite a concurrent user edit."""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, update

from core.security import hash_password
from models.user import User
from models.user_profile import UserProfile
from tests.conftest import TestSessionLocal


async def _create_user_with_profile(db, *, cv_text, title):
    user_id = uuid.uuid4()
    db.add(
        User(
            id=user_id,
            email=f"test-{user_id.hex[:8]}@example.com",
            hashed_password=hash_password("TestPass1!"),
            gdpr_consent=True,
        )
    )
    await db.flush()
    db.add(UserProfile(user_id=user_id, cv_text=cv_text, title=title, skills=[]))
    await db.commit()
    return user_id


@pytest.mark.parametrize(
    "mutation",
    [{"cv_text": "A newer CV"}, {"title": "My manual title"}, {"cv_text": None}],
)
async def test_old_analysis_cannot_overwrite_newer_profile(
    db_session, monkeypatch, mutation
):
    from tasks.profile_tasks import _analyze_and_autofill_async

    uid = await _create_user_with_profile(
        db_session, cv_text="Old CV", title="Original"
    )
    monkeypatch.setattr("database.task_session", TestSessionLocal)
    monkeypatch.setattr(
        "redis.asyncio.from_url",
        lambda *_: Mock(publish=AsyncMock(), aclose=AsyncMock()),
    )

    async def extract(_self, _text):
        async with TestSessionLocal() as other:
            await other.execute(
                update(UserProfile).where(UserProfile.user_id == uid).values(**mutation)
            )
            await other.commit()
        return {"title": "Stale AI title", "skills": ["stale skill"]}

    monkeypatch.setattr("services.cv_analyzer.CVAnalyzer.extract_fields", extract)
    result = await _analyze_and_autofill_async(str(uid))
    assert result["status"] == "discarded_profile_changed"
    async with TestSessionLocal() as check:
        profile = await check.scalar(
            select(UserProfile).where(UserProfile.user_id == uid)
        )
        assert profile.title == mutation.get("title", "Original")
        assert profile.cv_text == mutation.get("cv_text", "Old CV")
        assert profile.skills == [] and profile.cv_embedding is None


async def test_current_analysis_publishes_profile_fields(db_session, monkeypatch):
    from tasks.profile_tasks import _analyze_and_autofill_async

    uid = await _create_user_with_profile(
        db_session, cv_text="Current CV", title="Original"
    )
    monkeypatch.setattr("database.task_session", TestSessionLocal)
    monkeypatch.setattr(
        "redis.asyncio.from_url",
        lambda *_: Mock(publish=AsyncMock(), aclose=AsyncMock()),
    )
    monkeypatch.setattr(
        "services.cv_analyzer.CVAnalyzer.extract_fields",
        AsyncMock(return_value={"title": "New AI title", "skills": ["Python"]}),
    )
    result = await _analyze_and_autofill_async(str(uid))
    assert result["status"] == "success"
    async with TestSessionLocal() as check:
        profile = await check.scalar(
            select(UserProfile).where(UserProfile.user_id == uid)
        )
        assert profile.title == "New AI title" and profile.skills == ["Python"]
        assert profile.cv_embedding is None
