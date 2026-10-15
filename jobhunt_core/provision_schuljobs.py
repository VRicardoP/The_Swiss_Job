"""Provision schuljobs' verified sweep without enabling it.

The scope DECLARES its page budget: the initial listing plus three scroll
pages, which is the 83 offers measured live on 2026-10-15 and about twice what
the producer's dynamic budget fetches. The adapter's ceiling is 26 pages, so
widening the sweep is a scope decision — a params change with its own
validation — rather than a code change.
"""

from jobhunt_core.provision_scopes import provision_exact, run_cli

SOURCE = "schuljobs"
SCOPE_PARAMS = ({"max_pages": 4},)


async def provision(session, enabled: bool | None = None):
    return await provision_exact(session, SOURCE, SCOPE_PARAMS, enabled)


def main() -> None:
    run_cli(SOURCE, SCOPE_PARAMS)


if __name__ == "__main__":
    main()
