"""G5/P3-2 — el guard de identidad ya no depende SOLO del texto del mensaje.

Su docstring declaraba: «Se mira primero `constraint_name` (asyncpg lo expone
tipado) y solo se cae al texto si el driver no lo trae: comparar únicamente el
mensaje ataría el guard al idioma/formato del servidor.» Pero con
SQLAlchemy 2 + asyncpg esa rama era CÓDIGO MUERTO: `exc.orig` es el envoltorio
`AsyncAdapt_asyncpg_dbapi.IntegrityError` y NO propaga el atributo. El
`UniqueViolationError` de asyncpg —el que sí lo lleva— cuelga de su `__cause__`.

La detección funcionaba, pero por la rama de RESPALDO. El guard era exactamente
lo que su comentario decía no querer ser. Deuda de robustez y de veracidad del
comentario, no un bug de comportamiento: cero falsos positivos medidos.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from datetime import datetime, timezone

from services.job_repository import (
    JobIdentityConflictError,
    JobRepository,
    _is_url_unique_violation,
)

_URL = "http://example.com/g5-guard"


def _fila(hash_: str) -> dict:
    return {
        "hash": hash_.ljust(32, "0"),
        "source": "g5guard",
        "title": "T",
        "company": "C",
        "url": _URL,
        "description": "d",
        "description_snippet": "d",
        "published_at": datetime.now(timezone.utc),
        "location": "Bern",
        "canton": "BE",
        "remote": False,
        "tags": [],
        "logo": None,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": None,
        "seniority": None,
        "contract_type": None,
        "employment_type": None,
    }


class _Asyncpg(Exception):
    """Imita el `UniqueViolationError` de asyncpg: lleva `constraint_name`."""

    def __init__(self, name):
        super().__init__("duplicate key value violates unique constraint")
        self.constraint_name = name

    def __str__(self):
        return "duplicate key value violates unique constraint"


class _Wrapper(Exception):
    """Imita `AsyncAdapt_asyncpg_dbapi.IntegrityError`: SIN el atributo."""

    def __str__(self):
        return "duplicate key value violates unique constraint"


class TestElNombreDelIndiceSeLeeSinMirarElTexto:
    def test_se_detecta_por_constraint_name_aunque_el_texto_no_lo_mencione(self):
        """Es el punto entero del fix: el mensaje NO nombra el índice."""
        wrapper = _Wrapper()
        wrapper.__cause__ = _Asyncpg("ix_jobs_url")
        exc = IntegrityError("stmt", {}, wrapper)

        assert "ix_jobs_url" not in str(wrapper), "el texto no debe delatar el índice"
        assert _is_url_unique_violation(exc) is True

    def test_otro_indice_con_el_mismo_texto_NO_se_confunde(self):
        wrapper = _Wrapper()
        wrapper.__cause__ = _Asyncpg("ix_jobs_hash")
        exc = IntegrityError("stmt", {}, wrapper)

        assert _is_url_unique_violation(exc) is False

    def test_sin_constraint_name_en_ningun_nivel_sigue_el_respaldo_por_texto(self):
        exc = IntegrityError(
            "stmt", {}, Exception('duplicate key ... unique constraint "ix_jobs_url"')
        )
        assert _is_url_unique_violation(exc) is True


@pytest.mark.asyncio
class TestNoRegresionConElDriverReal:
    async def test_la_url_duplicada_sigue_siendo_JobIdentityConflictError(
        self, db_session
    ):
        repo = JobRepository(db_session)
        await repo.upsert_job(_fila("a"))
        await db_session.commit()

        with pytest.raises(JobIdentityConflictError):
            async with db_session.begin_nested():
                await repo.upsert_job(_fila("b"))
        await db_session.rollback()

    async def test_otra_violacion_de_integridad_sube_tal_cual(self, db_session):
        """Cero falsos positivos: un NOT NULL no se disfraza de deriva."""
        with pytest.raises(IntegrityError) as exc:
            async with db_session.begin_nested():
                await db_session.execute(
                    text(
                        "INSERT INTO jobs (hash, source, title, company, url, "
                        "description, is_active) VALUES "
                        "('c', 'g5guard', 'T', NULL, 'http://x/1', 'd', true)"
                    )
                )
        await db_session.rollback()
        assert not isinstance(exc.value, JobIdentityConflictError)
