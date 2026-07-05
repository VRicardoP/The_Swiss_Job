"""Test de caracterización para MatchService._priority_watchlist.

Filtro puro (sin DB/Redis): selecciona jobs de watchlist de colegios cuyo
score_final + urgency_score alcanza WATCHLIST_PUSH_THRESHOLD (70.0 por defecto).
Extraído de _notify_watchlist_priority para hacerlo testeable de forma aislada.
"""

from types import SimpleNamespace

from config import settings
from services.match_service import MatchService


def _result(source: str, score_final: float, urgency: float = 0.0) -> dict:
    return {
        "job": SimpleNamespace(source=source, title="t", company="c", hash="h"),
        "score_final": score_final,
        "urgency_score": urgency,
    }


def test_selects_school_jobs_above_threshold():
    thr = settings.WATCHLIST_PUSH_THRESHOLD
    results = [
        _result("swiss_schools_zh", thr),  # justo en el umbral → incluido
        _result("swiss_schools_be", thr - 0.1),  # por debajo → excluido
        _result("swiss_schools_vd", thr - 20, urgency=25),  # suma supera → incluido
    ]
    priority = MatchService._priority_watchlist(results)
    scores = {r["score_final"] for r in priority}
    assert len(priority) == 2
    assert (thr - 0.1) not in scores


def test_ignores_non_watchlist_sources():
    thr = settings.WATCHLIST_PUSH_THRESHOLD
    results = [
        _result("jobs_admin", thr + 50),  # no es swiss_schools_* → excluido
        _result("swiss_schools_ge", thr + 50),  # incluido
    ]
    priority = MatchService._priority_watchlist(results)
    assert len(priority) == 1
    assert priority[0]["job"].source == "swiss_schools_ge"


def test_missing_urgency_defaults_to_zero():
    thr = settings.WATCHLIST_PUSH_THRESHOLD
    r = {
        "job": SimpleNamespace(
            source="swiss_schools_lu", title="t", company="c", hash="h"
        ),
        "score_final": thr - 1,  # sin urgency_score → no alcanza el umbral
    }
    assert MatchService._priority_watchlist([r]) == []
