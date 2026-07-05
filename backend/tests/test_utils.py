"""Tests for utils/text.py and utils/http.py."""

from utils.text import (
    extract_canton,
    extract_job_skills,
    strip_html_tags,
)


# ---------------------------------------------------------------------------
# strip_html_tags
# ---------------------------------------------------------------------------


class TestStripHtmlTags:
    def test_removes_tags(self):
        assert strip_html_tags("<p>Hello <b>World</b></p>") == "Hello World"

    def test_empty_string(self):
        assert strip_html_tags("") == ""

    def test_none_returns_empty(self):
        assert strip_html_tags(None) == ""

    def test_plain_text_unchanged(self):
        assert strip_html_tags("No tags here") == "No tags here"

    def test_nested_tags(self):
        result = strip_html_tags("<div><ul><li>Item</li></ul></div>")
        assert "Item" in result

    def test_normalizes_whitespace(self):
        result = strip_html_tags("<p>Hello</p>   <p>World</p>")
        assert result == "Hello World"


# ---------------------------------------------------------------------------
# extract_job_skills
# ---------------------------------------------------------------------------


class TestExtractJobSkills:
    def test_finds_known_skills(self):
        # JOB_TAGS (Fase 5) refleja el perfil real: idiomas, docencia, contenido, RRHH.
        skills = extract_job_skills(
            "Content Editor and Copywriter",
            "Fluent in English and French, TEFL certified",
        )
        assert "content editor" in skills
        assert "copywriter" in skills
        assert "english" in skills
        assert "french" in skills
        assert "tefl" in skills

    def test_max_15_skills(self):
        # Description with many tech tags
        desc = " ".join(
            [
                "python javascript typescript java php ruby go rust",
                "react angular vue.js node.js django flask fastapi spring",
                "docker kubernetes aws azure gcp git linux terraform",
            ]
        )
        skills = extract_job_skills("Full Stack Developer", desc)
        assert len(skills) <= 15

    def test_empty_inputs(self):
        assert extract_job_skills("", "") == []

    def test_no_duplicates(self):
        skills = extract_job_skills("Copywriter Copywriter", "copywriter needed")
        assert skills.count("copywriter") == 1

    def test_case_insensitive(self):
        skills = extract_job_skills("COPYWRITER Needed", "Using ENGLISH")
        assert "copywriter" in skills
        assert "english" in skills


# ---------------------------------------------------------------------------
# extract_canton
# ---------------------------------------------------------------------------


class TestExtractCanton:
    def test_direct_match(self):
        assert extract_canton("zurich") == "ZH"
        assert extract_canton("Zürich") == "ZH"
        assert extract_canton("genève") == "GE"
        assert extract_canton("geneva") == "GE"

    def test_two_letter_direct(self):
        assert extract_canton("ZH") == "ZH"
        assert extract_canton("ge") == "GE"

    def test_substring_match(self):
        assert extract_canton("8001 Zurich, Switzerland") == "ZH"
        assert extract_canton("Bern, Switzerland") == "BE"

    def test_returns_none_for_unknown(self):
        assert extract_canton("New York") is None
        assert extract_canton("Berlin") is None

    def test_empty_returns_none(self):
        assert extract_canton("") is None
        assert extract_canton(None) is None

    def test_all_major_cantons(self):
        assert extract_canton("zurich") == "ZH"
        assert extract_canton("bern") == "BE"
        assert extract_canton("luzern") == "LU"
        assert extract_canton("vaud") == "VD"
        assert extract_canton("ticino") == "TI"
        assert extract_canton("aargau") == "AG"
        assert extract_canton("st. gallen") == "SG"
        assert extract_canton("graubünden") == "GR"
        assert extract_canton("valais") == "VS"
        assert extract_canton("neuchatel") == "NE"
        assert extract_canton("jura") == "JU"
