"""Tests for jobhunt_core.school_urgency.score_urgency."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jobhunt_core.school_urgency import (
    SCORE_DIRECT_EMAIL_OK,
    SCORE_GROUP_A,
    SCORE_GROUP_B_PENALTY,
    SCORE_GROUP_C_PENALTY,
    SCORE_IMMEDIATE_LANGUAGE,
    SCORE_TIGHT_DEADLINE,
    score_urgency,
)


TODAY = date(2026, 5, 29)


# --- group_tier contribution ---


def test_group_a_baseline() -> None:
    assert score_urgency({"group_tier": "A", "policy": "portal_only"}) == SCORE_GROUP_A


def test_group_b_penalty() -> None:
    expected = max(0, SCORE_GROUP_B_PENALTY)  # clamped to 0
    assert score_urgency({"group_tier": "B", "policy": "portal_only"}) == expected


def test_group_c_penalty() -> None:
    expected = max(0, SCORE_GROUP_C_PENALTY)  # clamped to 0
    assert score_urgency({"group_tier": "C", "policy": "portal_only"}) == expected


def test_missing_group_tier_does_not_crash() -> None:
    assert score_urgency({"policy": "portal_only"}) == 0


# --- policy contribution ---


def test_direct_email_ok_adds_bonus() -> None:
    # Group A + direct_email_ok = 30 + 20 = 50
    assert (
        score_urgency({"group_tier": "A", "policy": "direct_email_ok"})
        == SCORE_GROUP_A + SCORE_DIRECT_EMAIL_OK
    )


def test_other_policies_no_bonus() -> None:
    base = score_urgency({"group_tier": "A", "policy": "portal_only"})
    for policy in ("direct_email", "direct_email_director", "portal_only"):
        assert score_urgency({"group_tier": "A", "policy": policy}) == base, (
            f"policy={policy} unexpectedly added bonus"
        )


# --- language signal ---


@pytest.mark.parametrize(
    "text",
    [
        "We need this filled immediately",
        "Looking for an IT manager, immediately",
        "Position available as soon as possible",
        "Start ASAP",
        "Start date: next month or sooner",
    ],
)
def test_immediate_language_adds_bonus(text: str) -> None:
    score = score_urgency({"group_tier": "A", "policy": "portal_only"}, job_text=text)
    assert score == SCORE_GROUP_A + SCORE_IMMEDIATE_LANGUAGE


def test_no_immediate_language_no_bonus() -> None:
    text = "We are seeking an experienced IT manager for our team."
    score = score_urgency({"group_tier": "A", "policy": "portal_only"}, job_text=text)
    assert score == SCORE_GROUP_A


# --- deadline signal ---


def test_tight_deadline_adds_bonus() -> None:
    deadline = TODAY + timedelta(days=3)
    score = score_urgency(
        {"group_tier": "A", "policy": "portal_only"}, deadline=deadline, today=TODAY
    )
    assert score == SCORE_GROUP_A + SCORE_TIGHT_DEADLINE


def test_distant_deadline_no_bonus() -> None:
    deadline = TODAY + timedelta(days=30)
    score = score_urgency(
        {"group_tier": "A", "policy": "portal_only"}, deadline=deadline, today=TODAY
    )
    assert score == SCORE_GROUP_A


def test_past_deadline_no_bonus() -> None:
    deadline = TODAY - timedelta(days=1)
    score = score_urgency(
        {"group_tier": "A", "policy": "portal_only"}, deadline=deadline, today=TODAY
    )
    assert score == SCORE_GROUP_A


def test_deadline_today_gets_bonus() -> None:
    """Edge case: deadline = today should be considered tight (0 <= 0 <= 7)."""
    score = score_urgency(
        {"group_tier": "A", "policy": "portal_only"}, deadline=TODAY, today=TODAY
    )
    assert score == SCORE_GROUP_A + SCORE_TIGHT_DEADLINE


# --- combined / extremes ---


def test_max_realistic_score_isb_immediate_tight_deadline() -> None:
    """ISB in a peak scenario: Group A + direct_email_ok + immediate + tight deadline."""
    score = score_urgency(
        {"group_tier": "A", "policy": "direct_email_ok"},
        job_text="IT Manager needed immediately",
        deadline=TODAY + timedelta(days=3),
        today=TODAY,
    )
    # 30 + 20 + 20 + 10 = 80
    assert score == 80


def test_score_clamps_to_100() -> None:
    # Synthetic: if bonuses ever exceed 100, function must cap
    score = score_urgency(
        {"group_tier": "A", "policy": "direct_email_ok"},
        job_text="immediately as soon as possible asap",  # only matches once anyway
        deadline=TODAY,
        today=TODAY,
    )
    assert score <= 100


def test_score_clamps_to_0() -> None:
    # Group C alone gives -20 → clamped
    score = score_urgency({"group_tier": "C", "policy": "portal_only"})
    assert score == 0
