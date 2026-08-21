"""Deduplicator — fuzzy hash computation and cross-source duplicate detection."""

import hashlib
import re

from sqlalchemy import func, select
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

# Palabras de seniority — se filtran POR TOKEN (nunca por substring), para no
# corromper palabras legítimas que las contengan (international, leader, headset).
# Los puntos de "sr."/"jr." los elimina _PUNCT_RE antes de filtrar por token.
SENIORITY_TOKENS: set[str] = {
    "senior",
    "junior",
    "lead",
    "head",
    "intern",
    "trainee",
    "sr",
    "jr",
}

# Marcadores de diversidad/género. Se ENUMERAN los pares reales de género
# (m/w=männlich/weiblich, m/f, h/f=homme/femme...) + un 3.er marcador opcional
# (/d divers, /x nonbinary), para NO confundir abreviaturas técnicas (H/W hardware,
# R&D/M&A, C/C++, F/T) con género. Se quitan ANTES de la puntuación.
_DIVERSITY_RE = re.compile(
    r"\(?\b(?:m\s*/\s*f|f\s*/\s*m|m\s*/\s*w|w\s*/\s*m|h\s*/\s*f|f\s*/\s*h)"
    r"(?:\s*/\s*[dx])?\b\)?"  # (m/f/d), (m / w / d), h/f... (espacios opcionales)
    r"|\(\s*all\s+genders?\s*\)"  # (all genders)
    r"|\(\s*(?:divers|gn)\s*\)",  # (divers), (gn)
    re.IGNORECASE,
)

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
        """Normalize a job title for fuzzy matching.

        Los marcadores de diversidad (m/f/d, (all genders)...) se quitan antes de
        la puntuación porque la llevan. La seniority se filtra POR TOKEN, no por
        substring, para no romper palabras que la contengan (international, leader).
        """
        t = title.lower().strip()
        # Diversidad/género (regex) antes de quitar la puntuación (la llevan).
        t = _DIVERSITY_RE.sub(" ", t)
        # Puntuación fuera → tokens; filtrar seniority por token exacto.
        t = _PUNCT_RE.sub(" ", t)
        tokens = [w for w in t.split() if w not in SENIORITY_TOKENS]
        return _SPACES_RE.sub(" ", " ".join(tokens)).strip()

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
                Job.duplicate_of.is_(
                    None
                ),  # solo canónicas (no duplicados reactivados)
            )
            .order_by(Job.first_seen_at.asc())  # determinista: la más antigua = raíz
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        return row

    @staticmethod
    async def find_semantic_duplicates(
        db: AsyncSession, job: Job, threshold: float = 0.95
    ) -> list[str]:
        """Find cross-source jobs with embedding cosine similarity > threshold.

        Uses pgvector cosine distance: distance < (1 - threshold).
        Returns list of canonical job hashes (oldest first).

        Dos exclusiones (B-2):
        - Misma fuente: como en find_fuzzy_duplicate, los reposts dentro de una
          fuente ya los cubre la identidad exacta (hash / índice único de url);
          dentro de una fuente, títulos distintos son vacantes distintas
          ("Billing Specialist" vs "Billing Manager" superaban 0.95 por el
          boilerplate compartido).
        - Descripción vacía (en la entrada Y en los candidatos): el embedding
          de un stub sin descripción es degenerado — mide empresa+tags, no la
          vacante — y el coseno es simétrico, así que un stub a cualquiera de
          los dos lados invalida la comparación. Su dedup cross-source real lo
          sigue cubriendo la vía fuzzy (title+company).
        """
        if job.embedding is None:
            return []
        # Stub sin descripción → embedding degenerado; no comparar.
        if not (job.description or "").strip():
            return []

        max_distance = 1.0 - threshold

        stmt = (
            select(Job.hash)
            .where(
                Job.hash != job.hash,
                Job.source != job.source,
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                Job.embedding.is_not(None),
                # Candidatos con descripción real (btrim del mismo whitespace
                # ASCII que quita str.strip() en el filtro de la entrada).
                func.btrim(func.coalesce(Job.description, ""), " \t\n\r\f\v") != "",
                Job.embedding.cosine_distance(job.embedding) < max_distance,
            )
            .order_by(Job.first_seen_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
