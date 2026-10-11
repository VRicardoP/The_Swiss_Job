"""T9 — las credenciales del core caducan, y el solape olvidado se ve.

Dos agujeros, el mismo origen: nada obligaba a poner fecha. `create_credential`
aceptaba `expires_at=None` y eso es lo que hacía todo el mundo, así que las
credenciales en circulación no caducaban nunca. Y sin caducidad no hay
rotación; sin rotación, la credencial que se filtre vale para siempre.

La verificación YA sabía caducar (`expired` se evalúa en SQL desde A-09): lo
que faltaba era que alguna credencial llevara fecha que comprobar.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core import credentials, rotate_credential
from jobhunt_core.tests.test_integration_runs import db, pytestmark  # noqa: F401


def _consumer(factory, nombre="rotacion-test"):
    async def go():
        async with factory() as s:
            cid = (
                await s.execute(
                    sa.text(
                        "INSERT INTO consumers (id, name, active) "
                        "VALUES (gen_random_uuid(), :n, true) RETURNING id"
                    ),
                    {"n": nombre},
                )
            ).scalar_one()
            await s.commit()
            return cid

    return asyncio.run(go())


def test_una_credencial_nace_con_caducidad(db):  # noqa: F811
    """El defecto ya no es «perpetua»."""
    factory, _ = db
    cid = _consumer(factory)

    async def go():
        async with factory() as s:
            key_id, _ = await credentials.create_credential(s, cid, ["vacancies:read"])
            await s.commit()
            return (
                await s.execute(
                    sa.text(
                        "SELECT expires_at FROM consumer_credentials WHERE key_id = :k"
                    ),
                    {"k": key_id},
                )
            ).scalar_one()

    caduca = asyncio.run(go())
    assert caduca is not None, "volvió a emitirse una credencial perpetua"
    dias = (caduca - datetime.now(timezone.utc)).days
    assert 85 <= dias <= 90, dias


def test_una_caducidad_explicita_manda(db):  # noqa: F811
    """Control: el defecto no puede pisar lo que pide quien llama — hay
    ensayos que necesitan una credencial de horas, o una ya caducada."""
    factory, _ = db
    cid = _consumer(factory, "rotacion-explicita")
    pedida = datetime.now(timezone.utc) + timedelta(hours=2)

    async def go():
        async with factory() as s:
            key_id, _ = await credentials.create_credential(
                s, cid, ["vacancies:read"], expires_at=pedida
            )
            await s.commit()
            return (
                await s.execute(
                    sa.text(
                        "SELECT expires_at FROM consumer_credentials WHERE key_id = :k"
                    ),
                    {"k": key_id},
                )
            ).scalar_one()

    assert abs((asyncio.run(go()) - pedida).total_seconds()) < 1


def test_una_credencial_caducada_no_autentica(db):  # noqa: F811
    """Que la fecha TENGA efecto, no que esté guardada."""
    factory, _ = db
    cid = _consumer(factory, "rotacion-caducada")

    async def go():
        async with factory() as s:
            key_id, secret = await credentials.create_credential(
                s,
                cid,
                ["vacancies:read"],
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            viva, secreto_vivo = await credentials.create_credential(
                s, cid, ["vacancies:read"]
            )
            await s.commit()
            return (
                await credentials.authenticate(s, f"{key_id}.{secret}"),
                await credentials.authenticate(s, f"{viva}.{secreto_vivo}"),
            )

    caducada, viva = asyncio.run(go())
    assert caducada is None
    assert viva is not None, "control: una credencial con fecha futura sí entra"


def test_el_solape_olvidado_alerta_y_el_reciente_no(db):  # noqa: F811
    """Rotar con solape es correcto; quedarse en el solape, no.

    Dos credenciales vivas recién emitidas son una rotación en curso y la
    alerta calla. Las mismas dos con la última emitida hace un mes son una
    rotación abandonada: la vieja sigue autorizando y nadie la echa de menos.
    """
    factory, _ = db
    cid = _consumer(factory, "rotacion-solape")

    async def go():
        async with factory() as s:
            await credentials.create_credential(s, cid, ["vacancies:read"])
            await credentials.create_credential(s, cid, ["vacancies:read"])
            await s.commit()
            recien = await credentials.active_credential_alerts(s)

            # Se envejece la emisión: el margen se cuenta desde la ÚLTIMA.
            await s.execute(
                sa.text(
                    "UPDATE consumer_credentials "
                    "SET created_at = created_at - interval '30 days' "
                    "WHERE consumer_id = :cid"
                ),
                {"cid": cid},
            )
            await s.commit()
            viejo = await credentials.active_credential_alerts(s)

            # Y cerrar la rotación apaga la alerta.
            key = (
                await s.execute(
                    sa.text(
                        "SELECT key_id FROM consumer_credentials "
                        "WHERE consumer_id = :cid ORDER BY created_at LIMIT 1"
                    ),
                    {"cid": cid},
                )
            ).scalar_one()
            await credentials.revoke_credential(s, key)
            await s.commit()
            return recien, viejo, await credentials.active_credential_alerts(s)

    recien, viejo, cerrado = asyncio.run(go())
    nombres = lambda alertas: [a["consumer"] for a in alertas]  # noqa: E731
    assert "rotacion-solape" not in nombres(recien), "gritó durante un solape legítimo"
    assert "rotacion-solape" in nombres(viejo)
    assert [a for a in viejo if a["consumer"] == "rotacion-solape"][0]["vivas"] == 2
    assert "rotacion-solape" not in nombres(cerrado)


def test_add_scope_is_separate_idempotent_and_immediate(db):  # noqa: F811
    factory, _ = db
    cid = _consumer(factory, "scope-aditivo")

    async def go():
        async with factory() as session:
            key_id, secret = await credentials.create_credential(
                session, cid, ["vacancies:read"]
            )
            await session.commit()
            first = await credentials.add_scope(session, key_id, "harvest:write")
            second = await credentials.add_scope(session, key_id, "harvest:write")
            await session.commit()
            authenticated = await credentials.authenticate(
                session, f"{key_id}.{secret}"
            )
            return first, second, authenticated

    first, second, authenticated = asyncio.run(go())
    assert first is True and second is False
    assert authenticated is not None
    assert authenticated[1] == ["vacancies:read", "harvest:write"]


def test_add_scope_rejects_invalid_vocabulary_before_database(db):  # noqa: F811
    factory, _ = db

    async def go():
        async with factory() as session:
            try:
                await credentials.add_scope(session, "unused", "harvest:admin")
            except ValueError as exc:
                return str(exc)
        raise AssertionError("scope inválido aceptado")

    assert "scope inválido" in asyncio.run(go())


def test_authorize_command_updates_the_only_live_credential(db, monkeypatch):  # noqa: F811
    factory, _ = db
    cid = _consumer(factory, "scope-command")

    async def seed_and_read():
        async with factory() as session:
            key_id, _ = await credentials.create_credential(
                session, cid, ["vacancies:read"]
            )
            await session.commit()
            return key_id

    key_id = asyncio.run(seed_and_read())
    monkeypatch.setattr(rotate_credential, "SessionLocal", factory)
    asyncio.run(rotate_credential.autorizar("scope-command", "harvest:write"))
    asyncio.run(rotate_credential.autorizar("scope-command", "harvest:write"))

    async def read_scopes():
        async with factory() as session:
            return await session.scalar(
                sa.text("SELECT scopes FROM consumer_credentials WHERE key_id=:key"),
                {"key": key_id},
            )

    assert asyncio.run(read_scopes()) == ["vacancies:read", "harvest:write"]


def test_authorize_command_refuses_overlapping_credentials(db, monkeypatch):  # noqa: F811
    factory, _ = db
    cid = _consumer(factory, "scope-command-overlap")

    async def seed():
        async with factory() as session:
            await credentials.create_credential(session, cid, ["vacancies:read"])
            await credentials.create_credential(session, cid, ["vacancies:read"])
            await session.commit()

    asyncio.run(seed())
    monkeypatch.setattr(rotate_credential, "SessionLocal", factory)
    with pytest.raises(SystemExit, match="exactamente una credencial viva"):
        asyncio.run(
            rotate_credential.autorizar("scope-command-overlap", "harvest:write")
        )
