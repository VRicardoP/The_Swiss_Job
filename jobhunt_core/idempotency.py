"""Persistencia compartida de idempotencia, independiente del adaptador HTTP."""

import sqlalchemy as sa


async def purge_expired(session) -> int:
    """Purga reservas caducadas mediante el índice de ``expires_at``."""
    return (
        await session.execute(
            sa.text("DELETE FROM idempotency_records WHERE expires_at < now()")
        )
    ).rowcount
