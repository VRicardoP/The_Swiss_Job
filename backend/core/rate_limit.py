from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

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
    # H5/T8: este `default_limits` era CONFIGURACIÓN MUERTA. slowapi sólo lo
    # aplica con su `SlowAPIMiddleware`, que no estaba instalado: únicamente
    # las 5 rutas con decorador tenían límite, el resto de la API ninguno.
    # Y su middleware TAMPOCO sirve aquí — ver `LimiteGlobalMiddleware`.
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.REDIS_URL,
)


class LimiteGlobalMiddleware(BaseHTTPMiddleware):
    """Límite por defecto para TODA la API, no sólo para las rutas decoradas.

    Por qué no `SlowAPIMiddleware`: busca el handler con
    `_find_route_handler`, que exige `hasattr(route, "endpoint")`. Desde
    FastAPI 0.141 los routers incluidos viven en `app.routes` como
    `_IncludedRouter`, que NO tiene ese atributo, así que devuelve `None` y
    `_should_exempt(None)` es True. Comprobado en este árbol: con su
    middleware instalado, 260 peticiones seguidas a `/api/v1/jobs/stats`
    salieron las 260 con 200 y no dejaron ni una clave en Redis. Habría sido
    cambiar una configuración muerta por otra.

    Éste no necesita saber qué función atiende la ruta: le basta el par
    (cliente, camino), que es justamente el bucket que se quiere.
    """

    def __init__(self, app, limite: str | None = None):
        super().__init__(app)
        self._item = parse(limite or settings.RATE_LIMIT_DEFAULT)

    async def dispatch(self, request: Request, call_next):
        if not limiter.enabled:
            return await call_next(request)
        # El camino, no la ruta con parámetros: dos ids distintos son dos
        # buckets. Es más estricto y no depende del enrutador.
        identidad = (get_limiter_key(request), request.url.path)
        if not limiter._limiter.hit(self._item, *identidad):
            # 429 propio en vez de la excepción de slowapi: construirla exige
            # armar un LimitGroup a mano y acoplarse a sus internos, que es
            # justo lo que ha resultado frágil.
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {self._item}"},
                headers={"Retry-After": str(self._item.get_expiry())},
            )
        return await call_next(request)
