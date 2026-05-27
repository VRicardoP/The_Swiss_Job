"""PatternAnalysisService — analiza jobs rechazados para detectar patrones de exclusión.

Metodología:
1. Carga todos los MatchResult con feedback negativo (dismissed / thumbs_down).
2. Extrae n-gramas de títulos (1-gram, 2-gram, 3-gram) filtrando stop words.
3. Calcula tasa de rechazo: veces_en_rechazados / veces_en_todos_los_matches.
4. Extrae los tags más frecuentes en rechazados con alta tasa de rechazo.
5. Genera PatternSuggestion pendientes de aprobación del usuario.
"""

import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.job_filter import PatternSuggestion
from models.match_result import MatchResult

logger = logging.getLogger(__name__)

# Feedback negativos que indican rechazo explícito del usuario
_NEGATIVE_FEEDBACK = frozenset({"dismissed", "thumbs_down"})

# Stop words multilingüe (DE/EN/FR/IT) + marcadores Swiss frecuentes
_STOP_WORDS = frozenset(
    {
        # Alemán
        "und", "der", "die", "das", "in", "mit", "für", "von", "zu", "bei",
        "als", "auf", "an", "im", "ist", "sie", "er", "wir", "ich", "ein",
        "eine", "einer", "einem", "einen", "eines",
        # Inglés
        "the", "a", "an", "in", "at", "for", "and", "or", "with", "of",
        "to", "be", "is", "are", "was", "were", "have", "has", "had",
        # Francés
        "de", "la", "le", "les", "du", "en", "et", "à", "par", "un", "une",
        "des", "au", "aux", "ce", "se",
        # Italiano
        "di", "il", "lo", "la", "in", "e", "con", "per", "un", "una",
        "del", "della", "dei",
        # Marcadores de género suizos
        "mf", "mw", "mfd", "mwd", "wm", "fm", "d", "w", "m",
        # Porcentajes de jornada (muy frecuentes en Suiza)
        "80", "100", "90", "50", "60", "70", "40", "120", "80-100",
        "100%", "80%", "90%", "60%", "50%", "80-100%",
        # Preposiciones y artículos cortos
        "of", "as", "on", "by", "be",
    }
)

# Mínimos para que un patrón sea sugerido
_MIN_REJECTED_OCCURRENCES = 2   # aparece en al menos N jobs rechazados
_MIN_REJECTION_RATE = 0.55      # al menos el 55% de sus apariciones son en rechazados
_MAX_SUGGESTIONS_PER_TYPE = 10  # máximo sugerencias por tipo en un análisis


def _tokenize(text: str) -> list[str]:
    """Tokeniza un título: lowercase, quita puntuación, filtra stop words."""
    text = text.lower()
    # Eliminar porcentajes (80-100%, etc.) y marcadores de género entre paréntesis
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\d+[-–]\d+\s*%?", "", text)
    text = re.sub(r"\d+\s*%", "", text)
    tokens = re.split(r"[\s/,\-\|\.]+", text)
    return [t for t in tokens if t and t not in _STOP_WORDS and len(t) > 1]


def _ngrams(tokens: list[str], n: int) -> list[str]:
    """Genera n-gramas de una lista de tokens."""
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _extract_ngrams(titles: list[str]) -> list[str]:
    """Extrae 1-gramas, 2-gramas y 3-gramas de una lista de títulos."""
    all_ngrams: list[str] = []
    for title in titles:
        tokens = _tokenize(title)
        all_ngrams.extend(_ngrams(tokens, 1))
        all_ngrams.extend(_ngrams(tokens, 2))
        all_ngrams.extend(_ngrams(tokens, 3))
    return all_ngrams


class PatternAnalysisService:
    """Analiza MatchResults rechazados y genera PatternSuggestion para el usuario."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def analyze_and_generate(
        self,
        user_id: uuid.UUID,
        min_rejected: int = _MIN_REJECTED_OCCURRENCES,
    ) -> int:
        """Analiza jobs rechazados y guarda PatternSuggestion pendientes.

        Elimina sugerencias pendientes anteriores antes de generar nuevas.
        Devuelve el número de sugerencias generadas.
        """
        # Borrar sugerencias pendientes anteriores (las aprobadas/rechazadas se conservan)
        await self._db.execute(
            delete(PatternSuggestion).where(
                PatternSuggestion.user_id == user_id,
                PatternSuggestion.status == "pending",
            )
        )

        rejected_jobs, all_jobs = await self._load_jobs(user_id)

        if len(rejected_jobs) < min_rejected:
            await self._db.commit()
            return 0

        title_suggestions = self._analyze_title_patterns(
            rejected_jobs, all_jobs, min_rejected
        )
        tag_suggestions = self._analyze_tag_patterns(
            rejected_jobs, all_jobs, min_rejected
        )

        all_suggestions = title_suggestions + tag_suggestions
        # Ordenar por confianza descendente y limitar total
        all_suggestions.sort(key=lambda s: s["confidence"], reverse=True)
        all_suggestions = all_suggestions[:20]

        now = datetime.now(timezone.utc)
        for s in all_suggestions:
            suggestion = PatternSuggestion(
                user_id=user_id,
                suggestion_type=s["type"],
                pattern=s["pattern"],
                description=s["description"],
                confidence=round(s["confidence"], 3),
                sample_jobs=s["samples"],
                affected_count=s["affected_count"],
                status="pending",
                created_at=now,
            )
            self._db.add(suggestion)

        await self._db.commit()
        return len(all_suggestions)

    async def get_rejected_count(self, user_id: uuid.UUID) -> int:
        """Devuelve el número de jobs con feedback negativo para el usuario."""
        stmt = (
            select(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.feedback.in_(_NEGATIVE_FEEDBACK),
            )
        )
        result = await self._db.execute(stmt)
        return len(result.scalars().all())

    # --- Métodos internos ---

    async def _load_jobs(
        self, user_id: uuid.UUID
    ) -> tuple[list[dict], list[dict]]:
        """Carga jobs rechazados y todos los jobs del usuario como dicts."""
        stmt_all = (
            select(MatchResult, Job)
            .join(Job, MatchResult.job_hash == Job.hash)
            .where(MatchResult.user_id == user_id)
        )
        rows = (await self._db.execute(stmt_all)).all()

        all_jobs: list[dict] = []
        rejected_jobs: list[dict] = []

        for match, job in rows:
            entry = {
                "title": (job.title or "").strip(),
                "company": (job.company or "").strip(),
                "tags": job.tags or [],
                "feedback": match.feedback,
            }
            all_jobs.append(entry)
            if match.feedback in _NEGATIVE_FEEDBACK:
                rejected_jobs.append(entry)

        return rejected_jobs, all_jobs

    def _analyze_title_patterns(
        self,
        rejected_jobs: list[dict],
        all_jobs: list[dict],
        min_rejected: int,
    ) -> list[dict]:
        """Detecta n-gramas de títulos con alta tasa de rechazo."""
        rejected_titles = [j["title"] for j in rejected_jobs if j["title"]]
        all_titles = [j["title"] for j in all_jobs if j["title"]]

        rejected_counts = Counter(_extract_ngrams(rejected_titles))
        all_counts = Counter(_extract_ngrams(all_titles))

        # Construir lookup: ngram → jobs rechazados que lo contienen
        ngram_to_samples: dict[str, list[dict]] = defaultdict(list)
        for job in rejected_jobs:
            if not job["title"]:
                continue
            tokens = _tokenize(job["title"])
            seen = set()
            for n in (1, 2, 3):
                for gram in _ngrams(tokens, n):
                    if gram not in seen:
                        seen.add(gram)
                        ngram_to_samples[gram].append(
                            {"title": job["title"], "company": job["company"]}
                        )

        suggestions: list[dict] = []
        seen_patterns: set[str] = set()

        for ngram, rejected_count in rejected_counts.most_common(100):
            if rejected_count < min_rejected:
                break
            if ngram in seen_patterns:
                continue

            total_count = all_counts.get(ngram, rejected_count)
            rejection_rate = rejected_count / total_count

            if rejection_rate < _MIN_REJECTION_RATE:
                continue

            # Evitar sugerir patrones que ya están cubiertos por uno más corto
            words = ngram.split()
            covered = any(
                p in seen_patterns and all(w in p.split() for w in words)
                for p in seen_patterns
            )
            if covered:
                continue

            seen_patterns.add(ngram)
            confidence = min(1.0, (rejection_rate * 0.7) + (min(rejected_count, 10) / 10 * 0.3))
            samples = ngram_to_samples[ngram][:5]

            tipo = "1-gram" if len(words) == 1 else f"{len(words)}-gram"
            suggestions.append(
                {
                    "type": "title_pattern",
                    "pattern": ngram,
                    "description": (
                        f"La expresión '{ngram}' ({tipo}) aparece en {rejected_count} de tus "
                        f"jobs rechazados ({int(rejection_rate * 100)}% tasa de rechazo). "
                        f"Activar este filtro excluirá jobs con este término en el título."
                    ),
                    "confidence": confidence,
                    "samples": samples,
                    "affected_count": rejected_count,
                }
            )

            if len(suggestions) >= _MAX_SUGGESTIONS_PER_TYPE:
                break

        return suggestions

    def _analyze_tag_patterns(
        self,
        rejected_jobs: list[dict],
        all_jobs: list[dict],
        min_rejected: int,
    ) -> list[dict]:
        """Detecta tags con alta tasa de rechazo."""
        rejected_tag_counts: Counter = Counter()
        all_tag_counts: Counter = Counter()
        tag_to_samples: dict[str, list[dict]] = defaultdict(list)

        for job in rejected_jobs:
            seen_tags = set()
            for tag in job.get("tags") or []:
                tag_lower = tag.lower().strip()
                if tag_lower and tag_lower not in seen_tags:
                    seen_tags.add(tag_lower)
                    rejected_tag_counts[tag_lower] += 1
                    tag_to_samples[tag_lower].append(
                        {"title": job["title"], "company": job["company"]}
                    )

        for job in all_jobs:
            seen_tags = set()
            for tag in job.get("tags") or []:
                tag_lower = tag.lower().strip()
                if tag_lower and tag_lower not in seen_tags:
                    seen_tags.add(tag_lower)
                    all_tag_counts[tag_lower] += 1

        suggestions: list[dict] = []

        for tag, rejected_count in rejected_tag_counts.most_common(50):
            if rejected_count < min_rejected:
                break

            total_count = all_tag_counts.get(tag, rejected_count)
            rejection_rate = rejected_count / total_count

            if rejection_rate < _MIN_REJECTION_RATE:
                continue

            confidence = min(1.0, (rejection_rate * 0.7) + (min(rejected_count, 10) / 10 * 0.3))
            samples = tag_to_samples[tag][:5]

            suggestions.append(
                {
                    "type": "tag_category",
                    "pattern": tag,
                    "description": (
                        f"El tag '{tag}' aparece en {rejected_count} de tus jobs rechazados "
                        f"({int(rejection_rate * 100)}% tasa de rechazo). "
                        f"Activar este filtro excluirá jobs que incluyan esta etiqueta."
                    ),
                    "confidence": confidence,
                    "samples": samples,
                    "affected_count": rejected_count,
                }
            )

            if len(suggestions) >= _MAX_SUGGESTIONS_PER_TYPE:
                break

        return suggestions
