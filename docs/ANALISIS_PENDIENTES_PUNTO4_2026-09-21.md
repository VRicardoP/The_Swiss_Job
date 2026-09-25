# Análisis crítico del informe de pendientes del punto 4 — 2026-09-21

Dictamen sobre `docs/PENDIENTES_PUNTO4_REVISION_EXTERNA_2026-09-21.md` con el
formato que ese informe pide en su §11: por cada R1–R7 un veredicto
(**NECESARIO / YA RESUELTO / SIMPLIFICABLE / FUERA DE ALCANCE / EVIDENCIA
INSUFICIENTE**), la evidencia (fichero:línea o sonda), la solución mínima y el
criterio de cierre; al final, lista finita de trabajo, camino crítico,
estimación desglosada y decisiones que sólo puede tomar el propietario.

**Nada de lo que sigue se ha ejecutado.** Todas las sondas al NAS fueron de
sólo lectura (`SELECT`, `docker inspect`, `docker logs`). No se modificó
configuración, no se activó ni retiró ninguna fuente, no se hizo push.

---

## 0. Veredicto en una página

1. **El informe es honesto y bien orientado, pero reparte mal el trabajo.** De
   sus siete pendientes, dos ya están resueltos por evidencia que el propio
   informe no había ido a buscar (R6, y la mitad de R4), uno se reduce a una
   decisión del propietario (R4 resto), y uno está infravalorado porque el
   informe se negó a cuantificarlo (R3). El subtotal 16–31 h «sin R3» es, en
   magnitud, razonable; la distribución no.

2. **El censo que el informe pone como primera tarea (60–120 min) está hecho
   aquí**, con datos vivos del NAS (21-09, 21:01–21:10 UTC). Resultado: hay
   **tres productores legacy** (BFF público diario, worker R5 cada 6 h,
   Portfolio diario) y **un solo corpus que importa**: el de core, alimentado
   únicamente por el worker R5 vía CDC. El worker público cosecha 28 fuentes al
   día en una base que **ningún lector de usuario consulta ya** salvo la alerta
   de profesor y el productor escolar.

3. **Hallazgo que el informe no recoge y que cambia el plan:** el corpus core
   **no recibe ninguna fuente de scraper desde el 1 de septiembre** (irishjobs,
   financejobs, myscience, gastrojob, tes, schuljobs, stelle_admin, los ocho
   colegios). El matching de los dos perfiles lleva tres semanas sin esas
   fuentes; las diez búsquedas las perdieron hoy a las 15:35 UTC al pasar a
   core (55 de las 92 ofertas pendientes recuperadas eran de IrishJobs).
   No es un riesgo futuro: es el estado actual de producción.

4. **R6 (postprocesado sin CDC) ya funciona.** A las 16:59 UTC, tras activar
   WorkingNomads nativo, `jobhunt.shadow.project` corrió 708 s con
   `batches: 0` y `recovery_evaluated: 3`: sin ningún lote CDC, el proyector
   drenó los embeddings del lote nativo y reevaluó los tres perfiles. Es
   exactamente lo que R6 pide demostrar. Coste restante: 0 h de código.

5. **R4 (Portfolio) se reduce a una decisión.** Portfolio tiene **0 búsquedas
   guardadas** (su runner se autodesactiva cada hora con un WARNING desde el
   flip de agosto) y su tablero público **usa sus propias cachés de 20
   fuentes** (`/api/v1/<fuente>-jobs/recent` en el frontend). Esa cosecha es
   una funcionalidad del Portfolio, no un motor legacy de SwissJob: no hay
   que retirarla ni desacoplar su digest. Coste: 0,5 h de acta.

6. **Estimación revisada:** **20–24 h efectivas** en la variante mínima,
   **27–32 h** en la estricta. Con suites, drenajes y el aviso natural de
   mañana, son **2 jornadas largas o 3 normales**, no «muchos días». Lo que
   decide entre ambas variantes son tres decisiones del propietario (§8), no
   trabajo técnico incierto.

---

## 1. Base de la comprobación

| Qué | Valor |
|---|---|
| SwissJob HEAD | `ee83f96` (el informe se escribió sobre `a859bf5`; sólo docs después) |
| Portfolio backend HEAD | `0f7ac3f` (repositorio git propio anidado en `ReactPortfolio/backend/`) |
| Sonda NAS | 2026-09-21 21:01–21:10 UTC, sólo lectura, sin secretos |
| Imágenes vivas | core `point4-3d5d67a` (API/worker/capture), BFF+worker público `point4-a0fb403`, worker R5 `point4-d89b6ee`, Portfolio `e15-9f8c85a` |
| Suites | **no ejecutadas** (el informe tampoco lo pide para leerlo) |
| Documentos leídos | informe, runbook, `CIERRE_PUNTOS_4_5`, ESTADO §42–43, DEUDA §0.A, las 9 actas del 20/21-09, `NATIVE_*` |
| Código leído | registros de productores (core, BFF, Portfolio), guardas, scheduler legacy y beat core, `pipeline_tasks`, `projector` (`_project_all`, `_after_batch`, `_replay_after_batch`), `schools/producer.py`, `alert_tasks`, `watchlist_tasks`, Portfolio `main.py`, `saved_search_runner`, `daily_match_report`, `job_cache_updater`, `native_json`, `search_execution` |

---

## 2. Estado real desplegado (lo que el informe no volvió a mirar)

### 2.1 Tres productores legacy, un corpus autoritativo

```
                 ┌────────────────────────────────┐
  BFF público    │ swissjob-worker (a0fb403)       │  cosecha DIARIA (12:26–12:36 UTC hoy)
  swissjobhunter │ 20 providers + 15 scrapers      │  → embed → dedup (matching OMITIDO)
                 │ LEGACY_DISABLED=[workingnomads] │  lectores: alerta profesor, productor escolar
                 └────────────────────────────────┘  NADIE MÁS (routing: todo core_primary)

                 ┌────────────────────────────────┐
  R5             │ swissjob-worker-r5 (d89b6ee)    │  `celery call tasks.fetch_providers` cada 6 h
  swissjobhunter │ SOLO providers (14 activos)     │  (trigger con imagen vieja 8c82ff5ca175)
  _r5_rehearsal  │ DISABLED=[jobicy, workingnomads]│  → CDC slot jobhunt_shadow_r5_rehearsal
                 └────────────────┬───────────────┘  → core (proyector cada 5 min)
                                  ▼
                 ┌────────────────────────────────┐
  CORE           │ jobhunt.* en la MISMA BD r5     │  40.406 vacantes vivas
                 │ scopes nativos: 1 habilitado    │  10 búsquedas (exec enabled), 3 perfiles
                 │ (workingnomads, 16:43 UTC)      │  outbox → BFF /integration/events
                 └────────────────────────────────┘  → Portfolio /integration/inbox

                 ┌────────────────────────────────┐
  Portfolio      │ portfolio_backend (9f8c85a)     │  cosecha circadiana DIARIA (14:20 UTC hoy)
  proyecto       │ 20 fuentes → Redis jobcache:*   │  lectores: SU tablero, SU digest 07:00 UTC
                 │ saved_searches: 0 filas         │  routing: catalog/matching core_read,
                 │ colegios: 4 scrape → core store │  applications/searches/docs/schools core_primary
                 └────────────────────────────────┘
```

Hechos verificados:

- `jobhunt_routing` público: catalog, matching (ambos perfiles), documents,
  schools y saved_searches en `core_primary`. Candidaturas: 0. `match_results`
  local: última escritura 04-09. `notifications` local: última 21-09 07:10
  (la última pasada legacy de búsquedas antes del corte).
- CDC al día: `shadow_capture_state.last_applied_lsn` actualizado 15:53:43
  (= última cosecha R5), heartbeat 21:04. Proyector cada 5 min con 0 lotes.
- `CORE_SAVED_SEARCH_EXECUTION_ENABLED=true`; `jobhunt.searches.run_due` cada
  5 min: `processed: 0` (nada vencido aún).
- Portfolio: `daily_match_report_failed_deliveries=0`; `saved_searches` vacía;
  cosecha de las 20 fuentes completada hoy 14:20–14:24 UTC.

### 2.2 Censo por fuente (la tarea 1 del informe, ya hecha)

**Base R5 → es lo que llega a core.** Columna «core» = última encarnación vista
en `source_listing_incarnations` (misma BD, esquema `jobhunt`).

| Fuente | Adaptador nativo | R5 nuevas 7 d | R5 última alta | Core última vista | Público nuevas 7 d |
|---|---|---:|---|---|---:|
| arbeitnow | sí | 3.472 | 21-09 15:49 | 21-09 16:12 | 1.568 |
| jobgether | sí | 1.033 | 21-09 09:49 | 21-09 16:12 | 375 |
| nav_arbeidsplassen | sí | 428 | 21-09 15:53 | 21-09 16:13 | 375 |
| publicjobs | sí | 118 | 21-09 09:50 | 21-09 16:13 | 37 |
| ostjob | sí | 89 | 21-09 15:53 | 21-09 16:13 | 16 |
| zentraljob | sí | 68 | 21-09 15:53 | 21-09 16:13 | 11 |
| weworkremotely | sí | 56 | 21-09 09:48 | 21-09 16:13 | 55 |
| globaljobs | sí | 44 | 19-09 21:49 | 21-09 09:56 | 44 |
| workingnomads | **nativo activo** | 17 | 21-09 15:52 | 21-09 16:43 | 16 |
| zebis | sí | 10 | 21-09 09:50 | 21-09 16:13 | 13 |
| thehub | sí | 6 | 21-09 09:49 | 21-09 16:13 | 6 |
| remotive | sí | 3 | 20-09 15:48 | 21-09 16:13 | 3 |
| jobspresso | sí | 0 (feed lento) | 29-08 | 21-09 16:12 | 0 |
| euremotejobs | sí | 0 (feed lento) | 11-09 | 21-09 16:12 | 0 |
| jobicy | sí | **nunca en R5** | — | — | (público sí) |
| **irishjobs** | **no** | 0 | 27-08 | **01-09** (+55 recuperadas hoy) | **193** |
| **financejobs** | **no** | 0 | 27-08 | **01-09** | **144** |
| myscience | no | 0 | 30-06 | 01-09 | 15 |
| gastrojob | no | 0 | 26-08 | 01-09 | 7 |
| tes | no | 0 | 26-08 | 01-09 | 4 |
| stelle_admin | no | 0 | 27-08 | 01-09 | 0 (última 27-08) |
| schuljobs | no | 0 | 25-08 | 01-09 | 0 (última 25-08) |
| swiss_schools_* (8) | no (van por observaciones) | 0 | 26-08 | 01-09 | nae 3, ecolint 6, iscs 2 |

Lectura:

- **Los 14 providers de R5 tienen los 14 adaptadores nativos que faltan por
  activar.** No falta código para ninguno; falta la maniobra por fuente.
- **Jobicy no es un traspaso**: R5 nunca lo cosechó (`DISABLED=["jobicy"]`
  se puso precisamente para conservar cobertura, acta R5 21-09). Activarlo en
  core sería cobertura nueva → fuera del punto 4 por la propia regla del
  informe (§5).
- **Los siete scrapers generales y los ocho de colegios no llegan a core desde
  el 01-09** (fecha del snapshot CDC). El worker público los cosecha cada día
  en una base que sólo leen la alerta de profesor y el productor escolar.
  De ellos, **solo dos tienen volumen real**: irishjobs (193/7 d) y
  financejobs (144/7 d). stelle_admin y schuljobs no producen nada desde
  finales de agosto (schuljobs tiene 530 filas, 0 nuevas: caído o bloqueado).
- **NAV y Jobgether los cosecha R5 sin problema** (428 y 1.033 nuevas en 7 d).
  Los 429/403 que el informe atribuye a «dependencia externa» los sufrieron
  los adaptadores **nativos** en sus sondeos. Es un problema de paridad del
  adaptador (cabeceras, paginación, ritmo) con el provider legacy, no un
  bloqueo del portal.

### 2.3 Colegios

- Core: 18 monitores del consumer `swissjob-shadow` (15 activos en modo
  `scrape`), 6 del consumer `portfolio` (4 activos). `school_job_details`:
  158 filas, última 20-09 12:10 (los scrapers escolares del worker público
  publican observaciones vía `SchoolClient` → core).
- Portfolio: 4 colegios `scrape` (isb, isg, vis, zis) por `groq_extract` /
  `jina_reader`, cada 24 h, contra `school_store` core. Sin dependencia legacy.
- Dependencia real y única: `backend/services/schools/producer.py:107-128`
  lee `select(Job)` local del scraper para decidir qué publicar; el cursor
  (`scraping_tasks.py:529`) no se reconoce hasta que la publicación llega.

### 2.4 Cosas que el informe no menciona y afectan al cierre

| # | Hallazgo | Evidencia | Consecuencia |
|---|---|---|---|
| H1 | Corpus core sin scrapers desde 01-09 | tabla §2.2, columna «core» | Hueco de cobertura **ya en producción**; hoy se extendió a las 10 búsquedas |
| H2 | Alerta de profesor de primaria lee `jobs` **público** (categoría H) | `backend/tasks/alert_tasks.py:81-88`; `TEACHER_ALERT_EMAIL` configurado en backend y worker | Si el worker público deja de cosechar zebis/publicjobs/tes/colegios, la alerta se apaga en silencio. El informe no la lista entre los «avisos» |
| H3 | Portfolio: 0 búsquedas guardadas; tablero lee sus cachés | `SELECT count(*) FROM saved_searches` = 0; frontend llama `/api/v1/<fuente>-jobs/recent` | R4 casi vacío (§3) |
| H4 | R6 ya probado en vivo | log core-worker 16:59:03 UTC: `batches: 0 … recovery_evaluated: 3`, 708 s | R6 = 0 h de código |
| H5 | Copia temporal **caducada y viva** | `swissjob-f-rehearsal-20260919` `status=running`, creada 19-09 20:26; caducidad 21-09 20:00 UTC; sonda 21:01 UTC | Obligación ya vencida; retirar contenedor + `swissjob-f-rehearsal.goIBte` con autorización |
| H6 | El worker público embebe y deduplica cada día un corpus que nadie lee | `pipeline_tasks.py:70-84` (matching omitido; embed+dedup siguen) sobre 14.791 arbeitnow, 5.785 jobgether… | Coste CPU diario sin lector. Se elimina solo al cerrar R7 |
| H7 | Restos R5: `swissjob-frontend-r5` (up), trigger con imagen vieja `8c82ff5ca175`, `swissjob-erasure-cdc` | `docker ps` | Van en el paquete de retirada R7; no antes |
| H8 | Coste real de portar un adaptador | git log 19/20-09: 4 RSS en 3,5 h; CH Media+PublicJobs 1,7 h; NAV+TheHub 0,5 h; Jobgether 0,3 h | Base para estimar R3 con cifras, no «sin número» |

---

## 3. Dictamen R1–R7

### R1 · Primer recorrido natural de avisos — **NECESARIO, 0,5 h, sin código**

- Evidencia: `saved_search_execution` 10/10 `enabled`; `run_due` cada 5 min
  `processed: 0`; última notificación legacy 21-09 07:10 → primer vencimiento
  diario 22-09 ~07:10 UTC (coincide con el acta).
- Acción: mañana tras 07:15 UTC, `SELECT` de `saved_search_observations` y
  `integration_outbox_deliveries` del evento `saved_search.matches`, y
  `notifications` en el BFF público. Correlacionar `event_id`.
- Si no hay coincidencias: registrar «sin evento real» y dejarlo abierto sin
  bloquear el resto. No fabricar avisos. El informe acierta.
- Cierre: un evento natural con `event_id` en outbox → entrega → notificación.

### R2 · Fuentes con adaptador nativo — **NECESARIO, 9–12 h; el informe lo infravalora (4–7 h) y sobreestima el riesgo externo**

Son **13 fuentes**, no «grupos genéricos»: las 14 de R5 menos WorkingNomads.
Jobicy fuera (cobertura nueva).

Tres grupos por riesgo de preparación, **una o dos maniobras de corte**:

| Grupo | Fuentes | Preparación pendiente | Riesgo |
|---|---|---|---|
| A · limpias | arbeitnow, remotive, weworkremotely, euremotejobs, jobspresso, globaljobs, zebis, publicjobs, thehub | Paridad de los 12 campos con el script ya usado para WorkingNomads (`3d5d67a` añadió `workingnomads_metadata` sólo a WN, `native_json.py:123`); declarar `admission_window_days=7` y `legacy_title_filter` como en legacy | bajo |
| B · identidad | ostjob, zentraljob | Reconciliar URLs legacy → `id:<portal>` (`NATIVE_CHMEDIA_PORTAL_IDENTITY`: 54/10 URLs compartidas con contenido distinto). Es la única brecha de identidad real | medio |
| C · paridad de acceso | nav_arbeidsplassen, jobgether | Igualar el patrón de petición del provider legacy (UA/cabeceras, paginación, pausa) en `native_nav.py`/`native_jobgether.py`; sondear una descarga completa sin 429/403 | medio-alto |

Sobre «Remotive por categorías» (informe R2): las 14 categorías son del
**Portfolio**, que sigue cosechando para su tablero (§3 R4). R5 —lo que llega
a core— usa el feed global de 200 (`providers/remotive.py`). El nativo con
`limit=200` es paridad exacta de **lo que se transfiere**. No hay que
conservar categorías en core.

Maniobra por corte (reutiliza la de WorkingNomads, acta 21-09):

1. `LEGACY_DISABLED_PROVIDERS` ampliada en worker R5 **y** en BFF+worker
   público (el público también las cosecha, para nada). Recrear los tres con
   margen de parada; verificar `get_provider(x) is None` y registry sin ellas.
2. Drenar: `celery inspect active/reserved/scheduled` = vacío; CDC
   `last_applied_lsn` estable tras la última cosecha R5.
3. `UPDATE harvest_scopes SET enabled=true, params=…` para los scopes nativos
   del grupo (crearlos si sólo existe `legacy:<x>`).
4. Ejecutar `jobhunt.harvest.run_scope` por scope con claim (no esperar a la
   ventana 00:10/06:10/12:10/18:10); segunda ejecución = `skipped`.
5. Verificar `source_scope_state.last_complete_at`, conteo de canónicas con
   idioma/campos, feed BFF por `CoreMatching`, 1 vacante primaria nativa por
   fuente vía HTTP.
6. Recibo privado en NAS. Rollback por fuente: `enabled=false` + quitar de la
   lista legacy + recrear (ya ensayado).

**Agrupación:** una sola maniobra para A+B tras completar la preparación de B
(la mecánica es idéntica y el rollback es por fuente), y una segunda para C.
Dos drenajes en vez de trece. Si el propietario prefiere la lectura literal
del runbook («no agrupar si difieren identidades»), son tres maniobras y +1,5 h.

Cierre: `source_scope_state` con `last_complete_at` reciente para las 13,
`legacy:*` sin encarnaciones nuevas, replay `skipped`, feed servido con
vacantes primarias nativas, dispatcher real despachando en la siguiente ventana.

### R3 · Productores sin sustituto — **NECESARIO PARA DOS FUENTES (5–7 h); el resto es decisión, no trabajo**

El informe se niega a cifrarlo. Con el censo:

| Fuente | Volumen 7 d | Complejidad de portado | Recomendación |
|---|---:|---|---|
| irishjobs | 193 | 675 líneas, SSR `__PRELOADED_STATE__` con regex anclada, 2 hosts, httpx | **Portar** (3 h + 1 h paridad). Es la fuente que más aportaba a las búsquedas (55/92) |
| financejobs | 144 | 276 líneas, `__NEXT_DATA__`, httpx | **Portar** (1,5 h + 0,5 h) |
| myscience | 15 | 164 líneas, SSR | Decisión: +1,5 h o excluir |
| gastrojob | 7 | 499 líneas, TYPO3, usa Playwright | Decisión: +2,5 h o excluir |
| tes | 4 | 183 líneas, `__NEXT_DATA__` 1 job/página | Decisión: +1,5 h o excluir (relevante para P2 si docencia) |
| stelle_admin | 0 desde 27-08 | Playwright | **Excluir**: inactiva, registrar |
| schuljobs | 0 desde 25-08 | AJAX scroll | **Excluir**: caída/bloqueada, registrar; reabrir sólo con reproducción |

Método: nuevo `native_<x>.py` que **reutiliza las funciones de parseo del
scraper legacy copiadas sin dependencia del backend** (patrón ya usado en
`native_rss.py`: «Copied unchanged from the corresponding legacy providers»),
prueba de paridad sobre una respuesta pública real (patrón
`NATIVE_*_PARITY_*.json`), y la misma maniobra de corte que R2. No hay
endpoint de ingesta HTTP en core (`jobhunt_core/api/`: sólo referencias y
`school-jobs`), así que la alternativa «colector que publica al core» **no
existe** para ofertas generales; el informe la ofrece como opción y no lo es.

Cierre: irishjobs y financejobs con `last_complete_at` en core y encarnaciones
nuevas; lista explícita de excluidas con causa medible en el acta.

### R4 · Portfolio — **SIMPLIFICABLE a 0,5 h (el informe presupuesta 4–7 h)**

- Búsquedas guardadas Portfolio: **0 filas**; routing `core_primary` desde
  25-08; el runner se autodesactiva cada hora (`saved_search_runner.py:175`,
  WARNING visible en logs). **No hay nada que desacoplar.** Nadie tiene una
  búsqueda que pueda dejar de avisar.
- Digest: `daily_match_report.py:330` lee `collect_normalized_jobs()` de la
  caché **que el propio Portfolio sigue cosechando** cada día (14:20 UTC hoy,
  20/20 fuentes). Esa cosecha alimenta el tablero público
  (`/api/v1/<fuente>-jobs/recent`, `/api/v1/jobs/search`, `jsearch-jobs`).
  No es un motor legacy de SwissJob: es una funcionalidad del Portfolio.
  Retirarla rompería su tablero. **Fuera del punto 4.**
- Único punto real: **doble sondeo** de 10 portales (Remotive, Jobicy,
  Arbeitnow, WWR, Ostjob, Zentraljob, TheHub, Jobgether, NAV, IrishJobs) por
  Portfolio y core. Una vez al día, ≤200 por petición. Decisión del
  propietario: aceptar y documentar (0 h) o desplegar la guarda `0f7ac3f` con
  `LEGACY_DISABLED_SOURCES` para las solapadas (0,5 h) asumiendo que esas
  páginas del tablero y esa parte del digest se quedan sin datos.
- El informe pide «revisar `26b752a`» (modelo CV): verificado en §2.1, el
  contenedor no define `GOOGLE_API_KEY`; el cambio no altera producción.

Cierre: párrafo en el acta con la decisión; nada más.

### R5 · Colegios — **SIMPLIFICABLE: 0,5 h (documentar) o 2 h (quitar la dependencia)**

- Portfolio: ya autónomo sobre `school_store` core (`school_scraper.py:319`).
  Nada que hacer.
- SwissJob: los 8 scrapers escolares corren en el worker público, leen los
  monitores de core (`producer.py:36-84`) y publican observaciones a core.
  Son «un colector especializado que publica al core» — la alternativa que el
  informe propone evaluar — **ya hoy**. Sólo dependen de la tabla `jobs`
  local como *staging* en `reconcile` (`producer.py:107-128`): publica
  históricos no observados + hashes vivos.
- Mínimo estricto: `reconcile` sobre `live_hashes` únicamente (ya recibe el
  parámetro), con regresión roja para «histórico sin observar» → 2 h. Mínimo
  pragmático: declarar en el acta la dependencia deliberada del staging local
  (la retirada final conserva ese worker para colegios) → 0,5 h.
- **No portar los 8 scrapers a core**: 1.600 líneas, cinco estrategias, sin
  contrato de ingesta general en core. El informe acierta al no exigirlo.

Cierre: acta nombra el colector escolar como responsabilidad explícita del
worker público reducido, con su prueba de alta/replay ya existente.

### R6 · Postprocesado sin CDC — **YA RESUELTO**

- Evidencia directa (core-worker, 21-09 16:59:03 UTC): `jobhunt.shadow.project`
  → `{'batches': 0, 'changes': 0, … 'profiles_evaluated': 0,
  'recovery_evaluated': 3}` en 708 s, quince minutos después de la activación
  nativa de WorkingNomads (16:43:56). Camino: `_project_all` → 0 lotes →
  `_replay_after_batch(drain_embeddings=True)` → `_drain_embeddings` +
  `_recovery_targets` (huella del corpus distinta) → `_evaluate_and_record`
  (`projector.py:1605-1650, 1734-1745`).
- Es la cadena canónica → embedding → evaluación → feed **sin captura CDC**,
  con reintento (la recuperación es idempotente por `eval_key`). Los «24
  minutos de drenaje» del acta son este mismo trabajo útil, no un defecto.
- Lo único que queda para R7: **no** deshabilitar `jobhunt.shadow.project` al
  retirar el slot (es el motor del postprocesado nativo). Sí se pueden
  retirar `check_slot_health`, `preview_cycle`, `run_cycle`, `purge_staging`
  y el servicio `core-capture`. Renombrar/extraer `project` a una tarea
  `harvest.postprocess` es estética: el informe la excluye y aquí también.

Cierre: párrafo con este log en el acta + prueba dirigida existente.

### R7 · Retirada final y recuperación — **NECESARIO, 3–4 h; alcance más pequeño que el descrito**

Estado final defendible:

| Componente | Destino | Por qué |
|---|---|---|
| worker R5, trigger R5, `core-capture`, slot `jobhunt_shadow_r5_rehearsal`, `swissjob-erasure-cdc` | **retirar** tras R2+R3 (drenaje, `last_applied_lsn` estable, `pg_drop_replication_slot`) | ya no producen nada que core no produzca |
| `swissjob-frontend-r5` | retirar | huérfano (su backend se paró el 19-09) |
| beat core `shadow.check_slot_health / preview_cycle / run_cycle / purge_staging` | retirar | sólo sirven al CDC/gate |
| beat core `shadow.project`, `delivery.dispatch_outbox`, `searches.run_due`, `harvest.*`, `maintenance.*` | conservar | motor nativo |
| worker público: `fetch_providers` | lista `LEGACY_DISABLED_PROVIDERS` = las 20 | nadie lee esa base salvo H2 |
| worker público: `fetch_scrapers` | `LEGACY_DISABLED_SCRAPERS` = los 7 generales; **conservar los 8 `swiss_schools_*`** | colector escolar (R5) |
| worker público: `embed_all_pending`, `dedup_semantic_batch`, `check_job_urls`, `cleanup_stale_jobs` | conservar (sólo tocan el staging escolar; coste residual) o desactivar la cadena diaria salvo scrapers | H6 |
| alerta profesor (H2) | decisión §8 | lee `jobs` público |
| BFF público, auth, CV, documentos, entrega, `profile_erasure` réplica `swissjob-live` | conservar | el informe acierta |
| tablas/volúmenes legacy | conservar sólo lectura el plazo ratificado | el informe acierta |

Recuperación post-corte: la que ya existe por fuente (deshabilitar scope +
lista legacy + recrear), ensayada en WorkingNomads. Sólo se añade el caso
«slot ya borrado»: reanudar legacy exige un snapshot CDC nuevo, es decir
**no hay vuelta atrás barata del slot** → borrarlo el último y con 48 h de
observación posteriores al corte de C.

---

## 4. Lista finita de trabajo restante

| # | Trabajo | Horas | Espera no solapable | Depende de |
|---|---|---:|---|---|
| 0 | Retirar copia caducada `swissjob-f-rehearsal-20260919` + directorio (autorización) | 0,3 | — | — |
| 1 | R1: observar vencimiento 22-09 07:10 UTC y correlacionar | 0,5 | hasta 07:15 UTC | — |
| 2 | R6: recoger log 16:59 en el acta | 0,3 | — | — |
| 3 | R2-A prep: paridad 12 campos × 9 fuentes con el script de WN; params de scope | 1,5 | — | — |
| 4 | R2-B prep: reconciliación URL legacy → id portal CH Media (script + rojo/verde) | 2,0 | — | — |
| 5 | R2-C prep: paridad de acceso NAV/Jobgether con el provider legacy | 3,0 | sondeos a portal (min) | — |
| 6 | R3: portar irishjobs + financejobs (parseo copiado, paridad pública) | 5,0 | — | — |
| 7 | Release candidata core (3–6): dirigidas + suite completa | 0,5 | ~15 min suite | 3,4,5,6 |
| 8 | **Corte 1**: A+B (11 fuentes) + irishjobs/financejobs = 13 scopes | 2,5 | drenaje ~25 min | 7 |
| 9 | **Corte 2**: NAV + Jobgether | 1,5 | drenaje ~25 min | 5, 8 |
| 10 | R5: decisión + (opcional) `reconcile` sin `select(Job)` | 0,5 / 2,0 | — | — |
| 11 | R4: decisión Portfolio + (opcional) guarda `0f7ac3f` | 0,5 | — | — |
| 12 | H2: decisión alerta profesor (§8) + implementación mínima | 0,5–2,0 | — | — |
| 13 | R7: recrear público con listas completas; parar R5/capture/trigger/erasure-cdc/frontend-r5; beat core sin tareas de slot; 48 h de observación; `drop slot` | 3,0 | 48 h observación (no bloquea el acta) | 8, 9 |
| 14 | Ensayo de recuperación post-corte (por fuente, ya ensayado; añadir «slot borrado») | 1,0 | — | 13 |
| 15 | Acta única + checkpoint vigente en runbook/ESTADO/DEUDA | 1,0 | — | todo |

**Variante mínima** (decisiones «documentar» en 10, 11, 12; portar sólo
irishjobs+financejobs): **20–24 h efectivas**.
**Variante estricta** (reconcile sin Job, guarda Portfolio, alerta re-cableada,
+myscience/tes): **27–32 h**.

Condiciones que invalidan el rango: (a) NAV o Jobgether no alcanzan paridad
de acceso en 3 h → se quedan en R5 y **R7 no puede retirar el slot** (queda
un R5 residual de 2 fuentes; +0 h pero cierre parcial declarado); (b) un
defecto nuevo con reproducción en el sink/admisión durante el corte 1;
(c) la primera entrega natural (R1) falla por causa del canal → reabre el
circuito de búsquedas (no de fuentes).

---

## 5. Camino crítico y qué se solapa

```
día 1 (mañana)   [0] copia  ──►  [3] prep A ──► [4] prep B ──► [6] irishjobs/financejobs
                 [1] R1 observar 07:15 UTC (10 min, en paralelo)
día 1 (tarde)    [5] prep C (NAV/Jobgether)  ──► [7] release + suite (15 min)
                 [10][11][12] decisiones (pedir al propietario al inicio del día 1)
día 2 (mañana)   [8] corte 1 (13 scopes)  ──► verificación feed/canary
día 2 (tarde)    [9] corte 2 (NAV/Jobgether) ──► [13] retirada (sin drop slot) ──► [14] ensayo
día 2 (noche)    [15] acta
día 4            drop slot tras 48 h de dispatcher nativo sin incidencias (5 min)
```

- Camino crítico: 3 → 4 → 6 → 7 → 8 → 9 → 13 → 15 (≈ 17 h efectivas).
- Solapable sin competir por BD/autoridad: 1, 5 (prep C mientras corre la
  suite de 7), 10–12 (decisiones), redacción parcial de 15.
- **No** solapar: dos suites sobre la BD de test; un corte con una suite; el
  drenaje de R5 con una cosecha en vuelo (comprobar `inspect active` antes).

---

## 6. Estimación frente a la del informe

| Partida | Informe | Este análisis | Diferencia y motivo |
|---|---:|---:|---|
| Censo | 1–2 | **0** | hecho aquí con datos vivos |
| R1 avisos | 0,5–1 | 0,5 | igual |
| R2 fuentes portadas | 4–7 | **9–12** | 13 fuentes con 3 preparaciones reales (CH Media identidad, NAV/Jobgether acceso, paridad 12 campos); el informe contaba «grupos» sin censarlos |
| R3 sin sustituto | «sin número» | **5–7** (2 fuentes) · +5,5 opcional | cifrado por líneas/modo/volumen con los tiempos reales del 19/20-09 |
| R4 Portfolio | 4–7 | **0,5** | 0 búsquedas; tablero y digest viven de su propia cosecha, que no se retira |
| R5 colegios | 3–6 | **0,5–2** | ya son colector→core; sólo el staging `Job` en `reconcile` |
| R6 sin CDC | 1–3 | **0,3** | probado en vivo a las 16:59 UTC |
| R7 retirada | 2–4 | 3–4 | igual; alcance más definido |
| Acta | 0,5–1 | 1 | igual |
| **Total** | **16–31 + R3** | **20–24 / 27–32** | el total es comparable; la diferencia es que ahora **está todo dentro** |

Tiempo de reloj no programable: suite core 907 s × 2–3 releases; BFF 328 s
× 1 (sólo si cambia el BFF: listas legacy son env, no código); Portfolio 198 s
× 0–1; drenajes ~25 min × 2; aviso natural mañana 07:10 UTC; 48 h de
observación antes de `drop slot` (no bloquea el acta de cierre, sí el borrado).

---

## 7. Por qué se consumió más tiempo (lectura del historial, no del informe)

`git log` desde el 18-09: **42 commits en dos sesiones** — 19-09 21:54 →
20-09 08:17 (10,4 h seguidas, 26 commits) y 21-09 12:36 → 19:29 (6,9 h, 15
commits). ≈ 17,3 h de reloj del agente, coherente con las «15–16 h» del
propietario. En ese tiempo se entregó: 15 adaptadores nativos con paridad,
guardas por fuente en los tres productores, planificador nativo con exclusión,
canal duradero de perfiles, corte de feedback (112 cambios), corte de búsquedas
(277 pendientes, 92 recuperadas), corte de WorkingNomads y siete actas.

La causa del desvío es la que el informe reconoce en §6: «unas horas» valoraba
sólo *cambiar el productor*, pero perfiles, feedback y búsquedas eran
**precondiciones**, no ampliación (sin ellas, apagar R5 habría dejado avisos
y decisiones de usuario colgando de una base muerta). Dos cosas sí eran
evitables y conviene decirlas:

1. **No hacer el censo primero.** Con la tabla de §2.2 el 19-09 se habría
   visto que sólo hay 14 fuentes que transferir, 2 que portar y 0 búsquedas
   Portfolio, y que el corpus core llevaba sin scrapers desde el 01-09.
2. **Documentar en exceso entre pasos.** 17 de los 42 commits son `docs:` con
   checkpoints que se superseden entre sí en horas. Una sola acta por corte
   habría ahorrado 2–3 h de reloj y evitado la «documentación fragmentada»
   que el propio informe lamenta.

---

## 8. Decisiones que sólo puede tomar el propietario (pedir el día 1, a primera hora)

| # | Decisión | Opción rápida | Opción estricta | Diferencia |
|---|---|---|---|---|
| D1 | **Scrapers de bajo volumen** (myscience 15/7 d, gastrojob 7, tes 4) | excluir con causa medible en el acta | portar (+5,5 h) | 5,5 h; tes puede importar si P2 busca docencia |
| D2 | **Alerta de profesor de primaria** (lee `jobs` público) | mantener en el worker público la cosecha diaria de zebis/publicjobs/tes/colegios **sólo** para ella (doble sondeo de 2 feeds/día, documentado) — 0,5 h | sustituirla por una búsqueda guardada core de P2 con el mismo filtro y retirar la tarea (0,5 h si el canal de avisos cubre el correo) o re-cablearla al catálogo core (2 h) | 0–1,5 h; la estricta elimina el último lector del corpus público |
| D3 | **Portfolio: doble sondeo** de 10 portales | aceptar y documentar (0 h) | guarda `0f7ac3f` con las solapadas (0,5 h; el tablero y el digest pierden esas fuentes) | funcionalidad del Portfolio |
| D4 | **Colegios: staging `Job` en `reconcile`** | dependencia declarada (0,5 h) | `reconcile` sólo con extracción viva (2 h) | 1,5 h |
| D5 | **Agrupar cortes** | A+B en una maniobra, C en otra (2 drenajes) | tres maniobras (runbook literal) | 1,5 h |

Con «rápida» en todo: ~20 h. Con «estricta» en todo: ~30 h.

---

## 9. Qué no hacer (coincide con el informe, con tres añadidos)

- No activar Jobicy, no reabrir stelle_admin/schuljobs, no portar los 8
  scrapers escolares, no crear un endpoint de ingesta general en core, no
  tocar el ranker ni el examen.
- **No deshabilitar `jobhunt.shadow.project`** por llamarse «shadow»: es el
  postprocesado nativo (R6).
- **No borrar el slot** antes de 48 h de dispatcher nativo sin incidencias:
  es la única acción sin vuelta atrás barata.
- **No desplegar la guarda Portfolio** «por completar la lista»: sin D3
  explícita, apaga páginas del tablero.

---

## 10. Checklist de cierre (la del informe, corregida)

- [x] Inventario efectivo de ambos proyectos conciliado (§2.2, §2.3).
- [ ] 13 fuentes R5 transferidas con paridad/identidad/filtros preservados; R5 sin escritor efectivo.
- [ ] irishjobs y financejobs (y las que fije D1) cosechadas en core; excluidas con causa.
- [ ] Ofertas exclusivamente core visibles y accionables (reutilizar canary WN; 1 por fuente).
- [ ] Diez búsquedas: entrega no vacua acreditada (R1) o «sin evento» declarado con fecha.
- [x] Portfolio: sin búsquedas dependientes; digest/tablero sobre su propia cosecha (D3 registrada).
- [ ] Colegios: observaciones y avisos siguen llegando desde el colector público (D4 registrada).
- [x] Embeddings/dedup/matching/notificaciones sin CDC: probado 21-09 16:59 UTC.
- [ ] Alerta de profesor: D2 aplicada y verificada.
- [ ] R5/capture/trigger/erasure-cdc/frontend-r5 parados; beat core sin tareas de slot; `shadow.project` conservado con responsabilidad explícita.
- [ ] Recuperación post-corte ensayada por fuente; slot borrado tras 48 h.
- [ ] Acta única con imagen/config/conteos; runbook y ESTADO con un solo checkpoint vigente.

---

## Anexo A · Sondas ejecutadas (reproducibles, sólo lectura)

```sh
# Contenedores e imágenes
ssh nas "$D ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'"
# Configuración efectiva (sin secretos)
ssh nas "$D inspect <c> --format '{{range .Config.Env}}{{println .}}{{end}}'" | grep -E 'SCHEDULER_|LEGACY_DISABLED|CORE_'
# Cosecha legacy por fuente (bases swissjobhunter y swissjobhunter_r5_rehearsal)
SELECT source, count(*), count(*) FILTER (WHERE is_active), max(first_seen_at),
       count(*) FILTER (WHERE first_seen_at > now()-interval '7 days') FROM jobs GROUP BY source;
# Frescura del corpus core por fuente
SELECT s.name, max(i.last_seen_at), count(*) FILTER (WHERE i.first_seen_at > now()-interval '7 days')
FROM jobhunt.source_listing_incarnations i JOIN jobhunt.source_listings l ON l.id=i.source_listing_id
JOIN jobhunt.sources s ON s.id=l.source_id GROUP BY s.name;
# Scopes, búsquedas, monitores, CDC
SELECT hs.id, s.name, hs.enabled, hs.params, st.last_complete_at FROM jobhunt.harvest_scopes hs
JOIN jobhunt.sources s ON s.id=hs.source_id LEFT JOIN jobhunt.source_scope_state st ON st.scope_id=hs.id;
SELECT * FROM jobhunt.saved_search_execution;  SELECT * FROM jobhunt.shadow_capture_state;
# Evidencia R6
ssh nas "$D logs swissjob-core-worker-r5 --since 72h" | grep 'shadow.project' | grep -v "'recovery_evaluated': 0"
# Portfolio
SELECT count(*) FROM saved_searches;  SELECT * FROM jobhunt_routing;
ssh nas "$D exec portfolio_redis redis-cli --scan --pattern 'jobcache:*'"
grep -rhoE "/api/v1/[a-z_-]+-jobs/recent" ReactPortfolio/frontend/src
```

---
---

# PARTE II · Manual de ejecución paso a paso

> Escrito para que lo ejecute **cualquier agente**, incluido uno menos capaz.
> Cada paso tiene: objetivo, precondición, comandos literales, **salida
> esperada**, **qué hacer si no coincide** y el recibo que hay que guardar.
> Si una salida no coincide con la esperada: **PARAR**, anotar lo observado
> en el acta (§P.15) y preguntar al propietario. No improvisar alternativas.

## II.0 Reglas de oro del ejecutor (leer antes de cada sesión)

1. **Sólo lectura por defecto.** Todo lo que toca el NAS es `SELECT`, `docker
   inspect`, `docker logs`, `ls`, salvo los pasos marcados **[ESCRIBE]**.
2. **Prohibido** en cualquier circunstancia: `docker compose down`,
   `--remove-orphans`, `docker rm` de contenedores con volumen, `celery purge`,
   `DELETE`/`DROP`/`TRUNCATE` sobre tablas (salvo el `pg_drop_replication_slot`
   del paso 13, con sus precondiciones), `rm -rf` sin haber mostrado antes la
   ruta exacta y recibido «sí» del propietario, `git push`, `git commit` sin
   aprobación explícita en ese momento.
3. **Antes de cada [ESCRIBE]**: copia `.before` del fichero o `SELECT` del
   estado que se va a cambiar, guardada en el directorio de recibos.
4. **Un cambio por paso.** Verificar la salida esperada. Guardar recibo.
   Sólo entonces el paso siguiente.
5. **Suites de tests siempre en serie**, nunca dos `pytest` a la vez (la BD de
   test es compartida y el teardown la vacía).
6. **Nunca imprimir secretos** (`*_TOKEN`, `*_KEY`, `PASSWORD`, `DATABASE_URL`
   completo). Nunca copiar dumps ni datos personales al ordenador.
7. **Idempotencia**: si al llegar a un paso el «estado esperado al final» ya se
   cumple, anotarlo y saltar el paso. No repetir suites sobre código intacto.
8. **Cero avisos artificiales**: no crear candidaturas, feedback, correos ni
   búsquedas de prueba en producción.
9. Si el propietario no ha contestado a las decisiones D1–D5 (§8), usar la
   **opción rápida** y dejarlo escrito en el acta.

## II.1 Convenciones y variables

Ejecutar en la máquina de desarrollo. `ssh nas` funciona sin contraseña. En el
NAS **no hay `python3` ni `docker compose` en el PATH**; se usan estas rutas:

```sh
# ---- pegar al inicio de cada sesión de shell ----
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker        # docker en el NAS
C=/share/Public/swissjob/bin-docker-compose                         # compose v5.4.0 en el NAS
E=/share/CACHEDEV1_DATA/Public/unification-e15-20260914             # composes vigentes + recibos
W=$E/point4-close-20260922                                          # recibos de ESTE cierre (crear en paso 0)
PGC="$D exec -i swissjob-postgres psql -U jobhunt_core -d swissjobhunter_r5_rehearsal -A"   # core (esquema jobhunt)
PGL="$D exec -i swissjob-postgres psql -U swissjob -d swissjobhunter -A"                    # legacy público
PGR="$D exec -i swissjob-postgres psql -U swissjob -d swissjobhunter_r5_rehearsal -A"       # legacy R5
# Ejecutar SQL en core (el 'SET search_path' es obligatorio):
sqlc() { ssh nas "$PGC" <<< "SET search_path=jobhunt; $1" 2>&1 | grep -v 'config file'; }
sqll() { ssh nas "$PGL" <<< "$1" 2>&1 | grep -v 'config file'; }
sqlr() { ssh nas "$PGR" <<< "$1" 2>&1 | grep -v 'config file'; }
```

Proyectos compose y ficheros de configuración **vigentes** (verificado con
`docker inspect` el 21-09):

| Contenedor | Proyecto `-p` | Fichero `-f` | Servicio |
|---|---|---|---|
| swissjob-backend, swissjob-worker | `swissjob` | `$E/swissjob.configured.yml` | `backend`, `worker` |
| swissjob-worker-r5 | `swissjob-r5` | `$E/r5-source-handover-20260921/worker.coverage-preserved.json` | `worker` |
| swissjob-core-api-r5 / core-worker-r5 / core-capture-r5 | `swissjob-r5` | `$E/core.configured.yml` | `core-api`, `core-worker`, `core-capture` |
| swissjob-harvest-trigger-r5, swissjob-frontend-r5 | `swissjob-r5` | `/share/Public/swissjob/docker-compose.rehearsal.qnap.yml` | `harvest-trigger`, `frontend` |
| swissjob-erasure-cdc | `swissjob-erasure` | `/share/CACHEDEV1_DATA/Public/unification-e10.XXkEjw88/e13-erasure/replica.yml` | `erasure-cdc` |
| portfolio_backend | `portfolio` | `$E/portfolio.configured.yml` | `backend` |

**Recrear UN servicio** (patrón único de todo el manual; jamás `up` sin
nombre de servicio, jamás `--remove-orphans`):

```sh
ssh nas "$C -p <proyecto> -f <fichero> stop -t 2400 <servicio> && $C -p <proyecto> -f <fichero> up -d --no-deps <servicio>"
```

**Ejecutar un script operador dentro de la imagen del core** (así se hizo el
corte de WorkingNomads; `core.private.env` ya existe en
`$E/workingnomads-preactivation.3d5d67a/`, **copiarlo, nunca mostrarlo**):

```sh
ssh nas "$D run --rm --network swissjob_swissjob-net --env-file $W/core.private.env \
  -v $W:/work -v $W/<script>.py:/tmp/op.py:ro swissjob-core:point4-<sha> python /tmp/op.py <modo>"
```

**Suites locales** (desde `/home/lothar/Public/SwissJob`):

```sh
# core — dirigidas (segundos) y completa (~15 min); SIEMPRE con el perfil dev
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate \
  python -m pytest jobhunt_core/tests/<fichero>.py -q
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate \
  python -m pytest jobhunt_core/tests -q 2>&1 | tail -5
# BFF — sólo si cambia backend/ (las listas LEGACY_* son entorno, no código)
docker compose exec -T backend python -m pytest tests/ -q --timeout=30 2>&1 | tail -3
```

**Construir y subir una imagen del core** (patrón de las actas
`NATIVE_SCHEDULER_DEPLOYMENT` / `WORKINGNOMADS_CUTOVER`: se construye desde
`git archive` del commit, nunca desde el árbol sucio):

```sh
SHA=$(git rev-parse --short HEAD); mkdir -p /tmp/rel-$SHA && git archive HEAD | tar -x -C /tmp/rel-$SHA
docker build -t swissjob-core:point4-$SHA --build-arg RELEASE_SHA=$SHA -f /tmp/rel-$SHA/jobhunt_core/Dockerfile /tmp/rel-$SHA
docker save swissjob-core:point4-$SHA -o /tmp/rel-$SHA/core.tar
scp /tmp/rel-$SHA/core.tar nas:$W/core-$SHA.tar
ssh nas "$D load -i $W/core-$SHA.tar && $D image inspect swissjob-core:point4-$SHA --format '{{.Id}}'"
# Cambiar `image:` en $E/core.configured.yml (copiar antes a .before) y recrear core-api, core-worker, core-capture (uno a uno).
# Verificar: curl -s http://<core-api>:8000/v1/ready → {"release": "<SHA>", "authoritative": true}
ssh nas "$D exec swissjob-core-api-r5 curl -s localhost:8000/v1/ready"
```

Recibos: cada paso termina con `ssh nas "cat > $W/<paso>.json"` o un `.log`.
Formato libre pero **con fecha UTC, comando y salida literal**.

---

## II.2 Pasos

### P0 · Preparar la sesión (10 min)

```sh
git status --short | grep -v '^ D ' ; git log -1 --oneline
ssh nas "date -u; $D ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | sort; mkdir -p $W && chmod 700 $W && cp $E/workingnomads-preactivation.3d5d67a/core.private.env $W/ && chmod 600 $W/core.private.env && ls -la $W"
```

- Esperado: `git status` sólo con los `D docs/...` del propietario y este
  análisis sin rastrear; HEAD `ee83f96` (o posterior sólo con `docs:`). En el
  NAS, la misma lista de contenedores que §2 (28 filas). `$W` creado con
  `core.private.env` dentro.
- Si hay más ficheros modificados de los esperados → PARAR: alguien tocó el
  árbol; preguntar.
- Pedir al propietario D1–D5 pegándole la tabla de §8. Anotar la respuesta en
  `$W/decisiones.md`.

### P1 · **[ESCRIBE]** Retirar la copia temporal caducada (10 min, requiere «sí» explícito)

```sh
ssh nas "$D inspect swissjob-f-rehearsal-20260919 --format 'status={{.State.Status}} created={{.Created}} mounts={{range .Mounts}}{{.Source}} {{end}} nets={{range \$k,\$v := .NetworkSettings.Networks}}{{\$k}} {{end}}'"
ssh nas "find /share/CACHEDEV1_DATA/Public /share/Public -maxdepth 2 -name 'swissjob-f-rehearsal*' 2>/dev/null"
```

- Esperado: `status=running`, `created=2026-09-19`, **mounts dentro de un
  directorio `swissjob-f-rehearsal.goIBte`**, red `none` o propia. Si algún
  mount es `swissjob_pgdata` o `/share/Public/swissjob` → **PARAR**: no es la
  copia.
- Mostrar al propietario la ruta exacta y pedir «sí». Sólo entonces:

```sh
ssh nas "$D stop swissjob-f-rehearsal-20260919 && $D rm swissjob-f-rehearsal-20260919 && echo CONTENEDOR_RETIRADO"
ssh nas "rm -rf '<ruta exacta mostrada>/swissjob-f-rehearsal.goIBte' && echo DIR_RETIRADO"
```

- Recibo `$W/p1-copia-retirada.log` con ambas salidas y la hora.

### P2 · R1: observar la primera entrega natural (22-09 a partir de 07:15 UTC; 15 min)

```sh
sqlc "SELECT saved_search_id, run_number, last_attempt_at FROM saved_search_execution ORDER BY last_attempt_at DESC NULLS LAST;"
sqlc "SELECT count(*) AS observaciones, max(created_at) FROM saved_search_observations;"   # si la columna no existe: SELECT count(*)
sqlc "SELECT event_id, type, subject_profile_id, created_at FROM integration_outbox WHERE type='saved_search.matches' ORDER BY created_at DESC LIMIT 10;"
sqlc "SELECT d.* FROM integration_outbox_deliveries d JOIN integration_outbox o ON o.event_id=d.event_id WHERE o.type='saved_search.matches' ORDER BY d.event_id LIMIT 10;"
sqll "SELECT id, created_at FROM notifications ORDER BY created_at DESC LIMIT 5;"
ssh nas "$D logs swissjob-core-worker-r5 --since 4h 2>&1 | grep 'searches.run_due' | grep -v \"'processed': 0\" | tail -5"
```

- Esperado A (con coincidencias): las 6 búsquedas `daily` con `run_number ≥ 1`
  y `last_attempt_at ≈ 07:10`; ≥1 fila en outbox `saved_search.matches`;
  su entrega con estado entregado; ≥1 `notifications` nueva en el BFF con
  `created_at` posterior a 07:10 UTC. → **R1 cerrado**; recibo
  `$W/p2-r1-entrega-natural.log`.
- Esperado B (sin coincidencias): `run_number ≥ 1`, `processed: 6`,
  `matches: 0`, outbox sin evento nuevo. → anotar «sin evento real
  disponible el 22-09»; R1 queda abierto **sin bloquear nada**. Repetir la
  consulta el 23-09 (las `weekly` vencen otro día).
- No esperado: `run_number` sigue en 0 pasadas las 08:00 UTC o
  `failed > 0` → PARAR; volcar `docker logs … | grep -i -B2 -A8 'run_due\|Traceback'`
  al recibo y preguntar. **No** tocar `saved_search_execution`.

### P3 · R6: dejar constancia (5 min)

```sh
ssh nas "$D logs swissjob-core-worker-r5 --since 48h 2>&1 | grep 'shadow.project' | grep -v \"'recovery_evaluated': 0\"" | tee -a /tmp/p3.log
```

- Esperado: al menos la línea `2026-09-21 16:59:03 … 'batches': 0 … 'recovery_evaluated': 3`.
  Copiarla literal al acta como evidencia de R6. Recibo `$W/p3-r6.log`.

### P4 · Preparación A: metadatos de búsqueda en las 12 fuentes nativas (1–1,5 h, local)

**Hecho verificado:** las 10 búsquedas sólo filtran por `q` (9), `remote_only`
(9) y `canton` (1). `language`, `seniority`, `contract_type` y salario **no
los usa ninguna**. Por tanto la única paridad que afecta a resultados es
**`canton` en las fuentes suizas** (ostjob, zentraljob, publicjobs, zebis).

1. Leer `jobhunt_core/harvest/providers/search_metadata.py` (tiene
   `SWISS_CANTONS`) y `native_json.py:123-124` (cómo lo usa WorkingNomads).
2. Test rojo primero. Añadir a `jobhunt_core/tests/test_native_json_parity.py`
   (o fichero nuevo `test_native_swiss_canton.py`):

```python
import pytest
from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.providers.native_chmedia import CHMediaProvider
from jobhunt_core.harvest.providers.native_publicjobs import PublicJobsProvider
from jobhunt_core.harvest.providers.native_rss import NativeRSSProvider


@pytest.mark.parametrize(
    "source,raw",
    [
        (
            "ostjob",
            {
                "id": 1,
                "title": "Lehrperson",
                "company": "Schule",
                "cantons": ["St. Gallen"],
                "city": "Wil",
            },
        ),
        (
            "zentraljob",
            {
                "id": 2,
                "title": "Lehrperson",
                "company": "Schule",
                "cantons": ["Luzern"],
                "city": "Luzern",
            },
        ),
    ],
)
def test_chmedia_exposes_canton_for_saved_search_filters(source, raw):
    CHMediaProvider(source)  # registra el normalizador
    content = normalize_offer(source, raw)
    assert content["canton"] in {"SG", "LU"}
```

   (Para publicjobs y zebis, construir el `raw` con la forma que usan sus
   tests existentes `test_native_publicjobs.py` / `test_native_zebis.py` y
   afirmar `content["canton"]` cuando la ubicación nombra un cantón.)
   Ejecutar: debe fallar con `KeyError: 'canton'`.
3. Implementar: en `_content` de `native_chmedia.py`, `native_publicjobs.py`
   y en la rama `zebis` de `native_rss.py`, añadir
   `content.update({"canton": <código>})` reutilizando `SWISS_CANTONS`
   (mover la búsqueda de cantón de `workingnomads_metadata` a una función
   `swiss_canton(location_text)` en `search_metadata.py`; WorkingNomads la
   sigue usando). **No** añadir `language`/`seniority`/`contract_type` al
   resto: no los consume nadie y sería trabajo sin lector.
4. Verificar que `normalize.py` conserva la clave: `SEARCH_TEXT_FIELDS`
   incluye `canton` (`grep -n SEARCH_TEXT_FIELDS jobhunt_core/harvest/normalize.py`).
5. Dirigidas verdes: `pytest jobhunt_core/tests/test_native_chmedia*.py
   test_native_publicjobs.py test_native_zebis.py test_native_json_parity.py
   test_workingnomads_search_metadata.py -q`.
6. `git diff --check`; **no commit todavía** (se agrupa con P5–P7 en una
   release; pedir aprobación al propietario para el commit).

Recibo: `$W/p4-canton.log` con la salida roja y la verde.

### P5 · Preparación B: identidad CH Media (2 h, local + copia NAS)

Contexto: el nativo identifica por `id:<id de portal>` y URL `/stelle/<id>`;
el legacy (`legacy:ostjob`, `legacy:zentraljob` en core) tiene 2.309 + 887
vacantes vivas identificadas por hash MD5 y URL con `externalId` ATS, que se
comparte entre plazas (`NATIVE_CHMEDIA_PORTAL_IDENTITY_2026-09-20.json`).
El sink ya **refresca** una vacante conocida si la URL exacta coincide
(admisión, acta 19-09). Lo que hay que demostrar es que la primera cosecha
nativa **no duplica** las vivas ni fusiona plazas.

1. Leer `jobhunt_core/harvest/providers/native_chmedia.py` (`_listing`,
   `_content`) y `jobhunt_core/tests/test_integration_chmedia_identity.py`.
2. Sonda de sólo lectura en core: ¿cuántas URLs vivas legacy de ostjob ya
   tienen la forma `/stelle/<id>`?

```sh
sqlc "SELECT s.name, count(*) AS vivas, count(*) FILTER (WHERE i.url ~ '/stelle/[0-9]+') AS con_id_portal FROM source_listing_incarnations i JOIN source_listings l ON l.id=i.source_listing_id JOIN sources s ON s.id=l.source_id WHERE s.name IN ('legacy:ostjob','legacy:zentraljob') AND i.ended_at IS NULL GROUP BY 1;"
```

   - Si `con_id_portal = vivas` → el refresco por URL exacta ya enlaza; el
     ensayo del punto 3 sólo confirma. Si es menor → las URLs legacy con
     `externalId` no coincidirán y la cosecha nativa dará de alta la misma
     plaza otra vez; hay que enlazar por `id` de portal antes del corte.
3. Ensayo en copia (misma técnica que `workingnomads_metadata_replay.py
   rehearse`): script `chmedia_identity_rehearse.py` que, en `core_copy`,
   toma 200 raws vivos de `legacy:ostjob`, extrae su `id` de portal del raw,
   construye `RawListing("id:<id>", "https://ostjob.ch/stelle/<id>", raw)`,
   pasa por `RawListingSink().handle(db, scope_nativo, listings)` y afirma:
   `count(vacancies vivas de ostjob)` **no crece**, cada vacante conserva
   `text_hash`, y la segunda pasada es idempotente. `rollback` al final.
   Para la copia: `$E/workingnomads-preactivation.3d5d67a/` documenta cómo se
   restauró `core_copy` (contenedor PostgreSQL 16 sin red); si ya no existe,
   volver a restaurar **sólo el esquema core** con `pg_restore --exit-on-error`
   como en el acta del 19-09 (§ «Copia del esquema core restaurada»).
4. Si el ensayo muestra duplicados → **PARAR**, anotar la cifra, y preguntar
   al propietario; la alternativa es un enlazado explícito
   `link_evidence` por `id` (2 h más), no «elegir un ganador por orden».
5. Recibo `$W/p5-chmedia-identity.json` con el resumen del ensayo.

### P6 · Preparación C: paridad de acceso NAV y Jobgether (2–3 h, local)

Hecho: el worker R5 obtiene de NAV 428 y de Jobgether 1.033 ofertas nuevas por
semana **sin** 429/403. Los adaptadores nativos usan `User-Agent:
SwissJobHunter/1.0` (`native_nav.py:99`, `native_jobgether.py:105`); el
legacy usa `services.scraper_stealth.realistic_headers()` (cabeceras Chrome)
en Jobgether (`providers/jobgether.py:62`, comentario: «sin UA de navegador
la API responde 403») y `fetch_with_retry` con `PAGE_DELAY_SECONDS` en NAV.

1. Copiar las cabeceras (no el módulo) a un helper del core
   `jobhunt_core/harvest/providers/browser_headers.py` — un dict literal con
   UA Chrome, `Accept`, `Accept-Language`, `Sec-Fetch-*`, igual que devuelve
   `realistic_headers()` (ver `backend/services/scraper_stealth.py`; copiar
   valores, sin importar el backend).
2. Jobgether: usar esas cabeceras en `_page`; igualar `PAGE_PAUSE_S` al
   `PAGE_DELAY_SECONDS` legacy y `MAX_PAGES` al `MAX_PAGES` legacy. NAV:
   igualar `PAGE_PAUSE_S` (hoy 10 s) al del legacy (`PAGE_DELAY_SECONDS` en
   `providers/nav_arbeidsplassen.py`) y el tamaño de página; conservar la
   detección `partial` del nativo.
3. Test rojo/verde en `test_native_jobgether.py` / `test_native_nav.py`: la
   petición lleva las cabeceras de navegador (inspeccionar `request.headers`
   en el `MockTransport`).
4. **Sondeo real acotado, una sola vez cada uno** (es tráfico a portal, no
   repetir en bucle):

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate python - <<'PY'
import asyncio, httpx
from jobhunt_core.harvest.providers import get_provider
async def main():
    async with httpx.AsyncClient() as http:
        r = await get_provider("jobgether").fetch_new({"query": ""}, None, http)
        print("jobgether", len(r.listings), r.pages_fetched, r.complete, r.error)
        r = await get_provider("nav_arbeidsplassen").fetch_new({"remote": "Kun hjemmekontor"}, None, http)
        print("nav", len(r.listings), r.pages_fetched, r.complete, r.error)
asyncio.run(main())
PY
```

   - Esperado: `complete=True`, `error=None`, y un número de listings del
     mismo orden que el legacy en una pasada (Jobgether ≥200; NAV ≥ 100 por
     faceta). Los valores válidos de `remote` para NAV están en
     `native_nav.py` (`REMOTE_FACETS`); usar los dos que usa el legacy.
   - Si sigue 403/429 con cabeceras de navegador y la misma pausa → PARAR y
     anotar: estas dos fuentes se quedan en R5 (**y entonces el paso 13 no
     puede borrar el slot**); el resto del plan sigue.
5. Recibo `$W/p6-nav-jobgether.log`.

### P7 · Portar irishjobs y financejobs (5–6 h, local)

Plantilla: `jobhunt_core/harvest/providers/native_publicjobs.py` (HTML/JSON
embebido, 131 líneas) y su test `test_native_publicjobs.py`. Regla del
proyecto: **copiar las funciones de parseo del scraper legacy sin importar el
backend** (como hizo `native_rss.py`).

Para **financejobs** (más simple; hacerlo primero):

1. Leer `backend/scrapers/financejobs.py` completo. Funciones a copiar:
   `_job_url_id`, `_extract_jobs_ssr` (rutas conocidas a `jobsSSR`), el bucle
   de `parse_listing_page` que convierte cada job en dict, y `normalize_job`
   (título, empresa, descripción, ubicación, fecha ISO).
2. Crear `jobhunt_core/harvest/providers/native_financejobs.py`:

```python
"""Financejobs.ch via the __NEXT_DATA__ blob; parse copied from the retiring scraper."""

import json, re
from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.provider import (
    BaseProvider,
    ProviderConfigError,
    ProviderResponseError,
)
from jobhunt_core.harvest.types import FetchResult, RawListing

SOURCE_NAME = "financejobs"
BASE_URL = "https://www.financejobs.ch"
LISTING_URL = f"{BASE_URL}/de/jobs"
MAX_PAGES = 10  # = MAX_PAGES del scraper legacy (comprobar)
PAGE_PAUSE_S = 2.0  # = RATE_LIMIT_SECONDS del legacy (comprobar)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _jobs_ssr(data):  # copiar _extract_jobs_ssr del legacy
    ...


def _listing(job):
    job_id = ...  # copiar _job_url_id del legacy
    if not job_id:
        return None
    return RawListing("id:" + job_id, f"{BASE_URL}/de/job/{job_id}", job)


def _content(
    raw,
):  # copiar normalize_job: title, company, description, location, remote=False, tags
    ...


def register_handlers():
    register_normalizer(SOURCE_NAME, _content)
    register_extractor(SOURCE_NAME, lambda raw: (raw.get("title"), raw.get("company")))


class FinancejobsProvider(BaseProvider):
    name = SOURCE_NAME
    SEMANTIC_PARAMS = ()

    def __init__(self):
        register_handlers()

    async def fetch_new(self, params, cursor, http):
        if not isinstance(params, dict) or params:
            raise ProviderConfigError("financejobs scope takes no parameters")
        listings, invalid, pages = [], 0, 0
        for page in range(1, MAX_PAGES + 1):
            async with http.stream(
                "GET",
                f"{LISTING_URL}?page={page}",
                timeout=25,
                headers=BROWSER_HEADERS,
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                body = await response.aread()
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError("financejobs page exceeds byte budget")
            m = _NEXT_DATA.search(body.decode("utf-8", "replace"))
            if not m:
                raise ProviderResponseError("financejobs: no __NEXT_DATA__")
            jobs = _jobs_ssr(json.loads(m.group(1)))
            if jobs is None:
                raise ProviderResponseError(
                    "financejobs: unknown __NEXT_DATA__ structure"
                )
            pages += 1
            if not jobs:
                break
            for job in jobs:
                listing = _listing(job)
                if listing is None:
                    invalid += 1
                    continue
                listings.append(listing)
            await asyncio.sleep(PAGE_PAUSE_S)
        return FetchResult(
            tuple(listings),
            {"pages": pages},
            pages_fetched=pages,
            complete=not invalid,
            error="invalid_items" if invalid else None,
        )
```

   (`BROWSER_HEADERS` = el helper de P6; `asyncio` importado. Adaptar: el
   esqueleto es orientativo, la lógica exacta se copia del legacy.)
3. Registrar en `jobhunt_core/harvest/providers/__init__.py`
   (`FinancejobsProvider.name: FinancejobsProvider()`), y en
   `jobhunt_core/harvest/admission.py` añadir `"financejobs": <ruta del campo
   de fecha en el job>` a `DATE_FIELDS` (sin fecha no hay ventana de
   admisión: obligatorio; si el JSON no trae fecha, declararlo y **no** poner
   `admission_window_days` en su scope).
4. Tests, rojo primero: (a) `test_native_financejobs.py` con un
   `__NEXT_DATA__` de fixture mínimo → 2 listings, identidad `id:`, contenido;
   estructura desconocida → `ProviderResponseError`; (b) paridad pública:
   una sola descarga real de la página 1 con `curl -s "$LISTING_URL?page=1"
   > /tmp/fj.html`, pasar el HTML por el scraper legacy
   (`docker compose exec -T backend python -c "from scrapers.financejobs import
   FinancejobsScraper; from bs4 import BeautifulSoup; ..."`) y por el nativo
   (MockTransport con ese HTML) y comparar `{url: (title, company, location)}`
   → 0 diferencias. Guardar como `docs/audits/NATIVE_FINANCEJOBS_PARITY_2026-09-22.json`.
5. Integración con BD desechable: añadir `"financejobs"` al `parametrize`
   de `test_integration_native_swiss_sources.py` (o copiar su patrón): raw
   persistido, canónica creada, replay sin duplicados.

Para **irishjobs**: mismo esquema. Copiar de `backend/scrapers/irishjobs.py`:
`_PRELOADED_RE` (regex anclada, líneas 48-56), `_extract_balanced_object`,
`_decode_state`, `_items_to_stubs`, `canonical_identity_url` (identidad =
`id:<id de plataforma>`; el host se normaliza a `irishjobs.ie`), `_parse_salary`
(salario en EUR: **dejarlo en `raw`**, `salary: None` en content, como hacen
los JSON nativos), y `normalize_job`. Listado: `/jobs/work-from-home?page=N`,
`remote=True`. Un solo host (irishjobs.ie) basta para paridad de cobertura:
el legacy deduplica por id de plataforma entre los dos hosts. `DATE_FIELDS`:
la fecha viene en el stub (`date`/`datePosted` — comprobar en
`_items_to_stubs`). Paridad pública sobre la página 1 como arriba →
`NATIVE_IRISHJOBS_PARITY_2026-09-22.json`.

Cierre de P7: dirigidas verdes; `git diff --check`; añadir ambas fuentes a la
tabla `ENDPOINTS`/registro; **suite completa del core en serie** (≈15 min)
sobre el árbol inmóvil; pedir aprobación y hacer **un solo commit**
`feat(harvest): port irishjobs and financejobs; expose canton on Swiss native
sources; browser headers for NAV/Jobgether`. Construir la imagen (§II.1) y
desplegar core-api/worker/capture **antes** del paso P8. Verificar `/v1/ready`
con el SHA nuevo y `authoritative: true`. Recibo `$W/p7-release.log`.

### P8 · **[ESCRIBE]** Corte 1: 11 fuentes R5 + irishjobs + financejobs (2,5 h + 25 min de drenaje)

Fuentes del corte 1: `arbeitnow remotive weworkremotely euremotejobs jobspresso
globaljobs zebis publicjobs thehub ostjob zentraljob` (+ `irishjobs financejobs`
que sólo existen en el público). Guardar esta lista en `$W/corte1.sources`.

**8.1 Listas de retirada.** Copiar los tres composes a `.before` y crear
`.candidate` con las listas nuevas:

```sh
ssh nas "cp $E/swissjob.configured.yml $W/public.before.yml; cp $E/r5-source-handover-20260921/worker.coverage-preserved.json $W/r5.before.json"
```

En local, editar copias (`scp` ida y vuelta) — **valor exacto, JSON válido,
nombres exactos del registro** (un nombre desconocido impide arrancar el
worker a propósito):

- Público (`backend` y `worker`, las dos apariciones):
  `LEGACY_DISABLED_PROVIDERS: '["arbeitnow","euremotejobs","globaljobs","jobspresso","ostjob","publicjobs","remotive","thehub","weworkremotely","workingnomads","zebis","zentraljob"]'`
  y añadir `LEGACY_DISABLED_SCRAPERS: '["irishjobs","financejobs"]'`.
  **No** incluir `nav_arbeidsplassen` ni `jobgether` (van en P9). **No**
  incluir `jobicy` en el público (D2: si se conserva la alerta de profesor por
  la vía rápida, tampoco `zebis`/`publicjobs`/`tes`/`schuljobs` — ver D2).
- R5 (`worker.coverage-preserved.json`, clave `services.worker.environment.LEGACY_DISABLED_PROVIDERS`):
  las mismas 12 + `"jobicy"` (ya estaba).

Validar sin arrancar nada:

```sh
ssh nas "$C -p swissjob -f $W/public.candidate.yml config -q && $C -p swissjob-r5 -f $W/r5.candidate.json config -q && echo CONFIG_OK"
```

**8.2 Drenaje de los tres productores legacy** (nada en vuelo; sin purgar):

```sh
for c in swissjob-worker swissjob-worker-r5; do ssh nas "$D exec $c celery -A celery_app inspect active reserved scheduled 2>/dev/null | grep -cE '^\s+\{' "; done
```

- Esperado: `0` en ambos (ninguna tarea activa/reservada/programada). Si hay
  tareas (cosecha en curso): esperar y repetir cada 5 min; **no** matar.
  La cosecha diaria pública corre ~12:30 UTC y la R5 a las 03:50/09:50/15:50/21:50
  UTC: elegir una ventana fuera de esos tramos.

**8.3 Recrear con las listas** (uno a uno, en este orden; instalar el
candidate como configured al final):

```sh
ssh nas "cp $W/public.candidate.yml $E/swissjob.configured.yml && $C -p swissjob -f $E/swissjob.configured.yml stop -t 2400 worker backend && $C -p swissjob -f $E/swissjob.configured.yml up -d --no-deps backend worker"
ssh nas "cp $W/r5.candidate.json $E/r5-source-handover-20260921/worker.coverage-preserved.json && $C -p swissjob-r5 -f $E/r5-source-handover-20260921/worker.coverage-preserved.json stop -t 2400 worker && $C -p swissjob-r5 -f $E/r5-source-handover-20260921/worker.coverage-preserved.json up -d --no-deps worker"
ssh nas "$D ps --format '{{.Names}} {{.Status}}' | grep -E 'swissjob-(backend|worker)'"
```

- Esperado: los tres `Up … (healthy)` o `Up` sin reinicios
  (`$D inspect <c> --format '{{.RestartCount}}'` = 0). Si un worker reinicia
  en bucle → el JSON de la lista tiene un nombre mal escrito: revisar
  `docker logs` (mensaje «Unknown disabled legacy sources») y corregir.

**8.4 Verificar la guarda sin red** (copiar `verify_workingnomads_guard.py`
de `$E` a `$W/verify_guard.py` cambiando `expected` por la lista completa de
cada contenedor y `'workingnomads'` por un bucle sobre la lista):

```sh
for c in swissjob-backend swissjob-worker swissjob-worker-r5; do ssh nas "$D cp $W/verify_guard.py $c:/tmp/vg.py && $D exec $c python /tmp/vg.py $( [ $c = swissjob-worker-r5 ] && echo r5 || echo public )"; done
```

- Esperado por contenedor: `{"disabled": [...], "individual_guard": true,
  "batch_guard": true, "fetches": 0}`. Guardar en `$W/<c>.guard.json`.

**8.5 CDC a cero antes de activar:**

```sh
sqlc "SELECT count(*) AS pendientes FROM shadow_change_log WHERE applied_at IS NULL; SELECT last_applied_lsn, updated_at, heartbeat_at FROM shadow_capture_state;"
```

- Esperado: `pendientes = 0`, `heartbeat_at` reciente (< 5 min).

**8.6 Activar los scopes nativos** — script `$W/native_cutover.py`, copia de
`workingnomads_native_cutover.py` con estos cambios: `SOURCES` = lista del
corte; `PARAMS` por fuente (`{'admission_window_days':7,'legacy_title_filter':True}`
para las que el legacy filtraba títulos —remotive, weworkremotely, euremotejobs,
jobspresso, workingnomads, jobicy—; `{'admission_window_days':7}` para el
resto con fecha en `DATE_FIELDS`; `{}` si la fuente no tiene fecha); en modo
`activate` **quitar** la aserción `assert not EXISTS(harvest_scopes WHERE
enabled)` (ya hay uno: workingnomads) y sustituirla por `assert not
EXISTS(SELECT 1 FROM sources WHERE name=:name)` por fuente; modo `run`/
`replay`/`inspect` en bucle por fuente escribiendo `native.<fuente>.<modo>.json`.
El `inspect` de fuentes no inglesas debe afirmar `canonical == active_listings`
y **no** `language_present` (sólo WN fija `language`).

```sh
ssh nas "$D run --rm --network swissjob_swissjob-net --env-file $W/core.private.env -v $W:/work -v $W/native_cutover.py:/tmp/op.py:ro swissjob-core:point4-<SHA> python /tmp/op.py activate"
ssh nas "$D run --rm --network swissjob_swissjob-net --env-file $W/core.private.env -v $W:/work -v $W/native_cutover.py:/tmp/op.py:ro swissjob-core:point4-<SHA> python /tmp/op.py run"
ssh nas "$D run --rm --network swissjob_swissjob-net --env-file $W/core.private.env -v $W:/work -v $W/native_cutover.py:/tmp/op.py:ro swissjob-core:point4-<SHA> python /tmp/op.py replay"
ssh nas "$D run --rm --network swissjob_swissjob-net --env-file $W/core.private.env -v $W:/work -v $W/native_cutover.py:/tmp/op.py:ro swissjob-core:point4-<SHA> python /tmp/op.py inspect"
```

- Esperado `run`: por fuente `status: ok`, `listings > 0` (ostjob/zentraljob
  varios cientos; jobspresso/euremotejobs pueden dar `ok` con pocas — sus
  feeds son lentos, **no** es fallo). `status: partial` es aceptable sólo si
  `error` es `None` (tope de páginas); con `error` → PARAR esa fuente
  (`UPDATE harvest_scopes SET enabled=false WHERE id=…`) y seguir con el resto.
- Esperado `replay`: `status: skipped` en todas.
- Esperado `inspect`: `enabled=true`, `last_complete_at` de hoy,
  `consecutive_failures=0`, `canonical == active_listings`.
- El `run` de 13 fuentes puede tardar 20–40 min (CH Media pagina 65+20
  páginas con pausa). Ejecutarlo en segundo plano y esperar.

**8.7 Verificación servida** (copiar `workingnomads_catalog_canary.py` y
parametrizar el nombre de la fuente; modo `core` dentro de la imagen core,
modo `bff` dentro de `swissjob-backend`):

```sh
for s in $(cat $W/corte1.sources); do
  ssh nas "$D run --rm --network swissjob_swissjob-net --env-file $W/core.private.env swissjob-core:point4-<SHA> python /tmp/canary.py core $s" > /tmp/canary-$s.json
  ssh nas "$D exec -i swissjob-backend python /tmp/canary.py bff $s" < /tmp/canary-$s.json
done
```

- Esperado por fuente: `{"native_primary_jobs_served": N>0, "port": "CoreCatalog", "application_links_preserved": true, "writes_performed": false}`.
- Feed de los dos perfiles (sólo lectura): mismo comando que usó el acta WN
  (`GET /api/v1/matches?page=1` con credencial real desde el BFF) → 200, 20
  por página, `CoreMatching`. Sin candidaturas ni feedback.
- Recuperación (R6 ya probado): tras el `run`, en ≤10 min el proyector debe
  mostrar `recovery_evaluated: 3`:
  `ssh nas "$D logs swissjob-core-worker-r5 --since 30m | grep shadow.project | tail -3"`.

**8.8 Recibos** en `$W/`: `legacy-guards.json` (los tres), `legacy-drain.jsonl`,
`native.<fuente>.{activated,run,replay,inspect}.json`, `canary.<fuente>.json`,
`public.before/candidate.yml`, `r5.before/candidate.json`.

**Rollback por fuente** (si una fuente falla después): `UPDATE harvest_scopes
SET enabled=false WHERE id='<scope>'`; quitar el nombre de las listas;
recrear el worker correspondiente. El legacy retoma con su cursor. No borrar
lo cosechado.

### P9 · **[ESCRIBE]** Corte 2: nav_arbeidsplassen + jobgether (1,5 h + drenaje)

Precondición: P6 con `complete=True` en el sondeo. Repetir 8.1–8.8 con
`corte2.sources = nav_arbeidsplassen jobgether`, añadiéndolas a las listas
`LEGACY_DISABLED_PROVIDERS` de público y R5. `PARAMS`: NAV necesita un scope
**por faceta** (`{'remote': '<faceta>', 'admission_window_days': 7}`, dos
scopes; los dos valores están en `native_nav.py:22`, `REMOTE_FACETS`);
Jobgether `{'admission_window_days': 7}` **sin `query`**: el legacy llama a
todos los providers con `fetch_jobs("", "Switzerland")`
(`backend/tasks/fetch_tasks.py:247`), así que ningún scope nativo lleva
`query` — vale también para P8. Aceptar `partial` sin `error` en NAV si el
tope de páginas es el mismo que el legacy; con 429 → PARAR, deshabilitar el
scope, devolver la fuente a R5 (quitarla de la lista y recrear) y anotar.

### P10 · R5 colegios (0,5 h rápida / 2 h estricta)

- Rápida (D4): párrafo en el acta: «los 8 scrapers `swiss_schools_*` siguen
  en `swissjob-worker` como colector; leen monitores de core y publican a
  `/school-jobs`; usan `jobs` local sólo como staging (`producer.py:107`)».
  Verificación: `sqlc "SELECT count(*), max(created_at) FROM school_job_details;"`
  → `max` posterior a la última cosecha pública; y
  `sqll "SELECT source, max(first_seen_at) FROM jobs WHERE source LIKE 'swiss_schools_%' GROUP BY 1;"`.
- Estricta: en `backend/services/schools/producer.py` `reconcile`, sustituir
  el `select(Job)` por la iteración sobre `live_hashes` con sus datos ya en
  memoria (el scraper tiene los stubs); test rojo en
  `backend/tests/test_school_producer*.py`: «histórico no observado ya no se
  publica; vivo sí». Suite BFF completa (5,5 min). Imagen del BFF/worker y
  recreación (§II.1 con `backend/Dockerfile.prod`, tags `swissjob-backend:point4-<sha>`
  y `swissjob-worker:point4-<sha>`; el worker usa la misma imagen que el
  backend con otro `command`: comprobar en `swissjob.configured.yml`).

### P11 · R4 Portfolio (0,5 h)

- Verificar y anotar (sólo lectura):

```sh
ssh nas "$D exec portfolio_db psql -U lothar -d proyecto -Atc 'SELECT count(*) FROM saved_searches'"     # 0
ssh nas "$D exec portfolio_redis redis-cli GET daily_match_report_failed_deliveries"                  # 0
ssh nas "$D logs portfolio_backend --since 30h 2>&1 | grep -E 'cache updated|Next circadian' | tail -3"
```

- D3 rápida: párrafo en el acta. D3 estricta: editar
  `$E/portfolio.configured.yml` (copia `.before`) añadiendo
  `LEGACY_DISABLED_SOURCES: '["remotive","jobicy","arbeitnow","weworkremotely","ostjob","zentraljob","thehub","jobgether","nav","irishjobs"]'`
  (nombres exactos de `HarvestSource` en `ReactPortfolio/backend/config.py`),
  imagen con `0f7ac3f` (`docker build -t portfolio-backend:point4-0f7ac3f
  ReactPortfolio/backend`), recrear `backend` del proyecto `portfolio`, y
  comprobar `/health/deep` (admin) muestra las fuentes como transferidas.
  **Avisar al propietario de que esas páginas del tablero dejarán de
  actualizarse.**

### P12 · H2 alerta de profesor (0,5–2 h según D2)

- Rápida: mantener en el público **sin** deshabilitar `zebis`, `publicjobs`,
  `tes`, `schuljobs` (quitarlos de la lista de P8/P13 si se habían puesto) y
  anotar «doble sondeo diario de zebis/publicjobs (RSS/JSON) para la alerta».
- Alternativa (sin lector legacy): crear en core una búsqueda guardada para
  el perfil de P2 con `q` = las palabras de `is_primary_teacher_job`
  (`backend/services/teacher_alert.py`), `notify_frequency='daily'`, vía la
  API del BFF (**el propietario la crea desde la interfaz**, no el agente), y
  después añadir `tasks.alert_tasks.detect_teacher_alerts` a la lista de tareas
  a no despachar (`SCHEDULER_TEACHER_ALERT_INTERVAL_HOURS` — comprobar en
  `backend/services/scheduler.py:209` si existe un flag; si no, dejar la tarea
  y quitar `TEACHER_ALERT_EMAIL` del entorno del backend/worker, que la
  convierte en `no_recipient`).

### P13 · **[ESCRIBE]** Retirada de procesos (3 h + 48 h de observación)

Precondición: P8 y P9 completados con `inspect` verde; dispatcher nativo ha
corrido **al menos una ventana** sola (`ssh nas "$D logs swissjob-core-worker-r5
--since 8h | grep dispatch_native"` → `succeeded` con N scopes despachados).

13.1 Público: listas finales. `LEGACY_DISABLED_PROVIDERS` = **todos** los
nombres de `_PROVIDER_CLASSES` sin credencial requerida (20; los 9 de
`_KEY_REQUIREMENTS` no hace falta listarlos, no se construyen), menos las
excepciones de D2. `LEGACY_DISABLED_SCRAPERS` = `gastrojob stelle_admin tes
schuljobs myscience financejobs irishjobs` (menos D1/D2). Recrear `backend`
y `worker` (8.3). Verificar guard (8.4) y que la cadena diaria siguiente
registra `fetch_providers` con 0 providers y `fetch_scrapers` sólo con
`swiss_schools_*` (`docker logs swissjob-worker` al día siguiente).

13.2 R5 y CDC — **orden obligatorio**:

```sh
# a) última cosecha R5 ya proyectada
sqlc "SELECT count(*) FROM shadow_change_log WHERE applied_at IS NULL;"                    # 0
sqlr "SELECT max(last_seen_at) FROM jobs;"  ;  sqlc "SELECT last_applied_lsn, updated_at FROM shadow_capture_state;"   # updated_at >= max(last_seen_at)
# b) parar productores R5 (sin rm, sin volúmenes)
ssh nas "$C -p swissjob-r5 -f /share/Public/swissjob/docker-compose.rehearsal.qnap.yml stop harvest-trigger frontend"
ssh nas "$C -p swissjob-r5 -f $E/r5-source-handover-20260921/worker.coverage-preserved.json stop -t 2400 worker"
ssh nas "$C -p swissjob-erasure -f /share/CACHEDEV1_DATA/Public/unification-e10.XXkEjw88/e13-erasure/replica.yml stop erasure-cdc"
# c) parar la captura y quitar del beat las tareas del slot
ssh nas "$C -p swissjob-r5 -f $E/core.configured.yml stop core-capture"
```

   Beat: en `jobhunt_core/celery_app.py` envolver `shadow-check-slot-health`,
   `shadow-preview-cycle` y `shadow-run-cycle` en `if settings.CORE_CAPTURE_ENABLED:`
   (flag nuevo, default `True`, `False` en `core.configured.yml`) — **NO**
   tocar `shadow-project`. Test rojo/verde en `test_celery_app*.py`. Release,
   imagen, recrear `core-worker`. Comprobar en logs que `check_slot_health`
   ya no aparece y `shadow.project` sigue cada 5 min con `recovery_evaluated`
   > 0 tras la siguiente ventana de cosecha.

13.3 **48 h de observación** (dos ventanas diarias del dispatcher, el aviso
natural, la cosecha escolar). Comprobaciones diarias:

```sh
sqlc "SELECT s.name, st.last_complete_at, st.consecutive_failures FROM harvest_scopes hs JOIN sources s ON s.id=hs.source_id LEFT JOIN source_scope_state st ON st.scope_id=hs.id WHERE hs.enabled ORDER BY 2 DESC NULLS LAST;"
ssh nas "$D logs swissjob-core-worker-r5 --since 24h | grep -E 'harvest.check_health' | tail -1"     # alertas: []
```

13.4 **Borrar el slot** (irreversible; sólo tras 13.3 sin incidencias y con
«sí» del propietario):

```sh
sqlc "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS wal_retenido FROM pg_replication_slots;"
sqlc "SELECT pg_drop_replication_slot('jobhunt_shadow_r5_rehearsal');"     # active debe ser 'f'
```

   Después, en `core.configured.yml` quitar el servicio `core-capture` y
   `CORE_CAPTURE_SLOT` de los otros dos (copia `.before`), recrear
   `core-api` y `core-worker`. La BD `swissjobhunter_r5_rehearsal` **se
   conserva** (contiene el esquema `jobhunt`, que ES el core). Las tablas
   `public.*` legacy de esa BD y la BD `swissjobhunter` quedan en sólo lectura
   durante el plazo ratificado; no se borran en este punto.

### P14 · Ensayo de recuperación post-corte (1 h)

Reutilizar exactamente el rollback por fuente del paso 8 sobre **una** fuente
de bajo volumen (jobspresso): `enabled=false`, quitarla de la lista pública
(no de R5, ya parado), recrear el worker público, comprobar que
`get_provider('jobspresso')` construye y que su siguiente cosecha diaria
escribe en `swissjobhunter.jobs` — y **volver a dejarla nativa** repitiendo
8.1–8.6 para ella sola. Anotar tiempos. Si se hizo 13.4, anotar en el acta
que la reanudación legacy completa (con CDC) ya no es posible sin snapshot
nuevo: la recuperación es hacia delante (deshabilitar scope + reparar).

### P15 · Acta única (1 h)

Fichero `docs/audits/POINT4_CLOSURE_2026-09-2X.md` con estas secciones y
nada más: (1) imágenes y SHAs desplegados (`docker inspect --format
'{{.Image}}'` de los 6 contenedores vivos); (2) tabla fuente → scope id →
`last_complete_at` → listings del primer run → replay → canary; (3) excluidas
con causa (cifras de §2.2); (4) decisiones D1–D5 con la opción elegida; (5)
R1 con `event_id` o «sin evento» y fecha; (6) R6 con la línea de log; (7)
procesos parados y beat resultante; (8) slot: borrado/pendiente; (9)
rollback ensayado (P14) con tiempos; (10) lo que queda abierto (fuentes en
R5 si P6 falló, alerta profesor si D2 estricta pendiente). Actualizar sólo la
cabecera «Estado documental vigente» del runbook, §43 de ESTADO y la fila
A18-06 de DEUDA; no reescribir actas anteriores. Commit sólo con aprobación.

---

## II.3 Tabla de parada rápida (qué hacer cuando algo no cuadra)

| Síntoma | Causa probable | Acción |
|---|---|---|
| Worker reinicia en bucle tras recrear | nombre mal escrito en `LEGACY_DISABLED_*` | `docker logs` → «Unknown disabled legacy sources»; corregir JSON; recrear |
| `run` devuelve `status: stale` | otro run (dispatcher) reclamó el scope | esperar a que termine (`source_harvest_runs`), repetir `run` con otro `run_key` |
| `run` devuelve `error` con 403/429 | cabeceras/ritmo distintos del legacy | deshabilitar scope; fuente vuelve a lista legacy; anotar; no reintentar en bucle |
| `inspect`: `canonical < active_listings` | normalizador devolvió `None` (sin título) para algunos raws | listar `source_listing_revisions` sin `offer_revisions` de esa fuente; si son pocos, aceptable y anotar; si muchos, PARAR |
| `shadow_change_log` pendientes > 0 al ir a activar | cosecha R5 en curso | esperar; no activar hasta 0 |
| Proyector no muestra `recovery_evaluated > 0` tras un run nativo | no hubo corpus nuevo (todo refrescos) o embeddings pendientes aún drenando | esperar 2 ciclos (10 min); comprobar `SELECT count(*) FROM offer_embeddings WHERE …` pendientes vía `jobhunt.embedding.run_pending` en logs |
| Canary BFF devuelve 404 para un id nativo | catálogo servido no es `CoreCatalog` o la vacante no tiene canónica | comprobar `jobhunt_routing.catalog = core_primary`; `inspect` de la fuente |
| Alerta de profesor deja de enviar | D2 rápida mal aplicada (zebis/publicjobs deshabilitados en público) | quitar esos nombres de la lista pública y recrear |
| Cualquier `Traceback` en un paso [ESCRIBE] | — | PARAR, guardar log completo en `$W`, no repetir el comando, preguntar |
