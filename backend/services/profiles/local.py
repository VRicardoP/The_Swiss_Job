"""Implementacion LOCAL de la capacidad perfiles — A.SEAM (plan §15bis).

La lectura del router era dos lineas (`db.refresh(user, ["profile"])` +
relacion): se mueve VERBATIM aqui — con routing 'local' el comportamiento es
byte-identico al previo a la costura, y los tests existentes
(test_profile_crud.py, test_profile.py) quedan intactos como evidencia.
NO cambiar semantica aqui sin contract test.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from models.user_profile import UserProfile


class LocalProfile:
    """Almacen actual: fila `user_profiles` del usuario via su relacion."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get(self, user: User) -> UserProfile | None:
        await self._db.refresh(user, ["profile"])
        return user.profile
