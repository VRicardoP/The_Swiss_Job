"""Traducir no puede deducir el idioma dentro de la petición.

`CLAUDE.md` §5 afirma que el camino de respuesta NO detecta idioma. Era falso
para `translate=true`, que es el **default** de `GET /match/results` y lo que
usan `/match/saved` y `/match/history`: `translate_titles` llamaba a
`_resolve_language`, sin memoizar, por cada título.

Medido en el NAS sobre 100 títulos reales del feed: **104 ms por título**, y
sólo 17 de 100 los resolvía la heurística barata de caracteres — el resto caía
en langdetect. Proyección a las 1.800 ofertas del feed: **187 s** por petición.

El idioma ya lo tenemos por dos vías baratas: el que sirve el core con la
oferta y el derivado que persiste `job_title_languages`. Aquí se fija que se
usan ésas y que langdetect NO entra en el camino de respuesta.
"""
import pytest

from services.translation_service import TranslationService


class _GroqFalso:
    """Disponible y con traductor de mentira: lo que importa es el idioma."""

    is_available = True

    def __init__(self):
        self.lotes: list[list[str]] = []


@pytest.fixture
def detector_que_estalla(monkeypatch):
    """langdetect es lo caro: si alguien vuelve a llamarlo al servir, esto
    explota en vez de ponerse lento en producción."""
    def estallar(cls, *a, **kw):
        raise AssertionError("el camino de respuesta llamó a langdetect")

    monkeypatch.setattr(TranslationService, "_langdetect_lang", classmethod(estallar))


@pytest.fixture
def traductor(monkeypatch):
    t = TranslationService(_GroqFalso())

    async def sin_cache(self, title):
        return None

    async def traducir(self, to_translate, result):
        self._groq.lotes.append(list(to_translate))
        for x in to_translate:
            result[x] = f"{x} [EN]"

    monkeypatch.setattr(TranslationService, "_get_cached", sin_cache)
    monkeypatch.setattr(TranslationService, "_translate_pending", traducir)
    return t


async def test_con_idioma_conocido_no_se_detecta(traductor, detector_que_estalla):
    """El core sirve `language` en la oferta: basta con leerlo."""
    out = await traductor.translate_titles([
        {"title": "Software Engineer", "language": "en"},
        {"title": "Ingénieur logiciel", "language": "fr"},
    ])
    assert out["Software Engineer"] == "Software Engineer", "un título EN no se manda al LLM"
    assert out["Ingénieur logiciel"] == "Ingénieur logiciel [EN]"


async def test_sin_idioma_tampoco_se_detecta(traductor, detector_que_estalla):
    """Sin dato, se manda al LLM —que devuelve el título igual si ya es inglés—
    en vez de gastar 104 ms en adivinarlo."""
    out = await traductor.translate_titles([{"title": "Remote Support Agent", "language": ""}])
    assert out["Remote Support Agent"] == "Remote Support Agent [EN]"


async def test_usa_el_idioma_derivado_que_se_le_pasa(traductor, detector_que_estalla):
    """El persistido en `job_title_languages` vale igual que el del core."""
    out = await traductor.translate_titles(
        [{"title": "Customer Success Manager", "language": ""}],
        languages={"Customer Success Manager": "en"},
    )
    assert out["Customer Success Manager"] == "Customer Success Manager"
    assert traductor._groq.lotes == [], "no debía mandarse nada al LLM"


async def test_la_heuristica_de_caracteres_sigue_valiendo(traductor, detector_que_estalla):
    """Es gratis y no usa langdetect: un compuesto alemán se traduce."""
    out = await traductor.translate_titles([{"title": "Softwareentwickler (m/w/d)", "language": ""}])
    assert out["Softwareentwickler (m/w/d)"] == "Softwareentwickler (m/w/d) [EN]"


async def test_sin_groq_devuelve_los_titulos_tal_cual(detector_que_estalla):
    class _NoDisponible:
        is_available = False

    out = await TranslationService(_NoDisponible()).translate_titles(
        [{"title": "Data Engineer", "language": ""}]
    )
    assert out == {"Data Engineer": "Data Engineer"}
