# E.15 — unificación de colegios: punto 3

Fecha: 14-09-2026, Europe/Madrid. Alcance: colegios, no retirada general de
productores ni campaña de calidad. No se ha hecho push.

## Estado operativo

SwissJob y ReactPortfolio sirven colegios con `schools=core_primary`; las
escrituras están abiertas y el estado escolar tiene autoridad única en core.
Productores existentes conservados y adaptados, sin añadir un crawler o scheduler.
**PUNTO 3 CERRADO Y DESPLEGADO.** CLI de recuperación verificado contra los
sellos reales, retención registrada y catálogo comprobado por el proxy público.

| Componente | Release / imagen |
|---|---|
| Core API, worker y captura | `1b8d910`, `swissjob-core:e15-1b8d910`, esquema `core0047` |
| SwissJob BFF | `4e40ffe`, `swissjob-backend:e15-4e40ffe` |
| SwissJob worker | `4e40ffe`, `swissjob-worker:e15-4e40ffe` |
| Portfolio | `9f8c85a`, `portfolio-backend:e15-9f8c85a` |
| CLI de recuperación | `daf2fad`, `swissjob-core-cli:e15-daf2fad`; ambos compose de migración actualizados |

Imágenes construidas desde commits, sobre las imágenes efectivas anteriores,
sin descargar dependencias ni sobrescribir `:prod`. No hay montajes de código.
Se conservaron redes, puertos, volúmenes, usuario, comandos, logging, límites y
healthchecks. Seis procesos activos, cero reinicios; los healthchecks de API y
captura del core y del BFF SwissJob están saludables. Los demás no tenían healthcheck:
se verificaron además HTTP real, autoridad del productor y ping de Celery.

## Datos y contrato

| Consumer | Monitores | Ofertas históricas | Estados/candidaturas | Preferencias |
|---|---:|---:|---:|---:|
| Portfolio | 6 | 92 | 0 | 0 |
| SwissJob | 18 | 53 | 54 | 2 |

Cada importación se repitió: cero inserciones adicionales y huellas materiales
idénticas. UUIDs, borradores, estados, preferencias, configuración, contactos y
marcas de entrega preservados. No había borradores no vacíos en esta historia;
su conservación y edición se probaron en el ensayo aislado y las regresiones.

35 ofertas SwissJob enlazadas: sus 35 GET del corpus devuelven 200. Las otras
18 y las 92 de Portfolio conservan cuarentena explícita (sin URL, página de
listado, fuente inactiva o espera de observación del corpus). No son ofertas
perdidas: ambos BFF conservan su presentación histórica. No se han fabricado
vacantes ni URLs para hacer verde el inventario. Sólo una observación recién
extraída puede publicar una vacante ausente; la historia sólo enlaza.

Identidad escolar compartida; configuración por consumer, preferencias y
candidaturas por perfil. Cruce de monitores entre ambos consumers: 404 en las
dos direcciones. Scopes `schools:read/write` añadidos sólo a las dos credenciales
efectivas preexistentes. No se crearon cuentas ni se cambiaron roles.

## Ensayo y pruebas

- Tres dumps privados restaurados con errores fatales, sin suprimirlos. Core:
  315 definiciones idénticas. Portfolio/SwissJob: 77/93; una representación de
  cast de ARRAY diferente en cada CHECK de routing, con equivalencia comprobada
  para los cinco modos, valores inválidos y NULL. PK/FK presentes y validadas.
- Importación histórica: cero vacantes creadas. Replay idempotente y vuelta por
  contenido íntegro; repetición después de altas/ediciones de candidaturas,
  borradores y preferencias en las copias, sin tocar el corpus compartido.
- SwissJob completo: **2396 passed, 4 xfailed**; Portfolio: **1991 passed,
  1 skipped**. Suites en serie.
- Core completo: **1205 passed**. Después: **40 escolares** sobre el arreglo
  del sello y **14** sobre el CLI final. No presentar estas últimas como una
  segunda suite completa del commit final. Avisos preexistentes no ocultados.
- Canary HTTP congelado: lecturas vivas y mutaciones 503 en ambos BFF.
- Canary HTTP abierto: catálogo, preferencias, estados, borrador ausente 404,
  calendario y PATCH de monitor. Escrituras sin cambio de valor tramitadas por
  core; hashes del estado escolar local antiguo permanecen iguales.
- Worker NAS: ocho productores consumen los 18 monitores del core; reconciliación
  histórica por la API real y Celery `pong`. No se simuló una extracción nueva.
- Portfolio: catálogo/ofertas core y camino de fallo SMTP con transporte SMTP/SSE
  interceptado. Fallo conserva pendiente, sin ack ni correo real enviado.
- Referencia anterior/posterior: routing ajeno a colegios y políticas de matching
  idénticos. No se tocó la racha, el holdout, los modelos ni los otros servicios.

Muestra secuencial de diez GET por ruta, todos 200 (no ensayo de carga global):

| Ruta BFF | p95 |
|---|---:|
| SwissJob `/api/v1/watchlist/schools` | 269,2 ms |
| SwissJob `/api/v1/profile` | 268,3 ms |
| Portfolio `/api/v1/schools/` | 194,7 ms |
| Portfolio `/api/v1/schools/jobs/all?limit=500` | 1108,3 ms |

## Correcciones que evitaron un cierre falso

1. El CDC lee R5, no la base del productor SwissJob público. Las observaciones
   nuevas se entregan explícitamente al core antes de confirmar el cursor local;
   un fallo revierte ese cursor. El feed resuelve su hash accionable sin inventar
   un listing CDC. Regresiones incluidas en la suite SwissJob.
2. Sello: `role_score` default flotante y fecha de contexto normalizada a UTC/ISO.
   La reproducción de fecha fallaba en `4e40ffe` y pasa en `1b8d910`, también con
   offset distinto de UTC. Un lote guardado puede derivarse de nuevo sin deriva.
3. Los healthchecks se restituyeron al detectar su omisión en la reconstrucción
   operativa. La verificación final compara los controles efectivos, no etiquetas.
4. El primer intento de sellado se detuvo antes de escribir porque Portfolio
   aún no escuchaba. Se esperó readiness y se repitió; no se contó como éxito.
5. El CLI de vuelta distingue avance legítimo del catálogo público SwissJob de
   una escritura local indebida de estado escolar. Importación sigue exigiendo
   huella completa; rollback exige intactos los durables anteriores, permite
   respaldos nuevos vacíos y bloquea borradores/estados/preferencias alterados.
   Imagen final probada en el NAS contra los dos lotes sellados reales, sin red,
   en solo lectura: sellos válidos, crecimiento de catálogo permitido únicamente
   en reverse, deriva de durables rechazada y cero escrituras de producción.
6. Nginx conservaba la IP anterior del BFF después de recrearlo: ruta pública
   404 aunque el BFF directo estaba sano. Recarga nativa de Nginx, sin cambiar
   imagen ni configuración; canary autenticado posterior por el frontend:
   **HTTP 200, 18 monitores**. Incorporada al paso operativo de reapertura.

## Recuperación y límites explícitos

Directorio operativo NAS privado (0700):
`/share/CACHEDEV1_DATA/Public/unification-e15-20260914`.
`core.configured.yml`, `swissjob.configured.yml` y `portfolio.configured.yml`
son las configuraciones persistentes.
Las variantes `*.frozen.yml` congelan los escritores; `*.before.yml` fijan las
imágenes efectivas anteriores, no un tag mutable. Configs no son dumps caducables.

Antes del flip basta mantener la autoridad local y recuperar la configuración
anterior. **Después del flip no arrancar la imagen antigua directamente:**

1. Congelar/drenar ambos BFF y el worker escolar; volver a emitir el token corto
   del administrador Portfolio existente para su sonda, sin cambiar roles.
2. Mantener `schools=core_primary` o `rollback_pending` durante la vuelta. Con
   el CLI final, ejecutar por consumer `reverse --bundle
   /evidence/<consumer>.school-bundle.private.json --import-report
   /evidence/<consumer>.import.private.json --report /evidence/<consumer>.reverse.json`.
   Usar su compose privado de migración y credenciales de entorno; no en argv.
3. El comando verifica sello/binding/freeze, bloquea el snapshot del core,
   revierte el estado actual a las columnas locales y comprueba la lectura
   material. Abortará si faltan respaldos o aparecen durables locales inesperados;
   no convertir ese aborto en un restore global ni ignorarlo.
4. Sólo con recibo verificado cambiar el comodín de colegios a local usando
   `set_routing`, confirmar autoridad y reabrir. Ensayar de nuevo si el estado
   o los bindings quedan fuera del lote sellado.
5. Tras recrear el BFF, recargar Nginx con `docker exec swissjob-frontend nginx
   -s reload` y repetir el canary por `/api/v1/watchlist/schools` a través del
   frontend público; una sonda directa al BFF no prueba el proxy.

Nunca borrar vacantes compartidas como rollback ni hacer downgrade con datos
escolares presentes. Borrado de monitor con historia: 409; desactivación disponible.
El país forma parte de su identidad y no se reasigna.

## Custodia y retirada del ensayo

Retirados los dos clústeres locales desechables con sus volúmenes y los tres
archivos dump, después del ensayo verificado. Las bases originales del NAS no
se tocaron. Las evidencias locales restantes están en directorio privado 0700,
archivos 0600, registro de 126 entradas (incluye las retiradas), máximo 48 horas
sin extender fechas originales; cero dumps presentes y cero vencidos pendientes.
En el NAS, nueve lotes/recibos/bindings de recuperación quedan registrados por
siete días en `unification-e15-20260914/retention.json`; preview: cero vencidos.
Las configuraciones de ejecución no son copias caducables.

El cron sigue expresamente diferido por el propietario al cierre final del
proyecto: hasta entonces la retención exige operación manual. Incluir también
este registro E.15 en esa operación y en la futura programación; el registro de
E.13 no cubre automáticamente los artefactos nuevos. No afirmar automatización.

## Alcance del cierre

La retirada general de productores y el rendimiento global son puntos separados; aquí no se certifican.
