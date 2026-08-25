"""Regresiones de la auditoría G1 — CircuitBreaker inerte y probe clavado.

- P2-1: fetch_with_retry/fetch_rss devuelven None y JAMÁS lanzan; el breaker
  solo contaba fallos en el except → nunca abría, success_count contaba cada
  fallo como éxito y los `except CircuitBreakerOpen` de ~8 providers eran
  inalcanzables. Ahora un resultado None cuenta como fallo (contrato V.0:
  None a través del breaker = fetch fallido definitivo).
- P3-2: un CancelledError (BaseException) en el probe HALF_OPEN dejaba
  `_half_open_pending=True` para siempre → circuito clavado.
"""

import asyncio

import pytest

from services.circuit_breaker import CircuitBreaker, CircuitBreakerOpen, CircuitState


class TestP21BreakerAprendeDelNone:
    @pytest.mark.asyncio
    async def test_none_cuenta_como_fallo_y_abre(self):
        """G1/P2-1: N fallos-None consecutivos deben abrir el circuito."""
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60)

        async def _failed_fetch():
            return None  # contrato de fetch_with_retry al agotar reintentos

        for _ in range(3):
            result = await cb.call(_failed_fetch)
            assert result is None, "el None se devuelve: el contrato del llamante no cambia"

        status = cb.get_status()
        assert status["state"] == "open"
        assert status["failure_count"] == 3
        assert status["success_count"] == 0, "un fallo no es un éxito"

        with pytest.raises(CircuitBreakerOpen):
            await cb.call(_failed_fetch)

    @pytest.mark.asyncio
    async def test_diez_nones_no_dejan_el_circuito_virgen(self):
        """La forma exacta de la sonda G1: 10 runs fallidos NO deben dejar
        closed/success=10/failure=0."""
        cb = CircuitBreaker("test", failure_threshold=5, recovery_timeout=60)

        async def _none():
            return None

        for _ in range(10):
            try:
                await cb.call(_none)
            except CircuitBreakerOpen:
                pass

        status = cb.get_status()
        assert status["state"] == "open"
        assert status["success_count"] == 0

    @pytest.mark.asyncio
    async def test_exito_real_sigue_cerrando(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0)

        async def _none():
            return None

        async def _ok():
            return {"docs": []}

        await cb.call(_none)
        await cb.call(_none)
        # recovery_timeout=0 → el OPEN transiciona a HALF_OPEN al leerse.
        assert cb.get_status()["state"] == "half_open"
        # Un éxito real cierra.
        assert await cb.call(_ok) == {"docs": []}
        assert cb.get_status()["state"] == "closed"

    @pytest.mark.asyncio
    async def test_provider_real_abre_el_circuito(self, monkeypatch):
        """Vía provider (jsearch): fetch_with_retry→None debe ejercitar el
        breaker de verdad, no dejarlo decorativo."""
        from providers.jsearch import JSearchProvider

        monkeypatch.setattr(
            "config.settings.JSEARCH_RAPIDAPI_KEY", "test-key", raising=False
        )

        async def _always_none(*args, **kwargs):
            return None

        monkeypatch.setattr("providers.jsearch.fetch_with_retry", _always_none)

        provider = JSearchProvider()
        threshold = provider._circuit.failure_threshold
        opened = False
        for _ in range(threshold + 1):
            try:
                jobs = await provider.fetch_jobs("")
                assert jobs == []
            except CircuitBreakerOpen:
                opened = True
                break
        assert opened or provider._circuit.get_status()["state"] == "open"
        assert provider._circuit.get_status()["success_count"] == 0


class TestP32ProbeCancelado:
    @pytest.mark.asyncio
    async def test_cancelled_en_half_open_no_clava_el_circuito(self):
        """G1/P3-2: un CancelledError en el probe debe liberar el slot."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0)

        async def _boom():
            raise RuntimeError("net down")

        with pytest.raises(RuntimeError):
            await cb.call(_boom)
        assert cb.state == CircuitState.HALF_OPEN  # recovery 0 → probe inmediato

        async def _cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await cb.call(_cancelled)

        # Sin el fix, _half_open_pending quedaba True y esto rebotaba con
        # CircuitBreakerOpen(retry_after=0) para siempre.
        async def _ok():
            return "ok"

        assert await cb.call(_ok) == "ok"
        assert cb.get_status()["state"] == "closed"
