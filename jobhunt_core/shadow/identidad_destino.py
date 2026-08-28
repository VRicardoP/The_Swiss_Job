"""Identidad de la base a la que el core se conecta DE VERDAD.

POR QUÉ EXISTE (auditoría externa R4 P1-1). El aislamiento del ensayo del
cutover (`backend/scripts/nas_cutover.sh`, `ENSAYO=1`) comparaba el `CORE_DSN`
como CADENA y solo rechazaba las que TERMINABAN en el nombre de la base de
producción. Una URL de PostgreSQL válida admite `?parámetros`, `#fragmento` y
percent-encoding, así que `…/swissjobhunter?ssl=require` pasaba la guarda y
llegaba al Paso 5 EN FIRME sobre la base viva.

El script ya parsea el DSN, pero una cadena bien formada sigue sin demostrar a
QUÉ base se conecta el proceso: el DSN efectivo sale de `--env-file`s, de
`CORE_DATABASE_URL` y de la validación de `settings`. Este módulo cierra esa
distancia: resuelve el DSN EXACTAMENTE igual que el one-shot del Paso 5
(`task_session_factory`, el mismo engine y el mismo `settings`) y publica la
identidad de la base que ve. El script compara esa identidad con la que le
devuelve `psql`, y para si difieren — antes de la primera escritura.

Es SOLO LECTURA: ejecuta la consulta que recibe y no confirma nada (la sesión se
cierra sin commit). Se exige que empiece por `SELECT` para que un error de
invocación no se convierta en una escritura.

La consulta la pasa el script (una sola fuente de verdad para los dos lados de
la comparación); en la práctica es

    SELECT current_database()
      || '|' || (SELECT oid::text FROM pg_database WHERE datname = current_database())
      || '|' || to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',
                        'YYYY-MM-DD HH24:MI:SS.US')

— base, oid y arranque del postmaster: distingue dos bases del mismo servidor y
dos servidores con la misma base, no necesita privilegios, y `to_char` la deja
libre de `DateStyle`/`TimeZone` (dos clientes distintos formatean un
`timestamptz` de forma distinta y la comparación sería un rojo falso).

CLI:
  docker run --rm … swissjob-core:prod \\
    python -m jobhunt_core.shadow.identidad_destino "SELECT current_database()"
"""

import asyncio
import sys

import sqlalchemy as sa


async def _leer(sql: str) -> str:
    from jobhunt_core.database import task_session_factory

    async with task_session_factory() as factory:
        async with factory() as session:
            return str((await session.execute(sa.text(sql))).scalar_one())


def main(argv: list[str] | None = None) -> int:
    """Imprime la identidad y devuelve 0; 2 si la invocación no es válida."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].strip():
        print(
            "uso: python -m jobhunt_core.shadow.identidad_destino '<SELECT …>'",
            file=sys.stderr,
        )
        return 2
    sql = args[0].strip()
    if not sql.upper().startswith("SELECT"):
        print(
            "solo se admite una consulta SELECT: esta sonda no escribe", file=sys.stderr
        )
        return 2
    print(asyncio.run(_leer(sql)))
    return 0


if __name__ == "__main__":  # pragma: no cover — envoltorio fino del CLI
    sys.exit(main())
