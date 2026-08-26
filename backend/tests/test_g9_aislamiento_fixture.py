"""Aislamiento entre tests: cada test arranca con la base de datos VACÍA.

Guarda permanente del cambio de `setup_db` (G9): el fixture dejó de truncar
las 15 tablas siempre y ahora trunca SOLO las que tienen filas, detectadas con
`EXISTS`. Si esa detección dejara residuo, dos tests podrían verse el estado y
la suite entera perdería su capacidad de refutar.

Cada caso hace lo mismo y en este orden: (1) EXIGE que las 15 tablas estén
vacías, (2) las ensucia. Así el que corra después —sea cual sea, y en
cualquier orden de ejecución— es quien comprueba la limpieza del anterior.
"""

import uuid

import pytest
from sqlalchemy import func, select

from database import Base
from models.job import Job
from models.user import User


async def _filas_por_tabla(db) -> dict[str, int]:
    return {
        table.name: await db.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }


@pytest.mark.parametrize("ronda", range(3))
async def test_la_base_llega_vacia_y_se_ensucia_para_el_siguiente(db_session, ronda):
    con_filas = {t: n for t, n in (await _filas_por_tabla(db_session)).items() if n}
    assert con_filas == {}, f"el test anterior dejó filas: {con_filas}"

    marca = uuid.uuid4().hex
    db_session.add(
        Job(
            hash=f"g9iso{ronda}{marca}"[:32].ljust(32, "0"),
            source="test_aislamiento",
            title="Ensucia la base",
            company="Acme",
            url=f"https://example.com/g9-iso/{marca}",
            description="Descripción cualquiera.",
            is_active=True,
        )
    )
    db_session.add(
        User(
            email=f"g9-iso-{marca}@example.com",
            hashed_password="x",
            gdpr_consent=True,
        )
    )
    await db_session.commit()
