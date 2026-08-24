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

from pydantic import Field, model_validator
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

    # Embeddings (A-06, ADR-02): MISMO modelo multilingüe 384d que el legacy —
    # los vectores deben ser comparables en la sombra de Fase B.
    CORE_EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
    CORE_EMBEDDING_BATCH_SIZE: int = 32

    # Cadencias del harness GATE-SOMBRA (B-05, §5/§6) — las consume el beat
    # de celery_app.py (corre en el core-worker LOCAL, ver shadow/RUNBOOK.md).
    CORE_SHADOW_OUTBOX_SAMPLE_EVERY_S: int = 300   # sample_outbox_lag (§5)
    CORE_SHADOW_SLOT_HEALTH_EVERY_S: int = 300     # check_slot_health (§6)
    CORE_SHADOW_RUN_CYCLE_HOUR: int = 6            # run_cycle diario 06:05
    CORE_SHADOW_RUN_CYCLE_MINUTE: int = 5          # (Europe/Zurich, tras el
    #                                              cierre del ciclo a las 06:00)
    # P1-1 (rev. externa parte 2): proyección y despacho del outbox EN
    # CADENCIA — con la proyección solo diaria (06:05) los lotes acumulaban
    # ~20h de latencia y latencia_p95<=600s / outbox_lag_p99<=300s (§6) eran
    # matemáticamente imposibles.
    CORE_SHADOW_PROJECT_EVERY_S: int = 300         # jobhunt.shadow.project
    CORE_DELIVERY_DISPATCH_EVERY_S: int = 300      # jobhunt.delivery.dispatch_outbox
    # C-API-W 2º análisis: barrido de idempotency_records caducados (acota la
    # retención del cv_text guardado en response al TTL de 24h).
    CORE_IDEMPOTENCY_PURGE_EVERY_S: int = 3600
    # Barrido de archivado ADR-07 (F-2, 2026-08-22) — la SALIDA del corpus.
    # GRACE: días tras cerrar la última encarnación antes de archivar la
    # vacante muerta (amortigua flaps cierre→reapertura). STALE: los 120 d
    # "sin visto y sin adjunto" del contrato ADR-07.
    CORE_ARCHIVE_GRACE_DAYS: int = 3
    CORE_CORPUS_STALE_DAYS: int = 120
    # Dedup semántico nivel 3 (F-5): generador de candidatos cross-source.
    # SIM_MIN hereda el umbral del dedup semántico legado (0,95) como punto
    # de partida operativo — B.3 dejó los SIM_* abiertos y la precisión
    # medida contra el oráculo dirá si hay que subirlo. KNN acota vecinos por
    # vacante nueva; WINDOW = ventana incremental (48 h ≈ beat diario + margen).
    CORE_DEDUP_SIM_MIN: float = 0.95
    CORE_DEDUP_KNN: int = 5
    CORE_DEDUP_SCAN_WINDOW_H: int = 48
    # Generador LÉXICO cross-portal (TRACK R.2b, medido en development-2:
    # 9/9 dup, 0 FP con trgm>=0.65; el ANN a 0.95 daba 0/9 en ese caso)
    CORE_DEDUP_LEX_TRGM_MIN: float = 0.65
    # Brazo INTRA-fuente (fase 2): umbral PROPIO más duro — el 94 % de los
    # FP históricos del legacy eran intra; se fija con development-3
    CORE_DEDUP_LEX_TRGM_INTRA_MIN: float = 0.90
    CORE_DEDUP_LEX_TOKEN_MAX_FREQ: int = 50
    # P1 rev. externa C-API-W: cota de ESPERA del INSERT-reserva de idempotencia
    # sobre el índice único. Sin ella, si el handler del dueño se cuelga, un
    # reintento de la MISMA key bloquea indefinidamente (no es deadlock que
    # Postgres detecte) y agota workers. Al superarse ⇒ 409 idempotency_in_progress.
    # gt=0 OBLIGATORIO (2ª rev. externa): Postgres interpreta lock_timeout=0 como
    # DESACTIVADO — un 0 reintroduciría la espera infinita que este setting cierra.
    # Cota superior operativa (le=60s): más allá no acota nada útil.
    CORE_IDEMPOTENCY_LOCK_TIMEOUT_MS: int = Field(3000, gt=0, le=60_000)

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
        # requirepass es único por instancia: broker y result backend DEBEN
        # llevar la misma contraseña (rev. 3ª #3 — si difieren, Celery falla
        # auth en uno de los dos aunque la API parezca ready).
        if (
            urlsplit(self.CORE_BROKER_URL).password
            != urlsplit(self.CORE_RESULT_BACKEND).password
        ):
            raise ValueError(
                "CORE_BROKER_URL y CORE_RESULT_BACKEND deben llevar la MISMA "
                "contraseña de redis-core (requirepass es único por instancia)"
            )
        return self


settings = CoreSettings()
