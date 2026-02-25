from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings


def get_limiter_key(request: Request) -> str:
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_limiter_key,
    default_limits=["100/minute"],
    storage_uri=settings.REDIS_URL,
)
