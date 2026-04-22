"""AI-powered job matching: embedding similarity + multi-factor scoring.

Uses paraphrase-multilingual-MiniLM-L12-v2 for cross-language embeddings.
Model loading is deferred until first use (lazy singleton).
"""

import numpy as np

from config import settings

# Default scoring weights (user can customize via profile.score_weights)
# Language factor added: rewards jobs whose required language matches user profile.
DEFAULT_WEIGHTS = {
    "embedding": 0.35,
    "salary": 0.15,
    "location": 0.10,
    "recency": 0.15,
    "llm": 0.15,
    "language": 0.10,
}

# Maps ISO 639-1 language codes → lowercase name variants used in user profiles
_LANGUAGE_CODE_MAP: dict[str, set[str]] = {
    "en": {"english", "inglés", "ingles"},
    "es": {"spanish", "español", "espanol"},
    "de": {"german", "deutsch", "alemán", "aleman"},
    "fr": {"french", "français", "francais", "francés", "frances"},
    "it": {"italian", "italiano"},
    "ja": {"japanese", "japonés", "japones"},
    "pt": {"portuguese", "português", "portugues"},
    "nl": {"dutch", "nederlands"},
    "zh": {"chinese", "mandarin"},
    "ru": {"russian", "ruso"},
    "ar": {"arabic", "árabe"},
    "ko": {"korean", "coreano"},
    "pl": {"polish", "polaco"},
    "sv": {"swedish", "sueco"},
}


class JobMatcher:
    """Core matching engine. Combines embedding similarity with multi-factor scoring."""

    _model = None

    @classmethod
    def _get_model(cls):
        """Lazy-load the SentenceTransformer model."""
        if cls._model is None:
            from sentence_transformers import SentenceTransformer

            cls._model = SentenceTransformer(
                settings.EMBEDDING_MODEL_NAME, device=settings.EMBEDDING_DEVICE
            )
        return cls._model

    @staticmethod
    def build_job_text(job: dict) -> str:
        """Combine job fields into a single text for embedding."""
        parts = [
            job.get("title", ""),
            job.get("company", ""),
            job.get("description", ""),
        ]
        tags = job.get("tags", [])
        if tags:
            parts.append(" ".join(tags))
        return " ".join(p for p in parts if p)

    def encode(self, text: str) -> np.ndarray:
        """Encode a text string to a 384-dim embedding vector."""
        model = self._get_model()
        return model.encode(text, normalize_embeddings=True)

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts. Returns (N, 384) array."""
        model = self._get_model()
        return model.encode(
            texts, normalize_embeddings=True, batch_size=settings.EMBEDDING_BATCH_SIZE
        )

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    def compute_embedding_score(
        self, profile_embedding: np.ndarray, job_embedding: np.ndarray
    ) -> float:
        """Compute embedding similarity score [0, 1]."""
        return max(0.0, self.cosine_similarity(profile_embedding, job_embedding))

    @staticmethod
    def compute_salary_match(
        user_min: int | None,
        user_max: int | None,
        job_min: int | None,
        job_max: int | None,
    ) -> float:
        """Score salary match [0, 1]. Returns 0.5 if either side has no data."""
        if user_min is None and user_max is None:
            return 0.5  # No preference
        if job_min is None and job_max is None:
            return 0.5  # No data

        user_mid = ((user_min or 0) + (user_max or user_min or 0)) / 2
        job_mid = ((job_min or 0) + (job_max or job_min or 0)) / 2

        if user_mid == 0 or job_mid == 0:
            return 0.5

        ratio = job_mid / user_mid
        if ratio >= 1.0:
            return 1.0  # Job pays more — perfect
        if ratio >= 0.8:
            return 0.5 + (ratio - 0.8) * 2.5  # Linear from 0.5 to 1.0
        return max(0.0, ratio / 0.8 * 0.5)  # Linear from 0.0 to 0.5

    @staticmethod
    def compute_location_match(
        user_locations: list[str], job_location: str | None
    ) -> float:
        """Score location match [0, 1]."""
        if not user_locations:
            return 0.5  # No preference
        if not job_location:
            return 0.3  # Unknown location

        job_loc_lower = job_location.lower()
        for loc in user_locations:
            if loc.lower() in job_loc_lower or job_loc_lower in loc.lower():
                return 1.0
        return 0.0

    @staticmethod
    def compute_recency_score(days_old: int) -> float:
        """Score recency [0, 1]. Newer jobs score higher."""
        if days_old <= 1:
            return 1.0
        if days_old <= 7:
            return 0.8
        if days_old <= 14:
            return 0.5
        if days_old <= 30:
            return 0.3
        return 0.1

    @staticmethod
    def compute_language_match(
        user_languages: list[str], job_language: str | None
    ) -> float:
        """Score language match [0, 1].

        1.0 = job language matches at least one user profile language.
        0.5 = job language unknown (neutral, no penalty).
        0.2 = job language not in user profile.
        """
        if not job_language:
            return 0.5  # Unknown — neutral
        code = job_language.lower().strip()
        name_variants = _LANGUAGE_CODE_MAP.get(code, {code})
        user_langs_lower = {lang.lower() for lang in user_languages}
        if name_variants & user_langs_lower:
            return 1.0
        return 0.2

    def compute_final_score(
        self,
        embedding_score: float,
        salary_score: float,
        location_score: float,
        recency_score: float,
        llm_score: float = 0.0,
        language_score: float = 0.5,
        weights: dict | None = None,
    ) -> float:
        """Compute weighted final score [0, 100]."""
        w = weights or DEFAULT_WEIGHTS
        raw = (
            w.get("embedding", 0.35) * embedding_score
            + w.get("salary", 0.15) * salary_score
            + w.get("location", 0.10) * location_score
            + w.get("recency", 0.15) * recency_score
            + w.get("llm", 0.15) * (llm_score / 100.0 if llm_score > 1 else llm_score)
            + w.get("language", 0.10) * language_score
        )
        return round(raw * 100, 1)
