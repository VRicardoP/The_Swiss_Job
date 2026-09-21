# Punto 4 — admisión nativa desplegada, corte de fuente pendiente

Reanudación expresa del propietario el 21-09. Core API, worker y captura ejecutan
`346fd36119f65db528ec404c018c245e2fd42d91`, imagen
`swissjob-core:point4-346fd36`, digest
`sha256:8abe29989673704db41b6a0b6314c983d6a8b813a82e52d522213058de7faa29`.
Esquema core0049 sin migraciones en este lote. Build desde archivo git limpio,
sin red, con SHA del contexto igual en ambos extremos:
`57fcf449dc08b5cf3da71e3763b50d39ec569a20a76198debbe0c22e05fdf03e`.

## Comprobaciones realizadas

- Suite completa en serie: **1.535 passed**, 933,14 s; no cuenta la ejecución
  interrumpida durante la pausa. [Pruebas de admisión](NATIVE_TITLE_ADMISSION_2026-09-21.md).
- Dispatcher candidato sin scopes: cero tareas y cero descargas.
- Worker anterior drenado sin purga; actualización de tres servicios con
  `--no-deps`, margen de parada 2.400 s. API y captura healthy, cero reinicios.
- Worker nuevo `celery@05081acaa7ea` consume las cinco colas core; inspección
  active/reserved/scheduled vacía. No se dejaron consumidores pausados.
- BFF real: ambos feeds HTTP 200, página de 20; totales 1.800/1.799.
  P2 conserva 18 guardados. Entrega de perfiles sin pendientes ni error;
  escritor de feedback core habilitado. Prueba sólo lectura, sin traducción.
- [Worker R5 preparado](R5_WORKER_HANDOVER_PREPARATION_2026-09-21.md), imagen d89b6ee,
  misma cobertura anterior. Trigger de seis horas intacto.

## Configuración y límites

NAS `/share/CACHEDEV1_DATA/Public/unification-e15-20260914/core.configured.yml`:
SHA256 `eef026984ace0685eeed087716b427d4b9c3d4ba73792fc9aafb6d4dc555f7df`.
Copias privadas previa/candidata en `title-preactivation.346fd36/`.
No usar Compose antiguos para recrear servicios actualizados.

Registro agregado local: `/tmp/point4-title-core-deployed.log`,
`/tmp/point4-title-live-reads.json`, `/tmp/point4-title-worker-after.json`.

**No se ha transferido Remotive ni ninguna otra fuente.** Sus guards se han
preparado sin instalar. La consulta inicial de paridad CDC con la credencial de
captura falló por falta de SELECT en jobs: no se ampliaron permisos ni se tomó
ese fallo por paridad válida. Se comprueba con las conexiones ya autorizadas
de cada servicio y las identidades permanecen en NAS.

No hay cambio de política canónica, holdout, feed writer, calidad, frontend ni
esquema. El punto 4 sigue abierto; no se afirma retirada legacy ni entrega final.
