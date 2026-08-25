"""API /v1 de búsquedas guardadas (C-4, DISEÑO v2.1) contra Postgres real.

DoD (suite core): Idempotency-Key REQUERIDA en el POST (R2-8: 400
idempotency_key_required; replay 201 byte a byte), 400 invalid_filters (R2-6),
PUT completo SOLO de client-writable con ausentes que conservan el valor
vigente y engine-owned INMUNES, homónimas legítimas (H10: sin 409 por nombre),
ETag/If-Match bajo FOR UPDATE (412), cursor keyset (created_at DESC, id DESC),
outbox `saved_search.changed` con revision MONOTÓNICA por agregado, ownership
por JOIN (404 indistinguible, 403 sin scope). Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import profiles
from jobhunt_core.config import settings
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests import test_integration_api as tia

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

SS_SCOPES = ["saved_searches:read", "saved_searches:write"]


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"consumers": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _seed_profile(factory, created, scopes=SS_SCOPES):
    tag = "c4ss" + uuid.uuid4().hex[:8]
    tenant = f"tenant-{tag}"

    async def go():
        async with factory() as s:
            cid = await profiles.ensure_consumer(s, tenant)
            created["consumers"].append(cid)
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            return pid

    pid = asyncio.run(go())
    _cid, _kid, token = tia._issue(factory, created, tenant, scopes)
    return token, tenant, pid


def _post(factory, token, body, key="auto"):
    headers = None
    if key is not None:
        headers = {"Idempotency-Key": (
            "ssk-" + uuid.uuid4().hex[:10] if key == "auto" else key
        )}
    return tia._api(
        factory, "/v1/saved-searches", token=token, headers=headers,
        method="POST", json_body=body,
    )


def test_post_requires_idempotency_key(db):
    """R2-8: sin header → 400 idempotency_key_required (código NUEVO del
    sobre); key malformada (vacía) → 400 invalid_idempotency_key. Nada se
    inserta en ninguno de los dos casos."""
    factory, created = db
    token, _tenant, pid = _seed_profile(factory, created)
    body = {"profile_id": str(pid), "name": "sin key"}
    r = _post(factory, token, body, key=None)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "idempotency_key_required"
    r = _post(factory, token, body, key="   ")
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_idempotency_key"
    assert _rows(
        factory, "SELECT 1 FROM saved_searches WHERE profile_id = :p", p=pid
    ) == []


def test_create_defaults_replay_and_event(db):
    """Create con defaults del core (daily/true — R2-6), replay 201 byte a
    byte con la MISMA key, y evento `saved_search.changed` revision=1 en el
    outbox con destino = consumer del perfil."""
    factory, created = db
    token, tenant, pid = _seed_profile(factory, created)
    key = "ssk-" + uuid.uuid4().hex[:8]
    body = {"profile_id": str(pid), "name": "python remoto", "min_score": 60,
            "filters": {"q": "python", "remote": True}}
    r1 = _post(factory, token, body, key=key)
    assert r1.status_code == 201, r1.text
    dto = r1.json()
    assert dto["notify_frequency"] == "daily" and dto["notify_push"] is True
    assert dto["is_active"] is True and dto["total_matches"] == 0
    assert dto["filters"] == {"q": "python", "remote": True}

    r2 = _post(factory, token, body, key=key)
    assert r2.status_code == 201
    assert r1.content == r2.content and r1.headers["etag"] == r2.headers["etag"]

    out = _rows(
        factory,
        "SELECT o.version, d.destination FROM integration_outbox o "
        "JOIN integration_outbox_deliveries d ON d.event_id = o.event_id "
        "WHERE o.subject_profile_id = :p AND o.type = 'saved_search.changed'",
        p=pid,
    )
    assert [(x.version, x.destination) for x in out] == [(1, tenant)]


def test_invalid_filters_400(db):
    """R2-6: filters no-objeto (string o null explícito) → 400 invalid_filters."""
    factory, created = db
    token, _tenant, pid = _seed_profile(factory, created)
    for bad in ("no-un-objeto", None, [1, 2]):
        r = _post(factory, token, {
            "profile_id": str(pid), "name": "x", "filters": bad,
        })
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "invalid_filters"


def test_homonyms_are_legitimate(db):
    """H10: dos búsquedas HOMÓNIMAS con keys distintas → ambas 201 (el
    candado anti-duplicado es la key, no el nombre; sin 409 por nombre)."""
    factory, created = db
    token, _tenant, pid = _seed_profile(factory, created)
    r1 = _post(factory, token, {"profile_id": str(pid), "name": "misma"})
    r2 = _post(factory, token, {"profile_id": str(pid), "name": "misma"})
    assert (r1.status_code, r2.status_code) == (201, 201)
    assert r1.json()["id"] != r2.json()["id"]


def test_put_partial_engine_owned_immune_and_revision(db):
    """Decisión 5: el PUT escribe SOLO client-writable presentes; los
    AUSENTES conservan el valor vigente; engine-owned (last_run_at,
    total_matches, created_at...) INMUNES aunque lleguen en el cuerpo.
    revision monotónica: create=1, put=2 en el outbox."""
    factory, created = db
    token, _tenant, pid = _seed_profile(factory, created)
    r = _post(factory, token, {
        "profile_id": str(pid), "name": "original", "min_score": 40,
        "notify_frequency": "weekly", "filters": {"q": "sre"},
    })
    dto = r.json()
    sid, etag = dto["id"], r.headers["etag"]

    put = tia._api(
        factory, f"/v1/saved-searches/{sid}", token=token, method="PUT",
        headers={"If-Match": etag},
        json_body={
            "min_score": 75,
            # engine-owned que el PUT debe IGNORAR (jamás machacar):
            "total_matches": 999, "last_run_at": "2020-01-01T00:00:00Z",
            "id": str(uuid.uuid4()), "created_at": "2020-01-01T00:00:00Z",
        },
    )
    assert put.status_code == 200, put.text
    after = put.json()
    assert after["min_score"] == 75
    # Ausentes conservan el valor vigente (consumer sin el campo — R2-6).
    assert after["name"] == "original"
    assert after["notify_frequency"] == "weekly"
    assert after["filters"] == {"q": "sre"}
    # Engine-owned inmunes.
    assert after["id"] == sid
    assert after["total_matches"] == 0 and after["last_run_at"] is None
    assert after["created_at"] == dto["created_at"]
    assert after["updated_at"] != dto["updated_at"]

    out = _rows(
        factory,
        "SELECT version FROM integration_outbox WHERE subject_profile_id = :p "
        "AND type = 'saved_search.changed' ORDER BY version",
        p=pid,
    )
    assert [o.version for o in out] == [1, 2]


def test_put_if_match_412_and_cross_tenant_404(db):
    """Decisión 2: If-Match errónea → 412 sin mutar; cross-tenant → 404
    INDISTINGUIBLE del inexistente; sin scope write → 403."""
    factory, created = db
    token, _tenant, pid = _seed_profile(factory, created)
    r = _post(factory, token, {"profile_id": str(pid), "name": "guardada"})
    sid = r.json()["id"]

    stale = tia._api(
        factory, f"/v1/saved-searches/{sid}", token=token, method="PUT",
        headers={"If-Match": '"deadbeef"'}, json_body={"min_score": 10},
    )
    assert stale.status_code == 412
    assert _rows(
        factory, "SELECT min_score FROM saved_searches WHERE id = :i",
        i=uuid.UUID(sid),
    )[0].min_score == 0

    _c, _k, intruder = tia._issue(factory, created, "tenant-intruso-ss", SS_SCOPES)
    r404 = tia._api(
        factory, f"/v1/saved-searches/{sid}", token=intruder, method="PUT",
        json_body={"min_score": 10},
    )
    missing = tia._api(
        factory, f"/v1/saved-searches/{uuid.uuid4()}", token=token, method="PUT",
        json_body={"min_score": 10},
    )
    assert r404.status_code == missing.status_code == 404
    assert r404.json()["code"] == missing.json()["code"] == "not_found"

    _c, _k, reader = tia._issue(
        factory, created, "tenant-lector-ss", ["saved_searches:read"]
    )
    forbidden = tia._api(
        factory, f"/v1/saved-searches/{sid}", token=reader, method="PUT",
        json_body={"min_score": 10},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["details"]["required_scope"] == "saved_searches:write"


def test_delete_204_last_event_then_404(db):
    """DELETE con If-Match: 204, fila fuera y ÚLTIMO `saved_search.changed`
    (deleted=true, revision+1) en la misma tx; el segundo DELETE → 404."""
    factory, created = db
    token, _tenant, pid = _seed_profile(factory, created)
    r = _post(factory, token, {"profile_id": str(pid), "name": "efímera"})
    sid, etag = r.json()["id"], r.headers["etag"]

    d = tia._api(
        factory, f"/v1/saved-searches/{sid}", token=token, method="DELETE",
        headers={"If-Match": etag},
    )
    assert d.status_code == 204
    assert _rows(
        factory, "SELECT 1 FROM saved_searches WHERE id = :i", i=uuid.UUID(sid)
    ) == []
    out = _rows(
        factory,
        "SELECT version, payload FROM integration_outbox "
        "WHERE subject_profile_id = :p AND type = 'saved_search.changed' "
        "ORDER BY version",
        p=pid,
    )
    assert [o.version for o in out] == [1, 2]
    assert out[-1].payload["deleted"] is True

    again = tia._api(
        factory, f"/v1/saved-searches/{sid}", token=token, method="DELETE"
    )
    assert again.status_code == 404


def test_list_keyset_pagination(db):
    """Decisión 10: keyset (created_at DESC, id DESC) con cursor opaco;
    limit+1 jamás emite cursor en el múltiplo exacto."""
    factory, created = db
    token, _tenant, pid = _seed_profile(factory, created)
    for i in range(3):
        assert _post(factory, token, {
            "profile_id": str(pid), "name": f"búsqueda {i}",
        }).status_code == 201

    r1 = tia._api(factory, f"/v1/saved-searches?profile={pid}&limit=2", token=token)
    page1 = r1.json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    r2 = tia._api(
        factory,
        f"/v1/saved-searches?profile={pid}&limit=2&cursor={page1['next_cursor']}",
        token=token,
    )
    page2 = r2.json()
    assert len(page2["items"]) == 1 and page2["next_cursor"] is None
    ids = [x["id"] for x in page1["items"] + page2["items"]]
    assert len(set(ids)) == 3
    keys = [(x["created_at"], x["id"]) for x in page1["items"] + page2["items"]]
    assert keys == sorted(keys, reverse=True)

    _c, _k, intruder = tia._issue(factory, created, "tenant-intruso-ss2", SS_SCOPES)
    assert tia._api(
        factory, f"/v1/saved-searches?profile={pid}", token=intruder
    ).status_code == 404
