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


# --- Huecos encontrados por la auditoría del 2026-09-23 --------------------
# Cada prueba provoca UN cambio que la página SIRVE y exige que la versión se
# mueva. Las cuatro primeras fallaban contra el digest original (pertenencia +
# evaluación + revisión canónica), que no cubría estado de usuario ni primary.

def _feedback(factory, pid, vid, feedback):
    """El escritor REAL de feedback del core, no un UPDATE a mano: lo que se
    prueba es que el camino de producción mueve la versión."""
    from jobhunt_core.feedback import set_vacancy_feedback

    async def go():
        async with factory() as s:
            await set_vacancy_feedback(s, pid, vid, feedback)
            await s.commit()

    asyncio.run(go())


def _save(factory, pid, vid, saved=True):
    from jobhunt_core import matching

    async def go():
        async with factory() as s:
            await matching.set_saved(s, pid, vid, saved)
            await s.commit()

    asyncio.run(go())


def _reassign_primary(factory, vid):
    """Otra encarnación del MISMO vacancy pasa a ser el primary: cambia el
    `primary_listing` servido (url/apply_url/external_id) sin tocar la canónica."""

    async def go():
        async with factory() as s:
            actual = (await s.execute(
                sa.text("SELECT primary_incarnation_id FROM vacancies WHERE id = :vid"),
                {"vid": vid})).scalar_one()
            fila = (await s.execute(
                sa.text("SELECT source_listing_id, seq FROM source_listing_incarnations "
                        "WHERE id = :iid"),
                {"iid": actual})).one()
            # Como el sink: la encarnación anterior se cierra y la nueva lleva seq+1
            # (índice único parcial por listing sobre ended_at IS NULL).
            await s.execute(sa.text(
                "UPDATE source_listing_incarnations SET ended_at = now() WHERE id = :iid"),
                {"iid": actual})
            nueva = uuid.uuid4()
            await s.execute(sa.text(
                "INSERT INTO source_listing_incarnations "
                "(id, source_listing_id, vacancy_id, seq, url, apply_url) "
                "VALUES (:id, :sl, :vid, :seq, :url, NULL)"),
                {"id": nueva, "sl": fila.source_listing_id, "vid": vid,
                 "seq": fila.seq + 1, "url": f"https://example.com/otra/{nueva}"})
            await s.execute(sa.text(
                "UPDATE vacancies SET primary_incarnation_id = :iid WHERE id = :vid"),
                {"iid": nueva, "vid": vid})
            await s.commit()

    asyncio.run(go())


def _drop_canonical(factory, vid):
    """Lo que hace el sink con un primary no normalizable: la vacante sigue
    viva pero sin revisión canónica vigente, y la página no puede servirla."""

    async def go():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE vacancies SET current_offer_revision_id = NULL WHERE id = :vid"),
                {"vid": vid})
            await s.commit()

    asyncio.run(go())


def test_la_version_cambia_con_feedback_positivo(db):
    """`thumbs_up` no cambia la pertenencia: el digest original no lo veía y
    un consumidor cacheado servía `feedback: null` tras el ACK."""
    factory, created = db
    pid, _vacs, token = api._seed_matches(factory, created)
    antes = _version(factory, pid, token)

    _feedback(factory, pid, _feed_ids(factory, pid, token)[0], "thumbs_up")

    despues = _version(factory, pid, token)
    assert despues["total"] == antes["total"], "el feedback positivo no saca la oferta"
    assert despues["version"] != antes["version"]


def test_la_version_cambia_al_guardar(db):
    factory, created = db
    pid, _vacs, token = api._seed_matches(factory, created)
    antes = _version(factory, pid, token)

    _save(factory, pid, _feed_ids(factory, pid, token)[0])

    assert _version(factory, pid, token)["version"] != antes["version"]


def test_la_version_cambia_al_reasignar_el_primary(db):
    """El `primary_listing` (url, apply_url, external_id) sale de la
    encarnación primaria, no de la canónica. Reasignarla cambia lo servido —
    y la clave con la que el BFF une el estado local— sin mover la revisión."""
    factory, created = db
    pid, _vacs, token = api._seed_matches(factory, created)
    antes = _version(factory, pid, token)

    _reassign_primary(factory, _feed_ids(factory, pid, token)[0])

    despues = _version(factory, pid, token)
    assert despues["total"] == antes["total"]
    assert despues["version"] != antes["version"]


def test_una_vacante_sin_canonica_no_cuenta_ni_versiona(db):
    """La página omite lo que no puede servir; el total y la versión deben
    hacer lo mismo. Si el total sobre-cuenta, el consumidor ve
    `len(items) != total` y apaga su caché en silencio."""
    factory, created = db
    pid, _vacs, token = api._seed_matches(factory, created)
    antes = _version(factory, pid, token)

    _drop_canonical(factory, _feed_ids(factory, pid, token)[0])

    despues = _version(factory, pid, token)
    servidos = _feed_ids(factory, pid, token)
    assert despues["total"] == len(servidos) == antes["total"] - 1
    assert despues["version"] != antes["version"]


def test_la_ruta_de_version_exige_el_scope(db):
    """401 y 404 ya estaban cubiertos; el 403 por scope insuficiente, no."""
    factory, created = db
    pid, _vacs, _token = api._seed_matches(factory, created)
    _cid, _kid, sin_scope = api._issue(factory, created, "tenant-match", ["vacancies:read"])
    assert api._api(factory, f"/v1/profiles/{pid}/matches/version",
                    token=sin_scope).status_code == 403
