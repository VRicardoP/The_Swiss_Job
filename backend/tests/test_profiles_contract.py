"""CONTRACT TESTS de la capacidad PERFILES — A.SEAM (plan §15bis).

El MISMO juego de datos se ejecuta contra las DOS implementaciones del
puerto ProfilePort:

- `local`: la fila `user_profiles` via la relacion del usuario (BD de test).
- `core`: cliente de GET /v1/profiles/{id} contra un /v1 FAKE cuya forma
  replica el contrato REAL del core (jobhunt_core/api/v1.py + api/schemas.py:
  ProfileDTO {id, external_ref, created_at, current_revision:{content,
  content_hash, text_hash}}, ETag con If-None-Match -> 304, auth Bearer
  key_id.secret, 404 ErrorDTO). El backend legacy no importa jobhunt_core
  (frontera estricta, plan §21), por eso el DTO se replica aqui como dict.

Se afirma forma y semantica DONDE EL CONTRATO LO EXIGE (criterio unificador
— docstring de services/profiles/seam.py: mientras el escritor sea LOCAL,
nada visible es no-accionable y ningun estado local es inaccesible):
- {title, cv_text, skills} se sirven del CORE (revision vigente proyectada
  por la sombra, PF.5) — es la lectura que el canary valida.
- El RESTO del perfil legacy (weights, salario, idiomas, remote_pref,
  watchlist, embedding...) NO existe en el core: se sirve del escritor
  LOCAL (mapeo honesto — nunca huecos inventados). Cota fijada aqui:
  entraran al core con el flip de escritor de Fase C.
- Sin fila local: None (404) y CERO peticiones — un perfil solo-core no
  admitiria PUT (no-accionable).
- Revision vigente ausente o sin las tres claves proyectadas:
  CoreUnavailableError (senal accionable, nunca un perfil con huecos).
- Las ESCRITURAS (PUT/CV) son del escritor local en TODOS los modos y su
  respuesta es su recibo; GDPR opera siempre sobre el almacen local.
"""

import hashlib
import json
import logging
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.enums import RemotePreference
from models.user import User
from models.user_profile import UserProfile
from schemas.profile import ProfileResponse
from services.matching import set_profile_link
from services.profiles import (
    CoreProfile,
    CoreUnavailableError,
    FallbackProfile,
    LocalProfile,
    ProfileUnsupportedError,
    clear_profile_cache,
    core_client,
    resolve_profiles,
)
from services.routing import (
    CAPABILITY_PROFILES,
    invalidate_routing_cache,
    set_routing,
)

TEST_CONSUMER_KEY = "testkid.testsecret"

# Perfil local del juego de datos: campos proyectados al core + campos que
# SOLO tiene el escritor local (el mapeo honesto debe servirlos de local).
LOCAL_PROFILE = {
    "title": "Senior Python Developer",
    "cv_text": "20 years building critical systems with Python and Docker.",
    "skills": ["docker", "fastapi", "python"],
    "experience_years": 20,
    "languages": ["en", "de"],
    "locations": ["Zurich", "Remote"],
    "salary_min": 90000,
    "salary_max": 130000,
    "remote_pref": RemotePreference.hybrid,
    "score_weights": {
        "embedding": 0.5,
        "salary": 0.2,
        "location": 0.1,
        "recency": 0.1,
        "llm": 0.1,
    },
    "watchlist_schools_enabled": True,
}

# Content proyectado (PF.5: {title, cv_text, skills} EXACTO).
PROJECTED_CONTENT = {k: LOCAL_PROFILE[k] for k in ("title", "cv_text", "skills")}

# Campos de ProfileResponse cuyo valor DEBE venir del escritor local en la
# implementacion core (el /v1 no los expone — cota documentada).
LOCAL_ONLY_FIELDS = (
    "experience_years",
    "languages",
    "locations",
    "salary_min",
    "salary_max",
    "remote_pref",
    "score_weights",
    "watchlist_schools_enabled",
    "has_cv_embedding",
)


@pytest.fixture(autouse=True)
def _clean_seam_caches():
    """Las caches de routing y de representaciones del perfil son de
    proceso: sin esto un test podria leer el modo o la copia de otro test."""
    invalidate_routing_cache()
    clear_profile_cache()
    yield
    invalidate_routing_cache()
    clear_profile_cache()


# ---------------------------------------------------------------------------
# Semilla local (usuario + perfil completo)
# ---------------------------------------------------------------------------


async def seed_local(db: AsyncSession, with_profile: bool = True) -> User:
    user = User(
        email=f"profiles-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    if with_profile:
        db.add(
            UserProfile(
                user_id=user.id,
                cv_embedding=[0.1] * 384,
                **LOCAL_PROFILE,
            )
        )
    await db.commit()
    return user


# ---------------------------------------------------------------------------
# /v1 fake fiel al contrato del perfil (ProfileDTO + ETag + auth + 404)
# ---------------------------------------------------------------------------


class FakeProfileApi:
    """Servidor /v1 fake de GET /profiles/{id}: ETag de representacion."""

    def __init__(
        self,
        profile_id: uuid.UUID,
        external_ref: str,
        content: dict | None,
    ):
        self.profile_id = profile_id
        self.external_ref = external_ref
        self.content = content  # None => perfil SIN revision vigente
        self.requests: list[httpx.Request] = []
        self.hits_304 = 0

    def _body(self) -> dict:
        revision = None
        if self.content is not None:
            canon = json.dumps(self.content, sort_keys=True).encode()
            revision = {
                "content": self.content,
                "content_hash": f"sha256:{hashlib.sha256(canon).hexdigest()}",
                "text_hash": f"sha256:{hashlib.sha256(canon).hexdigest()}",
            }
        return {
            "id": str(self.profile_id),
            "external_ref": self.external_ref,
            "created_at": "2026-07-01T08:00:00Z",
            "current_revision": revision,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {TEST_CONSUMER_KEY}":
            return httpx.Response(
                401,
                json={
                    "code": "unauthorized",
                    "message": "credencial invalida",
                    "details": {},
                },
            )
        if request.url.path != f"/v1/profiles/{self.profile_id}":
            return httpx.Response(
                404,
                json={
                    "code": "not_found",
                    "message": "perfil no encontrado",
                    "details": {},
                },
            )
        body = self._body()
        etag = (
            '"'
            + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]
            + '"'
        )
        if request.headers.get("if-none-match") == etag:
            self.hits_304 += 1
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, json=body, headers={"ETag": etag})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def make_core_profile(
    db: AsyncSession,
    transport: httpx.MockTransport,
    bearer: str = TEST_CONSUMER_KEY,
) -> CoreProfile:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=1.0,
            transport=transport,
        )

    return CoreProfile(db, client_factory=factory)


@pytest.fixture
async def seeded(db_session):
    """Usuario + perfil local + vinculo de identidad + /v1 fake del core.

    Devuelve (user, ports, fake) con `ports` = dict impl -> puerto. El fake
    sirve el MISMO content que proyectaria la sombra (PF.5: EXACTO al local).
    """
    user = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user.id, core_profile_id, updated_by="tests")
    fake = FakeProfileApi(core_profile_id, str(user.id), dict(PROJECTED_CONTENT))
    ports = {
        "local": LocalProfile(db_session),
        "core": make_core_profile(db_session, fake.transport()),
    }
    return user, ports, fake


def _response(view) -> ProfileResponse:
    """Mapeo REAL del router: model_validate + has_cv_embedding."""
    resp = ProfileResponse.model_validate(view)
    resp.has_cv_embedding = view.cv_embedding is not None
    return resp


# ---------------------------------------------------------------------------
# get(): mismo juego de datos contra ambas implementaciones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl", ["local", "core"])
async def test_get_shape_validates_as_profile_response(seeded, impl):
    """FORMA: la vista de ambos backends pasa por el mapeo REAL del router
    y valida como ProfileResponse."""
    user, ports, _ = seeded
    view = await ports[impl].get(user)
    assert view is not None
    resp = _response(view)
    assert resp.user_id == user.id
    assert resp.has_cv_embedding is True


async def test_get_semantics_equivalent_local_vs_core(seeded):
    """Con la proyeccion al dia (content EXACTO, PF.5) ambas implementaciones
    sirven la MISMA respuesta HTTP, campo a campo."""
    user, ports, _ = seeded
    local = _response(await ports["local"].get(user))
    core = _response(await ports["core"].get(user))
    assert core.model_dump() == local.model_dump()


async def test_core_serves_projected_fields_from_core(db_session):
    """{title, cv_text, skills} vienen del CORE: si la proyeccion difiere del
    local (lag del CDC), el canary sirve la copia del core — esa lectura es
    precisamente lo que se esta validando."""
    user = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user.id, core_profile_id)
    divergent = {
        "title": "Stale Projected Title",
        "cv_text": "projected cv text",
        "skills": ["projected"],
    }
    fake = FakeProfileApi(core_profile_id, str(user.id), divergent)
    view = await make_core_profile(db_session, fake.transport()).get(user)
    assert view.title == "Stale Projected Title"
    assert view.cv_text == "projected cv text"
    assert view.skills == ["projected"]


async def test_core_local_only_fields_served_from_local_writer(db_session):
    """MAPEO HONESTO: lo que el core no tiene se sirve del escritor local —
    ningun estado local inaccesible, ningun hueco inventado (cota Fase C)."""
    user = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user.id, core_profile_id)
    fake = FakeProfileApi(
        core_profile_id,
        str(user.id),
        {"title": "X", "cv_text": None, "skills": []},
    )
    core = _response(await make_core_profile(db_session, fake.transport()).get(user))
    local = _response(await LocalProfile(db_session).get(user))
    for field in LOCAL_ONLY_FIELDS:
        assert getattr(core, field) == getattr(local, field), field
    # Los ids del contrato legacy son los LOCALES (el UUID core no se filtra).
    assert core.id == local.id and core.user_id == local.user_id
    assert core.updated_at == local.updated_at


async def test_core_etag_second_pass_uses_304(seeded):
    """Segunda lectura: If-None-Match -> 304 sin cuerpo y misma respuesta
    desde la cache de representaciones."""
    user, ports, fake = seeded
    first = _response(await ports["core"].get(user))
    second = _response(await ports["core"].get(user))
    assert fake.hits_304 == 1
    assert second.model_dump() == first.model_dump()


# ---------------------------------------------------------------------------
# Contrato de identidad, auth y fallo del cliente core
# ---------------------------------------------------------------------------


def _fail_transport() -> httpx.MockTransport:
    def fail_on_any_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"peticion emitida indebidamente: {request.url}")

    return httpx.MockTransport(fail_on_any_request)


async def test_core_missing_local_profile_returns_none_no_request(db_session):
    """Sin fila local no hay perfil accionable: None (404 legacy) y CERO
    peticiones (el transporte falla el test si recibe alguna)."""
    user = await seed_local(db_session, with_profile=False)
    await set_profile_link(db_session, user.id, uuid.uuid4())
    core = make_core_profile(db_session, _fail_transport())
    assert await core.get(user) is None


async def test_core_unlinked_user_makes_no_request(db_session):
    """Sin vinculo en jobhunt_profile_map: CoreUnavailableError y CERO
    peticiones."""
    user = await seed_local(db_session)
    core = make_core_profile(db_session, _fail_transport())
    with pytest.raises(CoreUnavailableError):
        await core.get(user)


async def test_core_without_configured_credential_makes_no_request(
    db_session, monkeypatch
):
    """Con vinculo pero sin CORE_CONSUMER_KEY: ni una peticion (factory por
    defecto de produccion) — como catalogo."""
    user = await seed_local(db_session)
    await set_profile_link(db_session, user.id, uuid.uuid4())
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    core = CoreProfile(db_session)  # factory por defecto (produccion)
    with pytest.raises(CoreUnavailableError):
        await core.get(user)


async def test_core_rejects_bad_credential_as_unavailable(seeded, db_session):
    user, _, fake = seeded
    core = make_core_profile(db_session, fake.transport(), bearer="wrongkid.wrong")
    with pytest.raises(CoreUnavailableError):
        await core.get(user)


async def test_core_stale_profile_link_404_is_unavailable(db_session):
    """Vinculo apuntando a un perfil que el core no reconoce para este
    consumer (404 indistinguible del contrato): configuracion, no datos."""
    user = await seed_local(db_session)
    await set_profile_link(db_session, user.id, uuid.uuid4())
    # El fake solo conoce OTRO perfil => responde 404 para el vinculado.
    fake = FakeProfileApi(uuid.uuid4(), "other", dict(PROJECTED_CONTENT))
    core = make_core_profile(db_session, fake.transport())
    with pytest.raises(CoreUnavailableError):
        await core.get(user)


async def test_core_down_raises_unavailable(db_session):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    user = await seed_local(db_session)
    await set_profile_link(db_session, user.id, uuid.uuid4())
    core = make_core_profile(db_session, httpx.MockTransport(refuse))
    with pytest.raises(CoreUnavailableError):
        await core.get(user)


async def test_core_no_active_revision_is_unavailable(db_session):
    """Perfil enrolado cuya proyeccion aun no aterrizo (current_revision
    null): senal accionable del canary, nunca un perfil vacio en silencio."""
    user = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user.id, core_profile_id)
    fake = FakeProfileApi(core_profile_id, str(user.id), content=None)
    core = make_core_profile(db_session, fake.transport())
    with pytest.raises(CoreUnavailableError, match="sin revision vigente"):
        await core.get(user)


async def test_core_revision_missing_projected_key_is_unavailable(db_session):
    """Revision anomala (PF.5 garantiza {title, cv_text, skills}): servir
    huecos ocultaria estado local => indisponibilidad, no un perfil a medias."""
    user = await seed_local(db_session)
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user.id, core_profile_id)
    fake = FakeProfileApi(
        core_profile_id,
        str(user.id),
        {"title": "X", "skills": []},  # sin cv_text
    )
    core = make_core_profile(db_session, fake.transport())
    with pytest.raises(CoreUnavailableError, match="sin revision vigente completa"):
        await core.get(user)


# ---------------------------------------------------------------------------
# Routing: la costura resuelve la implementacion por perfil
# ---------------------------------------------------------------------------


async def test_resolve_profiles_default_is_local(db_session):
    port = await resolve_profiles(db_session, uuid.uuid4())
    assert isinstance(port, LocalProfile)


async def test_resolve_profiles_core_read_is_fallback(db_session):
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_PROFILES, "core_read", profile_id=user_id)
    port = await resolve_profiles(db_session, user_id)
    assert isinstance(port, FallbackProfile)


async def test_resolve_profiles_core_primary_has_no_fallback(db_session):
    user_id = uuid.uuid4()
    await set_routing(
        db_session, CAPABILITY_PROFILES, "core_primary", profile_id=user_id
    )
    port = await resolve_profiles(db_session, user_id)
    assert isinstance(port, CoreProfile)


async def test_resolve_profiles_profile_row_beats_wildcard(db_session):
    """Canary POR PERFIL: la fila exacta gana al comodin del consumer."""
    user_id = uuid.uuid4()
    await set_routing(db_session, CAPABILITY_PROFILES, "core_primary")  # comodin
    await set_routing(db_session, CAPABILITY_PROFILES, "local", profile_id=user_id)
    assert isinstance(await resolve_profiles(db_session, user_id), LocalProfile)
    assert isinstance(await resolve_profiles(db_session, uuid.uuid4()), CoreProfile)


# ---------------------------------------------------------------------------
# Canary core_read: fallback funcional + severidades separadas
# ---------------------------------------------------------------------------


async def test_fallback_serves_local_when_core_down(seeded, db_session):
    user, ports, _ = seeded

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    seam = FallbackProfile(
        make_core_profile(db_session, httpx.MockTransport(refuse)),
        ports["local"],
    )
    view = await seam.get(user)
    assert view is not None
    assert view.title == LOCAL_PROFILE["title"]


async def test_canary_warn_levels_separate_expected_from_actionable(caplog):
    """Regla de la 2ª rev. A.SEAM catalogo, aplicada a perfiles: Unsupported
    (cota del contrato, esperado) va a DEBUG; el core CAIDO
    (CoreUnavailableError) es el UNICO WARNING del canary."""

    class _Unsupported:
        async def get(self, user):
            raise ProfileUnsupportedError("cota contrato")

    class _Down:
        async def get(self, user):
            raise CoreUnavailableError("core caido")

    class _Fallback:
        async def get(self, user):
            return None

    with caplog.at_level(logging.DEBUG, logger="services.profiles.seam"):
        await FallbackProfile(_Unsupported(), _Fallback()).get(None)
        await FallbackProfile(_Down(), _Fallback()).get(None)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warns) == 1 and "core caido" in warns[0].getMessage()
    assert len(debugs) == 1 and "cota contrato" in debugs[0].getMessage()


# ---------------------------------------------------------------------------
# Router: la costura llega a HTTP con las semanticas de la matriz de escritor
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


async def test_router_core_primary_core_down_read_503_writes_local_200(
    client, db_session, monkeypatch
):
    """core_primary SIN fallback silencioso: core inaccesible => 503 en la
    LECTURA; las ESCRITURAS y GDPR son del escritor LOCAL y siguen sirviendo
    (criterio unificador: ningun estado local inaccesible por el routing)."""
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    user_id, headers = await _register(client)
    await set_routing(
        db_session, CAPABILITY_PROFILES, "core_primary", profile_id=user_id
    )
    resp = await client.get("/api/v1/profile", headers=headers)
    assert resp.status_code == 503
    # PUT: escritor local + recibo local, no pasa por la costura de lectura.
    resp = await client.put(
        "/api/v1/profile", headers=headers, json={"title": "Written Locally"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Written Locally"
    # DELETE /cv: escritor local.
    resp = await client.delete("/api/v1/profile/cv", headers=headers)
    assert resp.status_code == 200
    # GDPR export: siempre almacen local, fuera de la costura.
    resp = await client.get("/api/v1/profile/export", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["profile"]["title"] == "Written Locally"


async def test_router_core_read_falls_back_to_local(client, db_session, monkeypatch):
    """core_read = canary: con el core inaccesible el perfil se sirve LOCAL."""
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    user_id, headers = await _register(client)
    await set_routing(db_session, CAPABILITY_PROFILES, "core_read", profile_id=user_id)
    resp = await client.get("/api/v1/profile", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user_id"] == str(user_id)


async def test_router_default_local_untouched(client):
    """Sin filas de routing todo sigue local (default seguro del plan)."""
    _, headers = await _register(client)
    resp = await client.get("/api/v1/profile", headers=headers)
    assert resp.status_code == 200


@pytest.mark.parametrize("mode", ["core_read", "core_primary"])
async def test_router_serves_core_content_and_local_write_receipt(
    client, db_session, monkeypatch, mode
):
    """Cableado completo: {title, cv_text, skills} del CORE + resto del
    escritor local; el PUT devuelve su recibo LOCAL y la lectura siguiente
    sigue sirviendo la proyeccion (la frescura es la METRICA del canary —
    GATE-SOMBRA — no un defecto a ocultar)."""
    user_id, headers = await _register(client)
    # El perfil creado en el registro esta vacio: darle estado local.
    resp = await client.put(
        "/api/v1/profile",
        headers=headers,
        json={"title": "Local Title", "salary_min": 80000},
    )
    assert resp.status_code == 200
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id)
    fake = FakeProfileApi(
        core_profile_id,
        str(user_id),
        {"title": "Projected Title", "cv_text": None, "skills": ["core-skill"]},
    )

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://core-api:8000/v1",
            headers={"Authorization": f"Bearer {TEST_CONSUMER_KEY}"},
            timeout=1.0,
            transport=fake.transport(),
        )

    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", TEST_CONSUMER_KEY)
    monkeypatch.setattr(core_client, "default_client_factory", factory)
    await set_routing(db_session, CAPABILITY_PROFILES, mode, profile_id=user_id)

    resp = await client.get("/api/v1/profile", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Projected Title"  # del core
    assert body["skills"] == ["core-skill"]  # del core
    assert body["salary_min"] == 80000  # del escritor local (cota /v1)

    # Escritura local: recibo inmediato del escritor...
    resp = await client.put(
        "/api/v1/profile", headers=headers, json={"title": "Fresh Local Title"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Fresh Local Title"
    # ...y la LECTURA sigue sirviendo la proyeccion hasta que la sombra
    # aterrice (lag del CDC = metrica del canary, cota documentada).
    resp = await client.get("/api/v1/profile", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Projected Title"
