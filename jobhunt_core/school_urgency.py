"""Urgency scorer for school job postings.

Composes a 0-100 score from school metadata + job text signals. Used by
`school_alert.py` to decide push vs digest vs silent (thresholds 70/40).

Pure function, no I/O, no DB queries. The school metadata is passed in as a
plain dict so this stays trivially testable.

In Phase 1 the function ignores the academic calendar (Jan-Apr peak hiring
season etc.) — that lives in `school_scraper` Phase 2 alongside the scheduler.

Scoring rubric:
    +30  school in Group A
    +20  policy 'direct_email_ok'   (the doc's "no guarda candidaturas" — act fast)
    +20  job text contains "immediate" or "as soon as possible"
    +10  deadline in <= 7 days from detection
    -20  school in Group C
     -5  per group_tier B (mild damping vs A)

The score is then clamped to [0, 100].
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional, TypedDict


SCORE_GROUP_A = 30
SCORE_GROUP_B_PENALTY = -5
SCORE_GROUP_C_PENALTY = -20
SCORE_DIRECT_EMAIL_OK = 20
SCORE_IMMEDIATE_LANGUAGE = 20
SCORE_TIGHT_DEADLINE = 10

TIGHT_DEADLINE_DAYS = 7

_IMMEDIATE_PATTERN = re.compile(
    r"\b(immediate(ly)?|as soon as possible|asap|start[ _-]?date.{0,30}?next month)\b",
    re.IGNORECASE,
)


class SchoolMeta(TypedDict, total=False):
    """Subset of `schools` row fields used by the scorer."""

    group_tier: str  # "A" | "B" | "C"
    policy: str  # "direct_email_ok" | "direct_email" | "portal_only" | ...


def score_urgency(
    school: SchoolMeta,
    job_text: str = "",
    deadline: Optional[date] = None,
    today: Optional[date] = None,
) -> int:
    """Compose urgency score in [0, 100].

    Args:
        school: dict with at least 'group_tier' and 'policy' keys.
        job_text: title + description concatenated, used for language signals.
        deadline: explicit job deadline if known.
        today: injected for testability (defaults to UTC today).

    Returns:
        Integer score clamped to [0, 100].
    """
    today = today or date.today()
    score = 0

    tier = school.get("group_tier")
    if tier == "A":
        score += SCORE_GROUP_A
    elif tier == "B":
        score += SCORE_GROUP_B_PENALTY
    elif tier == "C":
        score += SCORE_GROUP_C_PENALTY

    if school.get("policy") == "direct_email_ok":
        score += SCORE_DIRECT_EMAIL_OK

    if job_text and _IMMEDIATE_PATTERN.search(job_text):
        score += SCORE_IMMEDIATE_LANGUAGE

    if deadline is not None:
        days_left = (deadline - today).days
        if 0 <= days_left <= TIGHT_DEADLINE_DAYS:
            score += SCORE_TIGHT_DEADLINE

    return max(0, min(100, score))
