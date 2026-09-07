"""Migración de durables SwissJob (Fase D) — idempotencia, preservación de
estado (ADR-03) y rollback por valores exactos del manifiesto."""

import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import import_swissjob_durables as isd
from jobhunt_core.tests.test_integration_matching import (  # noqa: F401
    SHA_A, _listing, _rows, _setup, db,
)

TITULOS = ["english teacher primary", "content editor remote"]


def _urls(factory, created):
    async def go():
        async with factory() as s:
            return {
                r.titulo: r.enlace
                for r in (
                    await s.execute(sa.text(
                        "SELECT o.content->>'title' AS titulo, i.url AS enlace "
                        "FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "JOIN vacancies v ON v.id = i.vacancy_id "
                        "JOIN offer_revisions o "
                        "  ON o.id = v.current_offer_revision_id "
                        "WHERE l.source_id = :src"
                    ), {"src": created["sources"][0]})
                ).all()
            }

    return asyncio.run(go())


def _plan(pid, urls, extra_search=None):
    searches = [{
        "name": "Remote roles (EN/ES)", "filters": {"remote_only": True},
        "min_score": 1, "is_active": True,
        "notify_frequency": "weekly", "notify_push": False,
    }]
    if extra_search:
        searches.append(extra_search)
    return {
        "profiles": {"u1": str(pid)},
        "feedback": {"u1": [
            {"url": urls[TITULOS[0]], "feedback": "thumbs_down",
             "created_at": "2026-08-01T10:00:00+00:00"},
            {"url": urls[TITULOS[1]], "feedback": "thumbs_up",
             "created_at": None},
            {"url": "https://x/inexistente", "feedback": "thumbs_up",
             "created_at": None},
        ]},
        "saved_searches": {"u1": searches},
        "exclusions": {"u1": [{"kind": "title_contains", "pattern": "Director"},
                              {"kind": "tag_contains", "pattern": "VP"}]},
    }


def test_migracion_idempotente_y_semantica_de_feedback(db):
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, TITULOS)
    urls = _urls(factory, created)

    async def go(plan):
        async with factory() as s:
            man = await isd.run_import(s, plan)
            await s.commit()
            return man

    plan = _plan(pid, urls)
    man1 = asyncio.run(go(plan))
    c = man1["counts"]["u1"]
    assert c["feedback"] == {"migrated": 2, "kept_existing": 0,
                             "unresolved": 1, "invalid_feedback": 0}
    assert c["saved_searches"]["migrated"] == 1
    assert c["notify"]["fixed"] == 1  # weekly/False != defaults daily/true
    assert c["exclusions"]["insertadas"] == 2
    assert len(man1["saved_search_ids"]) == 1

    filas = _rows(
        factory,
        "SELECT s.feedback, s.dismissed_at, o.content->>'title' AS t "
        "FROM profile_vacancy_state s "
        "JOIN vacancies v ON v.id = s.vacancy_id "
        "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
        "WHERE s.profile_id = :p ORDER BY 3", p=pid)
    assert [(f.feedback, f.dismissed_at is not None) for f in filas] == [
        ("thumbs_up", False),      # content editor: up, sin dismissed
        ("thumbs_down", True),     # teacher: down ⇒ dismissed_at
    ]
    ss = _rows(
        factory,
        "SELECT notify_frequency::text AS f, notify_push "
        "FROM saved_searches WHERE profile_id = :p", p=pid)[0]
    assert (ss.f, ss.notify_push) == ("weekly", False)
    # P1-4: configuración AUTORITATIVA del perfil, no un JSONB inerte
    ex = _rows(factory, "SELECT kind, pattern FROM profile_exclusions "
               "WHERE profile_id = :p ORDER BY kind", p=pid)
    assert [(x.kind, x.pattern) for x in ex] == [
        ("tag_contains", "VP"), ("title_contains", "Director")]

    # IDEMPOTENCIA: mismos conteos de clasificación, cero duplicados
    man2 = asyncio.run(go(plan))
    assert man2["counts"]["u1"]["saved_searches"]["existing"] == 1
    assert man2["counts"]["u1"]["exclusions"]["ya_presentes"] == 2
    assert man2["saved_search_ids"] == []
    assert man2["counts"]["u1"]["feedback"]["kept_existing"] == 2
    n = _rows(factory, "SELECT count(*) AS n FROM saved_searches "
              "WHERE profile_id = :p", p=pid)[0].n
    assert n == 1


def test_estado_existente_jamas_se_pisa(db):
    """ADR-03: un feedback ya presente en el core NO se sobreescribe."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, TITULOS)
    urls = _urls(factory, created)

    async def preexistente():
        async with factory() as s:
            await s.execute(sa.text(
                "INSERT INTO profile_vacancy_state "
                "(profile_id, vacancy_id, feedback, notes) "
                "VALUES (:p, :v, 'thumbs_up', 'nota previa')"),
                {"p": pid, "v": vacs[TITULOS[0]]})
            await s.commit()

    asyncio.run(preexistente())

    async def go():
        async with factory() as s:
            man = await isd.run_import(s, _plan(pid, urls))
            await s.commit()
            return man

    man = asyncio.run(go())
    assert man["counts"]["u1"]["feedback"]["kept_existing"] == 1
    fila = _rows(
        factory,
        "SELECT feedback, dismissed_at, notes FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v",
        p=pid, v=vacs[TITULOS[0]])[0]
    # el thumbs_down del legacy NO pisó el thumbs_up existente ni las notas
    assert (fila.feedback, fila.dismissed_at, fila.notes) == (
        "thumbs_up", None, "nota previa")


def test_rollback_restaura_el_estado_exacto(db):
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, TITULOS)
    urls = _urls(factory, created)

    async def foto():
        async with factory() as s:
            pvs = (await s.execute(sa.text(
                "SELECT profile_id, vacancy_id, feedback, dismissed_at, "
                "saved_at, notes FROM profile_vacancy_state "
                "WHERE profile_id = :p ORDER BY vacancy_id"),
                {"p": pid})).all()
            ss = (await s.execute(sa.text(
                "SELECT name, filters, notify_frequency::text, notify_push "
                "FROM saved_searches WHERE profile_id = :p ORDER BY name"),
                {"p": pid})).all()
            ex = (await s.execute(sa.text(
                "SELECT kind, pattern FROM profile_exclusions "
                "WHERE profile_id = :p ORDER BY kind, pattern"),
                {"p": pid})).all()
            return ([tuple(r) for r in pvs], [tuple(r) for r in ss],
                    [tuple(r) for r in ex])

    antes = asyncio.run(foto())

    async def migrar_y_rollback():
        async with factory() as s:
            man = await isd.run_import(s, _plan(pid, urls))
            await s.commit()
        async with factory() as s:
            counts = await isd.rollback_import(s, man)
            await s.commit()
            return counts

    counts = asyncio.run(migrar_y_rollback())
    assert counts["pvs_deleted"] == 2
    assert counts["searches_deleted"] == 1
    assert asyncio.run(foto()) == antes  # byte-equivalente al estado previo


def test_rollback_exacto_con_dos_entradas_que_convergen(db):
    """Revisión externa 2026-09-07 (P1-5): dos entradas legacy que resuelven a
    la MISMA vacante generaban dos imágenes previas en el manifiesto; la
    segunda capturaba el valor que la PRIMERA acababa de escribir, y el
    rollback en orden directo lo restauraba. Debe consolidarse por clave
    natural: una sola imagen previa, restaurada una vez."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, TITULOS)
    urls = _urls(factory, created)
    objetivo = vacs[TITULOS[0]]

    async def fila_previa():
        async with factory() as s:
            await s.execute(sa.text(
                "INSERT INTO profile_vacancy_state "
                "(profile_id, vacancy_id, notes) VALUES (:p, :v, 'previa')"),
                {"p": pid, "v": objetivo})
            await s.commit()

    asyncio.run(fila_previa())

    plan = {
        "profiles": {"u1": str(pid)},
        # DOS entradas para la MISMA url ⇒ misma vacante
        "feedback": {"u1": [
            {"url": urls[TITULOS[0]], "feedback": "thumbs_up",
             "created_at": None},
            {"url": urls[TITULOS[0]], "feedback": "thumbs_down",
             "created_at": "2026-08-01T10:00:00+00:00"},
        ]},
        "saved_searches": {"u1": []}, "exclusions": {"u1": []},
    }

    async def migrar_y_rollback():
        async with factory() as s:
            man = await isd.run_import(s, plan)
            await s.commit()
        async with factory() as s:
            await isd.rollback_import(s, man)
            await s.commit()
        async with factory() as s:
            return (await s.execute(sa.text(
                "SELECT feedback, dismissed_at, notes FROM "
                "profile_vacancy_state WHERE profile_id = :p AND "
                "vacancy_id = :v"), {"p": pid, "v": objetivo})).one_or_none()

    fila = asyncio.run(migrar_y_rollback())
    assert fila is not None, "la fila preexistente NO debía borrarse"
    assert (fila.feedback, fila.dismissed_at, fila.notes) == (
        None, None, "previa"), f"rollback dejó {fila}"


def test_url_con_varias_vacantes_aplica_el_feedback_a_TODAS(db):
    """Ensayo del 2026-09-07: la deriva de identidad hace que la MISMA oferta
    re-listada entre como clon, así que una url legacy resuelve a varias
    vacantes vivas. Enlazar a una arbitraria (lo que hacía el código
    original) pierde la intención del usuario en las demás; rechazar la
    pierde entera. Son la MISMA oferta: el feedback se aplica a todas."""
    factory, created = db
    pid, mid, polid, vacs = _setup(factory, created, TITULOS)
    urls = _urls(factory, created)
    url = urls[TITULOS[0]]

    async def clonar():
        """Otra FUENTE lista la MISMA url sobre otra vacante (clon real: la
        url es única POR FUENTE, así que el clon llega de otro portal o de
        una re-inserción con fuente distinta)."""
        async with factory() as s:
            otra = vacs[TITULOS[1]]
            src2 = uuid.uuid4()
            created["sources"].append(src2)
            await s.execute(sa.text(
                "INSERT INTO sources (id, name, tier) "
                "VALUES (:i, 'jobicy', 0)"), {"i": src2})
            listing = uuid.uuid4()
            await s.execute(sa.text(
                "INSERT INTO source_listings (id, source_id, external_id, "
                "url_normalized) VALUES (:i, :src, :ext, :u)"),
                {"i": listing, "src": src2, "ext": "clon-1", "u": url})
            await s.execute(sa.text(
                "INSERT INTO source_listing_incarnations "
                "(id, source_listing_id, vacancy_id, seq, url) "
                "VALUES (:i, :sl, :v, 1, :u)"),
                {"i": uuid.uuid4(), "sl": listing, "v": otra, "u": url})
            await s.commit()
            return otra

    otra = asyncio.run(clonar())

    plan = {
        "profiles": {"u1": str(pid)},
        "feedback": {"u1": [{"url": url, "feedback": "thumbs_down",
                             "created_at": None}]},
        "saved_searches": {"u1": []}, "exclusions": {"u1": []},
    }

    async def go():
        async with factory() as s:
            man = await isd.run_import(s, plan)
            await s.commit()
            return man

    man = asyncio.run(go())
    assert man["counts"]["u1"]["feedback"]["migrated"] == 2, (
        "el feedback debe alcanzar a TODAS las vacantes de esa url")
    filas = _rows(
        factory,
        "SELECT vacancy_id FROM profile_vacancy_state "
        "WHERE profile_id = :p AND feedback = 'thumbs_down'", p=pid)
    assert {str(f.vacancy_id) for f in filas} == {
        str(vacs[TITULOS[0]]), str(otra)}
