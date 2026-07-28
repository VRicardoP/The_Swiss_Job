"""CONTRACT TESTS de la capacidad DOCUMENTOS — A.SEAM (plan §15bis).

Variante LIGERA de la costura: el /v1 del core (jobhunt_core/api/v1.py) NO
expone documentos generados en Fase A — la cota contractual es
DocumentsUnsupportedError en TODAS las operaciones del puerto, fijada aqui
(patron search/stats de catalogo).

CRITERIO UNIFICADOR (heredado de A.SEAM matching): el UNICO escritor de
`generated_documents` es LOCAL hasta Fase C => el estado es accesible en
TODOS los modos de routing, incluida core_primary — nunca 501/503 por
routing. Fijado aqui a nivel de resolver y de HTTP.
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.user import User
from services.documents import (
    CoreDocuments,
    CoreUnavailableError,
    DocumentsUnsupportedError,
    FallbackDocuments,
    LocalDocuments,
    resolve_documents,
)
from services.routing import (
    CAPABILITY_DOCUMENTS,
    invalidate_routing_cache,
    set_routing,
)

JOB_HASH = "d" * 32


@pytest.fixture(autouse=True)
def _fresh_routing_cache():
    """La cache del routing es por proceso: sin esto un test podria leer el
    modo de otro test."""
    invalidate_routing_cache()
    yield
    invalidate_routing_cache()


async def seed_user(db: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"contract-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user.id


async def seed_job(db: AsyncSession, job_hash: str = JOB_HASH) -> str:
    db.add(
        Job(
            hash=job_hash,
            source="test_source",
            title="Senior Python Developer",
            company="SwissTech Corp",
            url=f"https://example.com/job/{job_hash[:6]}",
            description="FastAPI, PostgreSQL, Docker.",
            is_active=True,
        )
    )
    return job_hash


# ---------------------------------------------------------------------------
# Cota /v1: el core NO expone documentos — Unsupported en TODA operacion
# ---------------------------------------------------------------------------

CORE_OPS = {
    "create": lambda c, u: c.create(u, JOB_HASH, "cv", "content", "en"),
    "list": lambda c, u: c.list(u, JOB_HASH),
    "delete": lambda c, u: c.delete(u, uuid.uuid4()),
}


@pytest.mark.parametrize("op", list(CORE_OPS))
async def test_core_documents_operations_not_in_v1_contract(op):
    """El /v1 del core no expone documentos (Fase A): la costura lo declara
    como DocumentsUnsupportedError, no lo simula. CoreDocuments no necesita
    credencial ni abre cliente HTTP: cero peticiones por construccion."""
    core = CoreDocuments()
    with pytest.raises(DocumentsUnsupportedError):
        await CORE_OPS[op](core, uuid.uuid4())


def test_core_covers_entire_port_surface():
    """La cota es TOTAL: cada operacion publica del puerto local existe en el
    cliente core (con Unsupported) y esta fijada en CORE_OPS."""
    local_ops = {n for n in vars(LocalDocuments) if not n.startswith("_")}
    core_ops = {n for n in vars(CoreDocuments) if not n.startswith("_")}
    assert local_ops == core_ops == set(CORE_OPS)


# ---------------------------------------------------------------------------
# LOCAL: evidencia a nivel de puerto (la HTTP la dan los tests preexistentes)
# ---------------------------------------------------------------------------


async def test_local_create_list_roundtrip(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    port = LocalDocuments(db_session)

    created = await port.create(
        user_id,
        JOB_HASH,
        "cv",
        "# Tailored CV",
        "en",
        job_title="Senior Python Developer",
        job_company="SwissTech Corp",
    )
    assert created.id is not None
    assert created.doc_type == "cv"
    assert created.job_title == "Senior Python Developer"

    await port.create(user_id, JOB_HASH, "cover_letter", "Dear...", "de")
    listed = await port.list(user_id, JOB_HASH)
    assert listed.total == 2
    # Orden created_at DESC + job_title resuelto por el join en el listado.
    assert listed.data[0].doc_type == "cover_letter"
    assert listed.data[1].job_title == "Senior Python Developer"

    only_cv = await port.list(user_id, JOB_HASH, doc_type="cv")
    assert only_cv.total == 1
    assert only_cv.data[0].id == created.id


async def test_local_delete_semantics(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    port = LocalDocuments(db_session)
    created = await port.create(user_id, JOB_HASH, "cv", "x", "en")

    assert await port.delete(user_id, uuid.uuid4()) is False  # => 404
    assert await port.delete(user_id, created.id) is True
    assert (await port.list(user_id, JOB_HASH)).total == 0


# ---------------------------------------------------------------------------
# Resolucion por jobhunt_routing (criterio unificador incluido)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode", [None, "local", "shadow", "core_primary", "rollback_pending"]
)
async def test_resolve_documents_serves_local_writer(db_session, mode):
    """Todo modo salvo core_read resuelve a LOCAL — incluida core_primary:
    criterio unificador, el unico escritor del estado es local y el /v1 no
    expone la capacidad => nunca 501/503 por routing."""
    user_id = uuid.uuid4()
    if mode is not None:
        await set_routing(db_session, CAPABILITY_DOCUMENTS, mode, profile_id=user_id)
    port = await resolve_documents(db_session, user_id)
    assert isinstance(port, LocalDocuments)


async def test_resolve_documents_core_read_is_fallback(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_DOCUMENTS, "core_read", profile_id=user_id)
    port = await resolve_documents(db_session, user_id)
    assert isinstance(port, FallbackDocuments)


async def test_resolve_documents_profile_row_beats_wildcard(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_DOCUMENTS, "core_read")  # comodin
    await set_routing(db_session, CAPABILITY_DOCUMENTS, "local", profile_id=user_id)
    assert isinstance(await resolve_documents(db_session, user_id), LocalDocuments)
    assert isinstance(
        await resolve_documents(db_session, uuid.uuid4()), FallbackDocuments
    )


# ---------------------------------------------------------------------------
# Canary core_read: cae a local con severidades heredadas
# ---------------------------------------------------------------------------


async def test_core_read_falls_back_to_local_for_v1_bound(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session)
    await db_session.commit()
    seam = FallbackDocuments(CoreDocuments(), LocalDocuments(db_session))
    created = await seam.create(user_id, JOB_HASH, "cv", "content", "en")
    listed = await seam.list(user_id, JOB_HASH)
    assert listed.total == 1
    assert await seam.delete(user_id, created.id) is True


async def test_canary_warn_levels_separate_expected_from_actionable(caplog):
    """Severidades heredadas (2ª rev. A.SEAM catalogo): Unsupported (cota
    /v1, esperado) va a DEBUG; el core CAIDO (CoreUnavailableError) es el
    UNICO WARNING."""

    class _Unsupported:
        async def list(self, user_id, job_hash, doc_type=None):
            raise DocumentsUnsupportedError("cota contrato")

    class _Down:
        async def list(self, user_id, job_hash, doc_type=None):
            raise CoreUnavailableError("core caido")

    class _Fallback:
        async def list(self, user_id, job_hash, doc_type=None):
            return None

    with caplog.at_level(logging.DEBUG, logger="services.documents.seam"):
        await FallbackDocuments(_Unsupported(), _Fallback()).list(
            uuid.uuid4(), JOB_HASH
        )
        await FallbackDocuments(_Down(), _Fallback()).list(uuid.uuid4(), JOB_HASH)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warns) == 1 and "core caido" in warns[0].getMessage()
    assert len(debugs) == 1 and "cota contrato" in debugs[0].getMessage()


# ---------------------------------------------------------------------------
# Router: el estado local es accesible en TODOS los modos (nunca 501/503)
# ---------------------------------------------------------------------------


async def _register(client) -> tuple[uuid.UUID, dict]:
    email = f"seam-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "gdpr_consent": True},
    )
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    return uuid.UUID(me.json()["id"]), headers


async def _set_cv_text(db: AsyncSession, user_id: uuid.UUID) -> None:
    from sqlalchemy import select

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    await db.refresh(user, ["profile"])
    user.profile.cv_text = "Experienced Python developer."
    user.profile.skills = ["Python", "FastAPI"]
    await db.commit()


@pytest.mark.parametrize(
    "mode", ["local", "shadow", "core_read", "core_primary", "rollback_pending"]
)
async def test_router_local_state_accessible_in_all_modes(client, db_session, mode):
    """Generar (escritura), listar (lectura) y borrar documentos sirve en
    los 5 modos de routing — incluida core_primary (criterio unificador):
    jamas un 501/503 para estado cuyo unico escritor es local."""
    user_id, headers = await _register(client)
    await seed_job(db_session)
    await db_session.commit()
    await _set_cv_text(db_session, user_id)
    await set_routing(db_session, CAPABILITY_DOCUMENTS, mode, profile_id=user_id)

    mock_groq = AsyncMock()
    mock_groq.is_available = True
    mock_groq.get_chat_response = AsyncMock(return_value="# Tailored CV")
    gemini_off = MagicMock()
    gemini_off.is_available = False

    with (
        patch("routers.documents._get_groq", return_value=mock_groq),
        patch("routers.documents._get_gemini", return_value=gemini_off),
    ):
        resp = await client.post(
            "/api/v1/documents/generate",
            headers=headers,
            json={"job_hash": JOB_HASH, "doc_type": "cv", "language": "en"},
        )
    assert resp.status_code == 200
    doc_id = resp.json()["id"]

    resp = await client.get(f"/api/v1/documents/{JOB_HASH}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.delete(f"/api/v1/documents/{doc_id}", headers=headers)
    assert resp.status_code == 204
