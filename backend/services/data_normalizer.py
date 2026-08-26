"""DataNormalizer — enrich job dicts with salary, language, seniority, contract type."""

import logging
import re

from langdetect import LangDetectException, detect_langs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Currency conversion rates (CHF base)
# ---------------------------------------------------------------------------

# Tasas APROXIMADAS y ESTÁTICAS (no hay feed de divisas en el proyecto): su
# función es ordenar salarios entre sí para el matching, no dar una conversión
# financiera exacta. G3/P2-5 amplía el mapa más allá de CHF/EUR/USD/GBP porque
# una divisa AUSENTE ya no se convierte 1:1 — se descarta el importe.
CURRENCY_TO_CHF: dict[str, float] = {
    "CHF": 1.0,
    "EUR": 0.96,
    "USD": 0.88,
    "GBP": 1.12,
    # G3/P2-5: divisas emitidas por portales remotos globales (jobgether y
    # compañía). Sin ellas, 107 filas de producción quedaron con el importe
    # nominal guardado como CHF (INR ≈ ×106, ZAR ≈ ×21).
    "CAD": 0.64,
    "AUD": 0.58,
    "NOK": 0.082,
    "SEK": 0.083,
    "DKK": 0.13,
    "PLN": 0.22,
    "INR": 0.010,
    "ZAR": 0.048,
    "SGD": 0.66,
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
# G3/P2-3: el ESPACIO (y el espacio duro U+00A0) es el separador de miles del
# francés, del uso suizo francófono y de la propia norma ISO 31-0. Se admite
# SOLO entre grupos de EXACTAMENTE 3 dígitos, para no tragarse números vecinos
# ("80000 100000" siguen siendo dos números). La alternativa va PRIMERO: el
# motor debe preferir "100 000" antes que quedarse en "100" (que persistía
# 100 CHF donde el portal decía 100 000 — error ×1000).
_SPACED = r"\d{1,3}(?:[ \xa0][0-9]{3})+"
_NUM = rf"({_SPACED}|\d(?:[\d.,'’]*\d)?)"
# G2/P3-3: el fin de número(+k) exige borde REAL — fin de texto, un carácter
# no alfanumérico o un código de divisa pegado ("80'000CHF", "100kEUR"). El
# lookahead negativo anterior (solo letras) dejaba retroceder al motor DENTRO
# del número hasta que siguiera un dígito: "80'000CHF" casaba `80'00` → 8000
# (plausible-pero-falso). "Kanton" sigue sin multiplicar: su K va seguida de
# letra y no es divisa.
_K = r"\s*([kK])?(?=$|[^0-9A-Za-zÀ-ÿ]|(?i:CHF|EUR|USD|GBP)\b)"
# G3/P2-4: la forma canónica británica/irlandesa repite la divisa en el segundo
# extremo ("£30,000 - £40,000"). Sin ella el rango no casaba y el parser caía EN
# SILENCIO al camino `single`, que devuelve la primera cifra como mínimo Y
# máximo (30000-30000 persistido).
#
# G4/P2-1: hacer la divisa OPCIONAL en el patrón único fue una regresión. Como
# `re.search` es leftmost, «<número de ruido> - <DIVISA><importe>» pasaba a ser
# un rango válido y el ruido secuestraba la cota baja: «Grade 6 - £30,000» se
# persistía con salary_min_chf = 6. Antes del cambio ese patrón NO casaba —el
# `£` cortaba el segundo `_NUM`— y el `single` encontraba el importe real: la
# red de seguridad desapareció. Los productores están vivos (`scrapers/tes.py`
# e `scrapers/irishjobs.py` vuelcan texto libre) y las escalas salariales
# británicas e irlandesas se escriben exactamente así: Grade N, Band N,
# NJC Scale N, MPS/UPS N, Point N.
#
# Son DOS patrones probados en orden, no uno con la divisa opcional:
#   1) divisa EXIGIDA delante de AMBOS extremos — el caso de G3/P2-4. Al ir
#      primero también desempata «Main Pay Scale 1 - 6 (£31,650 - £43,607)»
#      a favor del importe y no de la escala.
#   2) el patrón clásico anterior a `c20c0b8`, sin divisa entre medias.
# Ninguno captura la divisa: los grupos siguen siendo 1..4 en los dos.
_CUR_TOK = r"(?:(?i:CHF|EUR|USD|GBP)\s*|[€$£]\s*)"
_SALARY_RANGE_CUR_RE = re.compile(
    rf"{_CUR_TOK}{_NUM}{_K}\s*(?:[-–—]+|to)\s*{_CUR_TOK}{_NUM}{_K}"
)
# G5/P3-5: la forma «divisa solo en el extremo DERECHO» ("30,000 - £40,000")
# no casaba ninguno de los dos anteriores —el `£` corta el segundo `_NUM` del
# clásico— y colapsaba EN SILENCIO al camino `single`, que persiste
# 30000-30000. Va en TERCER lugar y su cota baja pasa además por
# `_LOW_LOOKS_LIKE_SALARY`: sin esa comprobación este patrón reabriría
# exactamente la regresión que cerró G4/P2-1, porque «Grade 6 - £30,000» tiene
# LA MISMA forma que «30,000 - £40,000» y el ruido secuestraría el mínimo.
_SALARY_RANGE_CUR_RIGHT_RE = re.compile(
    rf"{_NUM}{_K}\s*(?:[-–—]+|to)\s*{_CUR_TOK}{_NUM}{_K}"
)
_SALARY_RANGE_RE = re.compile(rf"{_NUM}{_K}\s*(?:[-–—]+|to)\s*{_NUM}{_K}")
# El single exige ≥2 dígitos (como el `[\d.,]+` original): un dígito suelto
# ("Level 5") no es un salario.
_SALARY_SINGLE_RE = re.compile(rf"({_SPACED}|\d[\d.,'’]*\d){_K}")
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
    # Abreviatura suiza del franco, de uso corriente en los portales DE/FR.
    "fr.": "CHF",
    "fr": "CHF",
    "sfr": "CHF",
    "sfr.": "CHF",
}


def _canonical_currency(raw: str) -> str:
    """Código ISO de una divisa escrita como venga (G4/P3-8).

    Tolera espacios, minúsculas y símbolos; lo que no reconoce lo devuelve en
    mayúsculas para que el llamante decida (hoy: descartar el importe).
    """
    cleaned = (raw or "").strip()
    return _CURRENCY_SYMBOL_MAP.get(cleaned.lower(), cleaned.upper())


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

        # G6/P2-1 — los extremos se ORDENAN. Ninguna cota posterior corregía un
        # rango invertido y `compute_salary_match` recibía un intervalo
        # imposible (`salary_min_chf` 5.600 > `salary_max_chf` 2.240). Es
        # alcanzable por varias vías —un bonus con divisa detrás del rango real,
        # o formas del corpus como "€100,000 - €00 per annum"— así que se
        # arregla aquí, donde los dos extremos ya están juntos, y no en cada
        # patrón.
        if sal_min is not None and sal_max is not None and sal_min > sal_max:
            sal_min, sal_max = sal_max, sal_min

        # Convert currency to CHF. G3/P2-5: una divisa que NO sabemos convertir
        # ya no se guarda 1:1 como CHF — eso puntuaba al máximo en el factor
        # salario y desplazaba vacantes suizas reales del top del feed. Mejor
        # sin importe que un importe ×100: salary_original y salary_currency
        # siguen en la fila, así que el dato crudo no se pierde.
        rate = 1.0
        if currency:
            # G4/P3-8: la divisa llega cruda del portal (`providers/jsearch.py`
            # pasa el valor de la API tal cual). " EUR ", "chf ", "€", "$",
            # "£" y "Fr." caían todas en el camino «divisa desconocida» del fix
            # G3/P2-5 y descartaban el importe ENTERO. Se normaliza con el
            # mismo mapa que usa el parser de la cadena libre.
            known_rate = CURRENCY_TO_CHF.get(_canonical_currency(currency))
            if known_rate is None:
                logger.warning(
                    "Unknown salary currency %r — salary_*_chf left empty", currency
                )
                job["salary_min_chf"] = None
                job["salary_max_chf"] = None
                return job
            rate = known_rate

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
            currency = _canonical_currency(raw_cur)

        # Pensums/porcentajes fuera: "60-80%" es workload, no salario.
        text = _PCT_TOKEN_RE.sub(" ", text)

        # Try range first: "80000-100000" or "80k-100k". El patrón con divisa
        # en ambos extremos va PRIMERO (G4/P2-1): es el más específico y el
        # único que puede desempatar un rango real de una escala salarial.
        # G5/P3-5: después, el de divisa solo a la derecha, cuya cota baja debe
        # parecer un salario para no reabrir la regresión de G4/P2-1.
        #
        # G6/P2-1 — entre el de divisa-a-la-derecha y el clásico se elige por
        # POSICIÓN, no por prioridad global. `re.search` barre TODO el texto, así
        # que encadenar prioridades hacía que un match del patrón nuevo en la
        # posición 23 desplazara al clásico en la 0 y el clásico ni se probara:
        #   "90000 - 110000 par an (7500 - CHF 9200 par mois)"
        #       -> persistía el MENSUAL (7500-9200), ~12x menos que el anual;
        #   "80'000 - 100'000 (bonus 5,000 - GBP 2,000)"
        #       -> persistía el bonus, y con los extremos INVERTIDOS
        #          (salary_min_chf 5600 > salary_max_chf 2240).
        # El más a la izquierda gana: es el rango que el anuncio enuncia como
        # suyo, y lo de después (paréntesis, bonus, referencia) es glosa.
        range_match = _SALARY_RANGE_CUR_RE.search(text)
        if range_match is None:
            right = _SALARY_RANGE_CUR_RIGHT_RE.search(text)
            plain = _SALARY_RANGE_RE.search(text)
            if (
                right is not None
                and DataNormalizer._low_looks_like_salary(right)
                and (plain is None or right.start() <= plain.start())
            ):
                range_match = right
            else:
                range_match = plain
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
            # G5/P3-5: la divisa se toma del PROPIO rango que casó, no de un
            # `_CURRENCY_RE.search` aparte sobre TODO el texto. Los dos regex
            # pueden apuntar a sitios distintos y el resultado mezclaba el
            # importe de uno con la divisa del otro: en
            # "90'000 - 110'000 CHF (env. £75,000 - £90,000)" el rango elegido
            # es el del paréntesis (delimitado por divisa, el más específico) y
            # la divisa de todo el texto es la PRIMERA, `CHF` — se persistía
            # 75.000-90.000 CHF cuando el texto dice libras. Si el rango que
            # casó no lleva divisa dentro, se conserva la del texto (que es el
            # comportamiento de siempre para "80000-100000 CHF").
            cur_in_range = _CURRENCY_RE.search(range_match.group(0))
            if cur_in_range:
                currency = _canonical_currency(
                    cur_in_range.group(1) or cur_in_range.group(2)
                )
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
    def _low_looks_like_salary(match: re.Match) -> bool:
        """¿La cota baja de un rango «<num> - <divisa><num>» es un importe?

        G5/P3-5 — «30,000 - £40,000» y «Grade 6 - £30,000» tienen la MISMA
        forma; lo único que las separa es la magnitud. Sin este filtro, admitir
        el patrón de divisa-a-la-derecha reabriría la regresión de G4/P2-1
        (`salary_min_chf = 6`), y las escalas británicas e irlandesas —Grade N,
        Band N, NJC Scale N, MPS/UPS N, Point N— se escriben exactamente así.
        Se exige que la cota baja llegue a 1.000 o traiga su propia «k».

        COTA PREEXISTENTE que este fix NO cierra (medida, no razonada): cuando
        el número de escala tiene DOS dígitos y no hay rango que casar
        —«Point 12 - €35,000», «Stufe 12 - CHF 90'000»—, el camino `single`
        sigue tomando el primero que encuentra (12) porque `re.search` es
        leftmost y `_SALARY_SINGLE_RE` solo exige 2 dígitos. Es el mismo
        comportamiento de antes de G5 y no produce ninguna diferencia sobre los
        637 valores reales del corpus; se deja escrito para que no se confunda
        con lo que aquí sí se arregla.
        """
        if match.group(2):  # shorthand con «k»: "30k - £40,000"
            return True
        low = DataNormalizer._parse_number(match.group(1))
        return low is not None and low >= 1000

    @staticmethod
    def _parse_number(raw: str, has_k: bool = False) -> float | None:
        """Parse a number string, handling thousands/decimal separators.

        `.`/`,` se interpretan por POSICIÓN (G1/P2-6): un grupo final de
        exactamente 3 dígitos es separador de miles ("80.000", "1,234");
        1-2 dígitos finales son decimales ("25.5", "45,50"). `'`/`’` son
        SIEMPRE separador de miles (formato suizo "80'000"), y el espacio
        (normal o duro) también lo es: el regex ya sólo lo deja pasar entre
        grupos de 3 dígitos, aquí basta con eliminarlo (G3/P2-3).

        `has_k`: la "k" la detecta el REGEX adyacente al número — nunca una
        palabra del texto circundante como "Kanton" (G1/P2-7).
        """
        if not raw:
            return None
        cleaned = (
            raw.strip()
            .replace("'", "")
            .replace("’", "")
            .replace(" ", "")
            .replace("\xa0", "")
        )
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
