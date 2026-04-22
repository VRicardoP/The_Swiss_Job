"""MatchService — orchestrates the 3-stage AI matching pipeline.

Stage 1: pgvector cosine similarity on ALL active jobs (full catalogue scan)
Stage 2: Multi-factor scoring (embedding + salary + location + recency)
Stage 3: LLM re-ranking via Groq (top N only) — score_llm + explanation

Results are filtered by a minimum score threshold and persisted.
Jobs previously dismissed or thumbs-downed are excluded from new runs.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.job import Job
from models.match_result import MatchResult
from models.user_profile import UserProfile
from services.groq_service import GroqService
from services.job_matcher import DEFAULT_WEIGHTS, JobMatcher

logger = logging.getLogger(__name__)

# Feedback values that exclude a job from future match runs
_NEGATIVE_FEEDBACK = {"dismissed", "thumbs_down"}


class MatchService:
    """Orchestrates the full matching pipeline for a user."""

    def __init__(self, db: AsyncSession, groq: GroqService | None = None):
        self.db = db
        self.matcher = JobMatcher()
        self.groq = groq

    async def run_matching(
        self,
        user_id: uuid.UUID,
        min_score: float = settings.MATCH_SCORE_THRESHOLD,
    ) -> dict:
        """Run the complete matching pipeline for a user.

        Returns dict with status, total_candidates, results_count.
        """
        profile = await self._get_profile(user_id)
        if profile is None:
            return {"status": "error", "reason": "profile_not_found"}

        if profile.cv_embedding is None:
            return {"status": "no_embedding", "total_candidates": 0, "results_count": 0}

        # TD-11: per-user configurable weights
        weights = profile.score_weights or DEFAULT_WEIGHTS

        # Load dismissed job hashes so they don't reappear
        excluded_hashes = await self._get_excluded_hashes(user_id)

        # Stage 1: fetch ALL active jobs with embeddings, ordered by cosine similarity
        candidates = await self._stage1_vector_search(
            profile.cv_embedding, excluded_hashes
        )

        if not candidates:
            return {"status": "no_jobs", "total_candidates": 0, "results_count": 0}

        total_candidates = len(candidates)

        # Stage 2: multi-factor scoring on ALL candidates
        scored = self._stage2_multifactor_score(
            profile=profile,
            candidates=candidates,
            weights=weights,
        )

        # Filter by minimum score threshold
        qualified = [r for r in scored if r["score_final"] >= min_score]

        if not qualified:
            # Still persist an empty set (clears old results)
            await self._save_results(user_id, [])
            return {
                "status": "no_jobs",
                "total_candidates": total_candidates,
                "results_count": 0,
            }

        # Stage 3: LLM re-ranking (top N candidates only)
        llm_top = settings.MATCH_LLM_RERANK_TOP
        if self.groq and self.groq.is_available and len(qualified) > 0:
            # Only send top N to LLM, keep the rest as-is
            head = qualified[:llm_top]
            tail = qualified[llm_top:]

            head = await self._stage3_llm_rerank(
                profile=profile,
                scored_results=head,
                weights=weights,
            )

            # Merge and re-sort: LLM-ranked head + unranked tail
            qualified = head + tail
            qualified.sort(key=lambda x: x["score_final"], reverse=True)

        # Persist ALL results above threshold (replace previous)
        await self._save_results(user_id, qualified)

        return {
            "status": "success",
            "total_candidates": total_candidates,
            "results_count": len(qualified),
        }

    async def get_results(
        self,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Get persisted match results with job details.

        Returns (results_with_jobs, total_count).
        """
        count_stmt = (
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.user_id == user_id)
        )
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = (
            select(MatchResult, Job)
            .join(Job, MatchResult.job_hash == Job.hash)
            .where(MatchResult.user_id == user_id)
            .order_by(MatchResult.score_final.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()

        results = [{"match": match, "job": job} for match, job in rows]
        return results, total

    async def submit_feedback(
        self,
        user_id: uuid.UUID,
        job_hash: str,
        feedback: str,
    ) -> MatchResult | None:
        """Record user feedback on a match result."""
        stmt = select(MatchResult).where(
            MatchResult.user_id == user_id,
            MatchResult.job_hash == job_hash,
        )
        result = await self.db.execute(stmt)
        match = result.scalar_one_or_none()

        if match is None:
            return None

        match.feedback = feedback
        await self.db.commit()
        await self.db.refresh(match)
        return match

    async def clear_feedback(
        self,
        user_id: uuid.UUID,
        job_hash: str,
    ) -> MatchResult | None:
        """Elimina el feedback explícito de un resultado (lo pone a None)."""
        stmt = select(MatchResult).where(
            MatchResult.user_id == user_id,
            MatchResult.job_hash == job_hash,
        )
        result = await self.db.execute(stmt)
        match = result.scalar_one_or_none()

        if match is None:
            return None

        match.feedback = None
        await self.db.commit()
        await self.db.refresh(match)
        return match

    async def get_saved_jobs(
        self,
        user_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Devuelve los empleos marcados como thumbs_up o applied."""
        _POSITIVE_FEEDBACK = ("thumbs_up", "applied")

        count_stmt = (
            select(func.count())
            .select_from(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.feedback.in_(_POSITIVE_FEEDBACK),
            )
        )
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = (
            select(MatchResult, Job)
            .join(Job, MatchResult.job_hash == Job.hash)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.feedback.in_(_POSITIVE_FEEDBACK),
            )
            .order_by(MatchResult.score_final.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()
        results = [{"match": match, "job": job} for match, job in rows]
        return results, total

    async def record_implicit_feedback(
        self,
        user_id: uuid.UUID,
        job_hash: str,
        action: str,
        duration_ms: int | None = None,
    ) -> MatchResult | None:
        """Record implicit feedback signal on a match result.

        Signals and their weights:
          opened -> +0.1, view_time (>10s) -> +0.2, saved -> +0.5,
          applied -> +1.0, dismissed -> -0.3, skipped -> -0.1
        """
        stmt = select(MatchResult).where(
            MatchResult.user_id == user_id,
            MatchResult.job_hash == job_hash,
        )
        result = await self.db.execute(stmt)
        match = result.scalar_one_or_none()
        if match is None:
            return None

        signals = match.feedback_implicit or []
        signal = {"action": action}
        if duration_ms is not None:
            signal["duration_ms"] = duration_ms
        signals.append(signal)
        match.feedback_implicit = signals

        await self.db.commit()
        await self.db.refresh(match)
        return match

    # --- Internal methods ---

    async def _get_profile(self, user_id: uuid.UUID) -> UserProfile | None:
        result = await self.db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _get_excluded_hashes(self, user_id: uuid.UUID) -> set[str]:
        """Load job hashes that the user dismissed or thumbs-downed."""
        stmt = (
            select(MatchResult.job_hash)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.feedback.in_(_NEGATIVE_FEEDBACK),
            )
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def _stage1_vector_search(
        self, profile_embedding: list, excluded_hashes: set[str] | None = None
    ) -> list[Job]:
        """Fetch ALL active jobs with embeddings, ordered by cosine similarity.

        Jobs with hashes in excluded_hashes are skipped (previously dismissed).
        """
        conditions = [
            Job.is_active.is_(True),
            Job.duplicate_of.is_(None),
            Job.embedding.is_not(None),
            *Job.exclude_student_conditions(),
        ]
        if excluded_hashes:
            conditions.append(Job.hash.not_in(excluded_hashes))

        stmt = (
            select(Job)
            .where(*conditions)
            .order_by(Job.embedding.cosine_distance(profile_embedding))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    def _stage2_multifactor_score(
        self,
        profile: UserProfile,
        candidates: list[Job],
        weights: dict,
    ) -> list[dict]:
        """Score each candidate with multi-factor weights. Returns sorted list."""
        import numpy as np

        profile_emb = np.array(profile.cv_embedding)
        now = datetime.now(timezone.utc)

        results = []
        for job in candidates:
            job_emb = np.array(job.embedding)

            emb_score = self.matcher.compute_embedding_score(profile_emb, job_emb)
            salary_score = JobMatcher.compute_salary_match(
                profile.salary_min,
                profile.salary_max,
                job.salary_min_chf,
                job.salary_max_chf,
            )
            location_score = JobMatcher.compute_location_match(
                profile.locations or [], job.location
            )
            language_score = JobMatcher.compute_language_match(
                profile.languages or [], job.language
            )
            first_seen = job.first_seen_at
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=timezone.utc)
            days_old = (now - first_seen).days
            rec_score = JobMatcher.compute_recency_score(days_old)

            final = self.matcher.compute_final_score(
                embedding_score=emb_score,
                salary_score=salary_score,
                location_score=location_score,
                recency_score=rec_score,
                llm_score=0.0,
                language_score=language_score,
                weights=weights,
            )

            matching, missing = self._compute_skill_overlap(
                profile.skills or [], job.tags or []
            )

            results.append(
                {
                    "job": job,
                    "score_embedding": round(emb_score, 4),
                    "score_salary": round(salary_score, 4),
                    "score_location": round(location_score, 4),
                    "score_recency": round(rec_score, 4),
                    "score_llm": 0.0,
                    "score_language": round(language_score, 4),
                    "score_final": final,
                    "matching_skills": matching,
                    "missing_skills": missing,
                }
            )

        results.sort(key=lambda r: r["score_final"], reverse=True)
        return results

    @staticmethod
    def _compute_skill_overlap(
        user_skills: list[str], job_tags: list[str]
    ) -> tuple[list[str], list[str]]:
        """Compare user skills with job tags (case-insensitive)."""
        user_lower = {s.lower() for s in user_skills}
        job_lower = {t.lower() for t in job_tags}

        matching = sorted(user_lower & job_lower)
        missing = sorted(job_lower - user_lower)
        return matching, missing

    async def _stage3_llm_rerank(
        self,
        profile: UserProfile,
        scored_results: list[dict],
        weights: dict,
    ) -> list[dict]:
        """Stage 3: LLM re-ranking via Groq. Updates score_llm + explanation."""
        # Build candidate dicts for the LLM prompt
        candidates_for_llm = []
        for r in scored_results:
            job = r["job"]
            candidates_for_llm.append(
                {
                    "title": job.title or "",
                    "company": job.company or "",
                    "description": job.description or "",
                    "tags": job.tags or [],
                    "location": job.location or "",
                    "remote": job.remote or False,
                    "language": job.language or "",
                    "contract_type": job.contract_type.value if job.contract_type else "",
                }
            )

        profile_text = profile.cv_text or ""
        profile_skills = profile.skills or []

        llm_results = await self.groq.rerank_jobs(
            profile_text=profile_text,
            profile_skills=profile_skills,
            candidates=candidates_for_llm,
        )

        # Build lookup: global_index -> llm result
        llm_by_index = {r["global_index"]: r for r in llm_results}

        for i, r in enumerate(scored_results):
            llm_data = llm_by_index.get(i)
            if llm_data and llm_data.get("score", 0) > 0:
                llm_score = llm_data["score"]
                r["score_llm"] = round(llm_score / 100.0, 4)
                r["explanation"] = llm_data.get("reason", "")

                # Merge LLM skill analysis if richer than rule-based
                if llm_data.get("matching_skills"):
                    r["matching_skills"] = llm_data["matching_skills"]
                if llm_data.get("missing_skills"):
                    r["missing_skills"] = llm_data["missing_skills"]

                # Recalculate final score with real LLM score
                r["score_final"] = self.matcher.compute_final_score(
                    embedding_score=r["score_embedding"],
                    salary_score=r["score_salary"],
                    location_score=r["score_location"],
                    recency_score=r["score_recency"],
                    llm_score=r["score_llm"],
                    language_score=r.get("score_language", 0.5),
                    weights=weights,
                )

        # Re-sort after LLM scoring
        scored_results.sort(key=lambda x: x["score_final"], reverse=True)
        return scored_results

    async def _save_results(
        self,
        user_id: uuid.UUID,
        results: list[dict],
    ) -> None:
        """Persist match results, replacing previous ones for this user.

        Preserves feedback from previous runs: if a job_hash already had
        positive feedback (thumbs_up, applied), it is carried over.
        """
        # Load existing positive feedback to preserve
        prev_stmt = select(MatchResult.job_hash, MatchResult.feedback).where(
            MatchResult.user_id == user_id,
            MatchResult.feedback.is_not(None),
            MatchResult.feedback.not_in(_NEGATIVE_FEEDBACK),
        )
        prev_rows = (await self.db.execute(prev_stmt)).all()
        prev_feedback = {row[0]: row[1] for row in prev_rows}

        # Delete all previous results
        await self.db.execute(delete(MatchResult).where(MatchResult.user_id == user_id))

        for r in results:
            job = r["job"]
            match = MatchResult(
                user_id=user_id,
                job_hash=job.hash,
                score_embedding=r["score_embedding"],
                score_salary=r["score_salary"],
                score_location=r["score_location"],
                score_recency=r["score_recency"],
                score_llm=r["score_llm"],
                score_final=r["score_final"],
                explanation=r.get("explanation"),
                matching_skills=r["matching_skills"],
                missing_skills=r["missing_skills"],
                feedback=prev_feedback.get(job.hash),
            )
            self.db.add(match)

        await self.db.commit()
