"""Registro de providers del core (A-03)."""

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.providers.arbeitnow import ArbeitnowProvider

_PROVIDERS: dict[str, BaseProvider] = {
    ArbeitnowProvider.name: ArbeitnowProvider(),
}


def get_provider(name: str) -> BaseProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise KeyError(f"Provider desconocido: {name!r} (registrados: {sorted(_PROVIDERS)})")
