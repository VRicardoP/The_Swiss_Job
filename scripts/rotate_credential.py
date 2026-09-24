"""Rotación de una credencial de consumer CON SOLAPE — T9.

Una credencial perpetua no se rota nunca porque nada obliga; una que caduca a
los 90 días hay que rotarla, y rotarla mal deja al consumidor fuera. El solape
es lo que evita el corte: primero se emite la nueva, luego se despliega, y sólo
cuando se confirma que el consumidor la usa se revoca la vieja.

Dos pasos, dos invocaciones, a propósito — entre ellas hay un despliegue que
este script no puede hacer ni verificar:

    python -m scripts.rotate_credential emitir  <consumer>      # paso 1
    python -m scripts.rotate_credential cerrar  <consumer> <key_id_viejo>

`emitir` imprime el secreto UNA vez: no vuelve a ser recuperable. `cerrar` se
niega a revocar si es la única credencial viva del consumer —eso no es cerrar
una rotación, es dejar al consumidor sin acceso— y exige el `key_id` exacto,
para que revocar sea un acto deliberado y no «la más vieja, supongo».

Sin argumentos, `listar` dice qué hay vivo y desde cuándo.
"""

import argparse
import asyncio
import sys

import sqlalchemy as sa

from jobhunt_core import credentials
from jobhunt_core.database import SessionLocal


async def _consumer_id(session, nombre: str):
    fila = (
        await session.execute(
            sa.text("SELECT id, active FROM consumers WHERE name = :n"), {"n": nombre}
        )
    ).one_or_none()
    if fila is None:
        sys.exit(f"consumer desconocido: {nombre}")
    if not fila.active:
        sys.exit(f"consumer inactivo: {nombre} (no se rota lo que está apagado)")
    return fila.id


async def _vivas(session, consumer_id) -> list:
    return (
        await session.execute(
            sa.text(
                "SELECT key_id, scopes, created_at, expires_at "
                "FROM consumer_credentials "
                "WHERE consumer_id = :cid AND revoked_at IS NULL "
                "  AND (expires_at IS NULL OR expires_at > clock_timestamp()) "
                "ORDER BY created_at"
            ),
            {"cid": consumer_id},
        )
    ).all()


async def listar(nombre: str) -> None:
    async with SessionLocal() as session:
        vivas = await _vivas(session, await _consumer_id(session, nombre))
    if not vivas:
        print(f"{nombre}: ninguna credencial viva")
        return
    for fila in vivas:
        caduca = (
            fila.expires_at.isoformat() if fila.expires_at else "NUNCA (anterior a T9)"
        )
        print(
            f"  {fila.key_id}  emitida {fila.created_at:%Y-%m-%d %H:%M}  caduca {caduca}"
        )
        print(f"      scopes: {fila.scopes}")


async def emitir(nombre: str) -> None:
    """Paso 1: la nueva credencial nace CON LOS MISMOS scopes que la viva.

    Copiarlos en vez de pedirlos por parámetro es deliberado: una rotación que
    de paso cambia los permisos es dos cambios a la vez, y el día que algo
    falle no se sabrá cuál de los dos fue.
    """
    async with SessionLocal() as session:
        consumer_id = await _consumer_id(session, nombre)
        vivas = await _vivas(session, consumer_id)
        if not vivas:
            sys.exit(
                f"{nombre} no tiene ninguna credencial viva de la que copiar los "
                "scopes; si es un alta, emítela con create_credential"
            )
        if len(vivas) > 1:
            sys.exit(
                f"{nombre} ya tiene {len(vivas)} credenciales vivas: hay una "
                "rotación a medias. Ciérrala antes de empezar otra."
            )
        key_id, secret = await credentials.create_credential(
            session, consumer_id, list(vivas[0].scopes or [])
        )
        await session.commit()
    print(f"credencial nueva para {nombre}:")
    print(f"  CORE_CONSUMER_KEY={key_id}.{secret}")
    print("  (el secreto NO se puede recuperar: guárdalo ahora)")
    print(
        f"\nla vieja ({vivas[0].key_id}) SIGUE VALIENDO. Despliega, comprueba, y luego:"
    )
    print(f"  python -m scripts.rotate_credential cerrar {nombre} {vivas[0].key_id}")


async def cerrar(nombre: str, key_id: str) -> None:
    async with SessionLocal() as session:
        consumer_id = await _consumer_id(session, nombre)
        vivas = await _vivas(session, consumer_id)
        claves = {fila.key_id for fila in vivas}
        if key_id not in claves:
            sys.exit(
                f"{key_id} no está entre las credenciales vivas de {nombre} "
                f"({', '.join(sorted(claves)) or 'ninguna'})"
            )
        if len(vivas) < 2:
            sys.exit(
                f"{key_id} es la ÚNICA credencial viva de {nombre}: revocarla "
                "no cierra una rotación, deja al consumidor fuera"
            )
        await credentials.revoke_credential(session, key_id)
        await session.commit()
    print(
        f"{key_id} revocada. {nombre} queda con {len(vivas) - 1} credencial(es) viva(s)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=["listar", "emitir", "cerrar"])
    parser.add_argument("consumer")
    parser.add_argument("key_id", nargs="?")
    args = parser.parse_args()
    if args.accion == "cerrar":
        if not args.key_id:
            parser.error("cerrar exige el key_id de la credencial que se revoca")
        asyncio.run(cerrar(args.consumer, args.key_id))
    elif args.accion == "emitir":
        asyncio.run(emitir(args.consumer))
    else:
        asyncio.run(listar(args.consumer))


if __name__ == "__main__":
    main()
