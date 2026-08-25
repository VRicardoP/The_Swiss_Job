from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings


def get_limiter_key(request: Request) -> str:
    """Clave del bucket de rate limiting.

    G1/P3-28: tras un reverse proxy (NAS) todos los clientes comparten la IP
    del proxy → el 5/minute de /auth/login era un bucket GLOBAL (un usuario
    torpe bloqueaba a todos). Con RATE_LIMIT_TRUST_PROXY (opt-in: activar
    SOLO si el proxy sobreescribe/sanea la cabecera — si no, un cliente
    podría falsificarla para esquivar el límite) se usa el primer salto de
    X-Forwarded-For.
    """
    if settings.RATE_LIMIT_TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
            if client_ip:
                return client_ip
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_limiter_key,
    default_limits=["100/minute"],
    storage_uri=settings.REDIS_URL,
)
