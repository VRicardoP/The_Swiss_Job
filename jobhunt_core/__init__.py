"""jobhunt-core — servicio unificado de job-hunting (Fase A, ticket A-01).

Paquete + app desplegable DENTRO del repo de SwissJob (ADR-08), con fronteras
estrictas: NO importa módulos del backend legacy (services/, models/, tasks/...);
habla con el exterior solo por su API /v1 y su esquema Postgres propio (`jobhunt`).
Broker/locks en un Redis DEDICADO (`redis-core`), nunca el Redis de caché legacy.
"""

import os

__version__ = "0.1.0"

# SHA de la release que corre ESTE proceso. Lo hornea la imagen al construir
# (ARG → ENV RELEASE_SHA del Dockerfile); NINGÚN servicio del compose lo pone en
# su `environment:` a propósito — inyectarlo al arrancar lo desligaría del código
# y volvería a ser una etiqueta que puede mentir. `version` es una constante que
# no distingue releases, así que sin este dato un operador no puede saber si API,
# worker y capturador corren lo mismo (auditoría externa 2026-08-27 P1-3).
__release_sha__ = os.getenv("RELEASE_SHA") or "unknown"
