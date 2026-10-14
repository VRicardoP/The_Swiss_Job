"""Provision JSearch's verified daily scope without enabling it."""

from jobhunt_core.harvest.providers.native_jsearch import (
    DEFAULT_COUNTRY,
    DEFAULT_DATE_POSTED,
    DEFAULT_NUM_PAGES,
    DEFAULT_PAGE,
    DEFAULT_QUERY,
)
from jobhunt_core.provision_scopes import provision_exact, run_cli

SOURCE = "jsearch"
SCOPE_PARAMS = (
    {
        "query": DEFAULT_QUERY,
        "country": DEFAULT_COUNTRY,
        "date_posted": DEFAULT_DATE_POSTED,
        "page": DEFAULT_PAGE,
        "num_pages": DEFAULT_NUM_PAGES,
    },
)


async def provision(session, enabled: bool | None = None):
    return await provision_exact(session, SOURCE, SCOPE_PARAMS, enabled)


def main() -> None:
    run_cli(SOURCE, SCOPE_PARAMS)


if __name__ == "__main__":
    main()
