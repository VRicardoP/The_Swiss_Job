"""Identidad determinista (A-05): tokens PF.5 + registro de extractores.

ADR-01 (re-enlace por niveles) — Fase A implementa SOLO lo determinista:
- Guard de reciclado (nivel exacto): empresa por TOKENS distinta → reciclar
  (cierra la incarnación y abre otra con vacante NUEVA). El coseno
  < SIM_RECYCLE requiere embeddings (A-06+) y queda diferido con el nivel 3
  semántico a Fase B: aquí JAMÁS se funde ni recicla por semántica.
- Cross-source fuerte por `url_normalized` (índice core0003) → attach a la
  vacante existente + `link_evidence`.
- Medio → `dedup_candidates` (pending); la RESOLUCIÓN es de Fase B.

La normalización de tokens es el PF.5 del legacy (commit 54aadb4) PORTADO:
fronteras estrictas — el core no importa del legacy.
"""

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

# Confianzas/similitudes REGISTRADAS (valores de evidencia, no umbrales de
# decisión — los umbrales SIM_* de decisión son config de Fase B).
CONF_URL_ATTACH = 1.0  # url_normalized idéntica y vigente en otra fuente
CONF_URL_ALIAS = 0.6  # conflicto external_id↔URL: gana external_id (ADR-01)
SIM_URL_DRIFT = 0.9  # misma URL vigente en 2 fuentes con vacantes DISTINTAS
SIM_FUZZY_BATCH = 0.85  # misma identidad difusa dentro del mismo lote

# --- Normalización PF.5 (portada de backend/services/deduplicator.py) ---

# Sufijos legales que se quitan del nombre de empresa.
COMPANY_SUFFIXES: set[str] = {
    "ag", "gmbh", "sa", "sarl", "sàrl", "ltd", "inc", "corp", "se", "plc",
    "srl", "co", "llc", "pty", "bv", "nv",
}

# Seniority filtrada POR TOKEN exacto (nunca substring: international, leader).
SENIORITY_TOKENS: set[str] = {
    "senior", "junior", "lead", "head", "intern", "trainee", "sr", "jr",
}

# Marcadores de diversidad/género: pares reales enumerados (m/f, m/w, h/f...)
# + 3.er marcador opcional (/d, /x) — para NO confundir H/W, R&D o C/C++.
_DIVERSITY_RE = re.compile(
    r"\(?\b(?:m\s*/\s*f|f\s*/\s*m|m\s*/\s*w|w\s*/\s*m|h\s*/\s*f|f\s*/\s*h)"
    r"(?:\s*/\s*[dx])?\b\)?"
    r"|\(\s*all\s+genders?\s*\)"
    r"|\(\s*(?:divers|gn)\s*\)",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Título → tokens: minúsculas, sin género/diversidad, sin puntuación,
    sin seniority (por token), espacios colapsados."""
    t = title.lower().strip()
    t = _DIVERSITY_RE.sub(" ", t)  # antes de la puntuación (la llevan)
    t = _PUNCT_RE.sub(" ", t)
    tokens = [w for w in t.split() if w not in SENIORITY_TOKENS]
    return _SPACES_RE.sub(" ", " ".join(tokens)).strip()


def normalize_company(company: str) -> str:
    """Empresa → tokens: minúsculas, sin puntuación, sin sufijos legales."""
    c = company.lower().strip()
    c = _PUNCT_RE.sub(" ", c)
    words = [w for w in c.split() if w not in COMPANY_SUFFIXES]
    return _SPACES_RE.sub(" ", " ".join(words)).strip()


def fuzzy_key(title: str | None, company: str | None) -> str | None:
    """Clave difusa 'título|empresa'; None si falta CUALQUIERA de las dos
    normalizadas — sin identidad completa no hay candidato (conservador)."""
    nt = normalize_title(title or "")
    nc = normalize_company(company or "")
    if not nt or not nc:
        return None
    return f"{nt}|{nc}"


def should_recycle(
    old_identity: tuple[str | None, str | None],
    new_identity: tuple[str | None, str | None],
) -> bool:
    """Guard de reciclado determinista (ADR-01 nivel exacto): SOLO cuando
    AMBAS empresas existen y sus tokens difieren. Falta de datos →
    conservador: nueva revisión en la misma incarnación (no corromper)."""
    old_company = normalize_company(old_identity[1] or "")
    new_company = normalize_company(new_identity[1] or "")
    return bool(old_company and new_company and old_company != new_company)


# --- Extractores de identidad por fuente ---

# (title, company) CRUDOS desde el payload raw de ESA fuente. Registro por
# NOMBRE de fuente: permite extraer también del raw HISTÓRICO (guard de
# reciclado) sin acoplar el sink al objeto provider.
_EXTRACTORS: dict[str, Callable[[dict], tuple[str | None, str | None]]] = {}


def register_extractor(
    source_name: str, fn: Callable[[dict], tuple[str | None, str | None]]
) -> None:
    _EXTRACTORS[source_name] = fn


def extract_identity(source_name: str, payload: dict) -> tuple[str | None, str | None]:
    """(title, company) o (None, None) si no hay extractor o el payload no
    encaja — la identidad ausente degrada a conservador, JAMÁS rompe el lote."""
    fn = _EXTRACTORS.get(source_name)
    if fn is None:
        return (None, None)
    try:
        title, company = fn(payload)
        # Auditoría A-05 #2: un title/company NO-string del feed (número, bool,
        # lista, objeto) reventaría .lower() FUERA de este try y abortaría el
        # lote entero en bucle. No-string = sin identidad (conservador).
        return (_as_text(title), _as_text(company))
    except Exception:
        logger.warning("identity: extractor de %r falló con un payload", source_name)
        return (None, None)


def _as_text(value) -> str | None:
    return value if isinstance(value, str) else None
