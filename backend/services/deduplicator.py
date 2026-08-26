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

# G3/P1-2 — el coseno del dedup semántico es PREFILTRO, no veredicto.
# El encoder (paraphrase-multilingual-MiniLM-L12-v2) trunca a 128 tokens, así que
# de una oferta suiza solo ve el arranque de la descripción: el boilerplate del
# empleador. Medido en vivo con el modelo real (build_job_text completo,
# "Sachbearbeiter Finanzbuchhaltung" vs "Gaertner Gruenflaechenunterhalt" del
# MISMO empleador): 0 chars de boilerplate -> 0.5860; 260 -> 0.9296;
# 520 y mas -> 0.9708 SATURADO (el resto del texto ni entra). Por encima de 0.95
# el coseno mide cuanto boilerplate comparten, no si son la misma vacante, y
# subir el umbral no arregla nada (satura). Por eso el veredicto exige ademas
# que los TITULOS compartan lexico y que canton/salario no se contradigan.
_SEMANTIC_CANDIDATE_LIMIT = 10
# Jaccard de tokens del titulo normalizado. 0.3 = "al menos un tercio del lexico
# combinado es comun": con titulos de 2 tokens basta 1 comun; con 3+3 hacen falta 2.
_TITLE_OVERLAP_MIN = 0.3


class Deduplicator:
    """Fuzzy deduplication across job sources."""

    @staticmethod
    def compute_fuzzy_hash(title: str, company: str) -> str:
        """Compute MD5 of normalized title + company for cross-source dedup.

        Normalization:
        - Title: lowercase, strip seniority keywords, remove punctuation, collapse spaces
        - Company: lowercase, strip legal suffixes, remove punctuation, collapse spaces

        G3/P2-12: si CUALQUIERA de los dos lados normaliza a vacío la identidad
        es degenerada y se devuelve "" (= "sin identidad fuzzy"), nunca un hash.
        `company` vacía es alcanzable (`_process_raw_jobs` valida title y url,
        no company) y el MD5 de "titulo|" metía en el MISMO bucket a vacantes de
        empresas y fuentes distintas; con un título de solo seniority el hash era
        MD5("|") y TODO caía en un único bucket global. Misma disciplina que
        `job_identity()` en `job_service.py`.
        """
        norm_title = Deduplicator._normalize_title(title)
        norm_company = Deduplicator._normalize_company(company)
        if not norm_title or not norm_company:
            return ""
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

        G3/P2-12: un `fuzzy_hash` vacío es la marca de identidad degenerada que
        emite `compute_fuzzy_hash` — no identifica nada y NUNCA debe casar.
        """
        if not fuzzy_hash:
            return None
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
    def _title_overlap(title_a: str, title_b: str) -> float:
        """Jaccard de los tokens del título normalizado (G3/P1-2).

        Reutiliza `_normalize_title` (misma limpieza que la vía fuzzy: género,
        puntuación, seniority) para que "Developer (m/w/d)" y "Senior Developer"
        cuenten como el mismo léxico. Sin título normalizado a ningún lado no
        hay señal → 0.0 (el candidato se descarta, no se acepta a ciegas).
        """
        tokens_a = set(Deduplicator._normalize_title(title_a or "").split())
        tokens_b = set(Deduplicator._normalize_title(title_b or "").split())
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    @staticmethod
    def _cantons_conflict(canton_a: str | None, canton_b: str | None) -> bool:
        """True solo si AMBOS cantones existen y son distintos.

        La misma vacante sindicada a dos portales no cambia de cantón; un cantón
        ausente no decide nada (la mayoría de fuentes no lo traen).
        """
        return bool(canton_a and canton_b) and canton_a != canton_b

    @staticmethod
    def _salaries_conflict(row_a, row_b) -> bool:
        """True solo si AMBAS declaran horquilla en CHF y NO se solapan.

        Con una sola cota ("hasta X") esa cota representa a las dos, igual que
        en `JobMatcher.compute_salary_match`. Sin dato en un lado no decide.
        """
        a_lo, a_hi = row_a.salary_min_chf, row_a.salary_max_chf
        b_lo, b_hi = row_b.salary_min_chf, row_b.salary_max_chf
        if (a_lo is None and a_hi is None) or (b_lo is None and b_hi is None):
            return False
        a_lo, a_hi = (
            (a_lo if a_lo is not None else a_hi),
            (a_hi if a_hi is not None else a_lo),
        )
        b_lo, b_hi = (
            (b_lo if b_lo is not None else b_hi),
            (b_hi if b_hi is not None else b_lo),
        )
        return a_hi < b_lo or b_hi < a_lo

    @staticmethod
    async def find_semantic_duplicates(
        db: AsyncSession, job: Job, threshold: float = 0.95
    ) -> list[str]:
        """Find cross-source jobs that are the SAME vacancy as `job`.

        El coseno de pgvector (distance < 1 - threshold) selecciona CANDIDATOS;
        el veredicto lo dan las condiciones estructurales de abajo. Devuelve el
        hash canónico más antiguo que pase todas (lista de 0 o 1 elemento, como
        antes).

        Exclusiones en la consulta (B-2):
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

        Segunda condición, en Python sobre los candidatos (G3/P1-2):
        - los títulos deben compartir léxico (`_title_overlap`), y
        - cantón y salario no deben contradecirse.
        El embedding persistido es el del MATCHING y lo comparten
        `_stage1_vector_search` y este dedup: NO se puede re-orientar el texto
        embebido solo para el dedup sin re-embeber todo el corpus, así que la
        discriminación se añade AQUÍ, donde no cuesta un vector nuevo.

        COTA CONOCIDA: el coseno sigue midiendo boilerplate (el techo de 128
        tokens del encoder no se toca). Lo que impide el falso positivo es la
        segunda condición, y esta es LÉXICA: dos vacantes del mismo empleador
        cuyos títulos comparten un tercio del léxico ("Sachbearbeiter
        Finanzbuchhaltung" vs "Sachbearbeiter Debitoren") siguen pudiendo
        casar si además comparten cantón y no declaran salario. A cambio, dos
        vacantes de roles distintos —el caso reportado— ya no se marcan.
        """
        if job.embedding is None:
            return []
        # Stub sin descripción → embedding degenerado; no comparar.
        if not (job.description or "").strip():
            return []

        max_distance = 1.0 - threshold

        stmt = (
            select(
                Job.hash,
                Job.title,
                Job.canton,
                Job.salary_min_chf,
                Job.salary_max_chf,
            )
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
            # Se mantiene el orden por antigüedad (la más antigua = raíz): es lo
            # que hace determinista al canónico y evita cadenas A→B→A. El precio
            # es que, con más de _SEMANTIC_CANDIDATE_LIMIT gemelos de boilerplate
            # más antiguos que el duplicado real, este se queda fuera del
            # prefiltro y NO se deduplica — falso negativo, el lado seguro.
            .order_by(Job.first_seen_at.asc())
            # Prefiltro: varios candidatos, porque el más antiguo puede ser un
            # gemelo de boilerplate y el duplicado real venir detrás.
            .limit(_SEMANTIC_CANDIDATE_LIMIT)
        )
        rows = (await db.execute(stmt)).all()

        for row in rows:
            if Deduplicator._title_overlap(job.title, row.title) < _TITLE_OVERLAP_MIN:
                continue
            if Deduplicator._cantons_conflict(job.canton, row.canton):
                continue
            if Deduplicator._salaries_conflict(job, row):
                continue
            return [row.hash]
        return []
