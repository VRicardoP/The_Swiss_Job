# Punto 4: planificador nativo por fuente

Estado: **local, no activado en NAS**. Este cambio no provisiona ni habilita
fuentes y no retira ningún productor legacy.

## Contrato

- Un despacho ligero cada seis horas (`00:10`, `06:10`, `12:10`, `18:10`, zona
  Europe/Zurich del Celery existente), conservando la frecuencia de providers R5.
- Una tarea independiente por scope habilitado. El límite de 1.800/2.100 s se
  aplica a esa tarea, no al conjunto de fuentes: una lenta no impide que las
  siguientes lleguen a ejecutarse. El worker/broker existentes son reutilizados.
- La clave de operación incluye ventana UTC de seis horas y scope; un retry
  del despacho conserva la ventana original. Un fallo del broker puede repetir
  mensajes, pero una operación terminada no repite la descarga pública.
- Los mensajes de fuente expiran a las seis horas para no acumular barridos
  antiguos indefinidamente. Error de despacho: dos retries acotados; error de
  fuente: el retry ya existente, no un bucle nuevo de consultas al portal.
- La exclusión global de `5425202` serializa manual/programado incluso con
  ventanas distintas. El worker vuelve a comprobar habilitación antes de HTTP y
  persistencia. Error de configuración no se convierte en cosecha vacía.
- No se omiten silenciosamente scopes habilitados con provider desconocido:
  generan el error explícito de configuración que vigila la salud de cosecha.
- Despacho → `core.default`; fetch → `core.harvest`, verificado contra el router
  real de Celery (no sólo contra el diccionario de configuración).

## Pruebas

Cinco regresiones iniciales fallaron antes de implementar el dispatcher y el
run_key de la tarea individual (`/tmp/point4-native-dispatch-red.log`).
**59 pruebas dirigidas verdes**; tras endurecer el caso de cambio de día y la
comprobación de colas, **6/6 específicas verdes**, incluida integración con PG:
misma ventana descarga una vez; ventana siguiente vuelve a descargar; scope
deshabilitado no se selecciona. No sumar los contadores solapados.

La prueba de medianoche usa un reloj con dos instantes (23:59 y 00:01) y un fallo
del broker después del primer envío. Debe consultar el reloj sólo una vez y
conservar todas las claves originales en el reintento.

Suite completa posterior al cambio: pendiente. La suite de exclusión anterior
fue 1.516 passed. No presentar ésta como verificación del nuevo beat.

## Corte

Desplegar sólo después de la suite; mantener todos los scopes nativos apagados.
Comprobar el dispatcher vacío sin descargas. Después, fuente por fuente, drenar
los productores anteriores y provisionar el scope con sus parámetros reales,
ventana de admisión y prueba de identidad/histórico. Un `dispatched` no acredita
éxito: comprobar `source_scope_state`, revisiones y feed servido.

La retirada definitiva del proyector/CDC requiere conservar su actual función de
recuperación de embeddings/matching en una tarea nativa. Este dispatcher sólo
sustituye el despacho de cosecha, no declara resuelta esa dependencia.
