"""Configuración del core — AISLADA de la del backend legacy (ADR-08 / §15bis).

Solo variables CORE_* (sin colisión con las del legacy). Sin env_file: los
valores llegan por entorno (compose); los defaults sirven solo para dev.
En prod (CORE_ENV=prod) el validador EXIGE el aislamiento del DoD: rol
jobhunt_core, esquema jobhunt, broker/backend en redis-core y sin credenciales
de dev — la configuración muere al arrancar si no se cumple.
"""

from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Credenciales que SOLO valen en dev; en prod el arranque falla si siguen presentes.
_DEV_PASSWORD = "jobhunt_core_dev"
_DEV_REDIS_PASSWORD = "core_redis_dev"


class CoreSettings(BaseSettings):
    # "dev" | "prod". Los compose de prod lo fijan a "prod" en su bloque
    # environment (no depende del env_file) → fail-fast garantizado.
    CORE_ENV: str = "dev"

    # Postgres COMPARTIDO con el legacy, pero esquema propio + rol de mínimo
    # privilegio (el rol del core no tiene grants sobre `public`).
    CORE_DATABASE_URL: str = (
        f"postgresql+asyncpg://jobhunt_core:{_DEV_PASSWORD}@postgres:5432/swissjobhunter"
    )
    CORE_DB_SCHEMA: str = "jobhunt"

    # Redis DEDICADO para broker/locks (ADR-08), con auth y en red propia
    # (core-net). NUNCA el Redis de caché legacy: su allkeys-lru puede expulsar
    # mensajes Celery/locks bajo presión de memoria.
    CORE_BROKER_URL: str = f"redis://:{_DEV_REDIS_PASSWORD}@redis-core:6379/0"
    CORE_RESULT_BACKEND: str = f"redis://:{_DEV_REDIS_PASSWORD}@redis-core:6379/1"
    # Namespace propio de locks/coordinación (leader-lock separado del legacy).
    CORE_LOCK_PREFIX: str = "jobhunt:"

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def _enforce_isolation_in_prod(self) -> "CoreSettings":
        """El DoD de aislamiento se IMPONE, no se asume (revisión externa A-01 #2)."""
        if self.CORE_ENV != "prod":
            return self

        db = urlsplit(self.CORE_DATABASE_URL)
        if db.username != "jobhunt_core":
            raise ValueError(
                "en prod CORE_DATABASE_URL debe conectar como el rol jobhunt_core "
                f"(recibido: {db.username!r}) — nunca con el usuario legacy/admin"
            )
        if not db.password or db.password == _DEV_PASSWORD:
            raise ValueError(
                "en prod CORE_DATABASE_URL necesita contraseña real (no la de dev): "
                "define CORE_DATABASE_URL/CORE_DB_PASSWORD en .env.core.prod"
            )
        if self.CORE_DB_SCHEMA != "jobhunt":
            raise ValueError(f"en prod CORE_DB_SCHEMA debe ser 'jobhunt' ({self.CORE_DB_SCHEMA!r})")

        for name, url in (
            ("CORE_BROKER_URL", self.CORE_BROKER_URL),
            ("CORE_RESULT_BACKEND", self.CORE_RESULT_BACKEND),
        ):
            u = urlsplit(url)
            if u.hostname != "redis-core":
                raise ValueError(
                    f"en prod {name} debe apuntar al Redis DEDICADO redis-core "
                    f"(recibido host: {u.hostname!r}) — nunca al Redis de caché legacy"
                )
            if not u.password or u.password == _DEV_REDIS_PASSWORD:
                raise ValueError(f"en prod {name} necesita contraseña real de redis-core")
        return self


settings = CoreSettings()
