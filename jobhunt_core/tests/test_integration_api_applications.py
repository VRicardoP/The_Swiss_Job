"""API /v1 de candidaturas y bookmarks (C-4, DISEÑO v2.1) contra Postgres real.

DoD (suite core): cascada de vínculo de la Decisión 3 (vacancy_id directo,
cadena merged_into al ganador, resolución por URL en fuente NO-portfolio-import
sin sintetizar duplicado — R2-1, síntesis, camino manual sin url — R2-4),
semántica bookmark de la Decisión 4 (default saved + saved_at misma tx,
promoción idempotente con redirección — R2-2, DELETE dual), GET compuesto con
cursor estable cruzando ambas ramas y NOT EXISTS (R2-3), eventos VIVOS de
application_status_events + outbox por POST/PATCH/promoción/sync (R2-7),
idempotencia (replay 201 byte a byte), ETag/If-Match bajo FOR UPDATE (412),
ownership por JOIN (404 indistinguible, 403 sin scope) y erase GDPR con filas
escritas POR /v1 (H12). Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import import_portfolio as ip
from jobhunt_core import matching, profiles
from jobhunt_core.config import settings
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests import test_integration_api as tia

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

APP_SCOPES = ["applications:read", "applications:write"]


async def _purge_portfolio_import(s, urls):
    """Borra del grafo portfolio-import SOLO las urls sintetizadas por el
    test (targeted: jamás el grafo entero de la fuente compartida)."""
    keys = [k for k in (ip.normalized_key(u) for u in urls) if k]
    if not keys:
        return
    slots = (
        await s.execute(
            sa.text(
                "SELECT sl.id FROM source_listings sl "
                "JOIN sources src ON src.id = sl.source_id AND src.name = :n "
                "WHERE sl.url_normalized = ANY(:k)"
            ),
            {"n": ip.PORTFOLIO_IMPORT_SOURCE, "k": keys},
        )
    ).scalars().all()
    if not slots:
        return
    vac_ids = (
        await s.execute(
            sa.text(
                "SELECT DISTINCT vacancy_id FROM source_listing_incarnations "
                "WHERE source_listing_id = ANY(:sl)"
            ),
            {"sl": slots},
        )
    ).scalars().all()
    # Orden FK-safe (mismo que dbcleanup.purge_source_graph): primero lo que
    # cuelga de las vacantes (ORS referencia source_listing_revisions).
    if vac_ids:
        await s.execute(
            sa.text(
                "UPDATE vacancies SET current_offer_revision_id = NULL "
                "WHERE id = ANY(:v)"
            ),
            {"v": vac_ids},
        )
        await s.execute(
            sa.text(
                "DELETE FROM dedup_candidates "
                "WHERE vacancy_a = ANY(:v) OR vacancy_b = ANY(:v)"
            ),
            {"v": vac_ids},
        )
        await s.execute(
            sa.text("DELETE FROM offer_revision_sources WHERE vacancy_id = ANY(:v)"),
            {"v": vac_ids},
        )
        await s.execute(
            sa.text("DELETE FROM offer_revisions WHERE vacancy_id = ANY(:v)"),
            {"v": vac_ids},
        )
    await s.execute(
        sa.text("DELETE FROM link_evidence WHERE source_listing_id = ANY(:sl)"),
        {"sl": slots},
    )
    await s.execute(
        sa.text(
            "DELETE FROM source_listing_revisions WHERE incarnation_id IN "
            "(SELECT id FROM source_listing_incarnations "
            " WHERE source_listing_id = ANY(:sl))"
        ),
        {"sl": slots},
    )
    await s.execute(
        sa.text(
            "DELETE FROM source_listing_incarnations "
            "WHERE source_listing_id = ANY(:sl)"
        ),
        {"sl": slots},
    )
    if vac_ids:
        await s.execute(
            sa.text("DELETE FROM vacancies WHERE id = ANY(:v)"), {"v": vac_ids}
        )
    await s.execute(
        sa.text("DELETE FROM source_listings WHERE id = ANY(:sl)"), {"sl": slots}
    )


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {
        "sources": [], "scopes": [], "models": [], "consumers": [],
        "policies": [], "extra_vacs": [], "shadow_urls": [],
    }
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await _purge_portfolio_import(s, created["shadow_urls"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            await dbcleanup.purge_policies(s, created["policies"])
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _exec(factory, sql, **params):
    async def go():
        async with factory() as s:
            await s.execute(sa.text(sql), params)
            await s.commit()

    asyncio.run(go())


def _seed(factory, created, n=2, scopes=APP_SCOPES):
    """Fuente cosechada ('arbeitnow') con n vacantes + consumer/perfil/token.
    Devuelve (token_auth, tenant, pid, [(vacancy_id, url), ...])."""
    tag = "c4" + uuid.uuid4().hex[:8]
    tenant = f"tenant-{tag}"

    async def go():
        async with factory() as s:
            source_id, scope_id = uuid.uuid4(), uuid.uuid4()
            created["sources"].append(source_id)
            created["scopes"].append(scope_id)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, 'arbeitnow', 0)"),
                {"id": source_id},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:id, :src, '{}'::jsonb, 0)"
                ),
                {"id": scope_id, "src": source_id},
            )
            await s.commit()
            await RawListingSink().handle(
                s, str(scope_id),
                tuple(
                    RawListing(
                        external_id=f"{tag}-j{i}",
                        url=f"https://x.example.ch/{tag}/j{i}",
                        payload={
                            "title": f"{tag} title {i}", "company_name": "ACME AG",
                            "description": "puesto", "tags": [],
                        },
                    )
                    for i in range(n)
                ),
            )
            await s.commit()
            cid = await profiles.ensure_consumer(s, tenant)
            created["consumers"].append(cid)
            pid = await profiles.upsert_profile(s, cid, "user-1")
            await s.commit()
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT sl.external_id, i.vacancy_id, i.url "
                        "FROM source_listings sl "
                        "JOIN source_listing_incarnations i "
                        "  ON i.source_listing_id = sl.id "
                        "WHERE sl.source_id = :src ORDER BY sl.external_id"
                    ),
                    {"src": source_id},
                )
            ).all()
            return pid, [(r.vacancy_id, r.url) for r in rows]

    pid, vacs = asyncio.run(go())
    _cid, _kid, token = tia._issue(factory, created, tenant, scopes)
    return token, tenant, pid, vacs


def _set_saved(factory, pid, vid, notes=None):
    async def go():
        async with factory() as s:
            await matching.set_saved(s, pid, vid, True)
            if notes is not None:
                await s.execute(
                    sa.text(
                        "UPDATE profile_vacancy_state SET notes = :n "
                        "WHERE profile_id = :p AND vacancy_id = :v"
                    ),
                    {"n": notes, "p": pid, "v": vid},
                )
            await s.commit()

    asyncio.run(go())


def _post_app(factory, token, body, key=None):
    headers = {"Idempotency-Key": key} if key else None
    return tia._api(
        factory, "/v1/applications", token=token, headers=headers,
        method="POST", json_body=body,
    )


# --------------------------------------------------- cascada Decisión 3 (R2-1)


def test_post_direct_vacancy_event_and_outbox(db):
    """(3a) vacancy_id directo: 201, snapshot-primero, evento inicial VIVO en
    application_status_events + outbox application.status_changed (revision=1)
    con destino = consumer del perfil (R2-7)."""
    factory, created = db
    token, tenant, pid, vacs = _seed(factory, created)
    vid, _url = vacs[0]
    r = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(vid),
        "title": "Lo que vio el usuario", "company": "ACME AG",
        "url": None, "status": "applied", "notes": "cv enviado",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "application"
    assert body["status"] == "applied"
    assert body["vacancy_id"] == str(vid)
    # Precedencia snapshot-primero: la clave presente prima aunque valga null.
    assert body["title"] == "Lo que vio el usuario"
    assert body["url"] is None
    assert r.headers.get("etag")

    events = _rows(
        factory,
        "SELECT status FROM application_status_events WHERE application_id = :a",
        a=uuid.UUID(body["id"]),
    )
    assert [e.status for e in events] == ["applied"]
    out = _rows(
        factory,
        "SELECT o.type, o.version, d.destination FROM integration_outbox o "
        "JOIN integration_outbox_deliveries d ON d.event_id = o.event_id "
        "WHERE o.subject_profile_id = :p AND o.type = 'application.status_changed'",
        p=pid,
    )
    assert len(out) == 1
    assert out[0].version == 1 and out[0].destination == tenant


def test_post_default_saved_upserts_saved_at(db):
    """(Decisión 4) status ausente → saved (paridad con el puerto real) +
    upsert de profile_vacancy_state.saved_at en la MISMA tx."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    vid, _ = vacs[0]
    r = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(vid), "title": "T",
    })
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "saved"
    pvs = _rows(
        factory,
        "SELECT saved_at FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v",
        p=pid, v=vid,
    )
    assert len(pvs) == 1 and pvs[0].saved_at is not None


def test_post_direct_archived_or_missing_404(db):
    """(3a) inexistente/archivada → 404 INDISTINGUIBLE (mismo sobre que
    cross-tenant)."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    vid, _ = vacs[0]
    _exec(factory, "UPDATE vacancies SET archived_at = now() WHERE id = :v", v=vid)
    r1 = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(vid), "title": "T",
    })
    r2 = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(uuid.uuid4()), "title": "T",
    })
    assert r1.status_code == r2.status_code == 404
    assert r1.json()["code"] == r2.json()["code"] == "not_found"


def test_post_direct_follows_merge_chain(db):
    """(3a) vacante fundida: se SIGUE merged_into hasta el ganador y se
    enlaza a él (bucle acotado)."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=3)
    (loser, _), (mid, _), (winner, _) = vacs
    _exec(factory, "UPDATE vacancies SET merged_into = :w WHERE id = :l", w=mid, l=loser)
    _exec(factory, "UPDATE vacancies SET merged_into = :w WHERE id = :l", w=winner, l=mid)
    r = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(loser), "title": "T",
    })
    assert r.status_code == 201, r.text
    assert r.json()["vacancy_id"] == str(winner)


def test_post_by_url_resolves_harvested_no_shadow_duplicate(db):
    """(3b — R2-1) URL de una vacante COSECHADA (fuente arbeitnow): se
    resuelve SIN scope de fuente y JAMÁS se sintetiza un duplicado-sombra en
    portfolio-import."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    vid, url = vacs[0]
    r = _post_app(factory, token, {
        "profile_id": str(pid), "url": url, "title": "T", "status": "applied",
    })
    assert r.status_code == 201, r.text
    assert r.json()["vacancy_id"] == str(vid)
    ghosts = _rows(
        factory,
        "SELECT sl.id FROM source_listings sl "
        "JOIN sources s ON s.id = sl.source_id AND s.name = :n "
        "WHERE sl.url_normalized = :k",
        n=ip.PORTFOLIO_IMPORT_SOURCE, k=ip.normalized_key(url),
    )
    assert ghosts == []  # cero síntesis: la identidad por URL ya existía


def test_post_by_url_synthesizes_shadow(db):
    """(3c) URL desconocida → síntesis en portfolio-import por el camino del
    import (external_id = sha256(url normalizada)), misma tx del POST."""
    factory, created = db
    token, _tenant, pid, _vacs = _seed(factory, created)
    url = f"https://desconocida.example.ch/{uuid.uuid4().hex[:8]}/oferta"
    created["shadow_urls"].append(url)
    r = _post_app(factory, token, {
        "profile_id": str(pid), "url": url, "title": "Oferta externa",
        "company": "Externa SA", "status": "applied",
    })
    assert r.status_code == 201, r.text
    vid = uuid.UUID(r.json()["vacancy_id"])
    src = _rows(
        factory,
        "SELECT s.name FROM source_listing_incarnations i "
        "JOIN source_listings sl ON sl.id = i.source_listing_id "
        "JOIN sources s ON s.id = sl.source_id WHERE i.vacancy_id = :v",
        v=vid,
    )
    assert [x.name for x in src] == [ip.PORTFOLIO_IMPORT_SOURCE]


def test_post_by_url_unsynthesizable_400(db):
    """(3c) URL en la frontera del sink (NUL: cuarentena de _preprocess): ni
    resuelve ni sintetiza → 400 con el sobre del contrato."""
    factory, created = db
    token, _tenant, pid, _vacs = _seed(factory, created)
    r = _post_app(factory, token, {
        "profile_id": str(pid), "url": "https://x.example.ch/rota\u0000nul",
        "title": "T",
    })
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid_url"


def test_post_manual_without_url(db):
    """(3d — R2-4) candidatura manual SIN url: síntesis con external_id
    alternativo determinista; el DTO devuelve url null (la clave presente en
    snapshot prima — jamás la URL sintética); reintento del mismo POST →
    mismo slot ⇒ 409 application_exists."""
    factory, created = db
    token, _tenant, pid, _vacs = _seed(factory, created)
    from jobhunt_core.applications import MANUAL_URL_PREFIX, manual_external_id

    created["shadow_urls"].append(
        MANUAL_URL_PREFIX + manual_external_id(pid, "Recruiter Role", "Head GmbH")
    )
    body = {
        "profile_id": str(pid), "title": "Recruiter Role",
        "company": "Head GmbH", "status": "applied",
    }
    r1 = _post_app(factory, token, body)
    assert r1.status_code == 201, r1.text
    assert r1.json()["url"] is None
    r2 = _post_app(factory, token, body)
    assert r2.status_code == 409
    assert r2.json()["code"] == "application_exists"


# ------------------------------------------------------ idempotencia (Decisión 1)


def test_post_idempotent_replay_201_byte_a_byte(db):
    """Replay con la MISMA key: status ORIGINAL (201, no 200) y cuerpo byte a
    byte; misma key con cuerpo distinto → 409 idempotency_conflict."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    vid, _ = vacs[0]
    key = "c4-key-" + uuid.uuid4().hex[:8]
    body = {"profile_id": str(pid), "vacancy_id": str(vid),
            "title": "T", "status": "applied"}
    r1 = _post_app(factory, token, body, key=key)
    r2 = _post_app(factory, token, body, key=key)
    assert (r1.status_code, r2.status_code) == (201, 201)
    assert r1.content == r2.content
    assert r1.headers["etag"] == r2.headers["etag"]
    # Un solo evento inicial: el replay NO re-ejecuta.
    events = _rows(
        factory,
        "SELECT e.id FROM application_status_events e "
        "JOIN applications a ON a.id = e.application_id WHERE a.profile_id = :p",
        p=pid,
    )
    assert len(events) == 1
    r3 = _post_app(factory, token, {**body, "notes": "otro cuerpo"}, key=key)
    assert r3.status_code == 409
    assert r3.json()["code"] == "idempotency_conflict"


# ------------------------------------- GET compuesto + cursor (Decisión 10, R2-3)


def test_get_composite_feed_cursor_and_not_exists(db):
    """Composición applications + bookmarks PUROS con UN reloj y UNA identidad,
    orden (ts DESC, id DESC), cursor estable CRUZANDO ambas ramas, y NOT
    EXISTS: la vacante con application Y saved_at sale UNA vez (application)."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=5)
    # 2 applications reales + 1 con app Y saved (dedupe) + 2 bookmarks puros.
    for i in (0, 1, 2):
        r = _post_app(factory, token, {
            "profile_id": str(pid), "vacancy_id": str(vacs[i][0]),
            "title": f"app-{i}", "status": "applied",
        })
        assert r.status_code == 201
    _set_saved(factory, pid, vacs[2][0])  # además saved → NO item doble
    _set_saved(factory, pid, vacs[3][0], notes="nota-b1")
    _set_saved(factory, pid, vacs[4][0])

    collected, cursor, pages = [], None, 0
    while True:
        url = f"/v1/applications?profile={pid}&limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        r = tia._api(factory, url, token=token)
        assert r.status_code == 200, r.text
        page = r.json()
        collected += page["items"]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
        assert pages < 10
    ids = [it["id"] for it in collected]
    assert len(ids) == len(set(ids)) == 5  # sin item doble (NOT EXISTS)
    kinds = {it["kind"] for it in collected}
    assert kinds == {"application", "bookmark"}
    # Orden inmutable (ts DESC, id DESC) sobre la unión.
    keys = [(it["created_at"], it["id"]) for it in collected]
    assert keys == sorted(keys, reverse=True)
    # El bookmark puro es direccionable: id = vacancy_id (R2-2).
    bm = [it for it in collected if it["kind"] == "bookmark"]
    assert all(it["id"] == it["vacancy_id"] for it in bm)
    assert {it["vacancy_id"] for it in bm} == {str(vacs[3][0]), str(vacs[4][0])}


# ------------------------------------------- PATCH/DELETE dual (Decisiones 2 y 4)


def test_patch_status_change_event_revision_and_412(db):
    """PATCH normal: If-Match errónea → 412 SIN mutar; con ETag vigente →
    200, revision+1, evento del cambio de status en la misma tx."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    vid, _ = vacs[0]
    r = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(vid),
        "title": "T", "status": "applied",
    })
    aid = r.json()["id"]
    etag = r.headers["etag"]

    stale = tia._api(
        factory, f"/v1/applications/{aid}", token=token, method="PATCH",
        headers={"If-Match": '"deadbeef"'}, json_body={"status": "interview"},
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "precondition_failed"

    ok = tia._api(
        factory, f"/v1/applications/{aid}", token=token, method="PATCH",
        headers={"If-Match": etag}, json_body={"status": "interview"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "interview"
    row = _rows(factory, "SELECT revision FROM applications WHERE id = :a",
                a=uuid.UUID(aid))
    assert row[0].revision == 2
    events = _rows(
        factory,
        "SELECT status FROM application_status_events WHERE application_id = :a "
        "ORDER BY created_at",
        a=uuid.UUID(aid),
    )
    assert [e.status for e in events] == ["applied", "interview"]
    out = _rows(
        factory,
        "SELECT version FROM integration_outbox WHERE subject_profile_id = :p "
        "AND type = 'application.status_changed' ORDER BY version",
        p=pid,
    )
    assert [o.version for o in out] == [1, 2]  # revision monotónica


def test_patch_promotes_pure_bookmark_idempotent(db):
    """(R2-2) PATCH sobre bookmark puro ({id}=vacancy_id): promoción a
    application REAL en la misma tx (id NUEVO, notes de pvs, evento inicial);
    el reintento REDIRIGE a la application (ni duplica ni 409ea)."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    vid, _ = vacs[0]
    _set_saved(factory, pid, vid, notes="nota del feed")

    r1 = tia._api(
        factory, f"/v1/applications/{vid}", token=token, method="PATCH",
        json_body={"status": "applied"},
    )
    assert r1.status_code == 200, r1.text
    promoted = r1.json()
    assert promoted["kind"] == "application"
    assert promoted["id"] != str(vid)
    assert promoted["status"] == "applied"
    assert promoted["notes"] == "nota del feed"  # notes desde pvs
    events = _rows(
        factory,
        "SELECT status FROM application_status_events WHERE application_id = :a",
        a=uuid.UUID(promoted["id"]),
    )
    assert [e.status for e in events] == ["applied"]

    # Reintento con el MISMO identificador-bookmark: redirige a la application.
    r2 = tia._api(
        factory, f"/v1/applications/{vid}", token=token, method="PATCH",
        json_body={"notes": "seguimiento"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == promoted["id"]
    n_apps = _rows(
        factory,
        "SELECT count(*) AS n FROM applications WHERE profile_id = :p", p=pid,
    )
    assert n_apps[0].n == 1  # sin duplicado


def test_delete_dual_application_and_pure_bookmark(db):
    """DELETE dual: application → fila fuera (eventos por CASCADE); bookmark
    puro → saved_at=NULL CONSERVANDO notes. Ambos 204; después 404."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    (v_app, _), (v_bm, _) = vacs
    aid = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(v_app),
        "title": "T", "status": "applied",
    }).json()["id"]
    _set_saved(factory, pid, v_bm, notes="conservada")

    r = tia._api(factory, f"/v1/applications/{aid}", token=token, method="DELETE")
    assert r.status_code == 204
    assert _rows(factory, "SELECT id FROM applications WHERE id = :a",
                 a=uuid.UUID(aid)) == []
    assert _rows(
        factory,
        "SELECT id FROM application_status_events WHERE application_id = :a",
        a=uuid.UUID(aid),
    ) == []

    r = tia._api(factory, f"/v1/applications/{v_bm}", token=token, method="DELETE")
    assert r.status_code == 204
    pvs = _rows(
        factory,
        "SELECT saved_at, notes FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v",
        p=pid, v=v_bm,
    )
    assert pvs[0].saved_at is None and pvs[0].notes == "conservada"

    gone = tia._api(factory, f"/v1/applications/{v_bm}", token=token, method="DELETE")
    assert gone.status_code == 404


# --------------------------------------------- PUT bookmarks ADITIVO (Decisión 4)


def test_put_bookmarks_additive_dedupe_and_events(db):
    """Sync ADITIVO (paridad sync_bookmarks): crea las nuevas (status=saved,
    evento inicial VIVO — R2-7) + upsert saved_at, NO borra ausentes; dedupe
    por vacante resuelta; devuelve SOLO las creadas."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=3)
    (v0, u0), (v1, u1), (_v2, _u2) = vacs
    r0 = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(v0), "title": "previa",
    })
    assert r0.status_code == 201  # preexistente: el sync no debe borrarla

    r = tia._api(
        factory, f"/v1/profiles/{pid}/bookmarks", token=token, method="PUT",
        json_body={"bookmarks": [
            {"vacancy_id": str(v1), "title": "B1"},
            {"url": u1, "title": "B1-duplicada"},  # misma vacante → dedupe
            {"vacancy_id": str(v0), "title": "ya-tenia-app"},
        ]},
    )
    assert r.status_code == 200, r.text
    created_items = r.json()["created"]
    assert [c["vacancy_id"] for c in created_items] == [str(v1)]
    assert created_items[0]["status"] == "saved"
    events = _rows(
        factory,
        "SELECT e.status FROM application_status_events e "
        "JOIN applications a ON a.id = e.application_id "
        "WHERE a.profile_id = :p AND a.vacancy_id = :v",
        p=pid, v=v1,
    )
    assert [e.status for e in events] == ["saved"]
    # ADITIVO: la application preexistente sigue; su saved_at quedó upsertado.
    pvs = _rows(
        factory,
        "SELECT vacancy_id, saved_at FROM profile_vacancy_state "
        "WHERE profile_id = :p ORDER BY vacancy_id::text",
        p=pid,
    )
    assert all(x.saved_at is not None for x in pvs)
    assert _rows(
        factory, "SELECT count(*) AS n FROM applications WHERE profile_id = :p",
        p=pid,
    )[0].n == 2

    # Re-PUT del mismo lote: 0 creadas (idempotencia a nivel de datos).
    again = tia._api(
        factory, f"/v1/profiles/{pid}/bookmarks", token=token, method="PUT",
        json_body={"bookmarks": [{"vacancy_id": str(v1), "title": "B1"}]},
    )
    assert again.status_code == 200 and again.json()["created"] == []


# ------------------------------------------------- ownership y scopes (Decisión 7)


def test_cross_tenant_404_and_missing_scope_403(db):
    """Matriz ruta→scope→ownership: cross-tenant → 404 INDISTINGUIBLE en el
    GET compuesto y en el direccionamiento dual; sin scope → 403 con
    required_scope exacto (strings de H13)."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    vid, _ = vacs[0]
    aid = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(vid), "title": "T",
    }).json()["id"]

    _c, _k, intruder = tia._issue(factory, created, "tenant-intruso-c4", APP_SCOPES)
    r = tia._api(factory, f"/v1/applications?profile={pid}", token=intruder)
    assert r.status_code == 404
    r = tia._api(factory, f"/v1/applications/{aid}", token=intruder,
                 method="PATCH", json_body={"status": "applied"})
    assert r.status_code == 404
    r = tia._api(factory, f"/v1/applications/{vid}", token=intruder, method="DELETE")
    assert r.status_code == 404

    _c, _k, reader = tia._issue(
        factory, created, "tenant-solo-lectura-c4", ["applications:read"]
    )
    r = _post_app(factory, reader, {"profile_id": str(pid), "title": "T"})
    assert r.status_code == 403
    assert r.json()["details"]["required_scope"] == "applications:write"
    r = tia._api(factory, f"/v1/applications?profile={pid}", token=reader)
    assert r.status_code == 404  # scope ok, perfil ajeno → 404 indistinguible


# ------------------------------------------------------------- GDPR/erase (H12)


def test_erase_covers_v1_written_rows(db):
    """(H12) el erase GDPR arrastra las filas escritas POR /v1: applications
    (+eventos por CASCADE), saved_searches, pvs y outbox del perfil."""
    factory, created = db
    token, tenant, pid, vacs = _seed(
        factory, created,
        scopes=APP_SCOPES + ["saved_searches:read", "saved_searches:write"],
    )
    vid, _ = vacs[0]
    assert _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(vid),
        "title": "T", "notes": "PII", "status": "applied",
    }).status_code == 201
    assert tia._api(
        factory, "/v1/saved-searches", token=token, method="POST",
        headers={"Idempotency-Key": "erase-" + uuid.uuid4().hex[:8]},
        json_body={"profile_id": str(pid), "name": "búsqueda PII"},
    ).status_code == 201

    from jobhunt_core.shadow.projector import erase_shadow_profile

    async def erase():
        async with factory() as s:
            erased = await erase_shadow_profile(s, "user-1", consumer_name=tenant)
            await s.commit()
            return erased

    assert asyncio.run(erase()) == pid
    for table in ("applications", "saved_searches", "profile_vacancy_state"):
        assert _rows(
            factory, f"SELECT 1 FROM {table} WHERE profile_id = :p", p=pid
        ) == []
    assert _rows(
        factory,
        "SELECT 1 FROM integration_outbox WHERE subject_profile_id = :p", p=pid,
    ) == []
