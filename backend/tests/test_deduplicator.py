"""Tests for the Deduplicator service — fuzzy hash computation and DB lookup."""

from models.job import Job
from services.deduplicator import Deduplicator


# --- Fuzzy hash computation ---


class TestComputeFuzzyHash:
    def test_same_title_company_same_hash(self):
        """Identical title + company always produce the same fuzzy_hash."""
        h1 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        assert h1 == h2

    def test_case_insensitive(self):
        """Fuzzy hash ignores casing differences."""
        h1 = Deduplicator.compute_fuzzy_hash("PYTHON DEVELOPER", "ACME")
        h2 = Deduplicator.compute_fuzzy_hash("python developer", "acme")
        assert h1 == h2

    def test_seniority_stripped(self):
        """Seniority prefixes are removed before hashing."""
        h1 = Deduplicator.compute_fuzzy_hash("Senior Python Developer", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        assert h1 == h2

    def test_gender_markers_stripped(self):
        """Gender markers like (m/f/d) are removed before hashing."""
        h1 = Deduplicator.compute_fuzzy_hash("Developer (m/f/d)", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Developer", "Acme")
        assert h1 == h2

    def test_company_suffix_ag(self):
        """Legal suffix 'AG' is stripped from company name."""
        h1 = Deduplicator.compute_fuzzy_hash("Developer", "Acme AG")
        h2 = Deduplicator.compute_fuzzy_hash("Developer", "Acme")
        assert h1 == h2

    def test_company_suffix_ltd(self):
        """Legal suffix 'Ltd' is stripped from company name."""
        h1 = Deduplicator.compute_fuzzy_hash("Developer", "Swiss Corp Ltd")
        h2 = Deduplicator.compute_fuzzy_hash("Developer", "Swiss Corp")
        assert h1 == h2

    def test_different_jobs_different_hashes(self):
        """Completely different jobs produce different hashes."""
        h1 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Java Developer", "Google")
        assert h1 != h2


# --- DB lookup (requires db_session fixture) ---


class TestFindFuzzyDuplicate:
    async def test_finds_match_from_different_source(self, db_session):
        """find_fuzzy_duplicate returns the canonical hash when a match exists
        from a different source."""
        job = Job(
            hash="abc123",
            source="jobicy",
            title="Python Developer",
            company="Acme",
            url="http://example.com/job/1",
            fuzzy_hash="deadbeef",
            is_active=True,
        )
        db_session.add(job)
        await db_session.commit()

        result = await Deduplicator.find_fuzzy_duplicate(
            db_session, "deadbeef", "jooble"
        )
        assert result == "abc123"

    async def test_returns_none_for_same_source(self, db_session):
        """find_fuzzy_duplicate ignores matches from the same source."""
        job = Job(
            hash="abc123",
            source="jobicy",
            title="Python Developer",
            company="Acme",
            url="http://example.com/job/1",
            fuzzy_hash="deadbeef",
            is_active=True,
        )
        db_session.add(job)
        await db_session.commit()

        result = await Deduplicator.find_fuzzy_duplicate(
            db_session, "deadbeef", "jobicy"
        )
        assert result is None

    async def test_returns_none_when_no_match(self, db_session):
        """find_fuzzy_duplicate returns None when no matching fuzzy_hash exists."""
        result = await Deduplicator.find_fuzzy_duplicate(
            db_session, "nonexistent", "jooble"
        )
        assert result is None
