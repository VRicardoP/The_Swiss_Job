"""Tests de caracterización para compute_urgency_score.

Fijan el comportamiento ACTUAL (suma de componentes capada a 0-100) para poder
refactorizar la función sin alterarlo.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scrapers.swiss_schools_config import WatchedSchool
from services.urgency_scorer import compute_urgency_score


def _school(group_tier: str = "B", policy: str = "manual") -> WatchedSchool:
    return WatchedSchool(
        id="test_school",
        name="Test School",
        city="Zurich",
        careers_url="https://example.ch/careers",
        strategy="css_selector",
        group_tier=group_tier,
        policy=policy,
    )


def _job(days_old: int | None = None):
    first_seen = (
        datetime.now(timezone.utc) - timedelta(days=days_old)
        if days_old is not None
        else None
    )
    return SimpleNamespace(first_seen_at=first_seen, created_at=None, tags=[])


class TestUrgencyScore:
    def test_not_watchlist_returns_zero(self):
        assert compute_urgency_score(_job(), school=None) == 0

    def test_tier_a_base(self):
        assert compute_urgency_score(_job(), school=_school("A")) == 30

    def test_tier_b_base(self):
        assert compute_urgency_score(_job(), school=_school("B")) == 15

    def test_tier_c_base(self):
        assert compute_urgency_score(_job(), school=_school("C")) == 0

    def test_recency_fresh_under_48h(self):
        assert compute_urgency_score(_job(days_old=0), school=_school("C")) == 15

    def test_recency_within_week(self):
        assert compute_urgency_score(_job(days_old=3), school=_school("C")) == 10

    def test_recency_old_no_boost(self):
        assert compute_urgency_score(_job(days_old=10), school=_school("C")) == 0

    def test_urgent_keyword(self):
        assert (
            compute_urgency_score(_job(), school=_school("C"), description="Start ASAP")
            == 20
        )

    def test_deadline_detected(self):
        assert (
            compute_urgency_score(
                _job(), school=_school("C"), description="apply by 01/12/2027"
            )
            == 10
        )

    def test_portal_only_penalty_clamped_to_zero(self):
        assert compute_urgency_score(_job(), school=_school("C", "portal_only")) == 0

    def test_portal_only_reduces_positive(self):
        assert compute_urgency_score(_job(), school=_school("A", "portal_only")) == 20

    def test_combined_score(self):
        # A(30) + fresh(15) + urgent(20) + deadline(10) = 75
        assert (
            compute_urgency_score(
                _job(days_old=0),
                school=_school("A"),
                description="urgent, apply by 01/12/2027",
            )
            == 75
        )

    def test_always_in_range(self):
        s = compute_urgency_score(
            _job(days_old=0),
            school=_school("A"),
            description="urgent asap, apply by 01/12/2027",
        )
        assert 0 <= s <= 100
