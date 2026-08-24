"""Tests for JobRepository — upsert, dedup marking, and active counts."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from models.job import Job
from services.job_repository import JobRepository


def _job_dict(**overrides):
    base = {
        "hash": "abc123def456abc123def456abc12345",  # 32 chars
        "source": "test_provider",
        "title": "Python Developer",
        "company": "Acme Corp",
        "url": "https://example.com/job/1",
        "location": "Zurich, ZH",
        "canton": "ZH",
        "description": "Build Python APIs",
        "description_snippet": "Build Python APIs",
        "remote": False,
        "tags": ["python", "fastapi"],
        "logo": None,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": "en",
        "seniority": None,
        "contract_type": None,
        "employment_type": "Full-Time",
        "fuzzy_hash": "fedcba9876543210fedcba9876543210",
    }
    base.update(overrides)
    return base


@pytest.mark.anyio
class TestJobRepository:
    """Unit tests for JobRepository against a real test database."""

    async def test_upsert_new_returns_true(self, db_session):
        """Inserting a brand-new job must return True."""
        repo = JobRepository(db_session)
        is_new = await repo.upsert_job(_job_dict())
        await db_session.commit()
        assert is_new is True

    async def test_upsert_existing_returns_false(self, db_session):
        """Upserting the same hash a second time must return False."""
        repo = JobRepository(db_session)
        await repo.upsert_job(_job_dict())
        await db_session.commit()

        is_new = await repo.upsert_job(_job_dict())
        await db_session.commit()
        assert is_new is False

    async def test_upsert_refreshes_content_on_conflict(self, db_session):
        """PF.1: una oferta re-vista con contenido cambiado debe ACTUALIZARSE,
        no congelarse (antes solo se tocaba last_seen_at/is_active)."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="Old description", tags=["a"]))
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="New description", tags=["b"]))
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.description, Job.tags).where(Job.hash == h)
            )
        ).one()
        assert row.description == "New description"
        assert row.tags == ["b"]

    async def test_upsert_refreshes_last_seen_at(self, db_session):
        """A second upsert must bump last_seen_at to a newer timestamp."""
        repo = JobRepository(db_session)
        data = _job_dict()

        await repo.upsert_job(data)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        first_seen = row

        # Second upsert — last_seen_at should be refreshed
        await repo.upsert_job(data)
        await db_session.commit()

        # Expire cached attributes so we re-read from DB
        await db_session.flush()
        row2 = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        assert row2 >= first_seen

    async def test_upsert_reactivates_archived_job(self, db_session):
        """Re-upserting una oferta ARCHIVADA (is_active=False, SIN duplicate_of)
        debe reactivarla."""
        repo = JobRepository(db_session)
        data = _job_dict()
        await repo.upsert_job(data)
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == data["hash"]).values(is_active=False)
        )
        await db_session.commit()

        await repo.upsert_job(data)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.is_active).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        assert row is True  # archivada sin duplicate_of → reactiva

    async def test_upsert_does_not_reactivate_duplicate(self, db_session):
        """Re-upserting un DUPLICADO (duplicate_of set) NO debe reactivarlo — así no
        vuelve a los feeds."""
        repo = JobRepository(db_session)
        data = _job_dict()
        await repo.upsert_job(data)
        await db_session.commit()
        await repo.mark_duplicate(data["hash"], "canonical_dup_0000000000000000")
        await db_session.commit()

        await repo.upsert_job(data)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.is_active).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        assert row is False  # sigue inactivo (es duplicado)

    async def test_mark_duplicate_sets_fields(self, db_session):
        """mark_duplicate must set duplicate_of and deactivate the job."""
        repo = JobRepository(db_session)
        data = _job_dict()
        canonical = "canonical_hash_12345678901234567"

        await repo.upsert_job(data)
        await db_session.commit()

        await repo.mark_duplicate(data["hash"], canonical)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.duplicate_of, Job.is_active).where(Job.hash == data["hash"])
            )
        ).one()
        assert row.duplicate_of == canonical
        assert row.is_active is False

    async def test_get_active_count_only_active(self, db_session):
        """get_active_count must count only is_active=True jobs."""
        repo = JobRepository(db_session)

        # Insert 3 distinct jobs (hash must be exactly 32 chars)
        for i in range(3):
            h = f"a{i}" + "0" * 30
            await repo.upsert_job(
                _job_dict(hash=h[:32], url=f"https://example.com/job/{i}")
            )
        await db_session.commit()

        count = await repo.get_active_count()
        assert count == 3

    async def test_get_active_count_excludes_duplicates(self, db_session):
        """Duplicates (is_active=False) must not be counted."""
        repo = JobRepository(db_session)

        hashes = []
        for i in range(3):
            h = (f"b{i}" + "0" * 30)[:32]
            hashes.append(h)
            await repo.upsert_job(
                _job_dict(hash=h, url=f"https://example.com/job/d{i}")
            )
        await db_session.commit()

        # Mark first as duplicate of second
        await repo.mark_duplicate(hashes[0], hashes[1])
        await db_session.commit()

        count = await repo.get_active_count()
        assert count == 2

    async def test_get_active_count_excludes_reactivated_duplicate(self, db_session):
        """Un duplicado re-visto vuelve a is_active=True (intencional) pero, como
        conserva duplicate_of, NO debe contarse como activo."""
        repo = JobRepository(db_session)
        data = _job_dict()
        await repo.upsert_job(data)
        await db_session.commit()
        await repo.mark_duplicate(data["hash"], "canonical_hash_098765432109")
        await db_session.commit()
        # Re-upsert reactiva is_active=True pero duplicate_of se conserva.
        await repo.upsert_job(data)
        await db_session.commit()

        assert await repo.get_active_count() == 0

    async def test_upsert_handles_all_valid_columns(self, db_session):
        """upsert_job must persist every column present on the Job model."""
        repo = JobRepository(db_session)
        data = _job_dict(
            salary_min_chf=80000,
            salary_max_chf=120000,
            salary_original="80k-120k CHF",
            salary_currency="CHF",
            logo="https://example.com/logo.png",
            employment_type="Part-Time",
            remote=True,
            # Include an extra key that does NOT exist on the model:
            nonexistent_field="should_be_ignored",
        )

        is_new = await repo.upsert_job(data)
        await db_session.commit()
        assert is_new is True

        row = (
            await db_session.execute(select(Job).where(Job.hash == data["hash"]))
        ).scalar_one()

        assert row.title == "Python Developer"
        assert row.company == "Acme Corp"
        assert row.salary_min_chf == 80000
        assert row.salary_max_chf == 120000
        assert row.salary_original == "80k-120k CHF"
        assert row.salary_currency == "CHF"
        assert row.logo == "https://example.com/logo.png"
        assert row.employment_type == "Part-Time"
        assert row.remote is True
        assert row.location == "Zurich, ZH"
        assert row.canton == "ZH"
        assert row.language == "en"
        assert row.tags == ["python", "fastapi"]
        assert row.fuzzy_hash == "fedcba9876543210fedcba9876543210"
        assert row.is_active is True
        # Ensure nonexistent_field was silently ignored (no AttributeError)
        assert not hasattr(row, "nonexistent_field")


@pytest.mark.anyio
class TestUpsertContentVersioning:
    """PF.1: content_hash + invalidación de embedding al cambiar el contenido."""

    async def test_content_hash_set_on_insert(self, db_session):
        repo = JobRepository(db_session)
        await repo.upsert_job(_job_dict())
        await db_session.commit()

        ch = (
            await db_session.execute(
                select(Job.content_hash).where(Job.hash == _job_dict()["hash"])
            )
        ).scalar_one()
        assert ch is not None
        assert len(ch) == 32

    async def test_embedding_invalidated_when_content_changes(self, db_session):
        """Contenido cambiado -> embedding=NULL para forzar re-embed + re-match."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="v1"))
        await db_session.commit()
        # Simular el embedding ya generado por el pipeline.
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.1] * 384)
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="v2"))
        await db_session.commit()

        emb = (
            await db_session.execute(select(Job.embedding).where(Job.hash == h))
        ).scalar_one()
        assert emb is None

    async def test_embedding_preserved_when_content_unchanged(self, db_session):
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="same"))
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.2] * 384)
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="same"))
        await db_session.commit()

        emb = (
            await db_session.execute(select(Job.embedding).where(Job.hash == h))
        ).scalar_one()
        assert emb is not None

    async def test_embedding_preserved_when_prior_content_hash_null(self, db_session):
        """Fila anterior a la columna (content_hash=NULL): el re-upsert NO invalida
        el embedding — evita un re-embed masivo del corpus al desplegar la columna;
        solo empieza a rastrear el hash de ahí en adelante."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="v1"))
        await db_session.commit()
        # Simular fila migrada: content_hash NULL + embedding ya generado.
        await db_session.execute(
            update(Job)
            .where(Job.hash == h)
            .values(content_hash=None, embedding=[0.3] * 384)
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="v1"))  # mismo contenido
        await db_session.commit()

        emb = (
            await db_session.execute(select(Job.embedding).where(Job.hash == h))
        ).scalar_one()
        assert emb is not None  # conservado

    async def test_embedding_invalidated_when_prior_null_and_text_changed(
        self, db_session
    ):
        """Fila anterior a la columna (content_hash=NULL) cuyo TEXTO cambia: el
        embedding SÍ se invalida (no queda obsoleto)."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        await repo.upsert_job(_job_dict(description="legacy-v1"))
        await db_session.commit()
        await db_session.execute(
            update(Job)
            .where(Job.hash == h)
            .values(content_hash=None, embedding=[0.4] * 384)
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="provider-v2"))  # texto cambia
        await db_session.commit()

        emb = (
            await db_session.execute(select(Job.embedding).where(Job.hash == h))
        ).scalar_one()
        assert emb is None  # invalidado

    async def test_embedding_preserved_on_non_text_field_change(self, db_session):
        """Cambiar un campo que NO entra en el texto embebido (logo/salario) NO
        invalida el embedding (evita re-embeber texto idéntico)."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        await repo.upsert_job(_job_dict(description="stable", logo=None))
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.5] * 384)
        )
        await db_session.commit()

        # Mismo description/tags; cambian logo y salario (fuera de build_job_text).
        await repo.upsert_job(
            _job_dict(description="stable", logo="new-logo", salary_min_chf=99000)
        )
        await db_session.commit()

        emb = (
            await db_session.execute(select(Job.embedding).where(Job.hash == h))
        ).scalar_one()
        assert emb is not None  # conservado


@pytest.mark.anyio
class TestUpsertDescription:
    """VD.9/H2: una description vacía entrante NO pisa una existente no vacía."""

    async def test_reupsert_with_empty_description_preserves_stored(self, db_session):
        """Un fallo parcial del detalle (p. ej. thehub) emite la re-vista con
        description="" — sin el COALESCE (patrón published_at + NULLIF) la
        description buena se perdía, cambiaba el content_hash y el embedding
        pasaba a NULL para re-embeberse sobre texto vacío (matching
        degradado) y otra vez al recuperarse el detalle. Se comprueba que la
        description se conserva Y que el embedding NO se invalida (el texto
        efectivo de la fila no ha cambiado)."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="Good detailed text"))
        await db_session.commit()
        # Simular el embedding ya generado por el pipeline.
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.6] * 384)
        )
        await db_session.commit()

        # Re-vista degradada: el detalle falló y llega sin description.
        await repo.upsert_job(_job_dict(description="", description_snippet=None))
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.description, Job.embedding).where(Job.hash == h)
            )
        ).one()
        assert row.description == "Good detailed text"  # conservada, no ""
        assert row.embedding is not None  # sin churn de re-embed

    async def test_reupsert_with_new_description_still_updates(self, db_session):
        """El COALESCE solo protege contra el vacío entrante: una description
        no vacía fresca sigue refrescando la fila (PF.1 intacto)."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="v1"))
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="v2"))
        await db_session.commit()

        stored = (
            await db_session.execute(select(Job.description).where(Job.hash == h))
        ).scalar_one()
        assert stored == "v2"


@pytest.mark.anyio
class TestUpsertDegradedRevisit:
    """VD.9/V2-1: la re-vista degradada (detalle fallido, p. ej. thehub) no
    debe machacar snippet, tags ni location/canton — la protección de la ronda
    anterior solo cubría description. Réplica de la matriz C5/C9/C10 ejecutada
    contra BD real."""

    async def test_c9_degraded_revisit_preserves_tags_and_embedding(self, db_session):
        """C9: las tags salen de extract_job_skills(title, description) — un
        detalle fallido las degrada a []. Señal (description entrante vacía Y
        tags entrantes vacías) ⇒ conservar Job.tags y NO invalidar el
        embedding: sin esto había re-embed sobre tags degradadas y OTRO
        re-embed al recuperarse el detalle."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(
            _job_dict(
                description="Fluent in English and Spanish", tags=["english", "spanish"]
            )
        )
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.7] * 384)
        )
        await db_session.commit()

        # Re-vista degradada: sin detalle no hay description ⇒ tags a [].
        await repo.upsert_job(
            _job_dict(description="", description_snippet=None, tags=[])
        )
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.tags, Job.embedding).where(Job.hash == h)
            )
        ).one()
        assert row.tags == ["english", "spanish"]  # conservadas, no []
        assert row.embedding is not None  # sin churn de re-embed

    async def test_c9_empty_tags_with_real_description_still_update(self, db_session):
        """La señal exige AMBOS vacíos: con description entrante real, unas
        tags vacías son un re-cálculo legítimo y SÍ pisan — ninguna fuente
        pierde la capacidad de vaciar tags con texto presente."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="Some text", tags=["python"]))
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="Some text", tags=[]))
        await db_session.commit()

        stored = (
            await db_session.execute(select(Job.tags).where(Job.hash == h))
        ).scalar_one()
        assert stored == []

    async def test_c5_degraded_revisit_preserves_snippet(self, db_session):
        """C5: el snippet entrante degradado llega como NULL (_snippet("") →
        None) y machacaba el snippet bueno — la UI perdía el extracto hasta el
        siguiente run sano."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(
            _job_dict(
                description="Good detailed text",
                description_snippet="Good detailed text",
            )
        )
        await db_session.commit()

        await repo.upsert_job(_job_dict(description="", description_snippet=None))
        await db_session.commit()

        stored = (
            await db_session.execute(
                select(Job.description_snippet).where(Job.hash == h)
            )
        ).scalar_one()
        assert stored == "Good detailed text"  # conservado, no NULL

    async def test_c10_degraded_revisit_preserves_location_and_canton(self, db_session):
        """C10: el listado v2 sin detalle trae location {} ⇒ location "" y
        canton None machacaban la ubicación buena (canton alimenta filtros)."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(location="Zurich, ZH", canton="ZH"))
        await db_session.commit()

        await repo.upsert_job(
            _job_dict(
                description="", description_snippet=None, location="", canton=None
            )
        )
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.location, Job.canton).where(Job.hash == h)
            )
        ).one()
        assert row.location == "Zurich, ZH"
        assert row.canton == "ZH"

    async def test_h1_solo_espacios_cuenta_como_vacio_y_no_destruye(self, db_session):
        """Fase 3 r3/H1: para NULLIF(valor, '') un "   " o un "\\t" son datos
        REALES, y además hacen falsa la señal que protege tags
        (coalesce(excluded.description,'') == ''): la re-vista degradada con
        blancos dejaba ('   ', '\\t', [], '  ', None, None) — perdía
        description, snippet, tags, location y canton E invalidaba el
        embedding. Los solo-espacios se normalizan a "" en la frontera y la
        cascada de protecciones existente vuelve a actuar."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(
            _job_dict(
                description="Good detailed text",
                description_snippet="Good detailed text",
                tags=["python", "fastapi"],
                location="Zurich, ZH",
                canton="ZH",
            )
        )
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.7] * 384)
        )
        await db_session.commit()

        # La re-vista del hallazgo: blancos en vez de vacíos.
        await repo.upsert_job(
            _job_dict(
                description="   ",
                description_snippet="\t",
                tags=[],
                location="  ",
                canton=None,
            )
        )
        await db_session.commit()

        row = (
            await db_session.execute(
                select(
                    Job.description,
                    Job.description_snippet,
                    Job.tags,
                    Job.location,
                    Job.canton,
                    Job.embedding,
                ).where(Job.hash == h)
            )
        ).one()
        assert row.description == "Good detailed text"
        assert row.description_snippet == "Good detailed text"
        assert row.tags == ["python", "fastapi"]
        assert row.location == "Zurich, ZH"
        assert row.canton == "ZH"
        assert row.embedding is not None  # sin re-embed sobre texto degradado

        # Control (G2/G4): un string NO vacío con espacios alrededor no se
        # recorta — cambiar el contenido real cambiaría content_hash y el
        # material del embedding.
        await repo.upsert_job(_job_dict(description=" nuevo texto "))
        await db_session.commit()
        stored = (
            await db_session.execute(select(Job.description).where(Job.hash == h))
        ).scalar_one()
        assert stored == " nuevo texto "

    async def test_s1_alta_con_blancos_almacena_cadena_vacia(self, db_session):
        """Fase 4 r4/S1 — mata al mutante superviviente de r3/H1: hacer
        `values.pop(field)` en vez de asignar "" pasaba el test de la
        re-vista degradada. La diferencia observable está en un ALTA con
        blancos: con pop la columna queda NULL en vez de "" y el
        content_hash difiere. Se fija el valor ALMACENADO."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(
            _job_dict(description="   ", description_snippet="\t", location="  ")
        )
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.description, Job.description_snippet, Job.location).where(
                    Job.hash == h
                )
            )
        ).one()
        # "" y no NULL: los blancos se NORMALIZAN en la frontera, no se
        # eliminan del payload.
        assert row.description == ""
        assert row.description_snippet == ""
        assert row.location == ""

    async def test_c10_real_location_updates_and_canton_follows(self, db_session):
        """Decisión V2-1: una location entrante REAL siempre pasa (reubicación
        legítima) y entonces el canton entrante manda AUNQUE sea None — mejor
        sin cantón que un cantón obsoleto de la ubicación anterior."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(location="Zurich, ZH", canton="ZH"))
        await db_session.commit()

        await repo.upsert_job(_job_dict(location="Remote (Europe)", canton=None))
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.location, Job.canton).where(Job.hash == h)
            )
        ).one()
        assert row.location == "Remote (Europe)"
        assert row.canton is None  # el canton entrante manda con location real

    async def test_tags_none_does_not_destroy_stored_text_state(self, db_session):
        """Fase 3 r2/H3: SQLAlchemy serializa tags=None como `null` JSONB y
        jsonb_array_length(excluded.tags) sobre un escalar abortaba el
        savepoint en PostgreSQL — la oferta NO se persistía. None se
        normaliza a [] en la frontera: el upsert completa y, con description
        entrante vacía, el CASE existente conserva las tags buenas."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="Real text", tags=["python"]))
        await db_session.commit()

        # Re-vista degradada con tags=None: ni aborta ni machaca.
        await repo.upsert_job(
            _job_dict(description="", description_snippet=None, tags=None)
        )
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.tags, Job.description).where(Job.hash == h)
            )
        ).one()
        assert row.tags == ["python"]  # conservadas, no perdidas ni null
        assert row.description == "Real text"

        # Y en un ALTA, tags=None almacena una lista válida ([]), no null.
        alta = _job_dict(
            hash="ffffffffffffffffffffffffffffffff",
            url="https://example.com/job/none-tags",
            tags=None,
        )
        await repo.upsert_job(alta)
        await db_session.commit()
        stored = (
            await db_session.execute(select(Job.tags).where(Job.hash == alta["hash"]))
        ).scalar_one()
        assert stored == []

    async def test_tags_no_lista_se_rechaza_en_la_frontera(self, db_session):
        """Fase 3 r2/H3 (fija la decisión, no discrimina un comportamiento
        externo): un `tags` no-lista (p. ej. una cadena) es un bug del
        productor, no un dato degradado — coaccionarlo a [] podría machacar
        tags buenas con description real. Se rechaza con ValueError ANTES de
        tocar la BD; ningún productor actual emite no-listas."""
        repo = JobRepository(db_session)
        with pytest.raises(ValueError, match="tags"):
            await repo.upsert_job(_job_dict(tags="python"))

    async def test_location_real_sin_clave_canton_anula_canton_obsoleto(
        self, db_session
    ):
        """Fase 3 r2/H5: cambiar location OMITIENDO la clave canton dejaba el
        cantón de la ubicación ANTERIOR alimentando los filtros geográficos —
        contradice la semántica V2-1/C10 ("mejor sin cantón que un cantón
        obsoleto"). Con location entrante real y canton ausente ⇒ canton NULL;
        con location entrante vacía se conservan ambos."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(location="Zurich, ZH", canton="ZH"))
        await db_session.commit()

        entrante = _job_dict(location="Remote (Europe)")
        del entrante["canton"]
        await repo.upsert_job(entrante)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.location, Job.canton).where(Job.hash == h)
            )
        ).one()
        assert row.location == "Remote (Europe)"
        assert row.canton is None  # NULL, no el "ZH" obsoleto

        # Con location entrante VACÍA y canton omitido: fetch degradado, se
        # conservan la ubicación y el cantón buenos (sin falso positivo G2).
        await repo.upsert_job(_job_dict(location="Basel, BS", canton="BS"))
        await db_session.commit()
        degradado = _job_dict(location="", description="", description_snippet=None)
        del degradado["canton"]
        await repo.upsert_job(degradado)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.location, Job.canton).where(Job.hash == h)
            )
        ).one()
        assert row.location == "Basel, BS"
        assert row.canton == "BS"


@pytest.mark.anyio
class TestUpsertProtectionEdges:
    """Fase 3/H4: dos bordes de la protección del upsert que se escapaban.

    (1) Un description_snippet="" entrante pisaba el snippet bueno: el
    COALESCE solo cubría NULL. (2) Un campo AUSENTE del payload hacía que
    effective_description/effective_tags fueran excluded.<campo> = NULL y el
    is_distinct_from contra la columna invalidara el embedding aunque el
    texto efectivo de la fila no cambiara."""

    async def test_empty_string_snippet_does_not_clobber_stored(self, db_session):
        """Borde 1: `_snippet("")` devuelve None, pero quien asigne "" a mano
        también es un entrante degradado — el NULLIF debe equipararlo a NULL
        y conservar el snippet bueno."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(
            _job_dict(
                description="Good detailed text",
                description_snippet="Good detailed text",
            )
        )
        await db_session.commit()

        await repo.upsert_job(
            _job_dict(description="Good detailed text", description_snippet="")
        )
        await db_session.commit()

        stored = (
            await db_session.execute(
                select(Job.description_snippet).where(Job.hash == h)
            )
        ).scalar_one()
        assert stored == "Good detailed text"  # conservado, no ""

    async def test_omitted_description_preserves_embedding(self, db_session):
        """Borde 2a: payload SIN la clave description — el ON CONFLICT no toca
        la columna, así que el embedding NO debe invalidarse (el texto
        efectivo de la fila es idéntico). Antes, excluded.description = NULL
        era "distinto" del texto almacenado y el embedding caía a NULL."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(description="Stable text"))
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.8] * 384)
        )
        await db_session.commit()

        # Payload sin description ni snippet (p. ej. un caller parcial).
        partial = _job_dict()
        del partial["description"]
        del partial["description_snippet"]
        await repo.upsert_job(partial)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.description, Job.embedding).where(Job.hash == h)
            )
        ).one()
        assert row.description == "Stable text"  # la columna no se tocó
        assert row.embedding is not None  # y el embedding tampoco

    async def test_omitted_tags_preserves_embedding(self, db_session):
        """Borde 2b: misma protección para tags — un payload sin la clave no
        cambia las tags efectivas y no debe invalidar el embedding."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]

        await repo.upsert_job(_job_dict(tags=["python", "fastapi"]))
        await db_session.commit()
        await db_session.execute(
            update(Job).where(Job.hash == h).values(embedding=[0.9] * 384)
        )
        await db_session.commit()

        partial = _job_dict()
        del partial["tags"]
        await repo.upsert_job(partial)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.tags, Job.embedding).where(Job.hash == h)
            )
        ).one()
        assert row.tags == ["python", "fastapi"]
        assert row.embedding is not None


@pytest.mark.anyio
class TestUpsertPublishedAt:
    """V.1 / ADR-10: el re-upsert sin fecha NO borra la fecha ya conocida."""

    async def test_reupsert_without_date_preserves_stored_value(self, db_session):
        """COALESCE del on_conflict: si un run posterior no trae la fecha (fallo
        del detalle, cambio de DOM), el NULL entrante no machaca el valor bueno."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        known = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)

        await repo.upsert_job(_job_dict(published_at=known))
        await db_session.commit()

        await repo.upsert_job(_job_dict(published_at=None))
        await db_session.commit()

        stored = (
            await db_session.execute(select(Job.published_at).where(Job.hash == h))
        ).scalar_one()
        assert stored == known  # conservado, no NULL

    async def test_reupsert_with_new_date_updates_value(self, db_session):
        """El COALESCE deja pasar una fecha fresca del portal (corrección aguas
        arriba): solo protege contra el NULL entrante."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        old = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
        new = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)

        await repo.upsert_job(_job_dict(published_at=old))
        await db_session.commit()

        await repo.upsert_job(_job_dict(published_at=new))
        await db_session.commit()

        stored = (
            await db_session.execute(select(Job.published_at).where(Job.hash == h))
        ).scalar_one()
        assert stored == new


@pytest.mark.anyio
class TestUpsertColumnBounds:
    """Fase 3 r3/R11: la cota "cabe en la columna" vive donde vive la columna.
    Solo financejobs acotaba la URL; el resto de scrapers construyen URLs con
    datos del portal sin acotar y un desborde de String(2048) abortaba el
    savepoint con un error del driver. Mismo patrón que el rechazo del tags
    no-lista: degradar ESA oferta con un mensaje claro."""

    async def test_url_desbordada_se_rechaza_con_mensaje_claro(self, db_session):
        """Truncar no es opción: la URL es identidad (hash + ix_jobs_url)."""
        repo = JobRepository(db_session)
        url = "https://example.com/job/" + "9" * 3000
        with pytest.raises(ValueError, match="url excede"):
            await repo.upsert_job(_job_dict(url=url))

    async def test_url_en_el_borde_exacto_se_acepta(self, db_session):
        """G2 del residual: la cota no puede crear falsos rechazos — una URL
        de exactamente 2048 chars cabe y la oferta se persiste."""
        repo = JobRepository(db_session)
        prefix = "https://example.com/job/"
        url = prefix + "9" * (2048 - len(prefix))
        assert len(url) == 2048
        assert await repo.upsert_job(_job_dict(url=url)) is True
        await db_session.commit()

    async def test_logo_desbordado_degrada_solo_el_campo(self, db_session):
        """logo comparte el String(2048) pero es decorativo: un logo
        kilométrico no debe costar la oferta entera — se persiste con NULL."""
        repo = JobRepository(db_session)
        h = _job_dict()["hash"]
        oversized = "https://cdn.example.com/" + "x" * 3000
        assert await repo.upsert_job(_job_dict(logo=oversized)) is True
        await db_session.commit()
        row = (
            await db_session.execute(select(Job.logo).where(Job.hash == h))
        ).scalar_one()
        assert row is None


class TestColumnBoundGuard:
    """Fase 4 r4/R3-4: la cota derivada del modelo debe FALLAR EN EL ARRANQUE
    si la columna pierde la longitud (p. ej. migrada a Text): sin el guard,
    `len(url) > None` lanzaba TypeError en CADA upsert y todas las ofertas se
    degradaban hasta detectarlo."""

    def test_columna_sin_cota_es_fallo_de_arranque(self):
        # TypeError y no AssertionError (r5/H3): un `assert` desaparece bajo
        # `-O`/PYTHONOPTIMIZE y el guard devolvía None en silencio — el raise
        # explícito protege también en ese modo.
        from sqlalchemy import Column, Text

        from services.job_repository import _column_max_len

        with pytest.raises(TypeError, match="sin cota de longitud"):
            _column_max_len(Column("url", Text()))

    def test_cota_actual_derivada_del_modelo(self):
        """Control: con el modelo real el guard devuelve la cota, no peta."""
        from services.job_repository import _column_max_len

        assert _column_max_len(Job.__table__.c.url) == 2048
        assert _column_max_len(Job.__table__.c.logo) == 2048


@pytest.mark.anyio
class TestLogoOversizedRevisit:
    """r6/H4 (G5): la degradación hacía `values["logo"] = None` y ese None
    ENTRABA en el ON CONFLICT pisando el logo bueno almacenado — evidencia
    ejecutada contra Postgres: alta con logo bueno + re-vista con logo de
    3020 caracteres ⇒ LOGO_AFTER_OVERSIZED_REVISIT None. El campo inválido
    ahora se OMITE (`pop`) y no destruye el válido."""

    GOOD_LOGO = "https://cdn.example.com/logo.png"

    async def _stored_logo(self, db_session) -> str | None:
        h = _job_dict()["hash"]
        return (
            await db_session.execute(select(Job.logo).where(Job.hash == h))
        ).scalar_one()

    async def test_logo_desbordado_en_revisita_no_pisa_el_logo_bueno(self, db_session):
        """La evidencia ejecutada de la revisión, ahora en verde: el logo
        bueno sobrevive a la re-vista con logo desbordado."""
        repo = JobRepository(db_session)
        assert await repo.upsert_job(_job_dict(logo=self.GOOD_LOGO)) is True
        await db_session.commit()

        oversized = "https://cdn.example.com/" + "x" * 3000
        assert await repo.upsert_job(_job_dict(logo=oversized)) is False
        await db_session.commit()

        assert await self._stored_logo(db_session) == self.GOOD_LOGO

    async def test_logo_valido_en_revisita_sigue_actualizando(self, db_session):
        """Control (sin cambio de comportamiento): un logo VÁLIDO re-suministrado
        sigue refrescando la columna."""
        repo = JobRepository(db_session)
        assert await repo.upsert_job(_job_dict(logo=self.GOOD_LOGO)) is True
        await db_session.commit()

        new_logo = "https://cdn.example.com/logo-v2.png"
        assert await repo.upsert_job(_job_dict(logo=new_logo)) is False
        await db_session.commit()

        assert await self._stored_logo(db_session) == new_logo

    async def test_none_explicito_del_productor_ya_no_pisa(self, db_session):
        """r7/H5 (G5) — CAMBIO DE PREMISA respecto a la fase anterior: este
        test se llamaba `test_none_explicito_del_productor_sigue_pisando` y
        fijaba que un None explícito era "dato real" que borraba el logo
        almacenado. La quinta revisión tumbó esa premisa: los productores
        construyen el valor con `.get("logo")`, así que NO pueden distinguir
        "el portal retiró el logo" de "este fetch no lo trajo" — tratar None
        como borrado autoritativo interpretaba como intención lo que es
        ausencia de dato. Ahora None/"" preservan el valor bueno; un borrado
        autoritativo exigiría un DTO que distinga "omitido" de "borrado
        explícito" (no implementado a propósito)."""
        repo = JobRepository(db_session)
        assert await repo.upsert_job(_job_dict(logo=self.GOOD_LOGO)) is True
        await db_session.commit()

        assert await repo.upsert_job(_job_dict(logo=None)) is False
        await db_session.commit()

        assert await self._stored_logo(db_session) == self.GOOD_LOGO

    @pytest.mark.parametrize(
        "degraded",
        ["", "   ", 123],
        ids=["vacio", "solo_espacios", "tipo_invalido"],
    )
    async def test_logo_degradado_en_revisita_no_pisa_el_logo_bueno(
        self, db_session, degraded
    ):
        """r7/H5 (G5): "", solo-espacios y un tipo inválido son la misma
        ausencia de dato que None — ninguno destruye el valor almacenado."""
        repo = JobRepository(db_session)
        assert await repo.upsert_job(_job_dict(logo=self.GOOD_LOGO)) is True
        await db_session.commit()

        assert await repo.upsert_job(_job_dict(logo=degraded)) is False
        await db_session.commit()

        assert await self._stored_logo(db_session) == self.GOOD_LOGO

    async def test_alta_con_logo_none_queda_null(self, db_session):
        """Control: en un ALTA el logo omitido deja la columna en su default
        (NULL) — la omisión del INSERT no rompe el camino sin conflicto."""
        repo = JobRepository(db_session)
        assert await repo.upsert_job(_job_dict(logo=None)) is True
        await db_session.commit()

        assert await self._stored_logo(db_session) is None


@pytest.mark.anyio
class TestLogoNulByte:
    """Sexta revisión / H2: un logo con byte NUL (\\x00) abortaba el INSERT
    entero en Postgres (CharacterNotInRepertoireError) y costaba la OFERTA:
    en un alta se perdía y en una re-vista no refrescaba last_seen_at (a 60
    días, cleanup_stale_jobs la borra). El logo es decorativo — se omite el
    campo (mismo mecanismo que None/""/no-string) y la oferta se persiste."""

    GOOD_LOGO = "https://cdn.example.com/logo.png"
    NUL_LOGO = "https://cdn.example.com/logo\x00.png"

    async def _stored_logo(self, db_session) -> str | None:
        h = _job_dict()["hash"]
        return (
            await db_session.execute(select(Job.logo).where(Job.hash == h))
        ).scalar_one()

    async def test_alta_con_logo_nul_persiste_la_oferta_sin_logo(self, db_session):
        """El caso del alta: sin el fix el INSERT reventaba y la oferta se
        PERDÍA. Con él, la oferta entra y solo el logo queda NULL."""
        repo = JobRepository(db_session)
        assert await repo.upsert_job(_job_dict(logo=self.NUL_LOGO)) is True
        await db_session.commit()

        assert await self._stored_logo(db_session) is None

    async def test_logo_nul_en_revisita_no_pisa_el_logo_bueno(self, db_session):
        """El caso de la re-vista: sin el fix el upsert reventaba y la oferta
        no refrescaba last_seen_at (camino a cleanup_stale_jobs). Con él, se
        refresca y el logo bueno almacenado sobrevive (G5, mismo mecanismo
        que el resto de degradaciones de logo)."""
        repo = JobRepository(db_session)
        assert await repo.upsert_job(_job_dict(logo=self.GOOD_LOGO)) is True
        await db_session.commit()

        assert await repo.upsert_job(_job_dict(logo=self.NUL_LOGO)) is False
        await db_session.commit()

        assert await self._stored_logo(db_session) == self.GOOD_LOGO


class TestApplyUrlGuard:
    """Auditoría C1 P3-3: el guard de frontera de apply_url (R.6) con tests
    legacy propios — degrada SOLO el campo, jamás la oferta."""

    @pytest.mark.asyncio
    async def test_apply_url_invalido_degrada_solo_el_campo(self, db_session):
        repo = JobRepository(db_session)
        base = _job_dict(hash="aurl0001" + "0" * 24,
                         url="https://x.test/aurl-1")
        # no-string, NUL, desborde (>1000, contrato core) y vacío: campo fuera
        for i, malo in enumerate([123, "con\x00nul", "https://a/" + "b" * 1200,
                                  "   "]):
            job = dict(base)
            job["hash"] = f"aurl{i:04d}" + "0" * 24
            job["url"] = f"https://x.test/aurl-{i}"
            job["apply_url"] = malo
            assert await repo.upsert_job(job) is True  # la oferta SÍ persiste
            row = await db_session.get(Job, job["hash"])
            assert row is not None and row.apply_url is None

    @pytest.mark.asyncio
    async def test_apply_url_valido_persiste_y_refresca(self, db_session):
        repo = JobRepository(db_session)
        job = _job_dict(hash="aurlok01" + "0" * 24,
                        url="https://x.test/aurl-ok")
        job["apply_url"] = "https://ats.acme.com/jobs/1"
        await repo.upsert_job(job)
        row = await db_session.get(Job, job["hash"])
        assert row.apply_url == "https://ats.acme.com/jobs/1"
        job["apply_url"] = "https://ats.acme.com/jobs/2"
        assert await repo.upsert_job(job) is False  # re-vista
        await db_session.refresh(row)
        assert row.apply_url == "https://ats.acme.com/jobs/2"  # refresca
