# Evidencias de optimización — 2026-10-10

Este directorio conserva las mediciones y comprobaciones realizadas antes de
cerrar la migración de ReactPortfolio y SwissJob a Core. Los resultados de
producción contienen sólo recuentos, tiempos, estados y versiones; no incluyen
credenciales.

## Bi-encoder

- `encoder_probe.py` ejecuta una comparación controlada con uno y dos procesos,
  usando el mismo modelo, entradas y revisión.
- `encoder-comparison-final.jsonl` contiene los vectores resumidos por hash, los
  tiempos y la memoria proporcional de cada proceso.
- `worker-before.json`, `worker-after-start.json` y
  `worker-after-observation.json` registran la memoria de los contenedores antes
  y después de limitar el worker a una sola copia del modelo.
- `worker-tasks.json` identifica las tareas asignadas al worker medido.

La comparación aislada es la medida causal del ahorro del modelo. Las muestras
del contenedor completo son observaciones operativas y dependen de la carga que
hubiera en cada instante.

## Continuidad operativa

- `core-observation-containers-start.json` fija identificadores, imágenes,
  arranques y reinicios de los cinco contenedores observados, sin copiar su
  entorno. Al cerrar la ventana se comparará con una segunda captura.
- `core-observation-48h.jsonl` registra cada media hora la versión, la salud de
  las 17 fuentes, las colas y el progreso de embeddings.
- `capture-retirement-inventory.md` separa el proceso CDC que se retirará del
  drenado de embeddings, matching y borrado de perfiles que siguen activos.

## Fuentes y autoridad

- `harvest-health.json`, `irishjobs-candidate-live.json` y
  `source-inventory-production.json` documentan la salud y el inventario de las
  fuentes de Core.
- `school-harvest-production.json` registra una vuelta completa de los
  recolectores de colegios.
- `portfolio-routing.json`, `swissjob-routing.json` y
  `authority-readiness-production.json` recogen los modos de enrutamiento, los
  permisos y la preparación para transferir la autoridad.
- `portfolio-unique-sources-production.json` demuestra qué fuentes exclusivas
  de ReactPortfolio deben conservarse durante la simplificación.
- `portfolio-match-source-composition-production.json` demuestra que el feed de
  Core no contiene esas once fuentes y cuantifica la coexistencia nativa/legacy.

## Facetas y categorías

- `facet_transfer_probe.py` reproduce la comparación de transferencia entre el
  feed completo y los agregados.
- `facet-transfer-production.json` registra los bytes transferidos por ambos
  caminos.
- `facet-timing-production.json` conserva los tiempos de la primera consulta en
  producción.
- `facet-sql-optimization-production.json` compara el SQL vigente con la
  consulta optimizada sobre los mismos datos.

## Simplificación de código

- `code-simplification-baseline.json` fija con rutas y método reproducible las
  líneas de los productores transferidos, las costuras CDC y las dos ventanas
  de empleo antes de eliminarlas o extraer su presentación compartida.
- `routing-cleanup-baseline.json` separa motores y costuras post-rollback del
  catálogo y matching locales que deben conservar las fuentes exclusivas de
  ReactPortfolio.

## Frontend y regresión

- `frontend-before-final.json` y `frontend-after-final.json` comprueban las doce
  ventanas con fixtures deterministas y miden tres arrastres de dos segundos.
- `frontend-checks.json` reúne lint, pruebas, build y E2E.
- `swissjob-frontend-checks.json` registra lint, las 57 pruebas y el build del
  cliente que consume las facetas paginadas.
- `portfolio-backend-suite.json` y `core-suite-after-facets.json` resumen las
  suites completas de los dos backends.

Los datos de arrastre anteriores a este protocolo (43,25 ms de media y 100,1 ms
de p95) no son directamente comparables con las muestras deterministas de este
directorio. La comparación válida dentro del protocolo es la desaparición de la
excepción de Leaflet, con las doce ventanas presentes y tiempos de frame
estables alrededor de 16,7 ms.

`core-observation-48h.jsonl` es un artefacto vivo y sólo se versionará cuando
termine la ventana operativa requerida para retirar `core-capture`.
