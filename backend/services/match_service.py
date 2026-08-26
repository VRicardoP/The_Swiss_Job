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

from sqlalchemy import delete, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from models.job import Job
from models.job_filter import JobFilter
from models.match_result import NEGATIVE_FEEDBACK, MatchResult
from models.user_profile import UserProfile
from services.groq_service import GroqService
from services.job_classifier import CATEGORY_MULTIPLIERS
from services.job_matcher import DEFAULT_WEIGHTS, JobMatcher
from services.skill_synonyms import filter_missing_skills

logger = logging.getLogger(__name__)

# Clave interna de los scored results: True cuando el LLM emitió veredicto para
# ese result en ESTA corrida (G3/P3-9). Nunca sale del pipeline de matching.
LLM_VERDICT_KEY = "llm_verdict"


class MatchService:
    """Orchestrates the full matching pipeline for a user."""

    def __init__(
        self,
        db: AsyncSession,
        groq: GroqService | None = None,
        gemini: object | None = None,
    ):
        self.db = db
        self.matcher = JobMatcher()
        self.groq = groq
        # Segundo proveedor LLM (GeminiService) usado como fallback del
        # re-ranking cuando Groq falla/caduca. Interfaz get_chat_response.
        self.gemini = gemini

    @property
    def _llm_available(self) -> bool:
        """True si hay al menos un proveedor LLM disponible (Groq o Gemini)."""
        groq_ok = bool(self.groq and self.groq.is_available)
        gemini_ok = bool(self.gemini and getattr(self.gemini, "is_available", False))
        return groq_ok or gemini_ok

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

        # Load user-approved exclusion filters
        active_filters = await self._get_active_filters(user_id)

        # Stage 1: fetch ALL active jobs with embeddings, ordered by cosine similarity
        candidates = await self._stage1_vector_search(
            profile.cv_embedding, excluded_hashes, active_filters
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

        # G1/P3-15: el umbral se aplicaba ANTES del rerank con llm_score=0 —
        # un job a 34.9 pre-LLM que el LLM subiría por encima del umbral se
        # descartaba sin verlo, y el hándicap crecía con el peso llm del
        # usuario. Prefiltro con el margen máximo que el LLM puede aportar
        # (w_llm × 100); el umbral REAL se aplica después del rerank. El LLM
        # solo suma (llm_score ≥ 0): nada que hoy pasara el umbral se pierde.
        w_llm = weights.get("llm", 0.0)
        prefilter_score = max(min_score - w_llm * 100.0, 0.0)
        qualified = [r for r in scored if r["score_final"] >= prefilter_score]

        # Stage 3: LLM re-ranking adaptativo (solo si hay proveedor LLM).
        if qualified:
            qualified = await self._maybe_rerank(qualified, profile, weights)

        # Umbral definitivo, ya con el factor LLM incorporado.
        qualified = [r for r in qualified if r["score_final"] >= min_score]

        if not qualified:
            # Still persist an empty set (clears old results)
            await self._save_results(user_id, [])
            return {
                "status": "no_jobs",
                "total_candidates": total_candidates,
                "results_count": 0,
            }

        # Persist ALL results above threshold (replace previous)
        await self._save_results(user_id, qualified)

        # Dispara push inmediato si algún job de la watchlist supera 70
        # (combinando score_final + urgency boost).
        await self._notify_watchlist_priority(user_id, qualified)

        return {
            "status": "success",
            "total_candidates": total_candidates,
            "results_count": len(qualified),
        }

    async def _maybe_rerank(
        self, qualified: list[dict], profile, weights: dict
    ) -> list[dict]:
        """Stage 3: re-ranking LLM adaptativo. Devuelve `qualified` (re-ordenado si aplica).

        Corre si hay CUALQUIER proveedor LLM (Groq o su fallback Gemini):
        - Pool pequeño (<= MATCH_LLM_RERANK_MAX): re-rankea TODAS (sin tope) — el caso
          normal con extracción incremental, nada relevante se queda sin pulir.
        - Avalancha (> MATCH_LLM_RERANK_MAX): limita a MATCH_LLM_RERANK_TOP para
          proteger el crédito de IA.
        """
        if not (self.groq is not None and self._llm_available and len(qualified) > 0):
            return qualified

        rerank_n = (
            len(qualified)
            if len(qualified) <= settings.MATCH_LLM_RERANK_MAX
            else settings.MATCH_LLM_RERANK_TOP
        )
        head = qualified[:rerank_n]
        tail = qualified[rerank_n:]

        head = await self._stage3_llm_rerank(
            profile=profile,
            scored_results=head,
            weights=weights,
        )

        # Merge and re-sort: LLM-ranked head + unranked tail
        qualified = head + tail
        qualified.sort(key=lambda x: x["score_final"], reverse=True)
        return qualified

    # --- Internal methods ---

    async def _get_profile(self, user_id: uuid.UUID) -> UserProfile | None:
        result = await self.db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _get_excluded_hashes(self, user_id: uuid.UUID) -> set[str]:
        """Load job hashes that the user dismissed or thumbs-downed."""
        stmt = select(MatchResult.job_hash).where(
            MatchResult.user_id == user_id,
            MatchResult.feedback.in_(NEGATIVE_FEEDBACK),
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def _get_active_filters(self, user_id: uuid.UUID) -> list[dict]:
        """Carga los filtros de exclusión activos del usuario."""
        stmt = select(JobFilter).where(
            JobFilter.user_id == user_id,
            JobFilter.is_active.is_(True),
        )
        result = await self.db.execute(stmt)
        return [
            {"type": f.filter_type, "pattern": f.pattern}
            for f in result.scalars().all()
        ]

    async def _stage1_vector_search(
        self,
        profile_embedding: list,
        excluded_hashes: set[str] | None = None,
        active_filters: list[dict] | None = None,
    ) -> list[Job]:
        """Fetch ALL active jobs with embeddings, ordered by cosine similarity.

        Excluye jobs con feedback negativo y aplica filtros aprobados por el usuario.
        """
        conditions = [
            Job.is_active.is_(True),
            Job.duplicate_of.is_(None),
            Job.embedding.is_not(None),
            *Job.exclude_student_conditions(),
        ]
        if excluded_hashes:
            conditions.append(Job.hash.not_in(excluded_hashes))

        # Aplicar filtros de título (ILIKE) y de tags (JSONB @> operator)
        for f in active_filters or []:
            if f["type"] == "title_contains":
                # Escapa wildcards de ILIKE para tratar el patrón como literal
                safe = (
                    f["pattern"]
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                conditions.append(~Job.title.ilike(f"%{safe}%", escape="\\"))
            elif f["type"] == "tag_contains":
                # tag IS NULL (sin tags) → incluir; tags no contiene el patrón → incluir.
                # G1/P2-16: los patrones se guardan en minúsculas pero los tags
                # se ingieren con su capitalización original («Informatik»), y
                # el operador JSONB @> es exact-match case-sensitive: el filtro
                # aprobado no excluía NUNCA. Comparación con lower() sobre los
                # elementos del array (EXISTS correlacionado), case-insensitive
                # en ambos lados.
                elem = func.jsonb_array_elements_text(Job.tags).table_valued("value")
                tag_hit = (
                    select(literal(1))
                    .select_from(elem)
                    .where(func.lower(elem.c.value) == f["pattern"].lower())
                    .exists()
                )
                conditions.append(or_(Job.tags.is_(None), ~tag_hit))

        stmt = (
            select(Job)
            .where(*conditions)
            .order_by(Job.embedding.cosine_distance(profile_embedding))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _category_multiplier_for(job: Job, profile: UserProfile) -> float:
        """Devuelve el multiplicador de categoría aplicable a este job-usuario.

        Bypass watchlist: si el job viene de la lista cerrada de colegios
        suizos (source = swiss_schools_*) y el usuario ha activado la
        watchlist (profile.watchlist_schools_enabled), el multiplicador es
        1.0 — la penalización H (docencia) no aplica para esa lista.
        """
        source = job.source or ""
        if source.startswith("swiss_schools_") and getattr(
            profile, "watchlist_schools_enabled", False
        ):
            return 1.0
        category = job.category or "otros"
        return CATEGORY_MULTIPLIERS.get(category, 0.55)

    def _stage2_multifactor_score(
        self,
        profile: UserProfile,
        candidates: list[Job],
        weights: dict,
    ) -> list[dict]:
        """Score each candidate with multi-factor weights. Returns sorted list."""
        import numpy as np

        from services.urgency_scorer import compute_urgency_score

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

            # Penalización por categoría (A–G = ×1.0). Bypass per-user para
            # watchlist de colegios suizos si el usuario lo tiene activo.
            final = round(final * self._category_multiplier_for(job, profile), 2)

            # Urgency boost (solo aplica si el job es de la watchlist).
            # G1/P3-19: los deadlines suelen ir al FINAL del anuncio — con
            # solo el snippet (200 chars) el boost se perdía casi siempre.
            urgency = compute_urgency_score(
                job, description=job.description or job.description_snippet or ""
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
                    "urgency_score": urgency,
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
        # Quita "missing" que el candidato ya cubre por sinónimo (copywriting↔content writer…).
        missing = filter_missing_skills(user_skills, missing)
        return matching, missing

    async def _stage3_llm_rerank(
        self,
        profile: UserProfile,
        scored_results: list[dict],
        weights: dict,
    ) -> list[dict]:
        """Stage 3: LLM re-ranking via Groq. Updates score_llm + explanation."""
        candidates_for_llm = [self._llm_candidate(r["job"]) for r in scored_results]

        llm_results = await self.groq.rerank_jobs(
            profile_text=profile.cv_text or "",
            profile_skills=profile.skills or [],
            candidates=candidates_for_llm,
            fallback=self.gemini,
        )

        llm_by_index = {r["global_index"]: r for r in llm_results}
        for i, r in enumerate(scored_results):
            llm_data = llm_by_index.get(i)
            if llm_data and llm_data.get("score", 0) > 0:
                self._apply_llm_result(r, llm_data, profile, weights)

        # Re-sort after LLM scoring
        scored_results.sort(key=lambda x: x["score_final"], reverse=True)
        return scored_results

    @staticmethod
    def _llm_candidate(job) -> dict:
        """Proyecta un Job a los campos que ve el LLM en el prompt de re-ranking."""
        return {
            "title": job.title or "",
            "company": job.company or "",
            "description": job.description or "",
            "tags": job.tags or [],
            "location": job.location or "",
            "remote": job.remote or False,
            "language": job.language or "",
            "contract_type": job.contract_type.value if job.contract_type else "",
        }

    def _apply_llm_result(
        self, r: dict, llm_data: dict, profile: UserProfile, weights: dict
    ) -> None:
        """Mergea el resultado del LLM en un scored result y recalcula score_final."""
        # G3/P3-9: marca de «esta corrida SÍ tuvo veredicto del LLM para este
        # result» — la lee `_score_values` para decidir si persiste score_llm
        # y explanation. Solo se llega aquí con score > 0, así que el degradado
        # a ceros (LLM caído) queda fuera y no borra la explicación previa.
        r[LLM_VERDICT_KEY] = True
        r["score_llm"] = round(llm_data["score"] / 100.0, 4)
        r["explanation"] = llm_data.get("reason", "")
        # Merge LLM skill analysis if richer than rule-based
        if llm_data.get("matching_skills"):
            r["matching_skills"] = llm_data["matching_skills"]
        if llm_data.get("missing_skills"):
            r["missing_skills"] = filter_missing_skills(
                profile.skills or [], llm_data["missing_skills"]
            )
        # Recalculate final score with real LLM score + category multiplier
        base = self.matcher.compute_final_score(
            embedding_score=r["score_embedding"],
            salary_score=r["score_salary"],
            location_score=r["score_location"],
            recency_score=r["score_recency"],
            llm_score=r["score_llm"],
            language_score=r.get("score_language", 0.5),
            weights=weights,
        )
        r["score_final"] = round(
            base * self._category_multiplier_for(r["job"], profile), 2
        )

    @staticmethod
    def _score_values(r: dict) -> dict:
        """Columnas de scores/campos derivados de un result (forma dict).

        Fuente unica para el UPDATE en sesion y para el upsert ON CONFLICT:
        NUNCA incluye feedback/feedback_implicit/application_status/
        draft_letter — el estado del usuario no se pisa desde el motor.

        G3/P3-9: `score_llm`/`explanation` solo viajan si ESTA corrida obtuvo
        veredicto del LLM para el result (flag LLM_VERDICT_KEY, puesta en
        `_apply_llm_result`). Sin la guarda, la cola que no entra en el
        re-ranking —modo avalancha: >MATCH_LLM_RERANK_MAX ⇒ solo se re-rankean
        MATCH_LLM_RERANK_TOP— escribía `score_llm=0.0` y `explanation=NULL`
        encima de los valores buenos del día anterior, y la explicación «por
        qué encaja» aparecía y desaparecía según el volumen del día. El mismo
        criterio protege el degradado a ceros (Groq+Gemini caídos): un fallo
        de proveedor no borra lo ya explicado.
        """
        values = {
            "score_embedding": r["score_embedding"],
            "score_salary": r["score_salary"],
            "score_location": r["score_location"],
            "score_recency": r["score_recency"],
            "score_final": r["score_final"],
            "urgency_score": r.get("urgency_score", 0),
            "matching_skills": r["matching_skills"],
            "missing_skills": r["missing_skills"],
        }
        if r.get(LLM_VERDICT_KEY):
            values["score_llm"] = r["score_llm"]
            values["explanation"] = r.get("explanation")
        return values

    @classmethod
    def _apply_scores(cls, row: MatchResult, r: dict) -> None:
        """Copia scores/campos derivados de un result a la fila (update).

        NO toca feedback/application_status/draft_letter → los conserva en un UPDATE.
        """
        for key, value in cls._score_values(r).items():
            setattr(row, key, value)

    @staticmethod
    def _has_engagement(row: MatchResult) -> bool:
        """True si la fila tiene interacción del usuario → no se borra en el prune.

        G3/P2-11: `feedback_implicit` faltaba, así que una fila cuya única
        interacción era implícita (creada por `record_implicit_feedback`:
        feedback=None, application_status='detected', draft_letter=None) se
        consideraba «limpia» y se BORRABA en cada corrida — de rebote la oferta
        dejaba de estar `attached` y `cleanup_stale_jobs` la borraba con su
        cascada a los 60 días. Mismo criterio que `attached` en
        `maintenance_tasks`, pero por verdad (no `is not None`): una lista
        vacía no es interacción.
        """
        return (
            row.feedback is not None
            or bool(row.feedback_implicit)
            or row.application_status != "detected"
            or row.draft_letter is not None
        )

    async def _save_results(
        self,
        user_id: uuid.UUID,
        results: list[dict],
    ) -> None:
        """Upsert match results para el usuario.

        Estrategia: NO delete-all + insert. Razones:
        - feedback "dismissed"/"thumbs_down" debe preservarse para que
          _get_excluded_hashes siga excluyendo esos jobs en runs futuros.
        - application_status / draft_letter se debe preservar incluso si
          el job ya no supera el threshold y por tanto no aparece en
          `results` (caso típico: el usuario movió a "sent" un job cuyo
          score cae por re-ranking distinto del LLM).

        Lógica:
        1. Cargar todos los MatchResult existentes del usuario.
        2. Para cada result nuevo:
           - Si existe row → actualizar scores y campos derivados,
             conservar feedback, application_status, draft_letter.
           - Si no existe → insertar.
        3. Para cada row existente NO presente en results:
           - Si tiene feedback, application_status != "detected", o
             draft_letter → conservar (frozen, sin actualizar scores).
           - Si está "limpio" (sin engagement del usuario) → borrar.

        Los INSERT son upserts ON CONFLICT (uq_match_user_job): una fila de
        feedback commiteada FUERA DE BANDA (peticion de la API) en la ventana
        entre el snapshot y el commit ya no aborta la corrida con
        IntegrityError — se actualizan SOLO los scores y el feedback/estado
        del usuario de la fila ganadora se preserva.
        """
        existing_by_hash = await self._load_existing(user_id)

        new_hashes: set[str] = set()
        for r in results:
            job_hash = r["job"].hash
            new_hashes.add(job_hash)
            existing = existing_by_hash.get(job_hash)
            if existing is not None:
                # UPDATE: refrescar scores. feedback/application_status/draft_letter
                # NO se tocan — son del usuario (los conserva _apply_scores al no tocarlos).
                self._apply_scores(existing, r)
            else:
                # INSERT idempotente: si la fila aparecio tras el snapshot
                # (upsert minimo de feedback de la API), solo pisa scores.
                stmt = (
                    pg_insert(MatchResult)
                    .values(user_id=user_id, job_hash=job_hash, **self._score_values(r))
                    .on_conflict_do_update(
                        index_elements=["user_id", "job_hash"],
                        set_=self._score_values(r),
                    )
                )
                await self.db.execute(stmt)

        # Prune: borrar solo huérfanas SIN engagement del usuario.
        to_delete = [
            h
            for h, row in existing_by_hash.items()
            if h not in new_hashes and not self._has_engagement(row)
        ]

        if to_delete:
            await self.db.execute(
                delete(MatchResult).where(
                    MatchResult.user_id == user_id,
                    MatchResult.job_hash.in_(to_delete),
                )
            )

        await self.db.commit()

    async def _load_existing(self, user_id: uuid.UUID) -> dict[str, MatchResult]:
        """Snapshot de los MatchResult del usuario, indexado por job_hash."""
        existing_stmt = select(MatchResult).where(MatchResult.user_id == user_id)
        existing_rows = (await self.db.execute(existing_stmt)).scalars().all()
        return {row.job_hash: row for row in existing_rows}

    @staticmethod
    def _priority_watchlist(results: list[dict]) -> list[dict]:
        """Filtra los resultados de watchlist de colegios que superan el umbral
        de push (score_final + urgency_score >= WATCHLIST_PUSH_THRESHOLD)."""
        return [
            r
            for r in results
            if (r["job"].source or "").startswith("swiss_schools_")
            and (r["score_final"] + r.get("urgency_score", 0))
            >= settings.WATCHLIST_PUSH_THRESHOLD
        ]

    async def _daily_push_cap_reached(self, user_id: uuid.UUID) -> bool:
        """True si el usuario ya alcanzó ALERTS_MAX_PUSH_PER_DAY en 24h."""
        from datetime import timedelta

        from models.notification import Notification

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        count_stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.created_at >= since,
            )
        )
        sent_today = (await self.db.execute(count_stmt)).scalar_one()
        return sent_today >= settings.ALERTS_MAX_PUSH_PER_DAY

    @staticmethod
    def _watchlist_notification(user_id: uuid.UUID, priority: list[dict]):
        """Construye la Notification de watchlist con los top-5 (no satura)."""
        from models.notification import Notification

        top = priority[:5]
        lines = [
            f"• {r['job'].company or '?'} — {r['job'].title[:55]} "
            f"(score {r['score_final']:.0f} + urg {r.get('urgency_score', 0):.0f})"
            for r in top
        ]
        suffix = f"\n... y {len(priority) - 5} más" if len(priority) > 5 else ""

        return Notification(
            user_id=user_id,
            event_type="watchlist_priority",
            title=f"Watchlist colegios — {len(priority)} oportunidad(es) prioritaria(s)",
            body="\n".join(lines) + suffix,
            data={
                "count": len(priority),
                "job_hashes": [r["job"].hash for r in priority],
            },
        )

    async def _broadcast_watchlist_sse(self, user_id: uuid.UUID, count: int) -> None:
        """Broadcast SSE para refresh inmediato del frontend.

        Importante: usar redis.asyncio (no `import redis` síncrono), estamos
        dentro de un método async — el cliente síncrono bloquea el event loop.
        """
        try:
            import json

            from redis import asyncio as aioredis

            from config import settings as cfg

            r = aioredis.from_url(cfg.REDIS_URL)
            try:
                await r.publish(
                    f"sse:{user_id}",
                    json.dumps(
                        {"event": "watchlist_priority", "data": {"count": count}}
                    ),
                )
            finally:
                await r.aclose()
        except Exception:
            logger.warning("SSE broadcast failed for watchlist priority")

    async def _notify_watchlist_priority(
        self, user_id: uuid.UUID, results: list[dict]
    ) -> None:
        """Crea notificación push inmediata para jobs watchlist con
        score_final + urgency_score >= WATCHLIST_PUSH_THRESHOLD. Solo dispara
        si el usuario tiene watchlist_schools_enabled=True.

        Respeta ALERTS_MAX_PUSH_PER_DAY como cap diario de notificaciones
        push (todos los event_type combinados) para evitar fatiga.
        """
        from models.user_profile import UserProfile

        prof_stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        profile = (await self.db.execute(prof_stmt)).scalar_one_or_none()
        if not profile or not profile.watchlist_schools_enabled:
            return

        priority = self._priority_watchlist(results)
        if not priority:
            return

        if await self._daily_push_cap_reached(user_id):
            logger.info(
                "Watchlist priority skipped for user %s: daily cap reached (%d)",
                user_id,
                settings.ALERTS_MAX_PUSH_PER_DAY,
            )
            return

        self.db.add(self._watchlist_notification(user_id, priority))
        await self.db.commit()

        await self._broadcast_watchlist_sse(user_id, len(priority))
