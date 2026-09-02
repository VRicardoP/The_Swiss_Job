"""Transporte HTTP at-least-once hacia los inbox de consumidores de Fase C."""

import json
import logging
from collections.abc import Callable

import httpx

from jobhunt_core import delivery
from jobhunt_core.config import settings

logger = logging.getLogger(__name__)


class HttpDestinationTransport:
    """Enruta por consumer; un destino desconocido falla cerrado."""

    def __init__(
        self,
        destinations: dict[str, str],
        token: str,
        timeout_s: float,
        fallback: Callable[[str, dict], None] | None = None,
        client: httpx.Client | None = None,
    ):
        if not destinations:
            raise ValueError("destinos HTTP vacíos")
        if not token:
            raise ValueError("token HTTP vacío")
        self.destinations = dict(destinations)
        self.token = token
        self.fallback = fallback
        self.client = client or httpx.Client(timeout=timeout_s)

    def __call__(self, destination: str, event: dict) -> None:
        url = self.destinations.get(destination)
        if url is None:
            if destination == "swissjob-shadow" and self.fallback is not None:
                self.fallback(destination, event)
                return
            raise RuntimeError(
                f"delivery: destino {destination!r} sin inbox HTTP configurado"
            )
        body = json.dumps(
            {"consumer_id": destination, "event": event},
            default=str,
            separators=(",", ":"),
        )
        response = self.client.post(
            url,
            content=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-Event-ID": str(event["event_id"]),
            },
        )
        response.raise_for_status()


def register_if_configured(
    fallback: Callable[[str, dict], None] | None = None,
) -> bool:
    """Registra HTTP solo con configuración completa; respeta inyecciones."""
    if delivery.get_transport() is not None:
        return False
    if not settings.CORE_DELIVERY_HTTP_DESTINATIONS:
        return False
    transport = HttpDestinationTransport(
        settings.CORE_DELIVERY_HTTP_DESTINATIONS,
        settings.CORE_DELIVERY_HTTP_TOKEN,
        settings.CORE_DELIVERY_HTTP_TIMEOUT_S,
        fallback=fallback,
    )
    delivery.set_transport(transport)
    logger.info(
        "delivery: transporte HTTP registrado para %s",
        sorted(settings.CORE_DELIVERY_HTTP_DESTINATIONS),
    )
    return True
