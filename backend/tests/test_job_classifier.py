"""Tests de caracterización para JobClassifier (services/job_classifier.py).

classify_job era lógica pura sin cobertura pese a alimentar el matching
(CATEGORY_MULTIPLIERS penaliza H–M) y la alerta de profesor de primaria
(categoría H). Cubre: matching por categoría, word-boundary anti-falsos-
positivos, prioridad A→M, multilingüe (DE/FR), tags y caso "otros".
"""

import pytest

from services.job_classifier import (
    CATEGORIES,
    CATEGORY_MULTIPLIERS,
    classify_job,
)


class TestClassifyCategories:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Proofreader wanted", "A"),  # Edición & Localización
            ("Prompt Engineer", "B"),  # IA & Evaluación
            ("Virtual Assistant", "C"),  # Administración & VA
            ("Human Resources Officer", "D"),  # RRHH & Formación
            ("Customer Success Manager", "E"),  # Customer Success
            ("United Nations analyst", "F"),  # Organismos Internacionales
            ("Content Writer", "G"),  # Contenido & Marketing
            ("Primary Teacher", "H"),  # Docencia (fuera de perfil)
            ("Sales Manager", "I"),  # Ventas
            ("Accountant", "K"),  # Finanzas
        ],
    )
    def test_representative_keyword_matches_category(self, title, expected):
        assert classify_job(title, []) == expected

    def test_unclassified_returns_otros(self):
        assert classify_job("Software Engineer", []) == "otros"
        assert classify_job("Mechanical Engineer", []) == "otros"

    def test_case_insensitive(self):
        assert classify_job("TEACHER", []) == "H"
        assert classify_job("tRaNsLaToR", []) == "A"


class TestMultilingual:
    def test_german_teacher(self):
        assert classify_job("Lehrer gesucht", []) == "H"

    def test_french_teacher(self):
        assert classify_job("Enseignant de primaire", []) == "H"

    def test_german_sales(self):
        assert classify_job("Verkäufer im Aussendienst", []) == "I"


class TestWordBoundary:
    """No debe hacer match por substring interior (evita falsos positivos)."""

    def test_ngo_not_matched_inside_django(self):
        # "ngo" es keyword de F; no debe activarse dentro de "django"
        assert classify_job("Django Developer", []) != "F"

    def test_un_not_matched_inside_running(self):
        # "un " (→ \bun\b) es keyword de F; "running" no debe activarla
        assert classify_job("Running Coach", []) != "F"


class TestPriorityAndTags:
    def test_first_match_wins_A_over_H(self):
        # "translator" (A) precede a "teacher" (H): A gana por orden A→M
        assert classify_job("Translator and Teacher", []) == "A"

    def test_tags_contribute_to_classification(self):
        assert classify_job("Random title", ["translator"]) == "A"

    def test_empty_tags_none_safe(self):
        assert classify_job("Software Engineer", None) == "otros"


class TestCategoryMultipliers:
    def test_target_categories_no_penalty(self):
        for cat in ("A", "B", "C", "D", "E", "F", "G"):
            assert CATEGORY_MULTIPLIERS[cat] == 1.00

    def test_out_of_scope_penalized(self):
        # H (docencia) es el objetivo explícito a EVITAR → mayor penalización
        assert CATEGORY_MULTIPLIERS["H"] < 0.5
        assert CATEGORY_MULTIPLIERS["M"] < 0.5
        assert CATEGORY_MULTIPLIERS["otros"] < 1.0

    def test_every_category_has_a_multiplier(self):
        for cat_id, _ in CATEGORIES:
            assert cat_id in CATEGORY_MULTIPLIERS
        assert "otros" in CATEGORY_MULTIPLIERS
