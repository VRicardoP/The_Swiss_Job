"""Tests de caracterización para PatternAnalysisService.

Dos bloques:
- Funciones puras de tokenización/n-gramas (_tokenize, _ngrams, _extract_ngrams).
- Métodos async con DB: get_rejected_count y analyze_and_generate (umbral mínimo
  y generación de una sugerencia cuando varios rechazados comparten un patrón).
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.match_result import MatchResult
from services.pattern_analysis_service import (
    PatternAnalysisService,
    _extract_ngrams,
    _ngrams,
    _tokenize,
)
from tests.conftest import random_email

_PW = "TestPass123!"


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_lowercases_and_filters_stopwords(self):
        assert _tokenize("Der Primary Teacher") == ["primary", "teacher"]

    def test_removes_parenthesised_gender_markers(self):
        assert _tokenize("Lehrer (m/w)") == ["lehrer"]

    def test_removes_percentages(self):
        assert _tokenize("Teacher 80-100%") == ["teacher"]
        assert _tokenize("Editor 100%") == ["editor"]

    def test_drops_single_char_tokens(self):
        assert _tokenize("a x Teacher") == ["teacher"]

    def test_empty_when_all_stopwords(self):
        assert _tokenize("der die das und") == []


class TestNgrams:
    def test_unigrams(self):
        assert _ngrams(["a", "b", "c"], 1) == ["a", "b", "c"]

    def test_bigrams(self):
        assert _ngrams(["a", "b", "c"], 2) == ["a b", "b c"]

    def test_ngram_longer_than_tokens_is_empty(self):
        assert _ngrams(["a"], 2) == []

    def test_extract_produces_1_2_3_grams(self):
        grams = _extract_ngrams(["primary teacher"])
        assert "primary" in grams
        assert "teacher" in grams
        assert "primary teacher" in grams


# ---------------------------------------------------------------------------
# Métodos con DB
# ---------------------------------------------------------------------------


async def _register(client: AsyncClient) -> uuid.UUID:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": random_email(), "password": _PW, "gdpr_consent": True},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    return uuid.UUID(me.json()["id"])


async def _insert_job(db: AsyncSession, h: str, title: str, tags=None) -> None:
    valid = {c.key for c in Job.__table__.columns}
    data = {
        "hash": h,
        "source": "test",
        "title": title,
        "company": "C",
        "url": f"https://x.ch/{h}",
        "is_active": True,
        "tags": tags or [],
    }
    db.add(Job(**{k: v for k, v in data.items() if k in valid}))
    await db.commit()


async def _seed(db: AsyncSession, user_id: uuid.UUID, h: str, feedback=None) -> None:
    db.add(
        MatchResult(
            user_id=user_id,
            job_hash=h,
            score_embedding=0.1,
            score_salary=0.1,
            score_location=0.1,
            score_recency=0.1,
            score_llm=0.0,
            score_final=10.0,
            matching_skills=[],
            missing_skills=[],
            feedback=feedback,
        )
    )
    await db.commit()


@pytest.mark.anyio
class TestGetRejectedCount:
    async def test_counts_only_negative_feedback(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        await _insert_job(db_session, "r1", "Teacher A")
        await _insert_job(db_session, "r2", "Teacher B")
        await _insert_job(db_session, "r3", "Editor C")
        await _seed(db_session, user_id, "r1", feedback="dismissed")
        await _seed(db_session, user_id, "r2", feedback="thumbs_down")
        await _seed(db_session, user_id, "r3", feedback="thumbs_up")  # no cuenta

        count = await PatternAnalysisService(db_session).get_rejected_count(user_id)
        assert count == 2


@pytest.mark.anyio
class TestAnalyzeAndGenerate:
    async def test_returns_zero_below_min_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        await _insert_job(db_session, "z1", "Teacher A")
        await _seed(db_session, user_id, "z1", feedback="dismissed")

        generated = await PatternAnalysisService(db_session).analyze_and_generate(
            user_id, min_rejected=2
        )
        assert generated == 0

    async def test_generates_suggestion_for_shared_pattern(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        # 3 rechazados que comparten "teacher" → tasa de rechazo 100%
        for i in range(3):
            await _insert_job(db_session, f"t{i}", f"Primary Teacher {i}")
            await _seed(db_session, user_id, f"t{i}", feedback="dismissed")

        generated = await PatternAnalysisService(db_session).analyze_and_generate(
            user_id, min_rejected=2
        )
        assert generated >= 1
