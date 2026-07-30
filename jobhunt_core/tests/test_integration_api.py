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


def _api(factory, url, token=None, headers=None, method="GET", json_body=None):
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
                kw = {"json": json_body} if json_body is not None else {}
                return await client.request(method, url, headers=h, **kw)
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


def _seed_catalog(factory, created, n=3):
    """n vacantes ACTIVAS presentables con un TOKEN único en el title: el feed
    /v1/vacancies es GLOBAL, así que `q=token` aísla el conjunto del test del
    ruido del corpus compartido (la BD de la suite). Devuelve (token, {título:
    vid}, token_de_auth con ALL_SCOPES)."""
    token = "capir" + uuid.uuid4().hex[:8]
    titles = tuple(f"{token}v{i}" for i in range(n))
    _pid, vacs, auth = _seed_matches(factory, created, titles=titles)
    return token, vacs, auth


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


# ---------- C-API-R: feed/búsqueda de catálogo GET /v1/vacancies ----------

def test_catalog_feed_only_active_and_by_id_coherent(db):
    """Feed §2/C-API-R: solo vacantes ACTIVAS y presentables; archivada/fundida
    salen del feed y son coherentes con el GET by id (404)."""
    factory, created = db
    token, vacs, auth = _seed_catalog(factory, created, n=3)
    base = "/v1/vacancies"

    body = _api(factory, base + f"?q={token}", token=auth).json()
    assert len(body["items"]) == 3
    assert all(token in it["title"] for it in body["items"])

    # Cross con el GET by id: la lectura directa del primero coincide.
    first = body["items"][0]
    r_one = _api(factory, base + f"/{first['id']}", token=auth)
    assert r_one.status_code == 200
    assert (r_one.json()["id"], r_one.json()["title"]) == (first["id"], first["title"])

    ids = [it["id"] for it in body["items"]]

    async def hide():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                {"v": ids[0]},
            )
            winner = uuid.uuid4()
            created["extra_vacs"].append(winner)
            await s.execute(sa.text("INSERT INTO vacancies (id) VALUES (:w)"), {"w": winner})
            await s.execute(
                sa.text("UPDATE vacancies SET merged_into = :w WHERE id = :v"),
                {"w": winner, "v": ids[1]},
            )
            await s.commit()

    asyncio.run(hide())
    remaining = [it["id"] for it in _api(factory, base + f"?q={token}", token=auth).json()["items"]]
    assert remaining == [ids[2]]  # solo la ACTIVA queda
    assert _api(factory, base + f"/{ids[0]}", token=auth).status_code == 404  # coherente


def test_catalog_keyset_pagination_stable(db):
    """Keyset OPACO estable: paginar en trozos == la página completa, sin
    solapes ni huecos (created_at idéntico ⇒ ejercita el desempate por id)."""
    factory, created = db
    token, vacs, auth = _seed_catalog(factory, created, n=5)
    base = f"/v1/vacancies?q={token}"

    full = _api(factory, base, token=auth).json()
    assert len(full["items"]) == 5 and full["next_cursor"] is None
    order = [it["id"] for it in full["items"]]

    collected: list = []
    cursor = None
    for _ in range(10):  # cota de seguridad contra bucle infinito
        url = base + "&limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = _api(factory, url, token=auth).json()
        collected += [it["id"] for it in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert collected == order  # mismo orden, sin solapes
    assert len(collected) == len(set(collected)) == 5  # sin huecos ni duplicados


def test_catalog_q_substring_case_insensitive(db):
    """`q` = substring case-insensitive (mínimo honesto) sobre title/company."""
    factory, created = db
    token, vacs, auth = _seed_catalog(factory, created, n=3)
    base = "/v1/vacancies"

    assert len(_api(factory, base + f"?q={token}", token=auth).json()["items"]) == 3
    # Mayúsculas filtran igual (case-insensitive).
    assert len(_api(factory, base + f"?q={token.upper()}", token=auth).json()["items"]) == 3
    # Substring presente en un solo title.
    only = f"{token}v1"
    got = _api(factory, base + f"?q={only}", token=auth).json()["items"]
    assert len(got) == 1 and got[0]["title"] == only
    # Token inexistente ⇒ 0 filas.
    assert _api(factory, base + f"?q={token}zzz", token=auth).json()["items"] == []


def test_catalog_cursor_and_limit_errors(db):
    """Cursor OPACO inválido ⇒ 400 invalid_cursor (incl. la COTA propia:
    timestamp sin zona horaria); limit fuera de rango ⇒ 400 invalid_request."""
    import base64 as b64

    factory, created = db
    _cid, _kid, auth = _issue(factory, created, "tenant-a", ALL_SCOPES)
    base = "/v1/vacancies"

    r = _api(factory, base + "?cursor=%21%21%21no-cursor", token=auth)
    assert (r.status_code, r.json()["code"]) == (400, "invalid_cursor")
    # base64 válido pero timestamp NAIVE (sin tz) y timestamp basura ⇒ 400.
    for payload in (f"2026-07-30T00:00:00|{uuid.uuid4()}", f"no-es-fecha|{uuid.uuid4()}"):
        cur = b64.urlsafe_b64encode(payload.encode()).decode()
        r = _api(factory, base + f"?cursor={cur}", token=auth)
        assert (r.status_code, r.json()["code"]) == (400, "invalid_cursor"), payload
    for badlim in (0, 101):
        r = _api(factory, base + f"?limit={badlim}", token=auth)
        assert (r.status_code, r.json()["code"]) == (400, "invalid_request")


def test_catalog_etag_304(db):
    factory, created = db
    token, vacs, auth = _seed_catalog(factory, created, n=2)
    url = f"/v1/vacancies?q={token}"
    r1 = _api(factory, url, token=auth)
    etag = r1.headers["etag"]
    r2 = _api(factory, url, token=auth, headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.headers["etag"] == etag


def test_catalog_scope_required(db):
    """Sin credencial ⇒ 401; credencial válida sin vacancies:read ⇒ 403."""
    factory, created = db
    _cid, _kid, only_prof = _issue(factory, created, "tenant-a", ["profiles:read"])
    base = "/v1/vacancies"
    assert _api(factory, base).status_code == 401
    r = _api(factory, base, token=only_prof)
    assert (r.status_code, r.json()["details"]["required_scope"]) == (403, "vacancies:read")


# ---------- C-API-W: escritura del /v1 (PUT perfil + idempotency key) ----------

WRITE_SCOPES = ALL_SCOPES + ["profiles:write"]


def _seed_writable(factory, created):
    """Perfil PROPIO de un tenant + token que incluye profiles:write. El perfil
    lo crea _seed_matches bajo el consumer 'tenant-match'; ensure_consumer es
    idempotente, así que el token de escritura pertenece al MISMO tenant."""
    pid, vacs, _ro = _seed_matches(factory, created)
    _cid, _kid, token = _issue(factory, created, "tenant-match", WRITE_SCOPES)
    return pid, vacs, token


def _activation_count(factory, pid):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT count(*) FROM profile_revision_activations "
                        "WHERE profile_id = :p"
                    ),
                    {"p": pid},
                )
            ).scalar_one()

    return asyncio.run(go())


def test_put_profile_creates_revision_and_returns_etag(db):
    """PUT perfil (C-3 CV push): escribe una revisión VIGENTE y devuelve la
    representación nueva con su ETag — coherente con el GET posterior."""
    factory, created = db
    pid, _vacs, token = _seed_writable(factory, created)
    url = f"/v1/profiles/{pid}"
    body = {"title": "staff engineer", "cv_text": "20 anios", "skills": ["python", "pg"]}

    r = _api(factory, url, token=token, method="PUT", json_body=body)
    assert r.status_code == 200
    assert r.json()["current_revision"]["content"]["title"] == "staff engineer"
    assert r.json()["current_revision"]["content"]["skills"] == ["python", "pg"]
    assert "etag" in r.headers

    g = _api(factory, url, token=token)
    assert g.json()["current_revision"]["content"]["title"] == "staff engineer"
    assert g.headers["etag"] == r.headers["etag"]  # la escritura ES la vigente


def test_put_profile_cross_tenant_and_absent_404(db):
    """Ownership por tenant: el perfil de A escrito por B → 404 INDISTINGUIBLE
    de un perfil ausente (no revela existencia, como el GET)."""
    factory, created = db
    pid, _vacs, _tok = _seed_writable(factory, created)
    _cidb, _kidb, token_b = _issue(factory, created, "tenant-b", WRITE_SCOPES)

    r_cross = _api(factory, f"/v1/profiles/{pid}", token=token_b, method="PUT",
                   json_body={"title": "hijack"})
    r_absent = _api(factory, f"/v1/profiles/{uuid.uuid4()}", token=token_b,
                    method="PUT", json_body={"title": "x"})
    assert r_cross.status_code == r_absent.status_code == 404
    assert r_cross.json() == r_absent.json()
    # La escritura ajena NO ocurrió: el título del dueño sigue intacto.
    _cid, _kid, token_a = _issue(factory, created, "tenant-match", WRITE_SCOPES)
    assert _api(factory, f"/v1/profiles/{pid}", token=token_a).json()[
        "current_revision"]["content"]["title"] == "python dev"


def test_put_profile_requires_write_scope(db):
    """Matriz ruta→scope: credencial de solo-lectura sin profiles:write → 403."""
    factory, created = db
    pid, _vacs, _tok = _seed_writable(factory, created)
    _cid, _kid, ro = _issue(factory, created, "tenant-match", ALL_SCOPES)
    r = _api(factory, f"/v1/profiles/{pid}", token=ro, method="PUT",
             json_body={"title": "x"})
    assert (r.status_code, r.json()["details"]["required_scope"]) == (403, "profiles:write")


def test_put_profile_if_match_precondition_412(db):
    """Precondición optimista If-Match: con el ETag ACTUAL escribe (200);
    con un ETag OBSOLETO → 412 y la escritura no ocurre."""
    factory, created = db
    pid, _vacs, token = _seed_writable(factory, created)
    url = f"/v1/profiles/{pid}"
    stale = _api(factory, url, token=token).headers["etag"]

    r_ok = _api(factory, url, token=token, method="PUT",
                headers={"If-Match": stale}, json_body={"title": "nuevo"})
    assert r_ok.status_code == 200 and r_ok.headers["etag"] != stale

    r_stale = _api(factory, url, token=token, method="PUT",
                   headers={"If-Match": stale}, json_body={"title": "otro"})
    assert (r_stale.status_code, r_stale.json()["code"]) == (412, "precondition_failed")
    assert _api(factory, url, token=token).json()[
        "current_revision"]["content"]["title"] == "nuevo"  # 'otro' no se escribió

    # If-Match con validador DÉBIL del ETag ACTUAL (1ª rev.): RFC 9110 §13.1.1
    # exige comparación FUERTE → W/ nunca satisface la precondición ⇒ 412
    # (a diferencia de If-None-Match, que sí admite la débil).
    cur = _api(factory, url, token=token).headers["etag"]
    r_weak = _api(factory, url, token=token, method="PUT",
                  headers={"If-Match": "W/" + cur}, json_body={"title": "via-debil"})
    assert (r_weak.status_code, r_weak.json()["code"]) == (412, "precondition_failed")


def test_idempotency_same_key_replays_without_reexecution(db):
    """Idempotency-Key: el reintento con la MISMA key y MISMO cuerpo devuelve
    la respuesta GUARDADA sin re-ejecutar el handler — probado con una
    intervención externa entre ambas llamadas: si re-ejecutara, re-activaría la
    revisión (activación nueva); no lo hace."""
    factory, created = db
    pid, _vacs, token = _seed_writable(factory, created)
    url = f"/v1/profiles/{pid}"
    body = {"title": "idem-title", "skills": ["go"]}
    h = {"Idempotency-Key": "put-key-1"}

    r1 = _api(factory, url, token=token, method="PUT", headers=h, json_body=body)
    assert r1.status_code == 200

    # Intervención externa: activa OTRA revisión (la vigente deja de ser idem-title).
    async def bump():
        async with factory() as s:
            await profiles.save_profile_revision(
                s, uuid.UUID(str(pid)), {"title": "cambiado-por-fuera"}
            )
            await s.commit()

    asyncio.run(bump())
    before = _activation_count(factory, pid)

    r2 = _api(factory, url, token=token, method="PUT", headers=h, json_body=body)
    assert r2.status_code == 200
    assert r2.json() == r1.json() and r2.headers["etag"] == r1.headers["etag"]
    # Respuesta GUARDADA (refleja el estado de r1, no la intervención externa).
    assert r2.json()["current_revision"]["content"]["title"] == "idem-title"
    # Y CERO re-ejecución: ninguna activación nueva.
    assert _activation_count(factory, pid) == before


def test_idempotency_different_key_reexecutes(db):
    """Key DISTINTA (o ausente) ⇒ ejecución nueva: la segunda escritura sí muta."""
    factory, created = db
    pid, _vacs, token = _seed_writable(factory, created)
    url = f"/v1/profiles/{pid}"
    r1 = _api(factory, url, token=token, method="PUT",
              headers={"Idempotency-Key": "k-a"}, json_body={"title": "aaa"})
    r2 = _api(factory, url, token=token, method="PUT",
              headers={"Idempotency-Key": "k-b"}, json_body={"title": "bbb"})
    assert r1.json()["current_revision"]["content"]["title"] == "aaa"
    assert r2.json()["current_revision"]["content"]["title"] == "bbb"


def test_idempotency_same_key_different_body_409(db):
    """Misma key + cuerpo DISTINTO ⇒ 409 idempotency_conflict; el estado no
    avanza al segundo cuerpo (la key ya está ligada al primero)."""
    factory, created = db
    pid, _vacs, token = _seed_writable(factory, created)
    url = f"/v1/profiles/{pid}"
    h = {"Idempotency-Key": "k-x"}
    r1 = _api(factory, url, token=token, method="PUT", headers=h, json_body={"title": "one"})
    assert r1.status_code == 200
    r2 = _api(factory, url, token=token, method="PUT", headers=h, json_body={"title": "two"})
    assert (r2.status_code, r2.json()["code"]) == (409, "idempotency_conflict")
    assert _api(factory, url, token=token).json()[
        "current_revision"]["content"]["title"] == "one"


def test_idempotency_concurrent_same_key_single_execution(db):
    """Dos requests SIMULTÁNEOS con la misma key: el candado (PK natural +
    espera acotada en el INSERT ON CONFLICT) garantiza UNA sola ejecución;
    ambos devuelven la MISMA respuesta."""
    factory, created = db
    pid, _vacs, token = _seed_writable(factory, created)
    url = f"/v1/profiles/{pid}"
    body = {"title": "concurrent", "skills": ["rust"]}
    h = {"Authorization": f"Bearer {token}", "Idempotency-Key": "k-conc"}

    calls = {"n": 0}
    orig = profiles.save_profile_revision

    async def counting(*a, **k):
        calls["n"] += 1
        return await orig(*a, **k)

    async def go():
        from httpx import ASGITransport, AsyncClient

        from jobhunt_core.api import deps
        from jobhunt_core.api.main import app

        async def override_session():
            async with factory() as s:
                yield s

        app.dependency_overrides[deps.get_session] = override_session
        profiles.save_profile_revision = counting
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await asyncio.gather(
                    client.put(url, json=body, headers=h),
                    client.put(url, json=body, headers=h),
                )
        finally:
            profiles.save_profile_revision = orig
            app.dependency_overrides.clear()

    r1, r2 = asyncio.run(go())
    assert {r1.status_code, r2.status_code} == {200}
    assert r1.json() == r2.json() and r1.headers["etag"] == r2.headers["etag"]
    assert calls["n"] == 1  # UNA ejecución pese a la key compartida


def test_erase_gdpr_removes_applications_and_saved_searches(db):
    """DoD C-API-W: con el escritor activo, el erase GDPR (erase_shadow_profile
    / _ERASE_TABLES) arrastra applications + saved_searches (FK a profiles SIN
    CASCADE) — si no, el DELETE del perfil fallaría."""
    from jobhunt_core.shadow.projector import erase_shadow_profile

    factory, created = db
    pid, vacs, _tok = _seed_matches(factory, created)
    vid = vacs["backend python"]

    async def seed_durables():
        async with factory() as s:
            ref, cname = (
                await s.execute(
                    sa.text(
                        "SELECT p.external_ref, c.name FROM profiles p "
                        "JOIN consumers c ON c.id = p.consumer_id WHERE p.id = :p"
                    ),
                    {"p": pid},
                )
            ).one()
            await s.execute(
                sa.text(
                    "INSERT INTO applications (profile_id, vacancy_id, status) "
                    "VALUES (:p, :v, 'applied')"
                ),
                {"p": pid, "v": vid},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO saved_searches (profile_id, name) "
                    "VALUES (:p, 'remote python')"
                ),
                {"p": pid},
            )
            await s.commit()
            return ref, cname

    ref, cname = asyncio.run(seed_durables())

    async def do_erase():
        async with factory() as s:
            erased = await erase_shadow_profile(s, ref, consumer_name=cname)
            await s.commit()
            return erased

    assert asyncio.run(do_erase()) == pid

    async def counts():
        async with factory() as s:
            out = []
            for q in (
                "SELECT count(*) FROM applications WHERE profile_id = :p",
                "SELECT count(*) FROM saved_searches WHERE profile_id = :p",
                "SELECT count(*) FROM profiles WHERE id = :p",
            ):
                out.append((await s.execute(sa.text(q), {"p": pid})).scalar_one())
            return tuple(out)

    assert asyncio.run(counts()) == (0, 0, 0)
