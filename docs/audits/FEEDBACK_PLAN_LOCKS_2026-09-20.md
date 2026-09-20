# Punto 4 — ensayo del plan real y bloqueo de lectores

## Estado operativo

Core API/worker/capture y BFF: imagen limpia `point4-39579d7`. Perfiles siguen
bajo autoridad core0049. **Feedback continúa LOCAL y habilitado**: no se aplicó
ningún plan ni se habilitó el escritor core. No se retiraron fuentes.

El intento de preparar el plan bajo freeze se canceló antes de persistirlo:
estaba resolviendo las URLs una a una y retenía los locks de los propietarios.
Se verificó ausencia de plan/recibo aplicado antes de volver a habilitar local.
La salud posterior devuelve `writes=enabled, writer=local` (04:39:55 UTC).

## Defectos comprobados

1. `prepare_plan` repetía resolución de URL, consulta escolar y lectura de imagen
   previa por cada fila. La resolución buscaba en todas las encarnaciones.
   La prueba con sólo 21 filas supera la cota de 8 sentencias antes del fix.
   En NAS se observó el plan activo durante más de cinco minutos, sin completar.
2. El lock de `users FOR UPDATE` de `school_source.lock_source` excluía
   `FOR SHARE` de la autenticación BFF. Se observaron lectores esperando ese
   lock; no era una caída de la API ni de PostgreSQL. Regresión con dos sesiones:
   lector compartido falla por lock timeout en el padre, debe pasar en el fix;
   UPDATE y DELETE del propietario deben seguir bloqueados.

## Corrección local (pendiente de ensayo/despliegue del migrador)

- Resolución por lote de URLs exactas, conservando todos los clones, ganadoras
  finales, aislamiento por URL y exclusión de ciclos. La función singular usa
  el mismo SQL con un elemento.
- Consulta escolar por lote y lectura/bloqueo de imágenes previas por tabla
  y PK exacta. No se reduce el histórico ni se omiten las deselecciones.
- Lock compartido del propietario: impide su modificación/borrado y permite
  lectores de autenticación. Los locks de routing, bindings y tablas de datos
  se conservan; no se elimina el freeze de escrituras.
- 24 tests dirigidos pasan, incluidas migración, replay, reversión, escuelas,
  clones, ciclos y límites de consultas. Dos regresiones comprobadas en rojo.

Medición en la copia NAS (0,5 CPU, 384 MiB): **2.159 filas fuente, 112 cambios,
6 sentencias, 9,627 s**. Hash completo del origen y TODAS las identidades/valores
deseados coinciden con el plan anterior, no sólo sus conteos. Se neutralizó
únicamente la marca sintética del ensayo post-corte dentro de una transacción
revertida al terminar; ningún cambio se confirmó en la copia ni en producción.
Las imágenes previas difieren legítimamente porque esa copia ya ensayó el apply.

Suite completa en curso. La medición es del plan, no del tiempo de arranque del
contenedor/reflexión del esquema, ni del apply, ni un p95 productivo.

## Drenaje del worker: límite observado

La parada cálida del worker core no terminó dentro de los 600 segundos del
recreate; Docker registró salida **137**. No se presenta como drenaje limpio.
Celery usa ACK tardío y el trabajo transaccional incompleto se revierte, pero
eso no demuestra por sí solo su redelivery. El worker nuevo está ejecutándose;
la siguiente retirada debe comprobar cola/tareas y respetar su presupuesto
máximo (soft 1.800 s, hard 2.100 s), no repetir un plazo menor a ciegas.
API y capture se arrancaron por separado durante esa espera para restaurar
servicio. Este delta del migrador no requiere volver a recrear esos servicios.

## Retomar

Primero suite/paridad y medición, luego un NUEVO intento privado con freeze,
plan, apply y readback. Mantener la autoridad local hasta ese readback. Después
activar core bajo freeze, comprobar lectura servida y liberar las escrituras.
Recuperación post-corte: hacia delante con core como autoridad; no usar el
`revert` pre-activación para descartar marcas nuevas.
