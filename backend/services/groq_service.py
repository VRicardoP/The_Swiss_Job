"""GroqService — LLM re-ranking and chat via Groq API.

Uses settings.GROQ_RERANK_MODEL (qwen3.6-27b, fast) for re-ranking (Stage 3)
and translation; settings.GROQ_MODEL (gpt-oss-120b) for heavier document tasks.
Groq SDK is synchronous — calls are wrapped in run_in_threadpool.
"""

import asyncio
import hashlib
import json
import logging
from typing import Any

from fastapi.concurrency import run_in_threadpool

from config import settings

logger = logging.getLogger(__name__)


def _solo_textos(valor: object) -> list[str]:
    """Deja solo las cadenas con contenido de una lista que viene del LLM."""
    if not isinstance(valor, list):
        return []
    return [s.strip() for s in valor if isinstance(s, str) and s.strip()]


# G8/P3-4: versión del esquema de lo que se guarda en la caché de re-ranking.
# Subirla invalida de golpe todo lo escrito por versiones anteriores, que de
# otro modo se seguiría sirviendo durante `GROQ_CACHE_TTL_DAYS`.
# G9 — v3: la entrada dejó de ser un LOTE entero bajo el hash de su prompt y
# pasó a ser UN veredicto por oferta (ver `GroqService._verdict_key`).
_CACHE_SCHEMA_VERSION = 3


def _sanear_veredicto(valor: object) -> dict | None:
    """Normaliza UN veredicto a la forma que `match_service` da por buena.

    G8/P3-4: es la MISMA función en los dos sentidos de la caché — lo que se
    escribe y lo que se lee pasan por aquí, así que lo guardado tiene por
    construcción la forma que espera quien lo lee. La caché es el segundo
    camino por el que la respuesta del LLM llega a `match_service`, y saltarse
    el saneo reabría G7/P3-3 (un `reason` no-`str` → AttributeError → `except`
    POR USUARIO → se pierde el matching entero del perfil).

    No revalida el índice: desde v3 la caché es POR OFERTA y el índice es
    posicional dentro del lote, así que ni se guarda ni se lee de aquí.
    """
    if not isinstance(valor, dict):
        return None
    veredicto = {
        "score": valor["score"] if isinstance(valor.get("score"), (int, float)) else 0,
        "matching_skills": _solo_textos(valor.get("matching_skills")),
        "missing_skills": _solo_textos(valor.get("missing_skills")),
        "reason": valor["reason"] if isinstance(valor.get("reason"), str) else "",
    }
    if valor.get("degraded"):
        veredicto["degraded"] = True
    return veredicto


RERANK_SYSTEM_PROMPT = """You are an expert recruiter AI evaluating job-candidate fit for a non-technical profile focused on content, language, and people operations.

TARGET DOMAINS (score generously — these are the candidate's goal):
- Content editing, localization, LQA, translation, proofreading
- AI evaluation, RLHF, data annotation, search quality rating
- Administrative support, virtual/executive assistant, operations coordination
- HR coordination, L&D, instructional design, training, talent acquisition
- Customer success, client relations, bilingual support
- International organisations, NGOs, UN agencies, EU policy
- Content writing, communications, marketing, technical writing

NON-TARGET DOMAINS (score strictly — poor fit unless explicitly matching):
- Software engineering, programming, DevOps, IT infrastructure
- Finance, accounting, controlling, auditing
- Sales, business development, commercial roles
- Teaching, classroom instruction, school coordination
- Social work, community services
- Senior management, C-level, general directorship

Scoring rules:
- 80-100 = excellent fit (candidate meets most/all requirements in target domains)
- 60-79 = good fit (core requirements met, minor gaps)
- 40-59 = partial fit (some relevant skills, significant gaps)
- 0-39 = poor fit (wrong domain or few matching skills)

Evaluation criteria:
1. Domain alignment — target domains get full score range; non-target domains cap at 35
2. Skills and competencies match (languages, tools, certifications, domain expertise)
3. Seniority and experience level alignment
4. Language requirements (native English, bilingual EN/ES, multilingual advantage)
5. Location and remote compatibility — remote jobs get +5 bonus
6. For content/editorial roles: weight linguistic precision, editorial tools, LQA/MTPE/CELTA experience
7. For HR/L&D roles: weight HRIS tools, instructional design, onboarding experience
8. For AI annotation roles: weight native language proficiency, analytical skills, academic background
9. For admin/VA roles: weight organisation, calendar management, bilingual communication, software tools
10. For international organisations: weight multilingualism, international experience, UN/NGO background

IMPORTANT: Respond ONLY with a valid JSON array. No markdown fences, no extra text."""

# Huella del prompt de sistema. Entra en la clave de caché para que reescribir
# las reglas de puntuación invalide los veredictos viejos, en vez de seguir
# sirviéndolos durante `GROQ_CACHE_TTL_DAYS`.
_SYSTEM_PROMPT_FINGERPRINT = hashlib.md5(RERANK_SYSTEM_PROMPT.encode()).hexdigest()


class GroqService:
    """Async wrapper around the synchronous Groq Python SDK."""

    def __init__(self, redis_client=None):
        self.client = None
        self.redis = redis_client
        if settings.GROQ_API_KEY:
            from groq import Groq

            self.client = Groq(api_key=settings.GROQ_API_KEY)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    async def get_chat_response(
        self,
        user_message: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion request to Groq via threadpool."""
        if not self.is_available:
            raise RuntimeError("Groq client not configured (GROQ_API_KEY missing)")

        selected_model = model or settings.GROQ_RERANK_MODEL
        effective_temp = temperature if temperature is not None else 0.2
        effective_max_tokens = max_tokens or 2048

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        # qwen3.6 razona por defecto y agotaría max_tokens en <think> antes de
        # emitir contenido (rompe los parsers JSON de traducción y rerank).
        # Suprimimos el razonamiento SOLO para el modelo rápido; el modelo pesado
        # (gpt-oss-120b) ya emite su razonamiento en un campo aparte.
        extra_params: dict[str, Any] = {}
        if (
            selected_model == settings.GROQ_RERANK_MODEL
            and settings.GROQ_RERANK_REASONING_EFFORT
        ):
            extra_params["reasoning_effort"] = settings.GROQ_RERANK_REASONING_EFFORT

        def _sync_call() -> str:
            completion = self.client.chat.completions.create(
                messages=messages,
                model=selected_model,
                temperature=effective_temp,
                max_tokens=effective_max_tokens,
                **extra_params,
            )
            return completion.choices[0].message.content

        return await run_in_threadpool(_sync_call)

    async def rerank_jobs(
        self,
        profile_text: str,
        profile_skills: list[str],
        candidates: list[dict],
        fallback: "object | None" = None,
    ) -> list[dict]:
        """Re-rank job candidates using LLM evaluation.

        La caché es POR OFERTA, no por lote: solo se mandan al LLM las ofertas
        cuyo veredicto no está ya cacheado, y los lotes se forman con esos
        fallos de caché. Ver `_verdict_key` para por qué la caché anterior
        —hash del prompt del LOTE entero— no acertaba nunca.

        Args:
            profile_text: User CV text or profile summary.
            profile_skills: User skills list.
            candidates: List of dicts with job info (from Stage 2).
            fallback: Segundo proveedor LLM (p.ej. GeminiService) con
                get_chat_response compatible. Se usa si Groq falla o no está
                configurado — resiliencia ante caducidad de GROQ_API_KEY.

        Returns:
            List of dicts with LLM scores and explanations, keyed by index.
        """
        fallback_ok = fallback is not None and getattr(fallback, "is_available", False)
        if not self.is_available and not fallback_ok:
            return []

        skills_text = ", ".join(profile_skills) if profile_skills else "Not specified"
        views = [self._job_view(c) for c in candidates]
        profile_key = self._profile_fingerprint(profile_text, skills_text)
        keys = [self._verdict_key(v, profile_key) for v in views]

        verdicts: list[dict | None] = await self._get_cached_verdicts(keys)
        pending = [i for i, v in enumerate(verdicts) if v is None]
        if pending:
            batch_size = settings.GROQ_RERANK_BATCH_SIZE
            batches = [
                pending[i : i + batch_size] for i in range(0, len(pending), batch_size)
            ]
            sem = asyncio.Semaphore(settings.GROQ_CONCURRENCY)

            async def _process_batch(positions: list[int]) -> None:
                prompt = self._rerank_prompt(
                    skills_text, profile_text, [views[p] for p in positions]
                )
                async with sem:
                    try:
                        results = await self._rerank_call(
                            prompt, fallback, len(positions)
                        )
                    except Exception:
                        logger.exception(
                            "Rerank failed for %d jobs (Groq+fallback)", len(positions)
                        )
                        # Degradado: NO se cachea (indistinguible de un «poor
                        # fit» legítimo si se sirviera después). G1/P2-12.
                        for r in self._fallback_results(len(positions)):
                            verdicts[positions[r["index"]]] = r
                        return
                for r in results:
                    position = positions[r["index"]]
                    verdicts[position] = r
                    await self._set_cached(keys[position], r)

            await asyncio.gather(*[_process_batch(b) for b in batches])

        return [
            {**verdict, "index": i, "global_index": i}
            for i, verdict in enumerate(verdicts)
            if verdict is not None
        ]

    async def _rerank_call(
        self, user_prompt: str, fallback: "object | None", batch_len: int
    ) -> list[dict]:
        """Pide el re-ranking a Groq y PARSEA su respuesta; si Groq falla O
        responde basura no parseable, cae al fallback (Gemini).

        G2/P3-4 (mitad residual de G1/P2-12): con el parseo FUERA de este
        método, la excepción de `_parse_llm_response` saltaba después de
        retornar y el llamante degradaba a ceros SIN probar Gemini — el
        fallback solo cubría «Groq lanza», no «Groq responde basura» (el caso
        real documentado: qwen truncado por max_tokens).

        Ambos servicios exponen `get_chat_response` con firma compatible
        (Gemini no recibe `model`). Lanza si ninguno da una respuesta
        parseable, para que el llamante degrade a `_fallback_results`.
        """
        if self.is_available:
            try:
                response = await self.get_chat_response(
                    user_message=user_prompt,
                    system_prompt=RERANK_SYSTEM_PROMPT,
                    model=settings.GROQ_RERANK_MODEL,
                    temperature=settings.GROQ_RERANK_TEMPERATURE,
                    max_tokens=settings.GROQ_RERANK_MAX_TOKENS,
                )
                return self._parse_llm_response(response, batch_len)
            except Exception:
                logger.warning(
                    "Groq rerank falló o respondió basura; intentando fallback (Gemini)"
                )

        if fallback is not None and getattr(fallback, "is_available", False):
            response = await fallback.get_chat_response(
                user_message=user_prompt,
                system_prompt=RERANK_SYSTEM_PROMPT,
                temperature=settings.GROQ_RERANK_TEMPERATURE,
                max_tokens=settings.GROQ_RERANK_MAX_TOKENS,
            )
            return self._parse_llm_response(response, batch_len)
        raise RuntimeError("Sin proveedor LLM disponible para el re-ranking")

    @staticmethod
    def _parse_llm_response(response: str, batch_len: int) -> list[dict]:
        """Parse LLM JSON response, stripping markdown fences if present.

        G1/P2-12: ante JSON inválido/no-lista LANZA en vez de devolver el
        fallback de ceros — devolverlo aquí hacía que esos ceros se CACHEARAN
        7 días (indistinguibles de un «poor fit» legítimo) y que el except
        del llamante (que degrada SIN cachear) nunca se ejecutara. El caso
        real documentado es qwen truncado por max_tokens.

        G1/P3-16: el índice del LLM se validaba a ciegas — un LLM 1-based
        desplazaba todos los scores un puesto (score/explicación al job
        vecino). Se exige 0 <= index < batch_len y unicidad; la violación
        lanza y degrada por el mismo camino seguro.

        G3/P3-1: se exige además COBERTURA COMPLETA del lote. Una respuesta
        válida pero corta (qwen truncado: 9 de 25) se aceptaba y se CACHEABA
        7 días con los índices ausentes sin score LLM y sin haber probado
        Gemini; ahora cae por el mismo camino seguro (fallback → degradado
        sin cachear) que el JSON inválido.
        """
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            results = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"respuesta del LLM no es JSON: {exc}") from exc

        if not isinstance(results, list):
            raise ValueError("respuesta del LLM no es una lista JSON")

        # Normalize each result
        normalized: list[dict[str, Any]] = []
        seen_indexes: set[int] = set()
        for r in results:
            if not isinstance(r, dict):
                raise ValueError("elemento no-objeto en la respuesta del LLM")
            index = r.get("index")
            if not isinstance(index, int) or not (0 <= index < batch_len):
                raise ValueError(
                    f"índice fuera de rango en la respuesta del LLM: {index!r} "
                    f"(batch de {batch_len})"
                )
            if index in seen_indexes:
                raise ValueError(f"índice duplicado en la respuesta del LLM: {index}")
            seen_indexes.add(index)
            # G7/P3-3 y G7/P3-4: `index` se validaba a fondo y `score` se
            # acotaba, pero `reason` y las dos listas de skills pasaban CRUDAS.
            # Un `reason` no-`str` reventaba con AttributeError en
            # `match_service._apply_llm_result`, cuyo `except` más cercano es
            # POR USUARIO: se perdía el matching entero de ese perfil. Y un
            # `matching_skills` con `None`/`{...}`/`""` dentro se persistía tal
            # cual y luego `schemas/match.py` declara `list[str]` estricto —
            # `ValidationError` = 500 en `/api/v1/match/results` ENTERO, no una
            # tarjeta rota.
            # G8/P3-4 — RECTIFICACIÓN: «este es el único borde por el que entra
            # la respuesta del LLM» era FALSO. La CACHÉ es un segundo borde:
            # `_set_cached` guarda el resultado YA parseado y en un hit
            # `batch_results = cached` iba directo al llamante SIN volver a
            # pasar por aquí, así que una entrada escrita por una versión
            # anterior del esquema (TTL de 7 días) reabría G7/P3-3 y G7/P3-4.
            # Ahora `_get_cached` re-sanea lo que lee, y la clave lleva versión
            # de esquema para que un cambio futuro invalide lo viejo.
            normalized.append(
                {
                    "index": index,
                    "score": max(0, min(100, r.get("score", 0))),
                    "matching_skills": _solo_textos(r.get("matching_skills")),
                    "missing_skills": _solo_textos(r.get("missing_skills")),
                    "reason": r["reason"] if isinstance(r.get("reason"), str) else "",
                }
            )

        # G3/P3-1: cobertura completa del lote — los índices válidos y únicos ya
        # están garantizados arriba, así que basta el recuento.
        if len(normalized) != batch_len:
            raise ValueError(
                f"respuesta del LLM incompleta: {len(normalized)} resultados "
                f"para un batch de {batch_len}"
            )

        return normalized

    @staticmethod
    def _fallback_results(count: int) -> list[dict]:
        """Generate empty fallback results when LLM fails.

        G4/P2-6: llevan `degraded=True`. Sin esa marca, un lote degradado
        (LLM caído) y un veredicto legítimo de score 0 —salida DOCUMENTADA del
        prompt: «0-39 = poor fit»— eran indistinguibles para el llamante, que
        tenía que descartar AMBOS con un `score > 0`. Consecuencia: un poor fit
        real no marcaba la corrida como evaluada y la fila conservaba para
        siempre el `score_llm` y la explicación de otro día.
        """
        return [
            {
                "index": i,
                "score": 0,
                "matching_skills": [],
                "missing_skills": [],
                "reason": "",
                "degraded": True,
            }
            for i in range(count)
        ]

    @staticmethod
    def _job_view(candidate: dict) -> dict:
        """Proyección de UNA oferta tal y como la ve el LLM (ya truncada).

        Es a la vez el material del prompt y el material de la clave de caché:
        que sean lo mismo es lo que garantiza que un veredicto cacheado
        corresponde exactamente a la oferta que se le enseñó al modelo.
        """
        return {
            "title": candidate.get("title", ""),
            "company": candidate.get("company", ""),
            "description": (candidate.get("description", "") or "")[:800],
            "tags": (candidate.get("tags") or [])[:15],
            "location": candidate.get("location", ""),
            "remote": candidate.get("remote", False),
            "language": candidate.get("language", ""),
            "contract_type": candidate.get("contract_type", ""),
        }

    @staticmethod
    def _rerank_prompt(skills_text: str, profile_text: str, views: list[dict]) -> str:
        """Prompt de un lote. Los índices son LOCALES al lote (0..n-1).

        G9: se retiró el encabezado «(batch i/N)». El modelo no lo usaba para
        nada —evalúa cada oferta por separado— y era lo que ataba el texto al
        tamaño del pool.
        """
        jobs_json = json.dumps(
            [{"index": i, **view} for i, view in enumerate(views)], ensure_ascii=False
        )
        return (
            f"## Candidate Profile\n"
            f"Skills: {skills_text}\n"
            f"{profile_text[:2000]}\n\n"
            f"## Jobs to Evaluate\n"
            f"{jobs_json}\n\n"
            "Evaluate each job. Return a JSON array where each element has:\n"
            '- "index": the job index from above\n'
            '- "score": 0-100\n'
            '- "matching_skills": list of matching skills\n'
            '- "missing_skills": list of missing skills\n'
            '- "reason": one-sentence explanation\n\n'
            "Respond ONLY with the JSON array."
        )

    @staticmethod
    def _profile_fingerprint(profile_text: str, skills_text: str) -> str:
        """Huella de lo que el prompt le enseña al LLM del CANDIDATO, ya truncado."""
        material = f"{skills_text}\n{profile_text[:2000]}"
        return hashlib.md5(material.encode()).hexdigest()

    @staticmethod
    def _verdict_key(job_view: dict, profile_fingerprint: str) -> str:
        """Clave de caché de UN veredicto: esta oferta para este candidato.

        Lleva TODO lo que determina el veredicto y NADA que no lo determine:
        la proyección exacta de la oferta que va al prompt, la huella del
        perfil, el modelo, el prompt de sistema y la versión del esquema de lo
        que se guarda.

        Y deliberadamente NO lleva el índice dentro del lote, ni el número de
        lotes, ni el orden del pool. La clave anterior hasheaba el prompt del
        LOTE ENTERO, así que los llevaba los tres — y el orden lo mueve
        `recency_score`, una función ESCALONADA del tiempo (saltos a los días
        2, 8, 15 y 31): el pool se reordenaba solo con que pasara el tiempo, y
        una oferta nueva en el top-50 desplazaba en cascada e invalidaba su
        lote y todos los posteriores. Resultado medido: CERO claves
        `groq:rerank:*` vivas en Redis. Cada corrida escribía claves que la
        siguiente ya no buscaba.

        El modelo que se firma es el primario (`GROQ_RERANK_MODEL`). Si el
        veredicto acabó viniendo del fallback (Gemini), sigue siendo un
        veredicto válido para ESA oferta y ESE perfil; lo que la clave impide
        es servirlo después de cambiar de modelo primario o de reescribir las
        reglas de puntuación.
        """
        material = "\n".join(
            (
                json.dumps(job_view, sort_keys=True, ensure_ascii=False),
                profile_fingerprint,
                settings.GROQ_RERANK_MODEL,
                _SYSTEM_PROMPT_FINGERPRINT,
            )
        )
        h = hashlib.md5(material.encode()).hexdigest()
        return f"groq:rerank:v{_CACHE_SCHEMA_VERSION}:{h}"

    async def _get_cached_verdicts(self, keys: list[str]) -> list[dict | None]:
        """Lee de golpe (MGET, un solo viaje) los veredictos ya cacheados.

        G8/P3-4: lo que sale de la caché se re-sanea con la MISMA función que
        el borde de red. La caché es el segundo camino por el que la respuesta
        del LLM llega a `match_service`, y saltarse el saneo reabría G7/P3-3
        (un `reason` no-`str` → AttributeError → `except` POR USUARIO → se
        pierde el matching entero del perfil).
        """
        if not self.redis or not keys:
            return [None] * len(keys)
        try:
            raw = await self.redis.mget(keys)
        except Exception:
            logger.debug("Redis cache read failed for %d rerank verdicts", len(keys))
            return [None] * len(keys)

        out: list[dict | None] = []
        for item in raw:
            if not item:
                out.append(None)
                continue
            try:
                out.append(_sanear_veredicto(json.loads(item)))
            except (TypeError, ValueError):
                out.append(None)
        return out

    async def _set_cached(self, key: str, verdict: dict) -> None:
        """Guarda UN veredicto con TTL. Se guarda ya saneado: lo que se escribe
        tiene exactamente la forma que espera quien lo lee."""
        if not self.redis:
            return
        payload = _sanear_veredicto(verdict)
        if payload is None:
            return
        try:
            ttl = settings.GROQ_CACHE_TTL_DAYS * 86400
            await self.redis.set(key, json.dumps(payload), ex=ttl)
        except Exception:
            logger.debug("Redis cache write failed for %s", key)
