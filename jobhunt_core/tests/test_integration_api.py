"""API /v1 read-only multi-tenant (A-09) contra Postgres real.

DoD: matriz ruta→scope (403 sin scope) · ownership por tenant (404 cross,
indistinguible de ausente) · corpus GLOBAL para vacancies · DTOs §2 · ETag/304
· cursor keyset opaco · CONTRACT TESTS NEGATIVOS cross-consumer obligatorios.
Ejecutar vía core-migrate.
"""

import asyncio
import datetime
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import credentials, embeddings, matching, profiles
from jobhunt_core.config import settings
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests import test_integration_matching as tim

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

ALL_SCOPES = ["vacancies:read", "profiles:read", "matches:read"]


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {
        "sources": [], "scopes": [], "models": [], "consumers": [],
        "policies": [], "extra_vacs": [],
    }
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            # Winners de merged_into: DESPUÉS del grafo (las vacantes de fuente
            # que los referencian deben caer antes).
            if created["extra_vacs"]:
                await s.execute(
                    sa.text("DELETE FROM vacancies WHERE id = ANY(:v)"),
                    {"v": created["extra_vacs"]},
                )
            await dbcleanup.purge_policies(s, created["policies"])
            for mid in created["models"]:
                await dbcleanup.purge_model(s, mid)
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _api(factory, url, token=None, headers=None, method="GET"):
    """Petición contra la app real (ASGITransport) con la sesión inyectada."""

    async def go():
        from httpx import ASGITransport, AsyncClient

        from jobhunt_core.api import deps
        from jobhunt_core.api.main import app

        async def override_session():
            async with factory() as s:
                yield s

        app.dependency_overrides[deps.get_session] = override_session
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                h = dict(headers or {})
                if token is not None:
                    h["Authorization"] = f"Bearer {token}"
                return await client.request(method, url, headers=h)
        finally:
            app.dependency_overrides.clear()

    return asyncio.run(go())


def _issue(factory, created, consumer_name, scopes, expires_at=None):
    async def go():
        async with factory() as s:
            cid = await profiles.ensure_consumer(s, consumer_name)
            if cid not in created["consumers"]:
                created["consumers"].append(cid)
            key_id, secret = await credentials.create_credential(
                s, cid, scopes, expires_at=expires_at
            )
            await s.commit()
            return cid, key_id, f"{key_id}.{secret}"

    return asyncio.run(go())


def _seed_matches(factory, created, titles=("backend python", "data eng")):
    """Ofertas + perfil + evaluación (helpers de A-08) → (pid, vacs, token)."""
    pid, mid, polid, vacs = tim._setup(factory, created, list(titles))
    tim._evaluate(factory, pid, mid, polid)
    _cid, _kid, token = _issue(factory, created, "tenant-match", ALL_SCOPES)
    return pid, vacs, token


def test_auth_negative_catalog(db):
    """Contract tests NEGATIVOS de credencial: cualquier causa → 401 con la
    forma {code, message, details}, sin distinguir motivo."""
    factory, created = db
    _cid, key_id, token = _issue(factory, created, "tenant-a", ALL_SCOPES)
    url = f"/v1/vacancies/{uuid.uuid4()}"

    for bad_token, desc in (
        (None, "sin cabecera"),
        ("basura-sin-punto", "formato inválido"),
        (f"{key_id}.secreto-equivocado", "secreto malo"),
        (f"{'0' * 16}.{'x' * 43}", "key_id inexistente"),
    ):
        r = _api(factory, url, token=bad_token)
        assert r.status_code == 401, desc
        body = r.json()
        assert body["code"] == "unauthorized" and "message" in body and "details" in body

    # Revocada y caducada → mismo 401.
    async def revoke():
        async with factory() as s:
            await credentials.revoke_credential(s, key_id)
            await s.commit()

    asyncio.run(revoke())
    assert _api(factory, url, token=token).status_code == 401

    _cid2, _kid2, expired = _issue(
        factory, created, "tenant-a", ALL_SCOPES,
        expires_at=datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=1),
    )
    assert _api(factory, url, token=expired).status_code == 401


def test_scope_matrix_403(db):
    """Matriz ruta→scope (§2): credencial VÁLIDA sin el scope → 403 con el
    scope requerido en details."""
    factory, created = db
    _cid, _kid, only_vac = _issue(factory, created, "tenant-a", ["vacancies:read"])
    _cid2, _kid2, only_prof = _issue(factory, created, "tenant-a", ["profiles:read"])
    pid = uuid.uuid4()

    r = _api(factory, f"/v1/profiles/{pid}", token=only_vac)
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    assert r.json()["details"] == {"required_scope": "profiles:read"}
    r = _api(factory, f"/v1/profiles/{pid}/matches", token=only_vac)
    assert (r.status_code, r.json()["details"]["required_scope"]) == (403, "matches:read")
    r = _api(factory, f"/v1/vacancies/{uuid.uuid4()}", token=only_prof)
    assert (r.status_code, r.json()["details"]["required_scope"]) == (403, "vacancies:read")


def test_cross_tenant_404_and_global_corpus(db):
    """Ownership §2: el perfil de A visto por B → 404 INDISTINGUIBLE de
    ausente; el corpus de vacantes es GLOBAL (B puede leerlo)."""
    factory, created = db
    pid, vacs, token_a = _seed_matches(factory, created)
    _cidb, _kidb, token_b = _issue(factory, created, "tenant-b", ALL_SCOPES)

    r_cross = _api(factory, f"/v1/profiles/{pid}", token=token_b)
    r_absent = _api(factory, f"/v1/profiles/{uuid.uuid4()}", token=token_b)
    assert r_cross.status_code == r_absent.status_code == 404
    assert r_cross.json() == r_absent.json()  # indistinguibles

    r = _api(factory, f"/v1/profiles/{pid}/matches", token=token_b)
    assert r.status_code == 404

    vid = vacs["backend python"]
    r = _api(factory, f"/v1/vacancies/{vid}", token=token_b)
    assert r.status_code == 200  # corpus GLOBAL


def test_vacancy_dto_shape_and_404s(db):
    factory, created = db
    pid, vacs, token = _seed_matches(factory, created)
    vid = vacs["backend python"]

    r = _api(factory, f"/v1/vacancies/{vid}", token=token)
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "backend python"
    assert body["company"] == "ACME AG"
    assert body["tags"] == ["t"]
    assert body["translations"] == []
    assert body["primary_listing"]["source"] == "arbeitnow"
    assert body["primary_listing"]["external_id"] == "j0"
    assert body["primary_listing"]["last_seen_at"] is not None
    assert [x["source"] for x in body["listings"]] == ["arbeitnow"]

    assert _api(factory, f"/v1/vacancies/{uuid.uuid4()}", token=token).status_code == 404

    async def archive():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                {"v": vid},
            )
            await s.commit()

    asyncio.run(archive())
    r = _api(factory, f"/v1/vacancies/{vid}", token=token)
    assert r.status_code == 404  # solo ACTIVAS (§2)

    # Auditoría A-09: FUNDIDA (merged_into) también queda fuera del corpus.
    vid2 = vacs["data eng"]

    async def merge():
        async with factory() as s:
            winner = uuid.uuid4()
            created["extra_vacs"].append(winner)
            await s.execute(sa.text("INSERT INTO vacancies (id) VALUES (:w)"), {"w": winner})
            await s.execute(
                sa.text("UPDATE vacancies SET merged_into = :w WHERE id = :v"),
                {"w": winner, "v": vid2},
            )
            await s.commit()

    asyncio.run(merge())
    assert _api(factory, f"/v1/vacancies/{vid2}", token=token).status_code == 404


def test_vacancy_etag_304_and_change(db):
    factory, created = db
    pid, vacs, token = _seed_matches(factory, created, titles=("backend python",))
    vid = vacs["backend python"]
    url = f"/v1/vacancies/{vid}"

    r1 = _api(factory, url, token=token)
    etag = r1.headers["etag"]
    r2 = _api(factory, url, token=token, headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.headers["etag"] == etag

    # El contenido cambia → representación nueva → ETag nuevo y 200.
    async def change():
        async with factory() as s:
            scope_id = created["scopes"][0]
            from jobhunt_core.harvest.sink import RawListingSink

            await RawListingSink().handle(
                s, str(scope_id), (tim._listing("j0", "backend python senior"),)
            )
            await s.commit()

    asyncio.run(change())
    r3 = _api(factory, url, token=token, headers={"If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["etag"] != etag
    assert r3.json()["title"] == "backend python senior"


def test_profile_dto_and_etag(db):
    factory, created = db
    pid, vacs, token = _seed_matches(factory, created)
    r = _api(factory, f"/v1/profiles/{pid}", token=token)
    assert r.status_code == 200
    body = r.json()
    assert body["external_ref"] == "user-1"
    assert body["current_revision"]["content"]["title"] == "python dev"
    etag = r.headers["etag"]
    r2 = _api(factory, f"/v1/profiles/{pid}", token=token, headers={"If-None-Match": etag})
    assert r2.status_code == 304

    # Auditoría A-09: una revisión NUEVA cambia la representación → ETag nuevo.
    async def new_revision():
        async with factory() as s:
            await profiles.save_profile_revision(
                s, uuid.UUID(str(pid)), {"title": "arquitecto", "skills": ["aws"]}
            )
            await s.commit()

    asyncio.run(new_revision())
    r3 = _api(factory, f"/v1/profiles/{pid}", token=token, headers={"If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["etag"] != etag
    assert r3.json()["current_revision"]["content"]["title"] == "arquitecto"


def test_profile_without_revision_serves_null_current(db):
    """Auditoría A-09: perfil sin revisión → 200 con current_revision null y
    ETag presente (la rama null del DTO no estaba cubierta)."""
    factory, created = db
    cid, _kid, token = _issue(factory, created, "tenant-nuevo", ALL_SCOPES)

    async def mk():
        async with factory() as s:
            pid = await profiles.upsert_profile(s, cid, "user-sin-cv")
            await s.commit()
            return pid

    pid = asyncio.run(mk())
    r = _api(factory, f"/v1/profiles/{pid}", token=token)
    assert r.status_code == 200
    assert r.json()["current_revision"] is None
    assert "etag" in r.headers


def test_matches_dto_pagination_and_errors(db):
    factory, created = db
    pid, vacs, token = _seed_matches(
        factory, created, titles=("backend python", "data eng", "qa manual")
    )
    base = f"/v1/profiles/{pid}/matches"

    r = _api(factory, base, token=token)
    assert r.status_code == 200
    full = r.json()
    assert len(full["items"]) == 3 and full["next_cursor"] is None
    scores = [it["evaluation"]["score_final"] for it in full["items"]]
    assert scores == sorted(scores, reverse=True)  # score DESC
    first = full["items"][0]
    assert first["evaluation"]["model"] == {"name": "modelo-match", "version": tim.SHA_A}
    assert first["evaluation"]["policy"] == {"name": "cosine", "prompt_version": "v1"}
    assert "similarity" in first["evaluation"]["scores"]
    assert first["state"] == {"saved": False, "dismissed": False, "feedback": None, "notes": None}
    assert first["vacancy"]["primary_listing"]["source"] == "arbeitnow"

    # Keyset opaco: página de 1 → cursor → resto, sin repetir ni saltar.
    r1 = _api(factory, base + "?limit=1", token=token)
    cur = r1.json()["next_cursor"]
    assert cur is not None
    r2 = _api(factory, base + f"?limit=100&cursor={cur}", token=token)
    ids = [it["vacancy"]["id"] for it in r1.json()["items"]] + [
        it["vacancy"]["id"] for it in r2.json()["items"]
    ]
    assert ids == [it["vacancy"]["id"] for it in full["items"]]

    # Dismissed fuera del feed vía API.
    async def dismiss():
        async with factory() as s:
            await matching.set_dismissed(s, pid, uuid.UUID(first["vacancy"]["id"]), True)
            await s.commit()

    asyncio.run(dismiss())
    r = _api(factory, base, token=token)
    assert len(r.json()["items"]) == 2

    # Errores del contrato: cursor ilegible y limit fuera de rango → 400.
    r = _api(factory, base + "?cursor=%21%21%21no-cursor", token=token)
    assert (r.status_code, r.json()["code"]) == (400, "invalid_cursor")
    for bad in (0, 101):
        # Los límites los declara Query(ge/le) → validación → sobre uniforme.
        r = _api(factory, base + f"?limit={bad}", token=token)
        assert (r.status_code, r.json()["code"]) == (400, "invalid_request")

    # Auditoría A-09: cursor con Decimal NO finito → 400, jamás keyset roto.
    # Auditoría final: también MAGNITUD desbordante — 1E262144 lanza DataError
    # en el driver (→ 500) y 1E200000 se codificaba en silencio como 0.
    import base64 as b64

    for score in ("NaN", "-Infinity", "1E262144", "1E200000"):
        cur_bad = b64.urlsafe_b64encode(f"{score}|{uuid.uuid4()}".encode()).decode()
        r = _api(factory, base + f"?cursor={cur_bad}", token=token)
        assert (r.status_code, r.json()["code"]) == (400, "invalid_cursor")

    # Auditoría A-09 P2: la entrada MALFORMADA lleva el MISMO sobre del
    # contrato (nada de 422 con {'detail': ...}).
    r = _api(factory, base + "?limit=abc", token=token)
    assert (r.status_code, r.json()["code"]) == (400, "invalid_request")
    r = _api(factory, "/v1/vacancies/no-es-uuid", token=token)
    assert (r.status_code, r.json()["code"]) == (400, "invalid_request")
    assert "details" in r.json() and "message" in r.json()


def test_profile_reassignment_never_leaks(db):
    """Rev. A-09 #1 (repro): el ownership va EN la query y en UNA sentencia.
    Tras reasignar el perfil al tenant B (con revisión nueva 'secreta'), el
    tenant A recibe 404 en perfil y matches; y el feed llamado con el
    consumer de A devuelve CERO filas aunque el profile_id sea correcto."""
    factory, created = db
    pid, vacs, token_a = _seed_matches(factory, created)
    cid_a = created["consumers"][0]
    cid_b, _kb, token_b = _issue(factory, created, "tenant-b", ALL_SCOPES)

    async def reassign():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE profiles SET consumer_id = :b WHERE id = :p"),
                {"b": cid_b, "p": pid},
            )
            await profiles.save_profile_revision(
                s, pid, {"title": "SECRETO DEL NUEVO TENANT", "skills": ["x"]}
            )
            await s.commit()

    asyncio.run(reassign())
    assert _api(factory, f"/v1/profiles/{pid}", token=token_a).status_code == 404
    assert _api(factory, f"/v1/profiles/{pid}/matches", token=token_a).status_code == 404
    r = _api(factory, f"/v1/profiles/{pid}", token=token_b)
    assert r.status_code == 200  # el dueño NUEVO sí lo ve
    assert "SECRETO" in r.json()["current_revision"]["content"]["title"]

    # Cinturón SQL del feed: consumer de A + profile_id correcto → 0 filas.
    async def direct_feed():
        async with factory() as s:
            rows, _ = await matching.feed(s, pid, consumer_id=cid_a)
            return rows

    assert asyncio.run(direct_feed()) == []


def test_matches_etag_and_state_change(db):
    """Rev. A-09 #2: /matches TIENE ETag — estable entre llamadas idénticas,
    304 con If-None-Match y cambia cuando cambia la página (dismiss)."""
    factory, created = db
    pid, vacs, token = _seed_matches(factory, created)
    base = f"/v1/profiles/{pid}/matches"

    r1 = _api(factory, base, token=token)
    etag = r1.headers["etag"]
    assert _api(factory, base, token=token).headers["etag"] == etag  # estable
    r304 = _api(factory, base, token=token, headers={"If-None-Match": etag})
    assert r304.status_code == 304

    async def dismiss():
        async with factory() as s:
            await matching.set_dismissed(s, pid, next(iter(vacs.values())), True)
            await s.commit()

    asyncio.run(dismiss())
    r2 = _api(factory, base, token=token, headers={"If-None-Match": etag})
    assert r2.status_code == 200 and r2.headers["etag"] != etag


def test_if_none_match_http_semantics(db):
    """Rev. A-09 #5: comodín `*`, comparación DÉBIL (W/) y listas de entidades
    satisfacen la condición en GET."""
    factory, created = db
    pid, vacs, token = _seed_matches(factory, created, titles=("backend python",))
    url = f"/v1/vacancies/{vacs['backend python']}"
    etag = _api(factory, url, token=token).headers["etag"]
    for header in ("*", f"W/{etag}", f'"otro-etag", {etag}'):
        r = _api(factory, url, token=token, headers={"If-None-Match": header})
        assert r.status_code == 304, header


def test_router_404_and_405_wear_contract_envelope(db):
    """Rev. A-09 #3: los HTTPException de Starlette también llevan el sobre —
    ruta inexistente y método incorrecto incluidos."""
    factory, created = db
    r = _api(factory, "/v1/no-existe")
    assert (r.status_code, r.json()["code"]) == (404, "not_found")
    r = _api(factory, f"/v1/vacancies/{uuid.uuid4()}", method="POST")
    assert (r.status_code, r.json()["code"]) == (405, "method_not_allowed")
    assert set(r.json()) == {"code", "message", "details"}


def test_openapi_schema_exposed(db):
    factory, created = db
    r = _api(factory, "/v1/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    spec = r.json()
    # Rev. A-09 #6: OpenAPI FIEL al contrato — Bearer declarado, 400 (no 422),
    # 304 y 500 documentados en cada operación de negocio.
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    assert "ErrorDTO" in spec["components"]["schemas"]
    for p in (
        "/v1/vacancies/{vacancy_id}",
        "/v1/profiles/{profile_id}",
        "/v1/profiles/{profile_id}/matches",
    ):
        assert p in paths
        op = spec["paths"][p]["get"]
        assert op.get("security"), p  # securityScheme aplicado a la operación
        resps = op["responses"]
        assert "422" not in resps, p
        for status in ("400", "401", "403", "404", "304", "500"):
            assert status in resps, (p, status)

def test_listings_expose_external_id_and_etag_versioning(db):
    """P2 rev. externa A.SEAM: `external_id` en TODOS los listings activos —
    el alias legacy NO-primary (orden de ingestión core→legacy, attach por
    URL) también porta su MD5 accionable. Y el cambio de REPRESENTACIÓN
    queda versionado por el ETag: un If-None-Match calculado sobre la forma
    ANTIGUA (listings sin external_id) ya no revalida — 200 con la forma
    nueva, jamás un 304 sirviendo la vieja."""
    from jobhunt_core.api.v1 import _etag_of

    factory, created = db
    pid, vacs, token = _seed_matches(factory, created, titles=("backend python",))
    vid = vacs["backend python"]
    md5 = "d41d8cd98f00b204e9800998ecf8427e"
    legacy_source = f"legacy:apitest-{uuid.uuid4().hex[:6]}"

    # Attach de un segundo listing (fuente sombra legacy:*) a la MISMA
    # vacante: el primary sigue siendo arbeitnow; el alias llega después.
    async def attach_legacy():
        async with factory() as s:
            source_id, listing_id = uuid.uuid4(), uuid.uuid4()
            created["sources"].append(source_id)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :n, 0)"),
                {"id": source_id, "n": legacy_source},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO source_listings "
                    "(id, source_id, external_id, url_normalized) "
                    "VALUES (:id, :sid, :ext, :u)"
                ),
                {"id": listing_id, "sid": source_id, "ext": md5,
                 "u": f"https://x/alias-{md5[:8]}"},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO source_listing_incarnations "
                    "(id, source_listing_id, vacancy_id, seq, url) "
                    "VALUES (:id, :lid, :vid, 1, :u)"
                ),
                {"id": uuid.uuid4(), "lid": listing_id, "vid": vid,
                 "u": f"https://x/alias-{md5[:8]}"},
            )
            await s.commit()

    asyncio.run(attach_legacy())

    r = _api(factory, f"/v1/vacancies/{vid}", token=token)
    assert r.status_code == 200
    body = r.json()
    assert len(body["listings"]) == 2
    # external_id en TODOS los listings, incluido el alias legacy no-primary.
    assert all(x.get("external_id") for x in body["listings"])
    by_source = {x["source"]: x["external_id"] for x in body["listings"]}
    assert by_source[legacy_source] == md5
    assert body["primary_listing"]["source"] == "arbeitnow"  # primary intacto

    # Sanidad: el ETag servido ES el de la representación nueva…
    assert _etag_of(body) == r.headers["etag"]
    # …y la forma ANTIGUA (sin external_id en listings) YA NO revalida.
    old_shape = {
        **body,
        "listings": [
            {k: v for k, v in x.items() if k != "external_id"}
            for x in body["listings"]
        ],
    }
    r_old = _api(
        factory, f"/v1/vacancies/{vid}", token=token,
        headers={"If-None-Match": _etag_of(old_shape)},
    )
    assert r_old.status_code == 200  # la representación cambió: no revalida

    # El ETag real de la forma nueva sí revalida (304).
    r304 = _api(
        factory, f"/v1/vacancies/{vid}", token=token,
        headers={"If-None-Match": r.headers["etag"]},
    )
    assert r304.status_code == 304
