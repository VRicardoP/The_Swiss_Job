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
- **Causa raíz:** el helper `_base_settings(mock)` no fija `DAILY_DIGEST_HOUR` (setting
  añadido con el digest por email, commit `07a5c20`). El scheduler registra un cron con
  `settings.DAILY_DIGEST_HOUR`; en el test ese valor es un `MagicMock`, y APScheduler
  rechaza el campo `hour`:

  ```
  ValueError: Unrecognized expression "<MagicMock name='settings.DAILY_DIGEST_HOUR' ...>"
  for field "hour"
  ```

- **Severidad:** baja. Es un fallo **solo de test** (mock desactualizado); producción usa
  `settings` reales y funciona. Pero **enmascara regresiones reales del scheduler** (3 tests
  permanentemente rojos), lo cual es el verdadero riesgo.
- **Fix sugerido (~15 min):** añadir `DAILY_DIGEST_HOUR` (y revisar si faltan otros settings
  nuevos que el scheduler consuma) al mock `_base_settings` en `tests/test_scheduler.py`.
- **Nota adyacente:** falta `pytest-timeout` en la imagen del contenedor — el
  `--timeout=30` del comando de tests de `CLAUDE.md` no está disponible (hay que instalarlo
  o actualizar el comando documentado).
