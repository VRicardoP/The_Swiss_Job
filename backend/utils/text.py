"""Lo único que el BFF necesita de texto: quitar el HTML de una descripción.

DT-148 B2 (2026-10-21): este fichero tenía 264 LOC con `JOB_TAGS` (104
entradas), `SWISS_CANTONS` (100), `extract_job_skills` y `extract_canton`, y
NADIE fuera de sus propios tests los llamaba — se fueron con el runtime de
matching local (`42ecf96`). Las dos funciones de texto eran además copia byte
a byte de `jobhunt_core/harvest/providers/rss_text.py`, que es donde viven hoy
para la cosecha. No se importa desde el core a propósito: la frontera BFF↔core
es HTTP, no imports (plan §21).
"""

import re


def strip_html_tags(text: str) -> str:
    """Quita las etiquetas HTML y normaliza los espacios."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()
