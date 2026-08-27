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
from sqlalchemy.orm import defer

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
# G8/P2-4: «esta corrida SÍ tenía LLM y decidió NO mandar esta fila» (la cola
# de la avalancha). Es distinto de «no hay veredicto»: un lote DEGRADADO (LLM
# caído) tampoco deja veredicto, y ahí lo correcto es no tocar nada. Sin
# distinguir los dos casos, una caída del LLM borraría todas las explicaciones.
LLM_SKIPPED_KEY = "llm_skipped"


def _unir_skills(base: list[str], extra: list[str]) -> list[str]:
    """Une dos listas de skills sin duplicar, sin distinguir mayúsculas.

    G7/P2-3: el enriquecimiento del LLM se SUMA a lo que la regla deduce del
    perfil, en vez de reemplazarlo. Reemplazar obligaba a no escribir nunca una
    lista vacía para no perderlo, y eso congelaba el `[]`.
    """
    # G8/P3-4: total en el ARGUMENTO, no solo en sus elementos. La docstring
    # prometía totalidad pero `(*base, *extra)` revienta con `TypeError` si
    # `extra` no es iterable (`extra=7`) y desgrana en LETRAS si es un `str`
    # (`"Python"` -> ['P','y','t',...]). El `TypeError` reproduce exactamente el
    # daño de G7/P3-3: sube hasta el `except` POR USUARIO de
    # `tasks/matching_tasks.py` y se pierde el matching entero del perfil.
    base = base if isinstance(base, list) else []
    extra = extra if isinstance(extra, list) else []
    vistas: dict[str, int] = {}
    union: list[str] = []
    for skill in (*base, *extra):
        # G7/P3-4: lo que viene del LLM ya se sanea en `_parse_llm_response`;
        # aquí se ignora lo que no sea texto para que este helper sea total y
        # no dependa de que su llamante lo haya hecho.
        if not isinstance(skill, str) or not skill.strip():
            continue
        clave = skill.lower()
        if clave not in vistas:
            vistas[clave] = len(union)
            union.append(skill)
        elif union[vistas[clave]].islower() and not skill.islower():
            # G8/P3-5 (menor): `base` sale de `_compute_skill_overlap`, que
            # lowercasea, así que el dedup case-insensitive conservaba la
            # variante de REGLA y la tarjeta pasaba de «Python / English / Excel»
            # a «python / english / Excel». Gana la variante con mayúsculas.
            union[vistas[clave]] = skill
    return union


def _respaldadas_por_el_perfil(perfil: list[str], candidatas: object) -> list[str]:
    """Las skills que el LLM llama «matching» y el perfil SÍ respalda.

    `filter_missing_skills` devuelve las que el candidato NO tiene (directa ni
    por sinónimo); el complemento son las respaldadas. Reutilizarla mantiene una
    sola definición de «el perfil cubre esta skill» (G8/P3-5).
    """
    if not isinstance(candidatas, list):
        return []
    # `filter_missing_skills` hace `.strip()` sobre cada elemento: un `None` o
    # un `{...}` colado por el borde reventaría con AttributeError dentro del
    # `except` POR USUARIO de `tasks/matching_tasks.py`. Se filtra aquí, que es
    # donde entra lo ajeno (G7/P3-4 vive en `_unir_skills`, no cubre este uso).
    textos = [s for s in candidatas if isinstance(s, str) and s.strip()]
    respaldo = [s for s in perfil if isinstance(s, str)]
    sin_respaldo = set(filter_missing_skills(respaldo, textos))
    return [s for s in textos if s not in sin_respaldo]


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
        # G8/P2-4: la cola no pasa por el LLM en esta corrida, y sus listas de
        # skills SÍ se escriben (de regla, puras). Marcarla es lo que permite a
        # `_score_values` borrar también `score_llm`/`explanation` y mantener el
        # bloque LLM ATÓMICO: sin la marca, la fila queda con la prosa del LLM
        # de otro día y CERO badges que la respalden.
        for r in tail:
            r[LLM_SKIPPED_KEY] = True

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
    ) -> list[tuple[Job, float]]:
        """Fetch ALL active jobs with embeddings, ordered by cosine similarity.

        Devuelve cada oferta CON SU DISTANCIA coseno al perfil — la misma que
        Postgres acaba de calcular para ordenar. Antes se devolvía solo la
        oferta, con su `Vector(384)` dentro, y la etapa 2 recalculaba ese mismo
        coseno en Python sobre un vector que había viajado entero desde la base.

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

        # El `Vector(384)` NO viaja de vuelta: nadie lo vuelve a mirar y son
        # 15 MB del payload. `raiseload` lo deja por escrito — si alguien
        # accediera a `job.embedding` lanzaría, en vez de emitir en silencio
        # una consulta por fila. Medido contra el corpus real, los dos perfiles
        # de producción (9.769 filas cada uno): 987,9 -> 280,4 ms y
        # 920,7 -> 264,8 ms.
        distance = Job.embedding.cosine_distance(profile_embedding)
        stmt = (
            select(Job, distance.label("distance"))
            .options(defer(Job.embedding, raiseload=True))
            .where(*conditions)
            .order_by(distance)
        )
        result = await self.db.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

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
        candidates: list[tuple[Job, float]],
        weights: dict,
    ) -> list[dict]:
        """Score each candidate with multi-factor weights. Returns sorted list.

        `candidates` llega de la etapa 1 como (oferta, distancia coseno). El
        `<=>` de pgvector ES la distancia coseno y `compute_embedding_score`
        era `max(0, similitud)`, o sea `max(0, 1 - distancia)`: reaprovechar la
        que ya calculó Postgres no cambia la métrica. Comprobado FILA A FILA
        contra el corpus real, los dos perfiles de producción, 9.769 ofertas
        cada uno: `score_final` idéntico en las 19.538 filas (delta exacto de
        la suma: 0,0). `score_embedding` cambia en 3 filas de 19.538, y en
        1e-4 — el último decimal de `round(x, 4)`.
        """
        from services.urgency_scorer import compute_urgency_score

        now = datetime.now(timezone.utc)

        results = []
        for job, distance in candidates:
            emb_score = max(0.0, 1.0 - distance)
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
            # G4/P2-6: la condición es «HUBO veredicto del LLM para este
            # índice», no «el veredicto fue positivo». Un score de 0 es salida
            # DOCUMENTADA del prompt («0-39 = poor fit»), y descartarlo dejaba
            # la fila con el `score_llm` y la explicación de una corrida
            # anterior —texto que el usuario ve en MatchCard— junto a un
            # `score_final` recalculado con llm=0: incoherencia sobre la clave
            # de orden y sobre la presentación.
            #
            # G5/P3-8 — RECTIFICACIÓN de lo que este comentario afirmaba: NO
            # cerraba además ninguna «pérdida de datos por poda». Para un poor
            # fit, `_apply_llm_result` recalcula `score_final` con las MISMAS
            # entradas que la etapa 2 (`:315` ya usa `llm_score=0.0`), así que
            # el valor es EXACTAMENTE el de la etapa 2 — verificado: 49.5 y
            # 49.5, idénticos. El umbral ve lo mismo con y sin el fix. Lo que
            # sí cambia es el orden y lo que el usuario lee, que es bastante.
            # Y la poda no es la vía de pérdida que se le atribuía:
            # `_save_results` solo borra huérfanas SIN engagement
            # (`_has_engagement`), y una fila con feedback, estado o borrador
            # se conserva congelada aunque caiga del umbral. Medido en
            # producción (SOLO LECTURA): de 1.331 filas de `match_results`, 71
            # tienen `score_llm > 0` y **0** caerían bajo
            # `MATCH_SCORE_THRESHOLD` (42.0) al retirar el aporte del LLM.
            # Los lotes DEGRADADOS (LLM caído) quedan fuera: sus ceros no son
            # un veredicto y borrarían la explicación buena.
            if llm_data and not llm_data.get("degraded"):
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
        # y explanation. G4/P2-6: se llega aquí con CUALQUIER veredicto real,
        # incluido un 0 («poor fit»); el degradado a ceros (LLM caído) lo
        # filtra `_stage3_llm_rerank` por su marca `degraded`, así que sigue
        # sin borrar la explicación previa.
        r[LLM_VERDICT_KEY] = True
        r["score_llm"] = round(llm_data.get("score", 0) / 100.0, 4)
        # G5/P3-7: `reason` vacía NO se escribe. Al aplicar ahora los veredictos
        # de score 0 (G4/P2-6), el caso «el LLM devolvió índice y score y OMITIÓ
        # reason» pasaba a guardar `explanation = ""`, que es lo que el usuario
        # ve —en blanco— en `MatchCard`. Y como ese veredicto SÍ es real, se
        # cachea (`GROQ_CACHE_TTL_DAYS=7`) y se re-aplica en cada corrida
        # durante la semana, incluso con el LLM caído. Conservar el texto de
        # otra corrida junto a un score bajo es peor que nada solo en teoría;
        # en la práctica el vacío borra información y no añade ninguna.
        # G6/P3-1: la guarda era de TRUTHINESS y `"   "`, `"\n\t"` o un
        # espacio duro (U+00A0) son truthy en Python: se escribían en la
        # columna y en `MatchCard.jsx` —donde `""` es falsy y OCULTA el
        # bloque— pintaban el recuadro de info con el icono y NINGÚN texto,
        # peor que el `""` que este fix venía a quitar. Y se cacheaban 7 días
        # igual. Se exige contenido real, no solo verdad booleana.
        # G7/P3-3: `reason` llegaba SIN comprobación de tipo desde
        # `_parse_llm_response`, y un `.strip()` sobre una lista/dict/int subía
        # como AttributeError hasta el `except` POR USUARIO de
        # `tasks/matching_tasks.py`: se perdía el matching entero de ese perfil,
        # no una oferta. Se sanea en el borde (`groq_service`) y se comprueba
        # también aquí, que es donde revienta.
        reason = llm_data.get("reason")
        if isinstance(reason, str) and reason.strip():
            r["explanation"] = reason
        # G7/P2-3: el análisis del LLM se SUMA al de regla, no lo sustituye.
        # Sustituirlo era lo que obligaba a no escribir nunca `[]` (ver
        # `_score_values`) y lo que congelaba la tarjeta.
        if llm_data.get("matching_skills"):
            # G8/P3-5: `missing_skills` pasa por `filter_missing_skills`;
            # `matching_skills` no pasaba por NADA. Con perfil `['python']` y un
            # LLM que devuelve `['Kubernetes','Rust','Fluent German']` se
            # persistía las tres y la tarjeta afirmaba EN VERDE que el usuario
            # habla alemán con fluidez. Se exige respaldo del perfil —directo o
            # por sinónimo, la misma maquinaria que ya decide qué «falta de
            # verdad»— antes de firmar una skill como suya.
            r["matching_skills"] = _unir_skills(
                r["matching_skills"],
                _respaldadas_por_el_perfil(
                    profile.skills or [], llm_data["matching_skills"]
                ),
            )
        if llm_data.get("missing_skills"):
            r["missing_skills"] = filter_missing_skills(
                profile.skills or [],
                _unir_skills(r["missing_skills"], llm_data["missing_skills"]),
            )
        # G8/P2-3: las dos listas se construyen por caminos que NO se hablan.
        # La «missing» de regla es `tags - perfil`; el prompt le enseña al LLM
        # ESOS MISMOS tags pidiéndole que puntúe generosamente, así que que el
        # LLM llame *matching* a un tag que la regla acaba de marcar *missing*
        # es el caso frecuente, no el borde: 18 de las 71 filas con veredicto
        # LLM en producción. `filter_missing_skills` contrasta contra
        # `profile.skills`, nunca contra `matching_skills`, así que no lo cazaba.
        # La tarjeta pintaba la MISMA skill en verde «✓» y tachada «te falta»,
        # como hermanas del mismo `<div>` y con `key` de React duplicada
        # (`MatchCard.jsx`). Gana la lista positiva: el LLM ha visto el CV.
        vistas = {s.lower() for s in r["matching_skills"]}
        r["missing_skills"] = [
            s for s in r["missing_skills"] if s.lower() not in vistas
        ]
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

        G6/P3-5 aplicó a `matching_skills`/`missing_skills` esa MISMA guarda, y
        G7/P2-3 la retiró: la premisa «una lista vacía no aporta nada que
        merezca borrar lo que ya había» vale para `explanation` —que solo
        produce el LLM y solo a veces— y es FALSA para estas dos.
        `_stage2_multifactor_score` las recalcula SIEMPRE y
        `_compute_skill_overlap` las deriva del perfil ACTUAL del usuario: aquí
        una lista vacía no es «hoy no hubo dato», es «el dato correcto de hoy es
        ninguno». Con la guarda, `[]` no podía escribirse nunca y el efecto era
        irreversible: el usuario adquiere la última skill que le faltaba,
        `missing_skills` pasa a `[]`, no se escribe, y la tarjeta sigue diciendo
        «te falta k8s» para siempre — justo en la oferta de encaje perfecto, y
        sin ninguna corrida futura capaz de pisarlo.

        El hueco que G6/P3-5 sí cerraba —la avalancha borrando lo que el LLM
        enriqueció— se cubre ahora donde corresponde: `_apply_llm_result` UNE
        las skills del LLM con las de regla en vez de sustituirlas. Coste
        aceptado y recuperable: una oferta que cae fuera del top pierde ese día
        las skills que solo el LLM sabía, y las recupera la próxima vez que
        entre en el re-ranking.
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
            # G5/P3-7 + G6/P3-1: sin explicación con CONTENIDO en ESTA corrida
            # no se toca la columna — escribir `None` (o espacios) aquí borraba
            # lo mismo que el `""` de arriba.
            if (r.get("explanation") or "").strip():
                values["explanation"] = r["explanation"]
        elif r.get(LLM_SKIPPED_KEY):
            # G8/P2-4: el bloque del LLM es ATÓMICO. Si esta corrida reescribe
            # las skills con las de regla puras porque la fila se quedó en la
            # cola, la prosa y el score del LLM de otro día NO pueden
            # sobrevivir: la fila quedaría mitad fresca y mitad rancia, con un
            # recuadro azul citando skills que ningún badge respalda y un
            # `score_llm` que ya no está dentro de `score_final` (la etapa 2 lo
            # calcula con `llm_score=0.0`). Medido en producción: 53 de 71 filas
            # quedarían con `matching_skills = []` y las 71 explicaciones
            # intactas; el total de skills caía de 174 a 18.
            # Un lote DEGRADADO no entra aquí (no lleva la marca): sus ceros no
            # son un veredicto y borrarían una explicación buena.
            values["score_llm"] = 0.0
            values["explanation"] = None
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
