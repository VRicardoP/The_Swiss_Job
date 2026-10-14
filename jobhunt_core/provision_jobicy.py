"""Provision the verified Jobicy scopes without enabling them by accident."""

from jobhunt_core.provision_scopes import provision_exact, run_cli

SOURCE = "jobicy"
SCOPE_PARAMS = (
    {},
    {"tag": "python"},
    {"tag": "javascript"},
    {"tag": "react"},
    {"tag": "customer-success"},
    {"tag": "copywriting"},
    {"tag": "technical-writing"},
    {"tag": "translation"},
)


async def provision(session, enabled: bool | None = None):
    return await provision_exact(session, SOURCE, SCOPE_PARAMS, enabled)


def main() -> None:
    run_cli(SOURCE, SCOPE_PARAMS)


if __name__ == "__main__":
    main()
