"""Registro de providers del core (A-03)."""

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.providers.arbeitnow import ArbeitnowProvider

_PROVIDERS: dict[str, BaseProvider] = {
    ArbeitnowProvider.name: ArbeitnowProvider(),
}


class UnknownProviderError(LookupError):
    """Provider no registrado — error de configuración PERMANENTE (rev. A-04
    2ª #3): la tarea falla explícito SIN retry. Excepción PROPIA para no
    clasificar cualquier KeyError interno como error de configuración."""


def get_provider(name: str) -> BaseProvider:
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise UnknownProviderError(
            f"Provider desconocido: {name!r} (registrados: {sorted(_PROVIDERS)})"
        )
    return provider
