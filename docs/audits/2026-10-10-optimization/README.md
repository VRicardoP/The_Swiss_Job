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
- `container-resource-baseline-production.json` fija tres muestras homogéneas
  de CPU, memoria y PIDs para los nueve contenedores afectados; se repetirá con
  el mismo protocolo después del corte.
- `scripts/measure_nas_resources.py` versiona ese protocolo, valida el conjunto
  exacto de contenedores y falla si el modo solicitado contradice la presencia
  real de capture. `container-resource-scheduled-window-baseline-production.json`
  mantiene aparte la carga real del informe diario Portfolio para compararla
  únicamente con la misma ventana tras el traspaso.
- `portfolio-daily-report-baseline.jsonl` conserva el seguimiento por minuto
  hasta el evento terminal agregado: 26 muestras válidas, una sonda fallida
  cerrada y envío final de 8 empleos. Los eventos agregados delimitan 3.667,17 s
  de ejecución; las muestras de recursos cubren sus últimos 1.548,94 s. No
  contiene ofertas, URLs ni datos de usuarios.
- `matching-recovery-baseline-production.json` cuantifica el trabajo con un
  solo proceso: duraciones, ticks caducados, límites y el gate de convergencia
  después de drenar el backlog de embeddings.

La comparación aislada es la medida causal del ahorro del modelo. Las muestras
del contenedor completo son observaciones operativas y dependen de la carga que
hubiera en cada instante.

## Continuidad operativa

- `core-observation-containers-start.json` fija identificadores, imágenes,
  arranques y reinicios de los cinco contenedores observados, sin copiar su
  entorno. Al cerrar la ventana se comparará con una segunda captura.
- `core-observation-48h.jsonl` registra cada media hora la versión, la salud de
  las 17 fuentes, las colas y el progreso de embeddings.
- `native-harvest-round-production.json` conserva una ronda nativa completa:
  17 scopes distintos, 17 cursores confirmados y cero errores de tarea.
- `capture-retirement-inventory.md` separa el proceso CDC que se retirará del
  drenado de embeddings, matching y borrado de perfiles que siguen activos.
- `capture-retirement-readiness-production.json` conserva el checkpoint vivo
  previo al corte: staging drenado, slot activo y WAL retenido acotado.
- `supervisor-production.json` registra la reparación de la deriva del
  supervisor QTS: vuelve a vigilar los dos workers Core divididos y conserva
  capture hasta el corte.
- `supervisor-postcapture-preflight.json` demuestra que la variante final
  elimina sólo capture y conserva ambos workers; no se promueve antes de la
  ronda nativa posterior al stop.
- `cutover-file-preflight-production.json` fija respaldos, rutas y huellas de
  los tres ejecutables que deben recalcularse justo antes del corte.

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
- `swissjob-facets-deployment-production.json` demuestra que el bundle servido
  contiene la ruta de facetas y que producción atendió tanto facetas como
  páginas por categoría con HTTP 200, conservando sólo recuentos agregados.

## Simplificación de código

- `code-simplification-baseline.json` fija con rutas y método reproducible las
  líneas de los productores transferidos, las costuras CDC y las dos ventanas
  de empleo antes de eliminarlas o extraer su presentación compartida.
- `routing-cleanup-baseline.json` separa motores y costuras post-rollback del
  catálogo y matching locales que deben conservar las fuentes exclusivas de
  ReactPortfolio.
- `delivery-transport-production.json` verifica que los dos consumidores usan
  destinos HTTP explícitos, con 25.405 entregas y cero pendientes o dead-letter;
  el fallback ejecutable al inbox sombra puede retirarse después del cierre.
- `profile-authority-production.json` demuestra que todos los perfiles
  SwissJob ya reciben snapshots nativos; la recuperación extraída podrá usar
  `projection_active` sin consultar el staging CDC.

## Frontend y regresión

- `frontend-before-final.json` y `frontend-after-final.json` comprueban las doce
  ventanas con fixtures deterministas y miden tres arrastres de dos segundos.
- `frontend-measurement-protocol.json` fija el medidor reproducible ya
  versionado en ReactPortfolio frontend (`1bed6c2`). Su smoke headless cargó las
  doce ventanas con contenido, cero excepciones y tres arrastres; la comparación
  final deberá repetir exactamente el mismo modo y entorno en ambas muestras.
- `frontend-checks.json` reúne lint, pruebas, build y E2E.
- `swissjob-frontend-checks.json` registra lint, las 57 pruebas y el build del
  cliente que consume las facetas paginadas.
- `portfolio-backend-suite.json` y `core-suite-after-facets.json` resumen las
  suites completas de los dos backends.
- `portfolio-composite-checks.json` fija los commits, la imagen y las pruebas de
  la unión Core + once fuentes exclusivas sin reactivar productores transferidos.
- `portfolio-deploy-candidate.json` fija el worktree limpio, la imagen final y
  los guardas que aplicarán las nueve fuentes transferidas sin tocar secretos.
- `premature-worker-config-rollback-production.json` registra el despliegue
  `--config-only` adelantado por un tercer argumento ignorado, su reversión
  byte a byte, la salud posterior y el control que impide repetirlo.
- `reactportfolio-frontend-release.json` fija el commit publicable y repite lint,
  las 422 pruebas y el build aislado que se desplegará en Cloudflare Pages.
- `worker-release-candidate.json` conserva la construcción validada del worker
  `f29fd44`; `worker-deployment-decision.json` demuestra después que no debe
  desplegarse: la entrega de perfiles corre en el BFF y el worker usa
  `--config-only` para aplicar únicamente la política final 14+2.
- `post-capture-core-deploy-validation.json` demuestra en un ensayo local que el
  despliegue Core completo conserva la topología final de cuatro servicios y no
  reintroduce captura ni las claves del slot retirado.
- `release-capacity-preflight.json` fija el espacio libre local y del NAS frente
  al tamaño de las tres imágenes candidatas; debe repetirse justo antes de enviarlas.
- `cutover-file-preflight-production.json` confirma respaldos y ausencia de
  candidatos `.next` ambiguos en el NAS antes de preparar la retirada de capture.
- `portfolio-routing-flip-rehearsal.json` conserva el bloque exacto del flip
  final y demuestra en PostgreSQL 15 que reanuda estados parciales sin doble cambio.
- `frontend-publication-preflight.json` separa los pushes del despliegue externo
  de Cloudflare Pages y fija cómo demostrar que el dominio realmente cambió.
- `deployment-command-provenance.json` fija el checkout y la huella del
  comando que ejecuta cada despliegue posterior a capture.
- `formal-closure-probes-validation.json` documenta las tres sondas fail-closed
  de Core, SwissJob y Portfolio y las autorizaciones externas aún necesarias
  para ejecutarlas en producción.

Los datos de arrastre anteriores a este protocolo (43,25 ms de media y 100,1 ms
de p95) no son directamente comparables con las muestras deterministas de este
directorio. La comparación válida dentro del protocolo es la desaparición de la
excepción de Leaflet, con las doce ventanas presentes y tiempos de frame
estables alrededor de 16,7 ms.

`core-observation-48h.jsonl` es un artefacto vivo y sólo se versionará cuando
termine la ventana operativa requerida para retirar `core-capture`.
