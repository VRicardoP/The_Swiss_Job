"""G3/P2-7 y G3/P2-8 — `null` explícito sobre JSONB y validación de `filters`.

P2-7: `model_dump(exclude_unset=True)` NO distingue «campo ausente» de «campo
enviado como null». El None llega a una columna JSONB NOT NULL y SQLAlchemy lo
persiste como el valor JSON `null` — que para Postgres es un valor válido, así
que la red de seguridad NOT NULL no salta. Desde ahí la fila es ILEGIBLE: todo
GET del perfil (incluido el export GDPR) o del listado de búsquedas guardadas
responde 500 para siempre.

P2-8: `filters` era un `dict` libre. `{"source": 123}` o `{"canton": ["ZH"]}`
entraban sin protesta y reventaban la tarea `run_saved_searches`, que los
consume como texto — abortando el barrido de TODOS los usuarios.

Todos los tests recorren el camino REAL: cliente HTTP contra la app.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.documents import DocumentPageResponse
from tests.conftest import random_email


_TEST_PASSWORD = "TestPass123!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": random_email(),
            "password": _TEST_PASSWORD,
            "gdpr_consent": True,
        },
    )
    assert resp.status_code == 201
    return _auth(resp.json()["access_token"])


async def _call(coro, what: str):
    """Ejecuta la petición traduciendo una excepción de la app en fallo legible.

    Sin el fix la validación de la respuesta revienta DENTRO de la app y httpx
    (raise_app_exceptions) la propaga: sin esto el test moriría con un traceback
    en vez de con el motivo.
    """
    try:
        return await coro
    except Exception as exc:  # pragma: no cover — solo alcanzable sin el fix
        pytest.fail(f"{what} reventó en la app: {type(exc).__name__}: {exc}")


async def _poison(db: AsyncSession, table: str, column: str) -> None:
    """Envenena una columna JSONB con `'null'::jsonb`, como hacía el PUT."""
    await db.execute(text(f"UPDATE {table} SET {column} = 'null'::jsonb"))  # noqa: S608
    await db.commit()


async def _jsonb_typeof(db: AsyncSession, table: str, column: str) -> list[str]:
    rows = await db.execute(text(f"SELECT jsonb_typeof({column}) FROM {table}"))  # noqa: S608
    return [r[0] for r in rows.all()]


@pytest.fixture(autouse=True)
def _external_capabilities(monkeypatch):
    async def preference(db, user_id, local_value, *, enabled=None):
        return local_value if enabled is None else enabled

    class EmptyDocuments:
        async def page(self, user_id, cursor=None):
            return DocumentPageResponse(data=[])

    async def documents(*args, **kwargs):
        return EmptyDocuments()

    monkeypatch.setattr("routers.profile.school_preference", preference)
    monkeypatch.setattr("services.documents.export.resolve_documents", documents)


# ---------------------------------------------------------------------------
# G3/P2-7 — perfil
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestProfileNullJsonb:
    @pytest.mark.parametrize("field", ["skills", "languages", "locations"])
    async def test_put_null_no_corrompe_la_columna_ni_rompe_las_lecturas(
        self, client: AsyncClient, db_session: AsyncSession, field: str
    ):
        headers = await _register(client)
        seeded = ["python"]
        assert (
            await client.put("/api/v1/profile", headers=headers, json={field: seeded})
        ).status_code == 200

        resp = await _call(
            client.put("/api/v1/profile", headers=headers, json={field: None}),
            f"PUT /profile {{{field}: null}}",
        )
        assert resp.status_code == 200
        # `null` no es «borra»: la columna es NOT NULL, se ignora el campo.
        assert resp.json()[field] == seeded

        assert await _jsonb_typeof(db_session, "user_profiles", field) == ["array"]

        get_resp = await _call(
            client.get("/api/v1/profile", headers=headers), "GET /profile"
        )
        assert get_resp.status_code == 200
        assert get_resp.json()[field] == seeded

        export = await _call(
            client.get("/api/v1/profile/export", headers=headers), "GET /profile/export"
        )
        assert export.status_code == 200
        assert export.json()["profile"][field] == seeded

    async def test_null_sigue_borrando_los_campos_anulables_en_bd(
        self, client: AsyncClient
    ):
        """El fix no puede pasarse de largo: `title`/`experience_years`/
        `salary_min`/`salary_max`/`score_weights` SÍ admiten borrado por null."""
        headers = await _register(client)
        assert (
            await client.put(
                "/api/v1/profile",
                headers=headers,
                json={
                    "title": "Engineer",
                    "experience_years": 7,
                    "salary_min": 90000,
                    "salary_max": 120000,
                    "score_weights": {"embedding": 1.0},
                },
            )
        ).status_code == 200

        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={
                "title": None,
                "experience_years": None,
                "salary_min": None,
                "salary_max": None,
                "score_weights": None,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        for field in (
            "title",
            "experience_years",
            "salary_min",
            "salary_max",
            "score_weights",
        ):
            assert data[field] is None, field

    async def test_fila_ya_corrupta_se_lee_como_vacia(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Endurecimiento: las filas envenenadas ANTES del fix dejan de dar 500."""
        headers = await _register(client)
        await _poison(db_session, "user_profiles", "skills")

        resp = await _call(
            client.get("/api/v1/profile", headers=headers), "GET /profile"
        )
        assert resp.status_code == 200
        assert resp.json()["skills"] == []

        export = await _call(
            client.get("/api/v1/profile/export", headers=headers), "GET /profile/export"
        )
        assert export.status_code == 200
        assert export.json()["profile"]["skills"] == []


# ---------------------------------------------------------------------------
