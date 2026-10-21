"""Tests de utils/text.py — lo único que queda: strip_html_tags.

DT-148 B2: los casos de `extract_job_skills` y `extract_canton` se fueron con
las funciones, que no tenían ningún llamador fuera de este fichero.
"""

from utils.text import strip_html_tags


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
