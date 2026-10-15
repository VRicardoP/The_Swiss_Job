"""Provision Himalayas' verified three-page feed without enabling it by accident."""

from jobhunt_core.provision_scopes import provision_exact, run_cli

SOURCE = "himalayas"
SCOPE_PARAMS = ({},)


async def provision(session, enabled: bool | None = None):
    return await provision_exact(session, SOURCE, SCOPE_PARAMS, enabled)


def main() -> None:
    run_cli(SOURCE, SCOPE_PARAMS)


if __name__ == "__main__":
    main()
