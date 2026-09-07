# Entrega de exclusiones — contrato de la corrección local

Estado: código en validación; no supone que esta versión esté desplegada.

## Autoridad y orden

El BFF conserva la autoridad de edición de las reglas. Cada edición adquiere el
lock del usuario y confirma, en una transacción, reglas y snapshot pendiente con
versión creciente. No se mantiene la transacción abierta durante HTTP.

El core aplica `PUT /v1/profiles/{pid}/exclusions` con cuerpo
`{version, exclusions}` bajo el lock del perfil y comprobación de ownership:

- Versión mayor: aplica el conjunto completo y confirma versión y reglas juntas.
- Versión idéntica y mismas reglas normalizadas: ACK idempotente.
- Versión idéntica y otras reglas: 409.
- Versión menor: no aplica y devuelve el estado/version vigentes.

Un cambio efectivo invalida la generación observada por recuperación y publicación.
Una entrega idéntica no vuelve a borrar/insertar las reglas. El orden del escritor
habitual es perfil → generación, compatible con la publicación del matching.

## Qué significa una respuesta al usuario

201/204 en la edición del BFF significa **guardado local y entrega durable**,
no garantiza una nueva fotografía del feed en ese instante. GET de filtros
expone `sync_status`: pending, versión, versión entregada y error seguro.
La UI muestra pending incluso tras borrar la última regla y consulta cada 5 s
solo mientras queda entrega pendiente.

La entrega se intenta al editar, al arrancar y cada 30 s. Un ACK perdido no
requiere otra edición del usuario. La baja antigua no puede reaparecer por
llegada fuera de orden. La indisponibilidad al guardar el diagnóstico no
deshace el pending ya confirmado ni convierte el éxito local en un 500.

ACK de entrega no equivale a haber terminado la recuperación del feed. Las
reglas se aplican en el siguiente análisis, con su generación revalidada.
El resultado vacío válido también registra intento; los caminos sin vector,
no encontrado o descartados no se convierten en éxitos por esta corrección.

## Restore, despliegue y rollback

Tras un restore local que retroceda la versión, `core_version_ahead` requiere
reconciliar explícitamente el conjunto autoritativo antes de continuar; no se
adivina la intención ni se sobrescribe el core automáticamente.

El receptor requiere ahora `version`: core y BFF deben desplegarse de forma
coordinada. La migración del BFF siembra las reglas ya existentes; durante ese
corte no puede quedar un escritor antiguo haciendo cambios posteriores al seed
sin generar pending. Ensayar antes la secuencia completa sobre una copia fiel.

El downgrade BFF rechaza perder entregas pendientes. Un rollback de imagen
también debe respetar la compatibilidad del receptor; no basta con que Alembic
pueda bajar. Mantener imágenes anteriores y un restore verificado antes del corte.

No activar CE como parte de esta entrega: su presupuesto de inferencia necesita
benchmark real del backend elegido y su promoción conserva gates independientes.
