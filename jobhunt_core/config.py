"""Configuración del core — AISLADA de la del backend legacy (ADR-08 / §15bis).

Solo variables CORE_* (sin colisión con las del legacy). Sin env_file: los
valores llegan por entorno (compose); los defaults sirven solo para dev.
En prod (CORE_ENV=prod) el validador EXIGE el aislamiento del DoD: rol
jobhunt_core, esquema jobhunt, broker/backend en redis-core y sin credenciales
de dev — la configuración muere al arrancar si no se cumple.
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Credenciales que SOLO valen en dev; en prod el arranque falla si siguen presentes.
_DEV_PASSWORD = "jobhunt_core_dev"
_DEV_REDIS_PASSWORD = "core_redis_dev"
# Marcadores de plantilla sin rellenar: también se rechazan en prod (rev. #3).
_PLACEHOLDER_RE = re.compile(r"CAMBIA|CHANGE_?ME|PLACEHOLDER|EXAMPLE", re.IGNORECASE)


def _bad_secret(value: str | None) -> bool:
    return not value or value in (_DEV_PASSWORD, _DEV_REDIS_PASSWORD) or bool(
        _PLACEHOLDER_RE.search(value)
    )


class CoreSettings(BaseSettings):
    # Literal: un valor desconocido ("production", "PROD"...) NO desactiva las
    # guardas en silencio — la configuración no valida (rev. #3).
    CORE_ENV: Literal["dev", "prod"] = "dev"

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
        if db.scheme != "postgresql+asyncpg":
            raise ValueError(
                f"en prod CORE_DATABASE_URL debe ser postgresql+asyncpg:// ({db.scheme!r})"
            )
        if db.username != "jobhunt_core":
            raise ValueError(
                "en prod CORE_DATABASE_URL debe conectar como el rol jobhunt_core "
                f"(recibido: {db.username!r}) — nunca con el usuario legacy/admin"
            )
        if _bad_secret(db.password):
            raise ValueError(
                "en prod CORE_DATABASE_URL necesita contraseña real (no la de dev "
                "ni un marcador de plantilla sin rellenar)"
            )
        if self.CORE_DB_SCHEMA != "jobhunt":
            raise ValueError(f"en prod CORE_DB_SCHEMA debe ser 'jobhunt' ({self.CORE_DB_SCHEMA!r})")

        for name, url in (
            ("CORE_BROKER_URL", self.CORE_BROKER_URL),
            ("CORE_RESULT_BACKEND", self.CORE_RESULT_BACKEND),
        ):
            u = urlsplit(url)
            if u.scheme != "redis":
                raise ValueError(f"en prod {name} debe ser redis:// ({u.scheme!r})")
            if u.hostname != "redis-core":
                raise ValueError(
                    f"en prod {name} debe apuntar al Redis DEDICADO redis-core "
                    f"(recibido host: {u.hostname!r}) — nunca al Redis de caché legacy"
                )
            if _bad_secret(u.password):
                raise ValueError(
                    f"en prod {name} necesita contraseña real de redis-core "
                    "(no la de dev ni un marcador de plantilla)"
                )
        return self


settings = CoreSettings()
