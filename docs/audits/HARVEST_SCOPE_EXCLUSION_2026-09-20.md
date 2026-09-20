# Punto 4: exclusión global de cosecha por scope

Estado: implementación local verificada; **no desplegada**, ninguna fuente
nativa habilitada. No cierra la retirada de productores.

## Defectos reproducidos antes de corregir

1. Dos `run_key` distintos obtenían permiso para el mismo scope: la PK de
   `source_harvest_runs` sólo excluía otra invocación del mismo run.
2. Un run nuevo no revocaba el token expirado de un run diferente: ambos podían
   seguir figurando como autoridades de escritura/heartbeat.
3. La tarea individual/manual no reclamaba ningún permiso; podía descargar
   simultáneamente con `run_all`.
4. Durante la corrección, reintentar un run ya terminado revocaba el permiso
   expirado de otro run aunque el reintento no pudiera adquirir nada. Regresión
   añadida y comprobada en rojo antes de introducir la salida idempotente temprana.

Logs locales: `/tmp/point4-cross-run-claim-red.log`,
`/tmp/point4-manual-claim-red.log`, `/tmp/point4-scope-idempotent-red.log`.

## Solución y límites

Se reutilizan las tablas, el fencing y el heartbeat existentes. Antes de reclamar
se bloquea la fila permanente `harvest_scopes`: ámbito → claim → estado de cursor.
La contabilidad de errores con token toma el mismo orden que la persistencia y
la reclamación. Se comprueba la vigencia DESPUÉS de bloquear los claims, evitando
decidir sobre un heartbeat anterior a la espera del lock.

El permiso expirado de otro run se cierra antes de crear el nuevo; el viejo ya
no puede latir, finalizar ni persistir. Un reintento terminado no lo modifica.
La entrada manual usa el mismo protocolo, comprueba habilitación y adquiere el
permiso antes de abrir el cliente HTTP. Mantiene heartbeat durante el fetch y
cierra el run tanto en éxito como en error. Si muere el proceso, expira el permiso.
No se introduce otro mutex, tabla ni servicio de coordinación.

Un worker desahuciado aún puede haber iniciado una petición externa; el fencing
protege persistencia, no deshace tráfico emitido. `run_scope` sigue siendo una
primitiva de bajo nivel para pruebas; los productores Celery sí usan claims.
El resultado agregado `ok` de un run vacío no demuestra frescura de las fuentes:
la aceptación exige `source_scope_state`, contenido y feed servido.

## Verificación

- **71 pruebas dirigidas verdes** tras los cinco primeros escenarios.
- **8/8 escenarios nuevos verdes** tras añadir error transitorio/configuración y
  scope inexistente; no sumar contadores solapados.
- Suite completa: **1.516 passed**, dos avisos previos, 871,69 s.
  `/tmp/point4-global-scope-core-full.log`. Código inmóvil durante la ejecución.
  El primer comando carecía de los montajes de scripts y falló en colección;
  se relanzó con los dos compose de desarrollo, sin modificar pruebas ni producto.

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps \
  core-migrate python -m pytest jobhunt_core/tests -q
```

## Preflight NAS de esta continuación

Sin escrituras productivas: core tiene 28 scopes `legacy:*` y uno
`school-observation`, todos deshabilitados para cosecha nativa; 57.960 cambios CDC
aplicados y ninguno pendiente en la lectura. Esto no certifica ausencia de errores
históricos de proyección ni permite retirar CDC todavía.

Los dos workers legacy siguen configurados sin cortes por fuente. Hay 9 providers
sin credenciales en cada inventario. Fuentes con último éxito de agosto no se
consideran saludables sólo porque su último estado fuera `ok`; public worker ya
incluye la corrección de equidad, pendiente de observar el siguiente barrido.
R5 no incluye Jobicy en su registro, el público sí. No se eliminó cobertura.

Siguientes dependencias: planificador nativo con presupuesto por fuente, ensayo
de identidad/histórico y corte por fuente, extracción escolar, búsquedas/avisos,
drenaje final y retirada de los procesos legacy sin quitar CV/avisos necesarios.
