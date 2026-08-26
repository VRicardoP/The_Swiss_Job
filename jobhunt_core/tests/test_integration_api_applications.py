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

NUL = chr(0)  # el NUL literal no puede vivir en el fuente
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


def test_g5_la_cadena_merged_into_resuelve_aunque_el_GANADOR_este_archivado(db):
    """Regresión G5-P3-4: `resolve_direct` conoce el perfil y comprueba el
    adjunto para la vacante CONSULTADA, pero delegaba en `_merge_winner` SIN
    propagarlo. El perfil tiene la candidatura sobre el GANADOR `W` y el BFF
    conserva el id del PERDEDOR `V` (`V.merged_into = W`) — el caso que la
    Decisión 3a existe para cubrir. `V`, por estar fundida, nunca se archiva
    (la rama 1 del barrido exige `merged_into IS NULL`), así que el adjunto ni
    se consulta para `V`: está en `W`, dentro de la cadena. Retirado el guard
    PF.3, `W` sí se archiva y la cadena devolvía None ⇒ 404 al re-POST y
    `skipped` en el PUT, con el item VISIBLE en el feed: una UI con un item
    sobre el que ninguna escritura de vínculo funciona."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=2)
    (loser, _), (winner, _) = vacs
    # El perfil YA tiene su candidatura sobre el GANADOR…
    r = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(winner), "title": "T-win",
    })
    assert r.status_code == 201, r.text
    # …el perdedor se funde en él y el barrido archiva al ganador (muerto).
    _exec(factory, "UPDATE vacancies SET merged_into = :w WHERE id = :l",
          w=winner, l=loser)
    _exec(factory, "UPDATE vacancies SET archived_at = now() WHERE id = :w",
          w=winner)

    # Con el id del PERDEDOR, la cadena sigue resolviendo al ganador adjunto:
    # el 409 NOMBRA la vacante resuelta (antes del fix era un 404 opaco, con
    # el item aún visible en el feed).
    r2 = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(loser), "title": "T-win",
    })
    assert r2.status_code == 409, r2.text  # antes del fix: 404 not_found
    assert r2.json()["code"] == "application_exists"
    assert r2.json()["details"]["vacancy_id"] == str(winner)

    # NO-REGRESIÓN del aislamiento: otro perfil SIN adjunto sigue en 404 (el
    # archivado es la regla; la excepción es «lo que el usuario ya tenía»).
    otro = asyncio.run(_perfil_extra(factory, pid))
    r3 = _post_app(factory, token, {
        "profile_id": str(otro), "vacancy_id": str(loser), "title": "T",
    })
    assert r3.status_code == 404
    assert r3.json()["code"] == "not_found"


def test_g6_la_cadena_resuelve_con_el_adjunto_en_el_PERDEDOR(db):
    """Regresión G6-P3-3: la comprobación del adjunto de G5-P3-4 viaja con la
    cadena pero solo mira el GANADOR, así que cubría media patología. La otra
    mitad —el usuario marcó `V` ANTES de la fusión, que es igual de natural y
    probablemente más frecuente— seguía rota: `V` no está archivada (las dos
    ramas del barrido exigen `merged_into IS NULL`) ⇒ `resolve_direct` no
    consulta su adjunto; la cadena llega a `W`, que sí está archivada, y
    `_profile_attached(pid, W)` es False ⇒ None. Item VISIBLE en el feed sobre
    el que ninguna escritura de vínculo funciona: 404 al re-POST y `skipped`
    en el PUT de bookmarks — el síntoma que el commit de G5 declaró cerrado.
    Ahora el adjunto se busca en la cadena ENTERA, en UNA consulta."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=2)
    (loser, _), (winner, _) = vacs
    # El perfil marcó el PERDEDOR (antes de la fusión), no el ganador.
    r = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(loser), "title": "T-lose",
    })
    assert r.status_code == 201, r.text
    _exec(factory, "UPDATE vacancies SET merged_into = :w WHERE id = :l",
          w=winner, l=loser)
    _exec(factory, "UPDATE vacancies SET archived_at = now() WHERE id = :w",
          w=winner)

    # Con el id del PERDEDOR la cadena RESUELVE al ganador y el vínculo se
    # escribe sobre él (antes del fix: 404 not_found, con el item aún visible
    # en el feed y ninguna escritura de vínculo posible).
    r2 = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(loser), "title": "T-lose",
    })
    assert r2.status_code == 201, r2.text
    assert r2.json()["vacancy_id"] == str(winner)

    # NO-REGRESIÓN del aislamiento: otro perfil, sin adjunto en NINGÚN eslabón
    # de la cadena, sigue en 404.
    otro = asyncio.run(_perfil_extra(factory, pid))
    r3 = _post_app(factory, token, {
        "profile_id": str(otro), "vacancy_id": str(loser), "title": "T",
    })
    assert r3.status_code == 404
    assert r3.json()["code"] == "not_found"


async def _perfil_extra(factory, pid):
    """Segundo perfil del MISMO consumer (sin adjunto sobre el ganador)."""
    async with factory() as s:
        cid = await s.scalar(
            sa.text("SELECT consumer_id FROM profiles WHERE id = :p"), {"p": pid}
        )
        vid = await profiles.upsert_profile(
            s, cid, f"g5-extra-{uuid.uuid4().hex[:8]}"
        )
        await s.commit()
        return vid


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


def test_delete_application_creada_por_defecto_no_resucita_como_bookmark(db):
    """Regresión G3-A-P2-2: el alta SIN `status` vale 'saved' (Decisión 4) y
    escribe DOS filas — la application y `profile_vacancy_state.saved_at`—,
    pero el DELETE retiraba solo la primera: al desaparecer la fila de
    applications, el NOT EXISTS del feed dejaba de excluir la vacante y el item
    REAPARECÍA como bookmark, con id = vacancy_id (otra identidad) y un segundo
    DELETE sobre el id original en 404. La asimetría estaba dentro del propio
    handler: la rama bookmark SÍ llamaba a set_saved(False). La suite no lo veía
    porque su DELETE se hacía sobre una application creada con status
    'applied'."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created)
    (v_app, _), _ = vacs
    # Alta POR DEFECTO (sin status): el camino del cliente normal.
    created_app = _post_app(factory, token, {
        "profile_id": str(pid), "vacancy_id": str(v_app), "title": "T",
    }).json()
    assert created_app["status"] == "saved"
    aid = created_app["id"]

    r = tia._api(factory, f"/v1/applications/{aid}", token=token, method="DELETE")
    assert r.status_code == 204

    feed = tia._api(
        factory, f"/v1/applications?profile={pid}", token=token
    ).json()["items"]
    assert feed == []  # antes: [(vacancy_id, 'bookmark', 'saved')]
    pvs = _rows(
        factory,
        "SELECT saved_at FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v",
        p=pid, v=v_app,
    )
    assert pvs == [] or pvs[0].saved_at is None
    # Y el borrado es idempotente en su propia identidad: 404, no un item
    # resucitado con otra.
    again = tia._api(factory, f"/v1/applications/{aid}", token=token, method="DELETE")
    assert again.status_code == 404


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


def test_put_bookmarks_archived_item_skipped_not_global_404(db):
    """Regresión G1-P3-2: un bookmark cuya vacante fue ARCHIVADA tras el
    snapshot del BFF (3a irresoluble) hacía 404 de TODO el PUT (tx única,
    rollback conjunto): los válidos tampoco se creaban y el retry del BFF
    repetía el 404 hasta sacar el item del lote — un sync «aditivo» sin
    progreso estructural. Ahora el item se SALTEA y se reporta en skipped[];
    el POST conserva su 404 por-item (test_post_direct_archived_or_missing_404)."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=2)
    (v0, _u0), (v1, _u1) = vacs
    _exec(factory, "UPDATE vacancies SET archived_at = now() WHERE id = :v", v=v0)

    r = tia._api(
        factory, f"/v1/profiles/{pid}/bookmarks", token=token, method="PUT",
        json_body={"bookmarks": [
            {"vacancy_id": str(v0), "title": "rancia"},
            {"vacancy_id": str(v1), "title": "valida"},
        ]},
    )
    assert r.status_code == 200, r.text  # antes: 404 global
    body = r.json()
    assert [c["vacancy_id"] for c in body["created"]] == [str(v1)]
    assert len(body["skipped"]) == 1
    sk = body["skipped"][0]
    assert sk["vacancy_id"] == str(v0) and sk["title"] == "rancia" and sk["reason"]
    # El item válido SÍ progresó (la application existe).
    assert _rows(
        factory,
        "SELECT vacancy_id FROM applications WHERE profile_id = :p",
        p=pid,
    )[0].vacancy_id == v1
    # El irresoluble no dejó rastro (ni application ni bookmark re-marcado).
    assert _rows(
        factory,
        "SELECT 1 FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v AND saved_at IS NOT NULL",
        p=pid, v=v0,
    ) == []


def test_feed_race_unsaved_bookmark_omitted_not_500(db):
    """Regresión G1-P3-1: entre las dos queries del GET compuesto (claves →
    detalle, misma tx READ COMMITTED) otra tx des-marca el bookmark
    (saved_at=NULL) y commitea. La query de detalle re-filtra ahora
    saved_at IS NOT NULL: el item DESAPARECE de la página (como el resto de
    carreras toleradas) en vez de componer created_at=None y reventar
    ApplicationDTO → 500 al cliente."""
    from jobhunt_core import applications as appsvc
    from jobhunt_core.api import schemas

    factory, created = db
    _token, _tenant, pid, vacs = _seed(factory, created, n=1)
    v0, _u0 = vacs[0]

    async def go():
        async with factory() as s2:  # bookmark PURO (sin application)
            await matching.set_saved(s2, pid, v0, True)
            await s2.commit()
        async with factory() as s1:
            keys, _more = await appsvc._feed_keys(s1, pid, 10, None)
            assert [r.kind for r in keys] == ["bookmark"]
            # Interleaving determinista: otra tx des-marca y COMMITEA entre
            # las claves y el detalle.
            async with factory() as s2:
                await matching.set_saved(s2, pid, v0, False)
                await s2.commit()
            app_rows, bm_rows = await appsvc._feed_rows(s1, pid, keys)
            assert bm_rows == {}  # el detalle re-filtra: item despresentado
            # La composición de la página (el camino del endpoint) omite el
            # item y todos los DTO restantes son construibles — sin 500.
            corpus = await appsvc.corpus_fields(s1, [v0])
            items = [
                appsvc.compose_bookmark(bm_rows[r.item_id], corpus.get(r.item_id, {}))
                for r in keys
                if r.kind == "bookmark" and r.item_id in bm_rows
            ]
            dtos = [schemas.ApplicationDTO(**i) for i in items]
            assert dtos == []

    asyncio.run(go())


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


def test_g7_cuerpo_no_almacenable_es_400_de_frontera_y_no_500(db):
    """REGRESIÓN G7-P3-1 en el otro extremo de la misma clase: el snapshot
    (`title/company/url/source/description`) va a un `CAST(:snap AS jsonb)` y
    `notes` a una columna `text`. Ninguno de los dos admite el NUL que el
    `json.loads` de la stdlib decodifica sin rechistar desde `\\u0000`, y los
    DTO no lo filtran (un NUL no viola ningún `max_length`). Salía **500** por
    entrada de usuario; ahora es el 400 de frontera del contrato, y no se
    crea la candidatura."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=1)
    vid, _url = vacs[0]

    for campo, valor in (("description", "linea1\x00linea2"), ("notes", "a\x00b")):
        r = _post_app(
            factory, token,
            {"profile_id": str(pid), "vacancy_id": str(vid), "title": "T",
             campo: valor},
            key="app-" + uuid.uuid4().hex[:10],
        )
        assert r.status_code == 400, (campo, r.text)
        assert r.json()["code"] == "invalid_json", (campo, r.text)

    assert _rows(
        factory, "SELECT 1 FROM applications WHERE profile_id = :p", p=pid
    ) == []


def test_g8_la_url_toxica_con_vacancy_id_es_400_de_frontera_y_no_500(db):
    """REGRESIÓN G8-P3-3: la excepción de la `url` en `_check_storable` era
    INCONDICIONAL, y su justificación escrita —«la url ya tiene su propia
    frontera: la cuarentena del sink en `_link` responde 400 invalid_url»—
    solo se cumple en la rama por-URL.

    `link_vacancy` valida la url dentro de `if url is not None:`, y a esa
    rama solo se llega tras `if vacancy_id is not None: return
    resolve_direct(...)`. Los DTO permiten mandar los DOS: con `vacancy_id`
    presente el vínculo NI MIRA la url, pero la url entra igual en el
    snapshot (`SNAPSHOT_KEYS`) ⇒ `CAST(:snap AS jsonb)` ⇒
    `UntranslatableCharacterError` ⇒ 500 por entrada de usuario. El test que
    motivó la excepción manda la url tóxica SIN `vacancy_id`: cubre justo el
    caso en que la cuarentena sí corre y no ve el otro.

    Se comprueban las dos rutas expuestas (`POST /v1/applications` y cada item
    de `PUT /v1/profiles/{pid}/bookmarks`; el `PATCH` no tiene `url`) y, como
    control, que el diagnóstico específico de la rama por-URL NO se degrada."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=1)
    vid, _url = vacs[0]
    url_toxica = "https://x.example.ch/rota" + NUL + "nul"

    r = _post_app(
        factory, token,
        {"profile_id": str(pid), "vacancy_id": str(vid), "title": "T",
         "url": url_toxica},
        key="app-" + uuid.uuid4().hex[:10],
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid_json", r.text

    b = tia._api(
        factory, f"/v1/profiles/{pid}/bookmarks", token=token, method="PUT",
        json_body={"bookmarks": [
            {"vacancy_id": str(vid), "title": "T", "url": url_toxica},
        ]},
    )
    assert b.status_code == 400, b.text
    assert b.json()["code"] == "invalid_json", b.text

    # Control: SIN vacancy_id la cuarentena del sink sí corre y su
    # diagnóstico, más específico, se conserva.
    c = _post_app(
        factory, token,
        {"profile_id": str(pid), "url": url_toxica, "title": "T"},
        key="app-" + uuid.uuid4().hex[:10],
    )
    assert c.status_code == 400, c.text
    assert c.json()["code"] == "invalid_url", c.text

    assert _rows(
        factory, "SELECT 1 FROM applications WHERE profile_id = :p", p=pid
    ) == []


def test_g8_el_nul_en_la_cabecera_de_idempotencia_es_400_y_no_500(db):
    """REGRESIÓN G8-N-4: `Idempotency-Key` iba CRUDA a
    `idempotency_records.key` (columna `text`) sin pasar por ninguna regla de
    almacenabilidad. Un NUL ahí revienta con
    `CharacterNotInRepertoireError` ⇒ 500. Hoy no es alcanzable POR LA RED
    —la imagen no lleva `httptools`, así que uvicorn usa `h11`, que responde
    400 antes del ASGI— pero eso es una propiedad de la IMAGEN y no del
    código: el día que entre httptools el 500 aparece sin que nadie toque una
    línea. El ASGI de este test entrega la cabecera tal cual, que es
    exactamente el escenario contra el que se protege."""
    factory, created = db
    token, _tenant, pid, vacs = _seed(factory, created, n=1)
    vid, _url = vacs[0]
    r = _post_app(
        factory, token,
        {"profile_id": str(pid), "vacancy_id": str(vid), "title": "T"},
        key="app-" + NUL + "k",
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid_idempotency_key", r.text
