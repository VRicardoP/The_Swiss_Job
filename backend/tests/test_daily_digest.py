"""Tests del constructor del email de digest diario."""

from services.daily_digest import build_digest_email


def test_subject_incluye_conteo_y_plural():
    subject, _, _ = build_digest_email(
        [
            {"title": "A", "company": "X", "location": "ZH", "url": "u", "score": 80},
            {"title": "B", "company": "Y", "location": "GE", "url": "v", "score": 70},
        ]
    )
    assert subject.startswith("2 nuevas ofertas")


def test_singular():
    subject, _, _ = build_digest_email(
        [{"title": "A", "company": "X", "location": "ZH", "url": "u", "score": 80}]
    )
    assert subject.startswith("1 nueva oferta")


def test_html_escapa_contenido():
    _, _, html = build_digest_email(
        [
            {
                "title": "Dev <script>",
                "company": "A&B",
                "location": "ZH",
                "url": "http://x?a=1&b=2",
                "score": 90,
            }
        ]
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A&amp;B" in html


def test_incluye_score_redondeado():
    _, text, _ = build_digest_email(
        [{"title": "A", "company": "X", "location": "ZH", "url": "u", "score": 87.4}]
    )
    assert "87%" in text
