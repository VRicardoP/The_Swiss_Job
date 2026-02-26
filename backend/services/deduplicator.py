"""Deduplicator — fuzzy hash computation and cross-source duplicate detection."""

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job

# Legal suffixes to strip from company names
COMPANY_SUFFIXES: set[str] = {
    "ag",
    "gmbh",
    "sa",
    "sarl",
    "sàrl",
    "ltd",
    "inc",
    "corp",
    "se",
    "plc",
    "srl",
    "co",
    "llc",
    "pty",
    "bv",
    "nv",
}

# Seniority words to strip from titles for fuzzy matching
SENIORITY_STRIP: set[str] = {
    "senior",
    "junior",
    "lead",
    "head",
    "intern",
    "trainee",
    "sr.",
    "jr.",
    "sr",
    "jr",
    "(m/f/d)",
    "(m/w/d)",
    "(f/m/d)",
    "(w/m/d)",
    "(m/f/x)",
    "(w/m/x)",
    "(all genders)",
    "m/f/d",
    "m/w/d",
    "f/m/d",
    "w/m/d",
}

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")


class Deduplicator:
    """Fuzzy deduplication across job sources."""

    @staticmethod
    def compute_fuzzy_hash(title: str, company: str) -> str:
        """Compute MD5 of normalized title + company for cross-source dedup.

        Normalization:
        - Title: lowercase, strip seniority keywords, remove punctuation, collapse spaces
        - Company: lowercase, strip legal suffixes, remove punctuation, collapse spaces
        """
        norm_title = Deduplicator._normalize_title(title)
        norm_company = Deduplicator._normalize_company(company)
        raw = f"{norm_title}|{norm_company}"
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize a job title for fuzzy matching."""
        t = title.lower().strip()
        # Remove seniority keywords
        for word in SENIORITY_STRIP:
            t = t.replace(word, " ")
        # Remove punctuation and collapse spaces
        t = _PUNCT_RE.sub(" ", t)
        t = _SPACES_RE.sub(" ", t).strip()
        return t

    @staticmethod
    def _normalize_company(company: str) -> str:
        """Normalize a company name for fuzzy matching."""
        c = company.lower().strip()
        # Remove punctuation first (dots in "Inc.", commas)
        c = _PUNCT_RE.sub(" ", c)
        # Remove legal suffixes
        words = c.split()
        words = [w for w in words if w not in COMPANY_SUFFIXES]
        return _SPACES_RE.sub(" ", " ".join(words)).strip()

    @staticmethod
    async def find_fuzzy_duplicate(
        db: AsyncSession, fuzzy_hash: str, source: str
    ) -> str | None:
        """Find an existing active job with the same fuzzy_hash from a different source.

        Returns the canonical job hash if a duplicate is found, None otherwise.
        """
        stmt = (
            select(Job.hash)
            .where(
                Job.fuzzy_hash == fuzzy_hash,
                Job.source != source,
                Job.is_active.is_(True),
            )
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        return row

    @staticmethod
    async def find_semantic_duplicates(
        db: AsyncSession, job: Job, threshold: float = 0.95
    ) -> list[str]:
        """Find jobs with embedding cosine similarity > threshold.

        Uses pgvector cosine distance: distance < (1 - threshold).
        Returns list of canonical job hashes (oldest first).
        """
        if job.embedding is None:
            return []

        max_distance = 1.0 - threshold

        stmt = (
            select(Job.hash)
            .where(
                Job.hash != job.hash,
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                Job.embedding.is_not(None),
                Job.embedding.cosine_distance(job.embedding) < max_distance,
            )
            .order_by(Job.first_seen_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
