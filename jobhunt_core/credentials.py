"""Credenciales de consumidores (A-09, ADR-09 + CONTRATOS §1/§2).

Formato del token: `Authorization: Bearer <key_id>.<secret>` — el secreto solo
existe en el momento de la emisión; en BD queda su sha256 (`hash`). La
verificación es en tiempo constante (hmac.compare_digest). Scopes = lista
JSONB (`vacancies:read`, `matches:read`, `profiles:read`).
"""

import hashlib
import hmac
import json
import logging
import secrets
import uuid

import sqlalchemy as sa

logger = logging.getLogger(__name__)


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def create_credential(
    session, consumer_id, scopes: list[str], expires_at=None
) -> tuple[str, str]:
    """Emite una credencial. Devuelve (key_id, secret) — el secret NO vuelve a
    ser recuperable (solo se guarda su hash)."""
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    await session.execute(
        sa.text(
            "INSERT INTO consumer_credentials "
            "(id, consumer_id, key_id, hash, scopes, expires_at) "
            "VALUES (:id, :cid, :kid, :hash, CAST(:scopes AS jsonb), :exp)"
        ),
        {
            "id": uuid.uuid4(), "cid": consumer_id, "kid": key_id,
            "hash": _hash_secret(secret), "scopes": json.dumps(list(scopes)),
            "exp": expires_at,
        },
    )
    return key_id, secret


async def revoke_credential(session, key_id: str) -> None:
    await session.execute(
        sa.text(
            "UPDATE consumer_credentials SET revoked_at = clock_timestamp() "
            "WHERE key_id = :kid AND revoked_at IS NULL"
        ),
        {"kid": key_id},
    )


async def authenticate(session, token: str):
    """(consumer_id, scopes) o None. Un token inválido por CUALQUIER causa
    (formato, key_id, secreto, revocada, caducada, consumer inactivo) devuelve
    None sin distinguir el motivo — al cliente siempre el mismo 401."""
    key_id, sep, secret = token.partition(".")
    if not sep or not key_id or not secret:
        return None
    row = (
        await session.execute(
            sa.text(
                "SELECT cc.hash, cc.scopes, cc.consumer_id, cc.revoked_at, "
                "cc.expires_at, c.active "
                "FROM consumer_credentials cc "
                "JOIN consumers c ON c.id = cc.consumer_id "
                "WHERE cc.key_id = :kid"
            ),
            {"kid": key_id},
        )
    ).one_or_none()
    if row is None:
        # Comparación fantasma: el tiempo de respuesta no revela si el key_id
        # existe.
        hmac.compare_digest(_hash_secret(secret), _hash_secret("x"))
        return None
    if not hmac.compare_digest(row.hash, _hash_secret(secret)):
        return None
    if row.revoked_at is not None or not row.active:
        return None
    if row.expires_at is not None:
        expired = (
            await session.execute(
                sa.text("SELECT :exp < clock_timestamp()"), {"exp": row.expires_at}
            )
        ).scalar_one()
        if expired:
            return None
    scopes = row.scopes if isinstance(row.scopes, list) else []
    return row.consumer_id, scopes
