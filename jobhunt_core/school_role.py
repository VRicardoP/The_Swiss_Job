"""Keyword-based role classifier for school job postings.

Decides whether a detected job is the kind of IT/management role we want to be
alerted about, or a teaching role that should be filtered out.

Returns a float `role_score` in [-1.0, +1.0]:
    +1.0  full match (an IT/management term and no excluded teaching term)
    +0.5  partial match (a generic technology term without explicit role)
     0.0  irrelevant (no signal either way)
    -1.0  excluded (matches a teaching/professor term)

The exclusion list takes precedence: if a job title contains "IT teacher",
even though it also contains "IT", the role_score is -1.0.

Pure function, no I/O. Easy to unit-test.
"""

from __future__ import annotations

import re
import unicodedata


# Strong-match terms — clearly an IT/management role we want
_MATCH_TERMS: tuple[str, ...] = (
    "it manager",
    "it director",
    "head of it",
    "head of technology",
    "technology coordinator",
    "technology manager",
    "systems administrator",
    "system administrator",
    "sysadmin",
    "network administrator",
    "network manager",
    "infrastructure manager",
    "infrastructure engineer",
    "edtech",
    "ed-tech",
    "ict coordinator",
    "ict manager",
    "digital learning coordinator",
    "digital learning manager",
    "digital transformation",
    "cto",
    "chief information officer",
    "cio",
    "devops",
    "site reliability",
    "data engineer",
    "data analyst",
    # --- German (DE) — most Swiss schools post in German ---
    "informatiker",
    "informatikerin",
    "fachinformatiker",
    "wirtschaftsinformatiker",
    "systemadministrator",
    "systemadministratorin",
    "systemtechniker",
    "systemingenieur",
    "netzwerkadministrator",
    "netzwerktechniker",
    "it-leiter",
    "it-leiterin",
    "it-verantwortlicher",
    "it-verantwortliche",
    "ict-verantwortlicher",
    "ict-verantwortliche",
    "informatik-verantwortliche",
    "informatik-verantwortlicher",
    "it-administrator",
    "ict-administrator",
    "ict-supporter",
    "mediamatiker",
    # --- French (FR) — Romandie schools (Geneva, Verbier, Vaud) ---
    "informaticien",
    "informaticienne",
    "responsable informatique",
    "responsable it",
    "administrateur systeme",
    "administrateur systemes",
    "administrateur reseau",
    "administrateur reseaux",
    "technicien informatique",
    "technicien systeme",
    "ingenieur systeme",
    # --- Italian (IT) — Ticino schools ---
    "informatico",
    "responsabile informatico",
    "responsabile it",
    "amministratore di sistema",
    "amministratore di rete",
    "tecnico informatico",
    "sistemista",
)

# Exclude — teaching roles that happen to contain IT/tech keywords
_EXCLUDE_TERMS: tuple[str, ...] = (
    "it teacher",
    "ict teacher",
    "computer science teacher",
    "computer studies teacher",
    "informatics teacher",
    "computing teacher",
    "technology teacher",
    "professor",
    "lecturer",
    "assistant teacher",
    "teaching assistant",
    "head of computing department",  # ambiguous, lean to exclude (curriculum lead)
    # --- German teaching roles (exclusion wins over the IT match terms) ---
    "informatiklehrer",
    "informatiklehrerin",
    "informatiklehrperson",
    "lehrperson informatik",
    "fachlehrer informatik",
    "ict-lehrer",
    # --- French teaching roles ---
    "enseignant informatique",
    "enseignante informatique",
    "enseignant en informatique",
    "enseignante en informatique",
    "professeur informatique",
    "professeur d informatique",
    "maitre d informatique",
    # --- Italian teaching roles ---
    "insegnante di informatica",
    "docente di informatica",
    "professore di informatica",
)

# Partial — generic terms that hint at tech without committing to a role
_PARTIAL_TERMS: tuple[str, ...] = (
    "technology",
    "digital",
    "ict",
    "it support",
    "helpdesk",
    "help desk",
    "technical support",
    "support engineer",
    # --- German ---
    "informatik",
    "informatikdienst",
    "digitalisierung",
    # --- French ---
    "informatique",
    "numerique",
    "support informatique",
    # --- Italian --- (note: "informatico" is a strong-match term, so a title like
    # "supporto informatico" already scores +1.0 and never reaches here)
    "informatica",
)


SCORE_MATCH = 1.0
SCORE_EXCLUDE = -1.0
SCORE_PARTIAL = 0.5
SCORE_IRRELEVANT = 0.0


def _normalize(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace for matching.

    Accent-stripping (é->e, ü->u, ò->o) lets FR/DE/IT terms match regardless of
    how a posting spells them (système/systeme, informático/informatico). All
    keyword lists are written in accent-free lowercase to match this.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    # French elision: treat apostrophes (straight/curly) as spaces so
    # "d'informatique" matches a "d informatique" term.
    unelided = re.sub(r"['’]", " ", no_accents)
    return re.sub(r"\s+", " ", unelided.lower()).strip()


def _compile_terms(terms: tuple[str, ...]) -> re.Pattern:
    """Compile a term list into one whole-token alternation regex (once, at import).

    Word boundaries stop short acronyms matching inside words ('cto' in
    'director'); re.escape keeps metachars literal (e.g. 'ed-tech'). Equivalent to
    the old per-term loop, but compiled once instead of on every classify_role call.
    """
    alternation = "|".join(re.escape(t) for t in terms)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")


_EXCLUDE_RE = _compile_terms(_EXCLUDE_TERMS)
_MATCH_RE = _compile_terms(_MATCH_TERMS)
_PARTIAL_RE = _compile_terms(_PARTIAL_TERMS)


def classify_role(title: str, description: str = "") -> float:
    """Score a job postint by relevance.

    Args:
        title: Job title (e.g. "IT Manager").
        description: Optional full description; widens the haystack.

    Returns:
        A float role_score in [-1.0, +1.0] — see module docstring.
    """
    haystack = _normalize(f"{title} {description}")

    if not haystack:
        return SCORE_IRRELEVANT

    # El TITULO manda: un "IT Manager" legitimo no debe descartarse porque la
    # descripcion mencione terminos docentes ("support our teachers..."), pero
    # un titulo docente con "IT" dentro sigue siendo docencia (exclusion sobre
    # el propio titulo primero).
    title_hay = _normalize(title)
    if title_hay and _EXCLUDE_RE.search(title_hay):
        return SCORE_EXCLUDE
    if title_hay and _MATCH_RE.search(title_hay):
        return SCORE_MATCH

    # Exclusion wins — a teaching role with "IT" in the title is still teaching
    if _EXCLUDE_RE.search(haystack):
        return SCORE_EXCLUDE

    if _MATCH_RE.search(haystack):
        return SCORE_MATCH

    if _PARTIAL_RE.search(haystack):
        return SCORE_PARTIAL

    return SCORE_IRRELEVANT
