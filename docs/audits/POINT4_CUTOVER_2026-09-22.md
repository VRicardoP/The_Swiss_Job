# Punto 4 — las 16 fuentes transferidas al productor nativo

Fecha: 2026-09-22, 00:30 UTC. Trabajo ejecutado el 21-09 21:38 → 22-09 00:30 UTC.

**Qué acredita este acta:** las 16 fuentes que alimentaban el corpus del core
están cosechadas por productores nativos, verificadas en el servicio servido.
**Qué NO acredita:** el punto 4 cerrado. Quedan la primera entrega natural de
avisos, la retirada del slot de captura y la aceptación final (§6).

## 1. Versiones desplegadas

| Servicio | Imagen | Estado |
|---|---|---|
| core-api / core-worker / core-capture | `swissjob-core:point4-51be757` | `ready`, core0050, `authoritative: true`, 0 reinicios |
| backend / worker público | `swissjob-worker:point4-a0fb403` | sin cambios de código; sólo listas de retirada |
| portfolio_backend | `portfolio-backend:e15-9f8c85a` | intacto |

Commits de esta jornada: `20b92b9` (portado + paridad de acceso + canton),
`1e32dc7` y `f8e2fff` (ritmo de irishjobs), `51be757` (slug de jobgether).
Suite core sobre `20b92b9`: **1732 passed, 1025,59 s**. Sin push.

## 2. Fuentes transferidas

Los 17 scopes habilitados, todos con `last_complete_at` del día y **0 fallos
consecutivos**:

| Fuente | Presupuesto | Ventana | Última cosecha completa |
|---|---|---|---|
| arbeitnow, euremotejobs, globaljobs, jobspresso, ostjob, publicjobs, remotive, thehub, weworkremotely, zebis, zentraljob, workingnomads | — | 7 d | 21-09 22:10–22:49 |
| financejobs | 10 pág. | 7 d | 21-09 22:43 |
| irishjobs | 3 pág./host | 7 d | 21-09 23:55 |
| nav_arbeidsplassen (2 scopes, una faceta cada uno) | 3 pág. | 7 d | 22-09 00:05 y 00:06 |
| jobgether | 3 pág. | 7 d | 22-09 00:14 |

- **Replay con la misma clave: 16/16 `skipped`**, cero descargas repetidas.
- **Canario servido**: 45 vacantes nativas primarias consultadas por
  `GET /api/v1/jobs/{id}` en el BFF desplegado, puerto `CoreCatalog`, 0 fallos.
  Sólo lectura: no se crearon guardados, candidaturas ni avisos.
- Seis de esas 45 sirven la URL del portal en vez del `apply_url`: es correcto
  y deliberado de CH Media, que conserva los dos enlaces (la oferta en
  ostjob/zentraljob y el ATS del empleador). El usuario debe ver la del portal.
- Estado final: **45.564 vacantes vivas**, CDC pendiente 0, outbox sin entregar 0.

## 3. Guardas de los productores que se retiran

Verificadas **dentro** de cada contenedor, con tripwire sobre la clase y sin
tráfico de red (`fetches: 0` en los tres):

| Contenedor | Retirados | Siguen construyéndose |
|---|---|---|
| swissjob-backend | 12 providers + 2 scrapers | 5 providers, 13 scrapers |
| swissjob-worker | 12 providers + 2 scrapers | 5 providers, 13 scrapers |
| swissjob-worker-r5 | 15 providers | 2 providers (proz y remoteco, sin cosecha desde agosto) |

`zebis` y `publicjobs` **se conservan a propósito en el público** (decisión D2):
la alerta de profesor de primaria lee `jobs.category = 'H'` de esa base y esas
dos fuentes son las que la alimentan (76 y 4 ofertas vivas de categoría H, con
altas del 21-09). Sin ellas la alerta se apagaría en silencio.

## 4. Cuatro defectos que el corte destapó, todos nuestros

Ninguno era del portal. En los cuatro casos la diferencia estaba entre lo que
pide el productor nativo y lo que pide el que se retira.

1. **NAV devolvía 429** porque el adaptador pedía 100 páginas donde su
   productor pide 3. Sondeo tras el ajuste: 83 + 300 ofertas, ambas facetas
   completas, ni un 429.
2. **Jobgether devolvía 403** porque no enviaba las cabeceras de navegador que
   su productor documenta como obligatorias. Tras el ajuste: 136 ofertas.
3. **irishjobs daba ReadTimeout** tras 8 páginas. El portal no es lento (1,3 s
   por página aislada) pero se ralentiza progresivamente: 8 seguidas tardan
   105 s y la novena pasa de 40 s. El scraper que se retira nunca lo nota
   porque su cursor incremental para antes; el nativo siempre empieza en la
   página 1. Presupuesto declarado de 3 páginas por host, pausa entre hosts y
   margen de lectura: **150 ofertas de ambos hosts en 13,3 s, completo**.
4. **jobgether descartaba 6 de cada 150 ofertas** como inválidas. Todas tenían
   slug y título correctos: el patrón del slug no admitía el punto de
   `next.js`, `.net` o `psy.d`. Era un 4% de cobertura perdido por el traspaso.
   Tras el fix: 142 admitidas, barrido completo.

Además, **paridad pública** sobre una descarga real por portal:
`docs/audits/NATIVE_IRISHJOBS_PARITY_2026-09-21.json` — 25/25, cero diferencias
en 7 campos; `NATIVE_FINANCEJOBS_PARITY_2026-09-21.json` — 10/10, sin
diferencias semánticas (el legacy guarda descripción vacía y el core NULL; su
sink representa una como la otra).

## 5. Una decisión aplicada, medida y revertida

El plan cerraba las encarnaciones de `legacy:<fuente>` en la transacción del
corte, para que el archive-sweep las retirase en 3 días (`CORE_ARCHIVE_GRACE_DAYS`)
en vez de 120 (`CORE_CORPUS_STALE_DAYS`), evitando servir la misma plaza dos
veces. Se aplicó al corte 1: **28.767 cerradas**, excluida la única vacante con
candidatura.

El primer barrido nativo demostró que el razonamiento estaba incompleto: con
`admission_window_days = 7` el productor nativo **no readmite** las ofertas que
el portal sigue listando pero publicó antes, porque su identidad es nueva y la
ventana rechaza altas antiguas. Cerrar restaba cobertura viva en lugar de sólo
evitar duplicados — arbeitnow 19.523 cerradas frente a 2.780 nativas, irishjobs
2.334 frente a 25.

Se revirtieron **las 28.767 por id exacto** desde el propio recibo del operador,
dentro de la ventana de gracia y antes del barrido de las 03:35 UTC
(`corte1.reopened.json`: closed 28.767, found 28.767, reopened 28.767).
Vacantes vivas después: 45.443, frente a 40.406 antes del corte. El corte 2 ya
no cierra nada y el operador lleva el motivo escrito en el código.

Las dos identidades conviven hasta que `archive.py` retire las rancias, que es
lo que este proyecto ya aceptó para WorkingNomads. NAV y Jobgether, con el 100%
de sus URLs en su propio host, enlazaron directamente con las vacantes
existentes: sólo 16 y 22 vacantes primarias nuevas; el resto refresca el
histórico desde el productor nativo.

## 6. Recuperación acreditada

Ida y vuelta completa sobre `jobspresso` (`p14-recuperacion.log`):

1. Scope nativo deshabilitado primero — nunca dos escritores a la vez.
2. Fuente retirada de la lista del público y worker recreado.
3. **El productor legacy vuelve a construirse y cosecha 16 ofertas reales.**
4. Devuelta a la lista: `get_provider('jobspresso') is None` otra vez.
5. Scope nativo rehabilitado: 17 habilitados.
6. **Las 15 ofertas del corpus, intactas** tras la ida y vuelta.

## 7. Procesos retirados

| Proceso | Estado | Motivo |
|---|---|---|
| `swissjob-harvest-trigger-r5` | parado | disparaba `tasks.fetch_providers` cada 6 h; sus 15 fuentes ya son nativas |
| `swissjob-frontend-r5` | parado | su backend llevaba 2 días `Exited (255)` |
| `swissjob-worker-r5` | parado, **exit 0, sin OOM**, CDC a 0 | sin disparador y sin fuentes propias |
| `swissjob-f-rehearsal-20260919` + su directorio | eliminados | retención de 48 h vencida; 2,6 GB liberados |

Se conservan con responsabilidad explícita: `core-api`, `core-worker` (con
`shadow.project`, que **es** el postprocesado nativo, no una tarea de sombra),
`core-capture` (hasta retirar el slot), `swissjob-backend` y `swissjob-worker`
(BFF, CV, documentos, alerta de profesor y los 8 scrapers escolares),
`swissjob-erasure-cdc` (borrado de perfiles, con 1 solicitud registrada).

## 7bis. Ciclo autónomo acreditado

A las **04:10 UTC** (06:10 Europe/Zurich), con el worker R5 y su disparador ya
parados, el beat del core despachó por sí solo `jobhunt.harvest.dispatch_native`
en 0,92 s con **17 scopes despachados**, y los 17 completaron su barrido entre
las 04:10 y las 04:17 UTC **sin un solo fallo consecutivo**. Las cifras
crecieron en esa ronda (irishjobs 108 → 166 listings, ostjob 1.188 → 1.224,
globaljobs 207 → 215, jobgether 136 → 142; vacantes vivas 45.564 → 45.613).

No es una invocación manual: es la cadencia programada haciendo el trabajo que
hasta ayer hacía el productor legacy. El postprocesado acompañó: 2.378
embeddings y 159 evaluaciones en las tres horas siguientes al corte.

## 8. Lo que queda abierto

1. **Primera entrega natural de avisos (R1).** Las 10 búsquedas ejecutan en
   core; el primer vencimiento estimado es hoy ~07:10 UTC. Comprobar entonces
   `saved_search_execution.run_number`, un evento `saved_search.matches` en
   `integration_outbox` con su entrega, y la notificación correspondiente en el
   BFF. Un resultado vacío correcto no es un fallo: se registra como «sin
   evento real disponible» y se repite al día siguiente.
2. **Retirada del slot `jobhunt_shadow_r5_rehearsal`.** Requiere 48 h de
   dispatcher nativo sin incidencias. Es la única acción sin vuelta atrás
   barata: reanudar el legacy después exigiría un snapshot CDC nuevo. Orden:
   parar `core-capture`, quitar del beat `shadow.check_slot_health`,
   `preview_cycle`, `run_cycle` y `purge_staging` — **nunca `shadow.project`** —
   y sólo entonces `pg_drop_replication_slot`.
3. **Fuentes excluidas del alcance, con causa medida** (decisión D1):
   `myscience` 15 ofertas/7 d, `gastrojob` 7, `tes` 4 — bajo volumen;
   `stelle_admin` sin altas desde el 27-08 y `schuljobs` desde el 25-08 —
   inactivas. No se portan; su histórico se conserva.
4. **Cota aceptada**: no se repara el `canton` del histórico legacy (ostjob
   8/2.309 canónicas lo tienen frente a 2.424/2.966 en la tabla legacy). Serían
   ~4.500 canónicas por un campo que hoy consume una sola búsqueda semanal. Lo
   nuevo sí lo lleva: `canton` se expone en los cuatro normalizadores suizos.

## 9. Evidencia

Recibos privados en el NAS, `unification-e15-20260914/point4-close-20260921/`:
`corte1.{guards,prepared,activated,run,replay,inspect,reopened}.json`,
`corte2.*`, `p1-copia-retirada.log`, `p3-r6.log`, `p5-decision-identidad.md`,
`p6-nav-jobgether.md`, `p8-p9-cortes.md`, `p13-retirada.log`,
`p14-recuperacion.log`, los tres `*.guard.json` y los composes `.before` /
`.candidate` de cada maniobra. No se exportaron datos personales al ordenador.

`R6` quedó acreditado antes de estos cortes y sigue siéndolo: el 21-09 a las
16:59:03 UTC, `jobhunt.shadow.project` corrió 708 s con `batches: 0` y
`recovery_evaluated: 3` — la cadena canónica → embedding → evaluación → feed
**sin un solo lote de captura CDC**.
