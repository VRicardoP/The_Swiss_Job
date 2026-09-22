"""Language detection must not be paid once per served offer.

Measured on the NAS (2026-09-22): the served feed carries 1.800 offers, ALL of
them without a `language` field, and `_to_match_response` detects the language of
each one — 50,1 ms per call, about 90 s per request. It happens even with
`translate=false`, because the detection sits outside the LLM block, and it is
what made `GET /api/v1/match/results?limit=3000` take 79 s end to end.

Detection is a pure function of the title, so the same title must be resolved
once. This asserts the CALL COUNT to the underlying detector, which is
deterministic, instead of timing it on a loaded two-core NAS.
"""

import pytest

from services.translation_service import TranslationService


@pytest.fixture(autouse=True)
def _clear_cache():
    TranslationService._detect_language.cache_clear()
    yield
    TranslationService._detect_language.cache_clear()


def test_repeated_titles_are_detected_once(monkeypatch):
    llamadas = []
    original = TranslationService._resolve_language.__func__

    def contar(cls, text, language):
        llamadas.append(text)
        return original(cls, text, language)

    monkeypatch.setattr(TranslationService, "_resolve_language", classmethod(contar))

    for _ in range(5):
        TranslationService._detect_language("Softwareentwickler (m/w/d)")
    assert len(llamadas) == 1, f"detected {len(llamadas)} times instead of once"


def test_different_titles_are_each_detected(monkeypatch):
    """The cache must not collapse distinct titles into one answer."""
    de = TranslationService._detect_language("Softwareentwickler gesucht in Zürich")
    fr = TranslationService._detect_language("Développeur logiciel à Genève")
    assert de and fr and de != fr, f"expected different languages, got {de!r}/{fr!r}"


def test_the_same_title_always_gets_the_same_answer():
    """Memoising STABILISES an answer that langdetect does not guarantee.

    Found while writing this test: langdetect is not deterministic on short
    titles. "Sviluppatore software" came back as `sv` on one pass and `en` on
    the next, which means the language badge in the UI can flip between loads
    for the same offer. The cache does not cause that -- it hides it, by
    resolving each title once per process.

    So the guarantee asserted here is stability, not equality across cache
    clears: the latter would be asserting something the underlying library
    never promised.
    """
    titulo = "Sviluppatore software"
    primera = TranslationService._detect_language(titulo)
    repetidas = {TranslationService._detect_language(titulo) for _ in range(20)}
    assert repetidas == {primera}, f"the badge flipped: {repetidas}"


def test_an_empty_title_needs_no_detector(monkeypatch):
    """The common degenerate case must not reach the detector at all."""
    assert TranslationService._detect_language("") == ""
