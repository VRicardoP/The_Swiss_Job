# Punto 4 — feedback transferido al core

20-09-2026, 07:12 Europe/Madrid. **Este componente está cerrado y operativo;
punto 4 NO cerrado.** No se activó ninguna fuente nativa ni se retiró todavía
ningún productor de ofertas.

## Código y comprobaciones

- BFF y core API/worker/capture: `39579d7` (lectores compatibles y contexto por
  lote). Migrador de una ejecución: `7724037` (plan por lote, locks de identidad
  compatibles con autenticación). Sin cambio de esquema: core0049.
- Suite BFF **2.470 passed, 4 xfailed**; suite core posterior al fix del migrador
  **1.508 passed**, 893,49 s. Ejecutadas en serie. Avisos conservados en logs.
- Regresiones del plan y del bloqueo de lectores comprobadas en rojo antes
  del fix. En copia NAS: 2.159 filas, 112 cambios, 6 consultas, 9,627 s;
  coincidencia de origen y de TODAS las identidades/valores deseados.
- La recuperación HTTP post-corte sobre copia, con vacante exclusivamente
  nativa, reinicio de API y caída del core, está en
  [FEEDBACK_RECOVERY_NAS_2026-09-20.md](FEEDBACK_RECOVERY_NAS_2026-09-20.md).
  No se escribieron marcas sintéticas, candidaturas ni correos en producción.

## Secuencia aplicada

1. Primer intento de plan cancelado sin aplicación por lentitud y bloqueo de
   lectores; autoridad local restituida antes de corregir el migrador. Véase
   [incidente y corrección](FEEDBACK_PLAN_LOCKS_2026-09-20.md).
2. Segundo freeze confirmado por HTTP, plan NUEVO sobre **2.138 filas actuales**
   (no reutilizar el snapshot de la copia). Sello:
   `bf388a071f3e3ba7d5209f11ac9458a1ebfd6413d28357ec526cb03f639494fa`.
3. Apply transaccional: **112 cambios, verdict=verified**. Origen comparado
   contra el sello; lectura posterior de todos los valores escritos dentro de
   la transacción y recibo persistido después del commit.
4. `CORE_FEEDBACK_ENABLED=true` manteniendo `FEEDBACK_WRITES_FROZEN=true`;
   recreación sólo del BFF. Entrega de perfiles se mantiene habilitada.
5. Canary real de lectura, sin modificar intención del usuario:

   | Perfil (orden privado de bindings) | Feed HTTP BFF | Guardados core/BFF | Contexto / rechazos |
   |---|---|---|---|
   | 1 | 20 / total 1.800 | 0 / total 0 | 12.042 / 0 |
   | 2 | 20 / total 1.799 | 18 / total 18 | 12.235 / 49 |

   Feed por HTTP autenticado del BFF con traducción desactivada; guardados y
   contexto por su cliente real contra core. No se invocó traducción externa.
   Entrega de perfiles sin pendientes/errores. No es prueba de navegador ni p95.
6. Se libera el freeze. HTTP 200 con `writes=enabled, writer=core`; clase efectiva
   `CoreFeedback`. No hay fallback al escritor local.

## Identidad de imágenes

- Core en servicio: `01ffed34e1a31412ec2d1a7688d3b86f388f554dddcaa13320499fb7c2f41de0`.
- BFF en servicio: `246a32ff27d49aa4a1436df84b1767912deefc1548f81c633dbc048141e975a7`.
- Migrador: `7c660602ff1bbc119e4db63f3013881f55096e298fb80e85f0dcaf72dd435ce0`.
- Archivo limpio del migrador: `15e592f1af6a233f95ea62fff948d54f817ff64fdf50798291f4b7c34bfecee7`,
  igual en ambos extremos; revisión OCI y sello horneado `7724037a1cbc2669f16bbc4fb1c194f44cf79f1c`.

No se afirma que todos los procesos tengan la revisión del migrador: los servicios
no necesitan ejecutar su código y se evitó otro reinicio innecesario.

## Evidencia privada y recuperación

Sólo NAS: `unification-e15-20260914/feedback-preactivation.39579d7`, modo 0700;
configuraciones, bindings, plan, recibo y readback privados. Configuración SwissJob
final SHA256 `45c1c81db3fdb302e13733ec2eff67303a3b083139a350263155b829afa04391`.

Backups anteriores al apply, modo 0600, salida 0 y catálogos legibles:

- Fuente: 291.270.847 bytes,
  `f8321d33523f8ef70563fe2528538681f409c1bae561ee78afe3c2c7a9022de0`.
- Esquema core: 502.785.106 bytes,
  `b2712157266fc374bcc9fe05a526b302b0480f3c7b929975470d24d4c8e02338`.

La lectura del catálogo no equivale a restaurar esos mismos dumps. El restore
estricto y los ensayos se hicieron sobre copias previas del NAS. No se exportaron
datos SwissJob al ordenador.

**Desde ahora no volver a feedback local ni usar `revert` pre-activación**:
se perderían decisiones nuevas exclusivamente core. Recuperación compatible
hacia delante, conservando core0049, autoridad de perfiles y feedback. El corte
no cambia políticas, holdout, GO de calidad ni elimina corpus/histórico.

Quedan dentro del punto 4: exclusión global de cosecha por scope, scheduler,
traspaso por fuente, búsquedas/avisos, extracción escolar y drenaje final de CDC.
La parada del worker anterior terminó en 137 tras 600 s: está documentada, no
contabilizada como drenaje limpio. No repetir ese timeout inferior al presupuesto
de tareas ni declarar una cola drenada sólo porque el proceso nuevo responda.
