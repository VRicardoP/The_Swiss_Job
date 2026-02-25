"""Tests for utils/text.py and utils/http.py."""

from utils.text import (
    extract_canton,
    extract_job_skills,
    process_job_location,
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
        skills = extract_job_skills(
            "Senior Python Developer",
            "We use Django and PostgreSQL with Docker",
        )
        assert "python" in skills
        assert "django" in skills
        assert "postgresql" in skills
        assert "docker" in skills

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
        skills = extract_job_skills("Python Python", "python developer")
        assert skills.count("python") == 1

    def test_case_insensitive(self):
        skills = extract_job_skills("REACT Developer", "Using TYPESCRIPT")
        assert "react" in skills
        assert "typescript" in skills


# ---------------------------------------------------------------------------
# process_job_location
# ---------------------------------------------------------------------------


class TestProcessJobLocation:
    def test_known_country(self):
        assert process_job_location("usa") == "United States"
        assert process_job_location("ch") == "Switzerland"
        assert process_job_location("uk") == "United Kingdom"

    def test_worldwide_synonyms(self):
        assert process_job_location("remote") == "Remote / Worldwide"
        assert process_job_location("worldwide") == "Remote / Worldwide"
        assert process_job_location("anywhere") == "Remote / Worldwide"

    def test_unknown_titlecased(self):
        assert process_job_location("some city") == "Some City"

    def test_empty_string(self):
        assert process_job_location("") == "Unknown"

    def test_none(self):
        assert process_job_location(None) == "Unknown"

    def test_switzerland_variants(self):
        assert process_job_location("switzerland") == "Switzerland"
        assert process_job_location("schweiz") == "Switzerland"
        assert process_job_location("suisse") == "Switzerland"


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
