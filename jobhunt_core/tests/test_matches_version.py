"""La versión del feed cambia exactamente cuando cambia el feed.

Existe para que el consumidor deje de descargar 1.800 ofertas en 18 páginas
sólo para saber si algo cambió: medido en el NAS, ese recorrido es el 87-89 %
del coste de servir la pantalla principal (47,5 s en frío, 11,5 s en caliente).
Un `If-None-Match` no lo evitaba, porque el ETag se deriva del payload y
construirlo ES el trabajo.

Una versión que NO se moviese ante un cambio serviría un feed rancio en
silencio — peor que el coste que evita. Por eso cada prueba provoca UN cambio
observable y exige que la versión se mueva.
"""
import asyncio
import uuid

import sqlalchemy as sa

from jobhunt_core.tests import test_integration_api as api

db = api.db
pytestmark = api.pytestmark


def _version(factory, pid, token):
    r = api._api(factory, f"/v1/profiles/{pid}/matches/version", token=token)
    assert r.status_code == 200, r.text
    return r.json()


def _feed_ids(factory, pid, token):
    r = api._api(factory, f"/v1/profiles/{pid}/matches?limit=100", token=token)
    assert r.status_code == 200, r.text
    return [i["vacancy"]["id"] for i in r.json()["items"]]


def _archive(factory, vid):
    """Saca una oferta del feed como lo hace el barrido de archivado."""

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :vid"),
                {"vid": vid},
            )
            await s.commit()

    asyncio.run(go())


def _new_revision(factory, vid, title):
    """Revisión canónica NUEVA y vigente, como haría el sink ante un título
    cambiado: fila nueva en offer_revisions + `current_offer_revision_id`
    movido. Parchear el content en sitio NO es lo que pasa en producción."""

    async def go():
        async with factory() as s:
            content = (
                await s.execute(
                    sa.text(
                        "SELECT o.content FROM offer_revisions o JOIN vacancies v "
                        "ON v.current_offer_revision_id = o.id WHERE v.id = :vid"
                    ),
                    {"vid": vid},
                )
            ).scalar_one()
            nuevo = dict(content)
            nuevo["title"] = title
            rid = uuid.uuid4()
            await s.execute(
                sa.text(
                    "INSERT INTO offer_revisions (id, vacancy_id, content_hash, "
                    "text_hash, content) VALUES (:id, :vid, :ch, :th, (:c)::jsonb)"
                ),
                {"id": rid, "vid": vid, "ch": uuid.uuid4().hex,
                 "th": uuid.uuid4().hex, "c": __import__("json").dumps(nuevo)},
            )
            await s.execute(
                sa.text("UPDATE vacancies SET current_offer_revision_id = :rid "
                        "WHERE id = :vid"),
                {"rid": rid, "vid": vid},
            )
            await s.commit()

    asyncio.run(go())


def test_la_version_concuerda_con_el_feed_y_es_estable(db):
    factory, created = db
    pid, _vacs, token = api._seed_matches(factory, created)

    primera = _version(factory, pid, token)
    assert primera["total"] == len(_feed_ids(factory, pid, token))
    assert primera == _version(factory, pid, token), "sin cambios, misma versión"


def test_la_version_cambia_al_salir_una_oferta_del_feed(db):
    factory, created = db
    pid, _vacs, token = api._seed_matches(factory, created)
    antes = _version(factory, pid, token)

    _archive(factory, _feed_ids(factory, pid, token)[0])

    despues = _version(factory, pid, token)
    assert despues["total"] == antes["total"] - 1
    assert despues["version"] != antes["version"]


def test_la_version_cambia_al_cambiar_la_canonica(db):
    """Cambio de CONTENIDO sin cambio de pertenencia: un contador no lo vería,
    y el consumidor seguiría sirviendo el título viejo desde su caché."""
    factory, created = db
    pid, _vacs, token = api._seed_matches(factory, created)
    antes = _version(factory, pid, token)

    _new_revision(factory, _feed_ids(factory, pid, token)[0], "Título nuevo")

    despues = _version(factory, pid, token)
    assert despues["total"] == antes["total"], "la pertenencia no cambió"
    assert despues["version"] != antes["version"], (
        "un cambio de contenido debe mover la versión; si no, se sirve rancio")


def test_la_version_respeta_la_tenencia(db):
    """404 indistinguible para perfil ajeno o inexistente, como el feed."""
    factory, created = db
    pid, _vacs, _token = api._seed_matches(factory, created)
    _cid, _kid, ajeno = api._issue(factory, created, "tenant-b", api.ALL_SCOPES)

    assert api._api(factory, f"/v1/profiles/{pid}/matches/version",
                    token=ajeno).status_code == 404
    assert api._api(factory, f"/v1/profiles/{uuid.uuid4()}/matches/version",
                    token=ajeno).status_code == 404


def test_la_version_exige_credencial(db):
    factory, created = db
    pid, _vacs, _token = api._seed_matches(factory, created)
    assert api._api(factory, f"/v1/profiles/{pid}/matches/version",
                    token=None).status_code == 401
