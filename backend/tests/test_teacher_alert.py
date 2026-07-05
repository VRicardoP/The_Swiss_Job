"""Tests para la detección de ofertas de profesor de primaria (Suiza)."""

from services.teacher_alert import build_alert_email, is_primary_teacher_job


class TestIsPrimaryTeacherJob:
    def test_matches_english_primary(self):
        assert is_primary_teacher_job("H", "Primary Teacher (Zurich)", []) is True

    def test_matches_german_primarstufe(self):
        assert (
            is_primary_teacher_job("H", "Lehrperson Primarstufe 60-80%", ["schule"])
            is True
        )

    def test_matches_french_primaire(self):
        assert is_primary_teacher_job("H", "Enseignant primaire — Genève", None) is True

    def test_matches_from_tags(self):
        assert (
            is_primary_teacher_job("H", "Lehrperson gesucht", ["grundschule"]) is True
        )

    def test_rejects_secondary_teacher(self):
        # Docencia (H) pero NO primaria → no debe avisar.
        assert is_primary_teacher_job("H", "Sekundarlehrer Mathematik", []) is False

    def test_rejects_kindergarten_only(self):
        assert is_primary_teacher_job("H", "Kindergarten Betreuung", []) is False

    def test_rejects_non_teaching_category(self):
        # Aunque el texto diga "primary teacher", si la categoría no es H, no aplica.
        assert is_primary_teacher_job("A", "Primary Teacher", []) is False

    def test_rejects_empty(self):
        assert is_primary_teacher_job(None, None, None) is False


class TestBuildAlertEmail:
    def _job(self, title, company="Schule X", canton="ZH", url="https://e.ch/1"):
        class _J:
            pass

        j = _J()
        j.title, j.company, j.canton, j.location, j.url = (
            title,
            company,
            canton,
            None,
            url,
        )
        return j

    def test_subject_has_count_and_plural(self):
        subject, _, _ = build_alert_email([self._job("Primary Teacher")])
        assert "1 nueva oferta" in subject
        subject2, _, _ = build_alert_email([self._job("A"), self._job("B")])
        assert "2 nuevas ofertas" in subject2

    def test_text_and_html_contain_job(self):
        _, text, html = build_alert_email(
            [self._job("Primarlehrer 80%", company="PS Bern", url="https://e.ch/9")]
        )
        assert "Primarlehrer 80%" in text and "https://e.ch/9" in text
        assert "Primarlehrer 80%" in html and 'href="https://e.ch/9"' in html

    def test_html_escapes_content(self):
        _, _, html = build_alert_email([self._job("<script>x</script>", company="A&B")])
        assert "<script>" not in html
        assert "&lt;script&gt;" in html and "A&amp;B" in html
