"""Autoridad operativa ÚNICA sobre la canonicidad de políticas (P1-D).

Uso (dentro del contenedor del core):

    python -m jobhunt_core.policy_ctl status
    python -m jobhunt_core.policy_ctl declare cosine-baseline:v1 hybrid-rrf:v4

`declare` recibe el conjunto EXACTO deseado (name:version), lo resuelve a ids
del catálogo bootstrapeado y lo aplica en UNA transacción vía
matching.declare_active_policies — atómico, validado y serializado contra la
valla de canonicidad del evaluador. Un despliegue normal NO invoca declare:
solo bootstrap (filas, sin activación) y status.
"""

import asyncio
import sys

import sqlalchemy as sa

from jobhunt_core import matching
from jobhunt_core.database import task_session_factory


async def _status(session) -> None:
    filas = (
        await session.execute(
            sa.text(
                "SELECT name, prompt_version, active, weights::text AS w "
                "FROM scoring_policies ORDER BY name, prompt_version"
            )
        )
    ).all()
    for f in filas:
        marca = "ACTIVA" if f.active else "      "
        print(f"{marca}  {f.name}:{f.prompt_version}  {f.w}")


async def _declare(session, objetivos: list[str]) -> None:
    ids = await matching.bootstrap_policy_catalog(session)
    por_nombre = {f"{n}:{v}": pid for (n, v), pid in ids.items()}
    desconocidas = [o for o in objetivos if o not in por_nombre]
    if desconocidas:
        raise SystemExit(f"políticas fuera del catálogo: {desconocidas}")
    await matching.declare_active_policies(session, [por_nombre[o] for o in objetivos])
    await session.commit()
    print(f"conjunto activo declarado: {sorted(objetivos)}")
    await _status(session)


async def _main(argv: list[str]) -> None:
    if not argv or argv[0] not in {"status", "declare"}:
        raise SystemExit(__doc__)
    async with task_session_factory() as factory:
        async with factory() as s:
            if argv[0] == "status":
                await _status(s)
            else:
                if not argv[1:]:
                    raise SystemExit("declare exige el conjunto exacto deseado")
                await _declare(s, argv[1:])


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
