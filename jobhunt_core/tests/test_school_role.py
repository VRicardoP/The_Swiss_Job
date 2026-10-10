"""Tests for jobhunt_core.school_role."""

from __future__ import annotations

import pytest

from jobhunt_core.school_role import (
    SCORE_EXCLUDE,
    SCORE_IRRELEVANT,
    SCORE_MATCH,
    SCORE_PARTIAL,
    classify_role,
)


# --- strong matches ---


@pytest.mark.parametrize(
    "title",
    [
        "IT Manager",
        "Head of IT",
        "Technology Coordinator",
        "Systems Administrator",
        "EdTech Specialist",
        "ICT Coordinator",
        "Digital Learning Coordinator",
        "Network Administrator",
        "DevOps Engineer",
        "Chief Information Officer",
    ],
)
def test_match_terms_score_plus_one(title: str) -> None:
    assert classify_role(title) == SCORE_MATCH


# --- excluded (teaching roles) ---


@pytest.mark.parametrize(
    "title",
    [
        "IT Teacher",
        "ICT Teacher",
        "Computer Science Teacher",
        "Informatics Teacher",
        "Computing Teacher",
        "Professor of Computer Science",
        "Teaching Assistant - ICT",
    ],
)
def test_excluded_terms_score_minus_one(title: str) -> None:
    assert classify_role(title) == SCORE_EXCLUDE


def test_exclusion_overrides_match_when_both_present() -> None:
    """'IT Manager teacher' is ambiguous — exclusion wins by design."""
    # 'IT Teacher' substring beats 'IT Manager'
    assert classify_role("IT Manager / IT Teacher") == SCORE_EXCLUDE


# --- partial ---


@pytest.mark.parametrize(
    "title",
    [
        "Technology Lead",  # 'technology' partial term
        "Digital Specialist",  # 'digital' partial
        "IT Support Engineer",  # 'it support'
        "Helpdesk Technician",  # 'helpdesk'
    ],
)
def test_partial_terms_score_half(title: str) -> None:
    assert classify_role(title) == SCORE_PARTIAL


# --- irrelevant ---


@pytest.mark.parametrize(
    "title",
    [
        "English Teacher",
        "Maths Teacher",
        "Director of Admissions",
        "School Nurse",
        "Bus Driver",
    ],
)
def test_irrelevant_titles(title: str) -> None:
    # Most should be 0.0; some teacher roles already excluded via the teacher term
    score = classify_role(title)
    assert score in (SCORE_IRRELEVANT, SCORE_EXCLUDE)


def test_empty_input_is_irrelevant() -> None:
    assert classify_role("") == SCORE_IRRELEVANT
    assert classify_role("   ") == SCORE_IRRELEVANT


# --- description widens the haystack ---


def test_description_can_trigger_match_when_title_is_neutral() -> None:
    score = classify_role(
        "Job opening",
        "We are looking for an experienced IT Manager to lead our infrastructure.",
    )
    assert score == SCORE_MATCH


def test_description_can_trigger_exclude_even_with_partial_title() -> None:
    score = classify_role(
        "Technology Lead",
        "You will be an IT teacher reporting to the head of school.",
    )
    assert score == SCORE_EXCLUDE


# --- case + whitespace insensitive ---


def test_case_insensitive() -> None:
    assert classify_role("IT MANAGER") == SCORE_MATCH
    assert classify_role("it manager") == SCORE_MATCH


def test_whitespace_normalised() -> None:
    assert classify_role("IT    Manager\n\t  ") == SCORE_MATCH


# --- multilingual: Swiss schools post in DE / FR / IT ---


@pytest.mark.parametrize(
    "title",
    [
        "Informatiker/in 80-100%",  # DE
        "Systemadministrator",  # DE
        "ICT-Verantwortliche/r",  # DE
        "Netzwerkadministrator",  # DE
        "Responsable informatique",  # FR
        "Administrateur système et réseau",  # FR (accents)
        "Informaticien·ne",  # FR
        "Amministratore di sistema",  # IT
        "Tecnico informatico",  # IT
    ],
)
def test_multilingual_it_roles_match(title: str) -> None:
    assert classify_role(title) == SCORE_MATCH


@pytest.mark.parametrize(
    "title",
    [
        "Informatiklehrer",  # DE teacher
        "Professeur d'informatique",  # FR teacher (apostrophe)
        "Enseignant en informatique",  # FR teacher
        "Insegnante di informatica",  # IT teacher
    ],
)
def test_multilingual_teaching_roles_excluded(title: str) -> None:
    assert classify_role(title) == SCORE_EXCLUDE


@pytest.mark.parametrize(
    "title",
    [
        "Informatik Support",  # DE partial
        "Support informatique N1",  # FR partial
        "Dipartimento di informatica",  # IT partial ('informatica' field, not 'informatico')
    ],
)
def test_multilingual_partial_terms(title: str) -> None:
    assert classify_role(title) == SCORE_PARTIAL


def test_accent_insensitive_matching() -> None:
    # Same term with and without accents must classify identically.
    assert classify_role("Administrateur système") == classify_role(
        "Administrateur systeme"
    )
    assert classify_role("Administrateur système") == SCORE_MATCH
