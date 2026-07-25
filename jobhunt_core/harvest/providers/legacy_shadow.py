"""Handler genérico de fuentes sombra `legacy:<source>` (B-02, CONTRATOS_B §3).

El registry de identidad/normalización del core es EXACT-MATCH (sin
comodines): el proyector da de alta AQUÍ, en caliente y por cada fuente
`legacy:*` observada, el extractor y el normalizador genéricos. No hay
BaseProvider: estas fuentes no se cosechan (el runner jamás las toca — su
scope sombra nace deshabilitado); solo existen para que el sink/canónica
resuelvan identidad y contenido con el flujo NORMAL de A-04..A-06.

El payload contractual del proyector (§3) ya viene con las claves del core
(`company_name`, no `company`): el extractor/normalizador solo ESCOGEN campos
— la coerción de tipos es la central de normalize.py (misma disciplina que
arbeitnow: title/company crudos, lo no-string degrada, jamás revienta).
"""

import logging

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer

logger = logging.getLogger(__name__)

LEGACY_PREFIX = "legacy:"

# Fuentes ya registradas EN ESTE PROCESO (el registry es memoria del worker:
# cada arranque re-registra al observar — idempotente y barato).
_registered: set[str] = set()


def _extract(payload: dict) -> tuple:
    """(title, company) crudos del payload contractual de §3."""
    return (payload.get("title"), payload.get("company_name"))


def _normalize(raw: dict) -> dict:
    """Escoge los campos canónicos del payload de §3; `salary` es el texto
    original del legacy (salary_original) — los importes numéricos viajan en
    el raw y participan del content_hash, no del texto embebible (ADR-02)."""
    return {
        "title": raw.get("title"),
        "company": raw.get("company_name"),
        "description": raw.get("description"),
        "tags": raw.get("tags"),
        "location": raw.get("location"),
        "remote": raw.get("remote"),
        "salary": raw.get("salary_original"),
    }


def ensure_registered(source_name: str) -> None:
    """Alta idempotente del handler exact-match para UNA fuente `legacy:*`.

    El proyector la invoca al observar cada fuente (nueva o conocida) y
    también al arrancar para todas las fuentes `legacy:*` ya persistidas:
    la reconstrucción de canónica tras un cierre puede necesitar el
    normalizador de una fuente que este lote no trae.
    """
    if source_name in _registered:
        return
    if not source_name.startswith(LEGACY_PREFIX):
        raise ValueError(
            f"fuente {source_name!r} fuera del namespace {LEGACY_PREFIX!r}: "
            "este handler es EXCLUSIVO de la sombra"
        )
    register_extractor(source_name, _extract)
    register_normalizer(source_name, _normalize)
    _registered.add(source_name)
    logger.info("legacy_shadow: handler registrado para %r", source_name)
