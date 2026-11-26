"""D10 — `title_en` se rellena una vez por título en el núcleo y viaja en el DTO.

Controles negativos que importan:
- un título EN/ES NUNCA llega al LLM;
- un fallo del proveedor (429, 5xx, timeout) no marca NINGUNA fila (F3);
- una página de N vacantes cuesta las MISMAS consultas que una de 1.
"""

import asyncio
import json
import uuid

import httpx
import pytest
import sqlalchemy as sa

from jobhunt_core import title_translation as tt
from jobhunt_core.config import settings
from jobhunt_core.tests import test_integration_api as api

db = api.db
pytestmark = api.pytestmark


def _clear(factory):
    async def go():
        async with factory() as s:
            await s.execute(sa.text("DELETE FROM title_translations"))
            await s.commit()

    asyncio.run(go())


@pytest.fixture()
def tdb(db, monkeypatch):
    factory, created = db
    monkeypatch.setattr(settings, "CORE_GROQ_API_KEY", "test-key")
    # Detector determinista: el real se prueba aparte (test_title_translation).
    monkeypatch.setattr(
        tt, "detect_language", lambda t: "en" if "Engineer" in t else "de"
    )
    tt._pause["until"] = 0.0
    tt._discovered_at_generation["gen"] = None
    _clear(factory)
    yield factory, created
    tt._pause["until"] = 0.0
    tt._discovered_at_generation["gen"] = None
    _clear(factory)


class FakeGroq:
    """LLM falso: traduce con prefijo, o responde lo que se le programe."""

    def __init__(self):
        self.sent: list[str] = []
        self.calls = 0
        self.mode = "ok"
        self.omit: set[str] = set()

    def handler(self, request):
        self.calls += 1
        titles = json.loads(json.loads(request.content)["messages"][1]["content"])
        self.sent.extend(titles.values())
        if self.mode == "429":
            return httpx.Response(429)
        if self.mode == "500":
            return httpx.Response(500)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("lento", request=request)
        out = {i: f"EN {t}" for i, t in titles.items() if t not in self.omit}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(out)}}]}
        )

    def transport(self):
        return httpx.MockTransport(self.handler)


def _pass(factory, groq):
    return asyncio.run(tt.run_pass(factory, transport_factory=groq.transport))


def _rows(factory, keys):
    async def go():
        async with factory() as s:
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT title_key, language, title_en, attempts, priority "
                        "FROM title_translations WHERE title_key = ANY(:k)"
                    ),
                    {"k": list(keys)},
                )
            ).all()
            return {r.title_key: r for r in rows}

    return asyncio.run(go())


def _drain(factory, groq, keys, passes=30):
    """Pasadas hasta que las claves del test estén resueltas (la BD de la
    suite puede traer vacantes de otros tests que compiten por el lote)."""
    for _ in range(passes):
        _pass(factory, groq)
        rows = _rows(factory, keys)
        if len(rows) == len(keys) and all(
            r.language is not None and (r.title_en or r.language == "en")
            for r in rows.values()
        ):
            return rows
    raise AssertionError(f"sin resolver: {_rows(factory, keys)}")


def _seed(factory, created):
    tok = uuid.uuid4().hex[:8]
    titles = (
        f"Softwareentwickler {tok}",
        f"Lehrperson {tok}",
        f"Software Engineer {tok}",
    )
    _pid, vacs, token = api._seed_matches(factory, created, titles=titles)
    return tok, titles, vacs, token


def test_rellena_title_en_sin_mandar_ingles_al_llm(tdb):
    factory, created = tdb
    tok, titles, _vacs, token = _seed(factory, created)
    de1, de2, en = titles
    groq = FakeGroq()

    rows = _drain(factory, groq, titles)

    assert rows[de1].title_en == f"EN {de1}"
    assert rows[de2].title_en == f"EN {de2}"
    # Control negativo: el título en inglés NUNCA viajó al LLM.
    assert en not in groq.sent
    assert rows[en].language == "en" and rows[en].title_en is None
    # Están en un feed (evaluados): prioridad de feed.
    assert {r.priority for r in rows.values()} == {1}

    items = api._api(factory, f"/v1/vacancies?q={tok}", token=token).json()["items"]
    served = {i["title"]: i["title_en"] for i in items}
    assert served == {de1: f"EN {de1}", de2: f"EN {de2}", en: None}

    # Segunda pasada: nada nuevo que pedir para estos títulos.
    groq.sent.clear()
    _pass(factory, groq)
    assert not set(groq.sent) & set(titles)


@pytest.mark.parametrize("mode", ["429", "500", "timeout"])
def test_un_fallo_del_proveedor_no_envenena(tdb, mode):
    factory, created = tdb
    _tok, titles, _vacs, _token = _seed(factory, created)
    de1, de2, _en = titles
    groq = FakeGroq()
    groq.mode = mode

    _pass(factory, groq)
    _pass(factory, groq)
    rows = _rows(factory, titles)
    assert rows[de1].language == "de", "la detección local sí se hizo"
    # F3: ninguna fila marcada como traducida ni con intentos gastados.
    assert all(r.title_en is None and r.attempts == 0 for r in rows.values())
    if mode == "429":
        # La pausa: la segunda pasada no llamó al LLM.
        assert groq.calls == 1
    else:
        # Una llamada por pasada: el fallo para la pasada, no la repite.
        assert groq.calls == 2

    # El proveedor vuelve: lo pendiente se traduce.
    tt._pause["until"] = 0.0
    groq.mode = "ok"
    rows = _drain(factory, groq, titles)
    assert rows[de1].title_en == f"EN {de1}" and rows[de2].title_en == f"EN {de2}"


def test_titulo_omitido_suma_intentos_y_se_abandona(tdb):
    factory, created = tdb
    _tok, titles, _vacs, _token = _seed(factory, created)
    de1, de2, _en = titles
    groq = FakeGroq()
    groq.omit = {de2}

    for _ in range(tt.MAX_ATTEMPTS + 3):
        _pass(factory, groq)
    rows = _rows(factory, titles)
    assert rows[de1].title_en == f"EN {de1}"
    assert rows[de2].title_en is None and rows[de2].attempts == tt.MAX_ATTEMPTS
    # Abandonado: deja de pedirse.
    assert groq.sent.count(de2) == tt.MAX_ATTEMPTS


def _put(factory, mapping):
    async def go():
        async with factory() as s:
            for k, en in mapping.items():
                await s.execute(
                    sa.text(
                        "INSERT INTO title_translations (title_key, language, title_en) "
                        "VALUES (:k, 'de', :en)"
                    ),
                    {"k": k, "en": en},
                )
            await s.commit()

    asyncio.run(go())


def _count_statements(factory, url, token):
    engine = factory.kw["bind"]
    seen = []

    def on_exec(_conn, _cursor, statement, *_args):
        seen.append(statement)

    sa.event.listen(engine.sync_engine, "before_cursor_execute", on_exec)
    try:
        r = api._api(factory, url, token=token)
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", on_exec)
    assert r.status_code == 200
    return r.json(), len(seen)


def test_title_en_viaja_sin_consultas_por_vacante(tdb):
    factory, created = tdb
    term, vacancies, token = api._seed_catalog(factory, created, n=5)
    keys = sorted(vacancies)
    _put(factory, {k: f"EN {k}" for k in keys})

    one, n_one = _count_statements(
        factory, f"/v1/vacancies?q={term}&limit=1&offset=0", token
    )
    five, n_five = _count_statements(
        factory, f"/v1/vacancies?q={term}&limit=5&offset=0", token
    )
    assert len(one["items"]) == 1 and len(five["items"]) == 5
    assert all(i["title_en"] == f"EN {i['title']}" for i in five["items"])
    # Control negativo: si title_en se leyera por vacante, cinco costarían más.
    assert n_five == n_one


def test_title_en_en_el_feed_de_matches(tdb):
    factory, created = tdb
    pid, vacs, token = api._seed_matches(factory, created)
    _put(factory, {"backend python": "Backend Python (EN)"})
    items = api._api(factory, f"/v1/profiles/{pid}/matches", token=token).json()[
        "items"
    ]
    served = {i["vacancy"]["title"]: i["vacancy"]["title_en"] for i in items}
    assert served == {"backend python": "Backend Python (EN)", "data eng": None}


def test_traduccion_identica_no_se_sirve(tdb):
    factory, created = tdb
    term, vacancies, token = api._seed_catalog(factory, created, n=1)
    (title,) = vacancies
    _put(factory, {title: title})
    items = api._api(factory, f"/v1/vacancies?q={term}", token=token).json()["items"]
    assert items[0]["title_en"] is None
