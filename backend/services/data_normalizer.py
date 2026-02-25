"""DataNormalizer — enrich job dicts with salary, language, seniority, contract type."""

import logging
import re

from langdetect import LangDetectException, detect_langs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Currency conversion rates (CHF base)
# ---------------------------------------------------------------------------

CURRENCY_TO_CHF: dict[str, float] = {
    "CHF": 1.0,
    "EUR": 0.96,
    "USD": 0.88,
    "GBP": 1.12,
}

PERIOD_MULTIPLIER: dict[str, int] = {
    "yearly": 1,
    "monthly": 12,
    "hourly": 2080,  # Standard Swiss working hours/year
}

# ---------------------------------------------------------------------------
# Seniority patterns (checked in priority order: most senior first)
# ---------------------------------------------------------------------------

SENIORITY_PATTERNS: list[tuple[str, list[str]]] = [
    ("head", ["head of", "director", "directeur", "direktor", "chef de"]),
    ("lead", ["lead", "leiter", "team lead", "chef d'équipe", "teamleiter"]),
    ("senior", ["senior", "sr.", "experienced", "erfahren", "expérimenté"]),
    ("mid", ["mid-level", "mid level", "confirmé", "confirmed"]),
    ("junior", ["junior", "jr.", "anfänger", "débutant"]),
    (
        "intern",
        [
            "intern",
            "internship",
            "praktikant",
            "praktikum",
            "stage",
            "stagiaire",
            "trainee",
        ],
    ),
]

# ---------------------------------------------------------------------------
# Contract type patterns
# ---------------------------------------------------------------------------

CONTRACT_PATTERNS: list[tuple[str, list[str]]] = [
    (
        "apprenticeship",
        ["apprenticeship", "apprentissage", "lehre", "lehrstelle", "lehrling"],
    ),
    ("internship", ["internship", "praktikum", "stage", "stagiaire", "trainee"]),
    ("temporary", ["temporary", "temp ", "temporär", "intérim", "interim"]),
    (
        "contract",
        ["contract", "freelance", "befristet", "cdd", "contrat à durée déterminée"],
    ),
    (
        "part_time",
        [
            "part-time",
            "part time",
            "teilzeit",
            "temps partiel",
            "50%",
            "60%",
            "70%",
            "80%",
            "90%",
        ],
    ),
    (
        "full_time",
        [
            "full-time",
            "full time",
            "100%",
            "vollzeit",
            "temps plein",
            "festanstellung",
            "unbefristet",
            "cdi",
            "permanent",
        ],
    ),
]

# Salary string parsing pattern: captures ranges like "80000-100000", "80k-100k"
_SALARY_RANGE_RE = re.compile(r"(\d[\d.,]*)\s*[kK]?\s*[-–—to]+\s*(\d[\d.,]*)\s*[kK]?")
_SALARY_SINGLE_RE = re.compile(r"(\d[\d.,]+)\s*[kK]?")
_CURRENCY_RE = re.compile(r"\b(CHF|EUR|USD|GBP|€|\$|£)\b", re.IGNORECASE)

_CURRENCY_SYMBOL_MAP: dict[str, str] = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "chf": "CHF",
    "eur": "EUR",
    "usd": "USD",
    "gbp": "GBP",
}


class DataNormalizer:
    """Stateless job enrichment. All methods are static."""

    @staticmethod
    def normalize(job: dict) -> dict:
        """Run all normalization steps on a job dict."""
        job = DataNormalizer.normalize_salary(job)
        job = DataNormalizer.detect_language(job)
        job = DataNormalizer.infer_seniority(job)
        job = DataNormalizer.infer_contract_type(job)
        return job

    # ------------------------------------------------------------------
    # Salary
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_salary(job: dict) -> dict:
        """Convert salary to CHF annual. Parses salary_original if needed."""
        # Already normalized
        if job.get("salary_min_chf") and job.get("salary_max_chf"):
            return job

        salary_orig = job.get("salary_original") or ""
        currency = job.get("salary_currency")
        period = job.get("salary_period")
        sal_min = job.get("salary_min_chf")
        sal_max = job.get("salary_max_chf")

        # Try to parse from salary_original string
        if salary_orig and not (sal_min or sal_max):
            sal_min, sal_max, parsed_currency = DataNormalizer._parse_salary_string(
                salary_orig
            )
            if parsed_currency and not currency:
                currency = parsed_currency

        if not (sal_min or sal_max):
            return job

        # Convert currency to CHF
        rate = CURRENCY_TO_CHF.get(currency.upper(), 1.0) if currency else 1.0

        # Annualize
        multiplier = PERIOD_MULTIPLIER.get(period, 1) if period else 1

        if sal_min:
            job["salary_min_chf"] = int(sal_min * rate * multiplier)
        if sal_max:
            job["salary_max_chf"] = int(sal_max * rate * multiplier)

        return job

    @staticmethod
    def _parse_salary_string(
        text: str,
    ) -> tuple[float | None, float | None, str | None]:
        """Extract min, max salary and currency from a free-text salary string."""
        currency = None
        cur_match = _CURRENCY_RE.search(text)
        if cur_match:
            raw_cur = cur_match.group(1)
            currency = _CURRENCY_SYMBOL_MAP.get(raw_cur.lower(), raw_cur.upper())

        # Try range first: "80000-100000" or "80k-100k"
        range_match = _SALARY_RANGE_RE.search(text)
        if range_match:
            lo = DataNormalizer._parse_number(range_match.group(1), text)
            hi = DataNormalizer._parse_number(range_match.group(2), text)
            return lo, hi, currency

        # Single value: treat as both min and max
        single_match = _SALARY_SINGLE_RE.search(text)
        if single_match:
            val = DataNormalizer._parse_number(single_match.group(1), text)
            return val, val, currency

        return None, None, currency

    @staticmethod
    def _parse_number(raw: str, context: str = "") -> float | None:
        """Parse a number string, handling 'k' suffix and European formatting."""
        if not raw:
            return None
        cleaned = raw.replace(",", "").replace(".", "").strip()
        try:
            value = float(cleaned)
        except ValueError:
            return None
        # Detect "k" suffix in context near the number
        if "k" in context.lower() and value < 1000:
            value *= 1000
        return value

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_language(job: dict) -> dict:
        """Detect job language from title + description using langdetect."""
        if job.get("language"):
            return job

        text = f"{job.get('title', '')} {job.get('description', '')}".strip()
        if len(text) < 50:
            return job

        try:
            results = detect_langs(text)
            if results and results[0].prob >= 0.7:
                lang = results[0].lang
                if lang in ("de", "fr", "en", "it"):
                    job["language"] = lang
        except LangDetectException:
            pass

        return job

    # ------------------------------------------------------------------
    # Seniority inference
    # ------------------------------------------------------------------

    @staticmethod
    def infer_seniority(job: dict) -> dict:
        """Infer seniority level from job title."""
        if job.get("seniority"):
            return job

        title_lower = (job.get("title") or "").lower()
        if not title_lower:
            return job

        for level, keywords in SENIORITY_PATTERNS:
            for keyword in keywords:
                if keyword in title_lower:
                    job["seniority"] = level
                    return job

        return job

    # ------------------------------------------------------------------
    # Contract type inference
    # ------------------------------------------------------------------

    @staticmethod
    def infer_contract_type(job: dict) -> dict:
        """Infer contract type from employment_type, title, or description."""
        if job.get("contract_type"):
            return job

        # Check multiple fields in priority order
        texts = [
            (job.get("employment_type") or ""),
            (job.get("title") or ""),
            (job.get("description_snippet") or ""),
        ]
        combined = " ".join(texts).lower()
        if not combined.strip():
            return job

        for ct, keywords in CONTRACT_PATTERNS:
            for keyword in keywords:
                if keyword in combined:
                    job["contract_type"] = ct
                    return job

        return job
