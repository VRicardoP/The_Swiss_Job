# Inventario para retirar CDC sin borrar el matching nativo

Comprobado el 2026-10-10 sobre el árbol en `1726d77` y la topología viva
`5a1241d`. Este inventario separa la retirada operativa del slot de la
simplificación posterior al cierre de rollback.

## Cambio operativo al retirar el slot

Estos elementos representan al proceso CDC o lo exigen como servicio vivo:

- `deploy/nas/core.configured.yml`: servicio `core-capture` y
  `CORE_CAPTURE_SLOT` en los cuatro procesos Core.
- `scripts/check_core_release.py`: exige `core-capture` antes del corte y,
  únicamente con `--capture-retired`, exige la topología final de cuatro
  servicios y rechaza que el Compose aún declare captura (`5a6bcab`, `ce45e7a`).
- `scripts/nas/supervisor_contenedores.sh`: contenedor de captura dentro de
  `ESPERADOS`.
- `core-ops-worker`: `CORE_CAPTURE_ENABLED=false` debe retirar del beat sólo
  `shadow-check-slot-health`, `shadow-preview-cycle` y `shadow-run-cycle`.

La maniobra tiene dos fases. Tras parar la captura se añade el interruptor al
worker operativo y se observa una ronda nativa completa. Sólo si esa ronda
queda verde se borra el slot y se retiran el servicio, la variable y los dos
inventarios. Hasta entonces describen correctamente el sistema vivo.

## Código que debe mantenerse durante el corte

- `jobhunt.shadow.project` no es una tarea de captura. Aunque no encuentre
  lotes en `shadow_change_log`, llama a `_replay_after_batch`, drena
  embeddings pendientes y recupera evaluaciones de perfiles de cualquier
  consumer. Es parte del matching nativo.
- `jobhunt_core/shadow/projector.py` contiene además el borrado del grafo de
  perfiles que usan las API de aplicaciones, documentos, colegios y la
  eliminación de cuentas.
- `jobhunt.delivery.dispatch_outbox` y el transporte HTTP siguen entregando
  eventos a ReactPortfolio y SwissJob.
- `core-harvest-worker` y `core-ops-worker` son productores nativos vivos y
  no pertenecen al proceso CDC.

Por tanto, la retirada segura elimina el servicio, el slot y las tres cadencias
del harness. No elimina todavía el proyector, el dispatcher ni sus tablas.

## Limpieza después del cierre formal de rollback

Una vez transferida la autoridad y cerrado rollback, revisar y retirar como un
conjunto coherente:

- `jobhunt_core/shadow/capture.py`, `resync.py` y las funciones de slot,
  replay y gate histórico.
- Las tareas `preview_cycle`, `run_cycle`, `check_slot_health`,
  `compute_cycle` y `purge_staging`, junto con sus entradas de beat.
- Los perfiles Compose de captura, el despliegue rehearsal, el parche histórico
  de Compose, los runbooks de CDC y el ejemplo de entorno de captura.
- El bootstrap y la verificación del rol `jobhunt_capture` en el migrador. Sólo
  después de desplegar ese cambio y comprobar que no quedan sesiones ni
  referencias a `CAPTURE_DSN`, revocar sus privilegios, borrar el rol de
  replicación y retirar el fichero de credenciales del NAS. Dejar el rol con
  `LOGIN REPLICATION` mantendría una superficie operativa que ya no tiene
  consumidor.
- Los tests exclusivos de captura/gate y las costuras antiguas del cutover.
- El fallback `shadow_inbox`, sólo después de demostrar que todos los destinos
  activos usan el transporte HTTP.
- El drenado nativo y el borrado de perfiles que hoy viven en `projector.py`
  deben moverse a módulos con su responsabilidad real antes de borrar el
  paquete histórico.

La prueba de ausencia final será una búsqueda sin referencias ejecutables a
`core-capture`, `CORE_CAPTURE_SLOT` ni a las tres tareas del harness,
excluyendo migraciones inmutables y las actas de auditoría. Se acompañará por la
suite completa y una ronda nativa en producción.
