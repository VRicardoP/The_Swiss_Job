"""Implementacion LOCAL de la capacidad matching — A.SEAM (plan §15bis).

A diferencia de catalogo no hay codigo que mover: la lectura del feed ya
vivia extraida en `MatchResultService` (extraccion SRP previa), asi que esta
implementacion DELEGA VERBATIM en el servicio existente — con routing
'local' el comportamiento es byte-identico al previo a la costura, y los
tests existentes (test_match.py, test_match_result_service.py) quedan
intactos como evidencia. NO cambiar semantica aqui sin contract test.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.match_result_service import MatchResultService


class LocalMatching:
    """Motor actual: MatchResultService sobre `match_results` + `jobs`."""

    def __init__(self, db: AsyncSession):
        self._service = MatchResultService(db)

    async def results(
        self, user_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict], int]:
        return await self._service.get_results(
            user_id=user_id, limit=limit, offset=offset
        )

    async def saved(
        self, user_id: uuid.UUID, limit: int = 100, offset: int = 0
    ) -> tuple[list[dict], int]:
        return await self._service.get_saved_jobs(
            user_id=user_id, limit=limit, offset=offset
        )
