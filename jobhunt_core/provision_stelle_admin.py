"""Provision jobs.admin.ch's verified public API sweep without enabling it."""

from jobhunt_core.provision_scopes import provision_exact, run_cli

SOURCE = "stelle_admin"
SCOPE_PARAMS = ({},)


async def provision(session, enabled: bool | None = None):
    return await provision_exact(session, SOURCE, SCOPE_PARAMS, enabled)


def main() -> None:
    run_cli(SOURCE, SCOPE_PARAMS)


if __name__ == "__main__":
    main()
