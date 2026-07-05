"""Tests de caracterización para TranslationService._resolve_language.

Fija el comportamiento actual (heurística de caracteres + tokens largos +
2 ramas de langdetect con distinto umbral) antes de refactorizar.
Valores capturados de la implementación vigente.
"""

import pytest
from langdetect import DetectorFactory

from services.translation_service import TranslationService as T

# langdetect usa una semilla aleatoria interna; la fijamos para que las ramas 3-4
# (langdetect) sean deterministas y el test no sea flaky. Los valores esperados de
# esas ramas son los que produce langdetect con seed=0 (comportamiento vigente).
DetectorFactory.seed = 0


@pytest.mark.parametrize(
    "title,db_lang,expected",
    [
        # Reglas 1-2 (caracteres/tokens): deterministas, sin langdetect.
        ("Softwareentwickler Zürich", "en", "de"),  # char ü
        ("Développeur", "en", "fr"),  # char é
        ("Sviluppatore però", "en", "it"),  # char ò
        ("Projektkoordinator role", "en", "de"),  # token > 12
        # Reglas 3-4 (langdetect, seed=0):
        ("Software Engineer", "en", "en"),  # db=en + langdetect en >= 0.70
        ("Manager", "es", ""),  # db=es sin evidencia suficiente
        ("Chef de projet", "fr", "hr"),  # db no-skip; langdetect (seed=0) → hr
        ("Data Analyst", "en", ""),  # db=en, langdetect no coincide/insuf.
    ],
)
def test_resolve_language_characterization(title, db_lang, expected):
    assert T._resolve_language(title, db_lang) == expected
