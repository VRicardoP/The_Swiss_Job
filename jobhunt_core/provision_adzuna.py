"""Provision Adzuna's verified country scopes without enabling them."""

from jobhunt_core.harvest.providers.native_adzuna import COUNTRIES
from jobhunt_core.provision_scopes import provision_exact, run_cli

SOURCE = "adzuna"
SCOPE_PARAMS = tuple({"country": country} for country in COUNTRIES)


async def provision(session, enabled: bool | None = None):
    return await provision_exact(session, SOURCE, SCOPE_PARAMS, enabled)


def main() -> None:
    run_cli(SOURCE, SCOPE_PARAMS)


if __name__ == "__main__":
    main()
