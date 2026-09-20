# Punto 4 — despliegue de entrega de perfiles

20-09-2026, checkpoint 04:42 Europe/Madrid. **Entrega de perfiles y recuperación
del feed confirmadas. Este componente está cerrado; punto 4 NO cerrado.**
Código BFF `a93dcbc`, core `bac5e78`; ensayo e imágenes:
[PROFILE_DELIVERY_NAS_2026-09-20.md](PROFILE_DELIVERY_NAS_2026-09-20.md).

## Despliegue confirmado

- Fuente productiva `swissjobhunter`: `c57f2341b012`; core:
  `core0049`, migraciones terminadas con salida 0.
- Todos los escritores core (API, worker y capture) sustituidos antes de habilitar
  entrega. Paradas cálidas, sin purga de colas; `up -d --no-deps`, nunca
  `--remove-orphans`. Worker/capture previos terminaron sin OOM ni SIGKILL.
- Core: `swissjob-core:point4-bac5e78`,
  `sha256:d821671f836e7cd0e71a6eb0334924d373120594f2052e84429f7f758953925b`.
  Readiness: ready/core0049/release bac5e78/authoritative=true; Celery pong.
- BFF: `swissjob-backend:point4-bac5e78`,
  `sha256:a6b36ae595d2eb7a03c17199182726a48c37abf23f29b1cd82a0b24e78baed48`.
  Salud HTTP 200. Los cinco contenedores inspeccionados: running, cero reinicios.
- Worker público conserva `swissjob-worker:point4-d89b6ee`; no se retiró ningún
  productor de ofertas/colegios. Portfolio y etiquetas `:prod` sin modificar.
- BFF comprobado primero con flag OFF. A las 04:24 se cambió exclusivamente
  `CORE_PROFILE_SYNC_ENABLED=false→true` y se recreó sólo el backend.
  `CORE_FEEDBACK_ENABLED=false` confirmado después: no hay corte de feedback.

## Canary real sin editar datos personales

Entrega natural por el proceso de fondo, no por una edición sintética del CV:
**2/2 snapshots confirmados**, version=delivered_version, cero last_error.
Comprobación de sólo lectura dentro del NAS: versión, hash del payload completo,
contenido normalizado vigente, actividad y ownership coinciden en ambos perfiles.
El perfil del consumer portfolio conserva projection_version=0.

Durante la recuperación se observaron temporalmente 1.800 punteros a la revisión
anterior en cada perfil. El ciclo normal del proyector los sustituyó: **ambos
perfiles tienen un vector vigente, 1.800 punteros y CERO punteros a la revisión
anterior**. Intentos registrados: 1 y 2 al muestrear (procesamiento en curso).
No se forzó una evaluación ni se copiaron vectores manualmente en producción.

Canary posterior por la costura del BFF desplegado: ambos usuarios resuelven
CoreMatching puro, devuelven 20 ofertas, totales 1.295 y 1.314; status de entrega
sin pendientes ni error. Se invocó el servicio real con sus credenciales HTTP core,
en transacción fuente de sólo lectura. No es una prueba de navegador ni un p95;
se conservan para punto 5 las mediciones del recorrido completo del feed.
No se tocaron políticas, holdout, feedback, candidaturas ni correos para probarlo.

Pruebas del código desplegado, siempre en serie: **core 1.499 passed**;
**BFF 2.459 passed, 3 skipped, 4 xfailed**. Reproducciones, hashes de logs y
ensayos de recuperación están en el acta enlazada al inicio.

## Configuración y backups privados (sólo NAS)

Directorio `unification-e15-20260914/profile-preactivation.1jTCVK`, modo 0700.
Configuraciones/env contienen secretos: no versionarlos ni copiarlos al ordenador.
Se conserva la configuración anterior y la nueva OFF para recuperación compatible.
Sólo se retiró de core el override RELEASE_SHA viejo: manda la identidad horneada.

Hashes corrientes:
- Core: `9ec97c58bcb2d08c1bcf92359b70fd0ecef755e0ae8e062a1244265d98cfbfcb`.
- SwissJob ON: `c19a5c4e5f638158631a35e9669d5f7ad3c21323f09820cacfc175748bbbe25f`.
- SwissJob nueva imagen OFF:
  `4c408c8937475d0acd1517c8d69de9b0bec30600330a6a751f7baf36e58c6330`.

Backups terminados con salida 0, modo 0600 y catálogos legibles:
- Fuente completa: 291.131.577 bytes;
  SHA256 `3d49030b5866a105ff198f2a8c82fdab28947012c900ba891c763e7217370149`.
- Esquema jobhunt: 500.248.871 bytes;
  SHA256 `71aa13ca7a9e1d28e8a210be6de377ed4ba40c208519a122ccaf8596de79b7a0`.

La legibilidad de estos dumps NO equivale a un nuevo restore de esos mismos archivos.
Restore estricto, migración, recuperación hacia delante y reconstrucción de feed se
ensayaron sobre copias privadas previas. El dump jobhunt requiere extensiones/roles
según runbook. No restaurar encima del corpus vivo como prueba.

## Recuperación y siguientes cortes

La autoridad de contenido/actividad de los dos perfiles ya está transferida.
**No revertir a E.15 ni retroceder los contadores**: conservar core0049, outbox,
versiones y una imagen compatible con el fencing. Pausar entrega no deshace autoridad;
al reanudar se entrega la última versión. La recuperación hacia delante se ensayó
con vaciado y restauración del contenido mediante una versión NUEVA.

Frescura del feed vivo confirmada. Siguen pendientes feedback post-corte,
búsquedas/avisos, extracción escolar, scheduler nativo y traspasos por fuente.
Este despliegue no autoriza apagar CDC de ofertas ni iniciar el punto 5.
