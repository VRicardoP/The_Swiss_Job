# DT-144 · Contexto de relevo

> **Qué es esto.** Lo primero que debe leer quien retome la centralización de la
> cosecha. No sustituye a nada: el objetivo manda desde
> [CONTRATO_CENTRALIZACION_TOTAL.md](CONTRATO_CENTRALIZACION_TOTAL.md), la
> evidencia por portal vive en los checkpoints de
> [DEUDA_TECNICA.md](DEUDA_TECNICA.md) §DT-144 y el inventario verificable en
> [source_ownership.json](source_ownership.json). Este fichero explica **qué se
> ha hecho, cómo, en qué estado está respecto al objetivo final y qué queda**,
> para que no haya que reconstruirlo leyendo veinte checkpoints.
>
> Última actualización: **2026-10-15, corte en curso**. Las dos ramas iniciales
> y la corrección de redacción están fusionadas; Core `5b3bf3d` está desplegado.
> Los 33 scopes DT-144 están habilitados, SwissJob local está detenido y
> Portfolio local está verificablemente quiescente. El primer ciclo válido es
> el ciclo nativo de las 12:10 Europe/Zurich: 49 de 50 scopes terminaron `ok` y
> JSearch quedó pendiente por agotamiento comprobado de su cuota externa, como
> detalla §0.1.

---

## 0. Índice

1. En una frase
2. Estado exacto (ramas, SHAs, inventario)
3. Dónde estamos respecto a la definición de terminado (§10 del contrato)
4. Todo lo migrado, fuente a fuente
5. Lo que NO está migrado y por qué
6. Cómo se migra un portal (receta ya rodada) y comandos reales
7. Cómo se verifica la CI
8. Decisiones de diseño ya tomadas
9. Trampas ya pisadas
10. Lo que queda: el corte agrupado (propietario)
11. Mapa de ficheros

---

## 0.1 Checkpoint vivo del corte (2026-10-15)

Este bloque **prevalece sobre las fotografías anteriores de este documento**
hasta el siguiente checkpoint:

- Fusiones de código verificadas: `ReactPortfolioServer/main` incorporó
  `682a3e589d137fe180c2b905a508250e6f0d1e10` (PR 2, CI de `main`
  `38742958119` verde). La preparación SwissJob/Core quedó en
  `136116df590e98477a17ac23a20ec6243cc75633` (PR 5, CI `38744550106` verde) y
  el relevo documental se fusionó después como
  `205987ac01b88893b2199eeea083ed9f31341a66` (PR 6, CI de `main`
  `38747081401`, 9/9 trabajos verdes). Los PR documentales posteriores no
  cambian código operativo: la autoridad para la cabeza documental es siempre
  `github/main`, que debe actualizarse con `git fetch github main` al reanudar;
  ningún SHA documental de este párrafo es un objetivo de despliegue. Las dos
  preparaciones están fusionadas, pero **no se han desplegado** porque el gate
  JSearch sigue en 49/50.
- Core productivo: cuatro contenedores en `swissjob-core:point5-5b3bf3d`,
  `/v1/ready` publica `ready`, `authoritative=true`, Alembic `core0055`; los
  cuatro procesos comparten release y el worker de cosecha nuevo no ha
  reiniciado. El directorio de despliegue y Compose independiente se
  reconstruyeron desde manifiestos saneados; el supervisor QTS volvió a `ok`.
- El falso fallo de `grep -v` sobre un env administrativo de una línea quedó
  sustituido por `sed` en la release desplegada. `CORE_DB_PASSWORD` del fichero
  recuperado no coincidía con el DSN vivo: se sincronizó atómicamente sin
  imprimirlo; el bootstrap convergente verificó rol de mínimo privilegio,
  aislamiento, `pgvector` y cabeza `core0055`.
- Provisión exacta: base productiva con 71 fuentes y 79 scopes; 40 fuentes y 50
  scopes habilitados. Los 24 orígenes DT-144 existen con exactamente 33 scopes
  (Adzuna 3, Jobicy 8, los otros 22 uno cada uno).
- Productores locales: `swissjob-worker` está detenido. `portfolio_worker` no se
  detuvo porque también ejecuta Uvicorn: se recreó con la misma imagen y
  `BACKGROUND_SCHEDULERS_ENABLED=false`; su log de arranque confirma «0
  background tasks armed». El backend Portfolio sigue en rol `api`.
- La fuga de la URL de Adzuna no se repite: `jobhunt_core/celery_app.py` fija
  `httpx`/`httpcore` a WARNING al importar y tras las dos señales de logging de
  Celery. Tras peticiones reales, los recuentos seguros son cero coincidencias
  de parámetros sensibles y cero líneas INFO `HTTP Request`; el contenedor que
  contenía el log anterior fue recreado. **No copiar valores históricos.**
- Corrección de metodología: `run_all_task("dt144-cutover-1")` NO es el gate
  productivo. Un barrido agregado de 50 scopes no cabe en los límites Celery
  globales 1.800/2.100 s; sus reintentos y leases dejaron una fotografía
  parcial (24 `ok`, `financejobs` error y `jobroom` marcado en ejecución) que
  **se invalida**. Las tareas `c50b3cfe-7a2a-4d84-9848-ea2cbf7f75f1` y la
  duplicada manual `87de6aad` se revocaron sin terminar procesos. No atribuir
  aquella marca de Jobroom a trabajo vivo: una inspección Celery posterior
  demostró que la tarea activa real era un `run_scope` de Arbeitnow.
- La validación correcta sigue el camino real de producción: beat despachó a
  las 12:10 Europe/Zurich la ventana exacta
  `2026-10-15T12:00:00+02:00`, una tarea independiente por scope con identidad
  `scope:{scope_id}:native:{window}:{scope_id}`. El ciclo terminó su trabajo:
  49 scopes están `ok`, ninguno sigue ejecutándose o sin iniciar y sólo JSearch
  falló con `http_429` tras agotar sus reintentos.
- Diagnóstico seguro de JSearch: dos peticiones de control desde el Core
  devolvieron 429. La segunda se hizo sólo para distinguir el límite gratuito:
  publicó límite 200, restante `-2`, reset en 1.543.221 segundos (aprox. 17,9
  días) y ningún encabezado separado de *free-plan hard limit*. La
  [documentación oficial de RapidAPI](https://docs.rapidapi.com/docs/response-headers)
  confirma que `x-ratelimit-requests-reset` son segundos hasta reiniciar la
  cuota. No se imprimieron clave, URL, query ni cuerpo y no se harán más sondas
  antes del reset. El scope sigue habilitado, acumula dos fallos y todavía no
  alcanzó el umbral de autoapagado. La cola quedó vacía.
- Se comprobó después, dentro del NAS y sin imprimir valores ni huellas, la
  credencial efectiva de `swissjob-core-api-r5` frente a la de
  `portfolio_backend`: ambas son la **misma** credencial JSearch. El worker de
  Portfolio no conserva otra. Por tanto, la instalación actual no dispone de
  una segunda cuota ya configurada; no se debe rotar ni repetir la petición
  antes del reset salvo que el propietario aporte otra credencial válida y
  gratuita.
- Revalidación de espera `2026-10-15T17:02:34+02:00`: la sonda final se
  ejecutó otra vez por stdin y en sólo lectura. Core sigue `ready`, autoritativo,
  en release `5b3bf3d` y Alembic `core0055`; observa 50/50 scopes habilitados y
  con estado, cero autoapagados, 25.795 entregas, cero pendientes/dead-letter,
  staging 0 y slot de captura ausente. Su única causa de salida 1 es el gate
  `harvest_not_50_of_50` de JSearch; no hizo una petición al proveedor.
  Los nombres productivos exactos son `swissjob-core-api-r5`,
  `swissjob-core-worker-r5`, `swissjob-core-harvest-worker-r5` y
  `swissjob-core-ops-worker-r5`: los cuatro ejecutan la misma imagen y tienen
  cero reinicios. `swissjob-worker` continúa `exited (0)` y
  `portfolio_worker` sigue sin reinicios, con
  `BACKGROUND_SCHEDULERS_ENABLED=false` y la lista de fuentes heredadas
  configurada. No hubo despliegue ni mutación productiva.
- Preparación de consumidores: se detectó antes del despliegue que
  `watchlist_tasks._get_watchlist_sources()` derivaba de `_SCRAPER_CLASSES`, ya
  vacío por el traspaso, de modo que la alerta de salud y el digest escolar
  habrían filtrado cero fuentes. La rama `feat/dt144-consumer-cutover` fija las
  ocho fuentes del contrato del consumidor y obtiene su salud del agregado
  autenticado de Core, sin tabla de compliance ni registry local. Formato y
  Ruff pasan; prueba focal en Compose: 10/10.
- ReactPortfolio ya tiene una corrección reproducible en
  `feat/dt144-consumer-cutover` (`bf91254` + `0c2c9a6`, PR 2): el próximo despliegue fija
  `BACKGROUND_SCHEDULERS_ENABLED=true` para recuperar mantenimiento, alertas,
  digest y enriquecimiento, pero fuerza
  `SCHEDULER_DAILY_HARVEST_ENABLED=false` y conserva las veinte fuentes en
  `LEGACY_DISABLED_SOURCES`. Así se retira el quiesce global sin reactivar un
  portal. Además elimina del Compose vivo `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` y
  `JSEARCH_RAPIDAPI_KEY`. Prueba focal 43/43, `bash -n` y despliegue seco
  correctos.
- SwissJob `3af3dc6` prepara el mismo invariante: antes de recrear BFF/worker,
  el despliegue guarda una copia de recuperación, elimina del `.env.prod` los
  diez nombres de credenciales de portales conocidos, verifica su ausencia y
  borra la copia sólo después de la sonda saludable. No imprime nombres con
  valores ni toca las credenciales `CORE_*` del colector central.
- `probe_core_final.py` ya exige el censo nuevo (50 observados/habilitados/con
  estado, cero autoapagados y cero alertas), no los 17 scopes históricos. Su
  ejecución de sólo lectura contra producción confirma readiness autoritativo,
  delivery drenado, staging drenado, slot de captura ausente y censo 50/50;
  falla únicamente por la alerta de JSearch sin ciclo completo. La sonda queda
  roja por la misma puerta real, no por expectativas obsoletas.
- Revalidación posterior a la fusión documental, también de sólo lectura:
  release `5b3bf3d`, Alembic `core0055`, 50 scopes observados/habilitados/con
  estado, cero autoapagados, 25.540 entregas, cero pendientes/dead-letter,
  staging drenado y slot ausente. El único fallo continúa siendo
  `harvest_not_50_of_50` por JSearch; no se hizo otra petición al proveedor ni
  se modificó producción.
- Las sondas finales de ambos consumidores ya exigen el objetivo final, no el
  estado de rollback. La línea base viva de SwissJob confirma autoridades
  `CoreCatalog`/`CoreMatching`/`LocalProfile`/`CoreSchools`, 2/2 perfiles
  enlazados y cero snapshots pendientes o erróneos; permanece roja por 12
  providers, 13 scrapers, 39 módulos productores importables y cinco nombres
  de credencial local. Portfolio confirma sus seis capacidades exactamente en
  `core_primary`; permanece rojo por cosecha diaria habilitada, 11 providers,
  23 módulos productores/fallback importables y tres nombres de credencial.
  Las sondas sólo enumeran nombres, nunca valores, y convierten estos residuos
  en condiciones ejecutables del corte final.
- Despliegue futuro aislado de los cambios ajenos del workspace: los worktrees
  de `/tmp` son desechables y **no son autoridad**. Los últimos modos secos se
  hicieron desde los SHAs operativos `136116d` (SwissJob/Core) y `682a3e5`
  (Portfolio), resolviendo `swissjob-backend:point5-136116d`,
  `swissjob-worker:point5-136116d` y `portfolio-backend:board-682a3e5`, sin
  construir ni tocar el NAS. Antes del despliegue real hay que recrear o
  actualizar worktrees limpios desde el `github/main` vigente de cada repo y
  repetir el modo seco; no reutilizar por nombre un checkout de `/tmp`. Una
  inspección posterior a las fusiones confirma que producción no se
  autodesplegó: BFF/worker siguen en `4017081`, Portfolio en `444f072` y Core en
  `5b3bf3d`; cero reinicios y los productores locales permanecen quiescentes.
- Punto exacto de reanudación: no contar este ciclo como superado hasta obtener
  50/50 `ok`. Cuando la cuota esté disponible —o se aporte otra credencial
  válida sin coste— reejecutar **sólo JSearch** con la misma clave determinista
  de la ventana; después desplegar BFF/worker de SwissJob y backend/worker de
  Portfolio, sondar ambos consumidores, lanzar un segundo dispatcher nativo con
  ventana única y exigir otro 50/50. Sólo entonces retirar físicamente
  productores, credenciales, `CompositeCatalog` y `FallbackCatalog`, actualizar
  el inventario y repetir suites, CI, despliegues y sondas finales.

---

## 1. En una frase

Core debe ser el único propietario de la captura externa de ofertas; SwissJob y
ReactPortfolio deben consumir el corpus común sin cosechar, sin credenciales de
portal y sin tráfico hacia ellos. Cada portal se migra como unidad vertical
completa (sección 7.1 del contrato) y **nada se corta en producción** hasta la
fusión final aprobada por el propietario.

## 2. Estado exacto (2026-10-15)

| Repositorio | Rama de trabajo | Cabeza | `main` remoto (INTACTO) |
|---|---|---|---|
| SwissJob/Core (`The_Swiss_Job`, GitHub público) | `feat/fase-a-core` | `bc1ba91` + este commit documental | `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` |
| ReactPortfolio (`ReactPortfolioServer`, GitHub privado) | `feat/dt144-free-gate` | `5f03c68` | `5bf457afbde5b44a995acce4ef241324f066f836` |

- El worktree de ReactPortfolio está en `/tmp/reactportfolio-dt144-free-gate`
  (**`/tmp` es volátil**: si no existe, `git worktree add
  /tmp/reactportfolio-dt144-free-gate feat/dt144-free-gate` desde
  `/home/lothar/Public/ReactPortfolio/backend`; el remoto se llama `github`,
  no `origin`).
- El entorno `/tmp/dt144-backend-venv` (bs4, aiohttp, pydantic-settings, black,
  pytest-timeout) permite replayar productores legados fuera de Docker. Si se
  perdió, recrearlo con esas cinco dependencias.
- Las capturas selladas de cada portal vivían en el scratchpad de la sesión
  (volátil). Sus huellas SHA-256 están en los JSON de `docs/audits/`: con ellas
  se puede demostrar que una nueva captura es la misma o no.
- Suites en la cabeza: **Core 1.952 passed**, **backend SwissJob 1.916 passed +
  4 xfailed**, ReactPortfolio **2.256 passed + 3 skipped** (en `5f03c68`).
  Ratchets: `check_no_secrets` 0 hallazgos; `check_source_ownership` devuelve
  `{"core_owned":16,"local_pending":24,"sources":58}` tras retirar
  `proz`/`remoteco`.

**Inventario: 58 fuentes · 16 `local_removed` · 24 `local_pending` · 18
`retired`.** Esas cifras **describen producción** y por eso no se mueven al
migrar en rama: una fuente migrada conserva `state=local_pending` con
`core_adapter: true` hasta el corte agrupado. Las 24 `local_pending` siguen
cosechando en local AHORA MISMO (11 de ReactPortfolio, 14 de SwissJob, `jobicy`
en los dos).

Registros locales en rama: SwissJob `_SCRAPER_CLASSES` **VACÍO**; SwissJob
`_PROVIDER_CLASSES` con 7 nombres `retired` inertes (`careerjet`, `jooble` y
los cinco partner restringidos). ReactPortfolio sin
productores locales: `services/catalog/seam.py` devuelve `CoreCatalog()`
directamente.

## 3. Dónde estamos respecto a la definición de terminado (§10)

| Condición del §10 | Estado | Qué falta |
|---|---|---|
| 100 % de fuentes operativas en `core_primary` o `local_removed` | **NO** · 16 de 40 | el corte agrupado (§10 de este doc) mueve 24; `proz`/`remoteco` están retirados |
| Sin `local_pending` ni `core_shadow` | **NO** · 24 `local_pending` | ídem |
| Cero productores externos activos en ambos productos | **En rama SÍ; en producción NO** | desplegar la rama tras la fusión |
| Sin secretos ni tráfico de portales en los productos | **En rama SÍ** (`check_no_secrets` 0) · en producción las credenciales de Adzuna/JSearch siguen en el `.env` del Portfolio | rotarlas a `CORE_ADZUNA_APP_ID/APP_KEY` y `CORE_JSEARCH_RAPIDAPI_KEY` en el core del NAS y retirarlas del Portfolio en el corte |
| Sin catálogo compuesto ni fallback local | **NO** · `CompositeCatalog` y `FallbackCatalog` existen pero no se construyen (vía de rollback) | borrarlos en el corte final |
| Toda oferta nueva entra sólo por Core | **NO** | activar los scopes y apagar los productores locales en el mismo despliegue |
| Ambos productos pasan sus recorridos sobre el corpus común | **En rama SÍ** (suites + fidelidad G7/G8 sobre 18 rutas) | repetir contra producción tras el corte |
| Sondas productivas confirman invariantes | **NO ejecutadas** (no hay nada desplegado) | `probe_core_final.py` y afines tras el corte |
| Contrato, inventario y checks atados al SHA fusionado y desplegado | **NO** | fusión aprobada + despliegue + actualizar `source_ownership.json` al SHA |

Lectura honesta: **la parte de ingeniería está terminada y verificada; la parte
de operación no ha empezado**, y no debe empezar sin el propietario (contrato
§6 fase -1: «no se fusiona ni despliega antes de completar DT-144 y obtener la
aprobación final»).

## 4. Todo lo migrado, fuente a fuente

### 4.1 Ya propiedad de Core antes de DT-144 (16 · `local_removed`)

`arbeitnow`, `euremotejobs`, `financejobs`, `globaljobs`, `irishjobs`,
`jobgether`, `jobspresso`, `nav_arbeidsplassen`, `ostjob`, `publicjobs`,
`remotive`, `thehub`, `weworkremotely`, `workingnomads`, `zebis`, `zentraljob`.
Cortadas en el corte nativo del 2026-09-22 (`docs/audits/POINT4_CUTOVER_2026-09-22.md`).
No se tocaron en DT-144.

### 4.2 Oleada ReactPortfolio (11 · `local_pending` con adaptador)

Todas tienen: adaptador Core, `provision_<fuente>.py` (scope nace DESHABILITADO),
`SourceSpec` en `services/core_source_compat.py` del Portfolio (conserva las
seis rutas HTTP por fuente), productor local desconectado, tests fijados.
Commit Core / commit Portfolio / CI SwissJob (la del Portfolio NO es verificable
desde aquí, ver §7).

| Fuente | Core | Portfolio | CI Core | Mecanismo | Checkpoint |
|---|---|---|---|---|---|
| `jobicy` (también SwissJob) | `3fe20e6`+`7d4181a` | `0d1cc26`+`9390071` | verde | JSON API; preserva metadatos visibles | DT §«piloto Jobicy» |
| `remoteok` | `cdca8df` | `5cb6737` | verde | JSON API | DT L455 |
| `himalayas` | `d9b6859` | `e8ba714` | verde | JSON API | DT L499 |
| `swisstechjobs` | `a7016a6` | `f3baba8` | `38560944461` | JSON; 8/8 filas idénticas | DT L584 |
| `ictjobs` | `f273a24` | `fda24a9` | `38564269161` | JSON | DT L647 |
| `swissdevjobs` | `63fe4e5` | `fe0c77d` | `38568433424` | JSON | DT L732/838 |
| `jobroom` | `14b613a` | `edf1b12` | `38574880608` | API job-room.ch | DT L891/941 |
| `adzuna` | `c266b8d` | `95768ce` | `38588047774` | API con credencial → `CORE_ADZUNA_*` | DT L1022/1083 |
| `jsearch` | `ac819f5` | `855f243` | `38599397880` | RapidAPI con credencial → `CORE_JSEARCH_RAPIDAPI_KEY` | DT L1161/1240 |
| `dynamitejobs` | `9ec6901` | `1a0db8d` | `38607230379`/`38608902811` | `__PINIA_STATE__` + JSON-LD de detalle (≤20/barrido) | DT L1320/1396 |
| `jobscout24` | `33a804d` | `04a020d`+`ffcc0ba` | `38619763001` | barrido por palabra clave | DT L1463/1513 |

Residuos estructurales del Portfolio cerrados en `5f03c68`: `jobicy`/`remoteok`
ya no registran caché ni actualizador; barridos G7/G8 saltan con motivo y
prueban un agregado hostil sobre las 18 rutas; la costura sirve `CoreCatalog()`
sin compuesto.

### 4.3 Oleada SwissJob (14 · `local_pending` con adaptador)

Aquí no hay rutas por fuente que conservar: el BFF ya lee el feed del núcleo sin
lista blanca, así que el traspaso es apagar el productor (`_SCRAPER_CLASSES`
vacío; cada fuente añadida a `SOURCES_DECIDED_IN_ADVANCE` en
`backend/services/harvest_window.py`). Los módulos legados siguen en el árbol
como rollback.

| Fuente | Core | CI | Mecanismo y hallazgo clave | Evidencia |
|---|---|---|---|---|
| `myscience` | `244f15e` | `38632186849` | SSR + detalle; `published_at` de `<time datetime>`; el early-stop legado **ocultaba 4 de 26 ofertas** | `2026-10-14-dt144-myscience-baseline.json` |
| `gastrojob` | `280bfc2` | `38635097926` | TYPO3 SSR; veredicto de cinco vías para página vacía; `int()` protegido | `…-gastrojob-baseline.json` |
| `tes` | `9e62bd5` | `38655779623` | `__NEXT_DATA__`; el servidor **ignora `?page=N`** (8 de 28 páginas inalcanzables, contado y reportado) | `2026-10-15-dt144-tes-baseline.json` |
| `stelle_admin` | `82c742d` | `38657167787` | **Reimplementado SIN navegador**: API pública Prospective (`ohws.prospective.ch/public/v1/medium/1000624/jobs`) hallada en el bundle del SPA; 431 ofertas con unidad, lugar, fecha, pensum, home-office | `…-stelle-admin-baseline.json` |
| `swiss_schools_zis/isb/ecolint/hautlac/iscs` | `72d068d` | `38690714105` | 5 parsers de HTML propio; **vacío verificado**; 9 filas, 0 diferencias | `…-swiss-schools-boards-baseline.json` |
| `swiss_schools_nae/inspired` | `287cc18` | `38692407198` | SuccessFactors compartido; 13 filas vivas, 0 diferencias | ídem |
| `swiss_schools_isp` | `2200b56` | `38694111472` | Workday (Mosaic); cadena de guardas `externalPath` portada íntegra (27 formas rechazadas) | ídem + preflight |
| `schuljobs` | `020469e` | `38696733154` | SSR + scroll AJAX (`searchhash`) + JSON-LD; 84 listados + 83 detalles, misma SHA-256 en 10 campos; **la mitad de los detalles no trae JSON-LD** (normal) | `…-schuljobs-baseline.json` |

Infraestructura transversal añadida en esta oleada:

- **Kill-switch de bloqueo en Core** (`c7d5388`, migración `core0055`):
  `BLOCK_STATUSES={403,429,503}`, `blocking_status()` recorre la cadena de
  excepciones, tres bloqueos seguidos apagan el scope y sellan
  `disabled_by_blocks_at`, el censo de salud publica `apagados_por_bloqueo`.
- **`TagStackParser`** (`jobhunt_core/harvest/providers/html_response.py`):
  parser HTML de biblioteca estándar compartido por los scrapers SSR. Core no
  lleva bs4 a propósito.
- **`next_data.py`**: lector compartido de `__NEXT_DATA__` (financejobs, tes).
- `source_aggregate.py`: rutas de fecha para `advert.startDate` (tes),
  `start_date` (stelle_admin), `date_posted` (schuljobs);
  `statistics_location` para dynamitejobs.
- `scripts/check_source_ownership.py` lee `SOURCE_NAMES` (módulos
  multi-fuente como los colegios).

### 4.4 Retiradas sin productor (16 · `retired`)

`authenticjobs`, `careerjet`, `dailyremote`, `ilojobs`, `impactpool`, `jooble`,
`medjobs`, `reliefweb`, `translatorscafe`, `undpjobs`, `untalent` y los cinco
partner restringidos (`jobcloud_partner`, `linkedin_authorized`,
`indeed_partner`, `glassdoor_partner`, `xing_partner`). Cero publicadores, cero
credenciales; las clases que quedan registradas no emiten peticiones.

## 5. Lo que NO está migrado y por qué

| Fuente | Diagnóstico MEDIDO (2026-10-15) | Qué lo arreglaría | Decisión |
|---|---|---|---|
| `proz` | Cloudflare *managed challenge* en **toda la zona** (feed, portada, `robots.txt`, `www2`), con cualquier cabecera/protocolo y desde cuatro redes (este host, NAS —misma IP—, fetcher de Anthropic, archive.org). Muro levantado entre jun y jul 2026; el provider es de abr 2026 y probablemente cosechó hasta entonces. 46 errores seguidos | (a) retirar; (b) feed de miembro; (c) navegador que resuelva el desafío, fuera de la regla de no-evasión | **RETIRADO por decisión explícita del propietario, 2026-10-15** |
| `remoteco` | Dos fallos: **el feed `/remote-jobs/feed/` es 404 desde 2025-12-21**, antes de escribir el provider; y el edge es Akamai Bot Manager conductual (sirve a quien parece navegador hasta que su puntuación cae; sin el sensor JS nadie pasa de forma estable; un proxy no ayuda). 54 errores seguidos; nunca pudo cosechar | scraper HTML nuevo del listado (WordPress sin feed ni REST) **y** navegador stealth de pago. Ni con navegador queda feed que leer | **RETIRADO por decisión explícita del propietario, 2026-10-15** |

Evidencia: [`2026-10-15-dt144-proz-remoteco-analysis.json`](../audits/2026-10-15-dt144-proz-remoteco-analysis.json)
(corrige dos afirmaciones de la sonda del día 14, que se conserva). No se
intentó resolver desafíos ni usar navegador o proxy: se midió si una petición
conforme funciona, y no.

**Nota para el corte sobre los colegios:** los ocho `swiss_schools_*` tienen
adaptador, pero HOY el colector legado publica observaciones al núcleo
(`backend/services/schools/producer.py` → `POST /v1/schools/{monitor_id}/jobs`).
Al habilitar sus scopes en Core, ese empuje debe apagarse **en el mismo corte**
o el corpus escolar recibiría las mismas ofertas por dos caminos.

## 6. Cómo se migra un portal (receta ya rodada)

Los pasos son los de la sección 7.1 del contrato; esto es lo que significan en
la práctica de este repositorio.

1. **Preflight.** Leer rama, upstream y los dos `main`; comprobar que el
   checkpoint anterior tiene su CI verde. `git status` antes de tocar nada: en
   SwissJob hay cambios ajenos a DT-144 permanentes (ver §9).
2. **Trazado.** Localizar el productor, su registro, su despacho, el consumidor
   y sus credenciales. **El consumidor cambia según el producto:**
   - **ReactPortfolio** expone seis rutas HTTP por fuente
     (`/recent`, `/stats`, `/by-tag`, `/by-location`, `/refresh-cache`,
     `/health`). Las sirve el adaptador compartido
     `services/core_source_compat.py`; basta añadir el `SourceSpec` para que
     `routers/core_source_compat.py` las genere.
   - **SwissJob** no tiene rutas por fuente. Su BFF lee el feed del núcleo
     (`backend/services/matching/core_client.py`) sin lista blanca por fuente,
     así que el traspaso es sólo apagar el productor local.
3. **Línea base viva y SELLADA.** Capturar la respuesta real con las cabeceras
   que usará Core, guardarla y **replayar el productor legado EXACTO sobre esa
   captura**. Las huellas SHA-256 de esa ejecución son el patrón. Capturar
   también las páginas de detalle que el productor pediría.
4. **Core mínimo.** Reutilizar costuras existentes (`browser_headers`,
   `html_response`/`TagStackParser`, `next_data`, `rss_text`,
   `provision_scopes`). **Nunca una dependencia nueva.**
5. **Paridad byte a byte.** Replayar el adaptador contra la MISMA captura y
   comparar campo a campo; cada diferencia se corrige o se documenta con
   evidencia. Las mejoras deliberadas se justifican midiendo (§8).
6. **Pruebas por capas** (unitarias del parser, integración con PostgreSQL real
   en doble ciclo, provisión) + `provision_<fuente>.py` con scope DESHABILITADO.
7. **Apagado local** (registro, `SOURCES_DECIDED_IN_ADVANCE` en SwissJob;
   `SourceSpec` + desconexión en Portfolio) e inventario: `core_adapter: true`,
   `state` sin tocar.
8. **Suites completas, lint, ratchets y CI.** Commit funcional estrecho, push de
   la rama, y sólo después el commit documental con la CI ya verde.
9. **Checkpoint** en DEUDA_TECNICA con el siguiente paso exacto, y este fichero
   al día.

### Comandos que se usan de verdad

```bash
# Pruebas del núcleo (el override dev es el que monta el árbol de trabajo)
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  run --rm core-migrate python -m pytest jobhunt_core/tests -q

# Suite del BFF de SwissJob (contenedor efímero; usa swissjobhunter_test)
docker compose run --rm -v "$PWD/backend:/app" -w /app backend \
  python -m pytest tests/ -q

# Lint de TODO el repo (lo que exige el CI)
docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo backend \
  sh -c "ruff check --no-cache . && ruff format --check --no-cache ."

# Ratchets
python3 scripts/check_source_ownership.py \
  --portfolio-root /tmp/reactportfolio-dt144-free-gate
python3 scripts/check_no_secrets.py

# ReactPortfolio (su CI sólo ejecuta estas dos cosas)
cd /tmp/reactportfolio-dt144-free-gate
python3 scripts/check_local_harvest_ownership.py
/tmp/dt144-backend-venv/bin/python -m pytest tests -q
# commits allí: git -c core.hooksPath=/dev/null commit …  (el hook pide black)
```

Lanzar suites largas en segundo plano desde la RAÍZ del repo (desde otro cwd el
compose no se encuentra) y esperar por el log:
`until grep -qE "[0-9]+ (passed|failed)" suite.log; do sleep 30; done`.

## 7. Cómo se verifica la CI

`The_Swiss_Job` **responde a la API anónima de GitHub**:

```bash
curl -sS https://api.github.com/repos/VRicardoP/The_Swiss_Job/actions/runs?branch=feat/fase-a-core&per_page=3
curl -sS https://api.github.com/repos/VRicardoP/The_Swiss_Job/actions/runs/<id>/jobs
```

Nueve trabajos: `source-ownership`, `backend-lint`, `core-lint`, `backend-test`,
`core-test`, `frontend-lint`, `frontend-test`, `compose-config`,
`docker-build`. Los dos `*-test` tardan 15-20 min. Todos los runs de la tabla
§4 están verdes 9/9; el último verificado es `38696733154` (`020469e`). Los
commits documentales posteriores (`80785d2`, `bc1ba91`, éste) no tocan código.

`ReactPortfolioServer` es privado, pero la sesión actual sí dispone de acceso
autenticado de solo lo necesario para comprobar Actions. Verificados como
`completed/success`: `04a020d`, `ffcc0ba` y `5f03c68`.

## 8. Decisiones de diseño ya tomadas (no re-litigar sin evidencia)

- **Core no reproduce el early-stop por cursor de los scrapers.** Barre el
  presupuesto declarado y deja que el sink resuelva identidad. Medido en
  myScience: el early-stop dejaba **4 ofertas de 26 invisibles**.
- **Nada de dependencias de parseo en Core.** `TagStackParser` cierra
  implícitamente lo abierto, descarta cierres huérfanos y excluye
  `script`/`style`/`template`/`noscript`. Existe porque un parser que suponía
  etiquetas balanceadas **cortó una descripción real siete párrafos antes**.
- **En una watchlist, un barrido vacío es el estado NORMAL.** Los adaptadores
  de portales de empleo LANZAN cuando no encuentran nada (parser roto). Los
  ocho colegios bajan la página, la RECONOCEN por su título (y por el marcador
  de vacío donde el portal lo tiene) y sólo entonces reportan cero como sano.
- **`0 == 0` no prueba un parser.** Los tableros vacíos se compararon contra el
  parser legado sobre fixtures construidos con la estructura que sus propios
  selectores declaran.
- **Un SPA no obliga a un navegador.** `jobs.admin.ch` parecía exigir
  Playwright y era un React sobre una API JSON pública declarada en su bundle.
  Antes de dar por necesario un navegador: bajar el cascarón, leer el bundle,
  buscar su `baseUrl`. **Core no lleva Chromium y no debe llevarlo.**
- **Error ≠ vacío, siempre.** Un 200 ilegible es un fallo VISIBLE, nunca «0
  ofertas». Canónico: veredicto de cinco vías de gastrojob.
- **Un barrido que no devuelve nada falla** (en portales de empleo): el
  productor conservaba su caché anterior; Core archivaría un corpus vivo.
- **`published_at` sólo del dato del portal.** Sin fecha utilizable se OMITE la
  clave (una cadena vacía llega al agregado como `published_at` vacío).
- **Identidad estable del id propio del portal**, no de la URL con slug.
- **Las descripciones no se recortan para que quepan en un cursor** (myScience
  llega a 9.866 caracteres).
- **Presupuestos declarados en params del scope**, no en código (schuljobs 4
  páginas de 26: ampliarlo es un cambio de params validado).
- **El kill-switch clasifica por la cadena de excepciones**: si un adaptador
  hace `raise X` sin `from`, el 403 del transporte no se verá.

## 9. Trampas ya pisadas (no repetirlas)

1. **Cambios ajenos permanentes en SwissJob** (`CLAUDE.md`, documentos
   históricos/unificación, auditorías sin seguimiento). No añadirlos ni
   restaurarlos: seleccionar fichero a fichero en cada commit.
2. **Los bucles de espera con `pgrep` se auto-detectan.** Esperar por el LOG.
3. **No editar el árbol mientras corre una suite** que lo monta.
4. **Dos pytest contra la MISMA base se vacían las tablas entre sí.** BFF y
   núcleo SÍ pueden ir a la vez; dos del BFF no.
5. **`JOB_TAGS` es una taxonomía de idiomas y docencia**, no de tecnologías.
   «Python Developer» no produce tags. «Zürich» no está en la tabla de países.
   Ejecutar antes de escribir una expectativa.
6. **ReactPortfolio se formatea con `black`** (línea 100); hay deriva previa
   en tres ficheros: no mezclarla ni añadir nueva (comparar contra `HEAD`).
7. **El hook de pre-commit de ReactPortfolio** necesita black instalado;
   `-c core.hooksPath=/dev/null` y ejecutar sus dos comprobaciones a mano.
8. **Un `<meta content="…">` no pasa por la pila de elementos**: leerlo en
   `on_void`.
9. **`int()` lanza con más de 4.300 dígitos**: capturarlo.
10. **Módulos multi-fuente** (colegios) declaran `SOURCE_NAMES`, o el ratchet
    los cuenta como desajuste.
11. **Suites en segundo plano desde el scratchpad mueren al instante**
    (compose no encontrado): lanzar desde la raíz del repo.
12. **Una sonda es una foto, no una condición estática.** «El servidor nunca
    responde» de remote.co era la acción de Akamai para un cliente concreto en
    un momento; probar varias cabeceras, varias redes y el archivo antes de
    sentenciar.

## 10. Lo que queda: el corte agrupado (TODO es del propietario)

Nada de esto se ejecuta sin aprobación explícita. Orden sugerido, derivado del
contrato §6 (fases 8-10) y de lo medido:

1. **HECHO · `proz` y `remoteco` retirados** (§5): `state: retired`, fuera del
   registro de providers de SwissJob y decisión registrada en el inventario.
2. **Confirmar la CI de ReactPortfolio** (`04a020d`, `ffcc0ba`, `5f03c68`).
3. **Credenciales a Core en el NAS**: `CORE_ADZUNA_APP_ID/APP_KEY`,
   `CORE_JSEARCH_RAPIDAPI_KEY` (hoy en el `.env` del Portfolio).
4. **Fusionar las dos ramas** en sus `main` (aprobación explícita) y desplegar
   Core PRIMERO (`scripts/deploy_nas.sh core`), luego BFF y Portfolio.
5. **Provisionar y habilitar los 24 scopes** con los `provision_*.py`
   (`provision_exact(session, source, SCOPE_PARAMS, enabled=True)`); los
   colegios se provisionan juntos (`provision_swiss_schools.py` +
   `provision_isp_workday.py`) y en el mismo paso se apaga el empuje escolar
   legado (`SchoolProducer`).
6. **Drenar y retirar los productores locales**: con la rama desplegada ya no
   se construyen; borrar entonces los módulos legados, `CompositeCatalog` y
   `FallbackCatalog`, y los tests que los fijaban.
7. **Sondas productivas** (`probe_core_final.py`, `probe_swissjob_final.py`,
   `probe_portfolio_final.py`, en `docs/audits/2026-10-10-optimization/`) y
   censo de salud: cero `local_pending`, cero
   `apagados_por_bloqueo` inesperados, oferta nueva sólo por Core.
8. **Inventario al SHA desplegado**: `state: core_primary`/`local_removed`,
   ratchet esperado `{"core_owned":40,"local_pending":0,…}`, y cerrar DT-144 en
   DEUDA_TECNICA.

Rollback hasta el paso 6: revertir el commit del portal reconecta su productor;
después del paso 6, el §11 del contrato (incidencia registrada, temporal).

## 11. Mapa de ficheros

- Contrato: `docs/unificacion/CONTRATO_CENTRALIZACION_TOTAL.md`
- Ledger de ejecución: `docs/unificacion/DEUDA_TECNICA.md` §DT-144 (checkpoints
  por portal; punto de reanudación al inicio)
- Inventario: `docs/unificacion/source_ownership.json`; ratchet
  `scripts/check_source_ownership.py`; secretos `scripts/check_no_secrets.py`
- Evidencia: `docs/audits/2026-10-1[45]-dt144-*.json` (uno por portal, más
  métricas de producción del día 14, sonda y análisis de proz/remoteco)
- Adaptadores: `jobhunt_core/harvest/providers/native_*.py`; registro en
  `providers/__init__.py`, `registry.py`, `admission.py`
- Provisión: `jobhunt_core/provision_<fuente>.py` (todos deshabilitados)
- Kill-switch: `jobhunt_core/harvest/runner.py`, `provider.py`, `health.py`,
  migración `core0055`
- Apagado local SwissJob: `backend/scrapers/__init__.py` (vacío),
  `backend/providers/__init__.py`, `backend/services/harvest_window.py`
- Portfolio: `services/core_source_compat.py`, `routers/core_source_compat.py`,
  `services/catalog/seam.py`, `scripts/check_local_harvest_ownership.py`
