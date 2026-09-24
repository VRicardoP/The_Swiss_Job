"""Retained CV tasks must not overwrite edits while legacy engines retire.

Two real sessions, deterministic I/O boundaries, no external LLM/encoder calls.
"""

import uuid
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from sqlalchemy import delete, select, update

from models.user_profile import UserProfile
from tests.conftest import TestSessionLocal
from tests.test_embedding_tasks import _create_user_with_profile


@pytest.mark.parametrize(
    "mutation",
    [
        {"cv_text": "A newer CV"},
        {"title": "My manual title"},
        {"cv_text": None},
    ],
)
async def test_old_analysis_cannot_overwrite_newer_profile(
    db_session, monkeypatch, mutation
):
    from tasks.profile_tasks import _analyze_and_autofill_async

    uid = uuid.UUID(
        await _create_user_with_profile(db_session, cv_text="Old CV", title="Original")
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
    encoder = Mock()
    encoder.encode.return_value = np.ones(384, dtype=np.float32)
    monkeypatch.setattr("services.job_matcher.JobMatcher", lambda: encoder)
    result = await _analyze_and_autofill_async(str(uid))
    assert result["status"] == "discarded_profile_changed"
    async with TestSessionLocal() as check:
        p = (
            await check.execute(select(UserProfile).where(UserProfile.user_id == uid))
        ).scalar_one()
        assert p.title == mutation.get("title", "Original")
        assert p.cv_text == mutation.get("cv_text", "Old CV")
        assert p.skills == [] and p.cv_embedding is None
    encoder.encode.assert_not_called()


@pytest.mark.parametrize("task_kind", ["autofill", "standalone"])
@pytest.mark.parametrize("deleted", [False, True])
async def test_old_vector_cannot_publish_after_edit_or_delete(
    db_session, monkeypatch, task_kind, deleted
):
    from tasks.embedding_tasks import _generate_profile_embedding_async
    from tasks.profile_tasks import _analyze_and_autofill_async

    uid = uuid.UUID(
        await _create_user_with_profile(db_session, cv_text="Old CV", title="Original")
    )
    monkeypatch.setattr("database.task_session", TestSessionLocal)
    monkeypatch.setattr(
        "redis.asyncio.from_url",
        lambda *_: Mock(publish=AsyncMock(), aclose=AsyncMock()),
    )
    monkeypatch.setattr(
        "services.cv_analyzer.CVAnalyzer.extract_fields", AsyncMock(return_value={})
    )
    monkeypatch.setattr("services.job_matcher.JobMatcher", Mock())

    async def inference(_fn, *_args):
        async with TestSessionLocal() as other:
            stmt = (
                delete(UserProfile)
                if deleted
                else update(UserProfile).values(
                    cv_text="New CV",
                    cv_embedding=np.ones(384, dtype=np.float32).tolist(),
                )
            )
            await other.execute(stmt.where(UserProfile.user_id == uid))
            await other.commit()
        return np.zeros(384, dtype=np.float32)

    monkeypatch.setattr("asyncio.to_thread", inference)
    task = (
        _analyze_and_autofill_async
        if task_kind == "autofill"
        else _generate_profile_embedding_async
    )
    result = await task(str(uid))
    assert result["status"] == "discarded_profile_changed"
    async with TestSessionLocal() as check:
        p = (
            await check.execute(select(UserProfile).where(UserProfile.user_id == uid))
        ).scalar_one_or_none()
        if deleted:
            assert p is None
        else:
            # No asumir ndarray: pgvector devuelve `list` con las versiones
            # actuales, y `lista == 1` es un booleano, no una comparación
            # elemento a elemento. La prueba se caía por el tipo, no por el
            # código que quiere comprobar.
            assert p.cv_text == "New CV" and all(v == 1 for v in p.cv_embedding)


async def test_current_analysis_and_vector_publish_normally(db_session, monkeypatch):
    from tasks.profile_tasks import _analyze_and_autofill_async

    uid = uuid.UUID(
        await _create_user_with_profile(
            db_session, cv_text="Current CV", title="Original"
        )
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
    encoder = Mock()
    encoder.encode.return_value = np.ones(384, dtype=np.float32)
    monkeypatch.setattr("services.job_matcher.JobMatcher", lambda: encoder)
    result = await _analyze_and_autofill_async(str(uid))
    assert result["status"] == "success"
    async with TestSessionLocal() as check:
        p = (
            await check.execute(select(UserProfile).where(UserProfile.user_id == uid))
        ).scalar_one()
        assert p.title == "New AI title" and p.skills == ["Python"]
        assert all(v == 1 for v in p.cv_embedding)
    encoder.encode.assert_called_once_with("New AI title Current CV Python")
