"""JobClassifier — clasifica jobs en las 13 categorías del perfil.

Equivalente Python de frontend/src/utils/jobCategories.js.
Se ejecuta en DataNormalizer.normalize() en el momento de ingesta.

Categorías A–G: objetivo del análisis maestro (ANALISIS_MAESTRO_EMPLEABILIDAD_ALICIA_MOORE.md)
Categorías H–M: fuera del perfil objetivo — reciben penalización en el scoring
Categoría "otros": jobs no clasificados (IT, ingeniería, medicina, etc.)
"""

import re as _re

# ---------------------------------------------------------------------------
# Definición de categorías y keywords (sincronizado con jobCategories.js)
# ---------------------------------------------------------------------------

CATEGORIES: list[tuple[str, list[str]]] = [
    # ── A: Edición & Localización ────────────────────────────────────────────
    ("A", [
        "content editor", "copy editor", "proofreader", "proofreading",
        "localization", "localisation", "lqa", "linguistic quality",
        "post-editor", "post-editing", "mtpe", "translator", "translation",
        "editorial", "copywriter", "copywriting", "subtitling", "transcription",
        "content reviewer", "localization tester", "language reviewer",
        "linguistic", "bilingual editor", "multilingual editor",
        "writing specialist", "freelance writer", "assistant editor",
        " editor", "copy writer", "language specialist",
        "übersetzer", "übersetzerin", "redakteur", "redakteurin",
        "lektor", "lektorin", "korrektor", "korrektorin",
        "lokalisierung", "sprachqualität", "sprachredaktion",
    ]),
    # ── B: IA & Evaluación ───────────────────────────────────────────────────
    ("B", [
        "rlhf", "ai evaluator", "ai trainer", "content evaluator",
        "data annotator", "data annotation", "search quality rater",
        "quality rater", "ai content", "prompt engineer",
        "machine translation", "llm evaluator", "ai reviewer",
        "data labeler", "data labelling", "ai quality", "nlp annotator",
        "human feedback", "rater", "outlier ai", "scale ai",
        "telus international", "lionbridge", "appen ",
        "personalized ads evaluator", "ads evaluator", "ads quality",
        "online data analyst", "search evaluator", "web search evaluator",
    ]),
    # ── C: Administración & VA ───────────────────────────────────────────────
    ("C", [
        "virtual assistant", "executive assistant", "administrative",
        "operations coordinator", "project coordinator", "office manager",
        "event coordinator", "remote assistant", "admin coordinator",
        "administrative coordinator", "secretar", "office administrator",
        "personal assistant", "operations assistant", "office support",
        "project assistant", "programme manager", "program manager",
        "front desk", "reception", "empfangsmitarbeiter",
        "sachbearbeiter", "sachbearbeiterin", "büromanager", "büroassistenz",
        "assistent", "assistentin", "projektkoordinator", "projektkoordinatorin",
        "verwaltung", "administration", "sekretär", "sekretärin",
        "kaufmännisch", "kaufmännische", "empfang",
        "assistant administratif", "assistante administrative",
        "coordinatrice", "coordinateur",
    ]),
    # ── D: RRHH & Formación ──────────────────────────────────────────────────
    ("D", [
        "hr coordinator", "hr administrator", "hr officer", "hr manager",
        "hr generalist", "hr specialist", "human resources",
        "instructional designer", "instructional design", "elearning",
        "e-learning", "l&d", "learning and development", "training coordinator",
        "onboarding", "people operations", "talent acquisition",
        "recruiting coordinator", "learning coordinator", "l&d specialist",
        "training specialist", "learning specialist", "people partner",
        "talent management", "corporate training", "workforce development",
        "people manager", "payroll specialist", "hr services",
        "entgeltabrechnung", "personalsachbearbeitung",
        "personalwesen", "personalabteilung", "personalreferent",
        "personalreferentin", "weiterbildung",
        "hr-koordinator", "personalkoordinator", "personalmanager",
    ]),
    # ── E: Customer Success ──────────────────────────────────────────────────
    ("E", [
        "customer success", "customer support", "customer service",
        "client relations", "vip client", "guest experience",
        "concierge", "customer experience", "account manager",
        "client success", "customer care", "customer operations",
        "client support", "support specialist", "help desk",
        "bilingual support", "service representative",
        "client solutions", "account executive",
        "kundendienst", "kundenbetreuung", "kundensupport",
        "kundenerfolg", "kundenberater", "kundenberaterin",
        "kundenkommunikation", "kundenservice",
    ]),
    # ── F: Organismos Internacionales ────────────────────────────────────────
    ("F", [
        "language assistant", "programme assistant", "program assistant",
        "documentation assistant", "unicef", "unesco", "undp", "unfccc",
        "ngo", "ngos", "international organisation", "international organization",
        "conference services", "humanitarian", "development organisation",
        "united nations", "international bureau", "wto", "wfp",
        "who ", "ilo ", "un ", "iaea", "unhcr", "ifrc",
        "public affairs", "eu policy", "european union",
        "international development", "global policy",
        "policy officer", "policy analyst", "policy advisor", "policy manager",
        "advocacy officer", "advocacy manager", "advocacy coordinator",
        "humanitarian affairs", "humanitarian coordinator",
        "eu project", "eu programme", "eu global",
        "international affairs", "foreign affairs",
        "development cooperation", "grants officer", "grant officer",
        "programme consultant", "visiting fellow", "fellowship",
        "impact manager", "monitoring and evaluation", "m&e officer",
        "mission secretariat", "secretary general", "press specialist",
        "press officer", "anti-racism", "diversity intergroup",
        "sub-saharan", "carbon policy", "climate policy", "energy policy",
        "health policy", "eu projects officer", "europe policy",
        "internationale organisation", "uno", "entwicklungshilfe",
        "politikberatung", "eu-politik",
    ]),
    # ── G: Contenido & Marketing ─────────────────────────────────────────────
    ("G", [
        "content writer", "content specialist", "content creator",
        "technical writer", "blog editor", "communications",
        "educational content", "content strategist", "content manager",
        "social media", "marketing coordinator", "internal communications",
        "content marketing", "seo writer", "creative writer",
        "communications assistant", "communications consultant",
        "communications officer", "media coordinator",
        "documentation specialist", "knowledge base",
        "marketing manager", "brand manager", "community manager",
        "online marketing", "e-commerce", "ecommerce", "influencer",
        "digital marketing", "senior creative", "creative manager",
        "press release", "newsletter", "copyeditor",
        "texter", "texterin", "kommunikation", "kommunikationsmanager",
        "kommunikationsbeauftragter", "marketingkoordinator",
        "marketingmanager", "zeitung", "redaktion", "journalist",
    ]),
    # ── H: Docencia & Educación — FUERA DEL PERFIL OBJETIVO ─────────────────
    ("H", [
        "teacher", "esl teacher", "efl teacher", "english teacher",
        "language teacher", "tefl", "tesol", "celta", "esol",
        "classroom teacher", "primary teacher", "secondary teacher",
        "school teacher", "subject teacher", "class teacher",
        "academic staff", "faculty", "lecturer", "tutor",
        "academic support", "learning support assistant", "learning support",
        "sen teacher", "special needs", "head of department", "head of year",
        "form tutor", "pastoral", "school coordinator", "school leader",
        "deputy head", "assistant head", "international school",
        "british curriculum", "ib teacher", "cambridge teacher",
        "professor", "adjunct", "dorm head", "boarding",
        "academic tutor", "eap teacher", "school counselor", "school counsellor",
        "education coordinator", "instructor",
        "lehrperson", "lehrerin", "lehrer", "lehrkraft",
        "klassenlehrer", "klassenlehrerin", "klassenlehrperson",
        "unterricht", "schulischer",
        "pädagog", "pädagogin", "sozialpädagog", "sozialpädagogin",
        "heilpädagog", "heilpädagogin", "sonderpädagog",
        "schulleiter", "schulleiterin", "schulführung", "schulbegleitung",
        "berufsschule", "gymnasium", "kantonsschule",
        "primarstufe", "sekundarstufe", "grundschule",
        "fachlehrperson", "stellvertretung", "lehrauftrag",
        "dozent", "dozentin", "professur",
        "betreuung", "hort", "tagesstruktur", "kita",
        "schulabsentismus", "jugendcoach", "nachtwache", "nachtdienst",
        "gruppenleitung", "agogisch", "arbeitsagog",
        "enseignant", "enseignante", "professeur",
    ]),
    # ── I: Ventas & Negocio — FUERA DEL PERFIL OBJETIVO ─────────────────────
    ("I", [
        "sales manager", "sales consultant", "sales specialist",
        "business development", "business consultant",
        "commercial manager", "presales", "client solutions manager",
        "senior sales", "sales representative", "inside sales",
        "field sales", "sales director", "head of sales",
        "verkaufsberater", "verkaufsleiter", "vertrieb",
        "aussendienstmitarbeiter", "verkäufer", "verkaufsmitarbeiter",
        "geschäftsberater", "unternehmensberater",
    ]),
    # ── J: Investigación & Análisis — FUERA DEL PERFIL OBJETIVO ─────────────
    ("J", [
        "research assistant", "research associate", "researcher",
        "policy adviser", "data analyst", "product analyst", "senior analyst",
        "business analyst", "systems analyst", "intelligence analyst",
        "financial analyst", "market analyst", "programme analyst",
        "research officer", "research coordinator",
        "monitoring evaluation", "impact assessment",
        "fachmitarbeiter recht", "rechtsberater", "analyst",
        "wirtschaftsanalytiker",
    ]),
    # ── K: Finanzas & Contabilidad — FUERA DEL PERFIL OBJETIVO ──────────────
    ("K", [
        "finance specialist", "finance manager", "finance assistant",
        "financial controller", "payroll", "accounting", "accountant",
        "budget analyst", "finance expert", "portfolio management",
        "grants management", "finance officer",
        "buchhalter", "buchführung", "finanzbuchhalter", "lohnbuchhalter",
        "controlling", "finanzkontrolle", "treuhand", "rechnungswesen",
        "steuerberater", "buchhaltung", "finanzwesen", "finanzberater",
        "wirtschaftsprüfer", "revisor", "entgeltabrechner",
    ]),
    # ── L: Gestión & Dirección — FUERA DEL PERFIL OBJETIVO ──────────────────
    ("L", [
        "operations manager", "associate director", "managing director",
        "chief of", "head of", "vice president", "general manager",
        "country manager", "regional manager", "programme director",
        "senior manager", "product manager", "project manager",
        "geschäftsführer", "geschäftsstellenleiter",
        "bereichsleiter", "abteilungsleiter", "abteilungsleitung",
        "leiterin abteilung", "leiter abteilung",
        "standortleitung", "werkstattleitung",
        "direktor", "direktorin", "führungskraft",
        "teamleiter", "teamleiterin",
    ]),
    # ── M: Servicios Sociales — FUERA DEL PERFIL OBJETIVO ───────────────────
    ("M", [
        "sozialarbeiter", "sozialarbeiterin", "sozialarbeit",
        "sozialhilfe", "berufsbeistand", "berufsbeistände",
        "integrationscoach", "schulische sozialarbeit",
        "leiter sozialhilfe", "sozialpädagogisch",
        "fachperson wohnen", "leitung wohnen",
        "durchgangsheim", "wohngruppe", "stationsleitung",
        "aktivierungsfachperson", "soziale einrichtung",
        "gemeinwesen", "jugendarbeit", "jugendwohngruppe",
    ]),
]

# ---------------------------------------------------------------------------
# Multiplicadores de score por categoría
# Aplicados al score_final ANTES del umbral de threshold.
# A–G: categorías objetivo → sin penalización
# H–M: fuera del perfil → penalización progresiva
# otros: sin categoría conocida (IT, ingeniería, medicina…) → penalización moderada
# ---------------------------------------------------------------------------

CATEGORY_MULTIPLIERS: dict[str, float] = {
    "A": 1.00,  # Edición & Localización — objetivo principal
    "B": 1.00,  # IA & Evaluación — objetivo principal
    "C": 1.00,  # Administración & VA — objetivo
    "D": 1.00,  # RRHH & Formación — objetivo
    "E": 1.00,  # Customer Success — objetivo secundario
    "F": 1.00,  # Organismos Internacionales — objetivo secundario
    "G": 1.00,  # Contenido & Marketing — objetivo
    "H": 0.15,  # Docencia — objetivo explícito ES SALIR de este sector
    "I": 0.20,  # Ventas — fuera de perfil
    "J": 0.25,  # Investigación — fuera de perfil
    "K": 0.20,  # Finanzas — fuera de perfil
    "L": 0.30,  # Gestión — parcialmente solapado con RRHH/Admin, pero fuera de scope
    "M": 0.10,  # Servicios Sociales — completamente fuera de perfil
    "otros": 0.55,  # Sin categoría — IT, ingeniería, medicina, logística, etc.
}


# Pre-compilar un patrón por keyword usando word boundaries (\b).
# Esto evita falsos positivos como "ngo" matchando "django" o
# "uno" matchando en el interior de otra palabra.
# Los keywords con espacios (frases) usan boundary solo al inicio/fin de la frase.
_PUNCT_RE = _re.compile(r'[^\w\s]')
_COMPILED: list[tuple[str, list[_re.Pattern]]] = [
    (
        cat_id,
        [
            _re.compile(
                r'\b' + _re.escape(kw.strip()) + r'\b',
                _re.IGNORECASE,
            )
            for kw in keywords
            if kw.strip()
        ],
    )
    for cat_id, keywords in CATEGORIES
]


def classify_job(title: str, tags: list[str]) -> str:
    """Clasifica un job en una de las 13 categorías o devuelve 'otros'.

    Usa word-boundary matching (\\b) para evitar falsos positivos por substring
    (ej: "ngo" NO debe hacer match en "django"; "un " NO en "running").

    Orden A→M: primera coincidencia gana — las categorías objetivo tienen prioridad.

    Args:
        title: Título original del job (puede estar en DE/FR/IT/EN).
        tags: Lista de tags del job.

    Returns:
        ID de categoría ("A"–"M") o "otros".
    """
    combined = title + " " + " ".join(tags or [])
    # Normalizar puntuación a espacios para que \b funcione correctamente
    text = _PUNCT_RE.sub(" ", combined)

    for cat_id, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            return cat_id
    return "otros"
