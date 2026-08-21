"""Tests de caracterización para JobClassifier (services/job_classifier.py).

classify_job era lógica pura sin cobertura pese a alimentar el matching
(CATEGORY_MULTIPLIERS penaliza H–M) y la alerta de profesor de primaria
(categoría H). Cubre: matching por categoría, word-boundary anti-falsos-
positivos, prioridad A→M, multilingüe (DE/FR), tags y caso "otros".

Además fija los dos arreglos de borde del registro de keywords:
- Composición alemana: stems como "lehrperson" o "pädagog" se compilan con
  borde abierto (_OPEN_BOTH/_OPEN_SUFFIX) porque \b estricto los dejaba mudos
  ("Kindergartenlehrperson", "Pädagoge" caían a "otros").
- Keywords con puntuación: el título se normaliza con _PUNCT_RE pero los
  keywords no se normalizaban, dejando 11 keywords inalcanzables ("e-learning",
  "l&d", "m&e officer"...).
"""

import pytest

from services.job_classifier import (
    _COMPILED,
    _OPEN_BOTH,
    _OPEN_SUFFIX,
    _PUNCT_RE,
    CATEGORIES,
    CATEGORY_MULTIPLIERS,
    classify_job,
)
from services.teacher_alert import is_primary_teacher_job


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


class TestGermanCompoundBoundary:
    """El alemán compone títulos (Kindergarten+lehrperson) y flexiona stems.

    Con \b estricto estos títulos caían a "otros": multiplicador 0.55 en vez
    del 0.15 de H, y la alerta de docencia primaria (que prefiltra por
    categoría H) nunca los veía. Los stems del registro se compilan ahora con
    borde abierto (_OPEN_BOTH / _OPEN_SUFFIX).
    """

    @pytest.mark.parametrize(
        "title",
        [
            # Los casos de la auditoría B-3
            "Primarlehrperson 80-100%",
            "Kindergartenlehrperson",
            "Sekundarlehrperson 80%",
            "Fachlehrer Musik",
            "Sozialpädagoge 60%",
            "Heilpädagoge",
            # Títulos reales del corpus (compuestos, plurales, flexiones)
            "Fachlehrpersonen Sek I  Deutsch",
            "Heilpädagogische Klassenlehrpersonen, Zyklus 1 und 2 (20 - 40 %)",
            "Sekundarlehrer/-in 60-90% (oder Klassenteam)",
            "Mitarbeiter/-in Fachstelle Sonderpädagogik 50%",
            "Arbeitsagogin / Arbeitsagogen 100 %",
        ],
    )
    def test_german_compound_teaching_titles_are_H(self, title):
        assert classify_job(title, []) == "H"

    def test_secretar_stem_matches_inflected_forms(self):
        # "secretar" estaba muerto con \b: no casaba ni "secretary"
        assert classify_job("Secretary to the Director", []) == "C"
        assert classify_job("Legal Secretary - Banking", []) == "C"

    def test_primarlehrperson_reaches_primary_teacher_alert(self):
        # La alerta prefiltra por categoría H: sin el borde abierto esta
        # oferta caía a "otros" y el filtro fino (marcador "primarlehr")
        # jamás llegaba a evaluarla — señal muda en el evento raro
        title = "Primarlehrperson 80-100%"
        category = classify_job(title, [])
        assert category == "H"
        assert is_primary_teacher_job(category, title, []) is True


class TestGermanCompoundTradeoffs:
    """Falsos positivos del borde abierto, evaluados y aceptados."""

    def test_driving_and_ski_instructors_classified_H(self):
        # Prefijo libre en "lehrer" casa Fahrlehrer/Skilehrer: siguen siendo
        # docencia, así que el multiplicador H (0.15) es el correcto
        assert classify_job("Fahrlehrer Kat. B 60%", []) == "H"
        assert classify_job("Skilehrer für die Wintersaison", []) == "H"

    def test_driving_instructor_not_in_primary_alert(self):
        # El filtro fino de primaria los descarta: no hay marcador de nivel
        assert is_primary_teacher_job("H", "Fahrlehrer Kat. B 60%", []) is False

    def test_paedagogisch_titles_classified_H(self):
        # "pädagogisch" en títulos sin "Lehrer": el corpus real muestra que
        # son roles de educación/atención social → H (0.15) es más fiel al
        # perfil (que penaliza ese sector) que "otros" (0.55)
        assert classify_job("Pädagogische Fachperson / Sozialpädagoge/in", []) == "H"
        assert classify_job("Pädagogische Mitarbeitende Kindergarten", []) == "H"


class TestPunctuationKeywords:
    """Keywords con puntuación eran INALCANZABLES: classify_job normaliza la
    puntuación del título a espacios, pero los keywords se compilaban sin esa
    normalización, así que "e-learning" exigía un literal que el texto
    normalizado ("e learning") jamás contiene. 11 keywords muertos en D/F/G/A.
    """

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Post-Editing Specialist", "A"),
            ("E-Learning Entwickler", "D"),
            ("L&D Manager", "D"),
            ("HR-Koordinator 80%", "D"),
            ("M&E Officer, Geneva", "F"),
            ("Anti-Racism Officer", "F"),
            ("Sub-Saharan Africa Advisor", "F"),
            ("EU-Politik Referent", "F"),
            ("E-Commerce Manager (m/w/d)", "G"),
        ],
    )
    def test_punctuation_keyword_matches_normalized_title(self, title, expected):
        assert classify_job(title, []) == expected

    def test_every_punctuation_keyword_is_reachable(self):
        # Estructural: cubre las 11 (también "post-editor" y "l&d specialist",
        # imposibles de aislar por comportamiento porque " editor" y "l&d"
        # ya casan cualquier título que las contenga)
        for (cat_id, keywords), (_, patterns) in zip(CATEGORIES, _COMPILED):
            cleaned = [kw.strip() for kw in keywords if kw.strip()]
            for kw, pattern in zip(cleaned, patterns):
                if not _PUNCT_RE.search(kw):
                    continue
                normalized = _PUNCT_RE.sub(" ", kw)
                assert pattern.search(normalized), (
                    f"keyword inalcanzable en {cat_id}: {kw!r}"
                )

    def test_normalized_ld_keeps_word_boundary(self):
        # "l&d" → patrón "l d" con \b estricto: no debe casar dentro de
        # palabras ("World Development" contiene "l d" solo sin borde)
        assert classify_job("World Development Officer", []) != "D"


class TestOpenBoundaryGuardrails:
    """El borde abierto es una excepción keyword a keyword, nunca global."""

    def test_short_english_keywords_stay_strict(self):
        # Si alguien abre "ngo", "un", "uno"... vuelven los falsos positivos
        # tipo "django" — este test lo bloquea estructuralmente (el
        # comportamiento lo cubre TestWordBoundary)
        protected = {"ngo", "ngos", "un", "uno", "who", "ilo", "wfp", "wto"}
        assert not protected & set(_OPEN_BOTH)
        assert not protected & set(_OPEN_SUFFIX)

    def test_open_keywords_are_long_stems(self):
        # Un stem corto con prefijo/sufijo libre casaría media lengua
        assert all(len(kw) >= 6 for kw in _OPEN_BOTH | _OPEN_SUFFIX)

    def test_open_keywords_are_single_words(self):
        # El borde abierto (\b\w*<kw>\w*\b) solo tiene sentido en stems de
        # una palabra: con una frase, el prefijo libre aplicaría solo a la
        # primera palabra y el sufijo solo a la última, en silencio. Se
        # comprueba la forma NORMALIZADA: "hr-koordinator" también es frase
        # tras pasar por _PUNCT_RE.
        for kw in _OPEN_BOTH | _OPEN_SUFFIX:
            assert " " not in _PUNCT_RE.sub(" ", kw).strip(), kw

    def test_open_keywords_exist_in_registry(self):
        # Evita entradas muertas en los sets si el registro cambia
        registered = {
            kw.strip() for _, keywords in CATEGORIES for kw in keywords if kw.strip()
        }
        assert (_OPEN_BOTH | _OPEN_SUFFIX) <= registered
