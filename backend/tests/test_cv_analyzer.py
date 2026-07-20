"""Tests de CVAnalyzer: parseo/normalización JSON y fallback Gemini→Groq."""

from services.cv_analyzer import CVAnalyzer


# --- _parse_json ---------------------------------------------------------------


def test_parse_json_plain():
    assert CVAnalyzer._parse_json('{"title":"X"}') == {"title": "X"}


def test_parse_json_strips_markdown_fences():
    assert CVAnalyzer._parse_json('```json\n{"title":"X"}\n```') == {"title": "X"}


def test_parse_json_extracts_from_prose():
    out = CVAnalyzer._parse_json('Sure, here you go:\n{"a": 1}\nHope it helps')
    assert out["a"] == 1


def test_parse_json_invalid_returns_empty():
    assert CVAnalyzer._parse_json("not json at all") == {}
    assert CVAnalyzer._parse_json("") == {}


# --- _normalize ----------------------------------------------------------------


def test_normalize_coerces_and_dedupes():
    out = CVAnalyzer._normalize(
        {
            "title": "  Virtual Assistant  ",
            "skills": ["Translation", "translation", "Proofreading", ""],
            "languages": ["English", "Spanish"],
            "locations": ["Remote"],
            "experience_years": 10.7,
            "remote_pref": "REMOTE_ONLY",
        }
    )
    assert out["title"] == "Virtual Assistant"
    assert out["skills"] == ["Translation", "Proofreading"]  # dedupe + drop empty
    assert out["languages"] == ["English", "Spanish"]
    assert out["experience_years"] == 10  # 10.7 → 10, clamp [0,50]
    assert out["remote_pref"] == "remote_only"


def test_normalize_rejects_invalid_remote_and_bool_experience():
    out = CVAnalyzer._normalize({"remote_pref": "maybe", "experience_years": True})
    assert "remote_pref" not in out
    assert "experience_years" not in out  # bool NO cuenta como int


def test_normalize_empty_input():
    assert CVAnalyzer._normalize({}) == {}


# --- extract_fields: fallback Gemini→Groq --------------------------------------


class _FakeLLM:
    def __init__(self, available=True, text="{}", fail=False):
        self.is_available = available
        self._text = text
        self._fail = fail

    async def get_chat_response(self, **kwargs):
        if self._fail:
            raise RuntimeError("LLM down")
        return self._text


async def test_extract_fields_falls_back_to_groq_when_gemini_fails():
    gemini = _FakeLLM(available=True, fail=True)
    groq = _FakeLLM(available=True, text='{"title":"VA","skills":["x"]}')
    analyzer = CVAnalyzer(groq, gemini)

    out = await analyzer.extract_fields("a reasonably long CV text here")

    assert out["title"] == "VA"
    assert out["skills"] == ["x"]


async def test_extract_fields_no_provider_returns_empty():
    off = _FakeLLM(available=False)
    analyzer = CVAnalyzer(off, off)
    assert await analyzer.extract_fields("cv text") == {}


async def test_extract_fields_bad_json_returns_empty():
    groq = _FakeLLM(available=True, text="sorry, I cannot do that")
    analyzer = CVAnalyzer(groq, None)
    assert await analyzer.extract_fields("cv text") == {}
