"""Registro de providers del core (A-03)."""

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.providers.arbeitnow import ArbeitnowProvider
from jobhunt_core.harvest.providers.native_json import ENDPOINTS, NativeJSONProvider
from jobhunt_core.harvest.providers.native_rss import (
    ENDPOINTS as RSS_ENDPOINTS,
    NativeRSSProvider,
)
from jobhunt_core.harvest.providers.native_chmedia import (
    DOMAINS as CHMEDIA_DOMAINS,
    CHMediaProvider,
)
from jobhunt_core.harvest.providers.native_publicjobs import PublicJobsProvider
from jobhunt_core.harvest.providers.native_nav import NavProvider
from jobhunt_core.harvest.providers.native_thehub import TheHubProvider
from jobhunt_core.harvest.providers.native_jobgether import JobgetherProvider
from jobhunt_core.harvest.providers.native_financejobs import FinancejobsProvider
from jobhunt_core.harvest.providers.native_irishjobs import IrishJobsProvider

_PROVIDERS: dict[str, BaseProvider] = {
    ArbeitnowProvider.name: ArbeitnowProvider(),
    **{name: NativeJSONProvider(name) for name in ENDPOINTS},
    **{name: NativeRSSProvider(name) for name in RSS_ENDPOINTS},
    **{name: CHMediaProvider(name) for name in CHMEDIA_DOMAINS},
    PublicJobsProvider.name: PublicJobsProvider(),
    NavProvider.name: NavProvider(),
    TheHubProvider.name: TheHubProvider(),
    JobgetherProvider.name: JobgetherProvider(),
    FinancejobsProvider.name: FinancejobsProvider(),
    IrishJobsProvider.name: IrishJobsProvider(),
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
