import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings

logger = logging.getLogger(__name__)


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
    # A-17, la otra mitad: las rutas con `@limiter.limit(...)` —entre ellas las
    # cuatro de /auth— NO pasan por `LimiteGlobalMiddleware`, van por la
    # maquinaria de slowapi. Sin nada de esto, un fallo del almacén las devolvía
    # en 500: un corte de Redis tiraba justamente el camino de login.
    #
    # `in_memory_fallback_enabled` es lo que de verdad lo arregla, y es MEJOR
    # que rendirse: ante un almacén inalcanzable slowapi reintenta la evaluación
    # contra un contador EN MEMORIA, así que el límite sigue aplicándose (por
    # proceso: con `-w 2` son efectivamente 2× el límite, degradación aceptable
    # frente a no tener ninguno).
    in_memory_fallback_enabled=True,
    # `swallow_errors` solo NO bastaba, y hace falta MEDIRLO para verlo: slowapi
    # 0.1.10 traga el error del `hit`, pero el decorador lee después
    # `request.state.view_rate_limit` sin guarda (`extension.py:742`) y ese
    # atributo sólo se fija si la evaluación llegó al final → AttributeError →
    # 500 igual. Se queda como último recurso por debajo del fallback.
    swallow_errors=True,
)


class LimiteGlobalMiddleware(BaseHTTPMiddleware):
    """Límite por defecto para TODA la API, no sólo para las rutas decoradas.

    No necesita saber qué función atiende la ruta ni acoplarse a los internos
    de SlowAPI: le basta el par
    (cliente, camino), que es justamente el bucket que se quiere.
    """

    def __init__(self, app, limite: str | None = None):
        super().__init__(app)
        self._item = parse(limite or settings.RATE_LIMIT_DEFAULT)

    def _permitir(self, identidad: tuple[str, str]) -> bool:
        """¿Cabe esta petición en el bucket? Ante un fallo del ALMACÉN, sí.

        A-17: `limiter._limiter.hit` iba sin protección, y el almacén es Redis
        (`storage_uri=settings.REDIS_URL`). Un `ConnectionError` de
        `RedisStorage.incr` subía por `BaseHTTPMiddleware` y se convertía en un
        **500 para CADA petición**, `/health` y `/api/v1/health` incluidas,
        porque este middleware está delante de todo. Es decir: un corte de Redis
        pasaba de «se degradan el SSE, el bus de caché y el scheduler» a «la API
        entera caída», y además el healthcheck del compose marcaba el contenedor
        unhealthy y lo reiniciaba en bucle.

        Se falla ABIERTO a propósito, y la decisión merece quedar escrita: entre
        «sin Redis no se puede contar, así que no servimos a nadie» y «sin Redis
        no se puede contar, así que servimos sin límite», lo segundo degrada una
        protección y lo primero destruye el servicio. El límite es una defensa
        contra el abuso, no una invariante de corrección: perderlo un rato es
        tolerable; perder la API no.

        El fallo NO es silencioso: se registra como ERROR para que se vea en el
        log, que es lo único que distingue «Redis cayó un segundo» de «llevamos
        una semana sin límite de peticiones».
        """
        try:
            return bool(limiter._limiter.hit(self._item, *identidad))
        except Exception as exc:
            logger.error(
                "rate limit sin almacén (%s): se sirve SIN límite", type(exc).__name__
            )
            return True

    async def dispatch(self, request: Request, call_next):
        if not limiter.enabled:
            return await call_next(request)
        # El camino, no la ruta con parámetros: dos ids distintos son dos
        # buckets. Es más estricto y no depende del enrutador.
        identidad = (get_limiter_key(request), request.url.path)
        if not self._permitir(identidad):
            # 429 propio en vez de la excepción de slowapi: construirla exige
            # armar un LimitGroup a mano y acoplarse a sus internos, que es
            # justo lo que ha resultado frágil.
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {self._item}"},
                headers={"Retry-After": str(self._item.get_expiry())},
            )
        return await call_next(request)
