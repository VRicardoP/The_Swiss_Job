"""Credenciales de consumidores (A-09, ADR-09 + CONTRATOS §1/§2).

Formato del token: `Authorization: Bearer <key_id>.<secret>` — el secreto solo
existe en el momento de la emisión; en BD queda su sha256 (`hash`). La
verificación es en tiempo constante (hmac.compare_digest). Scopes = lista
JSONB; `_VALID_SCOPES` refleja los permisos exigidos por las rutas de Core.
"""

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def create_credential(
    session, consumer_id, scopes: list[str], expires_at=None
) -> tuple[str, str]:
    """Emite una credencial. Devuelve (key_id, secret) — el secret NO vuelve a
    ser recuperable (solo se guarda su hash).

    T9: sin `expires_at` la credencial es PERPETUA, y una credencial perpetua no
    se rota nunca porque nada obliga. La verificación ya sabe caducar (`expired`
    se evalúa en SQL), pero ninguna credencial emitida llevaba fecha. Ahora el
    defecto es `CORE_CREDENTIAL_TTL_DAYS` (90 d): emitir una sin caducidad deja
    de ser lo que pasa por omisión y pasa a requerir una fecha lejana explícita.
    """
    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=int(settings.CORE_CREDENTIAL_TTL_DAYS)
        )
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    await session.execute(
        sa.text(
            "INSERT INTO consumer_credentials "
            "(id, consumer_id, key_id, hash, scopes, expires_at) "
            "VALUES (:id, :cid, :kid, :hash, CAST(:scopes AS jsonb), :exp)"
        ),
        {
            "id": uuid.uuid4(),
            "cid": consumer_id,
            "kid": key_id,
            "hash": _hash_secret(secret),
            "scopes": json.dumps(list(scopes)),
            "exp": expires_at,
        },
    )
    return key_id, secret


_VALID_SCOPES = frozenset(
    {
        "applications:read",
        "applications:write",
        "documents:read",
        "documents:write",
        "harvest:write",
        "matches:read",
        "profiles:read",
        "profiles:write",
        "saved_searches:read",
        "saved_searches:write",
        "schools:read",
        "schools:write",
        "vacancies:read",
    }
)


async def add_scope(session, key_id: str, scope: str) -> bool:
    """Add one validated scope to a live credential, idempotently.

    Scope changes stay separate from credential rotation. The row lock makes
    expiry/revocation checks and the update one operation.
    """
    scope = scope.strip()
    if scope not in _VALID_SCOPES:
        raise ValueError(f"scope inválido: {scope!r}")
    row = (
        await session.execute(
            sa.text(
                "SELECT scopes FROM consumer_credentials "
                "WHERE key_id=:kid AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at>clock_timestamp()) "
                "FOR UPDATE"
            ),
            {"kid": key_id},
        )
    ).one_or_none()
    if row is None:
        raise ValueError(f"credencial no viva: {key_id}")
    scopes = row.scopes
    if not isinstance(scopes, list) or not all(
        isinstance(existing, str) for existing in scopes
    ):
        raise ValueError(f"scopes corruptos en credencial: {key_id}")
    if scope in scopes:
        return False
    await session.execute(
        sa.text(
            "UPDATE consumer_credentials SET scopes=CAST(:scopes AS jsonb) "
            "WHERE key_id=:kid"
        ),
        {"kid": key_id, "scopes": json.dumps([*scopes, scope])},
    )
    return True


async def revoke_credential(session, key_id: str) -> None:
    await session.execute(
        sa.text(
            "UPDATE consumer_credentials SET revoked_at = clock_timestamp() "
            "WHERE key_id = :kid AND revoked_at IS NULL"
        ),
        {"kid": key_id},
    )


# Hash dummy PRECOMPUTADO: la rama key_id-inexistente compara contra él — ni
# una query ni un hash de más respecto a la rama real (rev. A-09 #4).
_DUMMY_HASH = hashlib.sha256(b"jobhunt-core-dummy-credential").hexdigest()


async def authenticate(session, token: str):
    """(consumer_id, scopes) o None. Un token inválido por CUALQUIER causa
    (formato, key_id, secreto, revocada, caducada, consumer inactivo) devuelve
    None sin distinguir el motivo — mismo 401 y MISMO camino de ejecución
    (rev. A-09 #4: una query única con la expiración evaluada en SQL, el hash
    candidato calculado UNA vez y compare_digest SIEMPRE ejecutado)."""
    key_id, sep, secret = token.partition(".")
    if not sep or not key_id or not secret:
        return None
    candidate = _hash_secret(secret)
    row = (
        await session.execute(
            sa.text(
                "SELECT cc.hash, cc.scopes, cc.consumer_id, "
                "(cc.revoked_at IS NOT NULL) AS revoked, "
                "(cc.expires_at IS NOT NULL "
                " AND cc.expires_at < clock_timestamp()) AS expired, "
                "c.active "
                "FROM consumer_credentials cc "
                "JOIN consumers c ON c.id = cc.consumer_id "
                "WHERE cc.key_id = :kid"
            ),
            {"kid": key_id},
        )
    ).one_or_none()
    stored = row.hash if row is not None else _DUMMY_HASH
    ok = hmac.compare_digest(stored, candidate)
    if row is None or not ok or row.revoked or row.expired or not row.active:
        return None
    scopes = row.scopes if isinstance(row.scopes, list) else []
    return row.consumer_id, scopes


async def active_credential_alerts(
    session, overlap_days: int | None = None
) -> list[dict]:
    """Consumers con MÁS de una credencial viva (T9).

    Rotar con solape es correcto: se emite la nueva, se despliega, y sólo
    entonces se revoca la vieja. Lo que no es correcto es quedarse ahí. Una
    credencial vieja que nadie usa pero sigue valiendo es exactamente la que
    nadie echa de menos cuando se filtra.

    Desviación deliberada del acta (que pide alertar con >1 a secas): se da un
    margen de `CORE_CREDENTIAL_OVERLAP_ALERT_DAYS` desde la emisión de la más
    nueva. `check_health` corre cada hora y un solape legítimo dura horas: sin
    margen, la alerta gritaría en cada rotación y se aprendería a ignorarla.
    """
    margen = (
        overlap_days
        if overlap_days is not None
        else int(settings.CORE_CREDENTIAL_OVERLAP_ALERT_DAYS)
    )
    filas = (
        await session.execute(
            sa.text(
                "SELECT c.name AS consumer, count(*) AS vivas, "
                "  min(cc.created_at) AS mas_vieja, max(cc.created_at) AS mas_nueva "
                "FROM consumer_credentials cc "
                "JOIN consumers c ON c.id = cc.consumer_id "
                "WHERE cc.revoked_at IS NULL AND c.active "
                "  AND (cc.expires_at IS NULL OR cc.expires_at > clock_timestamp()) "
                "GROUP BY c.id, c.name HAVING count(*) > 1 "
                "ORDER BY c.name"
            )
        )
    ).all()
    ahora = datetime.now(timezone.utc)
    alertas = []
    for fila in filas:
        edad = (ahora - fila.mas_nueva).total_seconds() / 86400
        if edad <= margen:
            continue  # solape en curso, no solape olvidado
        alertas.append(
            {
                "code": "credenciales_solapadas",
                "consumer": fila.consumer,
                "vivas": int(fila.vivas),
                "dias_desde_la_ultima_emision": round(edad, 1),
                "msg": (
                    f"consumer {fila.consumer}: {fila.vivas} credenciales vivas "
                    f"{edad:.0f} d después de emitir la última (> {margen} d) — "
                    "la rotación quedó a medias y la credencial vieja sigue "
                    "autorizando; revocarla con `python -m jobhunt_core.rotate_credential`"
                ),
            }
        )
    return alertas
