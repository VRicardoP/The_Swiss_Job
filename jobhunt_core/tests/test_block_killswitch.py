"""El portero que el motor legacy tenía y Core no: tres bloqueos y se para."""

import httpx
import pytest

from jobhunt_core.harvest.provider import (
    BLOCK_STATUSES,
    ProviderResponseError,
    blocking_status,
)


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://portal.test/jobs")
    return httpx.HTTPStatusError(
        "blocked", request=request, response=httpx.Response(status, request=request)
    )


@pytest.mark.parametrize("status", sorted(BLOCK_STATUSES))
def test_un_bloqueo_directo_se_reconoce(status):
    assert blocking_status(_status_error(status)) == status


@pytest.mark.parametrize("status", [404, 410, 500, 502, 504])
def test_lo_que_no_es_bloqueo_no_lo_parece(status):
    assert blocking_status(_status_error(status)) is None


def test_el_bloqueo_se_encuentra_aunque_el_adaptador_lo_envuelva():
    """MUERDE sin recorrer la cadena: los adaptadores envuelven el error del
    transporte, y `raise … from None` sólo lo oculta del traceback."""
    try:
        try:
            raise _status_error(429)
        except httpx.HTTPStatusError:
            raise ProviderResponseError("source: request failed") from None
    except ProviderResponseError as exc:
        assert blocking_status(exc) == 429


def test_una_cadena_envuelta_dos_veces_tambien():
    try:
        try:
            try:
                raise _status_error(403)
            except httpx.HTTPStatusError as inner:
                raise ProviderResponseError("inner") from inner
        except ProviderResponseError as middle:
            raise RuntimeError("outer") from middle
    except RuntimeError as exc:
        assert blocking_status(exc) == 403


def test_sin_excepcion_o_sin_respuesta_no_hay_bloqueo():
    assert blocking_status(None) is None
    assert blocking_status(RuntimeError("boom")) is None
    assert blocking_status(httpx.ConnectError("down")) is None


def test_una_cadena_circular_no_cuelga():
    first = RuntimeError("a")
    second = RuntimeError("b")
    first.__cause__ = second
    second.__cause__ = first

    assert blocking_status(first) is None
