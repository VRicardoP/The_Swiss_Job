"""Celery del core — broker en redis-core (DEDICADO), colas core.* (§15bis).

Aislamiento estructural, no por convención: el worker legacy escucha
default/scraping/ai en el Redis de caché; este app usa OTRO broker y OTRO
namespace de colas. Aunque un worker legacy apuntara por error al broker del
core, ninguna cola coincide.
"""

from celery import Celery
from celery.schedules import crontab

from jobhunt_core.config import settings

celery_app = Celery(
    "jobhunt_core",
    broker=settings.CORE_BROKER_URL,
    backend=settings.CORE_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Zurich",
    enable_utc=True,
    # TODAS las colas del core viven en el namespace core.* (plan §15bis).
    task_default_queue="core.default",
    task_routes={
        "jobhunt.harvest.*": {"queue": "core.harvest"},
        "jobhunt.embedding.*": {"queue": "core.embedding"},
        "jobhunt.matching.*": {"queue": "core.matching"},
        "jobhunt.notifications.*": {"queue": "core.notifications"},
        # Despacho del outbox (A-10): cola general del core. La entrada
        # EXPLÍCITA de dispatch_outbox existe porque la tarea va en el beat
        # (P1-1) y el invariante "todo lo del beat rutea a core.*" se
        # verifica por nombre exacto.
        "jobhunt.delivery.*": {"queue": "core.default"},
        "jobhunt.delivery.dispatch_outbox": {"queue": "core.default"},
        # Barrido de archivado (F-2/ADR-07): mantenimiento ligero del corpus.
        # Entrada por nombre EXACTO además del comodín: va en el beat y el
        # invariante "todo lo del beat rutea a core.*" se verifica así.
        "jobhunt.maintenance.*": {"queue": "core.default"},
        "jobhunt.maintenance.archive_sweep": {"queue": "core.default"},
        "jobhunt.maintenance.dedup_scan": {"queue": "core.default"},
        "jobhunt.maintenance.dedup_lex_backfill": {"queue": "core.default"},
        "jobhunt.maintenance.dedup_revalidate_by_rule": {"queue": "core.default"},
        "jobhunt.maintenance.purge_retention": {"queue": "core.default"},
        # Proyector de la sombra (B-02, contrato §3): comparte la cola de
        # cosecha — es ingesta, y serializa con los locks del sink.
        "jobhunt.shadow.project": {"queue": "core.harvest"},
        # Métricas/muestreo/purga de la sombra (B-04): observabilidad y
        # mantenimiento, NO ingesta — cola general core.default. En
        # core.harvest el muestreador (cadencia 5 min vía B-05) quedaría
        # detrás de lotes largos del proyector (prefetch=1 + acks_late) y
        # ninguna de estas tareas toca los locks del sink.
        "jobhunt.shadow.sample_outbox_lag": {"queue": "core.default"},
        "jobhunt.shadow.compute_cycle": {"queue": "core.default"},
        "jobhunt.shadow.purge_staging": {"queue": "core.default"},
        "jobhunt.idempotency.purge_expired": {"queue": "core.default"},
        # Harness GATE-SOMBRA (B-05): el ciclo orquestado es ingesta (drena
        # el staging vía el proyector) — serializa en core.harvest; la
        # vigilancia del slot es observabilidad ligera — core.default.
        "jobhunt.shadow.run_cycle": {"queue": "core.harvest"},
        "jobhunt.shadow.check_slot_health": {"queue": "core.default"},
        # Salud de la COSECHA (G9 P2-C): observabilidad de solo lectura — misma
        # decisión que la vigilancia del slot, cola core.default para no quedar
        # detrás de un lote de cosecha en core.harvest (donde la enviaría el
        # comodín "jobhunt.harvest.*").
        "jobhunt.harvest.check_health": {"queue": "core.default"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # CADENCIAS de la sombra (B-05, §5/§6) — SOLO tareas de colas core.*.
    # El beat va EMBEBIDO en el command del core-worker (`worker ... -B`,
    # docker-compose.yml — decisión del propietario 2026-07-25): sobrevive
    # a recreates/restarts, cosa que un `exec -d ... beat` no hacía
    # (moría en silencio con cada `up -d`). Cadencias ajustables
    # por settings CORE_SHADOW_*; el crontab usa timezone Europe/Zurich (la
    # de este app): 06:05 = justo tras el cierre del ciclo (06:00, §5).
    beat_schedule={
        "shadow-sample-outbox-lag": {
            "task": "jobhunt.shadow.sample_outbox_lag",
            "schedule": float(settings.CORE_SHADOW_OUTBOX_SAMPLE_EVERY_S),
        },
        # G9 P2-C: nadie leía consecutive_failures/last_complete_at — una fuente
        # que dejaba de cosechar no producía alerta alguna hasta que el archivado
        # ADR-07 empezaba a retirar vacantes vivas (120 d después).
        "harvest-check-health": {
            "task": "jobhunt.harvest.check_health",
            "schedule": float(settings.CORE_HARVEST_HEALTH_EVERY_S),
        },
        "shadow-check-slot-health": {
            "task": "jobhunt.shadow.check_slot_health",
            "schedule": float(settings.CORE_SHADOW_SLOT_HEALTH_EVERY_S),
        },
        "shadow-run-cycle": {
            "task": "jobhunt.shadow.run_cycle",
            "schedule": crontab(
                hour=settings.CORE_SHADOW_RUN_CYCLE_HOUR,
                minute=settings.CORE_SHADOW_RUN_CYCLE_MINUTE,
            ),
        },
        # P1-1 (rev. externa parte 2): proyector y despacho del outbox EN
        # CADENCIA (5 min) — la proyección/entrega solo al cierre del ciclo
        # (06:05) producía lotes con ~20h de latencia: latencia_p95<=600s y
        # outbox_lag_p99<=300s (§6) eran imposibles. El single-flight del
        # proyector tolera solapes (already_running sale limpio) y el
        # dispatcher usa SKIP LOCKED: varios beats no se pisan.
        "shadow-project": {
            "task": "jobhunt.shadow.project",
            "schedule": float(settings.CORE_SHADOW_PROJECT_EVERY_S),
        },
        "delivery-dispatch-outbox": {
            "task": "jobhunt.delivery.dispatch_outbox",
            "schedule": float(settings.CORE_DELIVERY_DISPATCH_EVERY_S),
        },
        # C-API-W 2º análisis: la purga de idempotency_records vive AQUÍ (beat
        # del core-worker), no en C-2 (cuyo DoD son arpones del piloto).
        "idempotency-purge-expired": {
            "task": "jobhunt.idempotency.purge_expired",
            "schedule": float(settings.CORE_IDEMPOTENCY_PURGE_EVERY_S),
        },
        # F-2 (ADR-07): la salida del corpus. ANTES del cierre de ciclo de
        # las 06:05, para que las métricas del gate midan el corpus podado.
        "maintenance-archive-sweep": {
            "task": "jobhunt.maintenance.archive_sweep",
            "schedule": crontab(hour=5, minute=35),
        },
        # F-5: candidatos de dedup semántico ANTES del cierre de ciclo (y del
        # barrido: un candidato sobre vacante recién archivada no estorba).
        "maintenance-dedup-scan": {
            "task": "jobhunt.maintenance.dedup_scan",
            "schedule": crontab(hour=5, minute=20),
        },
        # O-4: retención de outbox/entregas/inbox sombra/evaluaciones. DESPUÉS
        # del cierre de ciclo de las 06:05 — el ciclo cuenta evaluaciones y
        # dead-letters de la ventana que acaba de cerrar, y purgar antes le
        # cambiaría los números por debajo de los pies.
        "maintenance-purge-retention": {
            "task": "jobhunt.maintenance.purge_retention",
            "schedule": crontab(hour=6, minute=40),
        },
    },
)

celery_app.conf.include = [
    "jobhunt_core.tasks.maintenance",
    "jobhunt_core.tasks.ping",
    "jobhunt_core.tasks.harvest",
    "jobhunt_core.tasks.embedding",
    "jobhunt_core.tasks.matching",
    "jobhunt_core.tasks.delivery",
    "jobhunt_core.tasks.idempotency",
    "jobhunt_core.tasks.shadow",
]
