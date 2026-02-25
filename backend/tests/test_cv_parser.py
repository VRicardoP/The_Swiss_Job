"""Tests for CVParser service."""

import io

import pytest

from services.cv_parser import CVParser, DOCX_TYPE, PDF_TYPE


def _make_pdf(text: str) -> bytes:
    """Create a minimal PDF with the given text."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_docx(text: str) -> bytes:
    """Create a minimal DOCX with the given text."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class TestParsePDF:
    def test_parse_pdf_extracts_text(self):
        pdf = _make_pdf("Senior Python Developer at Zurich")
        result = CVParser.parse_pdf(pdf)
        assert "Python" in result
        assert "Zurich" in result

    def test_parse_pdf_empty_document(self):
        import fitz

        doc = fitz.open()
        doc.new_page()
        pdf_bytes = doc.tobytes()
        doc.close()
        result = CVParser.parse_pdf(pdf_bytes)
        assert result == ""


class TestParseDocx:
    def test_parse_docx_extracts_text(self):
        docx = _make_docx("Full Stack Developer with React and Node.js experience")
        result = CVParser.parse_docx(docx)
        assert "React" in result
        assert "Node.js" in result


class TestExtractText:
    def test_routing_pdf(self):
        pdf = _make_pdf("Hello from PDF")
        result = CVParser.extract_text(pdf, PDF_TYPE)
        assert "Hello" in result

    def test_routing_docx(self):
        docx = _make_docx("Hello from DOCX")
        result = CVParser.extract_text(docx, DOCX_TYPE)
        assert "Hello" in result

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            CVParser.extract_text(b"data", "text/plain")


class TestExtractSkills:
    def test_finds_programming_languages(self):
        skills = CVParser.extract_skills("Experienced in Python, Java, and React")
        assert "Python" in skills
        assert "Java" in skills
        assert "React" in skills

    def test_finds_multilingual_keywords(self):
        skills = CVParser.extract_skills("Sprachen: Deutsch, English, Italiano")
        assert "Deutsch" in skills
        assert "English" in skills
        assert "Italiano" in skills

    def test_deduplication(self):
        skills = CVParser.extract_skills("Python python PYTHON developer")
        python_count = sum(1 for s in skills if s.lower() == "python")
        assert python_count == 1

    def test_empty_text_returns_empty(self):
        assert CVParser.extract_skills("") == []
        assert CVParser.extract_skills("no skills here at all") == []


class TestCleanText:
    def test_collapses_whitespace(self):
        result = CVParser.clean_text("Hello     World\n\n\n\n\nNew section")
        assert "  " not in result
        assert "\n\n\n" not in result
        assert "Hello World" in result
