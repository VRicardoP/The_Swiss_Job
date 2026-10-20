"""Regresiones de la auditoría G1 — alerta de profesor (P2-15).

- P2-15: alerta profesor sin cota superior (doble email) y con retry que
  re-enviaba tras SMTP OK (marca al final).

P2-10 (promoción de duplicados al archivar) y P2-14 (barrido de URLs por
lotes) se retiraron con sus tareas en DT-148: trabajaban sobre la tabla legacy
`jobs`, congelada desde el corte de la cosecha nativa.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class _OfertaServida:
    """Oferta del catálogo del core con los campos que lee el aviso."""

    def __init__(self, hash_, first_seen_at, *, title="Primary Teacher (Zurich)"):
        self.hash = hash_
        self.title = title
        self.first_seen_at = first_seen_at
        self.tags = []
        self.company = "Schule"
        self.canton = "ZH"
        self.location = None
        self.url = f"https://e.ch/{hash_}"


class _CatalogoFalso:
    """Doble del catálogo con SU contrato: la categoría pedida y el `since`.

    DT-148 A1: el aviso leía `jobs`, que dejó de tener escritores. El filtro
    por fecha vive ahora en el /v1, así que el doble lo reproduce.
    """

    def __init__(self, ofertas=()):
        self.ofertas = list(ofertas)
        self.calls: list[tuple] = []

    async def recent(self, *, category, since, limit=500):
        self.calls.append((category, since, limit))
        return [o for o in self.ofertas if o.first_seen_at >= since][:limit]


class _FakeRedis:
    """Doble de Redis: marca de agua + marcador «ya avisado» (`SET NX EX`)."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if value is not None else None

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value.decode() if isinstance(value, bytes) else value
        return True

    def delete(self, key):
        self.store.pop(key, None)

    def close(self):
        pass


@pytest.mark.asyncio
class TestP215AlertaProfesor:
    async def _run(self, catalogo, monkeypatch, fake_redis, email_mock):
        from config import settings
        from tasks.alert_tasks import _detect_and_notify

        monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True, raising=False)
        monkeypatch.setattr(
            settings, "TEACHER_ALERT_EMAIL", "user@example.com", raising=False
        )
        monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake_redis)
        with (
            patch("services.email_service.EmailService", return_value=email_mock),
            patch(
                "services.catalog.core_client.CoreCatalog",
                lambda *a, **k: catalogo,
            ),
        ):
            return await _detect_and_notify()

    def _oferta_de_primaria(self, marker, first_seen=None):
        """Oferta de docencia servida por el catálogo (antes: fila en `jobs`)."""
        return _OfertaServida(
            f"g1p215-{marker}",
            first_seen if first_seen is not None else datetime.now(timezone.utc),
        )

    async def test_envio_fallido_se_reintenta_y_el_exitoso_no_se_repite(
        self, monkeypatch
    ):
        """G3/P1-1 SUSTITUYE la decisión de G1/P2-15 en este punto.

        G1 guardaba la marca ANTES de enviar y aceptaba perder ESE aviso si el
        envío fallaba, a cambio de no duplicar jamás. Con el marcador «ya
        avisado» POR OFERTA la disyuntiva desaparece: un envío fallido retira
        su marcador y el reintento lo recupera; un envío correcto deja el
        marcador puesto y la ventana solapada no lo repite. El duplicado solo
        es posible si el proceso muere entre el envío y el marcador — mucho
        más barato que perder la alerta (el criterio del ciclo G3).
        """
        marker = uuid.uuid4().hex[:8]
        catalogo = _CatalogoFalso([self._oferta_de_primaria(marker)])

        fake_redis = _FakeRedis()
        email_mock = MagicMock()
        email_mock.is_available = True
        sends: list = []
        fail = True

        def _send(*args, **kwargs):
            sends.append(1)
            if fail:
                raise ConnectionError("SMTP caído")

        email_mock.send = _send

        # 1ª corrida: el envío falla y sube al retry de Celery.
        with pytest.raises(ConnectionError):
            await self._run(catalogo, monkeypatch, fake_redis, email_mock)
        assert len(sends) == 1

        # 2ª corrida (retry): la oferta NO se perdió — se reintenta y sale.
        fail = False
        result = await self._run(catalogo, monkeypatch, fake_redis, email_mock)
        assert result["matched"] == 1, "el aviso fallido debe reintentarse"
        assert len(sends) == 2

        # 3ª corrida: ya enviado — el solape de la ventana no lo repite.
        third = await self._run(catalogo, monkeypatch, fake_redis, email_mock)
        assert third["matched"] == 0
        assert len(sends) == 2, "el marcador impide el reenvío"

    async def test_cota_superior_excluye_altas_futuras(self, monkeypatch):
        """G1/P2-15: una oferta fechada por delante de `now` no entra en esta
        corrida (entrará en la siguiente) — sin doble email."""
        marker = uuid.uuid4().hex[:8]
        catalogo = _CatalogoFalso(
            [
                self._oferta_de_primaria(
                    marker,
                    first_seen=datetime.now(timezone.utc) + timedelta(hours=1),
                )
            ]
        )

        fake_redis = _FakeRedis()
        email_mock = MagicMock()
        email_mock.is_available = True
        email_mock.send = MagicMock()

        result = await self._run(catalogo, monkeypatch, fake_redis, email_mock)
        assert result["matched"] == 0
        email_mock.send.assert_not_called()
