"""UrgencyScorer — calcula un boost de urgencia para un job watchlist.

Inputs: Job + (opcional) WatchedSchool metadata + descripción.
Output: int 0–100. Se suma al score_final por separado para no contaminar
la base del matching, pero se usa para ordenar y disparar notificaciones.

Componentes (suma directa, capada a 100):
+30  Colegio del grupo A (alta prioridad / alto volumen)
+15  Colegio del grupo B
+0   Colegio del grupo C
+15  Job de la watchlist publicado en las últimas 48 h
+10  Job publicado en los últimos 7 días
+20  Descripción contiene "immediate" / "as soon as possible" / "urgent"
+10  Deadline explícito en < 7 días
-10  Policy "portal_only" sin opción de candidatura directa
"""

import re
from datetime import datetime, timezone

from scrapers.swiss_schools_config import (
    WatchedSchool,
    resolve_school_from_job,
)

_URGENT_KEYWORDS = re.compile(
    r"\b(immediate|immediately|as soon as possible|asap|urgent|"
    r"sofort|umgehend|dès que possible|au plus vite)\b",
    re.IGNORECASE,
)

# Detecta "deadline: <date>", "apply by <date>", "closing date <date>"
_DEADLINE_RE = re.compile(
    r"(?:deadline|apply by|closing date|closes on|frist|"
    r"date limite|terme)[^\d]{0,15}"
    r"(\d{1,2}[./ -]\d{1,2}[./ -]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def compute_urgency_score(
    job,
    *,
    school: WatchedSchool | None = None,
    description: str | None = None,
) -> int:
    """Devuelve un score 0–100 de urgencia para este job.

    Si el job no pertenece a la watchlist (school=None), devuelve 0 —
    el urgency boost es exclusivo de la watchlist por diseño.
    """
    if school is None:
        school = resolve_school_from_job(job)
    if school is None:
        return 0

    score = 0

    if school.group_tier == "A":
        score += 30
    elif school.group_tier == "B":
        score += 15

    # Recency (usa first_seen si está, si no created_at del job)
    first_seen = getattr(job, "first_seen_at", None) or getattr(job, "created_at", None)
    if first_seen:
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - first_seen).days
        if days_old < 2:
            score += 15
        elif days_old < 7:
            score += 10

    text = description or ""
    if _URGENT_KEYWORDS.search(text):
        score += 20

    if _DEADLINE_RE.search(text):
        score += 10

    if school.policy == "portal_only":
        score -= 10

    return max(0, min(100, score))


