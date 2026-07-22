"""jobhunt-core — servicio unificado de job-hunting (Fase A, ticket A-01).

Paquete + app desplegable DENTRO del repo de SwissJob (ADR-08), con fronteras
estrictas: NO importa módulos del backend legacy (services/, models/, tasks/...);
habla con el exterior solo por su API /v1 y su esquema Postgres propio (`jobhunt`).
Broker/locks en un Redis DEDICADO (`redis-core`), nunca el Redis de caché legacy.
"""

__version__ = "0.1.0"
