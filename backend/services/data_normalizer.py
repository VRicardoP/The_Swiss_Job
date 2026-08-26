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
    # week/day existen en productores reales (JSearch emite WEEK/DAY): sin
    # multiplicador, un "1500 EUR/week" se guardaba como 1500 anuales (G1/P2-8).
    "weekly": 52,
    "daily": 260,
}

# Periodos anualizables que el enum de BD (SalaryPeriod) NO modela: se usan
# para el multiplicador y después salary_period se guarda como None (añadirlos
# al enum exigiría migración; el importe anual CHF queda correcto, que es lo
# que consume el matching).
PERIODS_NOT_IN_DB_ENUM = {"weekly", "daily"}

# Map raw provider values to valid enum values
PERIOD_ALIASES: dict[str, str] = {
    "year": "yearly",
    "annual": "yearly",
    "annually": "yearly",
    "per_year": "yearly",
    "month": "monthly",
    "per_month": "monthly",
    "hour": "hourly",
    "per_hour": "hourly",
    "yearly": "yearly",
    "monthly": "monthly",
    "hourly": "hourly",
    "week": "weekly",
    "per_week": "weekly",
    "weekly": "weekly",
    "day": "daily",
    "per_day": "daily",
    "daily": "daily",
}

SENIORITY_VALID = {"intern", "junior", "mid", "senior", "lead", "head", "director"}

CONTRACT_VALID = {
    "full_time",
    "part_time",
    "contract",
    "internship",
    "apprenticeship",
    "temporary",
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
            "werkstudent",
            "abschlussarbeit",
            "bachelorarbeit",
            "masterarbeit",
            "diplomarbeit",
            "studienarbeit",
            "praxissemester",
            "pflichtpraktikum",
        ],
    ),
]

# ---------------------------------------------------------------------------
# Contract type patterns
# ---------------------------------------------------------------------------

CONTRACT_PATTERNS: list[tuple[str, list[str]]] = [
    (
        "apprenticeship",
        [
            "apprenticeship",
            "apprentissage",
            "lehre",
            "lehrstelle",
            "lehrling",
            "berufslehre",
            "ausbildungsplatz",
        ],
    ),
    (
        "internship",
        [
            "internship",
            "praktikum",
            "stage",
            "stagiaire",
            "trainee",
            "werkstudent",
            "abschlussarbeit",
            "bachelorarbeit",
            "masterarbeit",
            "pflichtpraktikum",
            "praxissemester",
        ],
    ),
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

# Salary string parsing pattern: captures ranges like "80000-100000", "80k-100k".
# Los números aceptan `'`/`’` (separador de miles suizo: "80'000") además de
# `.`/`,`. La "k" se captura ADYACENTE al número y se exige que no siga una
# letra (G1/P2-7: "80 CHF ... Kanton" no debe multiplicar ×1000). El separador
# de rango es un guion o la palabra "to" — NO una clase de caracteres, que
# casaba letras sueltas (G1/P3-13).
_NUM = r"(\d(?:[\d.,'’]*\d)?)"
# G2/P3-3: el fin de número(+k) exige borde REAL — fin de texto, un carácter
# no alfanumérico o un código de divisa pegado ("80'000CHF", "100kEUR"). El
# lookahead negativo anterior (solo letras) dejaba retroceder al motor DENTRO
# del número hasta que siguiera un dígito: "80'000CHF" casaba `80'00` → 8000
# (plausible-pero-falso). "Kanton" sigue sin multiplicar: su K va seguida de
# letra y no es divisa.
_K = r"\s*([kK])?(?=$|[^0-9A-Za-zÀ-ÿ]|(?i:CHF|EUR|USD|GBP)\b)"
_SALARY_RANGE_RE = re.compile(rf"{_NUM}{_K}\s*(?:[-–—]+|to)\s*{_NUM}{_K}")
# El single exige ≥2 dígitos (como el `[\d.,]+` original): un dígito suelto
# ("Level 5") no es un salario.
_SALARY_SINGLE_RE = re.compile(rf"(\d[\d.,'’]*\d){_K}")
# Pensums/porcentajes ("60-80%", "50 %"): se eliminan ANTES de parsear para
# que un workload no se confunda con un salario (G1/P3-13).
_PCT_TOKEN_RE = re.compile(r"\d[\d.,'’]*(?:\s*[-–—]\s*\d[\d.,'’]*)?\s*%")
# Los símbolos €/$/£ no tienen word-boundary a su lado ("\b€\b" no casa nunca,
# G1/P3-12): se buscan sin \b, en alternancia con los códigos ISO. Los códigos
# usan borde de LETRA, no \b: entre dígito y letra no hay \b y "80'000CHF"
# (divisa pegada, G2/P3-3) se quedaba sin divisa → sin conversión a CHF. El
# lookbehind alternativo `\d[kK]` cubre el shorthand con divisa pegada
# ("100kEUR"), donde la letra que precede al código es la propia «k».
_CURRENCY_RE = re.compile(
    r"(?:(?<=\d[kK])|(?<![A-Za-z]))(CHF|EUR|USD|GBP)(?![A-Za-z])|([€$£])",
    re.IGNORECASE,
)

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
        job = DataNormalizer.sanitize_enums(job)
        job = DataNormalizer.normalize_salary(job)
        job = DataNormalizer.detect_language(job)
        job = DataNormalizer.infer_seniority(job)
        job = DataNormalizer.infer_contract_type(job)
        job = DataNormalizer.classify_category(job)
        return job

    @staticmethod
    def classify_category(job: dict) -> dict:
        """Asigna la categoría del análisis maestro (A–M o 'otros')."""
        from services.job_classifier import classify_job

        job["category"] = classify_job(
            title=job.get("title") or "",
            tags=job.get("tags") or [],
        )
        return job

    @staticmethod
    def sanitize_enums(job: dict) -> dict:
        """Map raw provider enum values to valid DB enum values."""
        period = job.get("salary_period")
        if period:
            mapped = PERIOD_ALIASES.get(period.lower())
            job["salary_period"] = mapped  # None if unknown → won't break insert

        seniority = job.get("seniority")
        if seniority and seniority not in SENIORITY_VALID:
            job["seniority"] = None

        contract = job.get("contract_type")
        if contract and contract not in CONTRACT_VALID:
            job["contract_type"] = None

        return job

    # ------------------------------------------------------------------
    # Salary
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_salary(job: dict) -> dict:
        """Convert salary to CHF annual. Parses salary_original if needed."""
        # Periodo anualizable fuera del enum de BD (weekly/daily): se usa para
        # el multiplicador pero NO puede persistirse — se anula ANTES de
        # cualquier early-return para que nunca llegue al INSERT (G1/P2-8).
        period = job.get("salary_period")
        if period:
            period = PERIOD_ALIASES.get(period.lower(), period)
        if period in PERIODS_NOT_IN_DB_ENUM:
            job["salary_period"] = None

        # Already normalized. Basta UNA cota: si el productor prellenó
        # cualquier `_chf`, re-multiplicarla aquí sería doble conversión
        # (G1/P3-11 — trampa asimétrica de la familia ~2000x).
        if job.get("salary_min_chf") or job.get("salary_max_chf"):
            return job

        salary_orig = job.get("salary_original") or ""
        currency = job.get("salary_currency")
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
            # group(1) = código ISO con \b; group(2) = símbolo €/$/£ sin \b.
            raw_cur = cur_match.group(1) or cur_match.group(2)
            currency = _CURRENCY_SYMBOL_MAP.get(raw_cur.lower(), raw_cur.upper())

        # Pensums/porcentajes fuera: "60-80%" es workload, no salario.
        text = _PCT_TOKEN_RE.sub(" ", text)

        # Try range first: "80000-100000" or "80k-100k"
        range_match = _SALARY_RANGE_RE.search(text)
        if range_match:
            lo = DataNormalizer._parse_number(
                range_match.group(1), has_k=bool(range_match.group(2))
            )
            hi = DataNormalizer._parse_number(
                range_match.group(3), has_k=bool(range_match.group(4))
            )
            # G2/P2-3: shorthand «80-100k» — la única "k" (en la cota alta)
            # cubre también la baja: 80-100k significa 80k-100k. Sin esto,
            # salary_min quedaba en 80 CHF anuales (dato corrupto persistido).
            if (
                lo is not None
                and hi is not None
                and range_match.group(4)
                and not range_match.group(2)
                and lo < 1000 <= hi
            ):
                lo *= 1000
            return lo, hi, currency

        # Single value: treat as both min and max
        single_match = _SALARY_SINGLE_RE.search(text)
        if single_match:
            val = DataNormalizer._parse_number(
                single_match.group(1), has_k=bool(single_match.group(2))
            )
            return val, val, currency

        return None, None, currency

    @staticmethod
    def _parse_number(raw: str, has_k: bool = False) -> float | None:
        """Parse a number string, handling thousands/decimal separators.

        `.`/`,` se interpretan por POSICIÓN (G1/P2-6): un grupo final de
        exactamente 3 dígitos es separador de miles ("80.000", "1,234");
        1-2 dígitos finales son decimales ("25.5", "45,50"). `'`/`’` son
        SIEMPRE separador de miles (formato suizo "80'000").

        `has_k`: la "k" la detecta el REGEX adyacente al número — nunca una
        palabra del texto circundante como "Kanton" (G1/P2-7).
        """
        if not raw:
            return None
        cleaned = raw.strip().replace("'", "").replace("’", "")
        last_sep = max(cleaned.rfind("."), cleaned.rfind(","))
        if last_sep != -1:
            frac = cleaned[last_sep + 1 :]
            if len(frac) == 3 and frac.isdigit():
                # Grupo de miles: se eliminan TODOS los separadores.
                int_part, frac = cleaned, ""
            else:
                int_part = cleaned[:last_sep]
            int_part = int_part.replace(".", "").replace(",", "")
            cleaned = f"{int_part}.{frac}" if frac else int_part
        try:
            value = float(cleaned)
        except ValueError:
            return None
        if has_k and value < 1000:
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
