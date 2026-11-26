"""T12 — dos formas de que el feed deje de tumbarse entero.

1. **Presupuesto del recorrido (H9).** `CORE_HTTP_TIMEOUT_SECONDS` acota CADA
   petición, no la suma. Con cuatro páginas eso son cuatro veces el timeout en
   el peor caso, y nada lo cortaba: un core lento dejaba ocupado el worker de
   gunicorn hasta que terminara, y sólo hay dos. Ahora el recorrido entero
   tiene tope y salir por él es un 503, que el consumidor ya sabe tratar.

2. **Degradación escolar (M5).** Un fallo del corpus ESCOLAR se traducía a
   `CoreUnavailableError` y el usuario se quedaba sin NINGUNA oferta por no
   poder resolver la identidad de unas pocas. Ahora esas se sirven sin
   `school_id` y el resto va intacto.
"""

import asyncio
import uuid

import pytest

from config import settings
from services.matching.core_client import CoreMatching
from services.matching.port import CoreUnavailableError


class _ClienteLento:
    """Tarda más que el presupuesto en la PRIMERA página."""

    def __init__(self, demora):
        self._demora = demora

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, *_args, **_kwargs):
        await asyncio.sleep(self._demora)
        raise AssertionError("cliente falso: no responde de verdad")


@pytest.mark.asyncio
async def test_el_recorrido_tiene_presupuesto_y_sale_por_503(db_session, monkeypatch):
    monkeypatch.setattr(settings, "CORE_FEED_TOTAL_BUDGET_S", 0.2)
    cliente = CoreMatching(db_session, client_factory=lambda: _ClienteLento(5.0))

    inicio = asyncio.get_running_loop().time()
    with pytest.raises(CoreUnavailableError) as fallo:
        await cliente._fetch_full_feed(uuid.uuid4(), None)
    transcurrido = asyncio.get_running_loop().time() - inicio

    assert "presupuesto" in str(fallo.value)
    # Corta por el presupuesto, no espera los 5 s del cliente lento.
    assert transcurrido < 2.0, transcurrido


@pytest.mark.asyncio
async def test_sin_presupuesto_agotado_no_estorba(db_session, monkeypatch):
    """Control del control: el presupuesto no puede romper el camino normal."""
    monkeypatch.setattr(settings, "CORE_FEED_TOTAL_BUDGET_S", 30.0)
    cliente = CoreMatching(db_session, client_factory=lambda: _ClienteLento(0.01))
    # El cliente falso lanza AssertionError al responder: lo que se comprueba
    # es que NO sale por el presupuesto, que es otro error distinto.
    with pytest.raises(Exception) as fallo:
        await cliente._fetch_full_feed(uuid.uuid4(), None)
    assert "presupuesto" not in str(fallo.value)


class _FabricaLenta:
    """Abre un cliente cuya petición tarda `demora` segundos."""

    def __init__(self, demora):
        self._demora = demora

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def request(self, *_args, **_kwargs):
        await asyncio.sleep(self._demora)
        raise AssertionError("cliente falso: no responde de verdad")


@pytest.fixture
def _presupuesto_corto(monkeypatch):
    """A-09: con el timeout por petición de PRODUCCIÓN (120 s), el presupuesto
    total de colegios, búsquedas guardadas y documentos era 240 s. Ahora es
    `CORE_FEED_TOTAL_BUDGET_S`, el mismo que el del feed."""
    monkeypatch.setattr(settings, "CORE_HTTP_TIMEOUT_SECONDS", 120.0)
    monkeypatch.setattr(settings, "CORE_FEED_TOTAL_BUDGET_S", 0.1)
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "k.s")


async def _corta_a_tiempo(corrutina, error):
    inicio = asyncio.get_running_loop().time()
    with pytest.raises(error):
        await corrutina
    transcurrido = asyncio.get_running_loop().time() - inicio
    assert transcurrido < 1.0, transcurrido


@pytest.mark.asyncio
async def test_colegios_usan_el_presupuesto_total(_presupuesto_corto, monkeypatch):
    from services.schools.http_client import SchoolClient
    from services.schools.port import CoreUnavailableError as SchoolError

    cliente = SchoolClient(client_factory=_FabricaLenta(2.0))
    await _corta_a_tiempo(cliente.request("GET", "/x"), SchoolError)

    async def pagina_lenta(*_a, **_k):
        await asyncio.sleep(2.0)

    monkeypatch.setattr(cliente, "request", pagina_lenta)
    await _corta_a_tiempo(cliente.pages("/x"), SchoolError)


@pytest.mark.asyncio
async def test_documentos_usan_el_presupuesto_total(_presupuesto_corto):
    from services.documents.core_client import CoreDocuments
    from services.documents.port import CoreUnavailableError as DocumentsError

    documentos = CoreDocuments(client_factory=_FabricaLenta(2.0))

    async def usar():
        async with documentos._client() as client:
            await client.request("GET", "/x")

    await _corta_a_tiempo(usar(), DocumentsError)


@pytest.mark.asyncio
async def test_busquedas_guardadas_usan_el_presupuesto_total(
    _presupuesto_corto, monkeypatch
):
    from services import saved_searches

    async def perfil(*_a):
        return uuid.uuid4()

    monkeypatch.setattr(saved_searches, "resolve_core_profile_id", perfil)
    busquedas = saved_searches.CoreSavedSearches(
        None, uuid.uuid4(), client_factory=_FabricaLenta(2.0)
    )

    async def usar():
        async with busquedas._client() as (client, _pid):
            await client.request("GET", "/x")

    await _corta_a_tiempo(usar(), saved_searches.SearchCoreError)


def test_el_recorte_no_muta_lo_que_recorta():
    """La caché declara que sus items NO se mutan jamás: se copia."""
    from services.matching.core_client import _DESCRIPCION_MAX, _recortado

    original = {"vacancy": {"description": "x" * 2000, "tags": ["t"] * 30}}
    recortado = _recortado([original])[0]

    assert len(recortado["vacancy"]["description"]) == _DESCRIPCION_MAX
    assert len(original["vacancy"]["description"]) == 2000, "mutó el original"
    # Los tags NO se recortan: `school_for_job` empareja el colegio buscando
    # su id ENTRE ellos, y cortar a los 15 primeros haría desaparecer colegios.
    assert len(recortado["vacancy"]["tags"]) == 30


def test_el_recorte_tolera_payloads_raros():
    from services.matching.core_client import _recortado

    raros = [{}, {"vacancy": None}, {"vacancy": {}}, {"vacancy": {"description": None}}]
    assert _recortado(raros) == raros
