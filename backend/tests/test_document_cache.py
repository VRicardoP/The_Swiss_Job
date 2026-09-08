"""Cache is an input-versioned hint, never the document authority (E.4)."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from config import settings
from main import app
from models.job import Job
from models.match_result import MatchResult
from models.user import User
from tests.test_documents import (
    _auth,
    _gemini_on,
    _insert_job,
    _register_and_get_token,
    _set_cv_text,
)


class MemoryCache:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value


@pytest.fixture
async def generation(client, db_session, monkeypatch):
    token, email = await _register_and_get_token(client)
    await _set_cv_text(db_session, email)
    job_hash = await _insert_job(db_session)
    cache = MemoryCache()
    provider = _gemini_on()
    monkeypatch.setattr(app.state, "redis_client", cache, raising=False)
    monkeypatch.setattr("routers.documents._get_gemini", lambda: provider)

    async def generate():
        result = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "cv"},
        )
        assert result.status_code == 200, result.text
        return result.json()

    return generate, cache, provider, token, email, job_hash


@pytest.mark.anyio
async def test_cache_retains_only_id_and_reuses_authoritative_document(generation):
    generate, cache, provider, *_ = generation
    first = await generate()
    assert await generate() == first
    assert provider.get_chat_response.await_count == 1
    assert list(cache.values.values()) == [first["id"]]


@pytest.mark.anyio
async def test_late_cache_publication_cannot_resurrect_deleted_document(
    client, generation
):
    generate, cache, provider, token, *_ = generation
    first = await generate()
    late_publication = dict(cache.values)
    response = await client.delete(
        f"/api/v1/documents/{first['id']}",
        headers=_auth(token),
    )
    assert response.status_code == 204
    # The generating request can publish its Redis hint AFTER delete commits.
    # A DEL in the delete endpoint alone cannot close this interleaving.
    cache.values.update(late_publication)
    second = await generate()
    assert second["id"] != first["id"]
    assert provider.get_chat_response.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["cv", "job", "match", "model"])
async def test_changed_generation_inputs_never_reuse_old_document(
    generation,
    db_session,
    monkeypatch,
    changed,
):
    generate, _, provider, _, email, job_hash = generation
    first = await generate()
    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    if changed == "cv":
        await db_session.refresh(user, ["profile"])
        user.profile.cv_text = "A different CV: school teaching and child care."
    elif changed == "job":
        job = (
            await db_session.execute(select(Job).where(Job.hash == job_hash))
        ).scalar_one()
        job.description = "A different role, responsibilities and required experience."
    elif changed == "match":
        db_session.add(
            MatchResult(
                user_id=user.id,
                job_hash=job_hash,
                score_embedding=0,
                score_salary=0,
                score_location=0,
                score_recency=0,
                score_final=0,
                matching_skills=["Python"],
                missing_skills=["Rust"],
            )
        )
    else:
        monkeypatch.setattr(settings, "GEMINI_MODEL", "different-document-model")
    await db_session.commit()
    second = await generate()
    assert second["id"] != first["id"]
    assert provider.get_chat_response.await_count == 2


@pytest.mark.anyio
async def test_cache_content_is_not_trusted(generation):
    generate, cache, provider, *_ = generation
    first = await generate()
    # Old full-DTO entries and malformed hints must not become API responses.
    import json

    for invalid in [
        json.dumps({**first, "content": "FORGED"}),
        "not-a-uuid",
        str(uuid.uuid4()),
    ]:
        cache.values = {key: invalid for key in cache.values}
        response = await generate()
        assert response["content"] != "FORGED"
    assert provider.get_chat_response.await_count == 4


@pytest.mark.anyio
async def test_redis_unavailable_does_not_break_generation(generation):
    generate, cache, _, *_ = generation
    cache.get = AsyncMock(side_effect=ConnectionError("unavailable"))
    cache.set = AsyncMock(side_effect=ConnectionError("unavailable"))
    assert (await generate())["content"]


@pytest.mark.anyio
async def test_cache_cannot_disclose_other_users_document(
    client, db_session, generation
):
    from services.documents.local import LocalDocuments

    generate, cache, provider, _, _, job_hash = generation
    await generate()
    _, other_email = await _register_and_get_token(client)
    other = (
        await db_session.execute(select(User).where(User.email == other_email))
    ).scalar_one()
    foreign = await LocalDocuments(db_session).create(
        other.id,
        job_hash,
        "cv",
        "OTHER USER PRIVATE CV",
        "en",
    )
    cache.values = {key: str(foreign.id) for key in cache.values}
    result = await generate()
    assert result["id"] != str(foreign.id)
    assert result["content"] != "OTHER USER PRIVATE CV"
    assert provider.get_chat_response.await_count == 2


@pytest.mark.anyio
async def test_storage_error_on_cache_hit_does_not_generate_again(
    generation, monkeypatch
):
    generate, _, provider, *_ = generation
    await generate()
    monkeypatch.setattr(
        "services.documents.local.LocalDocuments.list",
        AsyncMock(side_effect=RuntimeError("storage unavailable")),
    )
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await generate()
    assert provider.get_chat_response.await_count == 1
