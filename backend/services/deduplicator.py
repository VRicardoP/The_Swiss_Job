"""Deduplicator — fuzzy hash computation and cross-source duplicate detection."""

import hashlib
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
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
    async def find_same_source_clone(
        db: AsyncSession, fuzzy_hash: str, source: str, job_hash: str
    ) -> str | None:
        """ALARMA de deriva de identidad: gemela intra-fuente ya persistida.

        G5/P1-1 — `find_fuzzy_duplicate` excluye a propósito los pares de la
        MISMA fuente (los reposts intra-fuente los cubría la identidad exacta).
        Esa exclusión es correcta para MARCAR, pero deja ciega la rama
        DOMINANTE de la deriva de identidad: cuando el portal re-lista la
        vacante con un id NUEVO en la url, el INSERT no choca con `ix_jobs_url`
        —la url es distinta—, entra una fila CLON y la histórica deja de
        refrescar `last_seen_at` para siempre. No hay `UniqueViolationError`,
        así que `JobIdentityConflictError` no se arma y el run sale con
        `identity_conflicts=0` y la fuente `ok`.

        Esta consulta es la gemela SIN esa exclusión, y se usa SOLO como
        alarma: NO escribe `duplicate_of` ni desactiva nada. La exclusión de
        misma fuente es deliberada para el marcado (G3), pero no tiene por qué
        serlo para la observabilidad.

        Cota declarada (medida, no razonada): dos vacantes REALMENTE distintas
        de la misma fuente con idéntico `title`+`company` normalizados producen
        una alarma que no corresponde a ninguna deriva. Por eso solo cuenta y
        registra — nunca desactiva.
        """
        if not fuzzy_hash:
            return None
        stmt = (
            select(Job.hash)
            .where(
                Job.fuzzy_hash == fuzzy_hash,
                Job.source == source,
                Job.hash != job_hash,  # la recién insertada no es su propio clon
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
            )
            .order_by(Job.first_seen_at.asc())  # la más antigua = la que se pudre
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

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
        ausente no decide nada (la mayoría de fuentes no lo traen — medido:
        9.354 de 10.523 activas con `canton IS NULL`, así que esta puerta
        decide sobre el 11 % del corpus).

        G4/P3-7: la comparación se hace en MAYÚSCULAS. `extract_canton`
        normaliza hoy, pero los 8 scrapers que escriben `canton` a mano no
        pasan por ahí: un `'zh'` frente a un `'ZH'` inventaba un conflicto y
        vetaba un duplicado real en silencio.
        """
        if not (canton_a and canton_b):
            return False
        return canton_a.strip().upper() != canton_b.strip().upper()

    @staticmethod
    def _salaries_conflict(row_a, row_b) -> bool:
        """True solo si AMBAS declaran horquilla COMPARABLE en CHF y no se solapan.

        Con una sola cota ("hasta X") esa cota representa a las dos, igual que
        en `JobMatcher.compute_salary_match`. Sin dato en un lado no decide.

        G4/P3-7: el salario es el campo MENOS fiable del corpus (medido: 88
        filas activas con `salary_min_chf < 20000` y 598 con salario y
        `salary_period NULL` — importes mensuales guardados como anuales), y
        esta puerta rechazó 0 pares en el barrido de 6 umbrales: cero señal y
        riesgo real de vetar un duplicado bueno. Se exige que el PERIODO sea el
        mismo y no nulo antes de dejar que el salario vete: comparar un
        "3.500/mes" con un "80.000/año" no es un conflicto, es una unidad
        distinta.
        """
        period_a = getattr(row_a, "salary_period", None)
        period_b = getattr(row_b, "salary_period", None)
        if period_a is None or period_b is None or period_a != period_b:
            return False
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
        db: AsyncSession, job: Job, threshold: float | None = None
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

        COTA CONOCIDA (falso POSITIVO): el coseno sigue midiendo boilerplate
        (el techo de 128 tokens del encoder no se toca). Lo que impide el falso
        positivo es la segunda condición, y esta es LÉXICA: dos vacantes del
        mismo empleador cuyos títulos comparten un tercio del léxico
        ("Sachbearbeiter Finanzbuchhaltung" vs "Sachbearbeiter Debitoren")
        siguen pudiendo casar si además comparten cantón y no declaran salario.
        A cambio, dos vacantes de roles distintos —el caso reportado— ya no se
        marcan.

        COTA CONOCIDA (falso NEGATIVO, G4/P3-6): la puerta léxica también
        rechaza duplicados REALES cuando el título cambia de forma sin cambiar
        de sentido — "Primarlehrperson 60%" vs "Lehrperson Primarstufe 60%"
        (coseno 0.9487, solape 0.250) — y, entre idiomas, siempre: 20/20 pares
        reales de la misma vacante en DE↔FR/IT/EN medidos, 15 con solape
        exactamente 0.000. Por eso la puerta se SALTA cuando los idiomas
        declarados difieren, y su umbral es ahora un setting
        (`SEMANTIC_DEDUP_TITLE_OVERLAP_MIN`) y no una constante de módulo:
        bajar `SEMANTIC_DEDUP_THRESHOLD` como mando de remediación no servía de
        nada mientras la puerta léxica siguiera fija.
        """
        if job.embedding is None:
            return []
        # Stub sin descripción → embedding degenerado; no comparar.
        if not (job.description or "").strip():
            return []

        # G4/P3-6: el umbral por defecto sale del setting, no de un literal —
        # si no, `SEMANTIC_DEDUP_THRESHOLD` solo actuaba en los llamantes que
        # se acordaban de pasarlo.
        if threshold is None:
            threshold = settings.SEMANTIC_DEDUP_THRESHOLD
        max_distance = 1.0 - threshold

        stmt = (
            select(
                Job.hash,
                Job.title,
                Job.canton,
                Job.salary_min_chf,
                Job.salary_max_chf,
                Job.salary_period,
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

        overlap_min = settings.SEMANTIC_DEDUP_TITLE_OVERLAP_MIN
        for row in rows:
            # G5/P2-2 — la puerta léxica se aplica SIEMPRE, también entre
            # idiomas declarados distintos. `a63745c` la retiraba en ese caso
            # (G4/P3-6) para recuperar la misma vacante publicada en DE y en
            # FR; se RETIRA esa excepción. Los tres motivos, todos MEDIDOS con
            # el encoder real y contra el corpus de producción:
            #
            #  1. La separación está INVERTIDA. Sin la puerta, el veredicto
            #     cross-idioma queda en manos de un coseno que sigue midiendo
            #     boilerplate (cota de G3/P1-2): una maestra de primaria (DE) y
            #     un contable de deudores (FR) del mismo municipio puntúan
            #     0.8220 — POR ENCIMA del duplicado real, que puntúa 0.8195. No
            #     existe ningún valor de `SEMANTIC_DEDUP_THRESHOLD` que recoja
            #     el segundo sin recoger también el primero.
            #  2. No servía de nada igualmente. Con el umbral por defecto (0.95)
            #     el prefiltro SQL de arriba mata el par a 0.8195 una capa
            #     antes: la rama era INALCANZABLE. Sobre una muestra aleatoria
            #     de 200 ofertas activas, las que tienen algún candidato
            #     cross-source son 0/200 a 0.95, 0/200 a 0.86 y 2/200 incluso a
            #     0.80 — y los pares CROSS-IDIOMA son **0 a los cuatro
            #     umbrales**. No hay tráfico que atender.
            #  3. El precio de equivocarse no es cosmético: `mark_duplicate`
            #     escribe `duplicate_of` **y `is_active=False`**. La oferta
            #     desaparece del catálogo y del matching. Este proyecto ya
            #     sufrió ese incidente una vez (664 vacantes reales
            #     recuperadas), y el gatillo aquí estaba documentado en el
            #     propio mensaje del commit: «baja el umbral».
            #
            # COTA ACEPTADA, por escrito: la misma vacante publicada en dos
            # idiomas NO se deduplica por esta vía (falso negativo; su dedup
            # cross-source real lo cubre `fuzzy_hash` cuando el título coincide).
            # Es el lado seguro: un duplicado que sobrevive se ve en el
            # catálogo; una vacante real desactivada, no.
            #
            # Si alguna vez hace falta reabrirlo, NO basta con bajar el umbral.
            # El discriminante que sí cruza idiomas es el coseno de los TÍTULOS
            # SOLOS (sin boilerplate): medido sobre 8 pares reales DE/FR y 8
            # falsos del mismo municipio, separa en el sentido correcto
            # —min(reales)=0.6067 > max(falsos)=0.5033—, al contrario que el
            # coseno del texto completo. Haría falta ese discriminante Y su
            # propio umbral Y abrir el prefiltro; y por (2) hoy no tendría
            # ningún par sobre el que actuar.
            if Deduplicator._title_overlap(job.title, row.title) < overlap_min:
                continue
            if Deduplicator._cantons_conflict(job.canton, row.canton):
                continue
            if Deduplicator._salaries_conflict(job, row):
                continue
            return [row.hash]
        return []
