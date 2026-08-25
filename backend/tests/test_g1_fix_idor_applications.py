"""Regresión de la auditoría G1 — P1-3: IDOR entre usuarios en el escritor
core de candidaturas.

El PATCH/DELETE del /v1 solo valida ownership a nivel de CONSUMER (el WHERE
de `_lock_target` acota por `p.consumer_id`) y la CORE_CONSUMER_KEY es única
y compartida por todos los usuarios del BFF: el usuario B podía mutar/borrar
la candidatura de A conociendo su UUID, y `update` devolvía la fila ajena
re-sellada con el user_id del atacante. El fix (lado CLIENTE, sin cambiar el
contrato /v1) verifica que el id pertenece al feed del PERFIL del usuario
antes de emitir la escritura; un id ajeno responde como inexistente (None /
False → 404 del router), indistinguible — el contrato del puerto («None si
no existe PARA ESE USUARIO»).
"""

import uuid

import httpx
import pytest

from models.enums import ApplicationStatus
from schemas.applications import ApplicationUpdate
from services.matching.identity import set_profile_link
from tests.test_applications_contract import (
    FakeCoreV1,
    JOB_HASH,
    make_core,
    seed_job,
    seed_user,
)


class FakeCoreV1DosPerfiles(FakeCoreV1):
    """Como el fake del contrato, pero sirve TAMBIÉN el feed (vacío) de un
    segundo perfil del MISMO consumer — la superficie real del IDOR: el
    PATCH/DELETE heredados mutan cualquier item del consumer, sin mirar el
    perfil (fiel a `_lock_target` del /v1)."""

    def __init__(self, profile_a: uuid.UUID, profile_b: uuid.UUID):
        super().__init__(profile_a)
        self.profile_b = str(profile_b)

    def _list(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("profile") == self.profile_b:
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        return super()._list(request)


@pytest.fixture
async def dos_usuarios(db_session):
    """Usuarios A y B, cada uno con su perfil core; una candidatura de A."""
    user_a = await seed_user(db_session)
    user_b = await seed_user(db_session)
    await seed_job(db_session, JOB_HASH)
    await db_session.commit()
    profile_a, profile_b = uuid.uuid4(), uuid.uuid4()
    await set_profile_link(db_session, user_a, profile_a, updated_by="tests")
    await set_profile_link(db_session, user_b, profile_b, updated_by="tests")
    fake = FakeCoreV1DosPerfiles(profile_a, profile_b)
    core = make_core(db_session, httpx.MockTransport(fake.handler))
    app_de_a = fake.add_item(kind="application", status="applied", notes="privada")
    return user_a, user_b, fake, core, app_de_a


@pytest.mark.asyncio
async def test_p1_3_update_ajeno_es_404_y_no_muta(dos_usuarios):
    user_a, user_b, fake, core, app_de_a = dos_usuarios
    app_id = uuid.UUID(app_de_a["id"])

    result = await core.update(user_b, app_id, ApplicationUpdate(notes="pwned by B"))

    assert result is None, "la candidatura de A no existe PARA el usuario B"
    assert fake.items[app_de_a["id"]]["notes"] == "privada", (
        "el PATCH ajeno jamás debe llegar al core"
    )


@pytest.mark.asyncio
async def test_p1_3_delete_ajeno_es_404_y_no_borra(dos_usuarios):
    user_a, user_b, fake, core, app_de_a = dos_usuarios
    app_id = uuid.UUID(app_de_a["id"])

    assert await core.delete(user_b, app_id) is False
    assert app_de_a["id"] in fake.items, "el DELETE ajeno jamás debe llegar al core"


@pytest.mark.asyncio
async def test_p1_3_el_propietario_sigue_pudiendo_mutar(dos_usuarios):
    """El scoping no debe romper el camino legítimo del propietario."""
    user_a, _, fake, core, app_de_a = dos_usuarios
    app_id = uuid.UUID(app_de_a["id"])

    result = await core.update(
        user_a, app_id, ApplicationUpdate(status=ApplicationStatus.interview)
    )
    assert result is not None
    assert result.status == ApplicationStatus.interview

    assert await core.delete(user_a, app_id) is True
    assert app_de_a["id"] not in fake.items
