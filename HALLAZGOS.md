# Hallazgos pendientes (fuera del alcance de la tarea en curso)

> Hallazgos detectados durante trabajo de saneamiento que NO se arreglan en la
> tarea actual, para mantener diffs mínimos por tarea. Cada uno debería
> convertirse en su propio ticket/commit.

## H-1 · 3 tests de scheduler fallan por mock incompleto (pre-existente)

- **Detectado:** 2026-07-21, durante la pre-fase de saneamiento (rama `fix/prefase-saneamiento`).
- **Estado:** **pre-existente en `main`** (verificado haciendo `git stash` de los cambios de
  pre-fase: los 3 tests siguen rojos sin ellos).
- **Tests afectados** (`tests/test_scheduler.py::TestScheduler`):
  - `test_daily_harvest_mode_registers_harvest_not_interval_fetch`
  - `test_daily_harvest_trigger_has_jitter`
  - `test_legacy_interval_mode_registers_fetch_jobs`
- **Causa raíz:** el `CronTrigger(hour=settings.DAILY_DIGEST_HOUR)` del digest por email
  (commit `07a5c20`) está DENTRO de `if settings.DAILY_DIGEST_ENABLED:` (`scheduler.py:197`).
  El helper `_base_settings(mock)` no fija NI `DAILY_DIGEST_ENABLED` NI `DAILY_DIGEST_HOUR`:
  la rama se ejecuta solo porque un atributo `MagicMock` sin fijar es truthy, y entonces
  APScheduler recibe un `MagicMock` como `hour` y rechaza el campo:

  ```
  ValueError: Unrecognized expression "<MagicMock name='settings.DAILY_DIGEST_HOUR' ...>"
  for field "hour"
  ```

- **Severidad:** baja. Es un fallo **solo de test** (mock desactualizado); producción usa
  `settings` reales y funciona. Pero **enmascara regresiones reales del scheduler** (3 tests
  permanentemente rojos), lo cual es el verdadero riesgo.
- **Fix sugerido (~15 min):** fijar EXPLÍCITAMENTE `DAILY_DIGEST_ENABLED` (True/False; el
  default real en `config.py` es False) **y** `DAILY_DIGEST_HOUR` en el mock `_base_settings`
  de `tests/test_scheduler.py`, no solo el segundo — así el test no registra el job por la
  truthiness del mock. Revisar si faltan otros settings nuevos que el scheduler consuma.
- **Nota adyacente:** falta `pytest-timeout` en la imagen del contenedor — el
  `--timeout=30` del comando de tests de `CLAUDE.md` no está disponible (hay que instalarlo
  o actualizar el comando documentado).

## H-2 · worker-ai serializa embeddings de lote e interactivos (trade-off aceptado)

- **Detectado:** 2026-07-21, revisión de PF.4.
- **Contexto:** el `worker-ai` (cola `ai`, `--concurrency=1`) sirve tanto los embeddings de
  lote de la cosecha (`embed_all_pending`) como los interactivos (`generate_profile_embedding`,
  `analyze_cv_and_autofill`). Un embedding interactivo disparado durante la cosecha diaria
  espera detrás del lote (head-of-line, ventana de minutos).
- **Decisión:** **aceptado.** `--concurrency>1` reintroduciría copias del modelo (el objetivo
  de PF.4 era 1 carga); el camino interactivo es asíncrono con barra de progreso SSE (sin
  bloqueo duro ni fallo) y el solape es raro (cosecha 1×/día). Nota: `analyze_cv_and_autofill`
  **SÍ usa el modelo de embeddings** (`matcher.encode`, `profile_tasks.py:153`), así que
  pertenece correctamente a `worker-ai`; la única alternativa sería una cola `ai_interactive`
  propia (otro worker → 2ª copia del modelo), que no compensa por ahora.
