"""Normalización canónica de ofertas (A-06, ADR-01/02).

Del raw de la fuente sale el CONTENIDO CANÓNICO de la oferta (offer_revisions.
content): title/company/description/tags/salary/location/remote. Registro de
normalizadores por NOMBRE de fuente (mismo patrón que identity): el fn de la
fuente solo ESCOGE campos del payload; la coerción de tipos es central y
defensiva (lección A-05 #2: un feed puede traer cualquier tipo — jamás debe
romper el lote; lo no-string degrada, nunca revienta).

`text_hash` = hash SOLO de title+company+description+tags (ADR-02): cambiar
salario/location NO altera el text_hash → NO re-embebe.
"""

import hashlib
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Campos del contenido canónico (DTO §2: title/company/description/salary/tags).
CONTENT_FIELDS = ("title", "company", "description", "tags", "salary", "location", "remote")
# Campos que definen el TEXTO embebible (ADR-02): salario/location fuera.
TEXT_FIELDS = ("title", "company", "description", "tags")

_NORMALIZERS: dict[str, Callable[[dict], dict]] = {}


def register_normalizer(source_name: str, fn: Callable[[dict], dict]) -> None:
    _NORMALIZERS[source_name] = fn


def normalize_offer(source_name: str, raw: dict) -> dict | None:
    """Contenido canónico o None (sin normalizador, payload imposible o sin
    título) — sin oferta canónica NO se mueve el puntero, jamás se rompe el
    lote."""
    fn = _NORMALIZERS.get(source_name)
    if fn is None:
        logger.warning("normalize: fuente %r sin normalizador registrado", source_name)
        return None
    try:
        picked = fn(raw)
    except Exception:
        logger.warning("normalize: normalizador de %r falló con un payload", source_name)
        return None
    content = {
        "title": _text(picked.get("title")),
        "company": _text(picked.get("company")),
        "description": _text(picked.get("description")),
        "tags": _tags(picked.get("tags")),
        "salary": _text(picked.get("salary")),
        "location": _text(picked.get("location")),
        "remote": picked.get("remote") if isinstance(picked.get("remote"), bool) else None,
    }
    if not content["title"]:
        # Sin título no hay oferta presentable (DTO §2): se salta con log.
        logger.warning("normalize: %r sin título tras normalizar — sin revisión canónica", source_name)
        return None
    return content


def build_offer_text(content: dict) -> str:
    """Texto de embedding — MISMA composición que el legacy (build_job_text:
    title company description tags) para que los vectores sean comparables en
    la sombra de Fase B."""
    parts = [
        content.get("title") or "",
        content.get("company") or "",
        content.get("description") or "",
        " ".join(content.get("tags") or []),
    ]
    return " ".join(p for p in parts if p)


def offer_content_hash(content: dict) -> str:
    """Hash del CONTENIDO CANÓNICO normalizado (rev. A-06 2ª #2): identifica
    el RESULTADO del normalizador, no el raw de la fuente — el hash del raw
    vive en source_listing_revisions. Dos fuentes con el mismo raw pero
    normalizadores distintos producen canónicas DISTINTAS; la misma canónica
    desde raws distintos se reutiliza."""
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def offer_text_hash(content: dict) -> str:
    """Hash del texto embebible EXACTO (rev. A-06 #5): text_hash y el input
    del encoder derivan de la MISMA representación (`build_offer_text`) —
    mismo texto ⇒ mismo hash ⇒ UN embedding, estrictamente. Salario/location
    no participan (TEXT_FIELDS, ADR-02): cambiarlos da OTRO content_hash con
    el MISMO text_hash → no re-embebe."""
    return hashlib.sha256(build_offer_text(content).encode()).hexdigest()


def _text(value) -> str | None:
    """Coerción defensiva: solo str no vacío; el resto degrada a None."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _tags(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [t.strip() for t in value if isinstance(t, str) and t.strip()]
