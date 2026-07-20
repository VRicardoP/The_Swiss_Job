"""Filtrado de skills "missing" falsas por sinonimia/equivalencia.

El re-ranking LLM y el overlap por reglas suelen marcar como "missing" skills que
el candidato SÍ tiene bajo otro nombre (p. ej. el candidato pone "copywriting" y
la oferta pide "content writer"). Este módulo resuelve esas equivalencias para no
penalizar ni confundir con huecos inexistentes.

Diseñado para el perfil objetivo de SwissJobHunter (contenido/localización, HR,
asistencia/administración, evaluación de IA, idiomas), no como taxonomía general.
"""

# Canónico -> variantes equivalentes (todo en minúsculas). Añadir variantes aquí
# es la única extensión necesaria; el mapa inverso se deriva automáticamente.
SKILL_SYNONYMS: dict[str, set[str]] = {
    "content writing": {
        "content writer",
        "content creation",
        "content creator",
        "copywriting",
        "copywriter",
        "redacción de contenidos",
        "redactor",
    },
    "localization": {
        "localisation",
        "l10n",
        "localization specialist",
        "lqa",
        "linguistic qa",
        "mtpe",
        "post-editing",
        "machine translation post-editing",
    },
    "translation": {
        "translator",
        "traducción",
        "traductor",
        "übersetzung",
        "traduction",
    },
    "seo": {"search engine optimization", "search engine optimisation", "seo/sem"},
    "project management": {
        "project manager",
        "pm",
        "gestión de proyectos",
        "projektleitung",
        "projektleiter",
    },
    "customer success": {
        "customer success manager",
        "csm",
        "customer support",
        "client success",
    },
    "human resources": {
        "hr",
        "recursos humanos",
        "people operations",
        "talent acquisition",
        "recruiting",
        "recruitment",
    },
    "virtual assistant": {
        "va",
        "administrative assistant",
        "admin assistant",
        "executive assistant",
        "asistente virtual",
    },
    "microsoft office": {
        "ms office",
        "office 365",
        "microsoft 365",
        "word",
        "excel",
        "powerpoint",
        "outlook",
    },
    "ai evaluation": {
        "rlhf",
        "ai training",
        "data annotation",
        "model evaluation",
        "prompt engineering",
        "ai evaluator",
    },
    "e-learning": {"elearning", "edtech", "instructional design", "moodle", "lms"},
    "social media": {"social media management", "community management", "smm"},
    "crm": {"salesforce", "hubspot", "zendesk", "customer relationship management"},
    "english": {"inglés", "englisch", "anglais", "native english"},
    "german": {"deutsch", "alemán", "allemand"},
    "french": {"français", "francés", "französisch"},
    "spanish": {"español", "castellano", "spanisch", "espagnol"},
    "italian": {"italiano", "italienisch", "italien"},
}

# Mapa inverso variante->canónico (incluye el propio canónico como variante).
_CANONICAL_BY_VARIANT: dict[str, str] = {}
for _canon, _variants in SKILL_SYNONYMS.items():
    _CANONICAL_BY_VARIANT[_canon] = _canon
    for _v in _variants:
        _CANONICAL_BY_VARIANT[_v] = _canon


def _norm(skill: str) -> str:
    return (skill or "").strip().lower()


def _canonical(skill: str) -> str | None:
    """Canónico de una skill si se conoce; None si no está en el mapa."""
    return _CANONICAL_BY_VARIANT.get(_norm(skill))


def filter_missing_skills(
    candidate_skills: list[str], missing_skills: list[str]
) -> list[str]:
    """Elimina de `missing_skills` las que el candidato ya tiene (directa o por sinónimo).

    Preserva el orden y el casing originales de las que sí faltan de verdad.
    Case-insensitive; una skill desconocida (fuera del mapa) solo se filtra si
    coincide literalmente con una del candidato.
    """
    if not missing_skills:
        return list(missing_skills or [])

    have_norm = {_norm(s) for s in candidate_skills if s}
    have_canon = {c for c in (_canonical(s) for s in have_norm) if c}

    out: list[str] = []
    for m in missing_skills:
        mn = _norm(m)
        if not mn:
            continue
        if mn in have_norm:
            continue  # duplicado exacto
        mc = _canonical(mn)
        if mc and (mc in have_canon or mc in have_norm):
            continue  # el candidato tiene una variante equivalente
        out.append(m)
    return out
