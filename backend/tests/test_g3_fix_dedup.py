"""G3 — LOTE E: deduplicación y repositorio de ofertas.

Mordidas de los cuatro hallazgos:
- G3/P1-2: el coseno solo ve los primeros 128 tokens (boilerplate del
  empleador) y marcaba como duplicadas vacantes REALMENTE distintas.
- G3/P2-12: `compute_fuzzy_hash` degeneraba con `company=""`.
- G3/P3-8: el upsert invalidaba el embedding por unas tags que nunca entran
  al encoder.
- G3/P3-10: una oferta desactivada por URL muerta resucitaba y no se volvía
  a sondear en toda la rotación.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from models.job import Job
from services.deduplicator import Deduplicator
from services.job_repository import JobRepository

# Boilerplate real de un empleador suizo: es lo ÚNICO que el encoder ve de una
# oferta con descripción larga, así que dos vacantes distintas del mismo
# empleador acaban con vectores casi idénticos (medido en vivo: 0.9708,
# saturado — añadir más texto no baja el coseno). Aquí se simula con el caso
# límite del informe: vectores IDÉNTICOS (coseno 1.0).
_BOILERPLATE = (
    "Die Stadt Musterhausen ist eine moderne Arbeitgeberin mit rund 3000 "
    "Mitarbeitenden. Wir bieten fortschrittliche Anstellungsbedingungen, "
    "flexible Arbeitszeiten, Weiterbildungsmoeglichkeiten und ein "
    "wertschaetzendes Arbeitsklima. Werden Sie Teil unseres Teams. "
)
_SAME_VECTOR = [0.1] * 384


def _vacancy(hash_, source, title, body, **kw):
    """Oferta con el boilerplate del empleador por delante del cuerpo real."""
    return Job(
        hash=hash_.ljust(32, "0"),
        source=source,
        title=title,
        company="Stadt Musterhausen",
        url=f"http://example.com/{hash_}",
        description=_BOILERPLATE * 3 + body,
        embedding=_SAME_VECTOR,
        is_active=True,
        **kw,
    )


class TestSemanticDedupNeedsSecondCondition:
    """G3/P1-2: el coseno es prefiltro; el veredicto lo dan título/cantón/salario."""

    async def test_boilerplate_twins_are_not_duplicates(self, db_session):
        """Dos vacantes DISTINTAS del mismo empleador, cross-source, con el
        mismo vector por culpa del boilerplate compartido: NO son duplicadas."""
        canonical = _vacancy(
            "g3p12canon",
            "publicjobs",
            "Sachbearbeiter Finanzbuchhaltung 80-100%",
            "Ihre Aufgaben: Fuehrung der Finanzbuchhaltung, Debitoren, MWST.",
        )
        candidate = _vacancy(
            "g3p12dup",
            "schuljobs",
            "Gaertner Gruenflaechenunterhalt 100%",
            "Ihre Aufgaben: Pflege der Gruenflaechen, Baumschnitt, Winterdienst.",
        )
        db_session.add_all([canonical, candidate])
        await db_session.commit()

        assert await Deduplicator.find_semantic_duplicates(db_session, candidate) == []

    async def test_same_vacancy_is_still_deduplicated(self, db_session):
        """El propósito de la función sigue intacto: la MISMA vacante sindicada
        a dos portales sí se deduplica (el fix no apaga el dedup)."""
        canonical = _vacancy(
            "g3p12same1",
            "publicjobs",
            "Sachbearbeiter Finanzbuchhaltung 80-100%",
            "Ihre Aufgaben: Fuehrung der Finanzbuchhaltung, Debitoren, MWST.",
        )
        candidate = _vacancy(
            "g3p12same2",
            "schuljobs",
            "Sachbearbeiter/in Finanzbuchhaltung (m/w/d) 80-100%",
            "Ihre Aufgaben: Fuehrung der Finanzbuchhaltung, Debitoren, MWST.",
        )
        db_session.add_all([canonical, candidate])
        await db_session.commit()

        result = await Deduplicator.find_semantic_duplicates(db_session, candidate)
        assert result == [canonical.hash]

    async def test_real_duplicate_found_behind_a_boilerplate_twin(self, db_session):
        """El gemelo de boilerplate es el candidato MÁS ANTIGUO: con `limit(1)`
        se devolvía a él y el duplicado real se perdía. El prefiltro debe mirar
        más allá del primero."""
        now = datetime.now(timezone.utc)
        twin = _vacancy(
            "g3p12twin",
            "publicjobs",
            "Gaertner Gruenflaechenunterhalt 100%",
            "Ihre Aufgaben: Pflege der Gruenflaechen, Baumschnitt.",
            first_seen_at=now - timedelta(days=10),
        )
        real = _vacancy(
            "g3p12real",
            "myscience",
            "Sachbearbeiter Finanzbuchhaltung 80-100%",
            "Ihre Aufgaben: Fuehrung der Finanzbuchhaltung, Debitoren, MWST.",
            first_seen_at=now - timedelta(days=5),
        )
        candidate = _vacancy(
            "g3p12cand",
            "schuljobs",
            "Sachbearbeiter Finanzbuchhaltung 80-100%",
            "Ihre Aufgaben: Fuehrung der Finanzbuchhaltung, Debitoren, MWST.",
            first_seen_at=now,
        )
        db_session.add_all([twin, real, candidate])
        await db_session.commit()

        result = await Deduplicator.find_semantic_duplicates(db_session, candidate)
        assert result == [real.hash]

    async def test_conflicting_canton_blocks_the_verdict(self, db_session):
        """Mismo título y mismo vector, pero cantones distintos: no puede ser la
        misma vacante sindicada."""
        canonical = _vacancy(
            "g3p12cant1",
            "publicjobs",
            "Primarlehrperson 60%",
            "Ihre Aufgaben: Unterricht in der Primarschule.",
            canton="ZH",
        )
        candidate = _vacancy(
            "g3p12cant2",
            "schuljobs",
            "Primarlehrperson 60%",
            "Ihre Aufgaben: Unterricht in der Primarschule.",
            canton="GE",
        )
        db_session.add_all([canonical, candidate])
        await db_session.commit()

        assert await Deduplicator.find_semantic_duplicates(db_session, candidate) == []

    async def test_disjoint_salaries_block_the_verdict(self, db_session):
        """Horquillas salariales declaradas que no se solapan: vacantes distintas."""
        canonical = _vacancy(
            "g3p12sal1",
            "publicjobs",
            "Primarlehrperson 60%",
            "Ihre Aufgaben: Unterricht in der Primarschule.",
            salary_min_chf=60000,
            salary_max_chf=70000,
        )
        candidate = _vacancy(
            "g3p12sal2",
            "schuljobs",
            "Primarlehrperson 60%",
            "Ihre Aufgaben: Unterricht in der Primarschule.",
            salary_min_chf=110000,
            salary_max_chf=130000,
        )
        db_session.add_all([canonical, candidate])
        await db_session.commit()

        assert await Deduplicator.find_semantic_duplicates(db_session, candidate) == []


class TestDegenerateFuzzyIdentity:
    """G3/P2-12: sin empresa (o sin título útil) NO hay identidad fuzzy."""

    def test_empty_company_yields_no_identity(self):
        """Tres títulos distintos sin empresa compartían hash (un solo bucket)."""
        hashes = {
            Deduplicator.compute_fuzzy_hash(title, "")
            for title in (
                "Customer Support Specialist",
                "Data Annotator",
                "Gaertner Gruenflaechenunterhalt",
            )
        }
        assert hashes == {""}

    def test_seniority_only_title_yields_no_identity(self):
        """Un título de solo seniority normaliza a vacío: MD5("|") metía TODO
        el corpus en el mismo bucket."""
        assert Deduplicator.compute_fuzzy_hash("Senior", "Acme AG") == ""

    def test_real_identity_still_hashes(self):
        """La identidad buena sigue produciendo un hash estable y distinto."""
        good = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme AG")
        assert good and good != Deduplicator.compute_fuzzy_hash("Gaertner", "Acme AG")

    async def test_empty_hash_never_matches(self, db_session):
        """`find_fuzzy_duplicate` debe ignorar el hash vacío (marca de
        identidad degenerada), no usarlo como clave de búsqueda."""
        db_session.add(
            Job(
                hash="g3p212degenerate".ljust(32, "0"),
                source="jobicy",
                title="Customer Support Specialist",
                company="",
                url="http://example.com/g3p212",
                fuzzy_hash="",
                is_active=True,
            )
        )
        await db_session.commit()

        assert await Deduplicator.find_fuzzy_duplicate(db_session, "", "jooble") is None


def _job_dict(**overrides):
    base = {
        "hash": "g3e0000000000000000000000000abcd",  # 32 chars
        "source": "test_provider",
        "title": "Sachbearbeiter Finanzbuchhaltung",
        "company": "Stadt Musterhausen",
        "url": "https://example.com/g3/lote-e/1",
        "location": "Zurich, ZH",
        "canton": "ZH",
        "description": "Build Python APIs",
        "description_snippet": "Build Python APIs",
        "remote": False,
        "tags": ["python"],
        "language": "de",
        "employment_type": "Full-Time",
        "fuzzy_hash": "fedcba9876543210fedcba9876543210",
    }
    base.update(overrides)
    return base


# Descripción larga real (>1000 chars): agota por sí sola los 128 tokens del
# encoder, así que ninguna tag posterior puede influir en el vector.
_LONG_DESCRIPTION = _BOILERPLATE * 4


class TestEmbeddingInvalidationMatchesTheEncoder:
    """G3/P3-8: invalidar solo por lo que de verdad entra al encoder."""

    async def test_tags_change_with_long_description_preserves_embedding(
        self, db_session
    ):
        """Con descripción larga, cambiar las tags produce el MISMO vector: el
        NULL solo escondía la oferta del matching y del dedup hasta el
        siguiente embed_all_pending."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        await repo.upsert_job(_job_dict(description=_LONG_DESCRIPTION, tags=["python"]))
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.5] * 384)
        )
        await db_session.commit()

        await repo.upsert_job(
            _job_dict(description=_LONG_DESCRIPTION, tags=["python", "sap", "excel"])
        )
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.tags, Job.embedding).where(Job.hash == h)
            )
        ).one()
        assert row.tags == ["python", "sap", "excel"]  # las tags sí se refrescan
        assert row.embedding is not None  # el vector no cambiaría: no se tira

    async def test_tags_change_with_short_description_invalidates_embedding(
        self, db_session
    ):
        """Con descripción corta las tags SÍ entran en la ventana del encoder:
        el embedding debe invalidarse (el fix no relaja de más)."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        await repo.upsert_job(_job_dict(description="Kurzbeschrieb", tags=["python"]))
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.5] * 384)
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="Kurzbeschrieb", tags=["sap"]))
        await db_session.commit()

        emb = (
            await db_session.execute(select(Job.embedding).where(Job.hash == h))
        ).scalar_one()
        assert emb is None

    async def test_long_description_change_still_invalidates_embedding(
        self, db_session
    ):
        """La descripción sí entra al encoder: cambiarla invalida siempre."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        await repo.upsert_job(_job_dict(description=_LONG_DESCRIPTION, tags=["python"]))
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.5] * 384)
        )
        await db_session.commit()

        await repo.upsert_job(
            _job_dict(
                description=_LONG_DESCRIPTION + "Neu: Homeoffice.", tags=["python"]
            )
        )
        await db_session.commit()

        emb = (
            await db_session.execute(select(Job.embedding).where(Job.hash == h))
        ).scalar_one()
        assert emb is None


class TestReactivationReopensUrlProbe:
    """G3/P3-10: una oferta resucitada vuelve a la cabecera de la rotación."""

    async def test_reactivated_job_clears_url_last_check(self, db_session):
        """Desactivada por 404/410 y re-listada por el portal: el upsert la
        reactiva, así que su sondeo debe reabrirse (url_last_check=NULL) en vez
        de esperar a que la rotación complete el catálogo entero."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        await repo.upsert_job(_job_dict())
        await db_session.commit()
        # check_job_urls la mató: is_active=False + url_last_check fechado.
        await db_session.execute(
            update(Job)
            .where(Job.hash == h)
            .values(is_active=False, url_last_check=datetime.now(timezone.utc))
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict())
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.is_active, Job.url_last_check).where(Job.hash == h)
            )
        ).one()
        assert row.is_active is True  # sigue reactivándose (comportamiento previo)
        assert row.url_last_check is None  # y se re-sondea en el próximo barrido

    async def test_active_job_keeps_its_rotation_slot(self, db_session):
        """Una re-vista normal (la oferta ya estaba activa) NO reinicia la
        rotación: si no, cada cosecha diaria mandaría el catálogo entero a la
        cabecera de check_job_urls."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        checked_at = datetime.now(timezone.utc) - timedelta(days=2)
        await repo.upsert_job(_job_dict())
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(url_last_check=checked_at)
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="Neuer Text"))
        await db_session.commit()

        stored = (
            await db_session.execute(select(Job.url_last_check).where(Job.hash == h))
        ).scalar_one()
        assert stored is not None

    async def test_duplicate_revisit_keeps_its_probe_mark(self, db_session):
        """Un duplicado re-visto no se reactiva, así que tampoco reabre sondeo."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        checked_at = datetime.now(timezone.utc) - timedelta(days=2)
        await repo.upsert_job(_job_dict())
        await db_session.commit()
        await repo.mark_duplicate(h, "canonical_hash_098765432109")
        await db_session.execute(
            update(Job).where(Job.hash == h).values(url_last_check=checked_at)
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict())
        await db_session.commit()

        stored = (
            await db_session.execute(select(Job.url_last_check).where(Job.hash == h))
        ).scalar_one()
        assert stored is not None
