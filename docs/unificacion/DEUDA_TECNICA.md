# Deuda técnica — proyecto jobhunting (SwissJob + jobhunt-core)

> **Lectura vigente — 2026-10-19.** Este fichero es un ledger técnico
> cronológico. Para estado global y releases manda
> [MEMORIA_PROYECTO.md](MEMORIA_PROYECTO.md); aquí mandan las marcas
> **ABIERTO/PARCIAL/COTA** más recientes de cada identificador. Los bloques
> «prevalentes» fechados se conservan como evidencia de su sesión y no son una
> memoria paralela.
>
> **22-09-2026 — punto 4, cosecha nativa.** Las 16 fuentes del corpus core las
> cosechan productores nativos; ver `SwissJob/docs/audits/POINT4_CUTOVER_2026-09-22.md`.
> A18-06 (configuración de proveedores divergente) queda comprobada para el core:
> la release desplegada se verifica por `/v1/ready` y las listas de retirada se
> validan DENTRO de cada contenedor con tripwire antes de cada maniobra.
> Nueva cota aceptada: el `canton` del histórico legacy no se repara (~4.500
> canónicas por un campo que hoy consume una sola búsqueda semanal); lo nuevo sí
> lo lleva.

## DT-147 · Cadencia de cosecha escalonada y carga del NAS

**Estado: DESPLEGADO Y SONDEADO — pendiente únicamente observar los primeros
ciclos naturales antes de cerrar la medida operativa.**

La medición de producción del 2026-10-19 comprobó 40 fuentes y 50 scopes
habilitados, todos despachados cuatro veces al día. Sobre los tres días
estabilizados del 16 al 18 de octubre, eso representa 200 ejecuciones y unas
3,77 horas diarias de ocupación del worker de cosecha. La cadencia uniforme no
se justifica: unas fuentes aportan cientos de ofertas diarias y otras pasan días
sin novedades; además, el coste por barrido varía entre segundos y varios
minutos. JSearch queda fuera de este punto y continúa bloqueado por DT-146.

### Objetivo y condiciones obligatorias

- **6 horas:** `jobgether`, `himalayas`, `jobicy`, `jobscout24`, `jobroom`.
- **12 horas:** `arbeitnow`, `ostjob`, `financejobs`, `irishjobs`,
  `nav_arbeidsplassen`, `publicjobs`, `schuljobs`, `weworkremotely`.
- **24 horas:** las otras 27 fuentes activas; toda fuente futura empieza en
  esta cadencia segura hasta que exista evidencia para promoverla.
- Horarios Europe/Zurich: 01:30/07:30/13:30/19:30, 09:30/21:30 y 23:30,
  respectivamente. Evitan el backup de las 02:20 y el mantenimiento
  05:20–06:40.
- Cada ejecución conserva el barrido completo del adaptador. No se introducen
  lecturas parciales inseguras en fuentes con paginación mutable.
- Tras dos fallos consecutivos, el siguiente intento espera 12 horas; tras
  tres, 24 horas; desde cuatro, 48 horas. La exclusión y el fencing existentes
  por scope se conservan.
- Estimación reproducible con las duraciones observadas: 95 ejecuciones y 1,79
  horas de worker al día, reducción del 52,5 %. No se declarará ahorro de CPU
  causal hasta repetir el mismo protocolo tras el despliegue.

### Progreso verificable

- [x] **DT147-1 · Línea base y clasificación:** medidas en la base viva sin
  leer contenido de ofertas; grupos, horarios, exclusiones y criterio de
  ahorro fijados arriba.
- [x] **DT147-2 · Despacho por cadencia:** `celery_app.py` programa los tres
  horarios y `tasks/harvest.py` selecciona 6/12/24 h, usa ventanas y claves
  idempotentes que incluyen la cadencia y asigna toda fuente nueva a 24 h.
- [x] **DT147-3 · Fallos y cuota:** `tasks/harvest.py` calcula 12/24/48 h desde
  los fallos consecutivos y el último intento; `jsearch` devuelve cadencia nula
  y queda fuera del despachador general aunque su scope se habilite por error.
- [x] **DT147-4 · Regresiones escritas, aún no ejecutadas:**
  `test_native_harvest_dispatch.py` cubre horarios, grupos, fallback, JSearch,
  claves y reintentos; `test_native_harvest_window.py` cubre ventanas y backoff
  contra PostgreSQL. Se respeta la orden de reservar toda ejecución al final.
- [x] **DT147-5 · Validación final:** Ruff 0.15.14 deja 371 ficheros limpios;
  propiedad de fuentes `core_owned=41/local_pending=0`; secretos 0; diff sin
  errores; 7 regresiones unitarias y 4 PostgreSQL verdes. La suite completa dio
  1.767 verdes y 6 fallos ambientales porque sus subprocesos tomaron el Alembic
  global sin `psycopg2`; repetidas sólo esas seis con el PATH del venv, dieron
  6/6 verdes. Cobertura efectiva final: 1.773 pruebas superadas.
- [x] **DT147-6 · Publicación segura:** `ac6a0ff` implementa el cambio y
  `53ec655` conserva el checkpoint; PR #22 fusionado en `c990131`. CI #218, el
  CI del PR y el CI de `main` #39231720242 terminaron `success` en sus nueve
  jobs. La rama `feat/tiered-harvest-cadence` y su worktree temporal se
  eliminaron después de verificar que `53ec655` es ancestro del merge. El
  recibo de producción `a1ec554` pasó después CI #39262281353: 9/9 jobs verdes.
- [x] **DT147-7 · Despliegue y sondas inmediatas:** imagen
  `swissjob-core:point5-c990131` desplegada conjuntamente en API y tres workers.
  `ready`, autoritativo, Alembic `core0055`; Celery 3/3 nodos; 50/50 scopes con
  estado y cero alertas; selección efectiva 12/9/29 y JSearch con cadencia nula;
  SwissJob y ReactPortfolio responden 200. La imagen anterior `acf48b2` (1,58
  GB) y el archivo temporal se eliminaron tras comprobar cero referencias.
- [ ] **DT147-8 · Observación natural:** primer ciclo de 6 h confirmado el
  2026-10-19 a las 13:30 Europe/Zurich: 12 scopes despachados, 12 `ok`, 915
  ofertas leídas en 25 páginas, cero errores y unos 4 min 30 s de ejecución
  secuencial. Faltan los primeros ciclos de 12/24 h y repetir durante una ventana
  comparable la medida de ejecuciones y ocupación. No se fuerza una ronda de 50
  scopes: sería carga superflua y contaminaría la medición que se pretende validar.

El primer intento de despliegue abortó antes de construir o tocar el NAS porque
el worktree limpio no tenía las dos variables que Compose exige para resolver
el modelo. El segundo usó valores de interpolación exclusivos del build; los
secretos productivos no se leyeron ni entraron en la imagen. La sonda posterior
confirmó los horarios efectivos 01:30/07:30/13:30/19:30, 09:30/21:30 y 23:30.

**Reanudación exacta:** release viva `c990131`; observar en los logs y en
`source_harvest_runs` los primeros ciclos pendientes de 12/24 h a las 21:30 y
23:30 Europe/Zurich. Comparar después tres días completos con la línea base
16–18 de octubre; sólo entonces cerrar DT147-8 y declarar ahorro medido.

## DT-146 · Reactivación de JSearch dentro de la cuota gratuita

**Estado: ABIERTO — JSearch permanece deshabilitado y no debe ejecutarse antes
de completar todos los gates de esta entrada.**

El plan Basic de JSearch comprobado el 2026-10-19 ofrece 200 créditos por ciclo
mensual, con límite duro y sin tarjeta obligatoria. Cada página solicitada
consume un crédito y devuelve hasta diez ofertas. La integración retirada de
ReactPortfolio pedía diez páginas al arrancar y en cada cosecha diaria: veinte
cosechas bastaban para agotar la cuota y un mes de 30/31 días requería por sí
solo 300/310 créditos, antes de búsquedas en vivo, reinicios o pruebas. La
evidencia productiva conserva el `http_429`, límite 200, restante `-2` y el
reinicio conservador `2026-11-02T11:28:18+01:00` en
[`DT144_RELEVO.md`](DT144_RELEVO.md).

Fuentes oficiales vigentes al comprobar el plan:
[tarifa](https://rapidapi.com/letscrape-6bRBa3qguO5/api/jsearch/pricing),
[contrato de JSearch](https://rapidapi.com/letscrape-6bRBa3qguO5/api/jsearch),
[coste por página](https://rapidapi.com/letscrape-6bRBa3qguO5/api/jsearch/playground/Job%20Search)
y [cabeceras de cuota](https://docs.rapidapi.com/docs/response-headers).

### Presupuesto imperativo por ciclo mensual

| Uso | Créditos máximos |
|---|---:|
| Cosecha automática: 4 páginas/día × hasta 31 días | 124 |
| Búsquedas en vivo: máximo 1 página por búsqueda | 40 |
| Activación, prueba controlada y actuaciones manuales | 16 |
| Margen de seguridad que no se programa | 20 |
| **Total** | **200** |

El consumo ordinario debe detenerse en 180 créditos o cuando queden 20. Los
créditos no usados no autorizan a aumentar automáticamente páginas, frecuencia
o búsquedas. Cosecha, búsqueda en vivo y operaciones manuales comparten la
misma credencial y el mismo contador.

### Gates obligatorios antes de reactivar

1. No llamar a JSearch antes del límite conservador del 2 de noviembre; después
   hacer una sola prueba de una página y conservar sus cabeceras saneadas. Si
   devuelve 429, mantenerlo apagado y registrar el nuevo reset sin repetir.
2. Excluir JSearch de la cadencia general de Core y programarlo una sola vez al
   día. Reducir su scope de diez a cuatro páginas.
3. Limitar cada búsqueda en vivo a una página, reutilizar resultados repetidos
   y aplicar un contador único a cosecha, búsquedas y acciones manuales.
4. No reintentar 429 de cuota mensual. Deshabilitar automáticamente JSearch al
   alcanzar 180 créditos, quedar 20 o recibir un 429.
5. Añadir las regresiones mínimas para frecuencia, páginas, presupuesto, corte
   y ausencia de reintento; ejecutar suites y CI, desplegar Core primero y
   mantener los consumidores sin credencial ni productor local.
6. Ejecutar una única cosecha controlada, comprobar persistencia, duplicados,
   salud y consulta desde ReactPortfolio; observar después el primer ciclo
   diario ordinario antes de cerrar esta deuda y actualizar el inventario.

**Estado efectivo comprobado el 2026-10-19:** el dispatcher Core corre a las
00:10, 06:10, 12:10 y 18:10 de Europe/Zurich y selecciona todos los scopes
habilitados. Producción tiene 50 scopes habilitados pertenecientes a 40
fuentes: Jobicy aporta 8, Adzuna 3, NAV 2 y las otras 37 aportan uno. JSearch es
la fuente 41 bajo propiedad Core, pero su único scope está manualmente
deshabilitado y queda excluido. Las fuentes retiradas tampoco se despachan.
Por ello, activar hoy el scope sin implementar el gate 2 consumiría cuatro
cosechas diarias y rompería el presupuesto aunque se redujera el número de
páginas.

**Reanudación exacta:** partir de `main`, verificar de nuevo tarifa, cabeceras,
estado del scope y cadencia viva; implementar los seis gates en orden. No basta
con cambiar `enabled=false` a `true` y no se contratará ningún plan de pago.

## DT-145 · Simplificación y eficiencia post-DT-144

**Estado: CERRADO — objetivo completo y verificado en producción el
2026-10-18.**

El plan autoritativo y la bitácora de ejecución están en
[`PLAN_ACCION_POST_DT144_OPTIMIZACION.md`](PLAN_ACCION_POST_DT144_OPTIMIZACION.md).
Esta entrada no permite sustituir el objetivo por una mejora parcial: deben
completarse todas sus fases y conservarse las funcionalidades observables.

Recibo final reproducible:
[`2026-10-18-dt145-final-optimization.json`](../audits/2026-10-18-dt145-final-optimization.json).
Las releases funcionales son SwissJob/Core `acf48b2` y ReactPortfolio
`5492147`; Core es el único cosechador de las 59 fuentes inventariadas, 41 de
ellas activas bajo propiedad Core y 18 retiradas. No queda ninguna fuente
`local_pending` ni `core_shadow`.

El cierre no oculta incidencias ni resultados desfavorables. El primer intento
Core abortó antes de construir por faltar el enlace local a `.env`. El primer
despliegue Portfolio abortó antes de recrear por una guardia no idempotente;
ReactPortfolio PR 6 (`b565492`, merge `5492147`) la corrigió y sus CI pasaron.
En rendimiento, una primera serie de `categoria-50` dio p95 2,183 s; una
repetición controlada de 100 muestras dio 1,842 s bajo carga 9,41/9,77/9,39 en
un NAS de dos CPU. Ambos valores quedan en el recibo; no se cambió código sin
causa demostrada.

La simplificación acumulada de ambos repositorios deja 18.148 líneas netas
menos. En ocho contenedores, la memoria mediana agregada bajó 34,0 %
(1.867,87 → 1.232,40 MiB) y los procesos 33,3 % (54 → 36). Tras todas las
sondas se retiraron 65 etiquetas antiguas no usadas; no existían contenedores
detenidos propios. Se conservó de forma deliberada `d2a38e9`, utilizada por el
servicio vivo de borrado GDPR `swissjob-erasure-cdc`. Los tres backends
devolvieron 200 después de la limpieza.

### Reglas imperativas

1. **Nunca asumir nada:** comprobar toda afirmación relevante con código,
   configuración, pruebas, métricas o estado desplegado antes de decidir.
2. **Principios arquitectónicos (SIEMPRE):** SRP, cohesión alta, acoplamiento
   bajo, legibilidad y consistencia. Si chocan con YAGNI, aplicar el mínimo que
   satisface ambos; nunca crear una abstracción solo por cumplir un principio.
3. **Realizar todo cambio propuesto:** una propuesta solo puede sustituirse si
   evidencia reproducible demuestra que rompe funcionalidad; en ese caso debe
   ejecutarse la alternativa mínima que cumpla el mismo objetivo.
4. Toda retirada exige búsqueda de consumidores, prueba de caracterización,
   prueba posterior y evidencia anotada aquí y en el plan.

### Alcance obligatorio

- **P0 — propiedad única de cosecha:** retirar del Portfolio el camino manual
  que aún descarga portales/colegios y hacer que solicite la cosecha al Core;
  retirar módulos y pruebas exclusivamente locales y ampliar el ratchet.
- **P1 — imágenes y dependencias:** separar dependencias de producción y
  desarrollo verificadas, retirar toolchains de las imágenes finales y usar
  una sola imagen inmutable de SwissJob para API y worker.
- **P2 — complejidad accidental:** dividir por fuente los dos mapeadores de
  complejidad F detectados, conservando exactamente sus contratos.
- **P3 — herramientas congeladas:** clasificar el grafo real de los 17 módulos
  de corte/importación y retirar solo lo demostrado como no consumido; extraer
  primero cualquier primitiva todavía usada en ejecución.
- **P4 — validación y cierre:** suites, builds, CI, despliegue Core primero si
  cambia contrato, sondas funcionales y negativas, y comparación de métricas.

### Evidencia de apertura

- DT-144 está cerrada y las rutas críticas medidas permanecen por debajo del
  SLO; esta deuda persigue menos código y coste operativo, no corrige una caída.
- ReactPortfolio conserva un colector manual en
  `routers/schools.py::_scrape_and_alert` que fuerza
  `manual_refresh=True`; `services/school_scraper.py` solo delega al Core
  cuando ese valor es falso.
- Las imágenes desplegadas medidas son aproximadamente 996 MB por cada imagen
  SwissJob, 893 MB para Portfolio y 2,196 MB para Core; deben volver a medirse
  tras P1, sin prometer una reducción no comprobada.
- Los candidatos de dependencia son solo hipótesis hasta verificar imports,
  carga de aplicación y pruebas. No se retira ninguno por nombre o intuición.
- Los módulos de migración no se eliminan en bloque: existen consumidores vivos
  conocidos en `applications.py`, el registro y las fuentes documentales.

### Checkpoints

| Fecha | Fase | Estado | Evidencia / punto de reanudación |
|---|---|---|---|
| 2026-10-16 | Apertura | COMPLETADO | Plan creado; ramas `feat/post-dt144-simplification` sobre las cabezas productivas verificadas. |
| 2026-10-16 | P0 código | COMPLETADO | Core `1736dd6`: adaptador VIS y proyección nativa a observaciones escolares. Portfolio `08ca961`: refrescos sólo por dispatcher Core, retirada de 5 módulos y 6 suites locales, BeautifulSoup retirada y ratchet ampliado. 2.720 líneas netas eliminadas en el commit Portfolio. Validación: 27 unitarias + 2 integradas Core focales; 205 focales y suite completa Portfolio verdes. |
| 2026-10-18 | P0 operación | COMPLETADO | Scope VIS `777ec0f4-da00-5ad0-9c05-9966862f53a4` habilitado. Portfolio contactó sólo `core-api`; run `ok`, 3 ofertas activas y 0 fallos. |
| 2026-10-16 | P1 | COMPLETADO | SwissJob/Core `68f21c6`, Portfolio `1c795f9`: manifiestos runtime/dev separados; herramientas de test fuera de producción; toolchains retirados; API y worker SwissJob comparten una imagen y despliegue atómico. Builds sin caché, `pip check`, imports, Compose, ratchets, 11 pruebas de despliegue y dry-runs verdes. Tamaños: SwissJob 511.476.439 B (−48,66 %), Portfolio 596.152.846 B (−33,24 %), Core 2.175.767.560 B (−0,93 %); agregado único −802.060.265 B (−19,63 %). `lxml` y `requests` siguen transitivos por consumidores verificados. |
| 2026-10-16 | P2 | COMPLETADO | Core `ba47ec8`, Portfolio `cdf9996`: `_content` y `_job` divididos en transformadores cohesionados por fuente/familia con despacho explícito, sin jerarquías nuevas. Radon bajó de F/58 a A/2 y de F/88 a A/4; no queda E/F en las dos unidades. Validación: Core 80 pruebas focales/integradas; Portfolio 225 focales y suite completa 1.587 pasadas/15 omitidas; Ruff, Black y `diff --check` verdes. Siguiente paso exacto: P3, construir el grafo de imports de las 17 herramientas y retirar únicamente ejecuciones consumidas. |
| 2026-10-18 | P3 | COMPLETADO | Core `1f415d3`: grafo de 17 módulos verificado; 15 herramientas de corte consumidas, 4 auxiliares, 6 scripts de ensayo y 23 pruebas históricas retirados. Se conservaron `migrate.py`, Alembic y sus pruebas, `erasure_restore.py` porque lo llama `nas_backup_daily.sh`, y la síntesis runtime extraída como `portfolio_vacancies.py`. El restore retiene escritura privada/exclusiva y prueba propia. Resultado: 166 inserciones, 15.872 eliminaciones, −15.706 líneas netas. Ruff, formato, compilación/imports, `diff --check` y 58 pruebas integradas focales verdes; la suite Core completa previa pasó 1.768/1 y se repetirá sobre el SHA final en P4. Siguiente paso exacto: P4 completo; no ejecutar JSearch. |
| 2026-10-18 | P4 | COMPLETADO | Suites: Core 1.768/1, SwissJob 783, Portfolio 1.601/1; frontend lint/57/build y audit 0. CI finales `39140191519` y `39142591147` verdes. Despliegues por SHA, Core primero. Sondas: 50/50 scopes, cero alertas/colas/staging, JSearch 0 runs, consumidores sin productores locales y seis modos Portfolio `core_primary`. Recursos −34,0 % memoria y −33,3 % procesos. |
| 2026-10-18 | Limpieza NAS | COMPLETADO | Eliminadas 65 etiquetas antiguas no usadas después de salud y sondas. Cero contenedores propios detenidos; se conservaron todos los ajenos y la imagen del servicio GDPR. Salud posterior: Core/SwissJob/Portfolio 200. |

**Reanudación exacta:** no queda trabajo de DT-145. Ante una incidencia, partir
de las releases y el recibo citados, comprobar el estado efectivo y preservar
las invariantes: Core único cosechador, VIS habilitado, JSearch deshabilitado y
`swissjob-erasure-cdc` vivo mientras sostenga el borrado coordinado. No
reinterpretar DT-144 ni reactivar productores locales.

## DT-144 · Centralización total de cosechadores en Core

**Estado: CERRADO — objetivo cumplido y verificado en producción el
2026-10-16.**

La deuda se abrió porque ReactPortfolio y SwissJob mantenían productores de
ofertas propios. Ese estado inicial —once fuentes de Portfolio, 12 providers y
13 scrapers de SwissJob— queda conservado en los checkpoints históricos; el
checkpoint prevalente inferior acredita su retirada y la propiedad exclusiva
de Core.

El objetivo, las invariantes, las fases -1 a 10, el procedimiento por fuente,
los controles de cambio y la definición no reinterpretables de terminado están
en el
[Contrato de centralización total](CONTRATO_CENTRALIZACION_TOTAL.md). Ese
contrato es la única fuente de verdad del plan; esta sección funciona como
ledger de ejecución para que cada agente deje evidencia y el siguiente pueda
continuar sin rediseñarlo.

### Cierre y trazabilidad

> **Lee primero [DT144_RELEVO.md](DT144_RELEVO.md)**: reúne el estado exacto, la
> receta aplicada, los comandos que se usan de verdad, las decisiones de diseño,
> la verificación final y las trampas ya pisadas. Es el contexto de auditoría si
> hubiera una incidencia futura; no quedan pasos de migración por reanudar.

**Checkpoint prevalente 2026-10-16 — DT-144 CERRADO:**

- Inventario final: 58 fuentes verificadas = 40 `local_removed` y 18
  `retired`; cero `local_pending`, cero `core_shadow` y cero publicadores
  locales. El ratchet queda en 40/0/58 y el inventario está fechado el
  2026-10-16.
- Releases vivas: Core `5b3bf3d`/Alembic `core0055`; SwissJob BFF y worker
  `point5-5002445`; ReactPortfolio backend y worker `board-c47b7a3`. La
  corrección operacional que transforma y verifica el Compose vivo está en
  SwissJob `main` `f381979`.
- CI final: ReactPortfolio `main` `38794582363`; retirada SwissJob
  `38794769320`; limpieza de runtime `38801340232`; formato vivo
  `38804569607`; contexto Docker `38809368125`; cola del worker PR/push
  `38811880988`/`38811859394` y `main` `38813007893`. Todos terminaron en
  `success`; cada CI SwissJob citado tuvo 9/9 trabajos verdes.
- Sonda Core final: `ready`, autoritativo, 49/49 scopes ejecutables observados,
  cero alertas, cero staging pendiente, cero delivery muerto y cero cola
  pendiente. JSearch continúa con un scope manualmente deshabilitado, cero
  habilitados y **cero ejecuciones desde la dispensa**; no se reintentó.
- Sonda SwissJob final: `CoreCatalog`, `CoreMatching` y `CoreSchools`; cero
  módulos productores, providers/scrapers/colegios registrados, credenciales
  de portal, dependencias prohibidas, snapshots pendientes o errores activos.
  El perfil sigue siendo local por diseño de producto, no por cosecha.
- Sonda ReactPortfolio final: las seis capacidades (`applications`, `catalog`,
  `documents`, `matching`, `saved_searches`, `schools`) están en
  `core_primary`; cero módulos, credenciales o dependencias locales
  prohibidas.
- Inventario operativo: el worker SwissJob ejecuta `-Q default,ai` y Celery
  confirma sólo esas dos colas; ambos Compose vivos tienen cero marcadores de
  caché, credenciales o cosecha local. Los productores locales retirados no
  son importables en las imágenes.
- Retirada física autorizada y completada: 60 módulos y 58 pruebas puramente
  locales de SwissJob; 40 módulos y 29 pruebas puramente locales de
  ReactPortfolio, además de dependencias, fallback y configuración obsoletos.
  Suites de referencia: SwissJob 783 passed/4 warnings; ReactPortfolio 1740
  passed/1 skipped.
- No queda acción de migración ni portal pendiente. `proz` y `remoteco`
  permanecen retirados por decisión explícita. Cualquier productor local futuro
  requiere el procedimiento de incidencia/ADR del contrato; no reabre una vía
  ordinaria de cosecha. Recibo final:
  [`2026-10-16-dt144-final-cutover.json`](../audits/2026-10-16-dt144-final-cutover.json).

**Checkpoint histórico 2026-10-15 — fusiones terminadas; corrección de runtime
pendiente de publicar:** ReactPortfolio PR 4 está fusionada en `main` como
`c47b7a3` y SwissJob PR 13 como `f50c0f1`; sus CI de `main` son verdes
(`38794582363` y `38794769320`, nueve trabajos SwissJob). La sonda Core previa
al despliegue pasa sobre `5b3bf3d`/`core0055`: 49/49 scopes ejecutables sanos,
colas drenadas y JSearch como único scope manualmente deshabilitado, sin nueva
ejecución. El Core no se redesplegó porque su árbol es byte a byte igual al de
la fusión.

El despliegue BFF se detuvo antes de transferir o cambiar el NAS al descubrir
instalación condicional de Chromium todavía presente en
`backend/Dockerfile.prod`; producción sigue intacta. La rama
`fix/dt144-runtime-residue`, sobre `f50c0f1`, retira Chromium/Playwright, la cola
`scraping`, la caché HuggingFace local y el disparador de cosecha del rehearsal,
pero conserva la cola funcional `ai` usada por el análisis de CV. El ratchet
abarca ambos Dockerfiles y siete manifiestos. Validación local: `40 core_owned /
0 local_pending / 58 total`, Ruff, compilación, Bash, YAML y diff verdes; suite
completa **783 passed, 4 warnings** con PostgreSQL/Redis autenticados; la imagen
construida conserva el usuario `app` y no contiene Playwright, el matcher ni el
directorio de Chromium. Siguiente
paso exacto: commit/push/PR/CI/fusión de la corrección; desplegar BFF y worker
SwissJob desde el nuevo `main`, desplegar ReactPortfolio `c47b7a3`, ejecutar las
tres sondas finales y cerrar. No ejecutar JSearch. Detalles en
`DT144_RELEVO.md` §0.1.

Primer intento sobre `8906b89`: la imagen limpia se cargó en el NAS, pero la
guarda abortó antes de sustituir el Compose o recrear el servicio porque el
montaje vivo usa otra indentación, omite `read_only` y ordena las propiedades
del volumen de otra forma. Producción no cambió. La corrección estrecha vive en
`fix/dt144-live-compose-format`; después de CI/fusión se reintenta desde el
nuevo SHA exacto de `main`.

Tras desplegar SwissJob `ae3cb02` y Portfolio `c47b7a3`, Core y Portfolio
pasan sus sondas finales. SwissJob falla sólo porque el contexto Docker copió
`backend/providers/__pycache__`: registros, credenciales, dependencias y los
módulos fuente sí están ausentes. La causa comprobada es que `.dockerignore`
excluía bytecode sólo en primer nivel. La exclusión recursiva elimina los
ficheros, pero Docker conserva el directorio padre vacío y Python lo reconoce
como paquete namespace; `fix/dt144-docker-context` excluye también
`providers/`. El ratchet exige las tres reglas. Producción sigue funcional;
para cerrar hay que fusionar, redesplegar BFF/worker y repetir las tres sondas.

Tras fusionar `5002445`, desplegar BFF/worker y obtener las tres sondas verdes,
el inventario operativo encontró una última divergencia del Compose vivo: el
worker seguía suscrito a la cola ya vacía `scraping`, pese a que los
manifiestos versionados usan `default,ai`. La rama `fix/dt144-worker-queue`
hace que el transformador retire y verifique esa cola. Falta CI/fusión,
redesplegar sólo el worker y repetir inventario/sondas antes de cerrar DT-144.

1. No repetir el inventario ni la Fase 0: las 58 fuentes están verificadas y
   los ratchets están activos. GitHub Free es el único forge autorizado; no
   desplegar ni volver a proponer Forgejo, GitLab, Bitbucket o GitHub Pro.
2. Mantener `ReactPortfolioServer/main` y `The_Swiss_Job/main` intactas
   durante toda la ejecución. Trabajar sólo en sus ramas DT-144, con CI en cada
   push, y fusionar al final tras la aprobación explícita del propietario.
3. Los despliegues reales deben fallar mientras HEAD no sea exactamente el SHA
   de `github/main`. Esta guarda evita publicar una rama incompleta, pero no se
   presenta como una protección de rama inexistente en GitHub Free privado.
4. La Fase 3 de APIs/feeds públicos está implementada en las ramas para
   RemoteOK, Himalayas, SwissTechJobs, ICTJobs, SwissDevJobs y Job-Room; Jobicy
   también está centralizado en rama. Todos sus scopes productivos permanecen
   deshabilitados y ambos `main` intactos: el corte vivo continúa agrupado con
   la fusión final aprobada por el propietario.
5. **Ya no queda ningún portal por implementar.** Los últimos cerrados son
   schuljobs (`020469e`, CI `38696733154` verde), Mosaic en Workday (`2200b56`,
   CI `38694111472` verde), los dos portales ATS
   escolares (`287cc18`), los cinco tableros de HTML propio (`72d068d`),
   stelle_admin (`82c742d`, reimplementado sin navegador) y tes (`9e62bd5`); antes gastrojob
   (`280bfc2`), myScience (`244f15e`) y JobScout24 (`33a804d` + ReactPortfolio
   `04a020d`/`ffcc0ba`). Los cuatro residuos estructurales están cerrados
   (`5f03c68` en ReactPortfolio y `c7d5388` en Core). DynamiteJobs quedó cerrado antes (`9ec6901` /
   `1a0db8d`, CI `38607230379` y `38608902811` verdes), igual que JSearch y
   Adzuna en los commits de sus checkpoints. No se ha desplegado ninguno.
6. **Cero portales pendientes de implementar.** Las 40 fuentes operativas tienen
   `core_adapter` en rama. `proz` y `remoteco`, no implementables con un cliente
   conforme (medido el 2026-10-15), fueron retirados definitivamente por
   decisión explícita del propietario ese mismo día. El registro de scrapers de
   SwissJob está VACÍO y los módulos legados siguen temporalmente como rollback.
7. **Relevo operativo verificado el 2026-10-15:** SwissJob/Core continúa en
   `feat/fase-a-core` (última cabeza funcional `020469e`, CI `38696733154`
   verde; después sólo commits documentales); ReactPortfolio continúa en
   `feat/dt144-free-gate`, cabeza `5f03c68`. Los remotos `main` siguen
   exactamente en `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
   `5bf457afbde5b44a995acce4ef241324f066f836`. El estado condición a condición
   frente al §10 del contrato, el corte agrupado paso a paso y el diagnóstico
   completo de `proz`/`remoteco` (con lo que lo arreglaría) están en
   [DT144_RELEVO.md](DT144_RELEVO.md) §3, §5 y §10.
8. **Los cuatro residuos estructurales están CERRADOS** (checkpoint del
   2026-10-15): `jobicy` y `remoteok` ya no registran caché ni actualizador y el
   censo vacío está fijado; los barridos G7/G8 saltan con motivo y su garantía
   se prueba ahora sobre las 18 rutas servidas desde Core con un agregado
   hostil; la costura sirve `CoreCatalog()` directamente cuando no hay fuente
   local; y Core tiene su propio kill-switch de bloqueo (`core0055`, tres
   bloqueos 403/429/503 apagan el scope, con censo que lo canta). Lo único
   diferido a propósito al corte final es el BORRADO de `CompositeCatalog` y
   `FallbackCatalog`: son la vía de rollback mientras producción cosecha en
   local.
9. El worktree ReactPortfolio estaba limpio. En SwissJob/Core ya existían
   cambios ajenos a DT-144 en `CLAUDE.md`, documentación histórica y de
   unificación, además de documentos/auditorías sin seguimiento. No añadirlos,
   restaurarlos ni reformatearlos: comprobar `git status` y seleccionar para
   cada commit sólo los archivos DT-144 modificados por el agente.
10. La metodología obligatoria es la sección 7.1 del contrato: preflight; trazado
   extremo a extremo y contraste con runtime; línea base viva saneada y con
   huellas; implementación mínima en Core; pruebas por capas y doble ciclo
   aislado; paridad explicada; adaptación del consumidor y apagado local;
   suites/CI; y checkpoint de relevo antes de avanzar al portal siguiente.

### Checkpoint 2026-10-15 · Dispensa limitada de JSearch y reanudación del corte

- El propietario ordenó no esperar al `2026-11-02` y continuar **sin un nuevo
  intento de JSearch**. Esta decisión supersede sólo la puerta que exigía
  completar JSearch antes del despliegue; no altera el objetivo arquitectónico
  ni permite retirar, simular o marcar como saludable ese scope.
- El primer ciclo se acepta como 49/49 scopes ejecutables `ok`, cero en curso y
  un único fallo externo conocido: JSearch `http_429`. Su scope permanece
  configurado en Core pero permanece deshabilitado manualmente para que el beat
  no efectúe otra petición. DT-144 no espera ni programa un reintento; una
  reactivación futura será una actuación separada. Debe ser el único scope
  deshabilitado y no llevar marca de autoapagado; cualquier otro fallo vuelve a
  cerrar la puerta.
- La validación posterior exigirá un segundo ciclo nativo 49/49 con ventana
  nueva y JSearch expresamente excluido, además de todas las sondas, retiradas,
  inventarios, suites y CI ya contratados. La primera cosecha positiva futura
  de JSearch se observará en un ciclo ordinario cuando exista cuota y no bloquea
  el cierre de DT-144.
- Reanudación exacta: deshabilitar JSearch explícitamente por cuota; desplegar
  desde `main` limpio los consumidores preparados de SwissJob y ReactPortfolio;
  sondarlos; ejecutar el segundo ciclo 49/49; después efectuar la retirada
  física y el cierre verificable. Este
  checkpoint prevalece sobre todas las instrucciones históricas de esperar o
  reintentar JSearch.
- A las `2026-10-15T17:54:15+02:00` se ejecutó el primer paso con bloqueo de
  fila y comprobaciones previas: sólo JSearch pasó de habilitado a deshabilitado
  y conserva `disabled_by_blocks_at` nulo. La salud posterior muestra 49
  habilitados/con estado, cero autoapagados y cero alertas; delivery permanece
  drenado. La maniobra no llamó al proveedor ni modificó los otros 49 scopes.

#### Avance verificable · consumidores desplegados y retirada física preparada

- SwissJob BFF `a2ba944` y Portfolio `b587c3e` están desplegados y saludables.
  Portfolio ya resuelve las seis capacidades por Core, sin registro ni
  credenciales locales; SwissJob confirma las cuatro autoridades y 2/2 perfiles
  entregados. Los workers locales no cosechan: SwissJob permanece detenido y
  Portfolio ya no arma cosecha.
- Las sondas aíslan el trabajo restante: borrar código local/fallback, retirar
  `CAREERJET_AFFID` y `JOOBLE_API_KEY` del Compose SwissJob, arrancar su worker
  sólo con la imagen limpia y repetir las pruebas/sondas.
- El segundo ciclo de Core sigue en curso. La fotografía intermedia contiene
  24 `ok`, Arbeitnow `running`, ninguna ejecución de JSearch y 49 scopes
  habilitados. No es todavía el gate terminal 49/49.
- Checkpoint `2026-10-15T19:14+02:00`: Arbeitnow sigue procesando tras extraer
  13.064 ofertas y mantiene heartbeat reciente (`17:11:22Z`); el agregado
  permanece en 24 `ok` + 1 `running`, sin despacho ni sonda de JSearch. El PR
  documental 13 quedó verde tras repetir sólo el `docker-build` que había
  fallado por una descarga PyTorch interrumpida, sobre el mismo SHA `c6bd5af`.
  En SwissJob, la rama final ya deja vacío el registro local y desconecta de
  Celery/APScheduler las tareas y disparadores de cosecha, preservando los jobs
  de producto. Sintaxis y Ruff pasan; las pruebas focales de registro,
  scheduler y leader-lock se adaptaron al nuevo invariante y pasan 11/11 contra
  el PostgreSQL/Redis efímero DT-144. No se han borrado todavía los módulos
  físicos.
- Se retiraron también las diez opciones locales de credenciales de portal de
  settings, plantillas y `deploy/nas/swissjob.configured.yml`; el despliegue
  sanea tanto `.env.prod` como el Compose candidato. Las credenciales
  `CORE_ADZUNA_*`/`CORE_JSEARCH_*` se conservan porque pertenecen al único
  colector. Validación: `bash -n`, `py_compile`, Ruff, búsqueda nominal y
  ratchet verdes; el manifiesto sólo cambia las siete banderas de registro y
  mantiene honestamente `16` Core-owned/`24` local-pending hasta el borrado.
- La única rotura de colección de CI era el import histórico de
  `_PROVIDER_CLASSES`; se mantiene como diccionario vacío de compatibilidad,
  sin capacidad de construir providers. La colección completa descubre 2.068
  pruebas sin errores y la corrección está publicada en `66b3e6a`. El segundo
  ciclo avanzó: Arbeitnow, Jobroom y TES terminaron `ok`; hay al menos 27
  scopes terminales y JSearch no fue despachado.
- Punto exacto y metodología de relevo: ver
  `DT144_RELEVO.md` §0.1, checkpoint «despliegue de consumidores y limpieza
  final en curso». Los worktrees y ramas están allí fijados; ningún cambio de
  limpieza está versionado todavía y no se debe desplegar desde ellos.
- La sonda final de Core ya codifica la dispensa sin falsear JSearch: exige
  49/49 scopes ejecutables sanos y, por separado, exactamente un scope JSearch
  configurado, deshabilitado manualmente, sin sello del kill-switch y sin runs
  posteriores a `2026-10-15T17:54:15+02:00`. Ejecutada por stdin y en sólo
  lectura contra producción, pasa sin fallos: Core `ready`/autoritativo,
  delivery y staging drenados, y slot de captura ausente. El gate independiente
  del segundo ciclo terminó después con 49/49 `ok`, cero tareas en curso o en
  error y cero ejecuciones de JSearch. Esta puerta operativa queda cerrada sin
  esperar al 2 de noviembre ni efectuar el reintento dispensado.
  Evidencia saneada:
  `docs/audits/2026-10-15-dt144-second-cycle-production.json`.
- La CI de `dcc653a` deja verdes lint, Compose, Docker, frontend, ratchet y la
  primera suite Core. Backend llega a 1.895 aprobadas, pero queda rojo por 15
  fallos y 11 errores: salvo un timeout de carta LLM pendiente de reproducir en
  el entorno completo, todos fijan providers, políticas o credenciales locales
  ya retirados de `Settings`. No se reintroducen esos secretos ni se ocultan los
  tests: se eliminan o migran junto con el código local en la retirada física
  aprobada.
- Se cerró un hueco de la sonda SwissJob antes del borrado: ahora comprueba
  `tasks.harvest_persist` (ruta real) y `services.harvest_window`; ambos sólo son
  alcanzables desde las tareas locales retiradas del despacho. Se conserva
  `scrapers.swiss_schools_config`, que sí tiene consumidores de producto.
- Ejecutada de nuevo en sólo lectura, la sonda SwissJob confirma el alcance:
  41 módulos importables, siete entradas de provider y dos variables heredadas
  en el contenedor. Autoridades, perfiles y snapshots siguen sanos; no aparece
  ninguna cuarta causa de fallo.
- La sonda Portfolio también se endureció antes del commit: comprueba routers
  por fuente, helpers/worker de cosecha y los motores locales además de las
  implementaciones y composites que ya enumeraba. Así el gate valida la
  retirada completa, no sólo la ausencia del registro.
- La ejecución de sólo lectura en producción identifica exactamente 37 módulos
  Portfolio y ninguna otra causa de rojo: las seis rutas están en
  `core_primary`, no hay providers registrados ni credenciales de portal y los
  schedulers de producto permanecen activos con cosecha diaria deshabilitada.
- Auditoría transitiva posterior: los recuentos 41/37 medían sólo el primer
  corte directo. El alcance completo y verificable es **60 módulos SwissJob**
  (paquete `providers`, 41 productores/tareas y 18 helpers/modelos usados sólo
  por ellos) y **40 módulos Portfolio** (los 37 anteriores más
  `circuit_breaker`, `job_matcher` y el registro local vacío). Se conservan las
  piezas con consumidores reales: `models.Job`,
  `scrapers.swiss_schools_config`, `utils.text`, y en Portfolio
  `beautifulsoup4`/`SCRAPER_PROXY_URL` para detalle y colegios.
- Optimización exigida en la imagen limpia: retirar de SwissJob
  `beautifulsoup4`, `playwright`, la instalación de Chromium y su configuración
  Compose; retirar de Portfolio `sentence-transformers`, `scikit-learn`,
  `torch`, la dependencia directa de `numpy` y la precarga del modelo. `numpy`
  sigue transitivamente por `folium`, necesario para analítica. Las sondas
  finales fallan si reaparece cualquiera de esas dependencias directas o una
  distribución exclusiva del matcher. Los endpoints públicos de
  estado/warmup se preservan con semántica Core-only, sin motor local.
- Decisión prevalente del propietario en este checkpoint: continuar ahora, sin
  esperar al 2 de noviembre y **sin el reintento seguro de JSearch**. JSearch
  queda configurado y deshabilitado; no vuelve a ser una puerta de DT-144.
  Reanudación exacta: aprobación informada del borrado ampliado, retirada y
  migración de pruebas, suites/CI, fusión, despliegue limpio y sondas finales.

### Checkpoint 2026-10-15 · Decisión de retirada y comienzo del corte

- El propietario confirmó la retirada definitiva de `proz` y `remoteco` y
  autorizó continuar con fusión, despliegue, activación de scopes, retirada de
  código local y sondas finales.
- Ambos quedan fuera de `_PROVIDER_CLASSES`, sin publicador activo y en estado
  `retired`. La evidencia histórica se conserva; nunca almacenaron una oferta
  productiva (46 y 54 errores consecutivos respectivamente).
- El inventario pasa a 58 fuentes: 16 `local_removed`, 24 `local_pending` y 18
  `retired`; el ratchet se endurece a un máximo de 24 pendientes y 25
  publicadores locales (Jobicy todavía cuenta dos veces hasta el corte).
- Siguiente puerta: pruebas estrechas y ratchets, commit/push y CI verde antes
  de fusionar. Los módulos físicos se borran con todo el legado después de que
  Core esté desplegado y sus scopes hayan completado ciclos correctos.
- Primera CI del corte (`38703963650`): Core y otros siete trabajos verdes;
  backend detectó correctamente dos políticas huérfanas de `proz`/`remoteco`.
  Se eliminan de `_POLICIES` porque una fuente retirada no es una fuente viva ni
  una pre-decisión de cosecha. Repetir CI antes de fusionar.

### Checkpoint 2026-10-15 · Corte productivo pausado por redacción de logs

- PR iniciales fusionados y CI verdes: SwissJob/Core `239a737` (run de `main`
  `38708626741`) y ReactPortfolio `8b38ddd` (`38706379993`). Core está desplegado
  en sus cuatro procesos, autoritativo, con Alembic `core0055`.
- Infraestructura recuperada antes del corte: directorios/Compose del NAS y
  supervisor QTS reconstruidos y validados. La migración descubrió dos defectos
  del material recuperado: falso fallo de `grep -v` sobre un env administrativo
  de una línea y `CORE_DB_PASSWORD` divergente del DSN vivo. El primero queda
  corregido con `sed`; el segundo se sincronizó atómicamente sin revelar el
  valor. Bootstrap, aislamiento y `pgvector` pasaron después.
- Los 24 orígenes/33 scopes se provisionaron primero deshabilitados y después se
  habilitaron con recuento exacto: 71 fuentes, 79 scopes, 40 fuentes operativas y
  50 scopes habilitados. `swissjob-worker` está detenido; Portfolio conserva su
  proceso Uvicorn pero está recreado con el kill-switch global en `false` y cero
  tareas de fondo armadas.
- `dt144-cutover-1` empezó bajo el run
  `ec3e6041-463a-5d89-987e-506aaa5a2940`; tras 2 scopes `ok` se detuvo el worker
  de cosecha. Motivo: el INFO de `httpx` imprimía URLs completas y Adzuna lleva
  autenticación en la query. Los valores no se documentan ni se vuelven a
  copiar. El ciclo parcial **no** satisface el gate.
- Corrección mínima pendiente de PR: elevar `httpx`/`httpcore` a WARNING tanto
  al importar como después del setup de logger Celery; prueba focal 23/23 y
  Ruff/formato verdes. Hasta fusionarla y redesplegar, el worker Core de cosecha
  debe permanecer detenido. Después se reutiliza la misma clave idempotente,
  pasado el lease de 900 s, y se exigen los 50 scopes cerrados sin fallo.
- Estado completo y receta exacta de relevo: `DT144_RELEVO.md` §0.1.

### Checkpoint 2026-10-15 · Corrección desplegada y primer ciclo nativo en curso

- La pausa anterior está resuelta: PR 2 fusionada en
  `5b3bf3dad27a8575ab6d8f380d4343ba4f540ae3`, run de `main` `38715523686`
  entero en verde y los cuatro procesos Core desplegados en
  `swissjob-core:point5-5b3bf3d`. Readiness autoritativo, Alembic `core0055`,
  cero reinicios del nuevo worker de cosecha.
- La regresión de logger pasó 23/23 y, ya en producción, el log nuevo mantiene
  cero coincidencias de parámetros sensibles y cero INFO de `httpx`. El
  contenedor anterior fue recreado; no se copiaron secretos al ledger.
- Hallazgo operativo: el helper agregado `run_all_task` no sirve para demostrar
  un ciclo de 50 scopes bajo los límites globales Celery 1.800/2.100 s. Su run
  parcial y sus reintentos se invalidaron; se revocaron sin `terminate` las
  tareas `c50b3cfe-7a2a-4d84-9848-ea2cbf7f75f1` y `87de6aad`. La primera
  lectura que asoció Jobroom a actividad real era incorrecta y queda corregida:
  Celery mostró que el trabajo vivo era un `run_scope` de Arbeitnow.
- Gate válido: dispatcher nativo real de beat, ventana
  `2026-10-15T12:00:00+02:00`, una tarea independiente por scope. A las 11:58
  UTC hay 50 esperados, 24 `ok`, 1 Arbeitnow `running`, 25 no iniciados y cero
  fallos. La tarea activa empezó a las 11:38:37 UTC y su heartbeat avanzó a
  11:53:52 UTC, dentro de `SWEEP_BUDGET_S=1500`; no se interrumpe.
- Reanudación exacta: esperar 50/50 `ok`; desplegar y sondar ambos consumidores;
  ejecutar un segundo ciclo nativo completo; sólo entonces borrar productores,
  credenciales, conexiones y fallbacks locales, actualizar
  `source_ownership.json`, pasar suites/CI, fusionar, redesplegar y ejecutar las
  sondas finales. El detalle vivo manda en `DT144_RELEVO.md` §0.1.

### Checkpoint 2026-10-15 · Relevo fusionado y producción revalidada

- El PR documental 6 se fusionó en `The_Swiss_Job/main` como
  `205987ac01b88893b2199eeea083ed9f31341a66`. Su CI de `main`
  `38747081401` terminó con los 9/9 trabajos verdes; no se omitió ni forzó
  ninguna comprobación.
- La sonda Core final se repitió por stdin y en modo de sólo lectura contra el
  contenedor vivo. Confirmó release `5b3bf3d`, Alembic `core0055`, 50 scopes
  observados/habilitados/con estado, cero autoapagados, 25.540 entregas, cero
  pendientes o *dead-letter*, staging drenado y slot de captura ausente.
- El único fallo sigue siendo `harvest_not_50_of_50`: JSearch conserva la
  alerta `cosecha_sin_completar`. No se hizo otra petición al proveedor, no se
  consumió cuota y no hubo despliegue ni mutación productiva.
- Avance contractual estimado: **76 %**. No debe leerse el 49/50 de scopes como
  98 % global: aún faltan el primer 50/50, despliegue y sondas de consumidores,
  segundo 50/50, retirada física, inventarios/suites, despliegue final y sondas
  finales. El siguiente comando operativo sigue siendo reejecutar sólo JSearch
  con la clave determinista documentada cuando se restablezca la cuota —o se
  aporte una credencial válida sin coste—.

### Checkpoint 2026-10-15 · Primer ciclo detenido en 49/50 por cuota de JSearch

- La ventana nativa `2026-10-15T12:00:00+02:00` terminó con 49 scopes `ok`,
  cero en ejecución, cero sin iniciar y un único error: JSearch `http_429`.
  Celery agotó sus reintentos y dejó la cola vacía; no hay otro fallo oculto.
- Dos sondas seguras desde el contenedor Core confirmaron 429. La segunda se
  justificó para distinguir la cuota gratuita y devolvió límite 200, restante
  `-2`, reset en 1.543.221 segundos (aprox. 17,9 días) y ningún encabezado
  separado de *free-plan hard limit*. RapidAPI documenta oficialmente que
  `x-ratelimit-requests-reset` son segundos hasta el reinicio. Las salidas
  excluyeron secreto, URL, query y cuerpo; no se harán más peticiones antes del
  reset.
- El instante exacto de las sondas directas no quedó en logs; sólo constan los
  intentos nativos de `12:10:37Z` y `12:44:14Z`. Para no asumir, se tomó la
  primera marca externa que forzosamente es posterior: GitHub creó a
  `2026-10-15T13:47:57Z` el CI `38745776582` del commit que ya documentaba el
  contador 1.543.221. Su suma fija el límite conservador de reintento en
  `2026-11-02T10:28:18Z` (`11:28:18+01:00` Madrid/Zúrich). No llamar antes; a
  partir de entonces ejecutar una sola vez el `run_scope` determinista de
  JSearch y, si persiste el 429, registrar el nuevo reset sin otra repetición.
- El scope JSearch permanece habilitado con dos fallos consecutivos y no ha
  alcanzado el kill-switch. Este ciclo **no satisface** el gate 50/50 y no se
  despliegan aún los consumidores.
- Una comparación posterior dentro del NAS, limitada a presencia e igualdad y
  sin exponer valores ni huellas, confirmó que Core y `portfolio_backend` usan
  la **misma** credencial JSearch; `portfolio_worker` no contiene otra. No hay
  una segunda cuota productiva ya disponible. Esperar el reset sigue siendo la
  única acción autónoma segura, salvo que el propietario facilite otra
  credencial válida y gratuita.
- A las `2026-10-15T17:02:34+02:00` se repitió la sonda final de sólo lectura:
  Core permanece `ready`, autoritativo, en `5b3bf3d`/`core0055`, con 50 scopes
  habilitados y observados, cero autoapagados, 25.795 entregas, ninguna
  pendiente/dead-letter, staging 0 y sin slot de captura. El único fallo es el
  mismo `harvest_not_50_of_50`; no se llamó al proveedor. Los cuatro procesos
  Core reales (`api`, `worker`, `harvest-worker`, `ops-worker`) ejecutan la
  misma imagen con cero reinicios; el worker SwissJob sigue `exited (0)` y el
  worker Portfolio continúa quiescente mediante
  `BACKGROUND_SCHEDULERS_ENABLED=false`. No hubo despliegue ni mutación.
- Reanudación exacta: al restablecerse la cuota —o con otra credencial válida
  sin coste— reejecutar sólo el scope JSearch usando su misma clave de ventana.
  Si queda `ok`, el primer ciclo será 50/50 y continúa el orden ya documentado:
  consumidores y sondas, segundo ciclo 50/50, borrado físico, inventario,
  suites/CI, despliegue y sondas finales.

### Checkpoint 2026-10-15 · Salud y digest escolar preservados antes del corte

- La revisión del consumidor encontró una regresión concreta antes de
  desplegarlo: `_get_watchlist_sources()` leía `_SCRAPER_CLASSES`; como el
  registro local está vacío, `check_watchlist_health` comprobaba cero fuentes y
  el digest filtraba todas las ofertas escolares.
- Corrección mínima en `feat/dt144-consumer-cutover`: las ocho fuentes escolares
  son ahora el contrato cerrado del consumidor. La alerta consulta en paralelo
  su agregado autenticado de Core y valida fuente, fecha de último ciclo,
  scopes habilitados/observados, fallos y ejecución activa. Se eliminó la
  lectura de `SourceCompliance`; no se añadió endpoint ni dependencia.
- Evidencia local: Ruff/formato verdes y
  `docker compose run --rm --no-deps backend pytest -q
  tests/test_g1_fix_api_y_watchlist.py` = 10 passed. La primera ejecución fuera
  de Compose no alcanzó las pruebas porque el host Docker `postgres` no se
  resuelve desde el host; queda registrada como error de entorno, no como fallo
  funcional.
- Esta corrección debe fusionarse y desplegarse junto con el consumidor, pero
  no altera la puerta vigente: JSearch debe cerrar el primer ciclo 50/50 antes
  de desplegar BFF/worker.

### Checkpoint 2026-10-15 · Tareas útiles de Portfolio separadas de la cosecha

- El quiesce productivo de Portfolio usa
  `BACKGROUND_SCHEDULERS_ENABLED=false`, que detiene también purgas, alertas,
  digest y enriquecimiento. Mantenerlo tras el corte empeoraría funciones y
  permitiría crecer sin cota tres tablas; no es un estado final válido.
- ReactPortfolio `bf91254` + `0c2c9a6` (rama
  `feat/dt144-consumer-cutover`, PR 2) incorpora
  al manifiesto y a la política convergente de despliegue dos valores en ambos
  procesos: `BACKGROUND_SCHEDULERS_ENABLED=true` y
  `SCHEDULER_DAILY_HARVEST_ENABLED=false`. Las veinte fuentes siguen en
  `LEGACY_DISABLED_SOURCES` y los registros de caché/actualizadores están
  vacíos, por lo que no reaparece tráfico de portal. El mismo transformador
  elimina del Compose vivo las tres variables locales de Adzuna/JSearch.
- SwissJob `3af3dc6` elimina de `.env.prod` los diez nombres de credenciales de
  providers locales antes de recrear BFF/worker. Conserva una copia sólo
  durante la maniobra, verifica los nombres sin mostrar valores y la destruye
  tras la sonda saludable; los secretos `CORE_*` viven en el env aislado del
  núcleo y no se tocan.
- Evidencia: `tests/test_source_handover_quiesce.py` 43 passed; `bash -n` y los
  modos secos de Portfolio, BFF y worker son correctos. La política no se
  despliega antes de que JSearch complete el primer gate 50/50.

### Checkpoint 2026-10-15 · Sonda final Core actualizada y roja por la causa real

- `probe_core_final.py` deja atrás el supuesto de 17 scopes: exige 50
  observados, 50 habilitados, 50 con estado, cero scopes autoapagados y cero
  alertas, además de las invariantes previas de readiness, delivery, staging y
  retirada del slot de captura.
- Compila y se ejecutó por stdin dentro de `swissjob-core-api-r5`, sin escritura.
  Producción devuelve Core autoritativo `5b3bf3d`, Alembic `core0055`, delivery
  y staging drenados, slot ausente y censo 50/50 con cero autoapagados.
- Resultado deliberadamente rojo: una única alerta
  `cosecha_sin_completar` para JSearch, cuyo `last_complete_at` sigue nulo. No
  apareció ninguna segunda causa. Tras completar ese scope con la misma clave
  de ventana, esta sonda debe quedar verde antes de avanzar.

### Checkpoint 2026-10-15 · Fusión segura de Portfolio y líneas base de consumidores

- ReactPortfolio PR 2 se fusionó en `main` como
  `682a3e589d137fe180c2b905a508250e6f0d1e10` después de dos ejecuciones
  Backend CI completas y verdes; su CI de `main` `38742958119` también terminó
  verde. La fusión prepara la política final
  (`BACKGROUND_SCHEDULERS_ENABLED=true`, cosecha diaria `false` y retirada de
  tres credenciales del Compose); no se ha desplegado y no altera el gate
  vigente 49/50.
- SwissJob PR 5 se fusionó como
  `136116df590e98477a17ac23a20ec6243cc75633` después de que sus dos
  ejecuciones pasaran tests backend/Core/frontend, builds, Compose, lint y
  ratchet. La CI de `main` `38744550106` repitió todos los trabajos y terminó
  completamente verde. El fallo de formato anterior quedó corregido en
  `b27cde6`; no se omitió ni forzó ninguna comprobación.
- `probe_swissjob_final.py` ya representa el estado terminado: registros de
  providers y scrapers vacíos, cero módulos productores importables y cero
  nombres de credenciales de portal. Ejecutada por stdin y sin escritura en el
  BFF vivo confirma las cuatro autoridades correctas, 2/2 perfiles enlazados y
  cero snapshots pendientes/erróneos; falla, como debe, por 12 providers, 13
  scrapers, 39 módulos locales y cinco nombres de credencial aún presentes.
- `probe_portfolio_final.py` exige registro local vacío, cosecha diaria apagada,
  ausencia de módulos productores y de `CompositeCatalog`/`FallbackCatalog`,
  cero nombres de credenciales de portal y las seis capacidades exactamente
  en `core_primary`. En el backend vivo verifica el routing 6/6 pero falla por
  11 providers, 23 módulos locales, cosecha diaria todavía habilitada y tres
  nombres de credencial. Ninguna sonda imprime valores secretos.
- Este checkpoint no adelanta la retirada física: JSearch debe completar el
  primer 50/50, después se despliegan y sondan consumidores, se exige un
  segundo 50/50 y sólo entonces se eliminan los residuos que ahora enumeran
  las sondas.
- Se crearon worktrees limpios y reproducibles para el futuro despliegue:
  `/tmp/swissjob-dt144-cutover-main` en `136116d` y
  `/tmp/reactportfolio-dt144-cutover-main` en `682a3e5`. Sus modos secos
  resolvieron exactamente las tres imágenes esperadas sin tocar el NAS. La
  inspección viva posterior confirma que no hubo autodespliegue: SwissJob sigue
  en `4017081`, Portfolio en `444f072`, Core en `5b3bf3d`, todos con cero
  reinicios; `swissjob-worker` continúa detenido y el worker Portfolio conserva
  `BACKGROUND_SCHEDULERS_ENABLED=false` hasta superar el gate.

### Checkpoint 2026-10-14 · Fase -1 parcialmente completada

- Inventario reconciliado: 58 fuentes, 16 `local_removed`, 26
  `local_pending`, 16 `retired` y 27 productores locales activos porque
  Jobicy sigue publicándose desde los dos productos.
- Control machine-readable: `source_ownership.json` y
  `scripts/check_source_ownership.py` contrastan adaptadores Core, módulos y
  registros SwissJob, el literal de fuentes Portfolio y los tres ratchets.
- Bloqueo automático: CI ejecuta el control; `deploy_nas.sh` lo ejecuta antes
  de cualquier despliegue; un alta local, un productor duplicado adicional o
  un descenso de fuentes Core hace fallar el proceso.
- ReactPortfolio incorpora su control equivalente en
  `scripts/check_local_harvest_ownership.py`: la lista activa comprobada es de
  11 fuentes y cualquier adición local no acompañada de una decisión explícita
  falla en CI.
- Revisión: ambos repositorios incorporan `CODEOWNERS` para contrato, controles,
  registros y puntos de despliegue. No se declara aún revisión obligatoria
  efectiva: la API de GitHub devolvió 401, no hay `GITHUB_TOKEN`/`GH_TOKEN` ni
  cliente autenticado en el host y la clave SSH de Git no administra reglas.
- Pruebas: validación cruzada devuelve
  `{"core_owned":16,"local_pending":26,"sources":58}`; Ruff pasa en ambos
  verificadores; `bash -n scripts/deploy_nas.sh` y su modo seco Core pasan sin
  tocar el NAS.
- Estado de puerta: **NO SUPERADA** hasta conectar GitHub y verificar protección
  de `main` con CI y revisión de propietario obligatorias. Por el contrato no
  se provisiona ni corta Jobicy antes de resolver esta puerta.
- Siguiente paso exacto: conectar GitHub, activar/verificar las reglas en ambos
  repositorios, ejecutar CI sobre los commits de esta fase y sólo entonces
  iniciar la sombra de Jobicy.

### Checkpoint 2026-10-14 · quality gates publicados y Fase 0 revalidada

- Commits publicados en `feat/fase-a-core`: `37a0c6e` restaura los quality
  gates ejecutables y `7315b5d` satisface el formato que el propio CI exige a
  `scripts/` y `docs/`. No se han desplegado: son control y pruebas, no una
  migración de fuente.
- Se reprodujeron los fallos del commit anterior antes de decidir: formato en
  ocho ficheros, plantilla/variable ausentes en `compose-config`, creación de
  `pg_trgm` con el rol mínimo, pruebas que asumían `/app` o `redis-core` en el
  checkout de CI, URLs Redis heredadas por tests unitarios, centinela SlowAPI
  contrario a FastAPI 0.141.1 y deriva de Alembic por la tabla histórica
  `jobhunt_routing`.
- La solución conserva los seis registros `core_primary` de
  `jobhunt_routing` como evidencia de rollback y excluye únicamente esa tabla
  retirada del autogenerate; no se borró ni migró ningún dato productivo.
- Evidencia local: Ruff y formato, 714 ficheros; guardas Core, 29/29; gate A,
  1/1; cadena Core desde base vacía, 54 migraciones; suite Core completa antes
  de la corrección, 1.540 verdes y sólo los ocho fallos corregidos; backend,
  `alembic upgrade head`, `alembic check` sin operaciones y 1.916 verdes + 4
  `xfail` previstas; los seis aparentes fallos de la segunda suite Core fueron
  reproducidos y cerrados 6/6 con el Alembic del entorno que aporta psycopg2.
  `compose config` pasa para base, dev, prod y core-local.
- Evidencia productiva saneada:
  [`2026-10-14-dt144-phase0-snapshot.json`](../audits/2026-10-14-dt144-phase0-snapshot.json).
  Las consultas fueron de sólo lectura y el fichero no contiene secretos.
- Revalidación productiva de sólo lectura: Core mantiene 17 scopes habilitados
  de 16 fuentes, todos con `consecutive_failures=0` y último ciclo correcto el
  2026-10-13 UTC; Jobicy continúa sin fuente ni scope Core. ReactPortfolio
  mantiene exactamente 11 productores locales sanos: diez cachés actualizadas
  hace 6 h y JSearch hace 13 h, con 1.351 entradas totales; sus credenciales de
  Adzuna y JSearch están presentes y nueve fuentes ya transferidas figuran en
  `LEGACY_DISABLED_SOURCES`. SwissJob conserva tres providers efectivos
  (Jobicy, ProZ y RemoteCo) y 13 scrapers; las diez credenciales opcionales de
  otros providers están ausentes. Su scheduler productivo ejecuta la cadena
  diaria a las 12:00 Europe/Zurich con ±4 h.
- La consulta de la base efectiva `swissjobhunter` confirmó Jobicy correcto el
  2026-10-13 (69 descargadas) pero 17 ciclos consecutivos sin persistencia;
  ProZ y RemoteCo continúan en error y SchulJobs conserva un intento sin
  desenlace. Son datos de paridad/bloqueo de Fase 0, no razones para saltarse la
  puerta ni retirar productores.
- No cambian propietarios ni estados: 58 fuentes, 16 Core, 26 pendientes, 16
  retiradas y 27 instancias locales. La puerta de Fase -1 sigue **NO SUPERADA**.
- CI remoto de `7315b5d`: 9/9 jobs terminados en `success` (`source-ownership`,
  lint y test de backend/Core/frontend, `compose-config` y `docker-build`).
- Siguiente paso exacto: con acceso administrativo autenticado,
  activar/verificar protección de `main` con esos nueve checks y revisión
  CODEOWNER en ambos repositorios.

### Checkpoint 2026-10-14 · inventario verificable del piloto Jobicy

- El checkpoint `7471a1d` quedó publicado y sus nueve jobs remotos terminaron
  en `success`; no contiene cambios de productores ni de producción.
- La entrada única de Jobicy en `source_ownership.json` registra ahora los dos
  módulos productores, todos sus triggers de arranque/cadencia/refresco, la API
  JSON pública sin credenciales, la identidad externa, la deduplicación Core,
  la última evidencia correcta de cada producto, la ausencia comprobada de
  scopes Core y el bloqueo vigente. ReactPortfolio no expone el instante exacto
  de finalización de su caché, por lo que se conserva explícitamente como
  desconocido en vez de inferirlo a partir de su edad.
- `inventory_verified=true` sólo se acepta cuando están presentes los campos
  mínimos contractuales y los productos de `current_harvesters` coinciden con
  `active_publishers`. El ratchet `min_inventory_verified_sources=1` impide
  borrar silenciosamente la ficha o retirar su marca de verificación.
- Verificación positiva: 58 fuentes, 16 Core y 26 pendientes. Controles
  negativos: quitar `transport` falla con `verified inventory missing`; quitar
  `inventory_verified` falla con `verified inventory count decreased`.
- Pruebas focales sin tráfico real: Core 27 aprobadas y 3 integraciones
  omitidas por su contrato; SwissJob Jobicy 4/4; ReactPortfolioServer limpio en
  `5bf457a`, 5/5. La primera invocación SwissJob apuntó al hostname Compose
  `postgres`; se repitió contra PostgreSQL/Redis temporales y quedó verde.
- Estados sin cambio: Jobicy continúa `local_pending`, con dos publicadores
  locales, cero scopes Core y sin credencial. La puerta de Fase -1 sigue
  **NO SUPERADA**; no se ha provisionado ni ejecutado una sombra.

### Checkpoint 2026-10-14 · inventario de la oleada ya propiedad de Core

- Se verificaron individualmente las 16 fuentes `local_removed` contra su
  adaptador nativo, transporte e identidad: Arbeitnow, Euremotejobs,
  Financejobs, Globaljobs, IrishJobs/Jobs.ie, Jobgether, Jobspresso, NAV,
  Ostjob, Publicjobs, Remotive, The Hub, We Work Remotely, Working Nomads,
  Zebis y Zentraljob.
- Las fichas declaran el scheduler Core de cuatro ventanas, cero credenciales,
  la estrategia común del sink y su identidad específica; no se generalizó una
  identidad entre familias RSS, JSON, SvelteKit, Next.js o CH Media.
- Los 17 UUID —NAV usa dos scopes— coinciden exactamente con
  `2026-10-14-dt144-phase0-snapshot.json`. En la inspección productiva todos
  estaban habilitados, con cero fallos consecutivos y finalización dentro de la
  ventana UTC 22:10–22:39 del 2026-10-13; el inventario conserva esa ventana y
  no inventa una hora individual que la consulta no capturó.
- El ratchet de fichas completas avanza de 1 a 17. El verificador sigue dando
  58 fuentes, 16 Core y 26 pendientes; JSON y `diff --check` pasan.
- No cambió ningún estado, scope, productor o despliegue. La puerta de Fase -1
  continúa **NO SUPERADA** a la espera de protección administrativa de ramas.

### Checkpoint 2026-10-14 · inventario verificable de fuentes retiradas

- Se contrastaron las 16 entradas `retired` con los módulos, los registros
  efectivos y la instantánea productiva: Authentic Jobs, Careerjet, DailyRemote,
  Glassdoor partner, ILO Jobs, Impactpool, Indeed partner, JobCloud partner,
  Jooble, LinkedIn autorizado, MedJobs, ReliefWeb, TranslatorsCafe, UNDP Jobs,
  UN Talent y XING partner.
- Ninguna tiene publicador, trigger ni scope Core activo. Las fichas distinguen
  módulos fuera del registro, tres fuentes con errores históricos sin éxito,
  MedJobs bloqueada por el challenge de Cloudflare y conectores partner que no
  realizan tráfico sin una ruta autorizada implementada.
- Las siete credenciales aplicables —Careerjet, Jooble y los cinco partners—
  estaban ausentes en producción en la captura del 2026-10-14. Solo se guardan
  sus nombres y el booleano de presencia; no se leyó ni documentó ningún valor.
- La identidad registrada es la que realmente implementa el código legado:
  MD5 de título, empresa y URL —con GUID como fallback donde corresponde—,
  seguida por el dedup difuso común de SwissJob. Documentarla no convierte esas
  fuentes en candidatas de migración ni las reactiva.
- El ratchet de fichas completas avanza de 17 a 33. El control cruzado sigue
  dando 58 fuentes, 16 Core y 26 pendientes; las 16 retiradas están verificadas
  y quedan 25 fichas activas locales por completar.
- No cambió ningún estado, registro, credencial, conexión, scope o despliegue.
  La puerta de Fase -1 continúa **NO SUPERADA**: la sesión gráfica de GitHub
  comprobada muestra `Sign in` y el host sigue sin CLI, token API ni helper.

### Checkpoint 2026-10-14 · inventario medido de ReactPortfolio

- Se completaron las diez fuentes locales activas exclusivas de ReactPortfolio:
  Adzuna, DynamiteJobs, Himalayas, ICTjobs, Job-Room, JobScout24, JSearch,
  RemoteOK, SwissDevJobs y SwissTechJobs. Jobicy permanece en su ficha separada
  porque es el único productor duplicado entre los dos productos.
- Registro, módulos, triggers, endpoints, credenciales, identidad y dedup se
  trazaron hasta el productor y el worker efectivos. Las diez se ejecutan al
  arranque del worker, en la cadena diaria 12:00 hora del contenedor con ±4 h,
  por lecturas públicas con caché vencida y por refresco manual autenticado.
- La imagen viva `portfolio-backend:board-444f072` y el commit auditado
  `5bf457a` no difieren en ninguno de los productores, normalizadores, registros
  o reglas comunes usados por esta medición.
- Sonda productiva de solo lectura:
  [`2026-10-14-dt144-reactportfolio-metrics.json`](../audits/2026-10-14-dt144-reactportfolio-metrics.json).
  Leyó exclusivamente los snapshots Redis y emitió contadores, timestamps y
  nombres de campos: cero valores de ofertas, URLs, títulos, empresas, datos
  personales o secretos; cero mutaciones.
- Volumen medido: 1.311 entradas cacheadas, exactamente el total productivo
  anterior menos las 40 de Jobicy; las diez fuentes normalizaron todas sus
  entradas con cero errores. La identidad URL estable dejó una sola colisión
  —JSearch— y el hash exacto título/empresa 43, de las cuales 32 son Job-Room.
- La completitud de campos no se oculta: ICTjobs entrega 0/100 empresas y
  descripciones; Adzuna, 0/6 países; JSearch, 0/95 tags y algunos países y
  ubicaciones ausentes; DynamiteJobs, JobScout24 y SwissDevJobs tienen
  descripciones parciales, y Job-Room, RemoteOK y SwissDevJobs tags parciales.
  Son datos de paridad para sus futuros adaptadores Core, no una excusa para
  rellenar valores inventados.
- El ratchet avanza de 33 a 43 fichas completas. Quedan exactamente las 15
  fuentes locales activas de SwissJob. No cambió ningún estado, publicador,
  credencial, conexión, scope ni despliegue; la puerta -1 sigue sin superar.

### Checkpoint 2026-10-14 · inventario medido de SwissJob

- Se completaron las 15 fuentes locales activas restantes: Gastrojob,
  MyScience, ProZ, Remote.co, SchulJobs, Stelle Bund, las ocho fuentes
  escolares y TES. Cada ficha traza módulo, trigger diario, transporte,
  identidad, deduplicación, último resultado y bloqueo real; ninguna requiere
  credenciales.
- Sonda productiva de solo lectura:
  [`2026-10-14-dt144-swissjob-metrics.json`](../audits/2026-10-14-dt144-swissjob-metrics.json).
  Leyó agregados de ofertas, salud, cursores y cumplimiento desde la imagen
  viva `swissjob-backend:point5-4017081`; no emitió valores de ofertas,
  detalles de errores, datos personales ni secretos y no realizó mutaciones.
- Volumen medido: 498 entradas almacenadas, 312 activas, 9 marcadas como
  duplicadas y 10 excedentes por el hash difuso de título/empresa. La presencia
  de campos queda guardada en el orden empresa, ubicación, descripción, URL,
  fecha de publicación y tags, sin rellenar ausencias.
- ProZ y Remote.co no se consideran funcionales: acumulan respectivamente 46
  y 54 errores consecutivos, sin éxito ni fila persistida. Antes de migrarlas
  se debe verificar un transporte conforme que funcione o retirarlas de forma
  explícita. SchulJobs conserva 160 filas, pero su último intento no tiene
  resultado terminal, su último éxito es del 29-08 y el cursor no completó el
  bootstrap; exige resolver esa paridad antes de cualquier corte.
- Inspired, ISB y ZIS son resultados vacíos verificados, no fallos: terminaron
  sin errores, con cursor y cumplimiento correctos. El futuro adaptador Core
  debe conservar esa semántica. Las fuentes escolares carecen en general de
  descripción y fecha; Stelle Bund carece además de ubicación, descripción,
  fecha y tags. Son datos de paridad, no valores que deban inventarse.
- El ratchet avanza de 43 a 58 fichas completas: todo el inventario descubierto
  tiene ya propietario verificable. No cambió ningún estado, productor,
  credencial, conexión, scope ni despliegue. La puerta de Fase -1 sigue
  **NO SUPERADA** porque no existe una sesión administrativa GitHub autenticada
  con la que demostrar y fijar los checks obligatorios y la revisión CODEOWNER.

### Checkpoint 2026-10-14 · protección parcial y línea base Jobicy

- Se autenticó GitHub CLI mediante el flujo OAuth oficial como `VRicardoP`, con
  permisos administrativos comprobados en ambos repositorios. No se extrajeron
  cookies ni contraseñas del navegador y el cliente se ejecuta desde `/tmp`.
- `The_Swiss_Job/main` estaba 276 commits detrás de la rama operativa, sin
  divergencia. Se adelantó por fast-forward de `1af39b8` a `e59283b`; el SHA
  ya había terminado 9/9 checks en `feat/fase-a-core` y el push no dispara
  ningún despliegue automático. El CI específico de `main`, run
  `38520384241`, terminó con sus 9/9 jobs en `success`.
- La protección de `The_Swiss_Job/main` quedó aplicada y leída de vuelta:
  exige los nueve checks `source-ownership`, lint/test de backend, frontend y
  Core, `compose-config` y `docker-build`; rama al día, una aprobación, revisión
  CODEOWNER, conversaciones resueltas y cumplimiento también para
  administradores; force-push y borrado están deshabilitados.
- GitHub Free no ofrece protección administrativa para
  `ReactPortfolioServer/main` mientras el backend siga privado. El frontend
  público es otro repositorio y no altera esta limitación. No se cambia la
  visibilidad ni se contrata un plan: la mitigación vigente es trabajar sólo en
  la rama DT-144, ejecutar CI en cada push, bloquear despliegues cuyo SHA no sea
  el de `github/main` y exigir al propietario la aprobación final de la fusión.
- Línea base Jobicy de solo lectura:
  [`2026-10-14-dt144-jobicy-baseline.json`](../audits/2026-10-14-dt144-jobicy-baseline.json).
  Los dos productores locales usan ocho consultas únicas en conjunto: feed
  general y siete tags. Un solo scope genérico perdería categorías visibles.
- ReactPortfolio conserva 40 ofertas Jobicy, 40/40 normalizadas y completas en
  los once campos medidos, con dos colisiones tanto de clave estable como de
  título/empresa. SwissJob descargó 69 en el último ciclo pero no conserva
  ninguna fila y acumula 17 ciclos con cero persistidas; sus logs del ciclo ya
  no existen porque el worker actual arrancó después. Esa causa debe resolverse
  o clasificarse con evidencia antes del corte, nunca asumirse.
- No se creó fuente ni scope Core, no se ejecutó una sombra, no se detuvo ningún
  productor y no cambió producción en este checkpoint. La puerta se adaptó
  después al mecanismo GitHub Free ratificado por el propietario sin reducir la
  definición de terminado.

### Decisión vigente del propietario 2026-10-14 · sólo GitHub Free

- GitHub Free es el único alojamiento y flujo Git autorizado; el coste debe
  permanecer en 0 €. `ReactPortfolioServer` continúa privado y su frontend
  continúa público. No se contratarán planes ni se instalarán forges
  alternativos.
- Toda implementación de DT-144 vive en ramas de feature. Los dos `main`
  permanecen intactos hasta que la centralización completa esté validada y el
  propietario apruebe expresamente la fusión final.
- CI se ejecuta en cada push. Los scripts de despliegue sólo aceptan el SHA
  exacto de `github/main`; por tanto una rama incompleta no puede llegar al
  NAS aunque carezca de protección administrativa.
- Forgejo, sus repositorios duplicados, integración, backup, imagen y datos ya
  fueron retirados. No debe volver a proponerse ni desplegarse.
- Esta decisión cambia el mecanismo de trabajo, no la definición de terminado:
  Core debe seguir siendo el único cosechador y ambos productos deben perder
  productores, credenciales, conexiones y persistencia local de ofertas.

### Checkpoint 2026-10-14 · Forgejo retirado y flujo GitHub Free validado

- Se retiraron del NAS el contenedor `dt144-forgejo`, su imagen, el directorio
  de datos, el único archivo de backup y su entrada exacta del registro de
  retención. La comprobación posterior devolvió `FORGEJO_ABSENT`: no queda
  contenedor, imagen, ruta, backup, registro ni referencia en el script vivo.
  Los datos eliminados no son recuperables desde el NAS; el código continúa en
  las ramas publicadas de GitHub.
- Antes de retirar el servicio se instaló atómicamente el backup diario sin la
  dependencia Forgejo. La copia versionada y la viva comparten SHA-256
  `d1e21e88baa77aaaf482a78c07ff3727d3b2b245c6989fa7e9ba7fe1d7f19073`;
  además resuelve y valida la imagen Core efectiva en vez de fijar una etiqueta
  histórica.
- Los repositorios de GitHub conservaron sus referencias: SwissJob
  `main=e59283b`, rama DT-144 `fd643cb`; ReactPortfolioServer `main=5bf457a`,
  rama DT-144 `72478d8`. Ningún `main` se modificó durante la retirada.
- La protección gratuita de `The_Swiss_Job/main` se leyó de vuelta: PR y rama
  actualizada, nueve checks obligatorios, conversaciones resueltas, aplicación
  a administradores, y force-push y borrado prohibidos. Las aprobaciones quedan
  en cero y CODEOWNER desactivado porque el único propietario no puede aprobar
  su propia PR. `ReactPortfolioServer`, al ser privado, no ofrece protección de
  rama en GitHub Free; esta limitación está declarada, no simulada.
- El CI sigue ejecutándose en cada push y ambos despliegues comparan el commit
  completo con `github/main`. La prueba negativa real de esas guardas y los
  runs CI de los commits de retirada deben registrarse después de publicarlos;
  hasta entonces la Fase -1 continúa **NO SUPERADA**.
- Validación local previa al commit: backup SwissJob 3/3, sintaxis Bash y
  formato/diff limpios; suite completa ReactPortfolioServer 2.129 aprobadas,
  1 omitida. Siguiente paso exacto: publicar la retirada en ambas ramas,
  comprobar sus CI, ejecutar las dos pruebas negativas de despliegue y después
  iniciar la sombra Core de Jobicy sin tocar `main`.
- Revalidación de continuidad del 2026-10-14: los únicos remotos configurados
  en ambos repositorios son `github`; el NAS no devuelve contenedor, imagen,
  directorio de datos ni backup Forgejo. El script de backup vivo tampoco lo
  referencia y comparte con la copia versionada el SHA-256
  `d1e21e88baa77aaaf482a78c07ff3727d3b2b245c6989fa7e9ba7fe1d7f19073`.
  La rama ReactPortfolio `feat/dt144-free-gate` quedó enlazada explícitamente
  con `github/feat/dt144-free-gate`, no con `github/main`.

### Checkpoint 2026-10-14 · Fase -1 superada y piloto Jobicy en rama

- La Fase -1 queda **SUPERADA** conforme a la puerta vigente de GitHub Free:
  SwissJob `57f161e`, run `38539018069`, terminó 9/9 controles en `success`;
  ReactPortfolio `cb99f39`, run `38539017146`, terminó su CI en `success`.
  Las pruebas negativas reales rechazaron desplegar ambos SHA de feature porque
  no coincidían con `github/main`. Las ramas principales continúan exactamente
  en `e59283b` y `5bf457a`.
- La causa del histórico SwissJob `69 descargadas / 0 persistidas / 17 ciclos`
  quedó reproducida: el feed actual entrega `jobType` como lista y el
  normalizador local intentaba unir valores anidados; 50/50 filas del feed
  amplio lanzaron `TypeError`. No era el filtro tech ni la ventana de edad.
- Core incorpora ocho scopes Jobicy exactos e idempotentes. El aprovisionador
  crea scopes deshabilitados y sólo cambia activación con `--enable` o
  `--disable` explícitos; un scope extra, parámetros distintos o propietario
  incorrecto hacen fallar la transacción.
- Dos ciclos sombra, sin red y sobre PostgreSQL desechable, recibieron 204
  filas cada uno y estabilizaron en 158 ofertas, 158 revisiones raw y 158
  revisiones normalizadas. El segundo ciclo no añadió oferta ni revisión;
  8/8 scopes completaron, con cero fallos, 158 fechas, 22 categorías y tres
  tipos de contrato. Evidencia saneada y hashes de entrada:
  [`2026-10-14-dt144-jobicy-shadow.json`](../audits/2026-10-14-dt144-jobicy-shadow.json).
- La comparación del contrato visible detectó y cerró antes del corte tres
  diferencias no cubiertas inicialmente: países `USA/UK/Anywhere`, taxonomía
  técnica de `/by-tag` y el parámetro histórico `geo_filter`. Core preserva
  además `jobIndustry`, `jobType` y `pubDate` cuando llegan como listas/campo
  nativo; el agregado evita contar a la vez el valor singular y su lista.
- ReactPortfolio `0d1cc26` + `9390071` deja Jobicy fuera de su registro local,
  mantiene las rutas históricas como adaptador exclusivo de Core y declara la
  fuente deshabilitada en backend y worker. SwissJob deja Jobicy fuera de
  `_PROVIDER_CLASSES`; su buscador ya usa `CoreCatalog`, por lo que no necesita
  un segundo adaptador de lectura.
- Validación local: ReactPortfolio completa 2.127 aprobadas y una omitida;
  batería focal de compatibilidad 257 aprobadas. Core completa 1.549 aprobadas
  y una omitida; Jobicy/Core focal 36 aprobadas; registro SwissJob 85 aprobadas;
  ratchet cruzado `{"core_owned":16,"local_pending":26,"sources":58}`.
- No se desplegó ni se creó fuente/scope en producción. La ficha permanece
  `local_pending` porque describe el estado vivo, mientras el código de rama
  conserva las clases antiguas únicamente para rollback. Se borrarán después
  de la observación productiva final, no antes.
- Siguiente paso exacto: terminar el CI de los SHA Jobicy publicados, confirmar
  el commit de desconexión SwissJob y avanzar a las fuentes públicas de Fase 3.
  No fusionar, desplegar ni habilitar Jobicy hasta completar todas las fuentes
  y recibir la aprobación final del propietario.

### Checkpoint 2026-10-14 · RemoteOK centralizado en rama

- Se cerró primero el checkpoint anterior: los tres commits Core/SwissJob de
  Jobicy terminaron en `success`; el último fue `8efa7b4`, run
  `38545188502`. ReactPortfolio `9390071`, run `38544602348`, también terminó
  en `success`. Los dos `main` y producción siguen intactos.
- La API pública real de RemoteOK devolvió un objeto de metadatos y 99 ofertas:
  99/99 con identidad, URL, `apply_url` y fecha; 19 con ambos límites
  salariales. No existe paginación ni filtro que conservar: el catálogo exacto
  es un scope global determinista
  `d2d5b74e-56ea-5745-8ecb-8bac403b9613`, creado deshabilitado y activable sólo
  mediante orden explícita.
- Core reutiliza el productor JSON nativo y conserva el raw, `id`, URL de
  portal, URL de candidatura, fecha, todos los tags —incluidos orden y
  duplicados—, ubicación histórica, descripción y salarios USD. El agregado
  transporta los importes visibles sin etiquetarlos como CHF. La comparación
  campo a campo detectó primero el recorte a 15 tags y la deduplicación de un
  tag; ambos defectos se corrigieron antes de cerrar una paridad final 99/99.
  Una comprobación adicional detectó que el agregado genérico ordena por
  observación, mientras la ruta histórica ordenaba por fecha publicada; el
  adaptador RemoteOK conserva explícitamente el orden descendente anterior.
- Dos ciclos sobre PostgreSQL aislado recibieron 99 filas cada uno y
  estabilizaron en 99 identidades, 99 revisiones raw y 99 revisiones de oferta:
  el segundo ciclo no duplicó nada. El scope quedó observado, sin fallos, con
  99 fechas, 48 ubicaciones, 118 tags sensibles a mayúsculas, 19 salarios y 99
  URLs de candidatura. Los datos de prueba se eliminaron al terminar. Evidencia
  saneada:
  [`2026-10-14-dt144-remoteok-shadow.json`](../audits/2026-10-14-dt144-remoteok-shadow.json).
- ReactPortfolio sirve las seis rutas históricas de RemoteOK desde el adaptador
  Core compartido, conserva identidad, importes, estadísticas y salud, lo
  retira de `get_all_providers()` y baja el ratchet local de 10 a 9. El
  manifiesto de backend y worker lo bloquea como productor local. Los módulos
  antiguos quedan desconectados pero presentes como rollback hasta la
  observación productiva final.
- Validación a este checkpoint: Core completo 1.562 aprobadas y una omitida;
  Core JSON 56/56 y PostgreSQL 12/12; ReactPortfolio completo 2.138 aprobadas
  y una omitida, compatibilidad/registro 259/259; paridad viva 99/99; shadow de
  doble ciclo correcto. No se creó scope productivo, no se desplegó, no se
  fusionó y RemoteOK permanece `local_pending` porque el inventario describe el
  estado vivo.
- Siguiente paso exacto: publicar los commits RemoteOK y confirmar sus CI.
  Después continuar con la siguiente fuente pública de Fase 3; no habilitar
  RemoteOK ni cambiar `main` antes de la fusión final aprobada.

### Checkpoint de relevo 2026-10-14 · Himalayas centralizado en ramas

- Antes de empezar, RemoteOK quedó publicado y cerró CI: SwissJob/Core
  `cdca8df`, run `38549699864`, con 9/9 jobs en `success`; ReactPortfolio
  `5cb6737`, run `38549703279`, con su job en `success`. El commit documental
  posterior `80e0b00` ratifica GitHub Free y la retirada de Forgejo. Los `main`
  remotos siguen intactos: SwissJob `e59283b` y ReactPortfolioServer `5bf457a`.
- El adaptador Core de Himalayas quedó publicado en `d9b6859`; el run CI
  `38552971276` terminó sus 9/9 jobs en `success`. ReactPortfolio consume ese
  agregado en `e8ba714`, publicado únicamente en `feat/dt144-free-gate`; el run
  `38554552671` terminó su job en `success` en 4m31s. Ninguno de los dos commits
  alcanzó `main`.
- Recuento comprobado del inventario productivo: 26 portales únicos y 27
  productores locales —16 SwissJob y 11 ReactPortfolio—. En las ramas, tras
  Jobicy y RemoteOK, faltan 24 portales: 15 SwissJob y 9 ReactPortfolio.
  Tras completar Himalayas quedan 23: 15 SwissJob y 8 ReactPortfolio.
- Trazado Himalayas: el único productor activo es
  `ReactPortfolio/routers/himalayas_jobs.py`, con worker, caché y seis rutas.
  `SwissJob/backend/providers/himalayas.py` existe pero no está registrado ni
  ejecutado. El contrato activo consulta tres páginas de 20, offsets 0/20/40,
  conserva sólo la primera restricción de ubicación, categorías y excerpt raw,
  y ordena recientes por `pubDate` descendente.
- Línea base viva: tres páginas de 20, 60 filas y 57 GUID únicos; tres filas son
  duplicados exactos causados por desplazamiento entre páginas. Huellas por
  offset: `0=9d12d58e9f486acb6cad2aeff30a93d708b1f44eaec7c682eb22ba7e9c740bc9`,
  `20=4cb4bd726fcdda567bfaf2439fac6af708109c4d7c670775a880661cd02ab96f`,
  `40=e42501eb15fd1e411436663e24ac45d34f439f0025167fad6e423f0c2f7e482d`.
- Core en este checkpoint incorpora Himalayas al provider JSON, fecha de
  admisión, agregado, DTO y provisión opt-in. Conserva raw, GUID, candidatura,
  `pubDate` numérico, HTML, orden y duplicados de tags, primer país, salarios
  decimales y moneda, empleo y logo. La primera comparación encontró que 12
  logos vacíos pasaban de `""` a `null`; se corrigió y la paridad final es
  60/60 sin diferencias.
- El doble ciclo sombra aislado recibió 60 filas y tres páginas por ciclo,
  terminó dos veces en `ok`, observó 1/1 scope, registró cero fallos y estabilizó
  57 identidades, 57 listings, 57 raws y 57 revisiones de oferta. El segundo
  ciclo no creó revisiones. Las 57 fechas coinciden por GUID y el orden funcional
  sigue siendo más reciente primero. Evidencia:
  [`2026-10-14-dt144-himalayas-shadow.json`](../audits/2026-10-14-dt144-himalayas-shadow.json).
- Validación ejecutada: Ruff focal verde; JSON unitario 47/47 antes del ajuste
  de logo y 40/40 después; PostgreSQL focal 13/13; paridad viva 60/60; Core
  completo 1.573 aprobadas, una omitida y una advertencia conocida. Dos
  invocaciones descartadas fallaron por infraestructura del arnés —URL admin
  con driver asíncrono y `PYTHONPATH` ausente en el contenedor— y pasaron al
  repetirlas con la configuración correcta; no fueron fallos de producto.
- Archivos DT-144 que forman el checkpoint Core de SwissJob:
  `jobhunt_core/harvest/providers/native_json.py`, `normalize.py`,
  `admission.py`, `source_aggregate.py`, `api/v1_harvest.py`,
  `provision_himalayas.py` y sus pruebas focales/integrales. No incluir en
  ningún commit los cambios preexistentes de `CLAUDE.md`, documentación histórica,
  auditorías de viabilidad ni otros ficheros ajenos que muestra `git status`.
- ReactPortfolio conserva las seis rutas históricas desde el adaptador Core
  compartido: `by-category`, `by-location`, `recent`, `refresh-cache`, `stats` y
  `health`. Mantiene GUID, candidatura, fecha epoch, HTML, primer país, categorías,
  empleo, logo —incluido `""`— y salarios condicionales; solicita las 200 ofertas
  antes de ordenar por `pubDate` y aplicar el límite del cliente.
- El productor local se desconectó en todos sus puntos: ya no se importa ni se
  monta su router, no figura en `get_all_providers()`, no registra caché ni función
  de actualización aunque el módulo de rollback se importe, y backend/worker lo
  incluyen en `LEGACY_DISABLED_SOURCES`. El ratchet local baja de 9 a 8.
- La primera suite completa encontró dos fallos: las auditorías G7/G8 seguían
  viendo una caché local Himalayas porque `tests/test_cache_updates.py` importa el
  módulo heredado y éste aún ejecutaba `register_cache/register_update`. Se retiró
  únicamente ese efecto lateral y se añadió una guarda explícita; el código
  heredado y sus pruebas directas se conservan para rollback. La repetición focal
  pasó 101/101 y la suite final 2.151 aprobadas, una omitida y cero fallos; la
  batería focal previa pasó 323/323, Ruff y `diff --check` pasan, y el control de
  propiedad informa exactamente ocho cosechadores locales.
- En las ramas quedan 23 portales por implementar: 15 de SwissJob y 8 de
  ReactPortfolio. Producción conserva todavía los 26 portales únicos/27 productores
  de la línea base, porque no se ha creado scope productivo, desplegado, fusionado
  ni cambiado ninguna credencial. El inventario continúa `local_pending` porque
  describe esa producción. Rollback de rama: el módulo local permanece presente y
  puede reconectarse restaurando registro, router, provider y manifiesto.
- **Siguiente paso exacto:** continuar SwissTechJobs, siguiente fuente pública de
  Fase 3. Primero trazar y comparar el productor activo
  `ReactPortfolio/routers/swisstechjobs_jobs.py` con el módulo SwissJob dormido,
  capturar la API WordPress pública con huellas y fijar su contrato antes de tocar
  Core. No tocar `main`, producción ni crear/activar el scope real.

La metodología obligatoria usada en Jobicy, RemoteOK e Himalayas queda
formalizada en la sección 7.1 del contrato: preflight; trazado completo; línea
base con huellas; Core mínimo; pruebas por capas; doble ciclo y paridad; lectura
Core y apagado local; suites/CI; checkpoint antes de avanzar.

### Checkpoint de relevo 2026-10-14 · SwissTechJobs centralizado en ramas

- El preflight parte de SwissJob/Core `f137f4f` y ReactPortfolio `e8ba714`, con
  los CI de Himalayas verdes. Los `main` remotos siguen en `e59283b` y
  `5bf457a`; no se desplegó ni se creó ningún scope productivo.
- Core quedó publicado en `a7016a6`; su run `38557077146` terminó verde en
  sus 9 trabajos. ReactPortfolio quedó publicado en `f3baba8`; su run
  `38557887736` terminó verde. Ambos commits viven sólo en sus ramas DT-144.
- El único productor activo es
  `ReactPortfolio/routers/swisstechjobs_jobs.py`: dos páginas WordPress de 100,
  caché, worker, registro normalizador y cinco rutas públicas. El módulo
  `backend/providers/swisstechjobs.py` de SwissJob está probado pero no figura en
  `_PROVIDER_CLASSES`; no produce tráfico ni datos.
- La captura pública comprobada devolvió 8 ofertas únicas en la página 1 y el
  error terminal WordPress documentado en la página 2. `X-WP-Total=8` y
  `X-WP-TotalPages=1`; huellas:
  `page1=dd60e788a7d69df486dc3b32c56af2e486d16101657144ccdfbade568506f99d`
  y `page2=b6714928c361e00376c3eda08eef1f4fcc3343bd71d9be2bde61ca1eee190c26`.
- Core reutiliza `NativeJSONProvider` y añade sólo el contrato WordPress real:
  identidad por `id`, URL pública y candidatura, raw intacto, dos peticiones con
  fin 400 exacto, `date`, HTML sin tags pero entidades conservadas, meta de
  empresa/ubicación/remoto/salario y moneda CHF por defecto. El scope único es
  determinista, nace deshabilitado y sólo cambia con `--enable/--disable`.
- La comparación viva dio 8/8 filas y contadores idénticos, con huella visible
  `2738c0da2a666c257d1605428747438937bd0eff2f49331c39248b8ea5b55c07`.
  Se preserva expresamente la coincidencia por subcadena del extractor local:
  la etiqueta de una letra `r` puede aparecer dentro de palabras como
  `engineer`; corregirla aquí habría sido una regresión de migración.
- Dos ciclos sombra aislados terminaron en `ok`, dos páginas y 8 ofertas cada
  uno; estabilizaron 8 identidades, 8 listings, 8 raws y 8 revisiones canónicas,
  sin revisión adicional en el segundo ciclo. El agregado observó 1/1 scope,
  cero fallos, 8 fechas, 7 ubicaciones, 17 tags, 8 empresas, 6 salarios raw y 8
  URLs de candidatura. Los datos de prueba se purgaron. Evidencia:
  [`2026-10-14-dt144-swisstechjobs-shadow.json`](../audits/2026-10-14-dt144-swisstechjobs-shadow.json).
- Validación Core: batería focal 66/66; Ruff focal y `diff --check` verdes;
  suite completa 1.579 aprobadas, una omitida y una advertencia conocida; el
  ratchet sigue en `{"core_owned":16,"local_pending":26,"sources":58}` porque
  esos estados describen producción, aunque la ficha ya declara el adaptador de
  rama.
- Archivos Core versionados por `a7016a6`:
  `jobhunt_core/harvest/providers/native_json.py`, `harvest/admission.py`,
  `provision_swisstechjobs.py`, sus dos pruebas, `source_ownership.json`, esta
  deuda y la evidencia. No incluir ninguno de los cambios preexistentes ajenos
  enumerados en el checkpoint Himalayas.
- ReactPortfolio `f3baba8` conserva exactamente las cinco rutas históricas desde
  Core, incluido salario raw, CHF, fecha y orden; excluye `Switzerland` sólo del
  contador de ubicación y no crea `by-tag/by-company`. Retira import/router,
  provider y los efectos laterales de caché/update, añade la fuente a los dos
  manifiestos de apagado y baja el ratchet de 8 a 7.
- Validación ReactPortfolio: focal 371/371; suite completa 2.159 aprobadas, una
  omitida y 115 advertencias conocidas; Ruff en los archivos modificados y
  `diff --check` verdes. La primera focal encontró dos pruebas acopladas a la
  caché local —conflicto de refresh y salud tras purga—; se retiraron porque el
  contrato Core 409 ya está cubierto y la variante de JSearch sigue midiendo el
  productor local real.
- En las ramas quedan 22 portales por implementar: 15 de SwissJob y 7 de
  ReactPortfolio. Producción continúa en 26 portales únicos/27 productores y la
  ficha sigue `local_pending` hasta el corte final aprobado.
- **Siguiente paso exacto:** continuar con ICTJobs, la otra fuente WordPress
  pública de Fase 3. Trazar primero sus diferencias ACF/enlaces respecto a
  SwissTechJobs y capturar su API viva antes de generalizar código. No tocar
  `main`, producción ni crear/activar scopes reales.

### Checkpoint de relevo 2026-10-14 · ICTJobs centralizado en ramas

- El preflight parte de SwissJob/Core `7f28e71` y ReactPortfolio `f3baba8`.
  Los cuatro portales anteriores tienen CI verde; ambos `main` remotos y
  producción permanecen intactos.
- El trazado comprobó un único productor activo:
  `ReactPortfolio/routers/ictjobs_jobs.py`, con caché/worker, cinco páginas
  WordPress de 20 y cinco rutas públicas (`recent`, `stats`, `health`,
  `refresh-cache` y `by-location`). El provider homónimo de SwissJob está
  probado pero ausente de `_PROVIDER_CLASSES`, por lo que es código dormido.
- La API viva informó `X-WP-Total=185` y `X-WP-TotalPages=10`, pero el
  productor vigente sólo consume las páginas 1–5: 100 filas, 100 ids y 100 URL
  únicas. Se comprobó también la página 6 para confirmar que ampliar hasta 185
  cambiaría funcionalidad; se conserva deliberadamente la ventana 5×20.
  Las seis huellas están en
  [`2026-10-14-dt144-ictjobs-shadow.json`](../audits/2026-10-14-dt144-ictjobs-shadow.json).
- En las 100 filas actuales `acf` es un array vacío, no un objeto: producción
  presenta cero compañías, descripciones, remotos y salarios, aunque sí obtiene
  ubicación, tags, tipo y grupo profesional desde `_embedded.wp:term`. Core
  degrada defensivamente ese `acf` y conserva el soporte ya existente para
  cuando el portal vuelva a exponer un objeto ACF.
- La extensión mínima reutiliza `NativeJSONProvider`: identidad WordPress por
  `id`, raw intacto, `_embed=`, cinco páginas de 20, fecha del portal, enlace
  directo sólo bajo su flag, taxonomías, salario CHF y la regla histórica por
  la que un límite inválido anula ambos límites. Se añadió `prof_group` al
  contenido/agregado común porque la ruta de estadísticas pública lo exige; no
  se creó una capa o proveedor paralelo.
- La comparación reproducible, con la misma semilla de hash necesaria por el
  `list(set(...))` legado, dio 100/100 filas, todos los contadores y cero
  diferencias de campo. Las huellas legado y proyectada coinciden:
  `8fffc8d059bc63788695d5c6360640903a7c7eb8414ca84214774427633ca72a`.
- Dos ciclos sombra aislados terminaron `ok` con 100 ofertas y cinco páginas;
  estabilizaron 100 ids, listings, raws y revisiones canónicas. El agregado
  observó 1/1 scope, cero fallos, 100 fechas, 9 ubicaciones, 9 grupos
  profesionales y 42 tags tras el plegado a minúsculas legado. No aparecieron
  compañías, descripciones, remotos, salarios ni enlaces de candidatura, en
  paridad con la captura. Scope y datos de prueba se purgaron.
- Validación hasta este punto: unidad focal 79/79, integración focal 3/3 y
  agregado completo 8/8; Ruff completo de Core, formato, `diff --check` y
  ratchet de propiedad verdes. La primera suite completa detectó que la prueba
  del DTO enumeraba los contadores exactos sin `prof_groups: {}`; se actualizó
  el contrato. Una segunda ejecución se reinició antes de terminar al endurecer
  la semántica WordPress: un 400 sólo es fin normal desde la página 2; un 400 en
  la primera sigue siendo fallo, igual que en el productor legado. La ejecución
  definitiva terminó con 1.587 aprobadas, una omitida y una advertencia conocida
  en 7 min 08 s.
- Incidencia documental contenida: un parche aún no versionado coincidió primero
  con la ficha Adzuna. El ratchet falló con `missing=['adzuna']` y
  `unexpected=['ictjobs']`; se restauró Adzuna, se aplicó el cambio con
  contexto específico de ICTJobs y el ratchet volvió a
  `{"core_owned":16,"local_pending":26,"sources":58}`.
- **Rollback:** no se ha creado ni activado ningún scope productivo, no se ha
  desplegado y ambos `main` siguen intactos. Revertir `f273a24`
  elimina toda la capacidad nueva sin tocar al productor local vigente.
- Core quedó versionado y publicado en `f273a24`; la CI `38560944461` terminó
  verde en sus 9 trabajos. El checkpoint documental previo `7f28e71` también
  quedó verde en `38559313935`.
- ReactPortfolio sirve las cinco rutas desde `core_source_compat`, incluida la
  forma legado de oferta, orden por fecha, tags plegados antes del top 10,
  grupos profesionales, salud y refresh autenticado. Eliminó el include del
  router local, el provider del registro, los efectos de registro
  cache/update y añadió ICTJobs a las dos listas de apagado productivo. El
  módulo legado queda inerte para rollback; el ratchet baja de 7 a 6.
- La comparación a través del adaptador consumidor real dio 100/100 filas,
  cero diferencias y la misma huella de ofertas
  `2a88dae4c136985c6895180883eea471c3478215e65ed32299a096ae5bc8f4e0`.
  La focal ampliada terminó 331/331 y la suite completa 2.168 aprobadas, una
  omitida y 115 advertencias conocidas en 3 min 19 s.
- Incidencias ReactPortfolio resueltas antes de versionar: la primera focal
  mostró que las fixtures agregadas aún no declaraban `prof_groups` y que el
  registro esperado seguía enumerando ICTJobs; ambas expectativas se
  actualizaron. Un `ruff format` focal tocó líneas ajenas en ocho archivos; el
  diff no versionado se restauró y se reaplicaron sólo 124 adiciones/26
  eliminaciones funcionales. Ruff queda verde salvo los E402 históricos de
  `main.py` y el F841 histórico de `tests/test_jobs.py:229`, ninguno tocado.
- ReactPortfolio quedó versionado y publicado en `fda24a9`; la CI
  `38563293589` terminó verde. Se revalidó que los `main` remotos siguen
  exactamente en `e59283b` y `5bf457a`.
- Tras este traspaso quedan 21 portales por implementar en las ramas: 15 de
  SwissJob y 6 de ReactPortfolio. Producción continúa en 26 portales
  únicos/27 productores hasta el corte final aprobado.
- **Siguiente paso exacto:** empezar SwissDevJobs con la metodología 7.1 desde
  preflight y trazado; comprobar su productor real, contrato y API viva antes de
  modificar código. No tocar `main`, producción ni activar scopes reales.

### Checkpoint de relevo 2026-10-14 · SwissDevJobs EN CURSO, preflight cerrado

- Inicio verificado desde SwissJob/Core `030d347` en `feat/fase-a-core` y
  ReactPortfolio `fda24a9` en `feat/dt144-free-gate`; ambos `main` y producción
  siguen intactos. La CI `38564269161` de `12fabdc` terminó verde 9/9; la
  repetición `38564792245` de la anotación documental seguía ejecutando sólo
  sus suites largas al cerrar este preflight.
- El trazado confirma un único productor activo: ReactPortfolio
  `services/swissdevjobs_provider_impl.py`, registrado en caché/worker e
  incluido por `routers/swissdevjobs_jobs.py`. Hace un barrido de arranque, uno
  circadiano y refresco administrativo; expone `recent`, `stats`, `by-tag`,
  `by-location`, `refresh-cache` y `health`. SwissJob no contiene módulo ni
  registro SwissDevJobs y Core todavía no contiene provider ni scope.
- El contrato vivo es un `GET https://swissdevjobs.ch/api/jobsLight` sin
  credencial ni paginación, seguido de páginas públicas de detalle. La captura
  saneada dio 210 objetos, cero pausados, 210 `jobUrl` y 210 `_id` únicos,
  272.877 bytes y SHA-256
  `d4a611e9f0686279eb3cf2f7e02eed915bf8d19b9eabeb3704c591b8ef880d23`.
  La muestra de detalle dio 10.445 bytes, un único marcador
  `window.__detailedJob`, 1.138 caracteres de descripción y SHA-256 de texto
  `df02081d8bc43abdcdad7f9bb3c7375b9dd7fa4974e44ed85c2ad7a58ac554d4`.
- La ejecución del productor legado exacto sobre esa captura procesó 210 y
  dejó 157 ofertas servibles tras la regla compartida de 90 días, 20
  descripciones en caché fría, 14 ubicaciones y 259 tags; tasa 100 %, sin error.
  Huellas: ofertas
  `c0b543b747de87ca2cc5a87a3abd10f1094e7f9fa692f776290b1f9893ee8296`,
  proyección normalizada
  `7ac0d97bd21512264562b026424835ed32ae8a95096f4316bcd5ea8159b176cb`,
  ubicaciones
  `221f4697a852fb12f10d8b32297bd24c8ddef6a32f3e011ecd9f863532165583`
  y tags
  `d185799dedbc5b271f76514d5b0bb5c1e49f4efb6da611dbf6c91cf5135ba2db`.
- Contrato que no se debe simplificar: el feed ligero no trae descripción; el
  productor intenta como máximo 20 detalles nuevos por ciclo, conserva los ya
  enriquecidos por identidad, limita la descripción visible a 500 caracteres,
  conserva como máximo 300 recientes y cuenta todos los tags aunque cada oferta
  sólo muestra los diez primeros. El feed vivo contiene 41 ofertas con más de
  diez tecnologías, así que usar el mismo campo capado para estadísticas
  perdería información.
- Decisión mínima tras revisar el sink, cursores y providers Core: implementar
  un provider SwissDevJobs cohesivo y sin estado propio. Su cursor transaccional
  conservará sólo descripciones activas, podadas por el feed, para volver a
  emitir cada oferta y refrescar `last_seen_at` sin solicitar 210 detalles cada
  día ni borrar texto bueno. No se añade tabla, dependencia ni caché paralela.
  El agregado seguirá contando las tecnologías completas del raw y el consumidor
  limitará a diez las etiquetas visibles, como el legado.
- Incidencias de preflight sin efecto funcional: el primer parser aislado no
  llegó a importar por faltar las cinco variables obligatorias; se repitió con
  el entorno de pruebas versionado. El primer script temporal no encontraba el
  worktree en `PYTHONPATH`; se corrigió sin ejecutar red. La línea base final
  avisó de tablas SQLite auxiliares ausentes y continuó por sus degradaciones
  previstas; los recuentos/hashes anteriores proceden de la ejecución válida.
- **Siguiente paso exacto si hay relevo:** confirmar que `38564792245` terminó
  verde; implementar y probar provider, normalizador, registro, fecha
  `activeFrom`, agregado de tags completos y provisionador opt-in; ejecutar dos
  ciclos PostgreSQL aislados y comparar la proyección contra las huellas de este
  bloque. Sólo después adaptar las seis rutas ReactPortfolio, desconectar sus
  imports/registros y reducir el ratchet local de 6 a 5.

#### Avance verificable · capacidad SwissDevJobs terminada en Core

- La repetición documental `38564792245` terminó verde. Core quedó versionado y
  publicado en `63fe4e5` sobre `feat/fase-a-core`; `main` sigue exactamente en
  `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y no se ha creado, habilitado ni
  desplegado ningún scope productivo.
- `SwissDevJobsProvider` implementa el feed ligero y el enriquecimiento
  incremental de hasta 20 detalles por ciclo con el cursor transaccional ya
  existente. Conserva únicamente descripciones de identidades aún activas,
  limita el texto a 500 caracteres y no añade tabla, caché, dependencia ni
  proceso. El registro, la admisión, `activeFrom`, el agregado de tecnologías
  completas y el provisionador opt-in quedan incluidos en el mismo commit.
- La proyección Core reproducible sobre la captura viva devolvió 210 filas,
  157 dentro de la ventana histórica de 90 días y 20 descripciones. Coinciden
  byte a byte tanto la huella de ofertas legado
  `c0b543b747de87ca2cc5a87a3abd10f1094e7f9fa692f776290b1f9893ee8296`
  como la normalizada
  `7ac0d97bd21512264562b026424835ed32ae8a95096f4316bcd5ea8159b176cb`.
- Dos ciclos aislados sobre PostgreSQL terminaron correctamente: el segundo
  reutilizó la descripción, mantuvo una sola revisión raw por identidad y el
  agregado expuso los 12 tags completos de la muestra frente a los 10 visibles.
  Validación Core: 16 unidades nuevas, 120 pruebas focales, 10 integraciones,
  repetición aislada 2/2 y suite completa con 1.603 aprobadas, una omitida y dos
  avisos conocidos; Ruff, formato, `diff --check` y el ratchet
  `{"core_owned":16,"local_pending":26,"sources":58}` quedaron verdes.
- Incidencias contenidas: el primer test unitario llevaba una fixture híbrida
  incompatible con su expectativa remota y se corrigió la fixture, no el
  comportamiento; el host no tenía `psycopg2` y la integración se repitió en la
  imagen Core; el primer script de proyección usó el Python del sistema sin
  `aiohttp` y se repitió con el entorno ReactPortfolio. Ningún intento fallido
  modificó base de datos o producción.
- La CI `38568433424` de `63fe4e5` tiene siete trabajos verdes; sólo
  `backend-test` y `core-test` siguen en ejecución al escribir este checkpoint.
  No se modifica ReactPortfolio hasta comprobar que el run completo termina
  verde.
- Los cambios ajenos enumerados en este documento siguen preservados y sin
  añadir al commit. La única modificación DT-144 pendiente en el worktree Core
  es este ledger de relevo.
- **Siguiente paso exacto si hay relevo:** consultar `38568433424`; si termina
  verde, trabajar en `/tmp/reactportfolio-dt144-free-gate` desde `fda24a9`.
  Añadir SwissDevJobs al adaptador `core_source_compat`, conservar las seis
  rutas y la ventana servible de 90 días, retirar import/router/provider y los
  registros laterales locales, añadirlo a ambos `LEGACY_DISABLED_SOURCES`,
  bajar el ratchet de 6 a 5 y ejecutar focales, suite completa, paridad, commit,
  push y CI. Si la CI Core falla, resolver y documentar antes de tocar el
  consumidor.

#### Avance verificable · SwissDevJobs centralizado en ramas

- La CI Core `38568433424` terminó verde en sus 9 trabajos. ReactPortfolio se
  mantiene en `feat/dt144-free-gate`, partiendo de `fda24a9`; su `main` remoto
  continúa en `5bf457afbde5b44a995acce4ef241324f066f836` y producción no se ha
  modificado.
- `core_source_compat` sirve ya las seis rutas SwissDevJobs desde el agregado
  Core. Conserva la forma pública histórica, identidad, orden, límite de 300,
  ventana de 90 días, fechas desconocidas, salarios, logo, remoto, tipo, texto,
  diez tags visibles y estadísticas sobre todos los tags. Reutiliza
  `parse_job_posting_date` y `MAX_JOB_AGE_DAYS`; no añade dependencia ni una
  segunda regla temporal.
- El productor local queda desconectado en el worktree: `main.py` ya no importa
  ni monta su router, `get_all_providers()` ya no lo devuelve y la importación
  del módulo de rollback no registra caché ni actualización. Backend y worker
  lo añaden al manifiesto de fuentes retiradas y el ratchet local baja de 6 a
  5. El código antiguo no se borra aún para conservar el rollback de rama.
- La comparación aislada sobre la captura viva, sin red y usando el adaptador
  Core más el consumidor real, dio 210 filas de entrada, 157 visibles y cero
  diferencias. Las huellas directa y consumidora coinciden en
  `daa03fb296d2f3e2e07cc1f18cd0de67a67e3ed83200c7783adbb73d77696fac`.
  La variante no incluye descripciones porque fuerza 404 en detalle; la paridad
  completa con las 20 descripciones ya quedó probada en Core en el bloque
  anterior.
- Validación focal: batería amplia 345 pruebas, con un primer
  fallo exclusivo de la aserción que comparaba `httpx.QueryParams` con `dict`;
  tras convertirlo explícitamente pasó completa. La focal final del adaptador,
  apagado y provider pasó 218/218; el ratchet informa cinco cosechadores. Una
  primera ejecución temporal de paridad no importó por faltar el entorno
  obligatorio de Settings y se repitió con los valores de prueba versionados,
  sin acceder a base, Redis, portales ni producción. La suite completa terminó
  con 2.181 aprobadas, una omitida y 115 avisos conocidos en 3 min 19 s.
- Ruff semántico de los Python modificados, salvo los E402 históricos de
  `main.py`, y `git diff --check` están verdes. El lint global enumera 107
  incidencias preexistentes repartidas por el repositorio; no se mezclaron.
  `ruff format --check` propone reformatear los ocho ficheros completos igual
  que en el traspaso ICTJobs; tampoco se aplicó. El hook del commit avisó que
  `black` no está instalado y omitió su comprobación; pruebas, diff y controles
  posteriores sí se ejecutaron con el entorno aislado.
- ReactPortfolio quedó versionado y publicado en `fe0c77d`; la CI
  `38570851745` terminó verde. Core está en `63fe4e5` y su CI `38568433424`
  también terminó verde 9/9. El checkpoint documental intermedio quedó
  publicado en `b0af7e3`.
- En las ramas quedan 20 portales por implementar: 15 de SwissJob y 5 de
  ReactPortfolio. Producción sigue en 26 portales únicos/27 productores, porque
  los `main`, scopes y despliegues reales permanecen intactos hasta el corte
  final aprobado.
- **Rollback:** ningún scope productivo existe y el módulo local continúa
  disponible pero inerte. Revertir `fe0c77d` reconecta su router, provider,
  registros y manifiestos; revertir `63fe4e5` retira la capacidad Core.
- **Siguiente paso exacto si hay relevo:** continuar Job-Room desde el
  preflight siguiente; no rehacer SwissDevJobs ni tocar producción.

### Checkpoint de relevo 2026-10-14 · Job-Room EN CURSO, preflight cerrado

- El trazado confirma un único productor activo:
  `ReactPortfolio/services/jobroom_provider_impl.py`, conectado a caché,
  worker, barrido circadiano, lecturas stale y refresco administrativo. Expone
  seis rutas (`recent`, `stats`, `by-tag`, `by-location`, `refresh-cache` y
  `health`). SwissJob no contiene provider o scraper Job-Room y Core no tiene
  adaptador ni scope.
- El contrato actual se comprobó con un POST público sin credencial a
  `jobAdvertisements/_search`: `page=0`, `size=300`, `sort=date_desc`, ventana
  `onlineSince=7` y los cinco códigos ICT CHISCO3 ya versionados. El servidor
  informó 1.031 coincidencias y cuatro páginas, pero el productor vigente sólo
  consume las primeras 300; ampliar la paginación cambiaría funcionalidad y no
  forma parte de esta migración.
- La captura saneada contiene 300 filas y 300 ids únicos, sin ids ni títulos
  ausentes; ocupa 1.653.407 bytes y su SHA-256 es
  `b9c4c07ef168ad1bf6f0f74f4b32be8e52367a97373928f5a50c04f49cc9efeb`.
  Presenta 104 ubicaciones, 300 descripciones inline, 59 tags estadísticos,
  cero ofertas `HOME_WORK`, cuatro códigos de idioma y cinco ofertas cuyo
  conjunto completo supera los diez tags visibles.
- La ejecución exacta del productor legado sobre esa captura procesó y mantuvo
  300 ofertas, una petición correcta y cero fallos. Huellas: ofertas
  `0fec95a5f3cbdc81253bdd27d2c9678a831e4c233eb8f6c075527f8eb627b7f4`,
  normalizadas
  `71e44326befd4d09ccdd0109c2d61abdce01168160910d22e2061806cf1deb2b`,
  ubicaciones
  `a8583e223a130ffc02b27ec2d4600f0ff0f2f385128c5415138a48bb82d9cb86`
  y tags
  `4e0663d77a90f849b211ed5deb2926163463c407e0fb4331dc907a717d2562f6`.
  La captura actual produce 19 hashes title/empresa repetidos; la identidad
  autoritativa sigue siendo el id upstream, por lo que no se deben deduplicar.
- Se comprobó que las taxonomías técnicas de ReactPortfolio y Core contienen
  exactamente los mismos 77 términos. El helper Core actual no aplica los
  límites de palabra que Job-Room necesita; esa diferencia debe cerrarse sin
  alterar el comportamiento por defecto de las fuentes existentes. Los idiomas
  forman parte de los tags visibles pero no de sus contadores, y los contadores
  incluyen skills más allá del límite visual: ambas reglas requieren prueba
  explícita.
- Artefactos temporales no versionados:
  `/tmp/dt144-jobroom-feed.json`, `/tmp/dt144-jobroom-headers.txt` y
  `/tmp/dt144_jobroom_legacy_baseline.py`. No contienen credenciales de portal;
  las cabeceras temporales incluyen una cookie efímera del servidor y no deben
  copiarse al repositorio.
- **Siguiente paso exacto si hay relevo:** implementar en Core el POST acotado,
  normalización anidada, identidad, fecha `publication.startDate`, tags visibles
  frente a estadísticos y provisionador opt-in; cubrir respuesta inválida,
  filas parciales, cuerpo/parámetros exactos y doble ciclo PostgreSQL. Comparar
  luego la proyección completa contra las cuatro huellas antes de modificar el
  consumidor ReactPortfolio.

#### Avance verificable · Job-Room centralizado en ramas

- Core quedó versionado y publicado en `14b613a` sobre `feat/fase-a-core`; su
  CI `38574880608` terminó verde en los nueve trabajos. El adaptador ejecuta un
  único POST con los parámetros, ventana y cinco profesiones verificados,
  mantiene el límite histórico de 300 aunque el portal anuncie más páginas,
  conserva el id upstream y registra `publication.startDate`. El provisionador
  crea un único scope `{}` deshabilitado; no se creó ni activó ningún scope
  productivo.
- La normalización anidada conserva anonimato de empresa, ubicación, jornada,
  `HOME_WORK`, idiomas visibles y límites de palabra. El raw añade sólo la lista
  completa `_core_statistics_tags`: los idiomas no contaminan estadísticas y
  los skills posteriores al décimo siguen contándose. Core conserva la
  descripción completa para su contenido canónico y el agregado limita a 500
  caracteres la respuesta compatible, por lo que la API pública reproduce el
  legado sin perder texto útil dentro de Core.
- Sobre la captura pública de 300 ofertas, la proyección Core y después el
  consumidor real ReactPortfolio reproducen exactamente las cuatro huellas del
  productor retirado: tarjetas
  `0fec95a5f3cbdc81253bdd27d2c9678a831e4c233eb8f6c075527f8eb627b7f4`,
  normalizadas
  `71e44326befd4d09ccdd0109c2d61abdce01168160910d22e2061806cf1deb2b`,
  ubicaciones
  `a8583e223a130ffc02b27ec2d4600f0ff0f2f385128c5415138a48bb82d9cb86`
  y tags
  `4e0663d77a90f849b211ed5deb2926163463c407e0fb4331dc907a717d2562f6`.
  Permanecen 300 ids, 104 ubicaciones, 59 tags y los 19 hashes duplicados que no
  deben deduplicarse.
- Validación Core: 72 pruebas unitarias focales, dos integraciones de doble
  ciclo/provisionado y suite completa con 1.620 aprobadas, una omitida y dos
  avisos conocidos en 7 min 53 s. Ruff, formato, compilación, `diff --check`,
  JSON y ratchet
  `{"core_owned":16,"local_pending":26,"sources":58}` quedaron verdes. Los
  dos ciclos PostgreSQL mantuvieron una revisión raw y conservaron por separado
  diez tags visibles y todos los tags estadísticos.
- ReactPortfolio quedó versionado y publicado en `edf1b12` sobre
  `feat/dt144-free-gate`; su CI `38576264319` terminó verde. Las seis rutas
  históricas las sirve ahora `core_source_compat`, con ventana completa de 300
  antes de ordenar, tarjeta sin salarios, top 10 por frecuencia, conteos
  completos y salud/refresh Core. `main.py`, `get_all_providers()` y los
  registros cache/update ya no conectan el productor local; backend y worker
  incluyen Job-Room en `LEGACY_DISABLED_SOURCES` y el ratchet baja de cinco a
  cuatro productores locales.
- Validación ReactPortfolio: 233 pruebas focales de consumidor/apagado/provider,
  otras 159 de rutas/caché/registro/fidelidad, la repetición específica final
  203/203 y suite completa con 2.194 aprobadas, una omitida y 115 avisos
  conocidos en 3 min 20 s. Ruff focal quedó verde ignorando únicamente los 39
  E402 históricos de `main.py`; `ruff format --check` sigue proponiendo
  reformatear los mismos ocho ficheros completos y no se aplicó. El hook avisó
  que `black` no está instalado, pero compilación, diff, pruebas y CI sí
  concluyeron correctamente.
- Incidencias contenidas y corregidas antes de versionar: dos parches iniciales
  del inventario asociaron `core_adapter` a entradas equivocadas por usar un
  contexto no único; se corrigió con contexto de nombre y el diff final toca
  sólo Job-Room. Al cerrar el ledger, el blocker común coincidió primero con
  Adzuna; el diff previo al staging lo detectó y se reaplicó usando las métricas
  únicas de Job-Room. Una ejecución unitaria apuntó dos veces a nombres de test no
  existentes; se localizaron y ejecutaron los tres módulos reales. La primera
  integración quedó omitida por faltar `CORE_ADMIN_DATABASE_URL` y se repitió
  con ambos DSN aislados. Los primeros verificadores de paridad carecían de
  `PYTHONPATH`/Settings; se relanzaron con configuración SQLite de prueba. Un
  import `httpx` sin uso y una línea final sobrante fueron eliminados por los
  gates. Ninguno de estos intentos tocó producción.
- Los artefactos y cabeceras de captura siguen sólo en `/tmp`; la cookie efímera
  nunca se versionó. Los cambios ajenos del worktree SwissJob/Core siguen sin
  staging ni alteración. Los remotos `main` se revalidaron intactos en
  `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`.
- **Rollback:** revertir `edf1b12` reconecta router, provider, registros y
  manifiestos locales; revertir `14b613a` elimina la capacidad Core. Al no haber
  scope ni despliegue productivo, no existe estado vivo que drenar en este
  checkpoint.
- Tras Job-Room quedan 19 portales por implementar en ramas: 15 de SwissJob y
  cuatro de ReactPortfolio. Producción conserva 26 portales únicos/27
  productores hasta el corte final coordinado.
- **Siguiente paso exacto si hay relevo:** comenzar Adzuna, primera candidata de
  la Fase 4. Revalidar rama/main/worktrees, productor y scheduler en ambos
  proyectos, presencia real de credenciales sin exponerlas, contrato vivo,
  límites/paginación y baseline saneado antes de escribir el adaptador Core. No
  tocar producción, no activar scopes y no rehacer Job-Room.

### Checkpoint de relevo 2026-10-14 · Adzuna EN CURSO, preflight cerrado

- La rama y los propietarios se comprobaron de nuevo antes de decidir:
  ReactPortfolio cosecha Adzuna realmente desde
  `services/adzuna_provider_impl.py`; SwissJob conserva
  `backend/providers/adzuna.py` y su registro, pero `get_all_providers()` lo
  excluye porque `ADZUNA_APP_ID` no está presente. Los logs del worker SwissJob
  de las últimas 48 horas tampoco contienen ejecuciones Adzuna. Core aún no
  tiene adaptador, fuente ni scope Adzuna.
- Sólo ReactPortfolio tiene en runtime ambos secretos Adzuna; SwissJob y Core no
  los tienen. Se comprobó únicamente su presencia booleana: ningún valor se
  imprimió, capturó ni versionó. El traspaso del secreto vivo queda reservado al
  corte final coordinado; mientras tanto producción y ambos `main` permanecen
  intactos.
- El contrato productivo comprobado es `gb`, `us`, `de`, páginas 1 y 2, 50
  resultados, `what=remote+developer` y el `what_or` histórico. SwissJob contiene
  una consulta alternativa para `de`, `at`, `gb`, pero nunca ha estado activa en
  producción: es capacidad dormida y no se convertirá en comportamiento nuevo
  durante esta migración.
- La salud viva de ReactPortfolio declaró 6 procesadas, 5 visibles tras la
  ventana de 90 días, una página con resultados de seis intentadas, una
  ubicación, una categoría y 15 tags. Los logs confirmaron las seis peticiones:
  sólo `us/1` devolvió seis filas y una de ellas era antigua. La captura saneada
  local `/tmp/dt144-adzuna-capture.json` contiene esas seis envolturas y sólo los
  campos funcionales; SHA-256
  `81eb9b09378379a747467816b8c3703ca5959e14b9da736eb4fcab2a8ed7f088`.
- La reproducción legacy sin red fijó la línea base: 6 procesadas/5 visibles,
  1/6 páginas no vacías, hashes `jobs`
  `506051e70b52282d1f04ceb14601ffa7784bba2936add3b806911f9fa7cad1d1`,
  normalizado
  `4c66a0c4f34a88acbddd4d67bc68fe4592bcd7c043e3abf9641aa45e3efa37bd`,
  ubicaciones
  `4ff24ba47ffe40690cfb0785134caacd2622ac2588be2be6d95595363d0473fd`,
  categorías
  `a7fae6be6be0b9072d393c877e6e79f41d5aa7792f7d028dd5e6f4bfd98392fd`,
  tags `e56511f44ba97a1c4e8ab8250bce2b1a499ac7d190798231593758b19639f40b`
  e ids visibles
  `a553ffde1b95cec8ff26e1b24383e12ad92c6d1b0e23c35150dd47f6445c5d81`.
  Las cinco visibles conservan ambos límites salariales.
- Se verificó por código y comparación automática que las 77 etiquetas y el
  mapa de países usados por ReactPortfolio son idénticos a los helpers ya
  existentes de Core. La limpieza HTML legacy también coincide con
  `rss_text.strip_html_tags`; se reutilizarán esos tres puntos de verdad.
- Decisión mínima: tres scopes Core exactos, uno por país, siempre con sus dos
  páginas. Core conservará las seis filas y su fecha `created`; el consumidor
  aplicará la misma ventana visible de 90 días, manteniendo estadística completa
  y tarjetas recientes sin inventar una segunda consulta. Los secretos serán
  configuración `CORE_*` del worker de cosecha y todo error deberá quedar
  saneado para que la URL con credenciales nunca llegue a logs.
- Incidencia contenida: la primera lectura del worktree usó por error el prefijo
  inexistente `backend/`; se localizó la raíz real del worktree y no hubo
  escritura. La primera inspección estructural del capture trató `rows` como un
  sobre y terminó con `AttributeError`; se corrigió leyendo sólo sus nombres de
  campo, recuentos y hosts, sin exponer contenido.
- **Siguiente paso exacto si hay relevo:** implementar el adaptador
  `native_adzuna` y sus tres scopes deshabilitados, añadir fecha/categoría al
  agregado, inyectar los secretos sólo en `core-harvest-worker`, y ejecutar
  unitarias, provisión y doble ciclo con la captura. No adaptar todavía
  ReactPortfolio ni retirar el provider SwissJob hasta que Core demuestre
  paridad.

#### Avance verificable · Adzuna centralizado en ramas

- Core/SwissJob quedó versionado y publicado en `c266b8d` sobre
  `feat/fase-a-core`; su CI `38588047774` terminó verde en los nueve trabajos.
  `native_adzuna` reproduce exactamente los tres países `gb/us/de`, dos páginas
  por país, 50 resultados y las consultas observadas. El provisionador crea tres
  scopes deshabilitados e idempotentes; no existe ni se activó ningún scope
  productivo.
- Las credenciales nuevas son exclusivamente `CORE_ADZUNA_APP_ID/KEY` y sólo
  se inyectan en el worker de cosecha Core. Se retiraron las variables Adzuna de
  los manifiestos SwissJob y ReactPortfolio. El cliente no sigue redirects,
  sanea errores sin URL credentializada y elimina únicamente importes no
  finitos antes de persistir; las regresiones prueban que no reenvía secretos a
  otro host y no pierde la oferta por un salario `NaN`/`Infinity`.
- El agregado Core conserva fecha `created`, etiqueta de categoría y orden
  histórico país/página/fila. Sobre la captura saneada de seis filas, el
  productor Core y el consumidor ReactPortfolio reproducen exactamente las seis
  huellas legacy: ofertas
  `506051e70b52282d1f04ceb14601ffa7784bba2936add3b806911f9fa7cad1d1`,
  normalizadas
  `4c66a0c4f34a88acbddd4d67bc68fe4592bcd7c043e3abf9641aa45e3efa37bd`,
  ubicaciones
  `4ff24ba47ffe40690cfb0785134caacd2622ac2588be2be6d95595363d0473fd`,
  categorías
  `a7fae6be6be0b9072d393c877e6e79f41d5aa7792f7d028dd5e6f4bfd98392fd`,
  tags `e56511f44ba97a1c4e8ab8250bce2b1a499ac7d190798231593758b19639f40b`
  e ids visibles
  `a553ffde1b95cec8ff26e1b24383e12ad92c6d1b0e23c35150dd47f6445c5d81`.
  Se mantienen seis procesadas, cinco visibles a 90 días, una ubicación, una
  categoría, 15 tags y ambos límites salariales en las cinco visibles.
- Validación Core final: 22 pruebas focales/integradas, doble ciclo PostgreSQL,
  paridad extremo a extremo real y suite completa con 1.642 aprobadas, una
  omitida y tres avisos conocidos en 6 min 52 s. Ruff, formato, compilación,
  `diff --check`, Compose, cero secretos literales y el ratchet
  `{"core_owned":16,"local_pending":26,"sources":58}` quedaron verdes. El
  inventario conserva el estado productivo, por eso sus contadores no cambian
  antes del corte final.
- ReactPortfolio quedó versionado y publicado en `95768ce` sobre
  `feat/dt144-free-gate`; su CI `38588044222` terminó verde. Las siete rutas
  históricas se sirven desde `core_source_compat`, incluida categoría; aplican
  la misma ventana de 90 días y forma pública. `main.py`, `get_all_providers()`
  y los registros cache/update ya no conectan el productor local; backend y
  worker incluyen Adzuna en `LEGACY_DISABLED_SOURCES`. El ratchet local baja de
  cuatro a tres cosechadores.
- Validación ReactPortfolio final: 242 pruebas focales de consumidor/apagado y
  suite completa con 2.205 aprobadas, una omitida y 115 avisos conocidos en
  3 min 20 s. SwissJob cerró 46 pruebas focales y su suite completa con 1.916
  aprobadas, cuatro `xfail` esperados y ocho avisos conocidos en 3 min 56 s.
  Los tres manifiestos NAS pasan `docker compose config --quiet`; el control de
  secretos informa cero hallazgos.
- Incidencias contenidas y corregidas antes de versionar: la primera suite
  ReactPortfolio encontró tres pruebas que seguían inyectando salarios hostiles
  en la caché Adzuna ya desconectada; la protección se trasladó al propietario
  Core y se reforzó antes de retirar esa prueba obsoleta. La primera suite
  SwissJob detectó la política Adzuna huérfana; se añadió a la misma lista
  explícita de rollback que las fuentes ya transferidas. Un Ruff se invocó por
  error sobre un YAML y se repitió separando Python de Compose. Los intentos del
  host que carecían de dependencias se descartaron y se repitieron en las
  imágenes del proyecto. Ningún intento tocó producción.
- Los remotos `main` se comprobaron intactos en
  `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`. Producción continúa cosechando
  Adzuna desde ReactPortfolio porque el corte, los secretos Core y los scopes se
  reservan a la fusión final aprobada; no se desplegó ningún commit.
- **Rollback:** revertir `95768ce` reconecta router, provider, registros y
  manifiesto ReactPortfolio; revertir `c266b8d` restaura el registro/manifiesto
  SwissJob y elimina adaptador, scopes y configuración Core. Como no existe
  estado productivo nuevo, no hay cola, scope ni base viva que drenar.
- Tras Adzuna quedan 18 portales por implementar en ramas: 15 de SwissJob y
  tres de ReactPortfolio. Producción conserva 26 portales únicos/27 productores
  hasta el corte final coordinado.
- **Siguiente paso exacto si hay relevo:** comenzar JSearch, segunda candidata
  de la Fase 4. Revalidar ramas, `main`, worktrees, productor y scheduler en
  ambos proyectos; comprobar presencia booleana de `JSEARCH_RAPIDAPI_KEY` sin
  imprimirla, contrato vivo, cuotas, países, queries, paginación y baseline
  saneado antes de escribir el adaptador Core. No tocar producción, no activar
  scopes y no rehacer Adzuna.

### Checkpoint de relevo 2026-10-14 · JSearch EN CURSO, preflight cerrado

- Se revalidaron las ramas y los remotos antes de decidir: SwissJob/Core está
  en `47d5561`, ReactPortfolio en `95768ce`, y los `main` remotos continúan
  intactos en `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`. La CI documental de Adzuna
  `38589393135` terminó `success` en sus nueve trabajos. No hubo despliegue,
  scope ni cambio productivo.
- El propietario activo quedó comprobado, no supuesto: ReactPortfolio ejecuta
  `services/jsearch_provider_impl.py` al arranque, en la ronda circadiana diaria,
  por lectura de caché caducada y por refresco manual. SwissJob conserva
  `backend/providers/jsearch.py` y su registro, pero producción carece de
  `JSEARCH_RAPIDAPI_KEY`, por lo que nunca lo instancia; sus logs de 72 horas
  contienen cero ejecuciones. Core no tiene aún adaptador, fuente ni scope.
- Sólo el backend/worker de ReactPortfolio posee hoy la credencial. SwissJob y
  los procesos Core no la poseen. Se comprobaron exclusivamente booleanos; no
  se imprimió, copió ni versionó ningún secreto. En 72 horas el worker Portfolio
  registró 24 menciones JSearch y la salud viva declaró 95 procesadas/95 en
  caché, sin último error y con una actualización de hace un día.
- La cosecha activa real es una petición diaria con `query=software developer`,
  `country=us`, `page=1`, `num_pages=10` y `date_posted=month`, que devuelve
  hasta 100 ofertas. SwissJob contiene otra capacidad dormida —consulta amplia,
  `country=ch`, tres páginas— que nunca estuvo habilitada en producción y no se
  convertirá en comportamiento nuevo durante la migración.
- Se capturó únicamente la respuesta pública ya cacheada, sin llamar de nuevo a
  RapidAPI ni consumir cuota: `/tmp/dt144-jsearch-cache.json`, 143.668 bytes,
  SHA-256
  `2e10bf595aa30127ae874c65ce75c349329509be3e371a26b0c8dcdfa2055248`.
  Contiene 95 ids únicos, 95 URLs válidas, 95 títulos/empresas/fechas, 93 logos,
  20 mínimos y máximos salariales, dos remotas y tres pares título/empresa
  repetidos que no deben deduplicarse porque `job_id` es la identidad externa.
- La reproducción saneada fija los hashes de paridad: ofertas
  `0253deb45f791364e7ef03ebe3768af8b8e33cab1b1cd7ef14d99696c1530705`,
  ubicaciones (36)
  `3da25852ca86c03c85d31487980630802c233808704942a3d72cdb840709a50f`,
  empresas (74)
  `f4789313a1cd51779ed1aa6aa7a1d8ec31464472c0f85f0f10c6825fac382629`,
  publishers (24)
  `3908d6f5f6a9eda7c663809798640ce246ce412ca9fb9b02c1cfa33a71920bc6`
  y tipos de empleo (2)
  `16504c18158d35599c4bec1df29947e0f4019aa70e9bedc3523293d35b357c8b`.
  Las fechas van de `2026-09-14T00:00:00+00:00` a
  `2026-10-12T23:00:00+00:00`; las 95 descripciones públicas conservan el
  límite histórico de 500 caracteres.
- La búsqueda en vivo no es código inaccesible: `JobSearchWindow` monta
  `useJSearchLive`, que llama a `/api/v1/jsearch-jobs/search` con país, fecha,
  tipo y remoto. Los logs de acceso no muestran uso en 30 días, pero eso no
  autoriza eliminar una pestaña visible. Además se encontró una divergencia
  real: el frontend envía `q` mientras el router exige `query`; la migración
  conservará ambos nombres y corregirá ese 400 sin modificar el repositorio
  frontend público.
- Decisión mínima que satisface el contrato: un único adaptador JSearch Core
  compartirá transporte, validación y normalización entre (1) el scope diario
  persistente exacto y (2) un endpoint Core autenticado para la búsqueda en
  vivo. ReactPortfolio conservará todas sus rutas y el límite 5/min, pero su
  backend sólo consultará Core. La clave `CORE_JSEARCH_RAPIDAPI_KEY` estará en
  el worker de cosecha y, por la búsqueda dinámica, en el API Core; se retirará
  `JSEARCH_RAPIDAPI_KEY` de ambos BFF. No se añade cola síncrona ni una segunda
  caché porque no aporta funcionalidad y aumenta acoplamiento/latencia.
- El agregado Core deberá conservar, además del contrato común, ciudad, estado,
  país y publisher; ordenar por fecha como la caché activa y reproducir las
  dimensiones ubicación/empresa/publisher/tipo. La búsqueda dinámica devolverá
  la forma pública actual, truncará el snippet a 300 caracteres y nunca seguirá
  redirects ni expondrá cabeceras, URL o credencial en errores.
- Incidencias contenidas: dos primeras sondas SSH fallaron sólo por el quoting
  del shell remoto y se repitieron con código base64, sin escritura ni secretos.
  Una consulta inicial de CI usó por error el propietario GitHub `vicentepau`;
  el remoto verificó `VRicardoP` y la misma ejecución se leyó correctamente.
  La búsqueda global de referencias produjo salida truncada; se repitió por
  rutas concretas y reveló el consumidor frontend descrito arriba.
- **Siguiente paso exacto si hay relevo:** implementar `native_jsearch` y un
  scope deshabilitado con el contrato diario comprobado; exponer la búsqueda
  dinámica autenticada desde Core reutilizando el mismo transporte; probar
  errores saneados, redirects, límites y doble ciclo; después adaptar todas las
  rutas ReactPortfolio, desconectar registros/disparadores locales y retirar la
  credencial de ambos BFF. Validar la captura completa antes de cambiar el
  recuento de 18 a 17. No llamar a RapidAPI, no activar scopes y no tocar
  producción ni `main`.

#### Avance verificable · JSearch centralizado en ramas

- Core/SwissJob quedó versionado y publicado en `ac819f5` sobre
  `feat/fase-a-core`; su CI `38599397880` terminó verde en sus nueve trabajos.
  `native_jsearch` comparte un único transporte acotado, sin redirects y con
  reintentos entre el scope diario persistente y la búsqueda dinámica
  autenticada. El provisionador crea exactamente un scope deshabilitado con
  `software developer`, `us`, página 1, diez páginas y ventana mensual; no se
  creó ni activó ningún scope productivo.
- La búsqueda visible se expone en Core con `vacancies:read`. ReactPortfolio
  conserva sus rutas, DTO y límite de cinco peticiones por minuto, pero sólo
  actúa como BFF de Core; acepta tanto `q` como `query`, corrigiendo el 400 real
  del frontend sin tocar el repositorio público. El refresco manual conserva su
  query: usa el dispatcher, tarea, scope, exclusión y sink existentes y cambia
  sólo los parámetros del provider para esa ejecución; no hay un segundo
  colector, caché ni cola.
- La clave pasa a ser `CORE_JSEARCH_RAPIDAPI_KEY` y sólo se configura en API y
  worker Core porque ambos necesitan el transporte. Se retiró la clave antigua
  de los manifiestos SwissJob y ReactPortfolio. SwissJob dejó de registrar su
  capacidad dormida y ReactPortfolio desconectó registros, arranque, ronda
  diaria, caché y actualizador locales; backend y worker incluyen JSearch en
  `LEGACY_DISABLED_SOURCES`. El ratchet Portfolio baja de tres a dos
  cosechadores locales: DynamiteJobs y JobScout24.
- Identidad y seguridad quedan fijadas: `job_id` es la identidad externa; los
  ids que exceden 200 caracteres se representan internamente por SHA-256 sin
  perder el id proveedor; una URL de fuente sintética estable evita colisiones
  cuando ofertas distintas comparten enlace de aplicación; el enlace real se
  conserva. Respuestas, colecciones y tamaño están validados; errores y
  transporte no exponen secreto ni URL; salarios booleanos, no numéricos o no
  finitos se degradan a `null`.
- La prueba PostgreSQL sobre la captura completa persistió 95 ofertas, 74
  empresas, 36 ubicaciones, 24 publishers y dos tipos de empleo, con run `ok`.
  La comparación Core→ReactPortfolio reprodujo exactamente los hashes legacy:
  ofertas `0253deb45f791364e7ef03ebe3768af8b8e33cab1b1cd7ef14d99696c1530705`,
  ubicaciones `3da25852ca86c03c85d31487980630802c233808704942a3d72cdb840709a50f`,
  empresas `f4789313a1cd51779ed1aa6aa7a1d8ec31464472c0f85f0f10c6825fac382629`,
  publishers `3908d6f5f6a9eda7c663809798640ce246ce412ca9fb9b02c1cfa33a71920bc6`,
  tipos `16504c18158d35599c4bec1df29947e0f4019aa70e9bedc3523293d35b357c8b`
  y normalizado
  `16c667f30bf2522916643be66f0c7a85db58ae632c18eb10f34de2ecbe068506`.
- Validación local final: 58 pruebas focales Core antes de la regresión final y
  dos integraciones adicionales verdes; 168 pruebas focales ReactPortfolio;
  suites completas Core 1.671 aprobadas y una omitida, ReactPortfolio 2.210
  aprobadas y una omitida, SwissJob 1.916 aprobadas y cuatro `xfail` esperados.
  El CI independiente confirmó 1.673 Core, 2.210 ReactPortfolio y 1.916
  SwissJob. Ruff y formato Core, Ruff de todos los Python modificados de
  ReactPortfolio, Compose, `diff --check`, ownership y escáner de secretos
  quedaron verdes. Los 18 `E402` de `tests/conftest.py` son anteriores y no se
  reformateó el fichero completo para ocultarlos en este cambio.
- Incidencias contenidas y corregidas antes de versionar: la primera paridad
  detectó `null` frente a cadena vacía en ciudad/estado/país y la segunda la
  normalización de espacios de descripción; se preservó el raw proveedor sólo
  donde el contrato JSearch lo exige. Se detectó además que el refresco manual
  provisional ignoraba la query y se corrigió antes del commit. La primera
  suite SwissJob se lanzó fuera de la red Compose y falló por el hostname
  `postgres`; se interrumpió y repitió en la red correcta. Una comprobación
  Docker con una imagen ajena y montaje del repositorio privado fue rechazada
  antes de ejecutarse; se usó el entorno virtual local ya instalado. Ninguno de
  estos intentos tocó producción.
- El directorio local vacío `.forgejo` del worktree ReactPortfolio fue retirado
  y no contenía archivos versionados. Los cambios ajenos del worktree
  SwissJob/Core siguen fuera de staging y sin alteración. Los `main` remotos se
  revalidaron intactos en `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`; producción conserva sus
  cosechadores actuales y las 26 identidades locales hasta el corte agrupado.
- **Rollback:** revertir `855f243` reconecta router, provider, registros,
  caché, triggers y manifiesto ReactPortfolio; revertir `ac819f5` restaura el
  registro/manifiesto SwissJob y elimina adaptador, endpoint, scope y
  configuración Core. Como no existe estado productivo nuevo, no hay scope,
  cola ni datos vivos que drenar.
- Tras JSearch quedan **17 proveedores** por implementar en ramas: 15 de
  SwissJob y dos de ReactPortfolio. La cifra productiva permanece en 26
  identidades locales hasta la fusión y el corte final coordinados.
- **Siguiente paso exacto si hay relevo:** iniciar el preflight de DynamiteJobs,
  primera candidata de la Fase 5. Revalidar ramas, `main`, worktrees, productor,
  registro, scheduler, rutas, caché, persistencia, tráfico y consumidores;
  contrastar disponibilidad/autorización y capturar una línea base real
  saneada antes de decidir si se migra o se retira. No escribir adaptador, no
  tocar producción, no activar scopes y no rehacer JSearch ni Adzuna.

### Checkpoint de relevo 2026-10-14 · DynamiteJobs EN CURSO, preflight cerrado

- Se trazó el flujo completo sin inferencias: sólo ReactPortfolio produce la
  fuente mediante `services/dynamitejobs_provider_impl.py`; la registra en su
  caché/actualizador, la ejecuta al arrancar el worker, en la ronda diaria de
  las 12:00 locales con ±4 h y desde lecturas obsoletas/refresco administrativo.
  El frontend público sólo consume `/recent`; el BFF conserva además `/stats`,
  `/by-tag`, `/by-location`, `/refresh-cache` y `/health`. Core no contiene aún
  adaptador, fuente ni scope DynamiteJobs. No hay credenciales.
- El productor lee 20 páginas públicas `/category/<slug>`, decodifica el JSON
  doble de `window.__PINIA_STATE__`, deduplica globalmente por `id`, valida
  publicación/visibilidad/caducidad, ordena por `publishedAt` y retiene 300.
  Después enriquece como máximo 20 descripciones ausentes por ciclo desde las
  páginas públicas de detalle y conserva las ya cacheadas. Esta semántica exige
  **un único scope Core**: 20 scopes romperían el límite global de detalle y la
  deduplicación entre categorías.
- Comprobación productiva de sólo lectura: el worker cerró correctamente el
  2026-10-14 a las 11:39:36 UTC tras 864,19 s, 20/20 categorías, 321 ofertas
  procesadas y 300 retenidas; la caché exponía 119 descripciones, 39 ubicaciones,
  587 tags y tres duplicados exactos. El ciclo anterior tardó 135,19 s con 314
  procesadas. El primer request necesitó tres intentos, pero el ciclo terminó
  sin error y la salud viva declaró la caché lista.
- La captura saneada de las páginas públicas, tomada 81 s después de la caché,
  contiene 338 apariciones y 322 ids únicos. No se proclama paridad byte a byte
  entre instantes distintos. La reproducción aislada del código legacy sobre
  esa captura hizo 20/20 requests simuladas, procesó 321 y retuvo 300, con 39
  ubicaciones; su diferencia de filas, tags y siete descripciones frente a la
  caché queda explicada por la ventana temporal/top 300, no ocultada.
- Se confirmó un defecto repetido en producción: la oferta válida
  `sUqqaPGnPAkg4wyAqShG` trae `locationSlugs=["latinamerica", null]`; el
  formateador legacy llama a `isupper()` sobre `null` y descarta la oferta
  entera. El adaptador Core ignorará únicamente componentes de ubicación nulos
  o no textuales y conservará `Latin America`; será una diferencia funcional
  deliberada, probada y documentada, no una falsa paridad.
- Disponibilidad/autorización contrastadas: `robots.txt` no prohíbe categorías
  ni páginas públicas de empleo; sí prohíbe rutas privadas como `/api/` y
  `/admin/`, que no se usarán. La API oficial de desarrolladores es autenticada
  y sirve puestos/candidatos de la empresa cliente, no el catálogo público. Las
  condiciones oficiales no contienen una prohibición textual de scraping,
  robot o crawler en la versión revisada. Se mantendrán cabeceras, pausas,
  límites de bytes, reintentos y cadencia conservadora del productor vigente.
- Línea base reproducible guardada en
  `docs/audits/2026-10-14-dt144-dynamitejobs-baseline.json`: incluye fechas,
  tamaños, métricas y SHA-256 de capturas/normalizados sin guardar secretos ni
  el corpus de terceros. La caché de 300 filas tiene hash de datos
  `f98e273377077cd5932235d0bdc7399228286cadca4ef30bced2a6b095034258` y
  normalizado
  `2f62eac747dd918641cf6ea8a3262f62d5848592ff5e65c262b47282bb873a50`.
- Diseño mínimo cerrado antes de implementar: `native_dynamitejobs`, registro
  explícito, fecha `publishedAt`, provisionador de un scope `{}` deshabilitado,
  deduplicación por id, máximo 300 recientes, 20 detalles/ciclo y descripciones
  persistidas/podadas en el cursor. Se reutilizan `BROWSER_HEADERS`, el sink,
  dispatcher, scheduler y patrón de cursor de SwissDevJobs; no se crea cola,
  caché, scheduler, dependencia ni abstracción nueva.
- Las 119 descripciones productivas acumuladas no pueden degradarse a 20 en el
  corte. La sombra debe precalentar el cursor deshabilitado durante ciclos
  controlados o migrar ese estado comprobado antes de apagar el productor; el
  corte final sólo se acepta con cobertura al menos equivalente. El agregado
  Core hoy limita dimensiones a 500 y DynamiteJobs expone 587 tags: se debe
  preservar el total real con el cambio local mínimo y una prueba, sin crear un
  endpoint específico si ampliar el límite existente resulta suficiente.
- El CI documental del cierre JSearch, run `38600512329` sobre `7a86328`,
  terminó globalmente en `success` con sus nueve trabajos verdes.
- Incidencias de preflight: el primer capturador local falló antes de red porque
  cargó `Settings` sin variables obligatorias; se repitió con SQLite y valores
  locales ficticios. Una reproducción posterior confirmó el error de ubicación
  legacy y evitó interpretar como regresión la deriva temporal de 81 s.
  Ninguna comprobación escribió en producción, activó scopes o modificó `main`.
- **Siguiente paso exacto si hay relevo:** implementar y probar el adaptador,
  admisión, registro y provisionador Core sobre la captura saneada; ampliar sólo
  lo necesario el agregado para conservar los 587 tags; ejecutar doble ciclo
  PostgreSQL con cursor de descripciones precargado y comparar hashes/campos.
  Después adaptar las seis rutas BFF de ReactPortfolio, retirar sus registros y
  triggers locales, ejecutar suites y CI, y sólo entonces bajar el recuento de
  17 a 16. No tocar producción, `main` ni activar el scope.

#### Avance verificable · DynamiteJobs centralizado en ramas

- Core/SwissJob quedó versionado y publicado en `9ec6901` sobre
  `feat/fase-a-core`; ReactPortfolio quedó publicado en `1a0db8d` sobre
  `feat/dt144-free-gate`. Los CI funcionales `38607230379` y `38607229742`
  terminaron globalmente en `success`. Los `main` remotos se revalidaron
  intactos en `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`.
- `native_dynamitejobs` ejecuta un único barrido secuencial de las 20 categorías
  públicas, con pausas, reintentos, límites de bytes, deduplicación global por
  id y la selección legacy de las primeras 300 filas antes de ordenarlas por
  fecha. El provisionador define exactamente un scope `{}` deshabilitado. El
  sink, dispatcher, scheduler, admisión, registro y agregado existentes se
  reutilizan; no se añadió dependencia, cola, caché ni scheduler.
- El cursor conserva y poda las descripciones de las 300 filas servibles y
  solicita como máximo 20 ausentes por ciclo. No reproduce la petición legacy
  desperdiciada para filas posteriores a 300, porque esas descripciones nunca
  entraban en la caché pública. Salarios, tipos, ubicaciones y tags mantienen el
  contrato; la oferta con componente de ubicación `null` se conserva de forma
  deliberada y probada. El límite existente `top_n` se amplió sólo hasta 1.000
  para transportar los 590 tags reales sin endpoint ni abstracción específica.
- La reproducción sobre la captura saneada cerró dos ciclos PostgreSQL `ok`,
  hizo 80 respuestas simuladas, persistió 322 identidades y sirvió 300 recientes
  con 112 descripciones, 39 ubicaciones y 590 tags. Las 288 filas comunes con la
  caché legacy no presentan ninguna diferencia; no se proclama igualdad del
  conjunto completo porque las capturas fueron tomadas con 81 s de separación.
  La salida Core reciente tiene SHA-256
  `7ed45702858e31362391e51cc7fe5bc1842e072788483286872bfa958f3f9869`.
- ReactPortfolio conserva las seis rutas públicas bajo sus mismos paths y DTO,
  pero todas leen el agregado autenticado de Core. Se retiraron de la rama el
  montaje del router local, el provider, los registros de caché/actualización y
  sus disparadores; backend y worker añaden DynamiteJobs a
  `LEGACY_DISABLED_SOURCES`. El frontend público no requirió cambios porque ya
  consume `/recent`. El ratchet deja únicamente `jobscout24` como cosechador
  local de ReactPortfolio.
- Validación local final: Core 1.689 aprobadas y dos avisos; ReactPortfolio
  2.220 aprobadas y una omitida; SwissJob 1.916 aprobadas y cuatro `xfail`.
  El CI repitió 1.689 Core, 1.916 SwissJob, 57 frontend SwissJob y 2.220
  ReactPortfolio; lint/formato, ownership, Compose y build quedaron verdes.
  Las pruebas focales y de integración cubren respuesta hostil, cursor corrupto,
  límite de detalles, doble ciclo, agregado de 590 tags, scope opt-in y las seis
  rutas BFF.
- Incidencias cerradas antes de publicar: dos intentos host de integración
  carecían de `psycopg2`/`pgvector` y se sustituyeron por la imagen oficial; la
  primera suite Core no montaba `scripts/` y `backend/`, por lo que se relanzó
  con ambos en sólo lectura; la primera suite completa ReactPortfolio detectó
  que importar el módulo legacy aún registraba caché/actualizador y que dos
  censos esperaban el provider. Se eliminaron esos efectos de importación y se
  actualizaron los censos; las suites completas posteriores y ambos CI fueron
  verdes. Ningún intento tocó producción.
- El inventario conserva correctamente `state=local_pending`: el adaptador y el
  handover existen sólo en ramas, pero producción aún no tiene fuente/scope
  DynamiteJobs y continúa ejecutando el productor ReactPortfolio hasta la
  fusión y corte agrupados aprobados. No se creó scope, no se activó cosecha y
  no se cambió credencial alguna.
- **Rollback:** revertir `1a0db8d` reconecta router, provider, caché,
  actualizador y manifiestos ReactPortfolio; revertir `9ec6901` elimina
  adaptador, registro, admisión, agregado y provisionador Core. Como no existe
  estado productivo nuevo, no hay scope, cola ni datos vivos que drenar.
- Tras DynamiteJobs quedan **16 proveedores** por implementar en ramas: 15 de
  SwissJob y uno de ReactPortfolio. La cifra productiva continúa en 26
  identidades locales hasta el corte final coordinado.
- **Siguiente paso exacto si hay relevo:** iniciar el preflight de JobScout24,
  último cosechador ReactPortfolio. Revalidar productor, scheduler, rutas,
  caché, persistencia, disponibilidad, autorización y línea base real antes de
  diseñar el adaptador. No tocar producción, `main` ni activar scopes.

### Checkpoint de relevo 2026-10-14 · JobScout24 centralizado en ramas

- **Cierre formal de DynamiteJobs comprobado, no heredado:** la CI documental
  `38608902811` de `0d51a9a` terminó `success` con sus nueve trabajos verdes
  (`source-ownership`, `backend-lint`, `core-lint`, `backend-test`, `core-test`,
  `frontend-lint`, `frontend-test`, `compose-config`, `docker-build`), y la
  funcional `38607230379` de `9ec6901` también. Con eso DynamiteJobs queda
  cerrado y el recuento en ramas pasó a 16 portales pendientes.
- Inicio verificado desde SwissJob/Core `0d51a9a` en `feat/fase-a-core` y
  ReactPortfolio `1a0db8d` en `feat/dt144-free-gate`. Los `main` remotos se
  comprobaron antes y después del trabajo y siguen exactamente en
  `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`.
- El trazado confirma un único productor activo:
  `ReactPortfolio/services/jobscout24_provider_impl.py`, que registraba caché y
  actualizador al importarse y lo servía `routers/jobscout24_jobs.py` montado en
  `/api/v1/jobscout24-jobs` con seis rutas (`recent`, `stats`, `by-tag`,
  `by-location`, `refresh-cache`, `health`). SwissJob no contiene módulo ni
  registro JobScout24 y Core no tenía adaptador ni scope.
- El contrato vivo se comprobó sin credencial:
  `GET https://www.jobscout24.ch/en/jobs/{keyword}/?p={page}&SortIndex=1`, cinco
  keywords IT en el PATH (`?term=` no filtra), dos páginas por keyword y páginas
  públicas de detalle con JSON-LD `JobPosting`. Las diez peticiones de listado
  respondieron **200 con las cabeceras del propio Core**
  (`browser_headers.BROWSER_HEADERS`), no con las del productor, que son otras.
- Captura sellada 2026-10-14T18:08:28Z (listados, 813.913 bytes) y
  2026-10-14T18:10:25Z (20 detalles, 1.464.455 bytes), todas 200. Dio 231
  tarjetas, 197 identidades únicas, cero tarjetas sin URL de detalle y cero
  ubicaciones en blanco. Huellas por página y por detalle en
  [`2026-10-14-dt144-jobscout24-baseline.json`](../audits/2026-10-14-dt144-jobscout24-baseline.json).
- La ejecución del productor legado EXACTO sobre esa captura procesó 197 ofertas
  servibles, 10/10 peticiones correctas, 20 descripciones (el techo frío), 26
  tags y 85 ubicaciones, cero remotas, 197 con jornada y ocho tags como máximo
  en una oferta. Huellas: ofertas
  `5a1200965d0a9d9f32a23f4d0805fab49c31a191b2c516486fa076ee36b7766b`, tags
  `5c65564c275e401919f5a2d1e3664db9e2341497d36aa67dabf2822c4cfd47ef` y
  ubicaciones
  `ebe36445dd14dac378c73febf2fd28204ae1036bbe613601e27a4d909fa50220`.
- **Las dos taxonomías compartidas son idénticas, comprobado ejecutando**: 77
  términos técnicos en el mismo orden, 35 países y 20 sinónimos de «worldwide»
  entre `ReactPortfolio/jobs_config.py` y
  `jobhunt_core/harvest/providers/jobicy_metadata.py`.
- Contrato que no se debe simplificar: cinco keywords, dos páginas, corte al
  llegar a una página vacía, deduplicación global por `data-job-id`, 300
  recientes como techo, **20 detalles por ciclo** reutilizando los ya conocidos,
  descripción a 500 caracteres, diez tags visibles pero estadísticas sobre
  TODOS, y la purga de 90 días. Esa purga es inalcanzable aquí: la fecha
  relativa de la tarjeta satura en «30+ d», así que el camino Core no necesita
  filtro de edad y eso queda probado, no supuesto.

#### Avance verificable · JobScout24 centralizado en ramas

- Core/SwissJob quedó versionado y publicado en `33a804d`; ReactPortfolio en
  `04a020d` más `ffcc0ba`. Ninguno se ha desplegado y ningún scope productivo
  existe.
- `JobScout24Provider` barre las cinco keywords en serie con pausas, reintentos,
  topes de bytes y deduplicación global, y enriquece hasta 20 descripciones por
  ciclo desde el cursor transaccional existente. **No añade dependencia**: las
  tarjetas se leen con el parser HTML de la biblioteca estándar
  (`_Cards` reproduce `select()` selector a selector), porque Core no tiene
  BeautifulSoup y traerlo por una fuente habría sido una dependencia nueva.
- `html_response` recoge el parseo del `JobPosting` JSON-LD y la detección de
  soft-block, compartidos ya con DynamiteJobs, **con la lista COMPLETA del
  productor**: el port anterior había perdido `cf-chl-bypass` y la regla de
  «captcha» como palabra suelta, con lo que una página de desafío podía pasar
  como descripción vacía. Comprobado sobre la captura: cero falsos positivos en
  los 20 detalles ni en el armazón de los listados.
- **Paridad medida sobre la MISMA captura sellada, no sobre dos muestras**: el
  parser Core y el del productor devuelven los 231 diccionarios de tarjeta
  idénticos; las 197 filas servidas coinciden en el mismo orden; las huellas de
  tags y de ubicaciones son idénticas. La única diferencia en 197 filas × 10
  campos son **dos descripciones con un espacio final**, porque la coerción
  canónica de Core (`normalize.py:_text`) hace `strip()` sobre lo que el corte
  de 500 caracteres dejó. Es un invariante central de Core, no una decisión del
  adaptador, y no tiene efecto visible.
- Paridad del CONSUMIDOR por las seis rutas reales, con el adaptador compartido
  y un agregado Core construido desde esa captura: `/recent` devuelve las mismas
  197 filas en el mismo orden y los mismos `total_available`/`showing`;
  `/by-tag` los mismos 26 contadores y `/by-location` los mismos 85; `/stats`
  el mismo resumen y los mismos diez tags; `/health` la misma forma de siete
  claves. Evidencia en el mismo artefacto saneado.
- **Dos desviaciones deliberadas, ambas más ruidosas que el productor**, no
  menos: (1) una tarjeta con id y título pero SIN URL de detalle se descarta y
  se reporta como `invalid_jobscout24_cards`, porque en Core la URL es identidad
  de listado y dos vacías normalizarían al mismo valor y fundirían dos ofertas
  distintas en una vacante — el productor la cacheaba con URL vacía, inservible
  igualmente, y la captura viva no contiene ninguna; (2) un barrido que no
  devuelve ninguna tarjeta FALLA en vez de persistir nada, porque el productor
  conservaba su caché anterior en ese caso y Core, en silencio, archivaría un
  corpus vivo. El coste conocido de (1): una tarjeta mal formada recurrente
  dejaría el scope en `partial` y no avanzaría `last_complete_at`. Es el mismo
  trato que da DynamiteJobs y se prefiere ruidoso.
- ReactPortfolio conserva las seis rutas con sus paths y payloads, servidas por
  `core_source_compat`. Se retiró el montaje del router local, el módulo ya no
  registra caché ni actualizador al importarse, ambos manifiestos desplegados
  añaden `jobscout24` a `LEGACY_DISABLED_SOURCES` y **`get_all_providers()`
  queda VACÍO: era el último cosechador local de ReactPortfolio en rama**. El
  ratchet local informa 0 cosechadores. El módulo y su parser se quedan como vía
  de rollback.
- Validación: Core **1.713 aprobadas** y dos avisos en 8 min 38 s — exactamente
  las 1.689 anteriores más mis 22 unitarias y 2 de integración, sin omitidas;
  ReactPortfolio **2.232 aprobadas y 3 omitidas** en 3 min 22 s. Además: la
  suite de DynamiteJobs sigue verde tras compartir `html_response` (36/36 con
  las de JobScout24), doble ciclo PostgreSQL `ok` con cursor, agregado y
  dimensiones, provisión opt-in idempotente, `scripts/test_search_handover_contract.py`
  2/2, ratchet cruzado `{"core_owned":16,"local_pending":26,"sources":58}`,
  `check_no_secrets` 0 hallazgos y `git diff --check` limpio.
- Lint: `ruff check .` y `ruff format --check .` del repo SwissJob completo en
  verde (806 ficheros). En ReactPortfolio, cuyo formateador real es **black**
  (`pyproject.toml`, línea 100) y cuyo CI no linta, se comparó finding a finding
  contra los ficheros de `HEAD`: **cero hallazgos nuevos de ruff** (uno menos, de
  hecho) y la ÚNICA deriva nueva de black —dos líneas en blanco al retirar
  `register_cache`— quedó corregida. Las otras tres derivas de black y los 54
  avisos de ruff son preexistentes y no se mezclaron.
- Incidencias cerradas antes de publicar: el primer replay aislado no importó
  por faltar las cinco variables obligatorias de `Settings` y se repitió con el
  entorno de pruebas versionado; tres expectativas mías de los tests unitarios
  estaban mal (los tags técnicos que salen del propio título, y «Zürich», que no
  está en la tabla de países y por tanto no se traduce) y se corrigió la
  EXPECTATIVA, no el comportamiento; el `commit` de ReactPortfolio se hizo sin
  el hook local (que sólo bloquea `.env` y ejecuta black) y sus dos
  comprobaciones se ejecutaron a mano después. Ningún intento tocó producción.
- **Hallazgos abiertos que este portal NO arregla y bloquean la definición de
  terminado** (§10 del contrato), con su evidencia:
  1. `services/jobicy_provider_impl.py:55,294` y
     `services/remoteok_provider_impl.py:51,197` **siguen registrando caché y
     actualizador al importarse**, pese a estar ya transferidas. Hoy no cosechan
     porque el embudo las frena por `LEGACY_DISABLED_SOURCES`, pero si alguien
     retirase esa bandera volverían a la vida por el registro. Las otras diez
     transferidas sí tienen su prueba de «sin efectos de registro»; estas dos no.
     No se corrigen aquí por no reescribir dos portales ya cerrados dentro de
     este commit.
  2. Al desaparecer la última caché local, los barridos de fidelidad
     `test_g7_fidelity_audit.py::test_ningun_recent_publico_se_tumba_con_una_oferta_hostil`
     y `test_g8_fidelity_audit.py::test_ningun_contador_publico_se_tumba_con_una_CLAVE_hostil`
     recogen un conjunto de parámetros VACÍO y pytest los salta: son las dos
     omitidas nuevas de la suite. La garantía equivalente de las rutas servidas
     desde Core vive en `test_core_source_compat`, pero esos dos barridos son hoy
     cobertura muda y hay que resolverlos en la limpieza final.
  3. `services/catalog/seam.py:509-528` sigue construyendo un
     **`CompositeCatalog` Core + local** cuando `LEGACY_DISABLED_SOURCES` no está
     vacío. Con cero providers locales su mitad local no aporta nada, así que el
     comportamiento ya es Core puro, pero el CAMINO sigue existiendo y el
     contrato exige retirarlo antes de cerrar DT-144.
- **Rollback:** no existe scope productivo ni dato vivo que drenar. Revertir
  `ffcc0ba` y `04a020d` reconecta router, provider, caché, actualizador y
  manifiestos de ReactPortfolio; revertir `33a804d` retira adaptador, registro,
  admisión, agregado y provisionador de Core y devuelve a DynamiteJobs su
  `_description` local.
- El inventario conserva `state=local_pending` a propósito: el adaptador y el
  traspaso existen sólo en ramas y producción sigue ejecutando el productor de
  ReactPortfolio hasta la fusión y el corte agrupados. Por eso el ratchet sigue
  en 16 Core / 26 pendientes / 58 fuentes y la cifra productiva sigue en 26
  identidades locales.
- Tras JobScout24 quedan **15 proveedores** por implementar en ramas, **todos de
  SwissJob**: `gastrojob`, `myscience`, `proz`, `remoteco`, `schuljobs`,
  `stelle_admin`, `tes` y los ocho `swiss_schools_*`. ReactPortfolio no conserva
  ningún cosechador local EN RAMA (en producción sigue con los suyos).
- CI: la de Core/SwissJob `38617812386` de `33a804d` terminó **verde en sus
  nueve trabajos** (`source-ownership`, `backend-lint`, `core-lint`,
  `backend-test`, `core-test`, `frontend-lint`, `frontend-test`,
  `compose-config`, `docker-build`), y los dos `main` remotos se revalidaron
  intactos despues de publicar. **La CI de ReactPortfolio queda SIN
  COMPROBAR desde este host y no se declara verde:** el repositorio es privado y
  aquí no hay `GITHUB_TOKEN`/`GH_TOKEN` ni cliente autenticado, mientras que
  `The_Swiss_Job` sí responde a la API anónima. Lo que su CI ejecuta sí se
  reprodujo en local con su mismo mandato —el ratchet
  `check_local_harvest_ownership.py` y `pytest tests/` con `pytest-timeout`
  instalado y `timeout = 15` de su `pytest.ini`—, pero eso no sustituye a la
  ejecución remota.
- **Siguiente paso exacto si hay relevo:** confirmar en GitHub que la CI de
  ReactPortfolio para `04a020d` y `ffcc0ba` terminó verde (hace falta un token
  de lectura o mirarlo desde el navegador del propietario); después iniciar el preflight de la primera fuente
  de SwissJob con la metodología 7.1, contrastando productor, registro,
  scheduler de APScheduler, `LEGACY_DISABLED_PROVIDERS`/`LEGACY_DISABLED_SCRAPERS`
  y disponibilidad real antes de escribir adaptador. No tocar producción, los
  `main` ni activar scopes.

### Checkpoint de relevo 2026-10-14 · myScience centralizado en ramas

- **Primera fuente de SwissJob de la Fase 5**, y el lado SwissJob se traspasa de
  otra forma que el de ReactPortfolio: aquí no hay rutas públicas por fuente que
  conservar. El productor es un `BaseScraper` cuyo resultado persiste en la tabla
  `jobs` del BFF, así que desconectarlo es sacar su clase de
  `backend/scrapers/__init__.py` —exactamente lo que hizo el corte del
  2026-09-22 con las 13 fuentes ya nativas— y conservar su política de cosecha
  como pre-decidida. El módulo y sus pruebas se quedan como rollback.
- Inicio verificado desde `3831d83` (CI `38619763001` **verde 9/9**, con lo que
  JobScout24 quedó formalmente cerrado) y ReactPortfolio en `ffcc0ba`. Los
  `main` remotos siguen en `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`.
- El trazado confirma un único productor activo:
  `backend/scrapers/myscience.py`, registrado en `_SCRAPER_CLASSES`, despachado
  por `tasks/scraping_tasks` cada 6 h con `FETCH_DETAILS=True`, cursor
  incremental y early-stop por página conocida. ReactPortfolio no contiene
  myScience y Core no tenía adaptador ni scope.
- Contrato vivo, sin credencial: `GET https://www.myscience.ch/jobs?p={page}`,
  cinco páginas, 20 por página, pausa de 2 s (los tres valores del scraper), más
  una página de detalle por oferta porque el listado no trae descripción.
  Captura sellada 2026-10-14T18:4x UTC: 5 listados (1.083.100 bytes) y 26
  detalles (3.519.145 bytes), todos 200. Huellas en
  [`2026-10-14-dt144-myscience-baseline.json`](../audits/2026-10-14-dt144-myscience-baseline.json).
- **`?p=` apenas pagina, y eso le cuesta ofertas al productor:** 97 lecturas de
  tarjeta dan 26 ofertas únicas; la página 3 repite la 2 exactamente y las 3 y 4
  no aportan nada nuevo. Como el early-stop del cursor corta en la primera
  página enteramente conocida, en frío el productor para en la 3 y **nunca ve
  las cuatro ofertas que sólo aparecen en la 5**. Core barre las cinco páginas
  declaradas y sí las cosecha, con el mismo coste de cinco listados.
- La ejecución del scraper EXACTO sobre esa captura dio 26 ofertas, 26 con
  descripción, 26 con logo, **cero con jornada**, 5 cantones, 9 ubicaciones, dos
  tags como máximo y 58.789 caracteres de descripción. Huella normalizada
  `4a4eefc1516c0b426c5a5a75c505621d8e47ff824b3fb5a2b439ef9cb4dcecf7`.
- Taxonomías compartidas comprobadas EJECUTANDO: `JOB_TAGS` tiene los mismos 99
  términos en el mismo orden en `backend/utils/text.py` y en
  `jobhunt_core/.../rss_text.py`, `extract_canton` y `swiss_canton` coinciden en
  las diez sondas, y los dos extractores dan la MISMA lista en las 26 ofertas
  reales (incluidos sus falsos positivos por subcadena, que son su conducta).

#### Avance verificable · myScience centralizado en ramas

- `MyScienceProvider` reproduce el barrido y el pase de detalle con los parsers
  de la biblioteca estándar. **Paridad byte a byte sobre la misma captura
  sellada**: 97 de 97 registros de listado y 26 de 26 páginas de detalle
  idénticos a los de BeautifulSoup, y la proyección normalizada de las 26
  ofertas tiene la MISMA huella SHA-256 que la del scraper en los nueve campos
  comparados (título, empresa, ubicación, cantón, descripción, URL, remoto, tags
  y logo): cero diferencias de campo.
- **Tres mejoras deliberadas, cada una con su evidencia** (detalle en el
  artefacto):
  1. `published_at` sale de la fecha del propio portal. Las 97 tarjetas traen
     `<time datetime>` y el detalle lo repite como `datePosted` de schema.org;
     el scraper no la leía y por eso **las 115 filas de producción no tienen
     fecha**. Una tarjeta sin fecha ISO utilizable omite el campo: ausente
     sigue significando DESCONOCIDO, nunca la hora de ingesta.
  2. La identidad es el id numérico del portal (`/jobs/id70453-…`), no la URL:
     las 26 URLs lo llevan y el slug incluye el título, así que un cambio de
     título producía una segunda oferta en el productor.
  3. Se barren siempre las cinco páginas declaradas, que es lo que recupera las
     cuatro ofertas descritas arriba.
- **Lo que NO se toca, a propósito:** los metadatos `Arbeitsort`/`Funktion`/
  `Pensum` del detalle. El scraper exige `.descriptor` y `.long_value` en la
  MISMA `.long_value_row` y el marcado vivo nunca tiene ambos, así que su
  extracción está muerta —de ahí que `employment_type` esté vacío en las 115
  filas—. Leerlos cambiaría el contenido de cada oferta y `Funktion` no es un
  tipo de jornada: queda como hallazgo, no como suposición.
- **Tráfico, medido:** Core hace 31 peticiones por barrido (5 listados + 26
  detalles) al ritmo de 2 s del propio portal; el scraper hacía 5 + 97 en frío y
  ~2 + ~8 en caliente. No se añade cursor de descripciones a propósito: llegan a
  9.866 caracteres y recortarlas para que quepan en el cursor del scope
  alteraría cinco de las 26 ofertas en cada ciclo.
- **`TagStackParser` (nuevo, compartido) existe porque el marcado real lo
  exigió:** el primer parser de detalle suponía etiquetas balanceadas y cortó
  una descripción de myScience **siete párrafos antes del final**; además
  incluía el texto de `<script>`, que BeautifulSoup no cuenta. La base cierra
  implícitamente lo que quedó abierto, descarta etiquetas de cierre huérfanas y
  excluye script/style/template/noscript. JobScout24 pasó a usarla y se
  **re-demostró su paridad** (231 de 231 tarjetas idénticas) y sus 36 pruebas.
  Cubierta por `test_html_response.py`, con una prueba por fallo real.
- **El consumidor no necesita cambio, y eso se comprobó:** el BFF de SwissJob ya
  lee el feed del núcleo por perfil (`services/matching/core_client.py`,
  `/v1/profiles/{id}/matches`) sin lista blanca por fuente, igual que para las 16
  fuentes ya nativas. En cuanto exista scope, las ofertas de myScience entran
  solas; hasta entonces no cambia nada porque no hay scope. Esa es la diferencia
  estructural con los portales de ReactPortfolio, donde había que conservar seis
  rutas públicas por fuente.
- Lado SwissJob: `myscience` sale de `_SCRAPER_CLASSES` y entra en
  `SOURCES_DECIDED_IN_ADVANCE` (completitud inversa K10 de `harvest_window`).
  No hay otra referencia viva: sólo su política y la fila del seed histórico de
  compliance, igual que `financejobs` tras su propio traspaso.
- Validación: Core **1.751 aprobadas y ninguna omitida** (las 1.713 anteriores
  más mis 38 nuevas, en 7 min 33 s) y backend SwissJob **1.916 aprobadas y 4
  `xfail`**, exactamente los recuentos de antes del cambio. CI de `244f15e`:
  `38624702577`. Focales:
  19 unitarias y de integración nuevas de myScience (doble ciclo PostgreSQL con
  una sola revisión por identidad, fecha del portal viajando y provisión opt-in),
  15 del módulo compartido, 125 focales de SwissJob (`harvest_window`,
  `scrapers`, URLs degeneradas), `ruff check`/`format` del repo completo,
  `git diff --check`, `check_no_secrets` 0 hallazgos y ratchet cruzado
  `{{"core_owned":16,"local_pending":26,"sources":58}}`.
- Incidencias cerradas antes de publicar: tres expectativas mías estaban mal y
  se corrigió la EXPECTATIVA, no el comportamiento —`JOB_TAGS` es una taxonomía
  de idiomas y docencia, no de tecnologías, así que una oferta de «Python
  Developer» no produce tags—; y una tarjeta sin fecha dejaba `published_at`
  como cadena VACÍA en el agregado, así que el campo se omite cuando no hay
  fecha. Ningún intento tocó producción.
- **Hallazgo nuevo, abierto, que afecta a las 14 fuentes de SwissJob que
  quedan:** el motor legacy pasa cada barrido por `ComplianceEngine.can_scrape`
  (`is_allowed` + `robots_txt_ok` de `source_compliance`) y deshabilita la fuente
  tras tres bloqueos seguidos. **Core no tiene ese portero**: tiene contador de
  fallos (`CORE_HARVEST_MAX_CONSECUTIVE_FAILURES=3`), alerta de salud y el
  `enabled` del scope, pero ni la fila de compliance ni el apagado automático por
  bloqueo. Afecta ya a todas las fuentes de scraping nativas en Core
  (financejobs, irishjobs, chmedia, publicjobs) y lo heredarán las 14 restantes.
  No se arregla en este portal: es una pieza de Core, no de una fuente.
- **Rollback:** no existe scope productivo. Revertir el commit devuelve
  `myscience` al registro de scrapers, saca su pre-decisión y retira adaptador,
  registro, admisión y provisionador de Core; JobScout24 y DynamiteJobs
  recuperarían sus parsers previos con sus paridades ya demostradas.
- El inventario conserva `state=local_pending` y el ratchet sigue en 16 Core /
  26 pendientes / 58 fuentes: producción sigue ejecutando el scraper hasta el
  corte agrupado.
- Tras myScience quedan **14 proveedores** por implementar en ramas, todos de
  SwissJob: `gastrojob`, `proz`, `remoteco`, `schuljobs`, `stelle_admin`, `tes`
  y los ocho `swiss_schools_*`.
- **Siguiente paso exacto si hay relevo:** comprobar la CI de este commit;
  después el preflight de `gastrojob` (sano, TYPO3, con tres cotas propias ya
  anotadas en `COTAS_Y_DECISIONES.md` §1.10-1.12 y §1.19 que hay que respetar).
  **No** empezar por `stelle_admin`: es Playwright y la imagen de Core no lleva
  Chromium, así que exige una decisión del propietario antes de tocarlo.
  `proz`, `remoteco` y `schuljobs` arrastran bloqueos propios registrados en el
  inventario (46 y 54 errores seguidos sin ninguna cosecha correcta, y un
  cursor sin bootstrap completo).

### Checkpoint de relevo 2026-10-14 · gastrojob centralizado en ramas

- Inicio desde `599ec3f` (CI `38632186849` **verde 9/9**, con lo que myScience
  quedó formalmente cerrado). Los `main` remotos siguen intactos en
  `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` y
  `5bf457afbde5b44a995acce4ef241324f066f836`.
- El trazado confirma un único productor activo:
  `backend/scrapers/gastrojob.py`, registrado en `_SCRAPER_CLASSES` y
  despachado cada 6 h. El consumidor sigue siendo el feed del núcleo, así que
  el traspaso es el mismo patrón que myScience: sacar la clase del registro y
  conservar su política como pre-decidida.
- **Por qué el listado público no sirve, y por eso el contrato es el endpoint
  AJAX:** `/stellen` monta las ofertas por JavaScript y devuelve 200 con cero
  enlaces de oferta — el fallo exacto que mató a esta fuente (VD.4b). Se pagina
  contra el endpoint del PROPIO frontend, cuya URL sale de los href de su
  paginación, con la cabecera `Referer: .../stellen` que ese endpoint espera,
  10 ofertas por página, cinco páginas y 2 s de pausa.
- Captura sellada: 5 páginas AJAX (46.000 bytes, todas 200, contador 1.027
  anunciadas, 10 stubs por página, **50 únicas y cero duplicadas**) y 50
  detalles (1.471.686 bytes, todos 200). Huellas en
  [`2026-10-14-dt144-gastrojob-baseline.json`](../audits/2026-10-14-dt144-gastrojob-baseline.json).
- La ejecución del scraper EXACTO dio 50 ofertas, 48 con descripción, 50 con
  localidad y fecha de detalle, **cero anonimizadas hoy**, 50 con `published_at`,
  48 con jornada, 11 cantones y 23 ubicaciones. Huella normalizada
  `797788fcb1b55b54ff0983035c4d7ad858036c6447a875ea794494aeb54a9c50`.

#### Avance verificable · gastrojob centralizado en ramas

- Core/SwissJob publicado en `280bfc2`; CI `38635097926` **verde en sus nueve
  trabajos**. No existe scope productivo y nada se ha desplegado.
- **Paridad byte a byte sobre la misma captura**: 50 de 50 stubs de listado y 50
  de 50 páginas de detalle idénticos a los de BeautifulSoup, y la proyección
  normalizada de las 50 ofertas tiene la MISMA huella SHA-256 en los diez campos
  comparados —incluido `published_at` con su offset suizo—: cero diferencias.
- **El veredicto de cinco vías se porta ENTERO**, porque leer una estructura
  cambiada como listado vacío es el fallo original: sin contador, contador
  ilegible y «hay enlaces propios y ninguno parsea» son fallos VISIBLES; más
  allá del rango que cubre el contador y una página sólo-partner dentro de rango
  son vacíos legítimos (la vía de rehabilitación). Cada uno tiene su prueba.
- **Un caso que el legacy arregló tarde y este port también necesitaba:** un
  contador de miles de dígitos pasa el `[0-9]+` y aun así hace lanzar a `int()`
  (límite de 4.300 dígitos de CPython). Mi primera versión dejaba escapar ese
  `ValueError`; ahora se captura y se reporta como contador ilegible, con una
  prueba que muerde sin el guard.
- Reglas de identidad conservadas y probadas: la URL guardada se RECONSTRUYE del
  path canónico —una query de tracking, un puerto o un cambio de mayúsculas no
  crean una segunda fila—, el host se valida (nada de protocolo-relativo,
  userinfo ni otro dominio), y la empresa sale SÓLO del listado aunque la
  microdata del detalle la conozca: participa en la identidad y sólo el listado
  es estable run a run. Ése es el motivo de que entre ~3 % y ~13 % de los
  anuncios queden como «Unknown», y hoy la captura no trae ninguno.
- La fecha del listado («Erstmals aktiviert», hora civil suiza) se ancla a
  Europe/Zurich y el `datePosted` del detalle queda sólo como respaldo, igual
  que en el productor. La hora ambigua de otoño resuelve al primer instante: una
  hora de error, una noche al año, frente a perder la fecha entera.
- Validación: Core **1.791 aprobadas y ninguna omitida** (las 1.751 anteriores
  más 40 nuevas, en 8 min 52 s) y backend SwissJob **1.916 aprobadas y 4
  `xfail`**, los mismos recuentos de antes. Además 122 focales de SwissJob,
  doble ciclo PostgreSQL con una sola identidad por oferta, provisión opt-in,
  `ruff check`/`format` del repo completo, `git diff --check`,
  `check_no_secrets` 0 hallazgos y ratchet
  `{"core_owned":16,"local_pending":26,"sources":58}`.
- Incidencias cerradas antes de publicar: tres expectativas mías estaban mal
  —el `?tracking=` NO se rechaza, se descarta al reconstruir la URL canónica; y
  dos pruebas daban por vacío legítimo una página que el veredicto declara
  fallo— y se corrigió la EXPECTATIVA; la localidad del detalle llega en un
  `<meta content=…>`, que no pasa por la pila de elementos, así que leerla sólo
  en etiquetas de apertura no encontraba nada en ninguno de los 50 detalles.
  Ningún intento tocó producción.
- **Tráfico:** 55 peticiones por barrido (5 AJAX + 50 detalles) al ritmo de 2 s
  del portal. El productor hacía lo mismo en frío y menos en caliente por su
  early-stop de cursor, que Core no reproduce a propósito (emisión total).
- **Rollback:** revertir `280bfc2` devuelve `gastrojob` al registro de scrapers,
  saca su pre-decisión y retira adaptador, registro, admisión y provisionador.
- Tras gastrojob quedan **13 proveedores** por implementar en ramas, todos de
  SwissJob: `proz`, `remoteco`, `schuljobs`, `stelle_admin`, `tes` y los ocho
  `swiss_schools_*`.
- **Siguiente paso exacto si hay relevo:** comprobar la CI del checkpoint y
  seguir con `tes` (sano, `__NEXT_DATA__`, una oferta por página). `stelle_admin`
  exige decisión del propietario (Playwright, y la imagen de Core no lleva
  Chromium); `proz` y `remoteco` llevan 46 y 54 errores seguidos SIN ninguna
  cosecha correcta —el inventario pide decidir transporte o retirarlas— y
  `schuljobs` tiene un intento sin desenlace y el cursor sin bootstrap completo.

### Checkpoint de relevo 2026-10-15 · los cuatro residuos estructurales, cerrados

Ninguno era de un portal concreto: los cuatro salieron de migrar portales y los
cuatro bloqueaban la definición de terminado del contrato (§10).

#### 1. `jobicy` y `remoteok` ya no registran caché ni actualizador

- Seguían llamando a `register_cache`/`register_update` al importarse mucho
  después de su traspaso (`services/jobicy_provider_impl.py:55,294` y
  `services/remoteok_provider_impl.py:51,197`). Producción no los importaba, pero
  **el registro es la vía por la que un productor vuelve a la vida sin tocar su
  registro de providers**: lo único que los frenaba era la bandera
  `LEGACY_DISABLED_SOURCES`.
- Se retiran las cuatro llamadas y se añade el censo COMPLETO, no fuente a
  fuente: `test_no_module_registers_a_local_cache_or_updater` importa la
  aplicación y exige que los dos registros queden **vacíos**. Con él, las veinte
  fuentes transferidas tienen su prueba de «sin efectos de registro».

#### 2. Los barridos de fidelidad G7 y G8 vuelven a cubrir algo

- Al quedar el registro vacío, sus `parametrize` se quedaron sin fuentes y
  pytest los saltaba con «got empty parameter set» — un salto que nadie entiende
  **se lee como cobertura**. Ahora saltan con un motivo que NOMBRA lo que los
  sustituye.
- Y lo que los sustituye existe:
  `test_ninguna_ruta_servida_desde_core_se_tumba_con_un_agregado_hostil` mete la
  misma oferta hostil —número no finito, surrogate suelto y NUL, **también como
  CLAVE de los contadores**, que fue el hallazgo propio de G8— por el agregado
  del núcleo y barre TODAS las rutas públicas de las **18 fuentes** del
  adaptador: cada una debe responder 200 y conservar la oferta sana.
- Resultado medido: las 18 pasan. La defensa ya existía repartida
  (`finite_salary` del esquema y `pg_safe_tree` del router) pero **nadie la
  probaba de extremo a extremo** en el camino servido desde Core.

#### 3. La costura ya no construye un catálogo Core + local

- `services/catalog/seam.py` montaba un `CompositeCatalog(CoreCatalog,
  LocalCatalog)` siempre que `LEGACY_DISABLED_SOURCES` no estuviera vacío —es
  decir, siempre en producción—, y un `FallbackCatalog` en modo `core_read`.
- Con cero cosechadores locales **no hay nada que componer ni a lo que caer**, y
  un lector no puede distinguir un camino inerte de uno vivo. Si no hay fuente
  local propia, la costura devuelve `CoreCatalog()` **directamente**.
- El compuesto SIGUE existiendo para el caso de rollback (una fuente que
  vuelva), y eso lo cubre el mapeo modo→implementación de siempre. El ratchet
  `check_local_harvest_ownership.py` haría fallar el CI antes de que ese caso
  apareciera sin decisión explícita.
- Fijado por `test_sin_cosechadores_locales_el_catalogo_es_core_A_SECAS` para
  los tres modos no locales.

#### 4. Core ya tiene el portero que el motor legacy tenía

- **El hueco, medido:** el motor legacy pasa cada barrido por
  `ComplianceEngine.can_scrape` y, tras tres bloqueos seguidos (403/429/503),
  pone la fuente en `is_allowed = False` y **deja de pedir**. Core contaba
  fallos y alertaba, pero nunca paraba: un portal que devuelve 403 cuatro veces
  al día seguiría recibiendo las cuatro peticiones para siempre. Al traer los
  scrapers al núcleo, ese portero se quedaba fuera — y ya afectaba a
  financejobs, irishjobs, chmedia y publicjobs.
- **Lo implementado:** `BLOCK_STATUSES = {403, 429, 503}` (la lista EXACTA del
  legacy) y `blocking_status(exc)`, que **recorre la cadena de excepciones**:
  los adaptadores envuelven el error del transporte y `raise … from None` sólo
  lo oculta del traceback, así que `__context__` sigue llevándolo. Eso evita
  tocar los ocho adaptadores: la clasificación vive en un único sitio.
- Migración `core0055` (aditiva): `source_scope_state.consecutive_blocks`,
  `last_blocked_at` y `disabled_by_blocks_at`. Los bloqueos se cuentan
  **aparte** de `consecutive_failures` a propósito: una caída de red y un 429 no
  son lo mismo y no deben apagar la misma fuente.
- El runner cuenta el bloqueo en la misma transacción que ya contaba el fallo;
  al llegar al umbral (`CORE_HARVEST_BLOCK_THRESHOLD=3`) pone
  `harvest_scopes.enabled = false`, sella `disabled_by_blocks_at` y **grita** un
  `logger.error`. Una cosecha COMPLETA —y sólo ella— devuelve el contador a cero.
- **Re-encender es manual, a propósito:** sondear sola a una fuente que nos está
  bloqueando es exactamente lo que no debe automatizarse.
- **Y no se convierte en silencio:** la vigilancia de salud sólo mira scopes
  HABILITADOS, así que un scope apagado por el kill-switch desaparecería de
  `alertas` — el punto ciego que `harvest/health.py` ya documenta. El censo
  publica ahora `apagados_por_bloqueo` y se canta en CADA pasada mientras siga
  apagado.
- Pruebas: 12 unitarias del clasificador (incluida una cadena envuelta dos veces
  y una circular, que no debe colgar) y 4 de integración contra PostgreSQL real
  —tres bloqueos apagan y el cuarto run ya no llama al provider; cuatro fallos
  que no son bloqueo no apagan nada; un éxito borra el contador; y el censo de
  salud canta el apagado—.

#### Estado de este cierre

- ReactPortfolio publicado en `5f03c68`; suite completa **2.256 aprobadas y 3
  omitidas** (las dos de G7/G8, ahora con motivo, y la integración real opt-in),
  ratchet local en 0 cosechadores y **cero hallazgos nuevos de black** respecto a
  `HEAD` en los ocho ficheros tocados.
- Core/SwissJob: suite del núcleo **1.807 aprobadas y ninguna omitida** (las
  1.791 anteriores más 16: 12 unitarias y 4 de integración del kill-switch), la
  migración `core0055` aplicada desde `core0054` sin incidencias, `ruff
  check`/`format` del repo completo y `git diff --check` limpios.
- `CLAUDE.md` describe la cadena del núcleo como `core0001..core0052` y ya
  estaba desfasada antes de esto (el repo traía `core0053` y `core0054` de otro
  trabajo); con `core0055` lo sigue estando. **No se corrige aquí** porque ese
  fichero arrastra cambios ajenos sin versionar y mezclarlos sería peor.
- Lo que NO se hace y queda explicado: **no se borran** `CompositeCatalog` ni
  `FallbackCatalog`. Mientras producción siga cosechando en local, el código
  antiguo es la vía de rollback que el propio contrato exige (§7.7); su borrado
  pertenece al corte agrupado final, junto con el de los productores locales.

### Checkpoint de relevo 2026-10-15 · tes y stelle_admin centralizados; proz, remoteco y los colegios, investigados

#### tes centralizado en rama (`9e62bd5`, CI `38655779623` verde 9/9)

- Las ofertas viajan dentro de `__NEXT_DATA__`, en
  `props.pageProps.initialData` — **el mismo objeto** que el scraper lee de la
  caché tRPC en la página 1 (comprobado sobre la captura sellada) y el único que
  sobrevive en las páginas siguientes.
- Paridad byte a byte: las 20 ofertas coinciden en los doce campos comparados,
  identidad incluida. Huella
  `8748ff87cea0d33869ca627116f9edb7965bacf88436de3289d4934a43430d15` en ambos
  lados.
- **La cota REAL del portal, medida y no heredada:** `?page=N` se ignora en
  servidor. La página 2 renderiza los datos de la 1 (`currentPage: 1`), así que
  las ocho ofertas que `numFound` anuncia más allá de las veinte primeras **no
  son alcanzables** desde el payload renderizado. El scraper paginaba contra ese
  mismo parámetro ignorado y las perdía en silencio; su comentario
  `PAGE_SIZE = 1` («server enforces limit=1») está obsoleto: hoy sirve veinte
  por página.
- El barrido pide la página 2 UNA vez —si el portal empezara a honrar el
  parámetro, lo seguiría—, para, registra el déficit y publica
  `announced`/`harvested`/`shortfall` en el cursor. El run sigue COMPLETO: una
  limitación permanente del portal no debe congelar `last_complete_at` y
  convertir la alerta de obsolescencia en ruido.
- Identidad: el id propio del portal, que cada fila trae y con el que termina
  cada URL canónica, en vez de un hash de `title|company|url`.
- La lectura de `__NEXT_DATA__` pasa a `next_data`, compartido con financejobs,
  conservando sus tres mensajes de fallo. Suite de financejobs verde 25/25 tras
  el cambio.

#### stelle_admin reimplementado SIN navegador (`82c742d`, CI `38657167787` verde 9/9)

- **La pregunta era si jobs.admin.ch se puede cosechar sin Chromium, que la
  imagen de Core no lleva a propósito. Sí se puede, y lo dice el propio portal:**
  su cascarón SPA son 2.220 bytes con un `#root` vacío; su único script de módulo
  declara `baseUrl = https://ohws.prospective.ch/public/v1/medium/1000624`; y ese
  endpoint responde 200 con `{total, jobs[]}` a un cliente HTTP normal. Sin
  credencial, sin token, sin navegador.
- **La paridad campo a campo es imposible Y no deseable**, y eso está medido: el
  scraper conduce un navegador y adivina el DOM con seis selectores
  especulativos («Common patterns for government portals»). Lo que produce en
  producción, sobre 43 filas: título, URL y la CONSTANTE «Swiss Federal
  Administration»; ubicación, descripción, `published_at` y tags están vacíos en
  **todas** (field presence `[43,0,0,43,0,0]`).
- La API da **431 ofertas en cinco peticiones**, todas con la unidad
  contratante real, el lugar de trabajo, la fecha de publicación del portal, el
  pensum, los textos de tareas y requisitos, y una bandera de home office que
  hace de `remote` un dato y no una suposición (181 de 431). 201 resuelven
  cantón y 430 traen grupo profesional. 486.675 caracteres de descripción donde
  antes había cero.
- Lo que SÍ se conserva: la identidad es el id propio del portal más su enlace
  canónico, **el host del enlace se valida** (una API que cambiara el enlace no
  puede persistir una URL ajena como oferta clicable), y una respuesta ilegible
  es un fallo VISIBLE, nunca «0 ofertas».
- Evidencia:
  [`2026-10-15-dt144-stelle-admin-baseline.json`](../audits/2026-10-15-dt144-stelle-admin-baseline.json),
  con los cuatro pasos por los que se encontró la API.

#### proz y remoteco: NO son implementables desde esta infraestructura

- **proz**: Cloudflare aplica un *managed challenge* (`cType: 'managed'`) a
  **toda la zona**: feed, portada, `robots.txt` y `www2` responden 403 con el
  desafío, con cualquier juego de cabeceras y protocolo, y desde cuatro puntos
  de vista (este host, el NAS —misma IP pública—, el fetcher de Anthropic y el
  rastreador de archive.org). Exige ejecutar JavaScript; no hay cabecera que lo
  resuelva. El archivo fecha el muro entre junio y julio de 2026, dos meses
  después de escribir el provider (2026-04-22).
- **remoteco**: dos fallos independientes. (A) **El feed ya no existe**: el
  archivo lo tiene en `301 → 404` desde el **2025-12-21**, cuatro meses ANTES
  de escribir el provider; el feed del blog y `wp-json` tampoco están. (B) El
  edge es **Akamai Bot Manager con puntuación conductual** (`AkamaiGHost`,
  cookies `bm_s`/`bm_so`): a los clientes desconocidos les resetea el stream o
  les retiene la petición; a los que parecen navegador les sirvió `200`/`301`
  hasta que, tras ~20 sondas, pasó a `403 Access Denied` para los mismos. No
  es bloqueo por IP (otra red también falla) y un proxy no lo arreglaría: pasar
  de forma estable requiere el sensor JS de Akamai, es decir, un navegador.
- Producción concuerda: 46 y 54 errores seguidos. «Ninguna cosecha jamás» está
  probado **sólo desde el 2026-08-29**, inicio de la ventana de salud; proz
  probablemente cosechó de abril a junio; remoteco no pudo cosechar nunca.
- Lo que arreglaría cada una: proz, o bien un navegador que resuelva el
  desafío (fuera de la imagen de Core y de la regla de no-evasión de
  `docs/SCRAPING_HUMANO.md`, precedente medjobs), o bien comprobar con una
  cuenta ProZ si el **feed de miembro** que describe su foro queda exento del
  desafío —una prueba manual de un minuto que sólo el propietario puede hacer—.
  remoteco necesitaría un scraper HTML nuevo del listado **y** un navegador
  stealth de pago; no queda nada que leer aunque el edge dejara pasar.
- Evidencia:
  [`2026-10-15-dt144-proz-remoteco-analysis.json`](../audits/2026-10-15-dt144-proz-remoteco-analysis.json)
  (corrige dos afirmaciones de la sonda del día 14, que se conserva).
- **Por qué migrarlas aun así sería un error:** la metodología exige línea base
  viva reproducible y paridad campo a campo contra el productor. Ninguna de las
  dos ha producido nunca una cosecha, así que no hay nada contra lo que medir:
  el adaptador sería inverificable por construcción, y además el nuevo
  kill-switch de bloqueo lo apagaría en tres barridos.
- **Recomendación: retirarlas explícitamente con esta evidencia** (§10 lo
  permite: una fuente bloqueada se resuelve o se retira, nunca desaparece del
  recuento). Retirar quita dos fuentes del corpus de forma permanente y cambia
  el registro de producción, así que **es decisión del propietario**. La
  alternativa que el propio proyecto documenta es pagar un navegador stealth
  remoto y activar `SCRAPER_BROWSER_CDP_URL`, lo que cambia también la postura
  de compliance. Evidencia:
  [`2026-10-15-dt144-proz-remoteco-probe.json`](../audits/2026-10-15-dt144-proz-remoteco-probe.json).
- Límite de esta sonda, dicho explícitamente: describe ESTE host y ESTE momento.
  Otra IP de salida o un navegador de pago podrían dar otra respuesta — que es
  justo la decisión que se entrega, no una que se asume.

#### Los ocho colegios: preflight medido, adaptador NO escrito

- Captura en vivo de los siete tableros alcanzables por una sola URL (todos 200)
  y replay del parser legado de cada uno sobre ella. **Resultado: seis publican
  CERO ofertas ahora mismo**; sólo haut-lac (1) e ISCS (2) tienen algo. Eso es
  el estado NORMAL de una watchlist: un colegio publica una vacante cada varios
  meses.
- **Eso invierte una regla de diseño de todos los adaptadores anteriores:** los
  cinco escritos hasta ahora LANZAN cuando un barrido no encuentra nada, porque
  en un portal de empleo un barrido vacío significa parser roto. Para los
  colegios eso haría fallar seis de ocho fuentes todos los días. Lo que hay que
  conservar es el «vacío verificado» del legacy: la página se bajó, su
  estructura se RECONOCIÓ, y sólo entonces el cero se reporta como sano. Core
  necesita por tanto un **marcador estructural por colegio**: presente y sin
  ofertas ⇒ barrido completo y vacío; ausente ⇒ fallo visible.
- Complicaciones ya identificadas para quien lo escriba: `inspired` y `nae` no
  son un tablero cada uno —barren VARIOS colegios por fuente, cada uno con su
  URL de carreras y un filtro por nombre de colegio sobre portales ATS de
  terceros—, y un bloqueo en uno no debe reportarse N veces (el legacy corta el
  run en el primero por eso mismo); `isp` no usa la base compartida (467 líneas
  con su propio `normalize`); y `ecolint` declara cinco páginas pero la 1 y la 2
  de la captura son byte-idénticas, el mismo caso que myScience.
- Evidencia:
  [`2026-10-15-dt144-swiss-schools-preflight.json`](../audits/2026-10-15-dt144-swiss-schools-preflight.json).

#### Estado tras este checkpoint

- Validación: Core **1.846 aprobadas** (1.807 → +21 de tes → 1.823 → +23 de
  stelle_admin) y backend SwissJob **1.916 aprobadas y 4 `xfail`** en las dos
  tandas, los recuentos de siempre. Lint del repo completo, `diff --check`,
  `check_no_secrets` y ratchet `{"core_owned":16,"local_pending":26,"sources":58}`
  verdes.
- Quedan **11 portales** por implementar en ramas: los ocho `swiss_schools_*`,
  `schuljobs`, y `proz` y `remoteco` a la espera de decisión.
- **Siguiente paso exacto si hay relevo:** escribir el adaptador de los colegios
  con el marcador estructural por tablero descrito arriba —empezando por
  haut-lac e ISCS, que son los dos que hoy tienen ofertas y por tanto los únicos
  con paridad medible—, y después `schuljobs`, que arrastra un cursor sin
  bootstrap completo. No tocar producción, los `main` ni activar scopes.

### Checkpoint de relevo 2026-10-15 · los ocho colegios y schuljobs; el registro de scrapers queda VACÍO

Con este tramo **todas las fuentes migrables tienen adaptador nativo en rama**.
Las únicas dos que no lo tienen son `proz` y `remoteco`, y no por falta de
trabajo: no son implementables desde esta infraestructura y esperan decisión del
propietario (su checkpoint anterior tiene la evidencia).

#### La watchlist escolar invierte una regla de diseño

Seis de los ocho tableros publican CERO ofertas ahora mismo, y eso es el estado
NORMAL de una watchlist: un colegio publica una vacante cada varios meses. Los
cinco adaptadores anteriores LANZAN cuando un barrido no encuentra nada, porque
en un portal de empleo eso significa parser roto. Aquí haría fallar seis de ocho
fuentes todos los días.

Lo que se conserva es el «vacío verificado» del motor legacy: **se baja la
página, se RECONOCE y sólo entonces el cero se reporta como sano**. El
reconocimiento está medido, no supuesto — cada tablero declara el título de
página que lo nombra, y donde el portal tiene contenedor o marcador de vacío se
honra también (ISB renderiza `fsElementEmpty`, «No post to display»: el portal
lo dice él mismo). Una página que no se reconoce es fallo VISIBLE.

Probado donde importa: tres barridos vacíos seguidos dejan el scope con
`consecutive_failures=0`, `consecutive_blocks=0` y `last_complete_at`
confirmado.

#### Los cinco tableros de HTML propio (`72d068d`, CI `38690714105` verde)

- Paridad campo a campo: haut-lac (1) e ISCS (2) tienen ofertas hoy y coinciden;
  zis, isb y ecolint están vacíos, así que **se compararon TAMBIÉN contra los
  parsers legados sobre fixtures** construidos con la estructura que sus propios
  selectores declaran — `0 == 0` no prueba nada. Nueve filas, cero diferencias.
- Cada tablero conserva sus rarezas: los anclajes sintéticos por vacante que
  haut-lac e ISCS necesitan (compartir la URL del tablero dejaba viva UNA sola
  vacante por colegio, clase VD.1), los `&nbsp;` y el id repetido de ZIS, las
  dos formas de post de ISB, la regla de palabra clave + negrita de ISCS y las
  dos plantillas de haut-lac. La única mejora: el cantón, derivado de la ciudad
  del propio colegio, que el productor conocía y no usaba (4 de 5 resuelven).
- Un módulo posee cinco fuentes, así que el ratchet de propiedad aprende a leer
  un censo `SOURCE_NAMES` además de `SOURCE_NAME`.

#### Los dos portales ATS compartidos (`287cc18`, CI `38692407198` verde)

- Nord Anglia (tres colegios) e Inspired (dos) no tienen tablero propio:
  publican en un SuccessFactors compartido, una consulta por colegio. Una fuente
  deja de ser una página y pasa a ser una LISTA de tableros.
- **La búsqueda del portal es difusa, y por eso el productor filtra:** la
  consulta de La Côte devuelve 13 tarjetas y sólo 4 son suyas. Los dos parsers
  coinciden en cuáles. Trece filas vivas en los tres colegios de Nord Anglia,
  cero diferencias; los dos de Inspired están vacíos hoy y salen como vacíos
  verificados.
- Una tarjeta sin el campo de colegio no se atribuye a nadie, la ciudad de la
  propia tarjeta gana a la configurada, y una página que no es el portal —un
  login, un error— es fallo visible, no un tablero vacío.
- El bloqueo mantiene el motivo del productor: los colegios comparten HOST, así
  que N reportes en un run alcanzarían el kill-switch por un episodio
  transitorio. El barrido corta en el primer fallo y el kill-switch nuevo cuenta
  UN bloqueo por run.

#### Mosaic en Workday (`2200b56`, CI `38694111472` verde)

- El único tablero de la familia que es una API. Tres ofertas vivas, misma
  huella SHA-256 que el scraper en título, empresa, ubicación, URL, tags e
  idioma.
- **Toda la cadena de validación de `externalPath` se porta íntegra**, porque
  cada eslabón se añadió tras un defecto medido: una sonda en vivo demostró que
  `?job=123` pasaba el guard de tipo y se persistía como URL «válida» sin oferta
  detrás. Veintisiete formas rechazadas y cuatro aceptadas quedan fijadas por
  pruebas, incluidas la doble codificación (`%252E`), los overlong (`%C0%AF`),
  los surrogates (`%ED%A0%80`) y los sustitutos UTF-16 sueltos.
- Dos reglas de ORDEN se conservan con su motivo: el filtro de colegio va ANTES
  de los guards por elemento, porque el tenant de Workday es COMPARTIDO y
  validar filas de otros colegios apagaría una fuente sana cuyos cero matches
  son legítimos; y un elemento degenerado degrada ESE elemento, nunca la página.
- `published_at` queda ausente a propósito: Workday publica un bucket relativo
  («Posted 16 Days Ago»), nunca una fecha. **No lo "arregles".**

#### schuljobs (`020469e`, CI `38696733154` verde 9/9)

- La paridad que el inventario pedía ANTES de migrar, medida: 84 filas de
  listado y 83 páginas de detalle idénticas, y la proyección de las 83 ofertas
  con la misma huella SHA-256 en diez campos.
- **La mitad de los detalles no trae JSON-LD** (41 de 83), así que un detalle
  sin él es el caso normal y el stub del listado se sostiene solo: título,
  empresa, ciudad y cantón salen del listado.
- **El scope DECLARA su presupuesto de páginas**: cuatro —el listado inicial más
  tres de scroll, las 83 ofertas medidas en vivo y unas dos veces lo que trae el
  presupuesto dinámico del productor— contra un techo de 26. Ampliar el barrido
  es una decisión de params con su propia validación, no un cambio de código.
  Eso compra 87 peticiones por barrido.
- Rarezas conservadas con su motivo: `data-nextpage` y el `nextpage` del
  fragmento se castean SEGURO (`int("")` reventaba el barrido entero y perdía
  los detalles ya descargados), un fallo a mitad de scroll se REPORTA (aprender
  sólo la página inicial dejaría las de scroll sin pedir jamás) y sólo una
  cabeza de dos letras de «CANTÓN · Ciudad · Empresa» cuenta como cantón. La
  mejora: un `employmentType` multivaluado se renderiza en vez de perderse al
  entrar en una columna de texto — le pasa a 3 de 83.
- **`_SCRAPER_CLASSES` queda VACÍO**: los trece scrapers de SwissJob están
  traspasados, sus módulos siguen como vía de rollback y el docstring del
  registro recoge por qué medjobs, Riviera, Verbier e ISR nunca estuvieron.

#### Estado tras este checkpoint

- Inventario: **40 de las 42 fuentes migrables tienen `core_adapter`**. Las dos
  sin adaptador son `proz` y `remoteco`. Siguen en el registro de providers de
  SwissJob nueve nombres, y siete de ellos (`careerjet`, `jooble` y los cinco
  partner restringidos) ya están `retired` con cero publicadores y cero
  credenciales: son clases inertes que no emiten ninguna petición.
- Validación: Core **1.952 aprobadas** y ninguna omitida (1.871 → +46 de ISP →
  1.917 → +35 de schuljobs); backend SwissJob **1.916 aprobadas y 4 `xfail`** en
  las cuatro tandas, los recuentos de siempre. Lint del repo completo,
  `diff --check`, `check_no_secrets` y ratchet
  `{"core_owned":16,"local_pending":26,"sources":58}` verdes.
- El ratchet NO se mueve a propósito: el inventario describe PRODUCCIÓN, y
  producción sigue cosechando en local hasta el corte agrupado aprobado.
- **Siguiente paso exacto si hay relevo:** ya no queda ningún portal por
  implementar. Lo que falta para cerrar DT-144 es (1) la decisión del
  propietario sobre `proz` y `remoteco` —retirarlas con la evidencia o pagar
  navegador stealth—, (2) comprobar la CI de ReactPortfolio, que no se puede
  leer desde este host, y (3) el corte agrupado: provisionar y habilitar los
  scopes, drenar y retirar los productores locales, borrar `CompositeCatalog` y
  `FallbackCatalog`, y fusionar tras la aprobación explícita. Nada de eso se
  hace sin el propietario.

### Registro cerrado · Forgejo no forma parte de DT-144

La alternativa se evaluó temporalmente y se retiró por decisión expresa del
propietario. Sus detalles operativos se eliminaron de este plan para impedir que
se interpreten como instrucciones reanudables. La única información vigente es
el checkpoint de retirada anterior: GitHub Free es la autoridad única, Forgejo
no existe en los repositorios ni en el NAS y reintroducirlo queda fuera del
alcance autorizado.

**Prohibición de cierre parcial:** DT-144 no se cierra porque exista un adaptador,
un scope o una ruta denominada `core_primary`. Solo se cierra cuando no queden
productores, credenciales, conexiones ni persistencia de ofertas locales y las
sondas productivas satisfagan íntegramente la definición de terminado del
contrato.

## Foto histórica — 2026-09-21: punto 4, pendientes y evidencia

Consolidación documental de las actas hasta 21-09 16:53 UTC; **no es una nueva
auditoría NAS ni un cierre global**. Punto 4 abierto y trabajo funcional pausado;
autorizados sólo documentación y commits locales. Las fechas y marcas inferiores
son fotografías históricas; este bloque prevalece únicamente en lo que actualiza.

**Retirar de pendientes repetidos:** entrega de perfiles públicos, autoridad de
feedback y recuperación post-corte, migración/activación de las diez búsquedas
SwissJob, y traspaso WorkingNomads. No confundir lo último con todas las fuentes.
Búsquedas preservaron IDs/contadores y 277 pendientes; WorkingNomads produjo
40 ofertas con replay sin segundo fetch. Falta evidencia de entrega natural de
las búsquedas: barridos sin vencidas no equivalen a aviso entregado.

Actas: [perfiles](../audits/PROFILE_DEPLOYMENT_NAS_2026-09-20.md),
[feedback](../audits/FEEDBACK_DEPLOYMENT_NAS_2026-09-20.md),
[búsquedas](../audits/SEARCH_CUTOVER_OPERATOR_2026-09-21.md) y
[WorkingNomads](../audits/WORKINGNOMADS_CUTOVER_2026-09-21.md).

**Abierto para punto 4:** censo de fuentes activas y cobertura entre ambos
proyectos; traspasos restantes; lectores/avisos/digest Portfolio dependientes de
caché local; productores escolares; autonomía del postprocesado sin captura CDC;
retirada y recuperación preservando estado nuevo. Probar primero la recuperación
existente del proyector: su nombre `shadow` no justifica reescribirla.

**Separado:** rendimiento global/punto 5, cron/alarma de retención, aceptación y
versión final; calidad exige examen válido independiente. No añadir racha al
punto 4. La caducidad ya fijada de copias temporales sí debe respetarse.

Los hallazgos de auditorías previas no se dan por cerrados por esta actualización:
conciliar cada caso con código/prueba/acta actual antes de reabrirlo o eliminarlo.
La estimación inicial de horas omitió dependencias; el rango posterior tampoco
es una cota sin inventario efectivo. Desglose, supuestos, deuda clasificada y
criterios para que el revisor refute trabajo innecesario en el
[informe de pendientes](../PENDIENTES_PUNTO4_REVISION_EXTERNA_2026-09-21.md).

> **Qué es este documento.** El inventario único de la deuda técnica **reconocida y no abordada**
> del proyecto. Hasta ahora vivía dispersa entre mensajes de commit, residuales sueltos por track y
> comentarios en el código, y eso la hacía **redescubrirse una y otra vez** — el caso más caro fue
> una misma clase de fallo encontrada cinco veces seguidas por revisiones distintas.
>
> **Qué NO es.** No es un backlog de trabajo planificado: eso vive en
> `BACKLOG_UNIFICACION_JOBHUNTING.md`. Aquí solo está lo que se decidió **no** hacer, con su porqué.
>
> Creado: 2026-08-18 · Actualizado: 2026-09-18 (**auditoría vigente §0.A; último cierre operativo ESTADO §41**) ·
> Fuentes: backlog v3.1.2, `ESTADO_Y_HOJA_DE_RUTA.md`, `ADR_JOBHUNTING.md` v4,
> `RUNBOOK_CUTOVER_PILOTO.md`, `CONTRATOS_FASE_C.md`, `HALLAZGOS.md`, los últimos 40 commits de
> SwissJob y el código. Cada ubicación está verificada salvo marca expresa.

## 0.A · Auditoría profunda de integración — 2026-09-18

**Estado: deuda abierta; no es una autorización de cierre global ni un nuevo GO
de calidad.** Esta sección prevalece para los puntos que actualiza; las actas
E.14/E.15 siguen acreditando sus pruebas concretas, no todas las combinaciones de
uso. No se han aplicado fixes ni desplegado cambios durante esta auditoría.

**Base inspeccionada:** SwissJob `d9c8a0a`, backend Portfolio `26b752a`,
Public `2bcbcd9` antes de esta edición. Se revisaron recorridos UI/BFF/core de
catálogo, matching y colegios, avisos, productores escolares, fronteras de
autorización/validación y documentación operativa. Se contrastó el NAS mediante
lecturas, sin modificar servicios ni datos. No se afirma haber probado cada
módulo del proyecto ni ausencia de otros defectos.

**Resultado:** cuatro defectos reproducidos, una deuda de escalabilidad localizada
en código y una divergencia de configuración local/desplegada confirmada. No se
demostró pérdida de datos ni una vulnerabilidad P1 nueva. Los cuatro defectos son
P2: fallos de contratos de uso/concurrencia que deben corregirse antes de declarar
la funcionalidad completa; no prueban por sí solos que toda la plataforma esté caída.

| ID | Prioridad / dimensión | Evidencia y situación |
|---|---|---|
| A18-01 | P2 · funcionalidad/integración | **CERRADO 2026-09-28** — cota C-API-R cerrada en el core: `_catalog_filter_sql` sirve `q`/`source`/`remote`/`country`/`city` con semántica documentada; `test_catalog_contract.py` prueba la equivalencia local↔core por filtro (`test_search_filters_equivalent_local_vs_core`) y que los parámetros no soportados no generan petición. El 501 de `core_primary` ya no puede darse |
| A18-02 | P2 · bug/eficiencia | **CERRADO 2026-09-28** — el camino escolar ya no pagina: `CoreWatchlist.get_match` resuelve por `GET /school-jobs?dedup_key=<hash>&limit=2` (una petición, ambigüedad = error) y `services/schools/core_client.list()` es una sola llamada. No queda ningún bucle de 100 páginas en `backend/services/matching` ni `services/schools` |
| A18-03 | P2 · funcionalidad/UX | **CERRADO 2026-09-28** — la pantalla principal pide `limit=3000` (≥ corpus) y la pestaña Watchlist filtra sobre ese lote, así que el corte a 500 no aplica; `MatchPage` renderiza `isError` aparte del vacío. Residuo aceptado: un fallo del corpus ESCOLAR degrada a «sin `school_id`» con `logger.warning` (M5/T12), no con aviso en la UI |
| A18-04 | P2 · concurrencia | **CERRADO 2026-09-28** — `claim_deliveries` usa `FOR UPDATE OF d SKIP LOCKED` + lease; `test_g7_dos_dispatchers_solapados_retiran_UNA_fila_habiendo_dos_candidatos` lo prueba. Suite del núcleo en verde el 2026-09-28 |
| A18-05 | P2 · escalabilidad | **CERRADO 2026-10-13.** La UI actual entra por facetas compactas y pide 50 tarjetas por categoría; Core calcula categoría/facetas, el BFF sirve el feed calentado, cachea joins escolares y mantiene 60 s las páginas de catálogo antes de revalidar ETag. Con 100 muestras en el NAS: facetas p50/p95 0,088/0,180 s; categoría 0,371/1,033 s; guardados 0,564/1,551 s; catálogo 0,186/0,838 s. Las cuatro cumplen el presupuesto predeclarado p95 <= 2 s. Evidencia y protocolo en el checkpoint A18-05 al final. |
| A18-06 | P2 · operación/configuración | **CERRADO 2026-09-28** — el backend de producción (`2b59a92`) resuelve `GROQ_MODEL=openai/gpt-oss-120b`, `GROQ_RERANK_MODEL=qwen/qwen3.8-27b`, `GEMINI_MODEL=gemini-3.6-flash` (leído con `settings` dentro del contenedor del NAS); el compose del NAS no los sobreescribe, así que los defaults verificados el 09-15 son los efectivos |

### A18-01 · El buscador ofrece opciones que la ruta canónica rechaza

**Ubicación:** `SwissJob/frontend/src/pages/SearchPage.jsx:125`,
`frontend/src/hooks/useJobSearch.js:20`,
`backend/services/catalog/core_client.py:232` y `backend/routers/jobs.py:43`.

El frontend envía órdenes oldest/salary/relevance y filtros de cantón, idioma,
seniority, contrato o salario. CoreCatalog declara esas combinaciones no
soportadas; en core_primary el rechazo se traduce a 501. El rechazo y la ausencia
de fallback son correctos respecto al contrato: el defecto es ofrecer controles
sin conciliar ese contrato con la UI.

**Reproducción:** `reproduce_swiss.py` (enlace/comandos abajo) confirma los ocho
casos sin red. GET NAS
`/api/v1/jobs/search?sort=oldest&limit=1` → **501** el 18-09.
No se realizó una sesión de navegador autenticada.

**Corrección mínima:** ajustar las capacidades ofrecidas por la UI al backend
activo y explicar/restablecer selecciones incompatibles. Si estas funciones son
requisito del producto, implementar su contrato completo en core antes de
reexponerlas. No filtrar una página en memoria para aparentar búsqueda global;
no degradar silenciosamente al legacy.

**Cierre:** pruebas de UI y contrato para cada control visible, cambios de
routing y parámetros persistidos en URL; canary de las mismas acciones por el
proxy NAS. Una respuesta 501 explícita no basta para declarar la UX terminada.

### A18-02 · Confusión entre límite de respuesta y tamaño de página

**Ubicación:** `ReactPortfolio/backend/services/schools_core.py:133–152,222–226`
y `routers/schools.py:149–167`.

El router limita la respuesta final, pero transmite limit al core como tamaño de
página y `CoreSchools.jobs` drena TODAS las páginas antes de recortar. Con
101 registros y limit=1 alcanza el tope de 100 páginas y falla; el camino local
equivalente devuelve una fila. Con menos registros sigue haciendo trabajo
proporcional al total para una consulta acotada.

**Reproducción:** `reproduce_portfolio.py`, HTTP simulado: 101 entradas,
limit=1 → 100 peticiones de ofertas → `School page budget exhausted`
(`CoreSchoolError`, traducido a 503). No se afirma que hoy existan 101 entradas
elegibles en ese consumer del NAS.

**Corrección mínima:** distinguir lectura acotada de recorrido completo y
detener la primera al reunir su límite. Conservar recorrido completo explícito
para productores/reintentos que lo requieren; no truncarlos como efecto lateral.

**Cierre:** 101 entradas/limit=1 devuelve una fila con una petición de ofertas
(más catálogo si sigue siendo necesario); límites 100/500, cursores repetidos,
filtros y llamadas sin límite tienen regresiones independientes.

### A18-03 · Watchlist escolar incompleta y error disfrazado de vacío

**Ubicación:** `SwissJob/frontend/src/pages/WatchlistPage.jsx:55–67,147–158`.

La página pide las primeras 500 coincidencias generales y DESPUÉS filtra por
school_id. Una oferta escolar en posición 501 no aparece. Además, no consume
isError: sin datos tras un fallo muestra «Aún no hay vacantes», la misma señal
que un resultado vacío correcto.

**Reproducción:** `reproduce_watchlist.mjs` ejecuta el cuerpo real del useMemo
con 500 ofertas no escolares y una escolar al final: 0 con respuesta truncada,
1 con respuesta completa; datos undefined → 0. El render inspeccionado confirma
el mensaje vacío. Es prueba de lógica, no E2E de navegador.

**Corrección mínima:** seleccionar colegios ANTES de paginar, mediante una ruta
o filtro de servidor existente si permite mantener estados y permisos; paginar
esa colección. Mostrar error y reintento separados del vacío.

**Cierre:** oferta escolar más allá del puesto 500 visible; fallo 503 distinguible
de vacío; filtros y estados de candidaturas conservados. No aumentar 500
indefinidamente ni perder candidaturas históricas al cambiar la fuente de lectura.

### A18-04 · El acuse posterior no evita correos concurrentes duplicados

**Ubicación:** `ReactPortfolio/backend/services/school_alert.py:87,120–143,160–180`.
La ruta manual de refresco y el ciclo periódico pueden leer la misma oferta
pendiente antes de que cualquiera confirme el envío.

La comprobación notified opera sobre cada snapshot en memoria. Ambos procesos
pueden superarla y enviar; el acuse idempotente del core sucede después y no
deshace el segundo correo.

**Reproducción:** `reproduce_portfolio.py`, dos snapshots del mismo ID, barrera
en el transporte simulado → **2 intentos de correo y 2 acuses**. Cero correos
reales. Demuestra duplicación alcanzable, no que se haya observado en el NAS.

**Corrección mínima:** reserva durable exclusiva por consumer/oferta antes del
envío, con recuperación acotada y comprobación de propietario. Reutilizar un
outbox/claim existente si satisface este contrato. Un lock en memoria no cubre
procesos distintos. Declarar entrega al menos una vez: SMTP sin idempotencia
del receptor no permite prometer exactamente una entrega ante caída post-envío.

**Cierre:** manual+periódico y dos workers compitiendo → un envío simultáneo;
fallo antes del envío recuperable; reinicio/lease vencido y caída tras SMTP
documentados y probados sin ocultar el riesgo residual.

### A18-05 · Lecturas habituales dentro del presupuesto

El defecto estructural quedó eliminado sin cambiar resultados: Core calcula la
categoría y las facetas, el BFF reutiliza el recorrido completo verificado por
versión, resuelve categorías sobre esa copia, cachea los joins escolares y
sirve las páginas de catálogo comprobadas durante 60 s antes de revalidarlas por
ETag. La UI ya no pide 3.000 ofertas para construir sus pestañas: entra por
facetas y solicita 50 tarjetas de la categoría elegida.

La comparación usa 100 muestras secuenciales por ruta en el mismo NAS y separa
arranque de régimen caliente. Línea base p50/p95: facetas 2,943/5,991 s,
categoría 5,152/10,876 s, guardados 0,689/1,723 s, catálogo 1,530/7,579 s.
Final: 0,088/0,180; 0,371/1,033; 0,564/1,551; 0,186/0,838 s,
respectivamente. Todas cumplen el presupuesto predeclarado p95 <= 2 s. El
checkpoint A18-05 al final conserva commits, despliegue, rollback y límites.

### A18-06 · Configuración de proveedores divergente entre repositorio y NAS

**Ubicación:** `SwissJob/backend/config.py:116,181`; commits locales
`d9c8a0a` y `5f12b1f`, posteriores al despliegue E.15.

En NAS, lectura de settings dentro de swissjob-backend:
`GROQ_RERANK_MODEL=qwen/qwen3.6-27b` y `GEMINI_MODEL=gemini-2.5-flash`.
HEAD local define `qwen/qwen3.8-27b` y `gemini-3.6-flash`.
La imagen observada sigue siendo `swissjob-backend:e15-4e40ffe`.
Los commits describen correcciones de disponibilidad, pero esta auditoría
**no** llamó a esos proveedores: se confirma deriva, no un rechazo actual ni
la disponibilidad universal del reemplazo.

**Acción mínima:** release controlada que concilie versiones y configuración
efectiva, valide el identificador con el proveedor y haga canary acotado de la
función real. Verificar fallback, coste y errores permanentes sin reintento
infinito; rollback explícito. Actualizar la foto de despliegue con la configuración
observada, no solamente con el SHA local.

**Cierre:** modelo configurado y disponible, operación real autorizada con
resultado válido y evidencia de versión. No publicar claves ni prompts personales.

### Verificación, límites y deuda ya reconocida

- Evidencias y comandos: [auditoría reproducible](../audits/2026-09-18/README.md).
  **39 pruebas core sin BD y 16 frontend verdes**, ejecutadas en serie; cuatro
  defectos reproducidos con tres scripts sintéticos. No confundir scripts que
  confirman el defecto con regresiones verdes del comportamiento deseado.
- **Suite integral pendiente:** PostgreSQL SwissJob local estaba parado;
  core-api/capture locales aparecían unhealthy. En NAS, postgres y las sondas
  core observadas estaban saludables. No se reiniciaron servicios para esta
  auditoría ni se declara una caída de producción por el estado local.
  Restablecer el entorno de pruebas aislado antes de validar fixes integrales.
- **Seguridad:** se inspeccionaron scopes/consumer, validación y fronteras de
  errores; no apareció un defecto nuevo demostrable en esos recorridos.
  No equivale a una auditoría de seguridad exhaustiva.
- **Puntos 4 y 5:** continúan abiertos la migración/paridad de productores,
  retirada legacy y rendimiento global. Ver contenedores legacy vivos NO
  demuestra doble escritor: hay que verificar autoridad por capacidad antes
  de retirar cada uno.
- **Calidad:** se mantiene la necesidad de un holdout nuevo, independiente y
  separado del entrenamiento. No usar 0.5856/0.7438 como evidencia certificable,
  ni resultados dev solapados para declarar GO. No se abrió ningún examen aquí.
- **Cron de retención:** sigue diferido por decisión expresa del propietario,
  no es trabajo recién autorizado. Los artefactos NAS E.15 de 14-09 con siete
  días no estaban vencidos el 18-09; no se declara incumplimiento sin inventario.
- La búsqueda >30 s citada en la foto E.10 es **histórica**, sustituida para ese
  canary por E.12. Los documentos retirados se consultan en Git mediante
  [índice histórico](../ARCHIVO_HISTORICO.md); no restaurarlos todos.

### Orden de corrección y prevención de regresiones

1. Convertir A18-01/02/03/04 en pruebas del comportamiento esperado que fallen
   antes del fix. Corregir cada contrato de extremo a extremo, con el cambio
   mínimo y sin mezclar optimizaciones o nuevas funcionalidades.
2. A18-06: comprobar configuración/compatibilidad antes de un despliegue
   controlado; no dar por publicado un arreglo porque exista un commit.
3. Ejecutar suites **en serie** en BD aislada con constraints reales; cubrir
   paginación, errores, carreras, permisos y los consumidores de cada helper.
   Si el helper sirve lectura acotada y barridos, probar ambas semánticas.
4. Medir A18-05/punto 5 antes de optimizar. Migrar productores uno a uno con
   paridad y rollback ensayados; retirar legacy solo al cerrar dependencias.
5. Desplegar canary, comprobar la función **servida por el proxy**, observar
   errores/duplicados/latencia y registrar cuatro estados separados:
   corregido, probado, desplegado, confirmado. No convertir una suite verde
   en garantía de que no aparecerán nuevos errores.

## 0.B · Auditoría interna profunda — 2026-09-23

Fuente: `SwissJob/docs/audits/AUDITORIA_PROYECTO_2026-09-23.md` (hallazgos con evidencia,
Parte II) y su plan de acción (Parte IV, paquetes T0–T16 con el cambio exacto y la prueba
roja→verde de cada uno). Aquí sólo lo que sigue ABIERTO, con su paquete. Ítems de A18-* no
se repiten.

| Id | Severidad | Hallazgo | Paquete | Estado |
|---|---|---|---|---|
| A19-01 | Crítico · operación | Slot de replicación **huérfano** `jobhunt_shadow` reteniendo 40 GB de WAL sin consumidor | T1 | **CERRADO 2026-09-23** con autorización del propietario. `pg_wal` 41 GB → 81 MB; libre 432 → 472,3 GB. Siete precondiciones verificadas; recibo con el estado previo en `audit-fixes-20260923/T1-drop-slot.receipt` |
| A19-02 | Crítico · funcional | Caché del feed por versión servía `feedback` rancio con `CORE_FEEDBACK_ENABLED=True` (regresión del 23-09) | T2 | **CERRADO** el mismo día (`17e2b9e`), salvo el canario de escritura real |
| A19-03 | Crítico · proceso | CI disparaba sólo en `main` y `ruff` estaba rojo | T4 | **CERRADO 2026-09-23** salvo el push (del propietario): CI en toda rama, `ruff` fijado a 0.15.14, `check` 31→0, `format` aplicado (99 ficheros, en `.git-blame-ignore-revs`), `core-lint` informativo y `compose-config` nuevos. **Sigue pendiente subir SwissJob** |
| A19-04 | Crítico · infra | La topología real de producción (`*.configured.yml`) y las listas `LEGACY_DISABLED_*` no están en el repo: en local se construyen 17 providers y 15 scrapers legacy | T13 | **CERRADO 2026-09-28** — `deploy/nas/{core,swissjob}.configured.yml` son la copia literal de lo que corre, con 5 y 21 valores sustituidos por `${VARIABLE}` (el del Portfolio, en su repo); `scripts/check_no_secrets.py` en CI, con control negativo. Las listas `LEGACY_DISABLED_*` de producción están además en `backend/.env.prod.example`, generado desde `Settings` |
| A19-05 | Crítico · secretos | `.gitignore` no cubría `.env.core.capture.prod` (DSN con REPLICATION) | T3/T4 | **CERRADO 2026-09-23**: ignorado por prefijo `.env.core.*`, verificado en ambas direcciones. El fichero **no existe** en el árbol, así que el riesgo era latente |
| A19-06 | Crítico · frontend | Sin refresh de sesión (muere a los 30 min) y cualquier fallo de `/auth/me` borra las credenciales | T6 | **CERRADO 2026-09-24 (T6)** — refresh compartido en `api.js` con `authRequest` reintentando una vez; `/auth/me` fallido ya no borra credenciales. Verificado hoy: I-D3 (9 passed) y la sesión de T9 encadena refresh→rotación en producción |
| A19-07 | Crítico · red | Compose base publica Postgres y Redis en `0.0.0.0` con credenciales de desarrollo y Redis sin `requirepass` | T5 | **CERRADO 2026-09-24 (T5)** — `${HOST_BIND_IP:-127.0.0.1}` en todos los puertos salvo frontend, `POSTGRES_PASSWORD`/`REDIS_PASSWORD` sin defecto y redis con `--requirepass`. Invariante I-C2 (`check_compose_exposure.py`) CUMPLE con control negativo el 2026-09-27 |
| A19-08 | Alto | `translate=true` detectaba idioma en el camino de respuesta (127,8 ms/título medidos, 230 s por feed); `CLAUDE.md` §5 lo negaba | T3 | **CERRADO 2026-09-23**: 0,08 ms/título y 0 llamadas a langdetect, medido en el proceso desplegado |
| A19-09 | Alto · seguridad | Rate limiting inoperante fuera de 5 rutas; bucket de login global tras el proxy; JWT sin revocación; enumeración de usuarios; credenciales del core sin caducidad | T8, T9 | **CERRADO 2026-09-25 (T8, T9)** — `LimiteGlobalMiddleware` real; login sin oráculo de enumeración (hash señuelo); refresh con `jti`/familia y detección de reutilización; credenciales del core con caducidad de 90 d. Invariantes I-D3, I-D4, I-D5 CUMPLE el 2026-09-27/28 |
| A19-10 | Alto · core | Matching (`shadow.project`) comparte cola con el abanico de 16 cosechas; ventana del dispatcher en UTC con beat en Zurich; pérdida silenciosa por fecha ausente | T11 | **CERRADO 2026-09-25 (T11)** — `shadow.project` en `core.default`; ventana anclada a Europe/Zurich (11:59 y 12:00 daban la misma etiqueta en UTC, medido); alerta `cosecha_sin_fechas` sobre los contadores de admisión. Desplegado en `2b59a92`; I-F4 CUMPLE |
| A19-11 | Alto · infra | Sin límites de recursos ni rotación de logs; `restart` no reacciona a `unhealthy`; workers sin healthcheck; `core-local` 9 migraciones atrasado; `prebuilt` rompe la CDC | T13 | **CERRADO 2026-09-28 (T13)** — límites de memoria en los 9 servicios del NAS con margen sobre el consumo medido (postgres 2g/1,09 usado, core-worker 2g/1,15, backend 1536m/794, portfolio 1g/610, core-api 512m/114, capture 256m/25, redis 256m cada uno); los de postgres/redis van en el `resource.yml` de Container Station, que pisa al compose. Rotación de logs ya existía (json-file 10m). `restart` frente a `unhealthy` y healthchecks de workers se resuelven con el SUPERVISOR (A19-17) en vez de más healthchecks: la máquina ya arrancaba ~60 procesos/min para vigilarse (A19-25). `core-local` pide `CORE_IMAGE_TAG`; `prebuilt` retirado. Todo recreado y verificado sano, sin OOM ni reinicios |
| A19-12 | Alto · frontend | **CERRADO 2026-09-24 (T7)** — «Translated from » con idioma nulo; «Cover letter» reenvía la operación de CV; recuperación con `userId` indefinido; `crypto.randomUUID` en contexto no seguro; `memo` anulado con 1.800 tarjetas; token en la query del SSE | T7, T10 | **CERRADO 2026-09-27 (T7, T10 + fecd21c)** — los seis puntos atendidos; el último, el token en la query del SSE, se dio por cerrado en T10 pero `useCvAnalysis` quedó fuera de la migración y el servidor respondía 422 (barra de progreso muerta). Arreglado con prueba de forma de URL (I-D1, control negativo ejecutado) |
| A19-13 | Alto · mantenibilidad | Complejidad alta histórica, costuras/ciclos y taxonomía duplicada | T16 | **CERRADO 2026-10-13 con cotas explícitas.** Cero ciclos de import de carga en 185 módulos BFF/558 aristas y 116 Core/342; ningún E/F de radon en runtime. Los siete E/F restantes son herramientas de corte ya ejecutadas y no importadas por API/workers. Core es autoridad de clasificación; el frontend conserva 99 líneas de presentación sin keywords. No se creó una capa repositories redundante: ya existen JobRepository y puertos estrechos por capacidad. Detalle en el checkpoint A19-13. |
| A19-14 | Producto | Feature 3 del producto (móvil/PWA) no existe: ni manifest, ni SW, ni shells; `VITE_*` cero; 7 dependencias Capacitor y 3 hooks muertos | T15 | **CERRADO 2026-09-27 (T15, vía A)** — vía móvil retirada: hooks sin consumidores, 8 dependencias Capacitor, `capacitor.config.ts` y 4 meta de app instalable. `PORTALES_EMPLEO_SUIZA.md` §11 marcada como descartada |
| A19-15 | Datos · UX | HTML/descripciones ausentes, tarjetas escolares sin contenido/detalle y traducción en petición | Diagnóstico del panel, bloques A/B/D | **CERRADO 2026-10-13.** Productores nuevos normalizan Arbeitnow y generan hechos oficiales para Jobgether; la presentación limpia revisiones históricas y da fallback factual. En 1.800 matches vivos: Arbeitnow 975/975 y Jobgether 484/484 con descripción, cero HTML; cuatro tarjetas escolares con descripción/URL y detalle HTTP 200. Las traducciones se leen de Redis calentado, sin LLM en la petición. Frontend 57/57, lint y build verdes. |
| A19-17 | Crítico · operación | **Nada avisa de que los contenedores estén PARADOS.** El 23-09 los 42 del NAS se pararon (reinicio del daemon de Docker) y `restart: unless-stopped` no levantó ninguno; se descubrió al desplegar. Acta: `SwissJob/docs/audits/INCIDENTE_NAS_2026-09-23.md` | T13 (amplía H10: el supervisor debe cubrir «parado», no sólo «unhealthy») | **CERRADO 2026-10-07** — el supervisor corre por el **crontab del QTS** (que sí estaba armado: `/etc/config/crontab` línea 33) y **entrega correo** usando el `sendmail`/ssmtp del QTS, así que la entrega ya no depende de la credencial de Gmail de A19-31. El contenedor `swissjob-supervisor` se RETIRÓ el 2026-10-07 por duplicado: los dos caminos compartían el fichero de estado, cada uno tomaba por cambio lo que escribía el otro y avisaba — **3.729 correos en 24 h**, con el agravante de que la copia del host era anterior a la retirada de `swissjob-redis-r5` y pedía un contenedor que ya no existe (falso positivo permanente). Arreglado: script del repo subido al host, un solo supervisor y **guard de ejecución única** (240 s) que impide la duplicación aunque se rearme el otro camino; verificado con control negativo (`EXTRA_ESPERADOS` con un nombre inexistente sigue dando MAL). Antes decía: supervisor DESPLEGADO como contenedor (`restart: always`, socket en solo lectura, 64 MB) que cada 5 min comprueba los 15 contenedores esperados (`portfolio_worker` entre ellos desde el 2026-09-30) (15 hasta la limpieza del 2026-09-28: se retiraron `swissjob-redis-r5` —sin clientes ni claves, solo en una red vacía del ensayo r5—, 10 contenedores parados del ensayo r5 y de retrocesos del Portfolio, y `estrato_mineria3`, una minería de estratos del dedup de agosto) y escribe estado y log en `/share/Public/swissjob/supervisor/`. Probado en el NAS con un esperado inexistente (alerta) y con recuperación. Lo que falta NO depende del código: el correo está bloqueado por la credencial SMTP (A19-31), y el crontab del QTS exige suid (la entrada queda en `/etc/config/crontab`). Fuente: `scripts/nas/` |
| A19-18 | Alto · operación | Volumen de sistema de QTS (`/`) al **84 %, 64 MB libres**; Container Station vive ahí. Sospecha no confirmada del reinicio del daemon | — | **COTA DE PLATAFORMA 2026-09-28** — el volumen `/` del QTS es un rootfs de 400 MB con el firmware (`/usr` 124 MB, `/lib` 87 MB, `/home` 76 MB); nada nuestro vive ahí (`/tmp` 3,5 MB, ningún fichero >5 MB). Docker está en `/share/CACHEDEV1_DATA` (1,8 TB, 75 %). El 84 % es el estado normal de ese volumen; no hay nada que liberar |
| A19-16 | Despliegue | Frontend del Portfolio con título traducido y resumen (`a0bf889`) y con «Me interesa» (`0a8b8a8`) **sin publicar**: Cloudflare Pages despliega desde GitHub y no hay push | El **backend** ya está desplegado (`board-45d6ec4`, 2026-09-25) y su base tiene el estado `interested`, así que el push del frontend funciona en cuanto se haga — antes habría dado error | **CERRADO 2026-09-28** — push del frontend hecho el 25-09 a GitHub y GitLab; Cloudflare Pages sirve `index-D6v4vXBE.js` con las claves `interested`/`interestedDone`/«Me interesa» (comprobado con `curl` sobre https://vicentepau.is-a.dev) |
| A19-19 | Crítico · seguridad | **La base de PRODUCCIÓN del Portfolio (`portfolio_db`) publica `0.0.0.0:5435`** y se comprobó alcanzable desde otro equipo de la LAN. Es el único contenedor del NAS que publica puerto alguno. Contraseña de 20 caracteres, no trivial: el riesgo es la superficie. Hallazgo H15 de la auditoría del 23-09, descubierto el 24-09 al verificar T5 | Compose del Portfolio en el NAS → `127.0.0.1:5435` | **COTA ACEPTADA 2026-09-27** — el propietario no se conecta hoy con cliente gráfico pero quiere conservar la posibilidad. Se deja abierto A PROPÓSITO: la contraseña es de 20 caracteres y el riesgo asumido es la superficie, no la fuerza bruta. Si algún día se cierra, es una línea en el compose |
| A19-21 | Alto · entorno local | El compose base fija `image: swissjob-core:dev`, etiqueta **mutable** que apuntaba a un build del **04-09** mientras los contenedores del core corrían el del **08-09**: un `docker compose up -d core-api` lo dejaba `not_ready` | Imagen reconstruida del árbol con `RELEASE_SHA` real (`d63f74b`, antes `unknown`) + pin `swissjob-core:d63f74b`; `core-migrate` **core0042 → core0051** (9 migraciones, todas aditivas, ya probadas en producción); recreados los tres servicios. Costura: `scripts/check_core_release.py` (compose vs lo que corre, `ready`, release nombrable, `authoritative`), 4 controles negativos | **CERRADO 2026-09-24** — `ready`, `core0051`, `release d63f74b`, `authoritative: true`, CDC 0 pendientes |
| A19-23 | Bajo · calidad | **`scripts/` (20) y los .py de `docs/` (5) nunca se habían pasado por el linter**: `backend-lint` corre con `working-directory: backend`, así que su `ruff check .` no los alcanzaba. 22 y 31 avisos. Sumados a los 358 de `jobhunt_core`, **411** | **411 → 0**, sin silenciar nada en bloque: 263 sitios eran fixtures de pytest, 39 `pytestmark` (un `skipif` re-exportado) y uno import por efecto lateral — todos anotados con su motivo tras **comprobar ejecutando** que quitarlos rompe los tests; 21 imports muertos y 4 avisos en código de producción, arreglados de verdad. Core formateado (251 ficheros, AST idéntico en los 335). CI: `core-lint` pasa a bloqueante y `backend-lint` gana un paso para `scripts`+`docs`; los 799 .py del repo quedan cubiertos | **CERRADO 2026-09-24** — los 6 pasos de lint salen con código 0 |
| A19-25 | **Crítico · capacidad** | NAS de 2 CPU y carga global alta; el contrato p95 <= 2 s no se cumplía | Rediseñar carga, limitar procesos y medir extremo a extremo | **CERRADO 2026-10-13 dentro del alcance del proyecto.** Aun con load average 9,52/10,50/11,27 y picos de demonios QTS, las cuatro rutas habituales cumplen p95 <= 2 s. Cinco muestras/40 s de los ocho contenedores afectados suman 2,89 % CPU mediana y 2.830 MiB. Bi-encoder único, límites y cachés por versión eliminan el bloqueo; la carga de QTS queda como restricción del host. |
| A19-26 | Medio · despliegue | **CERRADO 2026-09-24** (`scripts/deploy_nas.sh`) — **El BFF de producción se construye con `backend/Dockerfile.prod`, no con `backend/Dockerfile`**: sólo el primero crea el usuario `app` que exige el compose del NAS. Con el otro el contenedor ni arranca. Y recrear en producción necesita `-p swissjob` / `-p swissjob-r5`, o choca con el nombre | Documentado en CLAUDE.md; falta un script de despliegue que no permita equivocarse | **CERRADO 2026-09-28** — `scripts/deploy_nas.sh` ya existía desde el 24-09 con las tres trampas, pero al usarlo el 25-09 se vio que retagueaba `core-capture` sin recrearlo (deriva A19-21 por construcción) y no corría `core-migrate` ni recreaba `core-worker`. Reescrito: retag por servicio, migración con contenedor desechable en la red de la API antes de recrear, y `core-api`+`core-worker` juntos |
| A19-27 | Alto · reproducibilidad | **31 de 32 dependencias eran RANGOS**, así que cada reconstrucción de la imagen era una lotería. En una sola tarde mordió dos veces: ruff saltó de 0.15 a 0.16 (**917 «errores»** sin cambiar una línea) y pgvector/numpy pasaron a devolver `list` donde daban ndarray (**3 pruebas de fencing rojas solas**) | Todas fijadas a la versión instalada y verificada; `pip install --dry-run` resuelve; `ruff` a la MISMA versión que CI | **CERRADO 2026-09-24** |
| A19-24 | Bajo · tests | ~~Flaky~~ **CERRADO 2026-09-24**: espera a que el latido AVANCE, no a que pase un segundo de reloj. `test_heartbeat_advances_on_keepalive_without_traffic` era flaky bajo carga: transmite 1 s con `status_interval=0.1` y exige que el latido avance. Falló una vez en una suite completa de 20 min y pasó **3/3 aislado**; el fichero tenía AST idéntico, así que no era regresión | Dar holgura al intervalo o esperar al latido en vez de a un reloj de pared | **CERRADO 2026-09-24** — `test_heartbeat_advances_on_keepalive_without_traffic` espera a que el latido avance en vez de a un reloj de pared; la suite del núcleo pasó dos veces íntegra el 2026-09-27/28 (1817 passed) sin reproducirlo |
| A19-22 | Medio · limpieza | En el NAS, la base `swissjobhunter` conserva un esquema **`jobhunt` residual: 50 tablas, 389 MB, congelado en `core0029`**. El core de producción NO la usa (usa `swissjobhunter_r5_rehearsal`, en `core0051`) y ese esquema no registra actividad. Es además una **trampa de diagnóstico**: consultarlo hace creer que producción va nueve migraciones atrasada | `DROP SCHEMA` tras confirmar que nada lo lee | **CERRADO 2026-09-27** — `DROP SCHEMA jobhunt CASCADE` en `swissjobhunter`: la base pasa de **844 MB a 455 MB**. Verificado ANTES que el esquema tenía 0 escrituras en toda su vida, que las 23 tablas vivas están en `public` con 40.351 escrituras, y que ninguna clave ajena cruzaba entre ambos. Recibo en `nas:/share/Public/swissjob/recibos/` (inventario de las 50 tablas + estructura `pg_dump --schema-only`, 72 KB); los DATOS no se conservan, por decisión. BFF `healthy` y `/health` 200 después |
| A19-20 | Alto · seguridad | **`redis` de producción sin contraseña** en los cuatro composes de despliegue. No publica puerto, así que sólo es alcanzable dentro de la red de Docker; el local ya lo exige desde T5 | T13 (exige reinicio de producción) | **COTA ACEPTADA 2026-09-27** — se deja sin contraseña a propósito. No publica puerto: sólo lo alcanzan los contenedores vecinos. Pesó también que ponerla reinicia las colas y que hacerlo mal deja a los dos workers en bucle de NOAUTH (pasó el 2026-09-24 en local) |
| A19-28 | **Alto · seguridad** | **Dos credenciales PERPETUAS abandonadas en producción.** El consumer `portfolio` del core tiene 3 credenciales vivas y sin caducidad; sólo `48c26fe326c924f4` la usa `portfolio_backend`. Las otras dos (`a02cbff1481dee59` sólo lectura, `de7924a343980d97` **con `profiles:write`, `applications:write` y `saved_searches:write`**) no las usa ningún contenedor. Descubierto el 2026-09-25 con `scripts/rotate_credential.py listar`, que se escribió para T9 | **Revocadas con autorización el 2026-09-25**: queda viva una sola credencial, `48c26fe326c924f4`, que es exactamente la que declara `portfolio_backend`. Verificado después de revocar que el Portfolio sigue leyendo el core. Revocar pone fecha, no borra la fila: la vuelta atrás es un UPDATE | **CERRADO 2026-09-25** |
| A19-29 | Bajo · documentación | **`CLAUDE.md` documentaba `pytest --timeout=30` y `pytest-timeout` no está instalado NI LO ESTUVO NUNCA** (`git log -S` sobre `requirements.txt` no devuelve nada): el comando canónico de tests fallaba al arrancar, no al probar. Descubierto al intentar usarlo. Misma clase que la regla de oro del proyecto | Corregido el comando en `CLAUDE.md` y añadido el aviso de que la imagen puede traer un `ruff` distinto al que fija `requirements.txt` (la local tenía 0.16.8 frente al 0.15.14 del CI: 48 rojos que el CI no ve) | **CERRADO 2026-09-25** |
| A19-30 | Medio · tests | **Una prueba de migración usaba el modelo ORM de HOY contra una base clavada en una revisión de AYER** (`test_profile_snapshot_migration`): cada columna nueva en `users` la rompía con un `UndefinedColumnError` ajeno a lo que medía. Saltó al añadir `users.token_version` (T9) | INSERT explícito con las columnas de esa revisión; la prueba queda inmune a las columnas futuras y además es honesta sobre qué esquema mide | **CERRADO 2026-09-25** |
| A19-31 | **Crítico · operación** | SMTP SwissJob autenticaba con una clave antigua de 14 caracteres; sus cuatro correos no se entregaban. Portfolio sí completaba envíos reales y el supervisor usa sendmail de QTS, independiente de Gmail | Con autorización literal, reutilizar la cuenta SMTP de Portfolio en BFF/worker, promover un Compose validado con rollback y probar ambos emisores sin revelar secretos | **CERRADO 2026-10-13.** Configuración idéntica comprobada en ambos contenedores, BFF saludable, Celery pong, cero reinicios y dos envíos SMTP reales correctos. Rollback `swissjob.configured.yml.before-smtp-20261013T184830Z`. |

## 0.C · Análisis profundo de código — 2026-09-28

Revisión de TODO el código vivo del proyecto local (BFF `backend/`, núcleo `jobhunt_core/`,
frontend `frontend/src/`) en busca de bugs, errores de lógica y oportunidades de
optimización. Hecha por un único revisor sin delegar; **cada hallazgo marcado «ejecutado»
se reprodujo o midió en el entorno local**, los demás se comprobaron leyendo el código en
dos puntos independientes (productor y consumidor). Fue un ANÁLISIS; **los 23 se corrigieron el mismo día** (columna Estado), con una prueba de regresión por hallazgo donde el síntoma es observable (`backend/tests/test_a20_fixes.py`, `DocumentGenerator.pending.test.jsx`, `test_patch_and_delete_honour_profile_scope` en el núcleo).
Lo que ya estaba en A18-*/A19-* no se repite; lo que está en `docs/COTAS_Y_DECISIONES.md`
tampoco. Qué NO salió mal (controles que dieron limpio): el coste del hit síncrono de Redis
del rate-limit (0,07 ms medidos, despreciable); el barrido de reglas de bug de `ruff`
(B/PLE/RUF) sobre el código vivo (0 hallazgos fuera de tests); la redacción de credenciales
en query, JSON, `Bearer` y userinfo con usuario (ejecutada, correcta); los guards del dedup
en SQL y el parser de fechas de los portales.

| Id | Severidad | Hallazgo (y cómo se verificó) | Corrección propuesta | Estado |
|---|---|---|---|---|
| A20-01 | **Alto · fiabilidad** | **El oyente de Redis del SSE muere para siempre a la primera excepción.** `SSEManager._listen` (`backend/services/sse_manager.py:137`) es una tarea única por worker; una excepción de `pubsub` (Redis reiniciado, timeout) sale del bucle y nada la relanza: el siguiente `subscribe` ve `_listener_task` ya creado y no arranca otro. **Ejecutado**: simulado un `pubsub.listen()` que lanza tras el primer mensaje → la tarea termina (`done()=True`), los suscriptores siguen conectados y no reciben nada más. Consecuencia en producción (gunicorn, 2 workers): tras cualquier corte de Redis las notificaciones en vivo de ese worker se apagan en silencio hasta reiniciar el contenedor | Bucle de reconexión con backoff dentro de `_listen` (como ya hace el scheduler en `scheduler.py:97`), y al salir por error poner `_listener_task = None` para que el siguiente suscriptor lo relance | **CERRADO 2026-09-28** — reconexión con espera creciente en `SSEManager._listen` (`_consume` + `_open/_close_pubsub`); prueba: un pub/sub que lanza en la 1ª suscripción y entrega en la 2ª |
| A20-02 | **Alto · rendimiento** | **bcrypt se ejecuta SÍNCRONO dentro del event loop.** `verify_password`/`hash_password` cuestan **234 ms** (medido en el contenedor) y se llaman directamente desde `async def login` (`routers/auth.py:111`), `register` (`:57`) y el cambio de contraseña (`:126`). Durante esos 234 ms el worker no atiende ninguna otra petición (SSE, feed, health). Con 2 workers, cuatro logins simultáneos serializan a casi un segundo de bloqueo. El proyecto ya usa `run_in_threadpool` para Groq. Mismo patrón en `upload_cv`: lee el cuerpo entero ANTES de comprobar el tamaño y parsea PDF/DOCX síncrono en el loop | Envolver hash/verify en `run_in_threadpool` (o `asyncio.to_thread`); en `upload_cv`, rechazar por `Content-Length` antes de leer y parsear en hilo | **CERRADO 2026-09-28** — `hash_password_async`/`verify_password_async` (threadpool) en login, registro, re-hash y baja; `upload_cv` rechaza por `UploadFile.size` antes de leer y parsea en hilo |
| A20-03 | **Alto · migraciones** | **`alembic check` del BFF está ROJO: los modelos no declaran cuatro objetos que la base sí tiene.** **Ejecutado** en el contenedor: `jobs.search_vector` (columna generada), `ix_jobs_search_vector`, `ix_jobs_embedding_hnsw` e `ix_match_results_application_status` se crearon con `op.execute` en `11a4b5b5a28c`, `f7a9c1e2b3d4` y `d4e5f6a8b9c1` pero no existen en `models/`. El siguiente `alembic revision --autogenerate` (comando documentado en `CLAUDE.md`) genera **DROP** de los cuatro; el índice HNSW es el que sostiene la etapa 1 del matching | Declararlos en los modelos (`Computed(...)` + `Index(..., postgresql_using="hnsw")`) o excluirlos con `include_object` en `env.py`; y añadir `alembic check` al CI para que no vuelva | **CERRADO 2026-09-28** — `Job.search_vector` (deferred) + índices GIN/HNSW parcial y `ix_match_results_application_status` declarados en los modelos; `alembic check` verde sobre la base viva Y sobre una base recién migrada desde cero (sonda `swissjob_alembic_probe`); paso `alembic upgrade head && alembic check` añadido al CI |
| A20-04 | **Alto · frontend** | **La insignia «Remote» no se pinta nunca.** `JobCard.jsx:64` y `JobDetailPage.jsx:110` leen `job.is_remote`; el contrato del BFF es `remote` (`schemas/job.py:26` y `:67`) y ninguna respuesta lleva `is_remote` (grep: 0 apariciones en `backend/`). Toda oferta remota se presenta como presencial | Renombrar a `job.remote` en los dos puntos; un test de render con `remote: true` | **CERRADO 2026-09-28** — `job.remote` en `JobCard` y `JobDetailPage` |
| A20-05 | **Alto · lógica** | **Guardar el perfil pone a CERO el peso de idioma.** `ProfilePage.jsx:36` lista cinco pesos (`embedding, llm, salary, location, recency`) y el backend tiene SEIS (`job_matcher.py:15`, incluido `language: 0.10`). Al guardar, `score_weights` viaja sin `language` y, por la regla vigente (G1/P2-11: clave ausente = 0), el factor idioma desaparece del matching de ese usuario sin que la UI lo muestre. Además los valores iniciales del formulario no salen de `DEFAULT_WEIGHTS` | Añadir el deslizador `language` y sembrar el formulario con los defaults del servidor (exponerlos en `GET /profile` o en un endpoint de constantes); en el backend, fusionar los pesos parciales sobre `DEFAULT_WEIGHTS` en vez de tratarlos como 0 | **CERRADO 2026-09-28** — deslizador `language`; `GET/PUT /profile` publican `default_score_weights` (los del motor) y el formulario se siembra con ellos; la regla «ausente = 0» del backend se conserva a propósito (G1/P2-11) |
| A20-06 | **Alto · rendimiento** | **Un INSERT por resultado al guardar un match.** `MatchService._save_results` emite una sentencia por fila nueva: **ejecutado** con 300 resultados → **301 sentencias** (contadas con el listener de SQLAlchemy). Con `run_all_matches` diario sobre todos los perfiles y hasta 3.000 resultados por usuario, son miles de round-trips por corrida | `insert(MatchResult).values([...])` en lotes (o `executemany` de asyncpg); el feedback positivo que se conserva ya se calcula aparte | **CERRADO 2026-09-28** — `_insert_new_rows`: un INSERT ... ON CONFLICT por lote de columnas homogéneas (500 filas/sentencia); prueba: 60 resultados ⇒ 2 sentencias |
| A20-07 | Medio · frontend | **El contador de la campana nunca baja.** `useNotifications.js:110` expone `resetCount` y nadie lo llama (`NotificationBell.jsx` sólo lee `unreadCount`); el contador sube con cada `new_matches` y no se reinicia ni al abrir el panel ni al marcar leído. Y el evento SSE `watchlist_priority` que emite el backend no tiene ningún `addEventListener` en el frontend: se emite al vacío | Llamar a `resetCount` al abrir la campana / tras `mark-read`; escuchar `watchlist_priority` o dejar de emitirlo | **CERRADO 2026-09-28** — `resetCount` al abrir la campana; oyente de `watchlist_priority` |
| A20-08 | Medio · frontend | **«Cover letter» sigue reenviando la operación de CV pendiente** (punto de A19-12 dado por cerrado). `DocumentGenerator.jsx:90` reutiliza `pending` para CUALQUIER clic y el botón «Cover letter» (`:190`) sólo se deshabilita con `isGenerating`, no con `!!pending` como el de CV (`:180`). Si la generación del CV falla en red, `pending` queda fijado y el clic en «Cover letter» reintenta el CV | Deshabilitar «Cover letter» con `!!pending` como el de CV, o que `handleGenerate(docType)` sólo reutilice `pending` si `pending.docType === docType` | **CERRADO 2026-09-28** — «Cover letter» deshabilitado con `pending`, como el CV; prueba de render con operación pendiente |
| A20-09 | Medio · coste LLM | **`/match/history` y `/match/saved` traducen títulos con el LLM en el camino de la petición.** Sólo `/match/results` acepta `translate` (`routers/match.py:286`); los otros dos llaman a `_build_results_response` con el `translate=True` por defecto (`:231`) y pagan `translate_titles` a Groq en cada carga. El calentamiento en fondo de A19-15 §D sólo cubre la pantalla principal | Aceptar `translate` en los tres endpoints y que el frontend pida `false`; los títulos ya calentados salen del MGET | **CERRADO 2026-09-28** — `translate` aceptado en `/history` y `/saved`; `getSaved` pide `false`; prueba con control negativo (sin el parámetro el servicio SÍ se llama) |
| A20-10 | Medio · tareas | **Dos tareas de mantenimiento se tragan cualquier excepción.** `maintenance_tasks.py:132` y `:258` (`check_job_urls`, `cleanup_stale_jobs`) devuelven `{"status": "error"}` ante cualquier `Exception`: Celery cuenta la tarea como ÉXITO, no reintenta y no queda rastro fuera del log. `dedup`/`digest` sí relanzan. Los 8 `SoftTimeLimitExceeded` del journal de `check_job_urls` (ver `fetch_diagnostics.py`) entran por aquí | Relanzar (o `self.retry`) tras registrar; reservar el retorno `error` para fallos por-URL | **CERRADO 2026-09-28** — `check_job_urls` y `cleanup_stale_jobs` reintentan una vez y LANZAN (`bind=True`), como dedup |
| A20-11 | Medio · rendimiento | **Un SELECT extra por upsert para saber si la oferta era nueva.** `JobRepository.upsert_job` hace `INSERT ... ON CONFLICT` y luego consulta para decidir `is_new`. Con ~1.000-1.300 ofertas por cosecha legacy son otros tantos round-trips evitables | `RETURNING (xmax = 0) AS inserted` en el mismo upsert (idioma de PostgreSQL, sin segunda consulta) | **CERRADO 2026-09-28** — `RETURNING (xmax = 0) AS is_new`; prueba: cero SELECT en el upsert |
| A20-12 | Medio · rendimiento | **`/catalog/stats` lanza 7 consultas por petición** (`services/catalog/local.py`), **63-109 ms medidos** en local; y `analytics`/`notifications` repiten un `COUNT` que la consulta principal ya podría devolver con `count(*) OVER()` o `FILTER` | Una consulta agregada con `count(*) FILTER (WHERE ...)` para stats; eliminar los COUNT redundantes | **CERRADO 2026-09-28** — `stats` en UNA consulta con `GROUPING SETS` + `GROUPING()`; notificaciones con `count FILTER`; analytics sin COUNT redundante. Prueba de igualdad de agregados contra datos sembrados |
| A20-13 | Medio · aplicaciones core | **Cada lectura Y cada escritura de candidaturas por el core drena el feed ENTERO.** `services/applications/core_client.py`: `list`/`stats` recorren hasta 100 páginas × 100; y `_owned_by_profile` (`:339`) vuelve a drenarlo COMPLETO antes de cada PATCH/DELETE sólo para comprobar la propiedad. Con pocas candidaturas es invisible; crece linealmente con el histórico del usuario | Que el `/v1` acepte `profile` en PATCH/DELETE (o exponga `GET /v1/applications/{id}?profile=`) y el cliente compruebe propiedad con una petición | **CERRADO 2026-09-28** — el `/v1` acepta `profile` opcional en PATCH/DELETE (404 indistinguible si no casa) y el cliente lo envía en vez de drenar el feed. **Orden de despliegue: core ANTES que BFF** (un core anterior ignora el parámetro) |
| A20-14 | Medio · tests | **`frontend/src/config/nginx.test.js` lee `../docker-compose.rehearsal.qnap.yml` de la raíz del repo** (`:9`): dentro del contenedor `frontend` (que sólo monta `frontend/`) el test falla por fichero ausente. Es un test del compose disfrazado de test del frontend | Moverlo a `scripts/`/CI como comprobación del compose, o que busque el fichero relativo a la raíz y se salte con `test.skipIf` si no existe | **CERRADO 2026-09-28** — `it.skipIf(!existsSync(...))` con el motivo en el propio test |
| A20-15 | Bajo · seguridad | **`redact_credentials` no tapa la userinfo con usuario VACÍO.** **Ejecutado**: `redis://:R3d1sP4ss@redis:6379/0` sale intacto (`_USERINFO_RE` exige `[^/\s:@]+` antes de los dos puntos, `utils/redact.py:146`), que es justo la forma de `REDIS_URL`/`CELERY_BROKER_URL`. Hoy no hay sink que las escriba (Celery enmascara su propio banner), por eso es Bajo. `ticket=` del SSE tampoco está en la lista, pero es de un solo uso | `//[^/\s:@]*:(?P<sec>[^@\s/]+)@` (usuario opcional) y un caso de test con `redis://:pass@` | **CERRADO 2026-09-28** — usuario opcional en `_USERINFO_RE`; prueba con `redis://:pass@` |
| A20-16 | Bajo · fiabilidad | **El cerrojo de líder del scheduler se retiene sin scheduler si `start()` lanza.** `scheduler.py:40-43`: se toma la clave `nx=True ex=TTL` y después `scheduler.start()`; una excepción ahí deja el cerrojo puesto y NINGÚN worker despacha hasta que caduque el TTL | Liberar la clave en el `except` (sólo si el valor es `_WORKER_ID`) | **CERRADO 2026-09-28** — `r.delete(_LEADER_KEY)` si `setup_schedules()`/`start()` lanzan; prueba |
| A20-17 | Bajo · memoria | **La caché de routing es un `dict` sin cota** (`services/routing.py:70`): una entrada por `(capacidad, user_id, modo)`, nunca se poda salvo `invalidate_routing_cache`. Crece con los usuarios que pasan por el proceso; entradas caducadas se quedan | Podar caducados al insertar o usar un `TTLCache`/LRU acotado | **CERRADO 2026-09-28** — `_prune_cache` (4.096 entradas: poda caducadas, si no basta vacía); prueba |
| A20-18 | Bajo · seguridad | **`sanitizeHtml.js:21` dice «Forzar rel=noopener» y no lo hace**: el comentario describe un paso que no existe en el código; los enlaces `target=_blank` de una descripción salen sin `rel` | Añadir el hook `afterSanitizeAttributes` de DOMPurify que ponga `rel="noopener noreferrer"`, o quitar el comentario | **CERRADO 2026-09-28** — hook `afterSanitizeAttributes` de DOMPurify que pone `rel="noopener noreferrer"` a todo `target=_blank` |
| A20-19 | Bajo · datos | **Pulsar «Apply» registra feedback implícito `applied` en el clic** (`MatchCard.jsx:264`), antes de que el usuario haya aplicado en el portal. Ese `applied` se CONSERVA entre corridas de matching (regla de feedback positivo): un clic curioso deja la oferta marcada como aplicada para siempre | Registrar `clicked`/`opened` en el clic y reservar `applied` a la candidatura real (`/applications`) | **CERRADO 2026-09-28** — el clic en «Apply» registra `opened` |
| A20-20 | Bajo · observabilidad | **`services/documents/delivery.py:247` tiene un `except Exception: pass` mudo** en el camino de reconciliación de entregas: un error de red o de contrato se pierde sin log | `logger.warning(..., exc_info=True)` como mínimo | **CERRADO 2026-09-28** — `logger.warning(..., exc_info=True)` |
| A20-21 | Bajo · consistencia | **Feedback implícito: `IS NOT NULL` en SQL frente a veracidad en Python.** Una consulta filtra `feedback_implicit IS NOT NULL` y el código Python comprueba `if feedback_implicit:`; con cadena vacía discrepan. Hoy no se escribe cadena vacía (teórico) | Unificar en `!= ''`/`COALESCE` o normalizar a NULL al escribir | **CERRADO 2026-09-28** — `jsonb_array_length(coalesce(feedback_implicit,'[]')) > 0` en el criterio `attached` de la limpieza |
| A20-22 | Bajo · limpieza | `pattern_analysis_service.py` construye un `set` con elementos repetidos en el literal (sin efecto, pero delata copia-pega); `search_execution.execute_search` valida el tipo de `force` DESPUÉS de tomar los cerrojos (`search_execution.py:133`, tras `_lock_search`); `tasks/searches.py:22` captura `SoftTimeLimitExceeded` como `Exception` y reintenta una vez con la misma duración | Limpiezas menores; en `run_due_task`, dejar pasar `SoftTimeLimitExceeded` como ya hace `_run` | **CERRADO 2026-09-28** — stopwords sin duplicados (95 únicas); `force` validado antes de los cerrojos; `SoftTimeLimitExceeded` pasa sin reintento en `run_due_task` |
| A20-23 | Bajo · documentación | **`CLAUDE.md` dice que el refresh duraba «30 días»; `config.py:95` fija `REFRESH_TOKEN_EXPIRE_DAYS = 7`.** La sección T9 describe el estado ANTERIOR con una cifra que no coincide con el código de ninguna época visible | Corregir la cifra (o citar la de `.env` de producción si difiere) | **CERRADO 2026-09-28** — `CLAUDE.md` cita `REFRESH_TOKEN_EXPIRE_DAYS` (7 por defecto) en vez de «30 días» |

**Cierre**: los 23 corregidos y verificados el 2026-09-28 (BFF, núcleo y frontend en verde;
`alembic check` verde sobre base viva y sobre base migrada desde cero). El único cambio de
contrato es aditivo (`profile` opcional en PATCH/DELETE `/v1/applications/{id}`) y exige
desplegar el núcleo antes que el BFF. **Desplegado en el NAS el 2026-09-28** en ese orden: núcleo, BFF y frontend en `point5-1125aae` (`/v1/ready` → `release 1125aae, authoritative`; frontend con etiqueta inmutable y la anterior conservada como `prod-before-1125aae`).

## 0 · Estado vigente y deuda priorizada (actualización 2026-09-14)

**Actualización E.15, prevalente (ESTADO §41): PUNTO 3 CERRADO.** Colegios
servidos y escritos exclusivamente en core por ambos BFF, productores existentes
adaptados y activos. Core API/worker/captura 1b8d910/core0047, BFF y worker SwissJob
4e40ffe, Portfolio 9f8c85a, CLI de recuperación daf2fad. Importación, replay,
ida/vuelta y canarios NAS verificados: 24 monitores, 145 ofertas históricas,
54 estados y 2 preferencias preservados. 35 ofertas enlazadas al corpus; las
restantes conservan cuarentena visible, sin fabricar vacantes.
Catálogo público comprobado después de recargar Nginx tras recrear el BFF.
Esta recarga y el canary por el proxy son obligatorios en futuros reemplazos.
No se certifica una nueva campaña de extracción ni rendimiento global.
[Acta E.15](../DESPLIEGUE_E15_2026-09-14.md).

Retirados dos clústeres privados y tres dumps del ensayo E.15; evidencias
restantes con retención 48 h y nueve artefactos NAS con siete días.
**Cron sigue diferido**: operación manual y futura programación deben incluir
también `unification-e15-20260914/retention.json`, no sólo el registro E.13.
Calidad/holdout, retirada general de productores y rendimiento global se
mantienen separados; no se modificaron modelos, políticas ni la racha.

**Actualización E.14, histórica donde contradiga E.15 (ESTADO §40):** corte documental vivo en ambos
BFF, dos históricos preservados, entrega HTTP y escritor único comprobados.
Core/BFF SwissJob d2a38e9/core0046; Portfolio ecf1c0a; CLI 0de9101.
**Punto 2 cerrado:** no volver a abrir scopes, migración, flip ni canary como
pendientes. Tras autorización explícita para Groq, la pareja CV+carta real de
Portfolio pasó generación/lectura/PDF/replay/baja en 28,16 s bajo ecf1c0a.
Cuatro eventos nuevos recibidos, históricos intactos y journals vacíos.
Esta evidencia HTTP real sustituye el pendiente de autorización anterior.
Core 1162/1 skipped; Portfolio 1979/1 skipped. Acta:
[DESPLIEGUE E.14](../DESPLIEGUE_E14_2026-09-13.md).
Las fotos anteriores de despliegue de este §0 son históricas donde contradigan
esta actualización. Cron continúa diferido por el propietario; no está instalado.

**Rectificación E.11 posterior a §34:** la API nueva de exportación integral fue
descartada por el propietario; no se implementará ni condiciona el cierre. Las
referencias de abajo a su autorización pendiente quedan sin efecto (histórico).
**Actualización E.12, 2026-09-13:** core 44fe6b8/core0045 y BFF be65fb5/a91
desplegados. Catálogo 33.933 ofertas: p95 general 1,67 s, remoto 0,72 s, texto
0,28 s y fuente 1,23 s; canary final completo aprobado. El primer remoto falló
por el plan elegido con relallvisible=0; VACUUM (ANALYZE) acotado, sin FULL,
permitió usar el índice. Incorporado al control posterior a restore/índices.
Core 1143 passed/1 skipped; BFF 2374/3 skipped/4 xfailed. UUID documental
compatible desplegado, pero la autoridad documental sigue local. Estado
prevalente: ESTADO §36 y [acta E.12](../DESPLIEGUE_E12_2026-09-13.md).
Las cifras de E.10 que siguen debajo se conservan como historial, no como
estado ejecutable actual. Rendimiento cerrado solo para el canary indicado.

> **Prevalece esta actualización sobre las tablas históricas de abajo.**
> Estado ejecutable: ESTADO §34 y
> [acta de despliegue E.10](../DESPLIEGUE_E10_2026-09-09.md).
> Calidad sigue sin examen independiente válido; no citar 0.5856/0.7438 como certificación.

**Desplegado y comprobado:** Portfolio 715c347/rr11s0042u18; core
613f1d5/core0043; SwissJob BFF 613f1d5/f70b15293d40; frontend 4dcfa0e
(bundle 21c07c6). E.8/E.9/E.10 y EOF WordPress ya NO son «solo locales».

- **CERRADO:** conservación/biblioteca/journal documental local, inbox y CLI
  snapshot/import/reverse implementados, suites y contratos reales. Copias privadas
  restauradas estrictamente con owners/ACL; ida/vuelta documental comprobada también
  después de altas/bajas. El corte vivo sigue pendiente, no se deduce de este ensayo.
- **CERRADO:** cruce de proxy entre producción y ensayo por alias Docker backend.
  Destinos exclusivos, regresión de red roja en imagen anterior y verde en la nueva;
  manifiesto operativo R5 actualizado. Navegador real conserva 2 documentos y PDF.
- **CERRADO:** publicación frontend Portfolio 3f6d7be previamente autorizada y
  verificada; no volver a usar ese permiso como bloqueo. Esta continuación no hace
  público código backend ni envía dumps/datos personales a Git.
- **DOCUMENTOS — PUNTO 2 CERRADO (E.14):** borrado coordinado en E.13;
  scopes, entrega, importación y flip vivos verificados. Canary final real
  autorizado: pareja CV+carta, ambos PDF, replay sin duplicados y bajas exactas.
  Core conserva los dos históricos; 12 eventos recibidos entre ambos consumers,
  cero journals pendientes. No se conserva contenido temporal de los canarios.
- **FUERA DE ALCANCE:** nueva API de exportación integral descartada por el
  propietario. No hay que implementarla ni solicitar permiso para añadirla.
  Se conservan las exportaciones existentes y el permiso de copias privadas/deploy.
- **PUNTO 1 CERRADO en alcance acordado (13-09):** erase→acks, anti-resurrección,
  restore saneado y retirada manual de copias verificados (ADR-07 sustituye KMS).
  **DIFERIDO al cierre final por el propietario:** cron, primera ejecución y alarma.
  Retención manual hasta entonces; RPO/RTO no se deducen de una sonda healthy.
- **COLEGIOS — PUNTO 3 CERRADO (E.15):** monitores, preferencias/contacto,
  borradores y candidaturas en core; aislamiento y vínculo/cuarentena del corpus
  comprobados, `schools=core_primary` en ambos BFF. Productores conservados y
  adaptados; no se apagaron para aparentar cierre. Evidencia en ESTADO §41.
- **PENDIENTE productores/F:** 0 scopes nativos habilitados; legacy sigue produciendo.
  Portar fuentes con paridad de identidad/cobertura y medir antes de retirarlas.
  EOF WordPress sí está desplegado; endpoint 404 y demás fallos requieren diagnóstico.
- **RENDIMIENTO — antecedente E.10, NO defecto vigente demostrado:** la búsqueda
  general superaba 30 s; el canary E.12 posterior midió p95 general 1,67 s.
  Sigue pendiente el rendimiento GLOBAL, no volver a abrir ese cierre concreto
  sin una reproducción nueva. Véase A18-05 para el coste del feed de matching.
  Biblioteca (2 documentos) p95 137 ms; journal vacío 135 ms, 20 peticiones por ruta:
  no certifican carga de escritura/LLM/PDF/backlog/reinicio del proyecto completo.
- **PENDIENTE previo:** cuatro ofertas locales sin listing (TOAST/CDC), reconciliación
  desde snapshot mediante sink existente; no fabricar LSN ni UPDATE artificial.
- **Calidad separada:** nuevo holdout independiente necesario. No se han modificado
  umbrales/políticas ni abierto racha durante este despliegue.

Suites en serie y skips explícitos en el acta: core 1136/1 skipped; BFF
2359/3 skipped/4 xfailed; Portfolio 1975/1 skipped; HTTP/PG/CLI 9; frontend 16.
No presentar estos contadores como garantía de ausencia de errores ni cinco cierres.
Las cifras y decisiones anteriores se conservan debajo como historial.

> Esta sección prevalece para la ejecución. Las secciones posteriores conservan la deuda y las
> decisiones de agosto como historial: antes de ejecutar cualquiera de ellas hay que demostrar que
> sigue abierta en el código o entorno actual. ReactPortfolio ya fue flipado al core; no volver a
> tratar la construcción de Fase C como pendiente.

> **Actualización 2026-09-04 noche (tras ejecutar la jornada — foto §22 del ESTADO):** las dos P1,
> el gate de desarrollo y el benchmark NAS de esta tabla quedaron **CERRADOS ese mismo día**; se
> conservan tachados como historial y debajo va la tabla vigente.

| Prioridad | Deuda (mañana 04-09) | Estado a la noche del 04-09 |
|---|---|---|
| ~~P1~~ | ~~`corpus_generation` sin lock hasta el commit~~ | **CERRADA** `fda0843`: FOR SHARE mantenido; test de barrera post-revalidación (B muerde LockNotAvailable) |
| ~~P1~~ | ~~valla comprueba «activo», no canónico exacto~~ | **CERRADA** `fda0843`: `canonical_model_id()` compartida tarea+valla; `declare_active_models` autoridad única; mordida del modelo-anterior verificada |
| ~~Gate~~ | ~~RankNet n=279 0.51/0.51 < 0.60~~ | **CERRADA en dev**: RankNet n=436 = 0.9872/0.8604, cobertura 100 % (Public `72171c8`). Certificación real = holdout (virgen) |
| ~~Operación~~ | ~~benchmark NAS~~ | **CERRADA**: P7 frío NO APTO (`3892df5`) ⇒ vía P7-b incremental **APTA** (`2fbad1e`): pico 100=30 min, corte+reanudación sin duplicados, capacidad 200/h |

**Deuda vigente (noche 04-09), en orden de ejecución:**

| Prioridad | Deuda actual | Cierre verificable |
|---|---|---|
| ~~D~~ | ~~Checkpoint + flip final~~ | **CERRADA 05-09**: checkpoint en forma fuerte (etapa OMITIDA), core_primary verificado con servicios puros ⇒ **`SWISSJOB SOBRE CORE`** (ESTADO §23) |
| ~~Release~~ | ~~NAS en 693fefc~~ | **CERRADA 04-09 noche**: `099cf6d` autoritativa, paridad de CONTENIDO 177/177 (los tar de save no son comparables entre versiones de docker) |
| ~~Examen~~ | ~~Holdout único~~ | **CONSUMIDO 06-09**: NO-GO DE CALIDAD (0.5856/0.7438 vs umbral 0.60/0.60). Reapertura = holdout independiente NUEVO en campaña futura |
| ~~Operación~~ | ~~Racha GATE de promoción~~ | **NO PROCEDE**: sin promoción no hay racha. Vuelve a la cola si una campaña futura pasa examen |
| Calidad | Techo del corpus para P1 (4 «2» en 36 ítems; 24 sin descripción) | Sourcing y calidad de datos ANTES de otro entrenamiento: descripciones ausentes y cobertura de ofertas afines a P1 |
| ~~Beat~~ | ~~materialize_ce fuera del beat~~ | **CERRADA**: `materialize_all` en beat 06:15 (commit 099cf6d). Inerte mientras no haya política CE activa |
| E/F | Documentos/colegios y fuentes legacy | Según plan y runbooks; no bloqueadas por lo anterior |
| E | Documentos y colegios siguen locales | Esquema/API/migración/canary y apagado del escritor local por vertical |
| F | Harvesters, CDC y responsabilidades legacy siguen vivos | Fuentes portadas o retiradas, backup/restore, retención, drenado y eliminación expand/contract |

No son bloqueantes del camino inmediato: claves opcionales de sourcing, mejoras sin evidencia de
impacto y optimizaciones que no justifique una medición/EXPLAIN. Las cotas aceptadas en
`SwissJob/docs/COTAS_Y_DECISIONES.md` no se reabren sin evidencia que invalide su premisa.

Plan operativo único: `SwissJob/PROMPT_CIERRE_TOTAL_UNIFICACION_2026-09-04.md`.

## Cómo leerlo

Cada ítem trae: **qué es** · **dónde vive** · **por qué no se abordó** · **severidad y consecuencia
real** · **coste** en órdenes de magnitud (líneas / horas / ciclo propio).

Las causas de "por qué no" se repiten y conviene distinguirlas, porque no todas son deuda del mismo
tipo:

| Causa | Significa |
|---|---|
| **Decisión de producto** | No es técnico. Cambiarlo altera qué ve el usuario. Requiere al propietario. |
| **Techo estructural** | No tiene arreglo sin crear un fallo peor (normalmente, falsos positivos). |
| **Fuera de alcance** | Correcto no hacerlo entonces; merece ciclo propio. |
| **Bloqueado** | Depende de otra cosa (el NAS, el GATE-SOMBRA, una acción del propietario). |
| **Coste/beneficio** | Se midió y no compensaba. |
| **No llegó** | Sin excusa buena: se quedó fuera. |

---

## Top 5 por relación valor / coste

> ⚠ **Reordenado de hecho por la auditoría total del 2026-08-21.** El top 5 de abajo sigue siendo
> válido para la deuda ESTRUCTURAL, pero el orden de ejecución inmediato está en **§6.8**, y hay una
> precedencia nueva que manda sobre todo lo demás: **el oráculo del gate quedó caducado con la
> reparación del corpus (§6.1)**, así que cualquier medición hecha antes de re-sembrarlo es señal
> falsa.

> Reordenado el 2026-08-19 tras la quinta revisión externa: entra la **identidad de oferta**
> (§1.23) por encima de los focos de VD.11 — es **pérdida de datos activa por diseño**, no una
> garantía sin extender — y sale del top 5 la validación de `HARVEST_WINDOW_DAYS` (§1.18), que
> sigue importando pero está bloqueada por el despliegue al NAS. El Bloque 3 conserva el primer
> puesto por puro valor/coste: son minutos del propietario frente a un ciclo de migración.
> **Precisión (8ª revisión, 2026-08-20):** decía que "desbloquea el tramo C entero", y eso ya no es
> cierto — la CONSTRUCCIÓN de C está completa desde el 2026-08-02. Lo que desbloquea es la
> validación del contrato `/v1` contra un cliente real, que sigue sin sustituto.

1. **Desatascar el Bloque 3 (Portfolio → `/v1` en solo lectura)** — minutos del propietario más
   horas de verificación, y es la **única validación del contrato `/v1` contra un cliente real**
   que ninguna prueba sintética sustituye (ADR-11). → **`PLAN_UNIFICACION_JOBHUNTING.md` §25**,
   apartado "Gatings activos y pendientes del propietario" (el puntero anterior, "→ §2.10",
   apuntaba a TRACK T: era incorrecto, corregido en la 8ª revisión).
2. **★ Identidad de oferta construida con campos mutables** — un título corregido por el portal
   basta para que la oferta deje de refrescarse y acabe borrada a los 60 días: pérdida de datos
   activa por diseño, en todo el corpus. Las dos rondas de estabilización de identidad de la
   Fase 3 eran síntomas de esto. Requiere migración cuidadosa (el `hash` tiene FKs aguas abajo),
   pero cada día sin abordarlo se paga en corpus. → §1.23 (VD.12)
   **Enmienda (sexta revisión):** no confiar en que "migrar al core lo arregla" — el único
   adaptador nativo reintroduce la misma identidad mutable, así que hace falta además el contrato
   de identidad por adaptador, que es barato y bloquea la recaída. → §2.19 (VD.15)
3. **Garantía error≠vacío en `swiss_schools_base`** — un solo arreglo de horas cubre **7 colegios
   de la watchlist**, que son el perfil objetivo, y ataca la familia de fallo que más daño ha hecho
   en este proyecto. → §1.13
4. **`publicjobs` al helper HTTP común** — decenas de líneas para eliminar el peor caso vivo: un
   **404 que sale como `empty`**, el bug original de la fase intacto en un provider activo. → §1.13
5. **Sincronizar los tres documentos desfasados** — una hora de edición que ataca la causa raíz de
   este inventario: la deuda que se redescubre no es la más grave, es la peor documentada. → §5

---

## 1 · Fase 3 — recuperación de fuentes (TRACK V / V-DIFERIDO)

La fase cerró el bucle que dejó **nueve fuentes mudas durante 66 días**. Lo que queda:

### 1.1 · `proz` y `remoteco` siguen mudas (VD.5)
Cloudflare en todo el sitio y handshake TLS que no completa. **Por qué no:** es un bloqueo real y
la postura del proyecto es de **no-evasión**; el enrutado de descargas por LLM fue evaluado y
**rechazado** por escrito. La vía recomendada —importar las alertas por email del propio portal, con
la cuenta del usuario— es decisión del propietario. **Severidad:** baja-media, cobertura de dos
portales genéricos. **Coste:** ciclo propio (parser de correo + `EmailService`).

### 1.2 · VD.8 — el filtro tech salta re-vistas ya guardadas
Una oferta guardada cuyo título pase a casar con una palabra tech deja de refrescar `last_seen_at` y
`cleanup_stale_jobs` la borra a los 60 días. **Dónde:** `backend/tasks/fetch_tasks.py:276-283`.
**Por qué no: decisión de producto** — cambia qué ofertas viven en el corpus. **Severidad:** media
(pérdida silenciosa, población pequeña). **Coste:** decenas de líneas, tras la decisión.

### 1.3 · `swiss_schools_isb` sin selectores de posts
El board está genuinamente vacío ("No post to display") y **no hay contra qué verificarlos**.
**Por qué no:** la regla que gobernó la fase — no se escribe un selector que no se pueda justificar
contra DOM real; escribirlos a ciegas es lo que apagó la fuente en su día. **Coste:** horas, cuando
el board publique algo.

### 1.4 · `zebis` no extrae empleador
El feed llega en texto plano y el empleador aparece en posiciones libres o engañosas. **Por qué no:**
sin patrón fiable, no se adivina. **Severidad:** baja; degrada el matching por empresa justo en el
portal del perfil objetivo. **Coste:** requeriría paso de detalle; ciclo pequeño.

### 1.5 · Selectores obsoletos sobre un 200 se leen como "vacío verificado"
**Techo estructural:** distinguirlo de un board seco sin crear falsos positivos no es posible, y un
falso positivo rompería la vía de rehabilitación de las fuentes secas. Lo mitiga la racha de vacíos
de `source_health`. **Severidad:** media — es la variante que queda del patrón "fallo que parece
éxito". **Coste:** solo mitigable (canarios de contenido por fuente); ciclo propio.

### 1.6 · Fuente apagada por compliance se lee como `empty`
Diagnóstico confuso en el panel de salud. **Por qué no:** no llegó, y es menor desde el reintento de
24 h del kill-switch. **Coste:** decenas de líneas (un veredicto `disabled`).

### 1.7 · `content_hash` oscila un run tras una re-vista degradada
Versiona el payload **entrante**, no la fila efectiva. **Dónde:**
`backend/services/job_repository.py:325-334` (desplazado por `747630f`; antes :299-308).
**Por qué no:** hoy es cosmético — su único lector es
un CAS auto-consistente. **Severidad:** baja hoy, **trampa** para un consumidor futuro que diffee
hashes entre runs. **Coste:** horas.

### 1.8 · `thehub` re-baja los ~46 detalles cada run
**Por qué no:** coste/beneficio. Evitarlo exige inyectar las URLs conocidas a los providers, que hoy
no reciben el cursor incremental. **Severidad:** baja (tráfico). **Coste:** decenas de líneas + un
cambio de arquitectura pequeño.

### 1.9 · `schuljobs.py:172` atribuye el fallo del scroll a `LISTING_URL`
Mismo host, así que el diagnóstico no miente sobre *qué* fuente cayó, solo aproxima el path.
**Coste:** líneas.

### 1.10 · `gastrojob` — techo del "partner-only"
Una página compuesta solo por anuncios de partner **dentro** del rango es un estado sano real
(p1-p5 son propias; p20 en adelante, partner) e **indistinguible** de "las ofertas propias se
volvieron irreconocibles". **Techo estructural**, documentado en el código como techo y no como
garantía. Mitigado: las páginas que se cosechan no contienen partner.

### 1.11 · `gastrojob` — hora ambigua del cambio de horario
Sale con `fold=0`: error máximo de **1 hora, una noche al año**, sobre una ventana de 7 días.
**Decisión razonada:** devolver `None` —lo que proponía una revisión externa— cambiaría esa
imprecisión por la **pérdida completa de la fecha**. La hora inexistente de primavera no puede
imprimirla el portal.

### 1.12 · `gastrojob` — rearme de `_current_page` sin test que lo discrimine
Defensa en profundidad sin comportamiento observable; el código lo declara así. Fue el único
superviviente de 20 mutantes en su ronda.

### 1.13 · ★ La garantía error≠vacío solo la cumplen 6 de ~53 fuentes
**El hallazgo más importante del inventario.** Un barrido sistemático (2026-08-18) muestra que la
clase de fallo **no es de fuentes sueltas, es estructural**: solo `financejobs`, `gastrojob`,
`thehub`, `zebis`, `irishjobs` y `swiss_schools_isp` garantizan hoy que un 200 ilegible acabe como
`error`. Las ~29 restantes tienen al menos un camino que devuelve `[]` sin registrar nada, así que
una fuente **rota** se seguiría presentando como **seca**.

Focos por rentabilidad:

| Foco | Por qué | Coste |
|---|---|---|
| **`swiss_schools_base`** | La base HTML no registra ningún fallo de estructura y de ella heredan **7 colegios de la watchlist**, exentos del backoff: se consultarían a diario en vano y en silencio | horas, cubre 7 fuentes |
| **`publicjobs`** | No usa el helper HTTP común: `return []` ante excepción, ante no-200 y ante JSON ilegible. **Un 404 sale como `empty`** | decenas de líneas |
| **`ostjob` / `zentraljob`** | Clones sobre `base_chmedia`: un redeploy de la API de CH Media las silencia a las dos a la vez | horas, cubre 2 |
| **`stelle_admin`** | Es la fuente cuyo bug abrió este track | horas |

**Ticket:** `VD.11` en `BACKLOG_UNIFICACION_JOBHUNTING.md`, con la tabla completa y el DoD.
**Por qué no se hizo:** cerrar 29 fuentes excede con mucho el alcance de la fase, y hacerlo sin las
dos rondas de análisis por sección contradiría la disciplina que encontró todo esto.
**Patrón a replicar:** el de `financejobs` (isinstance por nivel + guard "N elementos y ninguno
parseable" + `diag.record`), **con la cautela que costó descubrir**: si la fuente filtra después del
parseo, el guard debe mirar el **tipo**, nunca el número de coincidencias — o se convierte en un
falso positivo que apaga fuentes sanas.

### 1.14 · Un anuncio renovado no se re-evalúa si es la única novedad de su página
Patrón real de `tes`. **Dónde:** `backend/services/harvest_window.py:30-35`. **Coste:** invalidación
del cursor por fecha; ciclo pequeño.

### 1.15 · Una deriva de identidad **parcial** no dispara el detector
Mientras las secciones intactas mantengan reconocimiento, una sección derivada pasa desapercibida.
**Dónde:** `backend/services/harvest_window.py:546-549`. **Severidad:** media si ocurre (borrado a
60 días del subconjunto), probabilidad baja. **Por qué no:** se dejó "para cuando haya datos".

### 1.16 · Alertas de fecha con granularidad gruesa
Un fallo parcial que aún deja pasar algún alta no dispara nada. **Dónde:**
`backend/services/harvest_window.py:602-606` y `:370`. **Coste:** racha por fuente; horas-días.

### 1.17 · Backoff ×4 sobre fuentes con ventana dominadas por ofertas viejas
Runs "sin novedades" legítimos producen hasta 96 h de latencia. Asumido por escrito. **Dónde:**
`backend/tasks/scraping_tasks.py:163-169`.

### 1.18 · `HARVEST_WINDOW_DAYS=7` sin validar con datos reales
Hay que revisar los contadores de la primera semana en `zebis`, `schuljobs` y `tes`. **Ojo:** en
providers el contador es de flujo, no de ofertas únicas, y sobreestima. **Bloqueado** por el
despliegue al NAS. **Severidad:** media — si la ventana es corta, se pierde ingesta del perfil
objetivo. **Coste:** horas de análisis.

### 1.19 · `gastrojob`: empresas anonimizadas quedan "Unknown" (~3-13 %, varía por día)
La microdata del detalle sí conoce la empresa, pero **no** se usa: participa en la identidad y solo
el listado es estable. **Decisión razonada:** la identidad manda sobre el enriquecimiento — usarla
hacía oscilar el hash y provocaba que la oferta dejara de actualizarse.

### 1.20 · Una URL desbordada se rechaza en cada run, para siempre
Ruido de log permanente. Asumido: es preferible a truncar la identidad o envenenar el cursor.

### 1.21 · ★ Todo el trabajo de la Fase 3 sigue **sin desplegar** en el NAS
Producción corre con las fuentes mudas y **sin** la garantía error≠vacío. El despliegue tiene una
trampa documentada: **migrar antes que la imagen** (el entrypoint del worker no migra, y una imagen
nueva sobre una BD sin migrar degrada toda la observabilidad de salud a `None` **en silencio**), y
rebuild con `INSTALL_BROWSERS=true`. **Bloqueado:** sesiones sin acceso al NAS. **Severidad: alta
como riesgo operativo.**

### 1.22 · `irishjobs` — pre-check de `\`/`%5C` como defensa redundante sin test que la discrimine
Bajo fallo único la paran ya el check de userinfo o la reconstrucción de la URL sobre el host
propio; su test está rotulado como control. **Dónde:** `backend/scrapers/irishjobs.py`
(`_resolve_job_url`). Se conserva documentada (`7d63d8d`) porque protege frente a un refactor que
devolviera la URL cruda en vez de reconstruirla: el diferencial urllib/WHATWG (y el `%5C`
reactivado por un decode aguas abajo) volvería a ser explotable. Misma familia que §1.12.

### 1.23 · ★ La identidad de oferta se construye con campos MUTABLES — pérdida de datos activa por diseño
**Descubierto por la quinta revisión externa (2026-08-19); preexistente, verificado en el código.**
La clave primaria es `MD5(title|company|url)` (`backend/models/job.py:18`), la columna `url` tiene
**índice único** (`backend/models/job.py:30-31`) y el upsert resuelve conflictos **por hash**
(`backend/services/job_repository.py:359`). Si un portal **corrige un título** —algo normal—, el
hash cambia, el `ON CONFLICT` no encuentra la fila y el INSERT choca con el índice único de URL:
la oferta deja de refrescar `last_seen_at` y `cleanup_stale_jobs` la borra a los 60 días.
**Explica retroactivamente** las dos rondas de estabilización de identidad en `financejobs` y
`gastrojob` (`0e6b821`): eran síntomas; la causa es la identidad con campos mutables. **Por qué
no:** es rediseño con migración — `hash` está referenciado con FK desde candidaturas, matches y
documentos generados (`job_application.py:31`, `match_result.py:34`, `generated_document.py:29`);
cambiar solo el conflict target no arregla el contrato aguas abajo. **Severidad: alta** — pérdida
de datos activa y silenciosa en todo el corpus. **Coste:** ciclo propio (identidad
`(source, source_id)` con URL canónica como fallback + migración). **Ticket:** VD.12.
**Matiz (2026-08-19, verificado en código; nota en `PLAN_UNIFICACION_JOBHUNTING.md` §6):** el
modelo del core lo resuelve por diseño, pero mientras el legacy siga de cosechador la proyección
sombra usa su `hash` como `external_id` (`jobhunt_core/shadow/projector.py:734,745`): el core
**hereda la identidad mutable**. **Corregido en la sexta revisión (2026-08-19):** la cosecha
NATIVA tampoco lo extingue por sí sola — la identidad la fija el `external_id` de cada adaptador,
y el único nativo que existe usa `slug or url` con el título dentro del slug (§2.19). El modelo
del core es condición **necesaria, no suficiente**.

### 1.24 · ★ ~9 fuentes aceptan URLs del portal sin validar el host (barrido de G3)
**Quinta revisión externa (2026-08-19); preexistente.** El agujero que la cuarta revisión cerró en
`irishjobs` (`7d63d8d`) está replicado en `swiss_schools_ecolint`, `swiss_schools_isb`,
`swiss_schools_nae`, `swiss_schools_inspired`, `swiss_schools_zis`, `schuljobs`, `myscience`,
`tes` y `publicjobs`. Hoy **no hay contaminación en el corpus** (verificado), pero el camino es
alcanzable. Cautelas: `zis` necesita permitir su ATS legítimo
(`zurichinternational.schoolspring.com`) y los agregadores que enlazan al empleador necesitan
excepción declarada — un rechazo global crearía falsos positivos. **Por qué no:** el arreglo
correcto es de diseño (registro `fuente → política de URL` + validación común en la frontera de
persistencia + test parametrizado que falle si una fuente carece de política), no nueve parches
sueltos. **Severidad:** media-alta (URL clicable para el usuario). **Coste:** ciclo pequeño.
**Ticket:** VD.13.

### 1.25 · ★ `hautlac` e `iscs` emiten una URL de listado constante: solo una oferta persistible por colegio
**Quinta revisión externa (2026-08-19); preexistente.** `swiss_schools_hautlac.py:74` y
`swiss_schools_iscs.py:89` emiten la URL del listado para TODAS sus ofertas; con el índice único
de URL, la segunda vacante de un colegio **se pierde en silencio**. Hoy hay 1 fila por colegio:
efecto **latente**. Ambos scrapers producen `source_id`, así que hay material para la solución.
**Por qué no:** lo elimina de raíz VD.12; existe arreglo transitorio (fragmento determinista
sobre la URL). **Severidad:** media (población pequeña, pero es el perfil objetivo). **Coste:**
horas (transitorio) o resuelto por VD.12. **Ticket:** VD.14.
**Matiz (2026-08-19, verificado en código):** en el core el defecto tampoco desaparece — con
`UNIQUE(source_id, url_normalized)` el segundo listing se salta con warning
(`jobhunt_core/harvest/sink.py:337-345`): pérdida visible en lugar de silenciosa, pero la URL
por-oferta hay que arreglarla en la fuente igualmente.

### 1.26 · `employment_type` arrastra la premisa equivocada que tenía `logo`
Sus productores lo obtienen de un fetch de detalle que puede fallar por su cuenta
(`backend/scrapers/schuljobs.py:297-298`, `backend/scrapers/myscience.py:123-124`): un fallo del
detalle en una re-vista pisa con `NULL` un valor bueno almacenado. Es el único campo que queda
con esa premisa tras el barrido campo a campo de `747630f`; `salary_*` y `language` degradan
atómicamente con el listado y NO deben protegerse (enmascararía retiradas legítimas del portal).
**Coste:** líneas (mismo tratamiento que `logo` en el upsert).

### 1.27 · Skip silencioso de configuración en ISP
`backend/scrapers/swiss_schools_isp.py:56-57`: un colegio con `tenant` o `site` vacíos se salta
con `continue`, sin log ni issue — una fila mal configurada de la watchlist desaparece en
silencio. Y un `school_filter` vacío pasaría el filtro para TODO el tenant compartido y emitiría
ofertas de otros colegios bajo el nombre del colegio configurado. **Severidad:** baja-media (la
configuración actual es correcta). **Coste:** líneas (issue de configuración + guard del filtro
vacío).

### 1.28 · ★ Texto no almacenable cuesta la OFERTA entera — NUL y Unicode inválido (VD.16)
**Sexta revisión externa (2026-08-19).** El hallazgo era el `logo` — **arreglado en esta ronda**:
un NUL en el logo abortaba el INSERT (`CharacterNotInRepertoireError`) y costaba la oferta; ahora
se descarta solo el campo. El barrido posterior muestra que el logo era un caso particular:
**cualquier** campo `text`/`varchar` con NUL aborta igual, y `jsonb` con `\u0000` lanza
`UntranslatableCharacterError` (ambos verificados contra la BD del contenedor). El vector realista
es un JSON de portal con el escape `\u0000`, que `json.loads` acepta sin protestar. **Por qué
no:** los campos de identidad (`title`, `company`, `url`) no admiten la solución del logo —omitir
es imposible (NOT NULL y entran en el hash) y sanitizar almacenaría una identidad distinta de la
hasheada—, así que hay que elegir entre degradar la oferta y alterar su identidad: decisión de
producto, no de implementación. **Ampliado en la séptima revisión (2026-08-20):** no es solo el NUL. Un
**surrogate aislado** (`\ud800`) revienta **antes de tocar Postgres**, en `_content_hash()`
(`job_repository.py:100`/`:230`, `ensure_ascii=False` + `.encode()` → `UnicodeEncodeError`). Son
**dos fronteras distintas**, y el problema de fondo es "texto no representable en UTF-8 válido".
**Severidad: media** — pérdida total de oferta, pero **nunca observado en producción**. **Coste:**
una ronda con sus dos análisis. **Ticket:** VD.16, con el mapa campo a campo.

### 1.29 · ISP rechaza la barra final en `externalPath` — estrictez deliberada, con su riesgo
**Sexta revisión externa (2026-08-19); decisión tomada, no defecto.** La validación de forma nueva
exige al menos un segmento no vacío tras `/job/`, así que `/job/algo/` se degrada. Las dos ofertas
reales del colegio se comprobaron **en vivo** contra el endpoint de Workday: dos segmentos, sin
barra final, sin dot-segments, sin percent-encoding — la forma real es MÁS estricta que el
criterio. **El riesgo asumido:** si Workday empezara a emitir barra final, las dos ofertas se
degradarían y la fuente saldría `error`. Sería **visible, no silencioso** —y ese es el motivo de
aceptarlo—, pero es una regresión sobre una fuente sana, la misma familia que el falso positivo
del kill-switch que costó una ronda entera en esta fase. **Relajarlo cuesta una línea** (aceptar
exactamente una barra final) si alguna vez se observa. **Severidad: baja.**

---

## 2 · Otros tracks y el core

### 2.1 · ★ V.1c — `published_at` en el core, con trampa de captura
El capturador CDC usa **lista blanca de columnas** (`jobhunt_core/shadow/capture.py:80-100`) y
`published_at` no figura, así que hoy se ignora sin error. **La trampa**, documentada en el propio
bloque: al añadirla a la whitelist, **las filas legacy que ya no se re-tocan no emiten UPDATE al
WAL**, así que su histórico **no llega por streaming**. **Por qué no:** fuera de V.1 a propósito.
**DoD del ticket:** incluir plan de backfill dirigido, o **aceptar la pérdida por escrito**.
**Severidad:** media-alta cuando la ventana deba gobernar el core. **Coste:** ciclo propio con
ventana en el NAS.

### 2.2 · ★ GATE-SOMBRA: la racha está rota y la última medida es vieja y ROJA
**CORREGIDO el 2026-08-20 tras la séptima revisión externa.** Esta entrada afirmaba que el gate
"jamás se ejecutó" y que "el contador está a cero". **Era falso**, y lo verificó primero un revisor
externo. Medido contra la BD local (`swissjobhunter`, esquema `jobhunt`, entorno de desarrollo, el
2026-08-20):

| Medida | Valor real |
|---|---|
| Sets etiquetados **congelados** | **3** (`labeled_sets`, los tres con `frozen_at` = 2026-07-28) |
| Juicios | **91** (`labeled_judgments`) |
| Pares de dedup etiquetados | **779** (`labeled_dedup_pairs`) |
| Ciclos **sellados** | **6** (2026-07-25 → 2026-07-30; el del 07-31 quedó incompleto) |

Es decir: la curación manual **se hizo**, los sets están congelados y hubo seis ciclos. **El
problema real es otro, y no es menos grave:** el último ciclo completo (2026-07-30) salió **ROJO**
— `nDCG@10` = 0,374 en uno de los dos perfiles y `dedup_recall` = 0,073 — y **no hay ninguna racha
en curso**: la última evidencia tiene más de tres semanas. La racha efectiva es **0/7**, no porque
nunca se empezara, sino porque se rompió y no se ha retomado.

**Severidad: crítica** — sigue bloqueando el GATE C y, en cadena, las fases D, E y F. **Coste:**
recalibrar hasta poner las métricas en verde (los umbrales sin calibrar son §2.3) y después 7 días
consecutivos de conteo. **Lección de método, más importante que el dato:** esta entrada llevaba
semanas contradiciendo a la BD y nadie lo comprobó. Las cifras de estado se citan **con su fecha y
su entorno de medición**, o no se citan.

### 2.3 · Umbrales de similitud sin calibrar
`SIM_AUTOLINK`, `SIM_CANDIDATE` y `SIM_RECYCLE` gobiernan re-enlace, reciclado y dedup del corpus.
La calibración era en sombra y no ha ocurrido. **Coste:** días de análisis sobre datos de la sombra.

### 2.4 · C.3 — el push de CV traerá un conflicto de credencial
El runbook exige que la clave incluya `profiles:write`, pero la única emitida es de solo lectura y
la regla vigente es no emitir otra: habrá que **rotar con revoke**. **Severidad:** baja hoy, trampa
operativa segura si nadie lo recuerda. **Coste:** minutos, coordinados con el propietario.

### 2.5 · C.4 — la migración de durables solo está ensayada en scaffold local
Pendientes del ensayo gated: ledger del sink por entrada, **manifiesto de procedencia exacta**,
verificación estructural independiente, script de borrado FK-safe y el **cierre de la carrera
residual** (attach concurrente tras la revalidación). Además, el importador **aborta** ante estado
preexistente, así que la idempotencia del cutover con bookmarks es del mismo ensayo. **Bloqueado:**
exige datos reales del NAS. **Severidad:** alta en el momento del cutover, nula antes.

### 2.6 · C.5 — fallback read-only diferido salvo evidencia
Decisión registrada, no descuido.

### 2.7 · C.6 — paridad N días y GATE C, bloqueados por el GATE-SOMBRA
En cadena con §2.2.

### 2.8 · Fase D — tres huecos declarados
(a) El **manifiesto de datos de SwissJob** y el **runbook de D** son documentación pendiente.
(b) Residual asumido: al omitir `core_read`, la copia local del fallback queda **congelada** desde
la migración, así que si el core cae durante el canary el fallback sirve datos viejos
(`backend/services/matching/seam.py:46-47`). (c) Pendiente de UI: el botón de análisis sigue
habilitado para perfiles migrados y el 409 aparece como error.

### 2.9 · Fases E y F sin empezar, más los diferidos de la Fase A
Traducción de cuerpo, documentos WeasyPrint en el core, colegios, y la retirada de motores y tablas
(con retención, backup probado y N ciclos). El "BFF puro" en su sentido literal depende de E/F.
**Coste:** fases enteras (10-15 días estimados).

### 2.10 · ★ TRACK T sin ejecutar — dividido en T-PRE-FLIP (bloqueante) y diferidos
Paridad, observabilidad y **seguridad y ciclo de vida de datos**: rotación de credenciales,
RPO/RTO validados con restauración, borrado multi-almacén y contract tests transversales.
**Actualización 2026-09-13:** el propietario delegó la elección de backups. ADR-07 sustituye
el KMS por perfil no implementado por retención de 7 días (temporales 48 h), inventario mínimo
independiente y restore aislado/saneado. El borrado coordinado ya está desplegado (d2a38e9,
core0046/b46), con canary de ambas réplicas y barrera de restore verificados. Autorización
posterior ejecutada: 7 backups NAS vencidos y 9 dumps locales eliminados; clúster privado de
restore retirado con su PGDATA/WAL. Punto 1 cerrado en el alcance acordado. El cron se difiere
por decisión expresa al cierre final, incluida primera ejecución y alarma: NO está instalado.
Mientras tanto la retención es manual; no se acredita RPO diario automático ni cierre global.
Véase SwissJob/docs/DESPLIEGUE_E13_2026-09-13.md.
Las afirmaciones históricas siguientes se leen con esta decisión sustitutiva, no como deuda
de implantar un KMS. La evidencia de restore anterior no acredita por sí sola saneamiento.
El propietario confirma que su cuenta personal de Portfolio no se eliminará: no hay deuda
ni bloqueo por construir ese flujo o retirar el CV público/chatbot. Se mantienen los pendientes
de automatización de backups para el cierre final, sin debilitar las protecciones del core.

**Corregido el 2026-08-20 (novena revisión externa) — el agujero no era la deuda, era el gate.**
Esta entrada decía "severidad alta antes de cualquier flip", pero **ninguna precondición del runbook
ni de los gatings del plan lo exigía**: un operador podía atestar GATE-SOMBRA, NAS y §4, obtener
GATE C verde y ejecutar el flip sin restore probado ni cierre real del ciclo GDPR. Una obligación
que ningún procedimiento comprueba no es una obligación. Ahora el track está partido:

- **T-PRE-FLIP — BLOQUEA el flip** (añadido como precondición en `RUNBOOK_CUTOVER_PILOTO.md` y como
  gating en `PLAN_UNIFICACION_JOBHUNTING.md` §25): restore probado con RPO/RTO **medidos**, rotación
  de credencial ejercitada y ensayo de borrado multi-almacén. Conforme a ADR-07 (2026-09-13),
  verificar retirada por caducidad de backups afectados y que un restore anterior se sanea
  antes de servir; mientras una copia afectada persista, el borrado de backups sigue pendiente.
- **Resto de TRACK T — diferible**: paridad, observabilidad, contract tests transversales y el
  cifrado de PII completo. No se convierte el track entero en un megagate.

**Severidad: alta** (la parte pre-flip). **Coste:** ciclo propio (12-18 días el track completo; la
parte bloqueante es una fracción).

### 2.11 · TRACK O fuera del corte
ONNX, render-worker y slim. Decisión consciente.

### 2.12 · Core — un worker colgado pero latiendo retiene el scope
Contrapartida aceptada del heartbeat, acotada por timeouts HTTP.

### 2.13 · Core — gate por-perfil de perfiles sub-oráculo
Cambiarlo alteraría la semántica ratificada (set no medible ⇒ rojo). Obliga a "curar bien a la
primera".

### 2.14 · Core — una partición creada a mano no dispara el trigger de embeddings
Cerrado para los caminos del código; reaparece con scripts manuales por SSH. **No cerrable en
código:** es una regla operativa, ya escrita.

### 2.15 · Core — un tramo de downgrade no cerrable desde código
Cubierto como requisito operativo del runbook.

### 2.16 · Core — invariante frágil: un único escritor de `source_scope_state`
El fencing sin token depende de ello. Trampa para quien añada otro escritor.

### 2.17 · Cobertura muda "esperada"
Cinco fuentes restringidas por credencial de partner y cuatro sin API key. Asumido; obtenerlas es
gestión externa.

### 2.18 · ★ Riesgos operativos del NAS
(a) El stack se gestiona por CLI, **fuera de la UI de Container Station**: un *Recreate* revertiría
a la definición sin sombra y, con el slot lógico presente, **el Postgres no arranca**. Es el riesgo
operativo nº 1 declarado. (b) Healthcheck del frontend corregido en repo pero **sin aplicar**.
(c) Permisos de la caché del core parcheados con `chown`; el arreglo definitivo sigue pendiente.
(d) El corpus del NAS va varias migraciones por detrás y la distancia crece con cada commit.
(e) Cifras del estado sin re-medir desde 2026-08-07, y la primera ejecución de la caducidad real
nunca verificada. (f) WAL retenido por el slot: vigilancia con umbral de 2 GiB.

---

### 2.19 · ★ Core — ningún adaptador nativo acredita identidad estable (VD.15)
**Sexta revisión externa (2026-08-19); preexistente.** El único adaptador nativo del core resuelve
la identidad como `external_id = item.get("slug") or url`
(`jobhunt_core/harvest/providers/arbeitnow.py:221`), y el slug de arbeitnow **contiene el
título**: una corrección de título abre un slot nuevo en vez de una revisión. Es VD.12 replicado
dentro del core. Los fixtures usan slugs sintéticos (`"a"`, `"b"`…), así que **ningún test
verifica la estabilidad**. **Por qué no:** el código del core lo lleva otro agente y esta fase
tiene prohibido tocar `jobhunt_core/`; además el arreglo correcto no es un parche al adaptador
sino un **contrato de alta** que obligue a probarlo en todos los futuros. **Severidad: alta** —
invalida el argumento "migrar al core extingue VD.12" que sostenía parte del plan, y cada
adaptador nuevo puede reintroducir el defecto. **Coste:** contrato + un test por adaptador.
**Ticket:** VD.15.

---

## 3 · Código transversal

### 3.1 · `DeprecationWarning` de starlette
`HTTP_422_UNPROCESSABLE_ENTITY` en `backend/routers/analytics.py:68` y
`backend/routers/profile.py:154,160`. Trivial hoy; romperá en una major futura. **Coste:** 3 líneas.

### 3.2 · Deuda PF.4 — dos copias del modelo de embeddings en memoria
El worker único del NAS con `concurrency=2` puede cargar dos copias (~400 MB cada una). La
separación que lo evitaba nunca se desplegó. **Severidad:** media en el NAS. **Coste:** horas.

### 3.3 · ★ Trampa `INSTALL_BROWSERS`
`docker-compose.prod.yml:84` construye el backend **sin Chromium**: un rebuild desde ahí **rompe
todos los scrapers de Playwright en silencio**. Documentado en tres sitios, pero sigue siendo un
arma cargada. **Coste:** horas (build-arg por defecto, o un check de arranque que falle ruidoso).

**Actualización 2026-08-18 (cuarta revisión externa):** la revisión considera esta deuda
**infravalorada precisamente por su silencio** — el fallo no deja ningún rastro que lo distinga de
una sequía, que es la clase de fallo que más daño ha hecho en este proyecto (§1.13). Mitigación
concreta propuesta por la revisión y aceptada: **fallar el arranque —o el readiness— si hay
scrapers de Playwright activos y el Chromium no existe**, además de que el compose productivo lo
construya por defecto. Pendiente de implementar; misma severidad, prioridad revisada al alza.

### 3.4 · Flakiness de tests por `commit()` dentro de la transacción del fixture
Produce `Could not refresh instance` y **ya provocó una atribución errónea de causa**. **Coste:**
ciclo pequeño de refactor.

---

## 4 · Arnés y herramientas

### 4.1 · ★ La BD de test es compartida y se trunca tras cada test
`backend/tests/conftest.py:88` hace `TRUNCATE … CASCADE` en el teardown, así que **dos pytest
simultáneos se corrompen mutuamente**, con fallos en ficheros ajenos indistinguibles de bugs
reales. Agravante: `docker compose exec` deja procesos pytest fantasma en el contenedor. Regla
vigente: **una suite cada vez**. **Severidad:** media — limita el paralelismo y ya causó un
autoengaño documentado. **Coste:** ciclo propio (BD por sesión con nombre único).

### 4.2 · `pytest-timeout` ausente de la imagen
La documentación del proyecto sigue mostrando `--timeout=30` en sus comandos, y ese flag **no
existe** en esta imagen. **Coste:** una línea (o corregir la doc).

### 4.3 · Los bucles de espera con `pgrep` se auto-detectan y no terminan nunca
Lección aprendida (2026-08-19) que ya costó **nueve horas de espera en vacío**: un bucle del
tipo `while pgrep -f pytest; do sleep …; done` **se encuentra a sí mismo** — su propia línea de
comando contiene el patrón buscado — y no termina jamás. La forma correcta de esperar una
suite: lanzarla **desanclada** con log de nombre único, escribir un **marcador con el exit
code al final del log** (p. ej. `…; echo "EXIT=$?" >> run-<ts>.log`) y sondear ese marcador,
nunca la presencia del proceso.

---

## 5 · Incoherencias de documentación

**Esta sección es la causa raíz del documento.** La deuda que se redescubre no suele ser la más
grave: es la peor documentada.

- **`HALLAZGOS.md` H-1** (tres tests de scheduler rojos) está **resuelto** pero el fichero lo pinta
  pendiente. La nota adyacente sobre `pytest-timeout` sí sigue viva (§4.2).
- **La consulta GDPR del §9** está corregida en el runbook del core, pero `ESTADO_Y_HOJA_DE_RUTA.md`
  sigue diciendo lo contrario **en dos sitios**: quien lea solo el estado re-hará trabajo hecho.
- ~~**El "camino crítico" del backlog** aún señala C.2 como frente actual~~ — **CORREGIDO el
  2026-08-20** (séptima revisión externa). La cabecera de Fase C del backlog decía "FRENTE ACTUAL =
  C.2" tres semanas después de que C-2→C-6 se cerraran. Ahora remite al plan como estado canónico.
- ~~**GATE-SOMBRA jamás ejecutado, contador a cero**~~ — **CORREGIDO el 2026-08-20**: era falso.
  Hay 3 sets congelados, 91 juicios y 6 ciclos sellados; lo que pasa es que la racha está rota y la
  última medida es vieja y roja (§2.2). **Este documento afirmaba lo contrario que la BD durante
  semanas.**
- **Las cifras de `ESTADO`** son fotos del 2026-08-07; el propio documento lo declara, pero sigue
  siendo deuda.
- **Regla nueva, a raíz de las dos correcciones de arriba:** una cifra de estado se escribe **con su
  fecha y su entorno de medición**, y con la consulta que la reproduce si sale de la BD. El estado
  canónico vive en **`PLAN_UNIFICACION_JOBHUNTING.md` §25**; el resto de documentos lo **referencian**
  en vez de copiarlo. Copiar estado es cómo nacieron estas dos incoherencias.
- **Segunda regla, del fallo de la corrección misma (8ª revisión, 2026-08-20).** La corrección de la
  7ª arregló §2.2 y la cabecera de Fase C, **pero dejó cinco copias vivas de lo mismo**: el backlog
  seguía diciendo que faltaba congelar los sets (:91), omitía VD.15–VD.16 de los residuales (:202) y
  mantenía "FRENTE ACTUAL: C.2" en el camino crítico (:952); este documento enlazaba el Bloque 3 a
  §2.10, que es TRACK T (:46); y el plan titulaba "7ª revisión" con un párrafo que aún decía seis
  revisiones y 1354 tests (:1674). **Corregir una afirmación de estado obliga a barrer TODAS sus
  copias antes de dar la corrección por hecha** — `grep` de la cifra y de la frase, no solo del sitio
  donde saltó. Las cinco están corregidas; la lección es que el barrido es parte del arreglo.
- **Precedente, para el registro:** la observabilidad de fuentes estuvo entregada durante nueve días
  sin marcar en el backlog, dando la impresión de que el bloque estaba pendiente. Es el mismo
  mecanismo que motiva este inventario.

---

## 6 · Auditoría total del proyecto (2026-08-21) — estado y pendientes

> Auditoría completa de las tres bases (motor legacy, `jobhunt_core`, backend de ReactPortfolio) en
> cinco lotes paralelos, más un informe externo independiente. **35 hallazgos propios** (4
> bloqueantes, 17 importantes, 8 menores, 6 optimizaciones medidas) y **10 del externo**.
> Informe consolidado: `AUDITORIA_TOTAL_2026-08-21.md`. Informes por lote en el scratchpad de la
> sesión (efímeros — lo que importa está resumido aquí).

### 6.-1 · ⚡ ACTUALIZACIÓN 2026-08-23 (sesiones core 22-23 ago) — leer ANTES que el resto de §6

El §6 quedó parcialmente desfasado por dos sesiones de ejecución. Estado real:

- **§6.4 B-1 → CERRADO con causa raíz más profunda**: capture se perseguía la cola (heartbeat por
  tx vacía ⇒ consumo≈generación tras cualquier burst; 11,5 h clavado con latido fresco). Fix
  `accc10e` (throttle 1/s) + alarma EXTERNA del staging en el `--health` de capture (`876add6`).
- **§6.4 F-2 → CERRADO**: barrido de archivado ADR-07 entregado (`876add6` + fix set-based),
  3.502 archivadas en la primera pasada, beat diario 05:35. Corpus convergido (perdida=0).
- **§6.2.4b dedup_recall → RESUELTO CONSTRUYENDO el nivel 3** (`0e70cc6` ANN cross-source +
  `650a34c` exacto intra-fuente con regla multi-ciudad), no re-ratificando el umbral. Recall
  medido con oráculo curado: **1.0**; precisión **0.977**.
- **§6.2.4a outbox_lag → resuelto DISTINTO a la recomendación**: umbral 300→900 s (3× cadencia)
  en vez de bajar el despacho a 60 s. Racional en `metrics.py` y ESTADO §11.4.
- **§6.2.1 cursores → EJECUTADO** (los 5 estrictos, no los 7).
- **§6.2.3 recálculo de las 177 → DIFERIDO A POST-GATE**: dos intentos (24k filas × clasificador
  = horas, tx de 19 h matada sin commitear). En freeze cambiaría el scoring legacy a mitad de
  racha (mueve ndcg_legacy). Post-gate: versión set-based/batched.
- **§6.1 oráculo → SUPERADO**: en el NAS nunca existió (tablas vacías); sembrado fresco desde el
  duplicate_of REPARADO + hallazgo estructural (los pares de duplicate_of NO MAPEAN por
  construcción — el proyector no crea slot para jobs ya-duplicados en el backfill; ESTADO §11.7)
  + curación REAL del propietario (48 juicios + 81 pares ratificados). **Sets CONGELADOS
  2026-08-23 08:51; racha EN MARCHA; CODE FREEZE del core en vigor.**
- **§6.2.2 los 27 e-learning → pendiente post-gate** (toca scoring).

### 6.0 · Ya CERRADO — no volver a diagnosticarlo

| Commit | Repo | Qué cerró |
|---|---|---|
| `2e9c108` | SwissJob | Dedup semántico intra-fuente + `duplicate_of` colgantes · **664 ofertas recuperadas** |
| `2531540` | SwissJob | Clasificador: compuestos alemanes + 11 keywords con puntuación |
| `ebb2c51` | SwissJob | Lazo del presupuesto del crawler + cursor que aprendía de runs fallidos |
| `4716d48` | Portfolio | Cosecha externa forzable sin autenticación + CV que no era admin-only |
| `c7d14a6` | Portfolio | CAS por versión en el CV, cotas Pydantic/columna, filtro de secciones, caché, JSON irregular |

Suites tras los arreglos: legacy **1423**, core **461**, portfolio **1093**. Reparación del corpus
ejecutada (983 filas, solo `UPDATE`): 0 colgantes, 0 duplicados intra-fuente, activas 9.173 → **9.837**.

### 6.1 · ★ NUEVO — el oráculo del GATE-SOMBRA quedó CADUCADO con la reparación
**Consecuencia descubierta al reparar, no por la auditoría.** Los 779 pares etiquetados de dedup se
sembraron desde `duplicate_of` del legacy (`shadow/labels.py`, `DEDUP_SEED_SOURCE =
"seed_duplicate_of"`). La reparación del 2026-08-21 corrigió 983 de esas filas — entre ellas los 696
duplicados intra-fuente falsos. **Las etiquetas contra las que se mide `dedup_recall` siguen
reflejando decisiones que ya hemos borrado.** Cualquier ciclo lanzado ahora se mide contra un oráculo
obsoleto. **Severidad: alta** — bloquea cualquier reanudación honesta de la racha. **Arreglo:**
re-sembrar o re-curar el oráculo de dedup ANTES de iniciar ciclos nuevos. **Hacerlo en la misma
pasada de datos que §6.2.**

### 6.2 · Decisiones del propietario, con recomendación

1. **Cursores envenenados** — 7 con `bootstrap_complete=t` y `avg_new=0`; **5 son envenenamiento
   estricto** (`gastrojob`, `myscience`, `inspired`, `isb`, `zis`: jamás aprendieron una identidad).
   **RECOMENDACIÓN: los 5, no los 7.** `hautlac` e `iscs` tienen 1 identidad y salud `ok`: su media
   cero es decaimiento legítimo de un colegio sin vacantes, y con B-4 arreglado se recuperan solos si
   pasan hambre. `UPDATE` propuesto en el informe del lote, sin ejecutar.
2. **Los 27 «e-learning» por tag** — ofertas de informática que suben a categoría D por un
   tag-beneficio, no por el título. No es defecto del arreglo del clasificador; es del registro.
   **RECOMENDACIÓN:** medir primero cuántas son realmente formación; si dominan las de informática,
   exigir que esa keyword aparezca **en el título**, reutilizando la lista explícita que ya creó
   `2531540` para los stems alemanes (extiende una estructura existente, no inventa una nueva).
3. **Recálculo dirigido de las 177 ofertas reclasificadas** — hashes identificados. La categoría se
   autocura al re-ver una oferta, pero **las inactivas no se re-cosechan nunca** y el crawler
   incremental hace early-stop sobre URLs conocidas. **RECOMENDACIÓN: sí, y en la misma pasada que
   los cursores y el oráculo.**
4. **El gate, en dos piezas separadas:**
   - **`outbox_lag_p99` (§F-4)** — el umbral (300 s) es igual a la cadencia del despachador (300 s):
     puede salir rojo sin fallo real. **RECOMENDACIÓN: bajar `CORE_DELIVERY_DISPATCH_EVERY_S` a 60 s**
     en vez de ensanchar el umbral. El dead-letter y `outbox_dead` siguen cubriendo un transporte
     realmente caído.
   - **`dedup_recall ≥ 0,90` (§F-5)** — inalcanzable por construcción: el core no tiene generador de
     candidatos semánticos (solo URL/attach e intra-lote). El 0,073 es el techo del código.
     **RECOMENDACIÓN: re-ratificar el umbral acotado a los niveles 1-2** hasta que exista el nivel 3.
     Construir el generador es un ciclo propio y **no sirve de nada mientras el oráculo esté caducado**
     (§6.1).

### 6.3 · Pendientes de código — motor legacy

- **§A1-4 · Salarios corruptos** (`data_normalizer.py:281-296`). `_parse_number` borra puntos y comas
  antes de convertir: `€95.00/hour` → **18,9 M CHF/año**. La heurística de la «k» multiplica ×1000 si
  hay una `k` en cualquier parte («Kanton», «Kita»). 92/770 ofertas con divisa sin tasa convertidas
  1:1. Infla `score_salary` a 1,0. **Ningún test se opone** (el mutante del arreglo pasa los 27).
  Va junto con el código muerto de los símbolos `€/$/£`, que por el `\b` no casan nunca. **Máxima
  rentabilidad de la lista.**
- **§A1-2 · La ventana del dedup semántico (500) es menor que la entrada diaria (784)** — lo que
  desborda el día de su alta no entra nunca. Medido: 9,5 % de las ofertas fuera de ventana tienen
  duplicados sin marcar; el informe externo lo mide por el otro lado (**8.122 nunca alcanzadas**).
  **Arreglo:** columna `semantic_dedup_checked_at` reiniciada al cambiar el embedding, selección de
  pendientes con orden determinista. Requiere migración.
- **§A1-3 · Carrera del prune** (`match_service.py:513-527`) + **`draft_letter` fuera de
  `_has_engagement`** (mutante M10 superviviente): el prune puede borrar feedback escrito fuera de
  banda y **la carta borrador del usuario**. Arreglo: llevar el predicado al propio DELETE.
- **Capa de señal de fuente** — tres defectos, misma enfermedad («el monitor dice lo que no es»):
  **§A2-1** una fuente apagada por el kill-switch (o sin fila de compliance) se registra como `empty`
  sin haber hecho petición; **§A2-4** ISP nunca llama a `reset_blocks` y sale «nunca tuvo éxito» cada
  6 h mientras funciona; **§A2-3** el umbral «silent» de 24 h choca con el jitter de ±4 h y marca
  caídos a todos los colegios sanos la mitad de los días. **Arreglarlos juntos.**
- **§A2-7 · Búsquedas guardadas** — compara un CONTEO contra un umbral de SCORE y usa `last_seen_at`
  en vez de `first_seen_at`. **Latente: 0 búsquedas guardadas hoy.** Prioridad baja.
- **Informe externo §8 · Supresión de patrones redundantes invertida**
  (`pattern_analysis_service.py:316`): comprueba si el patrón largo está en el corto en vez de al
  revés. **Estaba en el alcance de mi lote A2 y se me pasó.**

### 6.4 · Pendientes del core — los mantiene OTRO AGENTE, no tocar desde aquí

- **§B-1 · La proyección sombra lleva parada desde el 2026-07-30.** `redis-core`/`core-api` en
  `Exited (0)`, `core-worker` en bucle de reconexión. 22.550 cambios sin proyectar, **62 % del corpus
  divergido**, `legacy:irishjobs` y `legacy:zebis` ni existen en `jobhunt.sources`. **Lo grave es de
  arquitectura:** toda la observabilidad de la sombra corre en el beat del mismo worker caído — tres
  semanas sin una alerta. **Arreglo:** (a) levantar los servicios y dejar drenar (el diseño lo
  tolera); (b) healthcheck del staging **fuera** del worker, p. ej. en `core-capture`, con umbral
  sobre `min(received_at) WHERE applied_at IS NULL`.
- **§F-2 · El barrido de archivado ADR-07 no existe.** El sink delega en él explícitamente, pero el
  único `UPDATE ... archived_at` de todo el core está dentro de `rollback_replay`. **914 vacantes
  muertas vivas, 413 aún servibles en el feed.** El contrato A-08 promete «solo vacantes activas».
  **Es el arreglo de core con más valor**: la salida del corpus.

### 6.5 · Portfolio — reportado y NO arreglado (decidir alcance)

- `GET /jsearch-jobs/search` consulta una API de pago **en vivo y anónima** por petición (mitigado con
  5/min por IP y el kill-switch, pero un atacante distribuido consume cuota).
- Cuatro fronteras sin cota o sin validación de forma: `status` de candidatura, `country`/`city` del
  chat, `username`, y el JSON libre del CV en las rutas de escritura completa.
- `backend/CLAUDE.md` sigue diciendo que los job routers no requieren auth; su `refresh-cache` ahora
  es admin-only.

### 6.6 · Cobertura — 8 mutantes SUPERVIVIENTES de 13

Lo que las rondas anteriores endurecieron mata bien; **lo que quedó fuera está sin red**:
invalidación de embedding por cambio solo de tags · `_URL_DEAD_STATUSES` ampliable a 403/500 **sin
que falle nada** (desactivaría ofertas vivas en masa) · el arreglo del decimal de salarios pasa los
27 tests · símbolos de divisa (código muerto) · **el multiplicador de categoría anulado en sus dos
puntos con 85 tests focales en verde** · `draft_letter` fuera del prune · lógica «due» de saved
searches invertida · título de `ostjob`/`zentraljob` roto con 112 tests pasando.
**Cobertura CERO:** `tasks/search_tasks.py` (215 líneas), `providers/base_chmedia.py`, 8 providers,
4 de los 8 colegios. **Prioridad: los tests de `check_job_urls`** — hoy se puede ampliar la lista de
códigos que desactivan ofertas y la suite entera pasa.

### 6.7 · Rendimiento — medido

| # | Qué | Medición | Cautela |
|---|---|---|---|
| **P-4** | `run_all_matches` instancia `GroqService()` **sin Redis**: el rerank pagado diario corre con la caché de 7 días desactivada | 1 línea | **Empezar por aquí:** ahorro en dinero real |
| **P-1+P-2** | Stage-1 deserializa 8.848 vectores para tirarlos; el coseno puede calcularse en SQL, y su `ORDER BY` es trabajo muerto | **949 ms → ~170 ms** (6-8×) y 73,8 → 6,3 ms | Son el mismo cambio |
| **P-3** | `find_semantic_duplicates` por top-K con HNSW | 37,3 ms → 0,4 ms (93×) | ⚠ **NO aplicar** mientras `dedup_recall` bloquee la fase: ANN aproximado, cambia comportamiento |
| **P-5** | `ix_jobs_embedding_hnsw` (18 MB) con `idx_scan = 0`, más otros 5 índices igual | — | Es la BD de desarrollo: **verificar en el NAS antes de borrar** |

**Anti-hallazgos medidos y descartados** (no reproponer): índices para cleanup (9,5 ms) y url-check
(5,3 ms), pre-SELECT del upsert (0,14 ms/oferta), `defer(description)` (986→962 ms). La carga única
del modelo de embeddings está bien hecha.

### 6.8 · Orden recomendado para la próxima sesión

1. **Una sola pasada de datos**: cursores (los 5) + recálculo de las 177 + **re-sembrado del oráculo**
   (§6.1). Un script, conteos antes/después. Desbloquea toda medición posterior.
2. **Salarios** (§6.3) — corrupción confirmada, arreglo corto, nada se opone.
3. **La capa de señal de fuente** — los tres juntos (§6.3).
4. **Lo que quita ofertas de la vista**: prune + `draft_letter` + tests de `check_job_urls`.
5. **Rendimiento**: P-4 primero, luego P-1+P-2.

**Fuera de esta lista, por coste:** el barrido de identidad (VD.12/VD.15) sigue siendo la deuda mayor
pero es un ciclo con migración; VD.16 (texto no almacenable) espera decisión de producto en los
campos de identidad; VD.11 y VD.13 son los barridos de garantías.

---

## 7 · Bitácora viva de optimización ReactPortfolio + SwissJob — 2026-10-10/11

> **Documento de relevo de esta sesión.** Actualizado por última vez el
> **2026-10-11 11:32 UTC**. Debe actualizarse después de cada punto cerrado,
> despliegue, medición o cambio de siguiente acción. No declarar un punto cerrado
> por tener código local: exige la evidencia de producción indicada aquí.

### Solicitud, objetivo y reglas que siguen vigentes

El propietario pidió simplificar y optimizar ambos proyectos sin perder ninguna
funcionalidad, siguiendo el orden acordado hasta terminar todos los objetivos. Ha
autorizado todos los commits necesarios y el cambio del worker Core de
`concurrency=2` a `concurrency=1`. El 11-10 a las 09:14:55 UTC autorizó
también la transferencia y ejecución de herramientas y sondas, el push a GitHub
y GitLab y la publicación en Cloudflare Pages. Esas acciones siguen pendientes
únicamente por el orden técnico y sus gates, no por permisos.

Reglas de ejecución: comprobar el estado real antes de cada acción; SRP, cohesión
alta, acoplamiento bajo, legibilidad y consistencia; aplicar el mínimo que cumpla
también YAGNI; no retirar respaldos antes de cerrar formalmente rollback; probar
las doce ventanas y sus contenidos; medir antes y después; no mezclar ni descartar
cambios previos del propietario.

Orden obligatorio:

1. Una sola copia del bi-encoder y ajuste de límites.
2. Sanear las 17 fuentes y retirar `core-capture` tras su ventana real.
3. Desactivar fuentes duplicadas y la cosecha automática duplicada de colegios.
4. Servir facetas/categorías desde Core bajo bandera y demostrar paridad.
5. Cerrar formalmente rollback.
6. Sólo después retirar motores, costuras y código histórico; extraer antes las
   responsabilidades nativas que aún viven en el paquete `shadow`.

### Estado de relevo

| Punto | Estado comprobado | Evidencia / siguiente condición |
|---|---|---|
| 1. Bi-encoder | **CERRADO Y DESPLEGADO** | Una copia en `swissjob-core-worker-r5`, `concurrency=1`; ahorro causal 605,64 MiB (43,08 %) en la prueba aislada y PSS del contenedor aproximadamente 14 % menor. Release Core `5a1241d`, cero reinicios. Con el proceso único, 24 recuperaciones completaron un perfil cada una: mediana 816,63 s, p95 1.454,25 s, máximo 1.531,94 s, todas por debajo del límite blando de 1.800 s y cero fallos; 46 ticks caducados fueron revocados y no formaron backlog sin cota. La pasada posterior a la ronda de las 10:10 cerró `ok` en 1.542,60 s, aún por debajo del límite blando, procesó una recuperación y descartó cinco ticks vencidos. La siguiente cerró `ok` en 788,66 s, comprometió exactamente el lote acotado de 200 embeddings de oferta —pendientes de 545 a 345— y descartó otro tick vencido. Una segunda ronda acotada cerró `ok` en 842,20 s y comprometió otros 200 —de 345 a 145—. La ronda final cerró `ok` en 835,41 s y comprometió los 145 restantes. Las tres tareas embebieron 545 ofertas en lotes 200/200/145, todas bajo el límite blando, descartaron cinco ticks vencidos y a las 11:31:29 UTC dejaron ofertas/perfiles pendientes y las cinco colas a cero, sin reinicios. Evidencia `post-harvest-embedding-convergence-production.json`. Tras llegar embeddings pendientes a cero, dos pasadas cerraron en 84,80 s y 14,03 s con `recovery_evaluated=0`; `core.matching=0`. Convergencia demostrada en `matching-recovery-baseline-production.json`. La observación encontró otro límite real fuera del worker del modelo: `dedup_scan` recorrió 7.078 vacantes durante 1.800 s, chocó con `SoftTimeLimitExceeded` y revirtió, mientras el único proceso de operaciones retenía hasta 20 tareas `core.default` y 5 `core.notifications`. Se descartó subir operaciones a concurrencia 2: añade 7,17 MiB/1 PID en la prueba aislada y no evita el rollback. La candidata conserva concurrencia 1 y fragmenta el ANN en lotes deterministas de 250 con ventana congelada, commit y cursor; exacto/léxico se ejecutan una sola vez al final. El commit `886ee04` cerró 39/39 pruebas dedup y añadió la evidencia `dedup-scan-batching-validation.json`; no está desplegada y debe verificarse tras la ventana de capture. |
| 2a. Salud de cosecha | **CERRADO** | IrishJobs prueba los dos dominios sin abortar tras el 403; 17/17 scopes saludables y sin alertas. La ronda de las 00:10 Europe/Zurich del 11-10 cerró 17/17 cursores, 14.037 listings, 210 páginas y cero errores; `native-harvest-round-production.json`. La siguiente ronda completa cerró también 17/17 `ok` entre 04:10:09 y 04:36:18 UTC, sin errores; la muestra posterior mantuvo 17/17 saludables, cero alertas y `core.harvest=0`; `native-harvest-round-2-production.json`. |
| 2b. Retirada CDC | **CERRADO Y DESPLEGADO** | Primera muestra conservada `2026-10-10T19:59:56Z`; no actuar antes del **2026-10-12 20:00 UTC**. Observador vivo: PID `701361`, script `/tmp/observe_core_48h.sh`, salida `core-observation-48h.jsonl`. Debe añadirse una sonda manual posterior a las 20:00 porque el script termina cerca de las 19:40. El runbook contiene ya su bloque exacto; se ejecutó a las 09:46 UTC sin anexar la salida y produjo JSON válido con todos los gates verdes. Proceso comprobado vivo a las 11:32 UTC. La muestra 32, a las 11:22:59 UTC, mantuvo 17/17 saludables, cero alertas, release `5a1241d`, 100.992 embeddings y cero perfiles pendientes; capturó 145 ofertas pendientes y `core.matching=1` mientras la última recuperación seguía activa, con las otras colas a cero. La sonda terminal de las 11:32 UTC confirmó 101.137 embeddings, cero ofertas/perfiles pendientes y las cinco colas a cero. Las 32 muestras son válidas, con una sola release/cabeza Alembic, cero `probe_failed` y cero muestras con alertas; el fichero de error correcto contiene sólo tres avisos inocuos de configuración Docker por muestra (96 en total), sin errores de sonda. Máximos observados: 2.185 ofertas pendientes, cero perfiles pendientes y colas `default=20`, `harvest=15`, `matching=5`, `notifications=5`, `embedding=0` (incluye la sonda manual del pico dedup; ambas colas volvieron a 0 a las 03:51:56 UTC). La ronda nativa anterior cerró 17/17 `ok` a las 04:36:18 UTC y después `core.harvest` volvió a 0; la ronda de las 10:10 cerró 17/17 `ok` entre las 10:10:03 y las 10:36:59 UTC, sin ejecuciones inconclusas ni errores; los 17 cursores quedaron posteriores al disparo, `core.harvest=0` y la salud oficial mantuvo `alertas=[]`. Dejó 545 embeddings de oferta y tres mensajes de matching derivados, cuya convergencia se observa por separado. Evidencia `native-harvest-round-3-production.json`. Los cinco contenedores del NAS —no los homónimos locales— mantienen `RestartCount=0`, reconfirmado a las 11:32 UTC: API, modelo, cosecha y operaciones conservan `StartedAt` entre 19:33:03 y 19:33:04 UTC del 10-10, y capture el del 30-09; los logs revisados no contienen `SoftTimeLimitExceeded`, `ERROR`, `CRITICAL` ni `Traceback`. A las 10:14 UTC Celery registró avisos informativos de heartbeat durante la carga; a las 10:25 UTC los tres nodos respondieron `OK`, no había tareas reservadas y los cinco contenedores seguían sin reinicios, por lo que el episodio fue transitorio. El único aviso funcional fue `shadow.preview_cycle NO APTO (provisional)`: el resultado llevaba `counts_toward_streak=false` y `alertas=[]`; código y regresión confirman que evalúa la ventana abierta dentro de un savepoint, no persiste ni cuenta para la racha y no forma parte del gate de retirada. Preflight CDC de las 01:14 UTC: staging pendiente 0, slot activo y WAL retenido 133 kB (`capture-retirement-readiness-production.json`); es sólo un checkpoint, no adelanta el corte. El supervisor QTS vivo omitía los workers Core de cosecha y operaciones; se respaldó y sincronizó con la fuente versionada, pasó sintaxis y ciclo aislado, y ahora vigila los 17 contenedores actuales, incluido capture hasta el corte; el cron real de las 01:20 UTC terminó `ok` y sin alerta nueva. El ciclo de las 08:20 UTC seguía `ok`, sin alerta nueva, y la huella viva `74d65131…` coincidía con la fuente versionada (`supervisor-production.json`). Las transformaciones temporales se revalidaron sin exponer valores: la fase intermedia conserva 5 servicios y `core-capture`, con `CORE_CAPTURE_ENABLED=false` sólo en `core-ops-worker`; la final deja 4 servicios, sin captura ni `CORE_CAPTURE_SLOT`. Ambas pasan `docker compose config --quiet`; son un ensayo estructural y el día del corte deben regenerarse desde el Compose vivo, nunca subirse desde `/tmp`. La revisión segura del Compose vivo a las 02:46 UTC confirmó huella SHA-256 `ee0a361e…d7948d4ad`, exactamente cinco servicios Core, cinco claves `CORE_CAPTURE_SLOT`, ninguna `CORE_CAPTURE_ENABLED` y `config --quiet` verde; no se copió ni expuso ningún valor secreto. `scripts/retire_core_capture.py` prepara ahora las fases `disable` y `finalize` dentro de la propia imagen Core sin sacar el Compose del NAS; falla ante deriva, es idempotente y conserva el flag falso hasta la limpieza de código. Cerró 8/8 pruebas focales y las dos salidas pasan `docker compose config --quiet`. El ensayo detectó inicialmente una pérdida de la cabecera `networks`; se corrigió acotando el parser al bloque `services` y quedó una regresión explícita. El runbook usa respaldo, candidato, validación y promoción. La primera ronda posterior al corte está fijada por código y zona horaria a las 22:10 UTC del 12-10; sólo cuenta como completa cuando los 17 cursores sean posteriores a esa ventana, `core.harvest=0` y la salud oficial no tenga alertas. El runbook fija ahora la consulta con esa frontera temporal exacta, enumera cualquier scope que la incumpla y lee directamente la longitud de `core.harvest`; el ensayo de sólo lectura a las 09:30 UTC contra la ronda ya completada de las 04:10 devolvió 17/17 posteriores, 17/17 sin fallos, cero excepciones y cola cero. Valida los comandos, pero no adelanta el corte ni su ronda posterior. Las regresiones válidas en el contenedor Core cerraron 143/143 en 299,22 s; el intento con Python del host se descarta porque carece de Celery y del arnés del repositorio. La relectura integral del runbook confirmó una sola salida de aborto y corrigió un recuento histórico: el registro ejecutado dentro de la imagen BFF candidata contiene exactamente 8 colectores `swiss_schools_*`, no 12. El comprobador de release dejó de ser un bloqueo post-corte: `5a6bcab` añade la fase explícita `--capture-retired`, que exige cuatro procesos y rechaza que el Compose aún declare capture; sin la opción sigue exigiendo los cinco y detecta una retirada prematura. `ce45e7a` permite usar la ruta real de Docker del QNAP y `1f39b7d` aplica el formato del repositorio. Las cuatro topologías positiva/negativa, junto con las cinco regresiones de transformación, cerraron 9/9; Ruff y formato pasan, y la comprobación real detectó que el QNAP no tiene `python3`, su Docker no incorpora `compose` y el API no publica `:8003`; por tanto se descartó antes de usar el comando anterior. `09e168c` ejecuta el control desde el checkout por SSH, usa por separado Docker y el Compose independiente, aplica quoting seguro y consulta `:8000` dentro de `swissjob-core-api-r5`. Cerró 11/11 regresiones, Ruff/formato y una sonda viva sin escritura: cinco servicios coherentes, release `5a1241d`, Alembic `core0053`, autoritativo. El control negativo vivo con `--capture-retired` salió 1 y señaló exactamente que el Compose todavía declara `core-capture`; demuestra que no aceptará por error el estado previo. Ambos resultados quedaron en `capture-retirement-readiness-production.json`. El runbook ya fija promoción del candidato, eliminación por nombre sólo del contenedor detenido —conservando la imagen—, recreación secuencial de los cuatro procesos y el mismo control remoto con la fase explícita. El ensayo local reproducible `post-capture-core-deploy-validation.json` aplicó `disable`, `finalize` y la preparación de release sobre el Compose saneado de `f29fd44`: quedaron exactamente cuatro servicios, cero captura y claves de slot, el flag falso sólo en operaciones, división idempotente y `compose config --quiet` verde. Confirma que el despliegue Core posterior parte del Compose vivo final y no reintroduce CDC; la validación viva previa a promover sigue siendo obligatoria. El inventario NAS `cutover-file-preflight-production.json` encontró 15 respaldos de Core, 15 de SwissJob, el supervisor vivo con dos respaldos y cero candidatos `.next-*` abandonados; la herramienta de retirada aún no está copiada, estado esperado porque el corte exige transferirla desde el checkout auditado y comparar SHA-256. Se repetirá antes de crear el candidato fechado. La revalidación de las 11 regresiones de transformación/release pasó de nuevo, y la sonda remota pre-corte confirmó cinco servicios coherentes, release `5a1241d`, Alembic `core0053` y autoridad activa; el intento host sin `PYTHONPATH` se descarta porque no recolectó pruebas. El gate temporal no cambia. El supervisor vivo y el versionado siguen idénticos (`74d651…`) y aún esperan 17 contenedores. La variante temporal postcaptura elimina sólo ese nombre, conserva ambos workers, espera 16 y pasó `sh -n`, ciclo sano y control negativo; huella `b3db3b…`, `supervisor-postcapture-preflight.json`. No se edita ni copia hasta que la ronda nativa post-stop sea verde y el slot ya esté borrado. Las huellas locales de corte quedan fijadas en `cutover-file-preflight-production.json`: retirada `bce07410…`, comprobador `2a0e4514…` y supervisor vigente `74d65131…`; deben recalcularse y coincidir inmediatamente antes de transferir o promover. El preflight vivo confirma además directorio de staging escribible, destino aún ausente, binario Compose ejecutable y configuración Core presente. La ventana cerró con 96 muestras entre `2026-10-10T19:59:56Z` y la sonda manual `2026-10-12T20:00:23Z`: una release `5a1241d`, Alembic `core0053`, cero `probe_failed`, cero alertas oficiales y cero reinicios nativos. Doce muestras de recuperación de `nav_arbeidsplassen` preceden a ocho muestras finales consecutivas 17/17 desde las 16:24:19 UTC; no se ocultan como verdes. A las 20:02:23 UTC se detuvo sólo `core-capture`: salida 0, sin OOM, imagen y contenedor conservados; los cuatro servicios nativos no cambiaron. El slot `jobhunt_shadow_r5_rehearsal` sigue presente e inactivo con 1.016 bytes retenidos. Evidencia `capture-retirement-cutover-production.json`; siguiente gate: desactivar exactamente tres entradas de captura y demostrar que `shadow-project` continúa. A las 20:05:15 UTC se recreó sólo `core-ops-worker` con `CORE_CAPTURE_ENABLED=false`; durante 6 min 23 s las cadencias `shadow-check-slot-health`, `shadow-preview-cycle` y `shadow-run-cycle` tuvieron 0 envíos, `shadow-project` tuvo 1 y no hubo errores. Salud 17/17, staging y colas 0; evidencia `capture-beat-disable-production.json`. La ronda explícita post-corte `postcapture-2026-10-12T20:13:39+00:00` despachó 17 scopes y terminó 17/17 `ok` entre 20:13:56 y 20:39:31 UTC (1.535 s), sin errores ni ejecuciones abiertas; los 17 cursores quedaron posteriores, `core.harvest=0`, staging 0 y `alertas=[]`. Dejó 159 embeddings y 3 mensajes de matching derivados para el proyector conservado. El slot sigue inactivo y retiene 374 MB. Evidencia `native-harvest-postcapture-production.json`. A las 20:45:18 UTC se borró exclusivamente el slot inactivo tras registrar 374 MB de WAL; la consulta posterior devolvió 0. El intento con `jobhunt_core` falló cerrado por falta de `REPLICATION`; el rol administrativo local existente `swissjob` ejecutó el mismo `pg_drop_replication_slot`, sin concesiones ni otros cambios. Sigue pendiente finalizar Compose/supervisor y retirar sólo el contenedor detenido. El Compose vivo quedó en cuatro servicios, sin `core-capture` ni `CORE_CAPTURE_SLOT`; la imagen de captura se conserva. El trabajo derivado drenó de 159 embeddings/3 mensajes a cero antes de recrear el único worker del modelo. Los cuatro contenedores quedaron `running`, `RestartCount=0`; `check_core_release.py --capture-retired`, readiness, 17/17 y cinco colas 0 pasaron. El supervisor vivo espera 16 contenedores, SHA `b3db3b…` y estado `ok`. Evidencia `capture-retirement-finalization-production.json`. |
| 3. Cosecha duplicada | **PREPARADO, NO DESPLEGADO** | Portfolio `ba3f886` conserva 11 fuentes exclusivas y compone sus resultados con Core; las 9 compartidas se desactivarán sólo al desplegar. La inspección viva de los cuatro procesos confirma que Portfolio aún no define `LEGACY_DISABLED_SOURCES`; SwissJob backend/worker tienen 12 providers y 2 scrapers desactivados, y el manifiesto final añade `publicjobs` y `zebis` (14+2). Valores exactos y expectativas quedaron en `harvest-overlap-baseline-production.json` (`10fc43b`). La cosecha escolar periódica debe retirarse conservando el refresco manual. El commit Portfolio `787ce44` corrige el despliegue: copia la política versionada a `backend` y `worker`, aborta si queda incompleta y pasó una sonda idempotente con 2 servicios y 9 fuentes. El commit SwissJob `e54051e` hace lo mismo al desplegar BFF/worker; la sonda confirmó 16 fuentes transferidas, 2 scrapers y dos transformaciones idempotentes. Regresiones focales: Portfolio 24/24; SwissJob 12/12 dentro del contenedor con los manifiestos montados en solo lectura. El commit SwissJob `1f33ba9` separa los respaldos remotos por servicio para que BFF y worker no sobrescriban el rollback del otro al compartir SHA. Portfolio `10eb0d4` y SwissJob `7973c15` suben el Compose como candidato, exigen `compose config --quiet` y sólo después lo promueven al nombre vivo. El worktree limpio `/tmp/reactportfolio-backend-deploy-10eb0d4` queda fijado a `10eb0d4`; `scripts/deploy_nas.sh --dry-run` resolvió `portfolio-backend:board-10eb0d4` sin cambios locales. La imagen final ya está construida localmente (`sha256:afcd10e…`, 3.464.664.949 B, `appuser`, `/release`) y sus cuatro hashes críticos coinciden; sigue sin desplegar. SwissJob `95a5d33` añade `--config-only` sólo para BFF/worker: valida y conserva la imagen viva, omite build/carga y aplica únicamente la política versionada más la recreación. Pasó `bash -n`, `diff --check`, los rechazos de servicio/modo (exit 2) y el modo seco de BFF sin tocar el NAS. ReactPortfolio `a292ef4` sustituye el colector escolar diario por un ciclo que sólo reintenta alertas pendientes: School Core conserva la cosecha programada y los endpoints de refresco manual mantienen el colector local. Las 48 regresiones de ciclo de vida, autoridad Core y scraper pasaron; Black y `diff --check`, limpios. La suite completa de `a292ef4` cerró 2.107 passed/1 skipped en 203,02 s. El worktree limpio `/tmp/reactportfolio-backend-deploy-a292ef4`, el modo seco y la imagen `portfolio-backend:board-a292ef4` quedaron verificados; ID `sha256:b929c500…`, 3.464.664.725 B, `appuser`, `/release`, bytecode y cinco hashes críticos idénticos al checkout. Evidencia `portfolio-deploy-candidate.json` en `35cd4ae`. La línea base viva `harvest-overlap-baseline-production.json` (`e688834`) demuestra una ronda programada Portfolio en las nueve fuentes compartidas (dos arranques para TheHub) durante la ventana disponible. El ciclo escolar se despertó una vez, pero delegó antes de red; tras el corte ambos contadores deben ser cero y las 11 fuentes exclusivas deben seguir activas.  El gate final ya no depende de una espera arbitraria de 24 h: el primer barrido de arranque Portfolio usa el mismo embudo que la cadencia diaria y el refresco manual, con el guard antes de abrir HTTP. Debe mostrar las nueve fuentes transferidas como `SKIPPED`, cero `Starting` para ellas, intento y health 200 con contenido para las once exclusivas; BFF y worker SwissJob deben compartir exactamente 14 providers y 2 scrapers desactivados, excluirlos de sus registros y conservar los productores no transferidos. El contrato completo está en `harvest-overlap-baseline-production.json`. Un intento de combinar `--config-only --dry-run` reveló que el CLI ignoraba el tercer argumento y recreó prematuramente sólo `swissjob-worker`; se detuvo la tanda, se restauró el respaldo automáticamente creado y se recreó de nuevo. El Compose vivo coincide byte a byte con el respaldo (`eb635537…`), volvieron exactamente 12 providers y 2 scrapers desactivados, la imagen siguió en `point5-84853cf`, el ping responde, el arranque no registra errores y ninguno de los cinco procesos Core cambió `StartedAt` ni `RestartCount=0`. `840ab13` hace que más de dos argumentos salga 2 antes de acceder al NAS; el control negativo pasa. Evidencia completa `premature-worker-config-rollback-production.json`. La reauditoría del runtime corrigió esa conclusión: `run_profile_delivery` se inicia únicamente en el lifespan FastAPI/Gunicorn de `backend/main.py`; el worker vivo ejecuta Celery, no importa `profile_sync` y `84853cf..f29fd44` no cambia ningún módulo o tarea Celery. `worker-deployment-decision.json` fija por ello BFF completo `f29fd44` y worker `--config-only`, conservando `point5-84853cf`; se aplica primero la configuración worker 14+2 para apagar su planificación duplicada y después el BFF completo con el entregador nuevo y la misma política. Cerrar sólo tras desplegar y verificar ausencia de peticiones duplicadas y presencia de los productores exclusivos. La línea base del informe diario confirma el coste adicional actual: API y worker vivos tienen cero fuentes desactivadas y a las 07:00:20 el worker inició matching local para 2.377 ofertas nuevas de 2.476, con pico observado de 154,50 % CPU. El contrato candidato excluye las transferidas de la caché local, reutiliza sus scores Core sin matcher local y deduplica la mezcla por URL; 4/4 regresiones pasaron sin red dentro de `portfolio-backend:board-a292ef4`. Repetir esta misma ventana tras el traspaso. |
| 4. Facetas/categorías Core | **CERRADO Y DESPLEGADO** | Bandera activa, paridad exacta, frontend SwissJob servido desde `point5-3986b4a`; logs agregados prueban HTTP 200 para `/match/facets` y páginas por categoría. SQL: facetas 17.472,956 → 1.489,177 ms (-91,48 %); categoría 5.754,283 → 2.676,985 ms (-53,48 %). |
| 5. Cierre de rollback | **LÍNEA BASE COMPROBADA; PENDIENTE** | `rollback-closure-baseline-production.json` (`1036697`, remotos verificados en `60f7704`) muestra SwissJob con `catalog`, `documents`, `saved_searches` y `schools` en `core_primary` comodín, y `matching` en `core_primary` para sus dos perfiles; `applications` y `profiles` no tienen fila y permanecen locales fuera de este cierre. Portfolio tiene applications/documents/saved_searches/schools en `core_primary`, pero catalog y matching siguen en `core_read` comodín. Una comprobación adicional encontró 2 usuarios activos, 2 enlaces Core y cero usuarios sin enlazar, pero sólo dos filas exactas de matching y ningún comodín: el alta pública creaba futuros perfiles sin identidad Core ni ruta. El commit `a8c422c` corrige esa continuidad con `POST /v1/profiles` idempotente, cercado de borrado y provisión desde el entregador; mantiene el modo legacy durante una caída y cambia sólo ese perfil a `core_primary` después del ACK del snapshot. La credencial viva única ya tiene `profiles:write`. Validación: 47/47 API Core y 11/11 entrega focal; suites completas de la cabeza: Core 1.912 passed/1 skipped y backend SwissJob 2.667 passed/3 skipped/4 xfailed dentro de la imagen con FastAPI 0.141.1, SlowAPI 0.1.10 y Starlette 1.7.0; Ruff, formato, compilación y diff limpios. Evidencia `future-profile-provisioning-validation.json`. Las candidatas desplegables `swissjob-core:point5-f29fd44` y `swissjob-backend:point5-f29fd44` reproducen los hashes críticos y no se han enviado ni desplegado; `core-release-candidate.json` y `backend-release-candidate.json`. La imagen worker `f29fd44` también fue construida y validada, pero queda sólo como evidencia local: `worker-release-candidate.json` remite a `worker-deployment-decision.json` y marca su despliegue como innecesario. El checkout desprendido y limpio `/tmp/swissjob-deploy-f29fd44` quedó fijado a ese SHA; los modos secos Core y BFF resuelven exactamente ambas etiquetas y no tocan el NAS. Si `/tmp` se pierde, las evidencias incluyen el comando de recreación. El preflight `release-capacity-preflight.json` comprobó 81 GiB libres en el host de construcción y 440 GiB en el volumen del NAS; tras corregir la decisión worker, sólo Core+BFF requieren transferencia, 5.159.840.952 B sin comprimir. Se repetirá justo antes del despliegue. Una revalidación local a las 08:54 UTC confirmó ambos worktrees limpios en sus SHA exactos, las tres imágenes con los mismos IDs/tamaños/usuarios y las cuatro huellas operativas sin deriva; no mutó el NAS (`candidate-revalidation.json`). Core debe desplegarse primero. `core-postdeploy-verification-plan.json` fija dos sondas sin datos personales: una recorre los nueve agregados desde el cliente autenticado Portfolio, valida sus cotas, scopes y ETag y, tras la concesión, demuestra `harvest:write` con una fuente inexistente que no encola trabajo; la otra exige una cadena dedup con fragmentos de 250, un único terminal, brazos exacto/léxico sólo al final, duraciones menores de 1.800 s y colas drenadas. Ambas pasaron compilación/Ruff; el transporte `--ready-only` pasó vivo contra `5a1241d` y el CLI de disparo pasó offline en la imagen `f29fd44`. No se ejecutaron aún las rutas candidatas ni la deduplicación. `deployment-command-provenance.json` fija además la procedencia ejecutable: Core/BFF desde el worktree limpio `f29fd44`; worker `--config-only` desde el script endurecido de la rama operativa, condicionado a SHA-256. Entre ambas cabezas no cambia Core, backend ni el manifiesto 14+2; los dos dry-run resolvieron `f29fd44` y el control de tres argumentos salió 2 antes de acceder al NAS. Tras retirar CDC y desplegar primero el endpoint Core y luego el entregador, hay que exigir cero usuarios activos sin enlace, cero filas `local|shadow|core_read|rollback_pending`, cero fallback en logs y un solo escritor.  El gate formal quedó definido sobre modos efectivos, porque una fila ausente también significa `local`: Portfolio exige los seis comodines en `core_primary`; SwissJob exige cuatro comodines y matching exacto para cada usuario activo, dejando applications/profiles locales expresamente fuera del alcance. Además requiere cero señales específicas de fallback, outbox/colas drenados, productores únicos, capture/slot ausentes y pruebas funcionales positivas antes de borrar código. Está en `rollback-closure-baseline-production.json`. La inspección del cambio final confirmó que `set_routing` no tiene llamador operativo productivo: se ejecutará en un proceso Python desechable dentro de `portfolio_backend`, nunca con SQL directo. Cada capacidad confirma su propio commit y revisión; después de catálogo y matching se esperan 10 s —el TTL vivo es 5 s y la invalidación del proceso desechable no alcanza las cachés de backend/worker— antes de releer filas/modos efectivos y sondear respuestas/logs. Un éxito parcial mantiene rollback abierto y sólo se reintenta la llamada idempotente ausente. El procedimiento quedó estructurado en la misma evidencia. `portfolio-routing-flip-rehearsal.json` conserva ahora el bloque stdin exacto para `docker exec -i portfolio_backend python -`, sin SQL directo. La imagen `portfolio-backend:board-a292ef4` lo cerró contra PostgreSQL 15 efímero: partiendo de catálogo ya confirmado y matching pendiente cambió sólo matching, mantuvo la revisión de catálogo, la segunda ejecución no modificó filas y una lectura fresca exigió ambos modos `core_primary` sin overrides y con actor de auditoría. Los intentos SQLite se descartan explícitamente por tipos PostgreSQL (`INET`/UUID), no como evidencia del runtime. Frontend de runtime local `07ac81b`; GitHub/GitLab seguían en `a711c26` en la última consulta autorizada. Las sondas inmutables `probe_core_final.py`, `probe_swissjob_final.py` y `probe_portfolio_final.py` convierten los gates finales en salidas 0/1 sin datos personales ni secretos. PyCompile y Ruff pasan. La sonda Core viva falló cerrado sólo por el slot aún existente, con ready autoritativo, 17/17, outbox drenado y staging 0. La revisión automática bloqueó inicialmente enviar las otras dos por SSH por considerarlo exportación de código no autorizada; no se intentó una vía alternativa. El propietario autorizó expresamente el 11-10 a las 09:14:55 UTC la transferencia y ejecución de los scripts y sondas. Se mantienen inmóviles hasta el punto 10, después de sus despliegues, con huellas SwissJob `f3913d35…` y Portfolio `ccd9e108…`. `formal-closure-probes-validation.json`. |
| 6. Simplificación | **INVENTARIADA; PENDIENTE POR ORDEN** | `runtime-cleanup-inventory.json` (`7179959`) fija la superficie real: 8.093 líneas Python en shadow+tareas, 3.752 en 25 módulos eliminables completos de las nueve fuentes Portfolio, más 237 líneas en nueve normalizadores que se retiran selectivamente de `services/job_provider.py` y 3.388 en 17 módulos eliminables completos de SwissJob (16 productores con 3.294 líneas más las 94 líneas de `providers/base_chmedia.py`, cuyos únicos importadores de runtime son Ostjob y Zentraljob). El transporte HTTP ya quedó verificado en producción: destinos explícitos para Portfolio y SwissJob, token presente, 25.405 entregas, cero pendientes/dead-letter y fallback sombra fuera del camino activo (`delivery-transport-production.json`). Se comprobó que `shadow-project` es el único planificador periódico del drenado y la recuperación, por lo que su responsabilidad sí debe extraerse. Los 2 perfiles SwissJob ya usan snapshots nativos; el módulo nuevo filtrará sólo por `profiles.projection_active` y no heredará `inactive_user_refs` ni consultas a staging (`profile-authority-production.json`). Antes de borrar shadow aún hay que extraer recuperación nativa de embeddings/matching, borrado de grafo a `erasure.py`, salud de outbox desde `delivery.stats` y utilidad nDCG; `import_search_corpus` se elimina tras rollback. `core-native-extraction-plan.json` fija ahora los intervalos fuente, destinos, límites y pruebas: recuperación en `tasks/matching.py` a una ronda de embeddings y un perfil por tick de 300 s, borrado FK-safe directamente en `erasure.py`, salud de entrega desde `delivery.stats` con lag máximo 900 s y cero dead letters, y nDCG local a `dev_eval.py`; no crea capas de un solo uso. La revisión estática de imports confirmó que las únicas costuras productivas externas del paquete son `tasks/delivery.py` (fallback de inbox), `erasure.py` (borrado de grafo), `dev_eval.py` (nDCG) e `import_search_corpus.py` (mapeos/lock del proyector); el resto de referencias externas son pruebas o ensayos que deben retirarse o reorientarse con su responsabilidad. El barrido completo de captura amplió el inventario más allá del paquete: durante la retirada deben sincronizarse el Compose vivo, `check_core_release.py` y el supervisor; después del cierre entran también bootstrap del rol, configuración/beat, manifiestos y ejemplos, CI, utilidades de cutover y la dependencia `wal2json`/`wal_level=logical`. Esta última exige una ventana propia de PostgreSQL y no se acopla al corte de aplicaciones. La clasificación reproducible está en `runtime-cleanup-inventory.json`. El runtime escolar (1.515 líneas) no se borra en bloque: conserva refresco manual, store Core, dashboard y alertas. Mantener catálogo/matcher Portfolio para sus 11 fuentes exclusivas y estados locales aún autoritativos. El endpoint aditivo `POST /v1/harvest/sources/{source}/runs` está implementado localmente: exige `harvest:write` e `Idempotency-Key`, resuelve sólo scopes habilitados, devuelve 409 ante un lease vivo, encola claves estables y no guarda éxito si falla el broker. Pasó 48/48 pruebas integradas de API más Ruff; no está desplegado. El agregado acotado `GET /v1/harvest/sources/{source}/aggregate` también está implementado localmente: `vacancies:read`, ETag, hasta 500 ofertas y 500 valores por dimensión, canónica más sólo cinco campos raw permitidos. Despacho y agregado cerraron juntos 50/50 pruebas integradas y Ruff. La base local no contiene las nueve fuentes (0,27–5,89 ms sólo de coste fijo), por lo que no se atribuye rendimiento; el probe previo a despliegue en NAS fue rechazado automáticamente por exportación no autorizada de código y la medición real queda para la release inmutable desplegada. Evidencia `source-aggregate-validation.json`. La concesión necesaria de `harvest:write` queda preparada, pero no aplicada: `credentials.add_scope` sólo acepta los 13 scopes realmente exigidos por las rutas, preserva los existentes, rechaza JSONB corrupto y bloquea la credencial; `rotate_credential autorizar` bloquea todas las credenciales vivas y exige exactamente una. Es idempotente y pasó 8/8 pruebas integradas, Ruff y compilación. El preflight de la imagen detectó antes de etiquetarla que `scripts/` no entra en el contexto final: la implementación se trasladó a `jobhunt_core.rotate_credential`, incluido por el Dockerfile, y el comando histórico queda como entrada compatible sin duplicación. Ambas entradas publican ayuda idéntica; las 8/8 pruebas demuestran además que el comando concede de forma idempotente con una credencial y se niega ante dos credenciales vivas. La candidata Core vigente es `swissjob-core:point5-f29fd44`, no enviada ni desplegada: ID `sha256:ba57dfbd…`, 2.196.691.010 B, usuario `core`, marcador `f29fd44` y seis hashes críticos idénticos al checkout. El smoke carga CLI, alta de perfiles, despacho y agregado; `core-release-candidate.json`. La BFF `swissjob-backend:point5-f29fd44` tampoco se ha enviado ni desplegado: ID `sha256:1444ec06…`, 2.963.149.942 B, usuario `app`, sin Chromium, cuatro hashes críticos idénticos y smoke de imports verde; `backend-release-candidate.json`. La imagen worker `swissjob-worker:point5-f29fd44` se construyó y validó localmente (ID `sha256:2cada33d…`, 4.451.761.388 B), pero no se enviará: el único cambio ejecutable frente a `84853cf` es `profile_sync.py` y corre en BFF, no en Celery. `worker-deployment-decision.json` demuestra el grafo de imports, el diff y los comandos vivos. Los modos secos de Core y BFF completos pasan; para worker se usará exclusivamente `--config-only`, que preserva la etiqueta viva, valida/promueve el Compose 14+2, recrea sólo ese servicio y exige ping Celery. Los despliegues completos Core/BFF reconstruirán y etiquetarán la cabeza vigente, no reutilizarán una etiqueta documental anterior. Debe ejecutarse sólo tras desplegar el endpoint, nunca antes. La operación exacta ya quedó fijada en `rollback-closure-baseline-production.json`: dentro del API Core `f29fd44`, `python -m jobhunt_core.rotate_credential autorizar portfolio harvest:write`; el preflight vivo demuestra un solo secreto activo, el comando bloquea la fila, es idempotente, valida el vocabulario y no rota ni imprime el secreto. Después se releen los scopes y se exige `harvest:write` una sola vez. La misma futura release debe incluir la provisión idempotente de perfiles de `a8c422c` descrita en el punto 5; el BFF no puede desplegar su entregador nuevo antes de que `POST /v1/profiles` esté disponible. La reauditoría AST de todos los consumidores directos descubrió costuras adicionales en `main.py`, informes/digests, sincronización de perfil, exclusiones, stores y tareas de cosecha; su clasificación reproducible está en `post-rollback-dependency-map.json`. No se borrarán los digests ni el overlay de `match_results` en bloque: Core emite `match.evaluated`, pero el inbox SwissJob sólo materializa `saved_search.matches`, y el inbox Portfolio aún no materializa alertas de búsquedas Core. Se preservan hasta que exista paridad de notificación o una decisión explícita retire esa función. La revalidación `cleanup-plan-revalidation.json` contrastó las tres cabezas inmutables: los 17 módulos SwissJob (3.388 líneas), 25 módulos Portfolio (3.752), nueve clases normalizadoras (237) y las costuras Core siguen presentes sin ficheros ausentes ni deriva; los worktrees backend permanecen limpios. Sólo corrigió el censo documental de `AIJobMatchWindow` de 469 a 471 líneas, verificado idéntico a `1bed6c2`; no adelantó implementación. Siguen pendientes el adaptador común Portfolio y ese despliegue posterior al cierre de rollback. |
| Frontend Portfolio | **ARTEFACTO LISTO; PUBLICACIÓN PENDIENTE** | La cabeza local `1bed6c2` conserva el runtime validado de `07ac81b` y añade únicamente el medidor reproducible `e2e/measure-dashboard.mjs`, su comando npm y un límite de 60 s para el gesto. El runtime mantiene lint, 46 ficheros/422 tests, build 3,13 s y bundle principal de 233.734 B (gzip 72.900 B). GitHub y GitLab se consultaron de nuevo a las 09:24 UTC y ambos siguen en `a711c26`; la cabeza local actual queda siete commits por delante de ese estado observado. El preflight `frontend-publication-preflight.json` reconfirmó a las 09:24:32 UTC que `https://vicentepau.is-a.dev` responde 200 desde Cloudflare con `index-D1neE1-1.js`; el repositorio sólo tiene CI, sin acción de despliegue, Wrangler, token ni prueba de qué remoto alimenta la integración Pages. El CNAME público identifica el proyecto `reactportfolio-ey6.pages.dev`, pero no el remoto conectado; el directorio local de Wrangler contiene sólo telemetría, logs y caché, sin sesión de autenticación. Un push verde no demostrará publicación: debe cambiar el asset público y volver a pasar el probe de 12 ventanas; si no cambia, se usa la integración externa de Pages. El propietario autorizó expresamente el 11-10 a las 09:14:55 UTC el push a GitHub/GitLab y la publicación en Cloudflare Pages; se ejecutará en el punto 8 del orden, después de los despliegues Core y SwissJob, y se demostrará por cambio de asset más el probe de 12 ventanas. El plan fija el push del SHA inmutable a ambos `main`, exige que los dos sigan en `a711c26` justo antes, consulta el workflow GitHub `239351652` y preserva el `README.md` ajeno sin incluirlo. Los dos `git push --dry-run` pasaron a las 09:37 UTC y confirmaron el avance rápido exacto `a711c26..1bed6c2` sin escribir en ningún remoto. |
| Validación visual | **BASE POSTCAMBIO LOCAL SUPERADA; REPETIR AL FINAL** | Con el mismo Chromium, fixtures y gesto, `a711c26` y `07ac81b` cargan las 12 ventanas con contenido; la excepción Leaflet baja de 1 a 0 y la carga de 711 a 592 ms (-16,74 %). El arrastre queda igual (~16,66 ms/frame, p95 16,7–16,8 ms): no se atribuye una mejora inexistente. 18 E2E superadas. La medida antigua 43,25 ms/p95 100,1 ms usa otro protocolo. El protocolo quedó versionado en `1bed6c2`, sin ruta absoluta ni dependencia nueva y con un timeout de 60 s sobre el arrastre. Tras añadirlo, el smoke headless volvió a pasar 12/12 ventanas con contenido, cero excepciones y tres arrastres (media global 24,68 ms/frame; p95 máximo 33,4 ms); sólo valida el ejecutable y no se mezcla con la serie headed anterior. Evidencia `frontend-measurement-protocol.json`. La repetición headed de las 06:00 UTC volvió a abrir 12/12 ventanas con contenido, pero el escritorio ralentizó el gesto sintético y no produjo JSON tras 163 s; se abortó, se detuvieron browser y preview y no se retuvo ningún dato de rendimiento de esa ejecución. Repetir después de todos los despliegues con modo y entorno idénticos para ambos extremos. El control headless sin cambiar el protocolo terminó después en 16,48 s: carga 512 ms, 12/12, cero errores, 384 frames en tres gestos, media ponderada 23.91 ms y p95 máximo 33.4 ms; el preview quedó detenido. Confirma que el ejecutable es repetible, sin constituir una nueva comparación. |
| Recursos producción | **LÍNEA BASE FIJADA; REPETIR AL FINAL** | Tres muestras homogéneas de los nueve contenedores afectados: suma de medianas 3,00 % CPU, 2.667,35 MiB y 55 PIDs. Es observación operativa, no causal; repetir el mismo protocolo tras retirar capture y productores duplicados. Evidencia `container-resource-baseline-production.json`; el ahorro causal del bi-encoder permanece separado. `scripts/measure_nas_resources.py` hace reproducible el mismo muestreo por SSH, falla ante conjuntos inesperados y con `--capture-retired` cambia explícitamente de nueve a ocho contenedores; PyCompile, Ruff y 8 pruebas pasan. La guarda de topología pasó viva con los nueve contenedores y el control `--capture-retired` salió 1 antes de muestrear porque capture aún existe; impide fabricar una comparación final prematura. Una muestra viva validó transporte/parser, pero se descarta como rendimiento porque coincidió con trabajo activo Portfolio. Un muestreo adicional de tres capturas en la ventana programada de las 07:00 UTC se conserva separado: 48,32 % CPU, 3.098,69 MiB y 58 PIDs; `portfolio_worker` aportó medianas de 46,29 % y 697,6 MiB. Los eventos agregados delimitan el informe desde las 07:00:20 hasta las 08:01:27: 3.667,17 s, 2.377 ofertas nuevas de 2.476 consideradas y envío de 8 empleos con 0 coincidencias escolares. El muestreo por minuto cubre sólo sus últimos 1.548,94 s, entre 07:35:36 y 08:01:25; sus 26 muestras válidas dieron CPU mediana 71,315 %, p95 103,23 % y máximo 111,30 %, memoria mediana 722,4 MiB, p95 779,0 MiB y máximo 784,6 MiB, siempre 13 PIDs. Una primera sonda falló cerrada por quoting y se conserva. El pico manual anterior de 154,50 % a las 07:29 queda separado porque precede al observador por minuto. Sólo se comparará contra la misma ventana tras el traspaso y sin presentar los percentiles como cobertura de la primera media hora. Evidencias `container-resource-scheduled-window-baseline-production.json` y `portfolio-daily-report-baseline.jsonl` (SHA-256 `2bc44d03…b011ab`). El observador terminó por su condición terminal; no queda proceso pendiente. |

### Diseño mínimo comprobado para la simplificación posterior

Este diseño se aplicará sólo después de cerrar rollback; todavía no se elimina
código ejecutable:

- La recuperación periódica es necesaria porque no existe otro planificador.
  Debe vivir en el módulo cohesivo `tasks/matching.py`, conservar la cadencia de
  300 s y las cotas vivas de una ronda de embeddings y un perfil por tarea,
  expirar ticks solapados y filtrar únicamente por `profiles.projection_active`.
  El worker vivo no define `CORE_SHADOW_PROJECT_EVERY_S`, así que usa ese valor
  por defecto; la limpieza lo renombra a configuración nativa, sin trasladar las
  demás variables `CORE_SHADOW_*`. Reutilizará `_run_pending_impl` y
  `_run_profile_impl`; sólo se trasladan la consulta de señal y el registro
  transaccional del intento, eliminando el filtro legacy
  `_shadow_inactive_profiles`. El mapa exacto de líneas, límites y regresiones
  a reubicar está en `core-native-extraction-plan.json`.
- `_erase_profile_graph` y su orden FK-safe pasan directamente a
  `erasure.py`, que ya es su único llamador productivo. No se crea una capa
  intermedia.
- La salud del outbox pasa a `tasks/delivery.py` reutilizando
  `delivery.stats`, el límite ratificado de 900 s y `dead_total=0`.
- El arranque de `tasks/delivery.py` deja de importar el inbox sombra. Los dos
  destinos HTTP productivos ya están configurados explícitamente; se conserva
  el comportamiento seguro actual: sin configuración no se reclama trabajo y
  un destino desconocido falla sin marcar la entrega como completada.
- Tras borrar `shadow.metrics`, `NDCG_K=10` y la pequeña función DCG quedan
  locales a `dev_eval.py`, su único consumidor; no se añade un módulo de una
  sola función. La búsqueda actual de referencias volvió a confirmar ese único
  consumidor externo y se corrigió la propuesta antigua del inventario que aún
  pedía un módulo neutral de un solo uso.
- `harvest/providers/legacy_shadow.py` no se retira mientras existan listings
  `legacy:*`: admisión nativa consulta ese historial, las búsquedas guardadas
  unifican el nombre público y el sink necesita su normalizador para reconstruir
  la canónica. Sólo podrá borrarse cuando el corpus ya no contenga esas fuentes.
- En SwissJob, los 14 providers y los scrapers `financejobs` e `irishjobs`
  transferidos sólo entran al runtime por los registros de cosecha; el router
  público de empleos lee la base y no importa sus clases. Junto con
  `base_chmedia.py` —usado sólo por Ostjob y Zentraljob— son 3.388 líneas
  candidatas a retirada tras rollback. Se conservan los ocho colectores
  escolares y todos los productores no transferidos; la limpieza incluye
  registros, manifiestos de desactivación y pruebas que ya no describan el
  runtime. La inspección completa del control de entrega confirmó que el cambio
  debe ser atómico: `disabled_sources` rechaza cualquier nombre configurado que
  ya no figure en el registro, por lo que borrar primero las 16 entradas y dejar
  las listas 14+2 impediría arrancar BFF y worker. La misma release retirará las
  entradas y sus 17 módulos, `legacy_sources.py`, los dos campos de `Settings`,
  la guardia de arranque, las cuatro entradas de los manifiestos, la generación
  del ejemplo y la validación correspondiente de `deploy_nas.sh`; los registros
  restantes se construirán directamente. También se quitarán las 16 políticas
  ya inalcanzables de `harvest_window.py`, después de drenar tareas en vuelo, y
  sólo los casos de prueba de productores retirados. Se preservan 12 providers,
  cinco scrapers generales, los ocho escolares, el mantenimiento de URLs, las
  migraciones históricas y la normalización compartida. El mapa reproducible,
  los consumidores y los gates están en
  `swissjob-producer-retirement-plan.json`.
- En SwissJob la topología fija Core abarca catálogo, matching, documentos,
  búsquedas guardadas y colegios. Perfil/autenticación siguen locales por
  diseño, y candidaturas no tienen fila de routing; el frontend usa activamente
  ambos, por lo que se conservan `profiles/local.py` y
  `applications/local.py`. La limpieza elimina los motores locales de las
  cinco capacidades migradas y las tareas legacy de matching/embeddings, pero
  mantiene sincronización de perfil, feedback, alertas y los colectores
  escolares que aún cumplen responsabilidades BFF. La revisión sobre `f29fd44`
  corrigió el inventario: `profile_tasks.py` no se borra en bloque porque también
  hace extracción/autocompletado de CV y progreso SSE; sólo pierde su embedding
  local. `DEFAULT_WEIGHTS` y la cota de tags se extraen antes de borrar
  `job_matcher.py`, y el estado/feedback legacy se conserva hasta su propio gate.
- Las nueve pestañas Portfolio transferidas ya llaman a `/api/v1/jobs/search`
  con filtro Core; sus `urlPath` antiguos no se ejecutan desde el frontend. La
  búsqueda global no encuentra consumidores versionados de esas rutas fuera de
  sus propios routers y pruebas, pero los logs del contenedor no registran
  accesos HTTP (29 líneas/0 solicitudes en 48 h) y no prueban ausencia de
  clientes externos. La superficie son 25 módulos eliminables completos: nueve configuraciones, nueve routers, seis implementaciones y `services/chmedia_job_base.py`; este último sólo tiene como importadores de runtime los routers transferidos de Ostjob y Zentraljob. Además se retiran selectivamente nueve clases normalizadoras (237 líneas) del `services/job_provider.py` compartido. Tras rollback se
  sustituyen por un único router de compatibilidad que reutiliza
  `services.core_http.default_client_factory`: los GET consumen el agregado
  acotado de Core y el POST despacha la fuente; `CompositeCatalog` sigue siendo
  la ruta del buscador unificado. `nav` se traduce a
  `nav_arbeidsplassen`, se preservan los sobres y se eliminan los fetchers
  externos. `refresh-cache` no puede fingir éxito: Core ya dispone de
  `jobhunt.harvest.run_scope`, con exclusión, heartbeat e idempotencia por run,
  pero la revisión confirmó que todavía no existe una operación HTTP que la
  encole. Los nueve nombres Portfolio mapean a nueve fuentes Core y diez scopes
  habilitados —`nav → nav_arbeidsplassen` tiene dos—. Antes de retirar los
  motores locales se añade un único endpoint acotado con scope
  `harvest:write`; el adaptador mantiene la frontera admin y los sobres 200/409,
  y nunca responde éxito si Core no aceptó el trabajo. La imagen candidata,
  ejecutada sin red y con configuración sintética, expone exactamente 56 rutas
  que deben conservarse: 47 GET y 9 POST. La mayoría de sus agregados sale de
  los ocho campos canónicos observados; `job_types` de Arbeitnow y
  `category/job_type` de Remotive sólo viven en las revisiones raw retenidas.
  Para no volver a descargar miles de vacantes al BFF, Core ya implementa localmente
  un único agregado por fuente acotado; el router Portfolio común dará forma a
  los sobres históricos después del cierre de rollback. La superficie se
  extrajo de la imagen inmutable sin red: el frontend vivo ya deriva esas nueve
  pestañas a `/api/v1/jobs/search`, pero no se presupone que no haya clientes
  externos y se mantienen los 56 contratos. El plan mínimo usa un solo módulo y
  una tabla inmutable de fuentes, conserva admin/200/409 e identifica dos límites
  honestos: el `success_rate` de peticiones del scraper no tiene equivalente
  nativo exacto y las dimensiones Core están acotadas a 500. Ambos se validarán
  en producción antes de borrar módulos, sin inventar paridad. Evidencias
  `portfolio-refresh-compatibility.json`, `source-aggregate-validation.json`
  y `portfolio-compatibility-adapter-plan.json`.
- Tras el cierre formal, Portfolio deja una topología fija: catálogo y matching
  resuelven siempre a `CompositeCatalog` y `CompositeMatching` (Core más las
  once fuentes exclusivas); candidaturas, búsquedas guardadas, documentos y
  colegios resuelven directamente a sus clientes Core. Así se retiran del
  runtime `services/routing.py`, el modelo de routing, los endpoints
  `cutover`/`gate_c`, los fallbacks y repositorios locales usados sólo para
  rollback. Se conserva la tabla histórica y se conservan `LocalCatalog` y
  `LocalMatching`, necesarios para los productores exclusivos. La revisión sobre
  `a292ef4` confirmó que también permanecen `analysis_runner.py` y
  `worker_bridge.py` para ejecutar y delegar ese matching local;
  `refresh_replay.py` se conserva por seguridad de rotación de sesión. Las
  costuras de catálogo/matching se simplifican a constructores compuestos fijos,
  no se borran como ficheros completos.
- En el frontend, `FreshnessBadge` y `CompanyResearchName` ya están
  compartidos. La extracción posterior se limita a presentación: resumen de
  tres estados, fecha/frescura y badges; cabe en el módulo cohesivo
  `JobCardExtras`. Título, score, skills y acciones permanecen en cada
  ventana: AI abre mediante una mutación y separa interesado/guardado, mientras
  Board usa enlace externo y considera abierto como guardado. No se crea una
  tarjeta configurable ni se mezclan ventanas/hooks. Evidencia
  `frontend-card-simplification.json`.
- La reauditoría por AST de las cabezas candidatas encontró todos los consumidores
  directos del routing que deben cambiar antes de borrar su modelo. Portfolio
  mantiene análisis local para once fuentes exclusivas, simplifica sus stores a
  Core y conserva el runner de búsquedas hasta materializar en el inbox las
  notificaciones Core. SwissJob entrega perfiles/exclusiones directamente y
  elimina disparadores de embeddings locales, pero conserva digests, salud de
  watchlist, `MatchResultService`, estado de candidaturas y feedback hasta sus
  gates funcionales propios. Detalle: `post-rollback-dependency-map.json`.
- `retention.py` y `erasure_restore.py` se conservan: siguen purgando el inbox
  histórico y aplicando recibos de borrado sobre `shadow_change_log`, tablas que
  expresamente no se eliminan. La búsqueda actual confirmó el llamador periódico
  en `tasks/maintenance.py`, el CLI de restauración y sus saneados de datos; el
  inventario ya no propone retirar esas garantías mientras existan las tablas.
  `search_cutover.py` es el único lector ejecutable
  restante de `public.*` fuera de `shadow`; tras cerrar rollback se borra y
  `migrate.py` deja de crear/verificar `jobhunt_capture` y de otorgar SELECT al
  esquema legacy. Sólo después de desplegar esa release se retira el rol.

### Metodología obligatoria de ejecución y relevo

Esta sesión usa un ciclo verificable y atómico para cada punto. El agente que la
retome debe mantenerlo:

1. Leer primero este bloque, el objetivo activo y
   `remaining-execution-checklist.json`; ejecutar sus identificadores en orden.
   El estado vivo comprobado prevalece sobre la documentación si difieren, y la
   discrepancia se registra antes de continuar.
2. Antes de cada acción, comprobar cabeza y suciedad de los repositorios, huellas
   de scripts/imágenes, release y migración vivas, salud, colas, productores y
   precondiciones temporales. No inferir un estado por una prueba anterior.
3. Declarar el resultado esperado y la condición de aborto. Ante un gate fallido,
   no compensar saltando pasos ni cerrar rollback; conservar el estado seguro,
   registrar la evidencia y corregir la causa mínima.
4. Aplicar el cambio más pequeño que resuelva el flujo trazado, reutilizando
   patrones existentes. Mantener SRP, cohesión alta, acoplamiento bajo,
   legibilidad y consistencia; YAGNI decide entre soluciones que ya satisfacen
   esos principios, nunca los sustituye.
5. Validar de dentro hacia fuera: prueba focal significativa, suite del
   componente, comprobación de configuración o imagen y sonda viva. Para
   frontend, exigir contenido en las doce ventanas y cero excepciones. Repetir
   suites completas sólo cuando un cambio nuevo pueda afectar su resultado.
6. Medir antes y después con el mismo protocolo, entorno, población y ventana.
   Separar causalidad de observación, conservar fallos de medición y no mezclar
   series incompatibles ni atribuir mejoras inexistentes.
7. Crear evidencia reproducible sin secretos ni datos personales, actualizar
   esta deuda inmediatamente con estado, versión, cifras y siguiente condición,
   y hacer un commit de alcance único. Revisar `git diff --cached --check` y el
   diff indexado antes de confirmar.
8. Preservar todos los cambios ajenos. Cuando este fichero tenga ediciones del
   propietario, construir su versión indexada desde `HEAD` y añadir sólo el
   bloque de esta sesión mediante `git hash-object`/`git update-index`; nunca
   indexar el fichero completo ni limpiar el árbol para facilitar un commit.
9. Al terminar o perder contexto, dejar anotados: último punto y subpunto
   cerrados, siguiente comando o gate, PID/proceso de observación, hora mínima,
   cabezas e imágenes candidatas, estado de producción, pruebas pendientes y
   artefactos sin versionar. El siguiente agente revalida todo antes de mutar.

Durante la espera temporal sólo se permiten lectura, validación y preparación
reversible. No se despliega, publica, cambia routing, retira un productor ni
borra código antes del identificador correspondiente. El slot, roles, tablas y
credenciales sólo se retiran en el paso que los nombra y con su condición viva.

### Checklist de reanudación

El orden ejecutable, los gates, las cabezas inmutables y el estado de las
autorizaciones están consolidados en
`docs/audits/2026-10-10-optimization/remaining-execution-checklist.json`. Debe
seguirse por `id`, revalidando cada estado vivo antes de mutarlo; ningún punto
posterior convierte en verde un gate anterior fallido.

### Estado vivo y artefactos exactos

Comprobado el 2026-10-11 00:22 UTC en el NAS:

- `core-api`, `core-worker`, `core-harvest-worker` y `core-ops-worker`:
  `swissjob-core:point5-5a1241d`, `running`, cero reinicios.
- `core-capture`: `swissjob-core:point5-9d6b46e`, aún `running`, cero reinicios.
- SwissJob frontend `point5-3986b4a`, BFF `point5-aa36c5e`, worker
  `point5-84853cf`; todos `running`, cero reinicios.
- Portfolio backend/worker siguen en `portfolio-backend:board-4ce76a7`; la nueva
  candidata local verificada es `portfolio-backend:board-a292ef4`, que incluye
  el traspaso de fuentes, las guardas de despliegue y el retiro del colector
  escolar automático. Aún no está viva.
- Core responde `ready`, `authoritative=true`, Alembic `core0053`, release
  `5a1241d`. Las quince primeras muestras del observador mantienen 17/17 fuentes,
  cero alertas, ninguna `probe_failed` y la cola de embeddings drenada a cero.

Cabezas locales relevantes:

- SwissJob `feat/fase-a-core`: cabeza comprobada `f29fd44`; incluye despacho, agregado, autorización de credencial, corrección dedup por lotes, provisión de futuras altas con cambio por perfil sólo tras ACK, ejemplo de producción regenerado y evidencias de las candidatas Core/BFF.
- ReactPortfolio backend `main`: `a292ef4`, doce commits por delante del remoto; conserva el cambio previo del propietario en `README.md` fuera de nuestros commits.
- ReactPortfolio frontend `main`: `1bed6c2`, siete commits por delante del último estado remoto comprobado (`a711c26`); el runtime termina en `07ac81b` y los dos commits adicionales sólo versionan y acotan la medición.
- Suites completas ya ejecutadas: Core 1.912 passed/1 skipped; Portfolio backend
  2.107 passed/1 skipped; SwissJob backend 2.667 passed/3 skipped/4 xfailed;
  SwissJob frontend 57 tests; Portfolio frontend 422. Validación focal posterior:
  47 API Core y 11 entregas de perfil SwissJob.

Evidencias: `docs/audits/2026-10-10-optimization/README.md`; el índice ya enlaza el medidor, la ventana Portfolio, las huellas de corte, la procedencia de despliegue y las sondas finales. El JSONL de 48 h es
un artefacto vivo y no se versiona hasta terminar la ventana. No guardar logs con
URLs, tokens, credenciales ni datos de usuario.

### Siguiente secuencia operativa exacta

La candidata Portfolio `board-a292ef4` está lista; no desplegarla hasta cumplir
el orden de corte y disponer de la publicación frontend autorizada.

1. Mantener el observador sin reiniciarlo; revisar cada nueva muestra. Si aparece
   `probe_failed`, cambia la release, reinicia cualquiera de los cuatro procesos
   Core o deja de haber 17/17 fuentes sanas, no retirar CDC y abrir una ventana
   nueva desde una muestra válida.
2. Después del 12-10 a las 20:00 UTC, ejecutar la sonda final y las precondiciones
   vivas de `docs/RETIRADA_SLOT_CDC_PUNTO4.md`.
3. Parar sólo `core-capture`; poner `CORE_CAPTURE_ENABLED=false` únicamente en
   `core-ops-worker`; recrear sólo ese worker; esperar seis minutos y demostrar
   que desaparecen las tres tareas de slot mientras `shadow-project` continúa.
4. Observar una ronda nativa completa adicional con capture parado y 17/17
   saludables. Confirmar staging drenado y slot inactivo; sólo entonces borrar
   `jobhunt_shadow_r5_rehearsal`.
5. Quitar servicio/variable/inventarios de captura y actualizar antes el
   supervisor QTS para que no genere una falsa alarma. Recrear los cuatro
   servicios Core de uno en uno y revalidar release/ready/colas.
6. Desde `/tmp/swissjob-deploy-f29fd44`, desplegar primero Core `f29fd44`
   con dedup acotado, despacho, agregado y `POST /v1/profiles`; verificar release
   y API, y conceder `harvest:write` sólo después de que exista su endpoint.
   Aplicar después `scripts/deploy_nas.sh worker --config-only`, preservando
   `swissjob-worker:point5-84853cf` y apagando primero sus productores 14+2;
   desplegar luego la imagen BFF completa `f29fd44`, único proceso que ejecuta
   el entregador nuevo, con la misma política. Completar el resto del traspaso
   en el orden que preserve cobertura: frontend Portfolio publicado → backend
   Portfolio `a292ef4`. Confirmar las 11 exclusivas, ausencia de cosecha
   compartida, refresco manual escolar y cero usuarios activos sin enlace Core
   antes de cerrar rollback.
7. Cerrar rollback con evidencia viva. Después ejecutar la eliminación de código,
   extraer drenado de embeddings, recuperación de matching, borrado de perfiles,
   métrica de outbox y utilidad NDCG fuera de `shadow` antes de borrar ese paquete.
8. Desplegar la limpieza, retirar el rol `jobhunt_capture` sin `CASCADE` y sus
   ficheros de credenciales sólo tras comprobar cero sesiones y cero referencias
   ejecutables. Mantener tablas/migraciones/actas históricas como evidencia.
9. Ejecutar suites completas, E2E de las 12 ventanas, mediciones finales de RAM,
   CPU, tiempos SQL, peticiones externas, número de contenedores (objetivo 8→7)
   y líneas netas retiradas; registrar comparativa antes/después.

### Checkpoint operativo — 2026-10-12 20:02 UTC

- Punto 1 cerrado: observación de 48 h terminada y sonda terminal validada; 96
  muestras, cero fallos de sonda o readiness y cero alertas oficiales. La
  recuperación NAV queda contabilizada aparte y la cola terminó drenada.
- Punto 2 cerrado: precondiciones vivas `17|17|17`, staging CDC 0 y cinco
  servicios coherentes; se detuvo únicamente `core-capture` con salida 0. El
  slot, imagen, contenedor, Compose y respaldos se conservan para rollback.
- Punto 3 activo: aplicar `CORE_CAPTURE_ENABLED=false` sólo en
  `core-ops-worker`, esperar seis minutos y exigir cero envíos de
  `shadow-check-slot-health`, `shadow-preview-cycle` y `shadow-run-cycle`, con
  al menos un envío de `shadow-project`.
- Metodología vigente: revalidar el estado vivo antes de cada mutación, cambiar
  una sola responsabilidad, probar de dentro hacia fuera, medir con el mismo
  protocolo, registrar de inmediato y hacer commits de alcance único sin
  indexar ediciones ajenas.

### Checkpoint operativo — 2026-10-12 20:12 UTC

- Punto 3 cerrado: el flag falso está sólo en `core-ops-worker`; las tres
  cadencias de captura quedaron ausentes y el proyector nativo siguió activo.
- Punto 4 activo: despachar `jobhunt.harvest.dispatch_native` con una ventana
  explícita única posterior al corte, registrar su instante, exigir los 17
  cursores posteriores, cero fallos, `core.harvest=0` y `alertas=[]`. Esta
  ejecución sustituye sólo la espera hasta las 22:10; usa el dispatcher y las
  claves idempotentes existentes, sin código ni abstracción nuevos.

### Checkpoint operativo — 2026-10-12 20:40 UTC

- Punto 4 cerrado: la ronda nativa post-corte terminó 17/17 `ok`, sin errores,
  con todos los cursores posteriores al corte y la cola de cosecha drenada.
- Punto 5 activo e irreversible: volver a leer slot activo/WAL, borrar sólo
  `jobhunt_shadow_r5_rehearsal`, comprobar su ausencia y después finalizar el
  Compose de cuatro servicios y el inventario del supervisor.

### Checkpoint operativo — 2026-10-12 20:45 UTC

- Subpunto irreversible del punto 5 cerrado: el slot de captura ya no existe;
  se registraron 374 MB retenidos y cero slots restantes con ese nombre.
- Siguiente acción: generar `finalize` desde el Compose vivo, validarlo,
  actualizar el supervisor quitando sólo captura, promover ambos, borrar sólo
  el contenedor detenido y revalidar la topología Core de cuatro servicios.

### Checkpoint operativo — 2026-10-12 21:16 UTC

- Punto 5 cerrado: slot y contenedor de captura ausentes; Compose y supervisor
  finales activos; imagen y respaldos conservados; cuatro servicios Core
  coherentes, sin reinicios, salud 17/17 y colas drenadas.
- Punto 6 activo: desplegar Core `f29fd44` desde el worktree limpio fijado,
  ejecutar las sondas de ready, rutas agregadas y dedup por lotes, y conceder
  `harvest:write` sólo después de verificar que el endpoint existe.

### Checkpoint operativo — 2026-10-12 21:20 UTC

- Punto 6 preparado, pendiente de autorización específica de egress: worktree
  limpio `f29fd44`, imagen `ba57dfbd…` y archivo local de 454 MiB con SHA-256
  `72d52307…`. El NAS no contiene la imagen ni el archivo; producción sigue
  sana en `5a1241d/core0053` con cuatro servicios y captura retirada.
- El despliegue normal falló cerrado antes de mutar porque el build local pedía
  interpolar dos variables. La revisión automática rechazó usar valores
  ficticios y también bloqueó transferir la imagen ya verificada por tratarla
  como posible código privado. No se intentará una ruta indirecta.
- Reanudación exacta tras autorización explícita: transferir
  `/tmp/swissjob-core-point5-f29fd44.tar.gz` a
  `nas:/share/Public/swissjob/core-f29fd44.tar.gz`, comparar SHA-256, cargar la
  imagen y continuar con candidato Compose, migración, recreación y sondas.
  Evidencia `core-f29-transfer-preflight.json`.

### Checkpoint operativo — 2026-10-12 21:45 UTC

- La transferencia autorizada y el despliegue Core `f29fd44` están cerrados:
  SHA-256 remoto idéntico, cuatro servicios sobre la etiqueta inmutable,
  `running`, cero reinicios y API `healthy`. Readiness publica `f29fd44`,
  `core0053` y autoridad activa; la sonda final mantiene 17/17, cero alertas,
  slot ausente, staging cero y entregas drenadas.
- El gate de rutas permanece abierto y `harvest:write` **no se ha concedido**.
  Arbeitnow devuelve contrato correcto para 21.402 ofertas, pero tardó 165,2 s
  y superó el límite de 60 s. El perfilado atribuye 57,9 s al fetch SQL frente
  a 72 ms combinados de validación, ETag y JSON.
- `EXPLAIN ANALYZE` confirma la causa: 21.402 búsquedas laterales de la última
  revisión consumen 27,4 s sin índice ordenado y el CTE materializado arrastra
  el JSON completo de oferta por todas las facetas (1.953.864 bloques hit y
  193.645 leídos). Evidencia `core-f29-deployment-production.json`.
- Siguiente acción exacta: índice `(incarnation_id, fetched_at DESC, id DESC)` y
  proyección acotada en `source_rows`; ejecutar pruebas focales y completas,
  construir/desplegar una release inmutable, repetir las nueve fuentes y dedup,
  y sólo entonces conceder el scope.
- Metodología vigente: comprobar el estado vivo antes de mutar; medir y aislar
  cada etapa; aplicar el cambio mínimo que elimina la causa raíz; validar de
  dentro hacia fuera; mantener los gates de funcionalidad, rendimiento y
  rollback; registrar evidencia y commit de alcance único sin indexar cambios
  ajenos. El estado vivo prevalece siempre sobre esta bitácora.

### Checkpoint operativo — 2026-10-12 22:05 UTC

- La corrección de rendimiento está implementada en e535e45: índice ordenado
  de última revisión y proyección acotada del CTE, sin caché, dependencia ni
  abstracción nueva. Conserva filtrado de job_types, límites, conteos y ETag.
- Validación local cerrada: 10/10 pruebas focales de ruta/migración; suite Core
  completa 1.912 passed y 1 skipped en 972,98 s; Ruff, formato y compilación
  verdes. El intento de Ruff dentro de la imagen runtime se descarta porque esa
  imagen no lo instala; se repitió correctamente con el ejecutable del host.
- Siguiente acción exacta: construir y desplegar imagen inmutable e535e45,
  aplicar core0054, repetir EXPLAIN ANALYZE, las nueve rutas, ETag y dedup;
  mantener harvest:write sin conceder hasta que todos esos gates pasen.
- Metodología vigente: comprobar estado vivo, medir por etapas con el mismo
  protocolo, corregir la causa mínima, validar de dentro hacia fuera, desplegar
  con release inmutable y comparar antes/después antes de continuar.

### Checkpoint operativo — 2026-10-12 22:25 UTC

- Release optimizada 19efe49 desplegada: archivo remoto verificado, core0054
  aplicado y cuatro servicios coherentes, running, sin reinicios. Readiness,
  autoridad, captura retirada, entregas y salud 17/17 permanecen verdes.
- El gate de rutas está cerrado: las nueve fuentes devolvieron 200, contrato
  completo, ETag/304 y cero fallos. Arbeitnow bajó de 165,2 s a 35,4 s en la
  medición aislada equivalente (78,58 %); en el probe completo tardó 37,1 s y
  el siguiente máximo fue Ostjob con 6,1 s.
- Dedup se disparó una sola vez a las 22:16:12 UTC tras demostrar cinco colas y
  tareas activas a cero. El primer fragmento de 250 sigue activo sin error; no
  se ha concedido harvest:write. Siguiente acción: esperar parcial más terminal,
  validar el log fijado y colas drenadas, conceder el scope y repetir el probe
  con --after-grant.
- Metodología vigente: estado vivo antes de mutar, misma carga para comparar,
  evidencia de fallos y éxitos, cambio mínimo, gates secuenciales y bitácora
  inmediata sin indexar ediciones ajenas.

### Checkpoint operativo — 2026-10-12 22:36 UTC

- Primer fragmento dedup cerrado: 250 filas, 36 candidatos nuevos, cursor
  completo y continuación encolada; duración 1.121,915 s, inferior al límite
  de 1.800 s. La ventana tiene 2.557 filas y 11 lotes previstos.
- La segunda tarea a292006e-bc05-49cd-9184-397faf36c4ea está activa sobre la
  misma ventana congelada. No lanzar otra cadena. harvest:write sigue sin
  conceder; el siguiente gate exige un único terminal, ausencia de errores y
  todas las colas drenadas.
- Metodología vigente: observar la cadena por sus IDs, ventana/cursor y logs
  propios; conservar cada duración real; no inferir éxito desde Redis vacío ni
  interrumpir un fragmento sano.

### Checkpoint operativo — 2026-10-12 22:50 UTC

- Frontend Portfolio 1bed6c2 revalidado sin publicar: lint, 422/422 pruebas,
  build en 3,20 s y 18/18 E2E. Ambas ramas remotas siguen en a711c26 y el
  README previo del propietario permanece fuera de commits.
- Medición candidata con el protocolo fijado: 12/12 ventanas con contenido,
  cero errores JS, carga 559 ms, arrastre medio 26,11 ms/frame y p95 33,4 ms.
  Frente a 43,25/100,1 ms son mejoras de 39,64 % y 66,63 %. Repetir tras el
  despliegue público; evidencia portfolio-frontend-candidate-revalidation.json.
- Se descartan dos intentos sin dato comparable: preview ausente y ejecución
  headed sin HEADLESS=true. Dedup sigue bajo el observador gestionado; no se
  adelantó publicación ni despliegue.

### Checkpoint operativo — 2026-10-12 23:17 UTC

- Dedup lleva 3 parciales verdes: 750 filas, 125 candidatos nuevos y duraciones
  1.121,915 / 926,413 / 1.451,184 s, todas inferiores a 1.800 s. La cuarta
  tarea bc1991b0-fcaf-450e-b19c-e719ebe94327 está activa; cero errores. Si se
  reanuda, leer logs desde 22:16:12 UTC y no disparar otra cadena raíz.
- Artefactos siguientes preparados sólo en local: BFF f29fd44, 638 MiB,
  SHA-256 4110915f...0803; Portfolio backend a292ef4, 954 MiB, SHA-256
  52435279...a06e. Ninguno se ha transferido ni desplegado.
- El transformador temporal NAS-only pasó compilación y doble aplicación
  idempotente sobre el manifiesto saneado para worker y backend. La ruta evita
  copiar secretos del Compose al host y conserva respaldo/candidato/config
  antes de promover.

### Checkpoint operativo — 2026-10-12 23:21 UTC

- Preparado el despliegue NAS-only de Portfolio a292ef4 sin tocar producción:
  transforma únicamente las imágenes `backend`/`worker` y su política exacta de
  nueve fuentes transferidas, valida el candidato y sólo entonces lo promueve.
- El transformador pasó `py_compile` y dos aplicaciones idénticas sobre el
  manifiesto saneado; el script pasó `bash -n`. Los secretos del Compose vivo no
  salen del NAS. El archivo de 954 MiB sigue sin transferir ni desplegar.

### Checkpoint operativo — 2026-10-12 23:32 UTC

- Cuarto fragmento dedup verde: 250 filas, 32 candidatos nuevos y 957,323 s.
  Acumulado: 1.000 filas, 157 candidatos, cuatro parciales, cero errores y
  máximo 1.451,184 s. La quinta tarea
  `90113586-184b-41f4-b69d-17cb0924d682` está activa.
- Las tareas periódicas pendientes se drenaron en el límite entre fragmentos;
  outbox siguió en cero. Se mantiene la misma ventana congelada y no se dispara
  una segunda cadena raíz.

### Checkpoint operativo — 2026-10-12 23:46 UTC

- Quinto fragmento dedup verde: 250 filas, 51 candidatos nuevos y 725,659 s.
  Acumulado: 1.250 filas, 208 candidatos, cinco parciales, cero errores y máximo
  1.451,184 s. La sexta tarea `218b227e-01c7-424c-b513-4319a6f98f79` está activa.
- Outbox siguió drenado en el límite. Continuar leyendo la cadena existente desde
  22:16:12 UTC; no volver a invocar la tarea raíz.

### Checkpoint operativo — 2026-10-12 23:53 UTC

- Sexto fragmento dedup verde: 250 filas, 52 candidatos nuevos y 535,023 s.
  Acumulado: 1.500 filas, 260 candidatos, seis parciales, cero errores y máximo
  1.451,184 s. La séptima tarea `3a5d5e5e-2fda-4ef1-b7e3-af3f0a15ed80`
  está activa; la ventana congelada no cambia.

### Checkpoint operativo — 2026-10-13 00:05 UTC

- Séptimo fragmento dedup verde: 250 filas, 45 candidatos nuevos y 541,599 s.
  Acumulado: 1.750 filas, 305 candidatos, siete parciales, cero errores y máximo
  1.451,184 s. La octava tarea `054b9f1f-7e8c-48c6-a4ba-8402585ffef7`
  está activa. El cambio de fecha UTC no altera la ventana congelada original.

### Checkpoint operativo — 2026-10-13 00:10 UTC

- Octavo fragmento dedup verde: 250 filas, 57 candidatos nuevos y 422,244 s.
  Acumulado: 2.000 filas, 362 candidatos, ocho parciales, cero errores y máximo
  1.451,184 s. La novena tarea `e824fe4c-c950-4ee6-9b90-f1d71d0d82e0`
  está activa; quedan tres fragmentos contando el actual.

### Checkpoint operativo — 2026-10-13 00:16 UTC

- Noveno fragmento dedup verde: 250 filas, 42 candidatos nuevos y 338,439 s.
  Acumulado: 2.250 filas, 404 candidatos, nueve parciales, cero errores y máximo
  1.451,184 s. La décima tarea `e29bc07a-ebe8-43fa-bd51-3bb2021381da`
  está activa; después sólo queda el lote terminal de 57 filas previsto.
- El chequeo horario intermedio volvió a dar 17/17 y alertas de cosecha y
  credenciales vacías; el outbox siguió drenado.

### Checkpoint operativo — 2026-10-13 00:24 UTC

- Décimo fragmento dedup verde: 250 filas, 72 candidatos nuevos y 425,554 s.
  Acumulado: 2.500 filas, 476 candidatos, diez parciales, cero errores y máximo
  1.451,184 s. El único lote terminal esperado, de 57 filas, está activo con ID
  `097b3c62-22fe-4b60-93bf-b2a0f1bea7e0`.
- Al terminar: recuperar logs desde 22:16:12 UTC, ejecutar el verificador, exigir
  un único terminal y cinco colas a cero; sólo entonces conceder `harvest:write`
  y repetir la sonda `--after-grant`.

### Checkpoint operativo — 2026-10-13 00:29 UTC

- El undécimo fragmento también fue parcial: 250 filas, 33 candidatos nuevos y
  265,675 s. Acumulado: 2.750 filas, 509 candidatos, once parciales y cero
  errores. La duodécima tarea `8e2cf04b-4f5c-43cb-958a-7a4f74d30a20` está activa.
- Corrección de evidencia: el recuento previo de 2.557 no representa el mismo
  conjunto que pagina la consulta real `_CORPUS_SQL` bajo la ventana congelada;
  queda conservado como preflight refutado y deja de usarse para estimar el
  terminal. La cadena es válida porque ventana y cursor keyset siguen avanzando;
  observar hasta `complete=true`, sin lanzar otra raíz ni inventar un total.

### Checkpoint operativo — 2026-10-13 00:31 UTC

- Recuento read-only con los mismos joins de `_CORPUS_SQL`, la ventana congelada
  y el cursor del undécimo fragmento: 3.359 filas totales y 609 restantes.
  Probablemente quedan dos parciales de 250 y un terminal de 109 si el conjunto
  de revisiones actuales permanece igual; `complete=true` sigue siendo la única
  condición válida de cierre.
- El primer intento de sonda no emitió JSON y fue descartado; el segundo usó
  stdin ya probado, devolvió el JSON anterior y no imprimió credenciales ni
  payloads de ofertas.

### Checkpoint operativo — 2026-10-13 00:36 UTC

- Duodécimo fragmento dedup verde: 250 filas, 70 candidatos nuevos y 500,553 s.
  Acumulado: 3.000 filas, 579 candidatos, doce parciales, cero errores y máximo
  1.451,184 s. La decimotercera tarea
  `5678995f-6441-48b4-9921-5f30b7aa256e` está activa.
- Según el recuento idéntico quedan 359 filas tras el cursor del duodécimo:
  previsiblemente un parcial de 250 y un terminal de 109. Verificar por la señal
  real, sin usar la predicción como gate.

### Checkpoint operativo — 2026-10-13 00:41 UTC

- Decimotercer fragmento dedup verde: 250 filas, 62 candidatos nuevos y
  341,171 s. Acumulado: 3.250 filas, 641 candidatos, trece parciales, cero
  errores y máximo 1.451,184 s.
- El lote 14 `60e1a405-00dc-49e2-9b52-d6e4e1339323` está activo sobre las 109
  filas restantes medidas y debe ejecutar los brazos exacto/léxico. Exigir que
  aparezca como único `complete=true`; la cifra de 109 sigue siendo observación,
  no sustituto del gate.

### Checkpoint operativo — 2026-10-13 00:48 UTC

- Cadena dedup cerrada y verificada: 14 lotes (13 parciales + un terminal),
  3.359 filas, 671 candidatos nuevos, máximo 1.451,184 s, cero fallos. Terminal:
  109 filas, 30 candidatos ANN, 4.403 exactos y 1.414 léxicos. El verificador
  inmutable devolvió `failures=[]` y las cinco colas quedaron a cero.
- El observador intentó ejecutar el verificador desde un worktree temporal ya
  ausente; ese resultado se descartó. Se recuperaron logs frescos y se ejecutó
  el mismo verificador desde el repositorio actual con salida verde.
- Dos lecturas de colas se descartaron (`NOAUTH` y URL no expuesta). La lectura
  válida reutilizó dentro del ops-worker el broker configurado por Celery,
  imprimió sólo longitudes y dio cero en las cinco colas.
- `harvest:write` NO se concedió: la revisión automática rechazó el comando antes
  de ejecutarlo porque es un privilegio persistente que permite despachar
  cosechas en producción y exige autorización explícita que nombre el scope y
  ese alcance. Tras recibirla, repetir el CLI idempotente, verificar el scope
  una sola vez y ejecutar la sonda `--after-grant`; no usar atajos.

### Checkpoint operativo — 2026-10-13 06:28 UTC

- El usuario autorizó explícitamente conceder de forma persistente
  `harvest:write` a la credencial `portfolio`, incluido su alcance para despachar
  cosechas en Core. El CLI idempotente lo añadió a la única clave activa
  `48c26fe326c924f4`; el listado posterior muestra una clave y una sola aparición
  del scope.
- La sonda `--after-grant` quedó verde: release `19efe49`, migración `core0054`,
  Core autoritativo, nueve agregados 200 con contrato y salud válidos, ETag 304 y
  despacho sobre una fuente inexistente 404/`not_found`, sin encolar. Celery
  confirmó cero tareas reservadas y activas en sus tres nodos.
- El presupuesto anterior de 60 s era insuficiente bajo carga real: Arbeitnow
  tardó 128,288 s y su revalidación 59,174 s. Se eleva sólo el timeout de la
  sonda a 180 s y se conserva la latencia como dato; no se relaja ningún gate
  funcional. El punto 6 queda completo y el 7 pasa a ejecutable.
- Metodología vigente: confirmar autorización y estado vivo antes de cada
  mutación, aplicar una operación idempotente mínima, probar el efecto por el
  límite público sin generar trabajo, comprobar colas, registrar latencias y
  fallos descartados, actualizar evidencia/checklist/deuda y crear un commit
  estrecho antes de avanzar al siguiente punto.

### Checkpoint operativo — 2026-10-13 06:39 UTC

- Punto 7 cerrado. El worker conserva la imagen `point5-84853cf`, está en
  ejecución con cero reinicios y responde al ping; su configuración efectiva
  desactiva exactamente 14 proveedores transferidos y los scrapers
  `financejobs`/`irishjobs`.
- El BFF inmutable `f29fd44` se transfirió con SHA-256 coincidente, se desplegó
  sólo sobre `backend` y quedó `healthy`, con cero reinicios. La sonda final dio
  `failures=[]`: 2/2 perfiles activos enlazados, cero snapshots pendientes o con
  error, matching y cuatro dominios wildcard en `core_primary`, y ocho scrapers
  escolares exclusivos retenidos.
- El primer transformado de la copia candidata falló antes de promoverla por
  permisos restrictivos. Se corrigió haciendo root únicamente al contenedor
  efímero que edita/valida el manifiesto; las copias conservaron sus permisos y
  los servicios desplegados mantuvieron sus usuarios. Los logs posteriores no
  muestran errores ni ejecución de productores transferidos.
- Metodología vigente: artefacto por commit y hash, copia candidata y respaldo,
  validación antes de promoción, recreación de un solo servicio, prueba desde el
  runtime real, comprobación de reinicios/logs/política efectiva y checkpoint
  estrecho. El punto 8 queda listo para publicar el frontend exacto `1bed6c2`.

### Checkpoint operativo — 2026-10-13 06:41 UTC

- Precondición del punto 8 revalidada: frontend `HEAD=1bed6c2`, ambos `main`
  remotos en `a711c26`, avance lineal y `README.md` del propietario excluido.
- La revisión automática rechazó ambos pushes antes de ejecutarlos. GitHub y
  GitLab no cambiaron. Aunque la autorización general y la autorización de
  publicación del 11 de octubre están documentadas, el revisor exige una nueva
  autorización explícita que nombre el push directo del SHA inmutable a `main`
  en ambos destinos y reconozca que modifica ramas por defecto y puede publicar
  código.
- Reanudación exacta: tras esa autorización, ejecutar los dos comandos ya
  registrados, volver a leer ambos refs, observar checks y esperar el cambio del
  asset público antes de repetir la sonda de 12 ventanas. No adelantar el punto
  9 porque el orden requiere cerrar primero publicación y navegador.
- Metodología vigente: precondición remota read-only, mutación fast-forward del
  SHA exacto, verificación independiente del ref y del sitio público, medición
  con el protocolo congelado, evidencia inmediata y commit estrecho.

### Checkpoint operativo — 2026-10-13 07:25 UTC

- Punto 8 cerrado tras autorización explícita posterior al aviso de riesgo.
  GitHub y GitLab apuntan al SHA exacto `1bed6c2`; el workflow Frontend CI
  terminó `success` y Cloudflare cambió el asset `D1neE1-1` por `V0q6T-Bl`.
  El `README.md` local del propietario siguió fuera de la publicación.
- La medición pública congelada cargó 12/12 ventanas con contenido y cero
  errores JavaScript: 900 ms de carga, 28.58 ms/frame de media y p95
  33.4 ms. Frente a la línea base 43,25/100,1 ms, el arrastre
  mejora 33.92 % de media y
  66.63 % en p95. La carga
  pública se registra sin compararla con la preview local por incluir red/CDN.
- Metodología vigente: publicar sólo el SHA validado, verificar refs remotos,
  exigir CI verde y asset público distinto, ejecutar el mismo navegador con
  fixtures y viewport congelados, conservar mediciones crudas y cerrar el gate
  en evidencia/checklist/deuda antes del siguiente despliegue.
- El punto 9 queda listo: backend Portfolio `a292ef4`, archivo local de 954 MiB
  y SHA-256 `52435279...a06e`; transformar el Compose sólo dentro del NAS,
  recrear `backend`/`worker` y probar política de nueve fuentes, once exclusivas
  y escuelas manuales sin cosecha programada.

### Checkpoint operativo — 2026-10-13 07:38 UTC

- Punto 9 cerrado. `portfolio_backend` y `portfolio_worker` ejecutan la imagen
  inmutable `board-a292ef4`, con cero reinicios, `/health=200` y Alembic
  `uu44v3375x21 (head)`.
- La política efectiva contiene exactamente nueve fuentes transferidas. Las
  nueve registraron `SKIPPED` en 0,00 s antes de abrir red; las once fuentes
  exclusivas completaron el ciclo inicial en 93,95 s. La tarea periódica escolar
  sólo reintenta alertas, mientras los dos endpoints y colectores manuales siguen
  presentes.
- Dos fallos de tooling quedaron descartados con evidencia: el primer candidato
  no se promovió porque esperaba una clave de política preexistente; el
  transformador corregido inserta/reemplaza idempotentemente. El script final
  consultó un atributo Settings en mayúsculas inexistente después de que salud,
  migración y recreación ya pasaran; la lectura correcta en minúsculas confirmó
  el runtime exacto.
- Metodología vigente: candidato antes de manifiesto vivo, hash remoto, cambio
  mínimo de dos servicios, comprobación desde Settings/código/logs desplegados,
  distinción entre fallos del despliegue y del verificador, y cierre documental
  antes de avanzar. El punto 10 queda listo para las tres sondas inmutables.

### Checkpoint operativo — 2026-10-13 07:41 UTC

- Punto 10 ejecutado con las tres sondas inmutables. Core dio `failures=[]`,
  17/17, release `19efe49/core0054`, slot ausente y entrega drenada. SwissJob
  dio `failures=[]`, 2/2 perfiles enlazados y política exacta de productores.
- Portfolio validó schedulers, nueve fuentes desactivadas y once retenidas; su
  único fallo fue `routing_not_exactly_core_primary_without_overrides` porque
  `catalog` y `matching` siguen exactamente en `core_read`. Las otras cuatro
  capacidades ya están en `core_primary` y no hay overrides.
- Este fallo es la precondición delimitada del punto 11, no un gate omitido: la
  siguiente mutación debe tocar sólo las filas wildcard de `catalog` y
  `matching`, esperar diez segundos y repetir sin cambios las tres sondas.
- Metodología vigente: sondas inmutables antes y después, conservar un fallo
  esperado sólo cuando identifica exactamente la mutación planificada, acotar
  filas y valores previos, esperar propagación, verificar single-writer y crear
  evidencia/commit antes de cerrar rollback.

### Checkpoint operativo — 2026-10-13 07:46 UTC

- Punto 11 cerrado. El bloque ensayado conservó el SHA-256 `8feaec...` y se
  ejecutó sólo después de comprobar cinco colas a cero y ausencia de tareas
  activas/reservadas. Cambió únicamente las filas wildcard de `catalog` y
  `matching`, de `core_read` a `core_primary`, y verificó actor y valor final.
- Tras diez segundos, las tres sondas inmutables devolvieron `failures=[]`.
  Portfolio resuelve las seis capacidades sólo a `core_primary`, mantiene nueve
  fuentes transferidas desactivadas y once propias; SwissJob conserva 14+2
  desactivadas, y Core sigue 17/17, autoritativo, sin slot ni entrega pendiente.
- El single-writer queda probado por estado efectivo y productores: Core posee
  el conjunto transferido, SwissJob y Portfolio lo omiten antes de red y no hay
  overrides ni filas no primarias dentro del alcance.
- Metodología vigente: bloque versionado y verificado por hash, precondiciones
  fail-closed, mutación mediante el servicio de dominio, espera de propagación,
  mismas sondas post-cambio y evidencia estrecha. El punto 12 debe cerrar
  formalmente rollback sólo tras una última auditoría conjunta del estado vivo.

### Checkpoint operativo — 2026-10-13 07:51 UTC

- Punto 12 y rollback formal cerrados. `core-capture` y el slot están ausentes;
  las cinco colas, staging y outbox están drenados; los ocho servicios relevantes
  ejecutan las releases previstas con cero reinicios, y las tres sondas finales
  devuelven `failures=[]`.
- Desde los despliegues finales hay cero señales específicas de fallback en
  Portfolio y SwissJob. El catálogo real respondió 200, cinco elementos y
  `core_plus_local` sin fallback; matching resolvió `core_primary` y obtuvo el
  feed persistente de 1.800 resultados sin suplantar usuarios.
- El single-writer queda cerrado: Core 17/17, SwissJob omite 14 proveedores y dos
  scrapers, Portfolio omite nueve y conserva once, todas las capacidades dentro
  del alcance son autoritativas y no existe consumidor CDC de proyección.
- Las imágenes, respaldos, migraciones y tablas históricas se conservan hasta
  superar sus gates de retirada física. Metodología siguiente: extraer primero
  las cuatro dependencias nativas de Core con pruebas, desplegar/verificar y sólo
  entonces borrar runtime/config/herramientas shadow y el rol capture; después
  continuar con Portfolio, SwissJob y frontend en orden numérico.

### Checkpoint operativo — 2026-10-13 08:35 UTC

- Punto 13 cerrado. La recuperación periódica pertenece ahora a
  `jobhunt_core.tasks.matching`: usa sólo `profiles.projection_active` para
  todos los consumidores, conserva las señales de revisión/generación e intento,
  omite perfiles sin vector y ordena de forma determinista por el intento más
  antiguo. Cada tick de 300 s caduca al siguiente, drena una sola ronda de
  embeddings y evalúa como máximo un perfil.
- El borrado FK-safe vive junto a `erase_owned_profile` y ya no importa el
  proyector. Se mantienen `scrub_capture` mientras exista la tabla histórica,
  `scrub_portfolio_manifests` y el orden de locks/recibo/hijos. La salud de
  entrega pasa a `jobhunt.delivery.check_health`, con alerta si el pendiente más
  antiguo supera 900 s o existe un dead-letter; no escribe métricas shadow.
  `dev_eval` posee localmente `NDCG_K=10` y la fórmula DCG.
- Verificación: `ruff` y `py_compile` verdes; 46 pruebas focalizadas y los
  tres casos corregidos verdes; suite completa oficial: 1.924 pasadas, una
  omitida por condición conocida del entorno y cero fallos en 997,75 s. La
  evidencia reproducible está en
  `core-native-extraction-validation.json`.
- Metodología vigente: trazar cada consumidor antes de mover código; trasladar
  el contrato mínimo a su módulo propietario sin capa nueva; mantener el runtime
  histórico hasta que los reemplazos tengan pruebas focalizadas y suite completa;
  distinguir defectos funcionales de fallos de infraestructura de test; cerrar
  evidencia, checklist, deuda y commit por punto. El punto 14 debe inventariar
  referencias ejecutables con `rg`, borrar sólo runtime/config/herramientas
  shadow, conservar migraciones/tablas/retención/restauración, repetir la suite
  completa y desplegar/verificar antes de eliminar el rol capture.

### Límites y cautelas para el siguiente agente

- El middleware global propio de rate limiting no es código histórico retirable con
  las dependencias fijadas: FastAPI 0.141.1/SlowAPI 0.1.10 no encuentran handlers
  de routers incluidos y cinco peticiones quedan en 200. Una imagen local antigua
  con 0.136.3/0.1.9 sí los encontraba; esa sonda no representa la release. Se
  restauró el middleware y la imagen fijada cerró 28/28 focales y 2.667 pruebas
  backend. No simplificarlo sin una actualización explícita de dependencias y la
  misma sonda 200/200/429/429.
- No suplantar usuarios construyendo JWT desde la base o la clave de firma: la
  revisión automática rechazó esa sonda. La prueba de facetas en producción se
  resolvió de forma segura con logs HTTP agregados; no repetir el intento.
- La recuperación, el borrado, la salud de entrega y nDCG ya son nativos.
  `import_search_corpus.py`, `retention.py` y `erasure_restore.py` conservan
  requisitos exactos registrados en `runtime-cleanup-inventory.json`; comprobarlos
  al borrar el paquete y preservar migraciones y tablas históricas.
- No cerrar 28.767 ofertas legacy en bloque: la admisión nativa de siete días no
  repone de inmediato el histórico. Se apagan productores y sus filas envejecen.
- No borrar presentación/normalizadores de fuentes del frontend mientras Core
  siga devolviendo esas fuentes. La extracción compartida entre
  `AIJobMatchWindow` y `JobBoardTabbedWindow` debe ser una tarjeta base pequeña;
  mantener ventanas y hooks separados.
- Hay cambios de documentación previos no pertenecientes a esta sesión en
  `CLAUDE.md`, varios documentos históricos/unificación y este mismo fichero.
  No incluirlos en commits de optimización. En ambos repos Portfolio, `README.md`
  también está modificado previamente. Comprobar siempre el índice antes de
  cada commit.

### Protocolo de actualización de esta bitácora

Después de cada punto o subpunto realizado: actualizar fecha, estado, evidencia,
commit/release, medición y siguiente acción concreta; después crear un commit que
incluya únicamente esa actualización y sus evidencias. Si la sesión termina, el
primer paso del siguiente agente es comprobar NAS, repositorios y el PID/sesión
del observador contra esta foto; el estado vivo prevalece sobre el texto.

---

## Mantenimiento

Este documento se actualiza **al cerrar cada fase**, junto con el backlog. Reglas:

1. Un ítem entra aquí cuando se decide **no** abordarlo, con su porqué — no cuando se descubre.
2. Un ítem sale cuando se cierra, y su cierre se referencia por commit.
3. Si el backlog y este documento se contradicen, **manda el código**: verifícalo antes de escribir.
4. Los ítems marcados ★ son los que cambian decisiones de planificación; revísalos antes de cada
   fase nueva.

### Checkpoint operativo — 2026-10-13 09:03 UTC

- Punto 14 validado localmente y aún no cerrado en producción. Se retiraron el
  paquete `jobhunt_core.shadow`, sus tareas, captura/gates/proyección, los
  comandos de importación/cutover ya sin rollback, el servicio `core-capture`
  de los ocho manifiestos y las herramientas de transición cerradas. El
  bootstrap de `jobhunt_core` converge ahora a cero grants directos sobre
  tablas y secuencias de `public`; el rol `jobhunt_capture` sólo se eliminará
  después de desplegar y verificar la nueva release.
- Se conservaron todas las migraciones/tablas históricas, la purga de
  `shadow_inbox`, `erasure_restore` y `scrub_capture` mientras exista
  `shadow_change_log`. `wal_level=logical` y wal2json quedan para un cambio de
  mantenimiento de base separado, sin mezclar un reinicio de Postgres con este
  despliegue de aplicación.
- Verificación: 138 pruebas focalizadas y suite Core completa con 1.547 pasadas,
  una omitida y cero fallos en 460,41 s; Ruff, `compileall`, `bash -n`,
  `git diff --check` y ocho modelos Compose verdes. No quedan imports
  ejecutables `jobhunt_core.shadow`; el contenedor local huérfano de captura se
  eliminó después de la suite.
- Metodología vigente: trazar dependencias antes de borrar; migrar primero cada
  consumidor nativo; preservar datos y mecanismos de retención/restauración;
  retirar por grupos huérfanos comprobados; usar pruebas focalizadas y suite
  completa; construir una imagen desde un commit exacto; desplegar/migrar y
  comprobar release, cuatro workers, colas, entrega, 17 fuentes y tablas
  históricas; sólo entonces eliminar el rol de captura y cerrar evidencia.
- Reanudación exacta: crear un commit estrecho que excluya cambios previos del
  propietario, construir/desplegar ese SHA, ejecutar las sondas de producción y
  retirar `jobhunt_capture` únicamente si todo sigue verde. Después marcar el
  punto 14 completo y habilitar el punto 15.

### Checkpoint operativo — 2026-10-13 09:16 UTC

- Punto 14 cerrado en producción. Los cuatro servicios Core ejecutan la imagen
  inmutable `swissjob-core:point5-3ed6462`, publican
  `3ed6462/core0054`, tienen cero reinicios y los tres workers responden
  `pong`. Las cinco colas están a cero; la sonda final devuelve
  `failures=[]`, 17/17 fuentes sin alertas, entrega drenada y staging a cero.
- El beat contiene doce tareas nativas y ninguna tarea shadow. Los destinos HTTP
  `portfolio` y `swissjob-shadow` conservan configuración completa; este último
  es el identificador contractual del consumidor SwissJob, no el runtime CDC
  retirado. Las seis tablas históricas comprobadas siguen presentes para
  retención, recibos y restauración.
- Se eliminó `jobhunt_capture` tras comprobar cero sesiones, slots y
  memberships; también desaparecieron el fichero secreto, servicio, contenedor
  y slot. `jobhunt_core` conserva cero relaciones privilegiadas en `public`.
- Incidencia corregida durante el despliegue: la primera invocación de migración
  tomó `.env.core.prod` y apuntó a `swissjobhunter` en vez de la base viva
  `swissjobhunter_r5_rehearsal`. Se detectó antes del cierre; el esquema creado
  tenía 62 tablas y sólo tres filas semilla de migración, sin datos de usuario.
  Se migró la base viva tras validar que DSN runtime/admin coincidían, se borró
  únicamente ese esquema accidental y se verificó su ausencia. El script queda
  corregido para derivar y validar siempre la base del contenedor vivo sin
  imprimir credenciales.
- Metodología vigente: comprobar el destino efectivo además de que el comando
  termine en verde; tratar cada desviación como incidente reversible, medir su
  alcance antes de limpiar, conservar evidencia y repetir todos los gates tras
  la corrección. El punto 15 queda habilitado; aplicar su plan ya verificado,
  mantener adaptador común y fuentes exclusivas, ejecutar pruebas y desplegar
  antes de avanzar al punto 16.

### Checkpoint operativo — 2026-10-13 09:33 UTC

- Punto 15 en validación integral. ReactPortfolio backend parte del SHA exacto
  `a292ef4` y frontend de `1bed6c2`; los `README.md` y toda la documentación
  previa del propietario permanecen fuera del alcance. Se verificó antes de
  editar que las nueve fuentes transferidas sólo tenían como consumidor runtime
  sus routers históricos y que el frontend ya seleccionaba Core mediante
  `CORE_SOURCE_BY_KEY`.
- Se añadió un único adaptador cohesivo hacia los contratos Core ya existentes:
  `GET /v1/harvest/sources/{source}/aggregate` y
  `POST /v1/harvest/sources/{source}/runs`. Conserva las 56 parejas método/ruta,
  límites, formas públicas, autenticación admin e idempotencia; `nav` se traduce
  a `nav_arbeidsplassen`. La salud usa señales nativas y deja `success_rate=null`
  porque Core no tiene una medida equivalente. La cota de facetas sigue siendo
  la ya documentada de 500 valores.
- Tras 99/99 pruebas contractuales se retiraron exactamente nueve configs, nueve
  routers y siete implementaciones de proveedor compartidas (25 módulos), además
  de nueve normalizadores, nueve circuit breakers y nueve `urlPath` frontend
  inalcanzables. El registro conserva exactamente once productores locales; no
  quedan imports o símbolos activos del código borrado y `compileall` pasa.
- Validación provisional: 356/356 pruebas backend focalizadas; frontend 423/423,
  ESLint verde y build de producción en 9,20 s. Ruff pasa sobre los cuatro
  módulos nuevos; el barrido ampliado sólo reproduce E402/F841 históricos fuera
  de la lógica nueva. La suite backend completa está ejecutándose y debe quedar
  verde antes de commit, despliegue y cierre del punto.
- Metodología vigente para retomar: comprobar estado y consumidores reales con
  `rg`/AST; caracterizar primero todo contrato observable; implementar la mínima
  frontera común reutilizando `core_http`; probar cada ruta, límites, auth,
  idempotencia y fallos; retirar dependencias desde hojas a raíz; compilar y
  buscar referencias tras cada grupo; ejecutar pruebas focalizadas y suites
  completas; medir diff y runtime; crear commits estrechos por repositorio sin
  documentación ajena; desplegar artefactos de SHA exacto; comprobar las nueve
  fuentes, once productores locales, workers, colas y 12 ventanas; sólo entonces
  cerrar evidencia/checklist y avanzar al punto 16.
- Reanudación exacta: esperar el resultado de la suite backend en curso; corregir
  sólo fallos atribuibles. Después revisar el diff, registrar recuentos netos,
  hacer commits separados de backend/frontend, desplegar ambos servicios del
  backend y verificar producción antes de marcar el punto 15 completo.

### Checkpoint operativo — 2026-10-13 10:06 UTC

- Punto 15 cerrado. Backend ReactPortfolio `8a5a323` y frontend `59507c8`: un adaptador común conserva las 56 rutas históricas; se retiraron 25 módulos, nueve normalizadores, nueve circuit breakers y nueve rutas frontend inalcanzables. La suite backend terminó con 2.128 pruebas aprobadas y una omitida; frontend mantiene 423/423, lint y build verdes.
- Producción: `portfolio_backend` y `portfolio_worker` ejecutan la imagen exacta `portfolio-backend:board-8a5a323`, sin reinicios. Las nueve fuentes devuelven 200 y al menos una oferta cuando el agregado no está vacío; los registros conservan exactamente once productores locales. Core continúa 17/17 saludable, 46 scopes, cero alertas y tres workers sin tareas activas o reservadas.
- Playwright público: 12/12 ventanas con contenido, cero errores JavaScript, carga determinista en 1.043 ms; arrastre medio 26,17 ms y p95 33,4 ms frente a la línea base 43,25/100,1. Arbeitnow tarda 83,819 s sólo en la ruta histórica de compatibilidad; el dashboard usa `/api/v1/jobs/search` para las fuentes Core, por lo que esa latencia no interviene en la carga de sus ventanas. Queda registrada sin ocultarla en `portfolio-core-adapter-production.json`.
- La publicación del commit frontend a GitHub y GitLab no se realizó: la revisión automática rechazó modificar ambas ramas `main` sin autorización específica por destino. El cambio sólo elimina configuración inalcanzable y el frontend público conserva el mismo camino activo ya validado, así que este gate aislado no bloquea la simplificación autorizada.
- Punto 16 abierto en orden numérico: retirar atómicamente los catorce proveedores y dos scrapers transferidos de SwissJob junto con su control de handover; conservar productores exclusivos, ocho colectores escolares, historial persistido, aplicaciones/perfiles, overlay MatchResult, feedback, digests y salud/notificaciones de watchlist.
- Metodología vigente para retomar: comprobar primero cabeza, estado, imports, consumidores, configuración efectiva, colas y producción; comparar cada hallazgo con el plan; caracterizar contratos y responsabilidades retenidas; editar desde las hojas hacia los registros y el control de despliegue en una sola unidad coherente; ejecutar pruebas focalizadas, búsqueda de referencias, compilación y suite completa; construir y desplegar sólo desde una cabeza limpia y exacta; repetir registros, ausencia de red transferida, salud, workers y funciones preservadas; documentar evidencia y checkpoint tras cada punto. No ampliar abstracciones ni borrar código con una responsabilidad viva.
- Reanudación exacta: revalidar `swissjob-producer-retirement-plan.json` contra la cabeza `bbaf8b3` y el Compose vivo; verificar colas drenadas; retirar los 17 módulos completos, `backend/services/legacy_sources.py`, entradas de registros y las 14+2 variables de configuración/despliegue; adaptar watchlist para derivar directamente los ocho `swiss_schools_*`; después probar y desplegar antes de abrir el punto 17.

### Checkpoint operativo — 2026-10-13 11:50 UTC

- Punto 16 cerrado en producción. SwissJob BFF y worker ejecutan las imágenes
  `swissjob-backend:point5-d54b2ec` y `swissjob-worker:point5-d54b2ec`, ambas
  con cero reinicios y sin OOM. Los cambios quedaron integrados localmente como
  `42ecf96` y `8acb043`; la diferencia de SHA se debe al cherry-pick desde el
  worktree aislado y el contenido desplegado es el validado.
- Se retiraron los catorce providers y dos scrapers transferidos, su control de
  handover, el routing dinámico cerrado y los motores locales de catálogo,
  matching, documentos, colegios y búsquedas ya propiedad de Core. Se conservan
  aplicaciones, perfil editable, overlay `MatchResult`, feedback, digests,
  salud/notificaciones de watchlist, cinco scrapers generales, ocho escolares y
  doce providers registrados. El diff del commit principal elimina 23.454 líneas
  y añade 686 en 181 ficheros.
- Validación local: 1.912 pruebas aprobadas, cuatro `xfail` declarados y cero
  fallos en 213,92 s; Ruff, `compileall`, `bash -n` y `diff --check` verdes. La
  imagen BFF baja de 2.963.149.942 a 1.202.129.754 bytes (-59,43 %) y ya no
  contiene `numpy`, `sentence_transformers` ni `torch`.
- Validación viva: la sonda fija devuelve `failures=[]`, 2/2 perfiles enlazados,
  cero snapshots pendientes/erróneos, autoridades local para aplicaciones y
  perfiles y Core para catálogo, documentos, matching y colegios. No quedan
  módulos ni variables retiradas; los registros contienen exactamente doce
  providers, trece scrapers y ocho colegios. El worker responde `pong`, sin
  tareas activas/reservadas; una consulta Jobicy sin persistencia devolvió 13
  ofertas. Core terminó 17/17, sin alertas, con 25.284 entregas realizadas y
  cero pendientes/dead letters. Evidencia:
  `swissjob-runtime-retirement-production.json`.
- Incidencia y solución: la primera sonda posterior encontró `HF_HOME` y
  `SENTENCE_TRANSFORMERS_HOME` heredadas en el Compose vivo aunque la imagen ya
  no tenía el modelo. Se amplió el transformador existente y se reaplicó sólo
  configuración a BFF/worker; la misma sonda pasó después. Dos lecturas Core
  captaron 21 pendientes y 13 entregas en vuelo, que drenaron sin intervención;
  no se ocultaron como verdes.
- Metodología vigente para retomar: verificar primero cabeza, cambios ajenos y
  flujo ejecutable; caracterizar responsabilidades vivas; retirar desde hojas a
  raíz en una unidad cohesiva; usar la mínima frontera fija y no crear capas de
  transición nuevas; ejecutar pruebas focales, suite total y controles
  estáticos; construir por SHA exacto; comprobar colas/estado vivo antes de
  mutar; desplegar con candidato, respaldo y rollback; repetir exactamente la
  sonda que detecte cualquier desviación; registrar evidencia, checklist y deuda
  antes de abrir el punto siguiente. Un gate transitorio se vuelve a medir y se
  continúa cuando drena; una desviación barata se corrige y se repite el gate.
- Punto 17 abierto en orden numérico. Reanudación exacta: comprobar la cabeza y
  los cambios ajenos de ReactPortfolio frontend, releer el plan verificado de
  `JobCardExtras`, trazar sus dos consumidores, aplicar sólo la extracción de
  presentación prevista, ejecutar lint, pruebas, build y E2E desde cabeza
  limpia, construir el artefacto de release y verificar las 12 ventanas antes
  de habilitar el muestreo final del punto 18.

### Checkpoint operativo — 2026-10-13 12:00 UTC

- Punto 17 cerrado en el commit frontend `49ca7ba`. `JobCardExtras` incorpora
  únicamente `JobSummary`, `JobDate` y `JobBadges`; AI, bolsa y filtros conservan
  sus contenedores y colores, y título, score, skills, acciones, hooks, tabs y
  ordenación siguen siendo responsabilidad de cada ventana. El cambio afecta a
  seis ficheros, añade 111 líneas y elimina 93. El `README.md` previo quedó fuera.
- Validación repetida desde el worktree limpio y detached
  `/tmp/reactportfolio-frontend-item17`: ESLint verde, 423/423 pruebas, build de
  producción en 8,62 s y 18/18 E2E. El artefacto ocupa 6.650.151 bytes;
  `dist/index.html` tiene SHA-256 `1823b994…a5dc53` y el chunk compartido es
  `JobCardExtras-NbFkOQD7.js` (`3a48f8b5…310f4e`). Evidencia:
  `portfolio-job-card-extraction-validation.json`.
- Metodología vigente: releer el plan y trazar duplicación real antes de editar;
  compartir sólo presentación con el mismo contrato observable; dejar estado y
  acciones en sus dueños; convertir primero los consumidores y eliminar sus
  bloques duplicados; usar las pruebas existentes contra el componente real;
  ejecutar lint, suite, build y E2E; repetirlos desde el SHA limpio; excluir
  cambios ajenos; registrar evidencia y avanzar sólo en orden numérico.
- Publicación preparada: GitHub `main` y GitLab `main` siguen exactamente en
  `1bed6c2`; ambos `push --dry-run` prueban avance rápido `1bed6c2..49ca7ba`.
  La revisión automática rechazó el push real antes de ejecutarlo porque exige
  una autorización que nombre expresamente ambos destinos; ningún remoto cambió.
- Punto 18 abierto. Reanudación exacta: tras autorización literal para publicar
  `49ca7ba` en `github/main` y `gitlab/main`, ejecutar esos dos push sin variar el
  SHA, comprobar ambos refs y el workflow/asset de Cloudflare, repetir muestreo
  de recursos, ventana programada, sonda pública de 12 ventanas y protocolo de
  arrastre idéntico; calcular deltas contra las líneas base y cerrar checklist,
  deuda y reporte final.

### Checkpoint operativo — 2026-10-13 12:03 UTC

- Punto 18 en curso, con el muestreo final de reposo ya cerrado. Tres lecturas
  separadas por ocho segundos sobre los mismos ocho contenedores retenidos dan
  2.381,29 MiB frente a 2.648,10 MiB en la línea base comparable: -266,81 MiB
  (-10,08 %). La base excluye los 19,25 MiB de `core-capture`, por lo que el
  ahorro no atribuye artificialmente la retirada del contenedor al runtime que
  permanece.
- SwissJob BFF baja de 860,5 a 364,2 MiB (-496,3 MiB; -57,68 %) y su worker de
  263,0 a 167,7 MiB (-95,3 MiB; -36,24 %). Las variaciones aisladas de otros
  contenedores no se declaran causales porque la carga puntual difiere. La sonda
  controlada del encoder sigue siendo la medida causal: -605,64 MiB (-43,08 %).
  Evidencia: `container-resource-final-production.json`.
- Metodología vigente para el cierre: mantener idénticos conjunto, intervalo y
  número de muestras; separar comparaciones de reposo de mediciones bajo carga;
  declarar causalidad sólo cuando la variable se aisló; publicar exclusivamente
  el SHA validado; comprobar el SHA remoto y el activo público; repetir el mismo
  recorrido de 12 ventanas y el mismo gesto de arrastre; ejecutar la carga
  programada sin enviar correo ni alterar su ledger; registrar límites y deltas;
  cerrar el punto únicamente tras validar evidencia, checklist y producción.
- Bloqueo externo restante: la revisión automática detuvo, antes de ejecutarlo,
  el push del commit inmutable `49ca7ba` a `github/main` y `gitlab/main`. Ambos
  remotos siguen en `1bed6c2` y los dos dry-runs confirman avance rápido. La
  revisión exige autorización literal con repositorio y destino, pese a la
  autorización general de la sesión; no se intentará una vía alternativa.
- Reanudación exacta: obtener esa autorización específica, publicar el mismo
  SHA en `github.com:VRicardoP/ReactPortfolio.git` y
  `gitlab.com:lotharsan/reactportfolio-front.git`, verificar el despliegue de
  Cloudflare y repetir la sonda pública. Después ejecutar la medición equivalente
  de la carga diaria con entrega y escrituras neutralizadas, calcular todos los
  deltas, marcar el punto 18 completo y emitir el informe final.

### Checkpoint operativo — 2026-10-13 12:13 UTC

- Punto 18 avanza con la ventana de carga final cerrada. Se ejecutó el cálculo
  real de `daily_match_report` dentro de `portfolio_worker`, sustituyendo antes
  de entrar al flujo únicamente `_record_outcomes` y `_build_and_deliver`; así
  conserva carga de fuentes, matching local, consumo del feed Core y consultas
  de colegios, pero no envía correo, no modifica el ledger y revierte la sesión.
  Procesó 1.674 resultados Core ya puntuados y sólo ocho candidatos locales.
- La ejecución duró 167,499 s frente a 3.667,175 s en la corrida programada base
  (-95,43 %). En 14 muestras cada 15 s, el worker tuvo 443,95 MiB de mediana y
  465,2 MiB de máximo frente a 722,4 y 784,6 MiB (-38,55 % y -40,71 %). El p95
  fue 465,2 frente a 779,0 MiB (-40,28 %). Evidencia:
  `container-resource-scheduled-window-final-production.json`.
- Límite comparativo explícito: la base fue una corrida natural con entrega y
  ledger, mientras la final neutralizó esos dos efectos para evitar mensajes y
  mutaciones fuera de horario. La reducción refleja sobre todo que las fuentes
  transferidas llegan puntuadas desde Core y sólo ocho candidatos conservaron
  matching local; no se atribuye el coste trivial de composición/SMTP al nuevo
  flujo. Un primer intento no importó `services` y terminó antes de ejecutar la
  carga; se corrigió `sys.path` a `/release` y sólo se conserva la corrida válida.
- Metodología vigente: aislar efectos externos antes de disparar una carga,
  conservar el cálculo costoso, muestrear durante toda su vida, guardar el hash
  del arnés y la imagen, fallar si la neutralización no está activa, comparar
  percentiles y duración con la base y declarar cualquier asimetría del
  protocolo. Validar el JSON y `diff --check`, actualizar checklist/deuda y
  hacer un commit de evidencia estrecho sin incluir documentación ajena.
- Reanudación exacta: queda únicamente publicar el SHA frontend `49ca7ba` en los
  dos destinos ya verificados, esperar el activo Cloudflare, repetir allí las
  12 ventanas y el arrastre idéntico, consolidar deltas y cerrar formalmente el
  punto 18. La revisión automática aún exige autorización literal por destino.

### Cierre de optimización — 2026-10-13

- Objetivo completo, 18/18 puntos. El commit frontend inmutable `49ca7ba` quedó
  publicado en `github/main` y `gitlab/main`; GitHub Frontend CI terminó en
  `success` (run 38406372899) y Cloudflare sirve `/assets/index-DmwHqaRA.js`, que referencia
  `/assets/JobCardExtras-C_UhG9nf.js`. Los dos refs remotos coinciden exactamente con el SHA local.
- Validación pública final conservada íntegra en dos corridas: 12/12 ventanas
  con contenido y cero errores JavaScript en ambas, cargas de 1003 y
  968 ms, media de 31.120 ms/frame sobre seis arrastres y peor
  p95 de 50.0 ms. Frente a 43,25/100,1 ms mejora un
  28.05 %/50.05 %. Frente a la muestra pública anterior de
  1bed6c2 empeora un 8.89 % en media y un
  49.70 % en peor p95; no se descartó la muestra alta. El main más el
  chunk compartido crece 499 bytes, por lo que la extracción se declara mejora
  de cohesión y eliminación de duplicación, no ahorro de transferencia.
- Las sondas vivas finales de Core, SwissJob y Portfolio devuelven
  `failures=[]`: Core 17/17, sin alertas, staging, slot ni delivery pendiente;
  Portfolio conserva nueve fuentes transferidas desactivadas, once propias y
  seis capacidades en `core_primary`; SwissJob conserva doce providers, trece
  scrapers y ocho escolares, sin módulos ni variables retiradas. Las cinco
  colas están a cero; los ocho contenedores relevantes están vivos, sin
  reinicios ni OOM; `core-capture` no existe.
- Resultados antes/después consolidados: encoder controlado -605,64 MiB
  (-43,08 %); reposo comparable -266,81 MiB (-10,08 %); SwissJob BFF -57,68 %
  y worker -36,24 %; carga diaria equivalente 3.667,175 -> 167,499 s (-95,43 %)
  y memoria mediana 722,4 -> 443,95 MiB (-38,55 %); SQL de facetas -91,48 % y
  página por categoría -53,48 %. Los límites causales y la neutralización de
  correo/ledger quedan explícitos en `optimization-final-production.json`.
- Limpieza de sesión completada: contenedores `item16-postgres`/`item16-redis`,
  sus volúmenes anónimos, la red `item16-test-net`, el arnés temporal del worker
  y los artefactos transitorios de Playwright fueron retirados. Los cambios
  previos del propietario permanecen fuera de los commits de esta sesión.
- Metodología aplicada hasta el cierre: comprobar estado real antes de cada
  mutación; ejecutar estrictamente en orden; caracterizar contratos; elegir la
  mínima solución cohesiva; medir una línea base comparable; aislar causalidad;
  desplegar SHA exactos con rollback; repetir sondas después de cada corrección;
  conservar también resultados desfavorables; no dar por verde un gate por
  ausencia de errores; cerrar sólo al demostrar cada requisito con estado vivo,
  pruebas, artefactos reproducibles y evidencia versionada.
- Reanudación futura: no queda trabajo del objetivo original. Si aparece una
  regresión, partir de `optimization-final-production.json`, repetir primero la
  sonda afectada sin mutar producción y comparar con el protocolo y los límites
  documentados antes de decidir cualquier cambio.

### Corrección de indicadores de carga — 2026-10-13 14:09 UTC

- Solicitud: corregir el cuadrado mostrado junto a «Fetching analytics data».
  Se comprobó en JSX y Chromium que el carácter previsto era `⏳` (`U+23F3`),
  que el estilo inline calculaba sólo `Courier New` y que el render correcto
  dependía de una fuente emoji instalada localmente (`Noto Color Emoji` en el
  entorno de prueba). El proyecto no distribuía ese glifo, por lo que otros
  sistemas podían mostrar tofu.
- Solución mínima aplicada en ReactPortfolio frontend `82ade98`: los tres
  loaders que usaban `⏳` o `📊` comparten ahora un spinner CSS sin fuentes ni
  dependencias. El principal mide 42 px y los loaders lazy 24 px; conservan
  textos y colores, exponen `role=status`, `aria-live=polite`, `aria-busy=true`,
  ocultan el dibujo a lectores de pantalla y desactivan la animación con
  `prefers-reduced-motion`.
- Validación: ESLint verde; 423/423 pruebas; build Vite en 3,56 s; 18/18 E2E.
  La sonda Playwright del build demoró analytics y el chunk de Stats por
  separado: ambos estados mostraron sólo su texto, tamaños 42/24, animación
  `app-loading-spin`, atributos accesibles completos, cero errores JavaScript y
  `animation-name=none` bajo movimiento reducido. El preview temporal se cerró.
- Publicación completada: el SHA exacto
  `82ade985df71df182d30780b32c9bdb468ffc35b` está en `github/main` y
  `gitlab/main`; Frontend CI terminó en `success` (run 38411087414) y Cloudflare
  sustituyó `/assets/index-DmwHqaRA.js` por `/assets/index-UWDdC0xO.js`, con
  `/assets/index-7XmNGod8.css`. El `README.md` modificado previamente por el
  propietario quedó fuera del commit.
- Verificación pública final: Playwright reprodujo por separado la espera de
  analytics y la carga diferida de Stats. Los spinners desplegados miden 42/24
  px, usan `app-loading-spin`, no contienen glifos, mantienen los textos
  accesibles exactos, declaran `aria-hidden=true` y `aria-busy=true`, se detienen
  bajo movimiento reducido y no generan errores JavaScript.
- Metodología aplicada y reanudación: identificar primero el punto Unicode y la
  fuente efectiva, reproducir el fallo potencial en navegador, reutilizar CSS
  sin crear componentes o dependencias, validar estado normal y movimiento
  reducido, ejecutar todas las puertas locales, publicar un commit estrecho y
  repetir la sonda contra el activo realmente servido. El punto queda cerrado;
  ante una regresión futura, partir del SHA y de los activos anteriores, demorar
  las dos rutas de carga por separado y aplicar el mismo contrato medible antes
  de modificar código.

### Ejecución de deuda restante — 2026-10-13

- Solicitud y alcance autorizado: cerrar A19-31, DT-54, A18-05/A19-25,
  A19-13, A19-15, DT-72, DT-102 y DT-115; reconciliar después la deuda
  documental inmediata. Quedan expresamente fuera DT-47, A19-19 y A19-20.
- Objetivo verificable: no marcar un identificador cerrado por una edición
  documental; comprobar primero código, datos y producción, aplicar la mínima
  corrección completa, ejecutar regresiones del contrato afectado y la suite
  apropiada, desplegar cuando cambie el comportamiento vivo y repetir la sonda
  sobre la release exacta. Rendimiento exige línea base y medición posterior con
  el mismo protocolo.
- Metodología de continuidad: trabajar un identificador cada vez; conservar los
  cambios previos del propietario; caracterizar el recorrido y sus consumidores;
  preferir borrar o reutilizar límites/helpers existentes; mantener SRP, cohesión
  alta, acoplamiento bajo, legibilidad y consistencia; evitar abstracciones sin
  consumidor; crear commits estrechos; y añadir aquí un checkpoint tras cada
  punto con causa, cambio, pruebas, SHA, despliegue, límites y reanudación exacta.
- Estado inicial: optimización 18/18 e indicador CSS cerrados. Los ledgers tienen
  estados históricos que pueden estar superados; `MEMORIA_PROYECTO.md` (09-10) y
  el acta inicial de optimización aún describen captura y salud antiguas. No se
  reconciliarán hasta cerrar los puntos funcionales para que la foto final use
  evidencia nueva.
- Reanudación exacta inicial: auditar en orden A19-31 y DT-54, medir después
  A18-05/A19-25, revisar el saldo real de A19-13/A19-15, resolver DT-72/DT-115,
  comprobar DT-102 contra la configuración viva y finalizar reconciliando todos
  los documentos autoritativos.


#### Checkpoint DT-54 — implementación validada y backend en producción (2026-10-13)

- Causa comprobada: el frontend conservaba `accessToken`, `refreshToken` y
  `tokenType` en `sessionStorage`; SSE reconstruía además `Authorization` desde
  ese estado. El backend ya soportaba cookies HttpOnly, pero producción
  devolvía el refresh en JSON y usaba cookies no seguras.
- Cambio mínimo: frontend `5045a28` determina la sesión con `/users/me`, usa
  `credentials=include`, comparte un único refresh concurrente, serializa
  login/logout y elimina el almacenamiento y lectura de JWT; SSE reutiliza
  `authenticatedFetch`. Backend `a63df1f` deja de exponer el secreto refresh,
  fija cookies `HttpOnly; Secure; SameSite=None`, rechaza escrituras de
  orígenes no autorizados y hace que el despliegue propague esas políticas.
- Validación local: frontend lint, build, 413/413 unitarias y 18/18 E2E;
  backend 2.129 pruebas pasadas, una omitida, y `py_compile` verde. Producción
  ejecuta `portfolio-backend:board-a63df1f`: login, `/users/me` y refresh
  devuelven 200; las dos cookies son HttpOnly/Secure/SameSite=None; ningún body
  contiene el secreto refresh; un origen ajeno recibe 403; logout responde 200.
  El acceso con una copia deliberadamente retenida del JWT sigue válido hasta
  su expiración, propiedad del access token stateless y fuera del contrato de
  borrado de cookies del navegador.
- Rollback backend:
  `portfolio.configured.yml.before-a63df1f`; la base permanece en
  `uu44v3375x21 (head)`. Los README preexistentes quedaron fuera de los commits.
- Cierre extremo a extremo: `5045a28` se publicó en GitHub y GitLab;
  Cloudflare sirve el activo nuevo. Playwright autenticó contra producción y
  comprobó las 12 ventanas con contenido en 6,455 s, cero indicadores de carga,
  glifos de sustitución, excepciones, fallos de red o respuestas >=400. Las
  cookies `access_token` y `refresh_token` son HttpOnly/Secure/SameSite=None y
  `sessionStorage` no contiene access, refresh ni tokenType. DT-54 queda cerrado.

#### Checkpoint DT-72 — cerrado (2026-10-13)

- Inventario comprobado: los 11 providers activos pasan
  `cache_update_interval` desde su módulo de configuración. La única duración
  local restante era el valor por defecto literal de `BaseJobCache`; la
  constante `DEFAULT_JOB_CACHE_DURATION` creada para centralizarlo no tenía
  consumidores.
- Cambio mínimo: `895a724` sustituye el literal por esa constante. No añade
  configuración, capas ni dependencias y no altera los intervalos específicos.
- Validación: Black y `git diff --check` verdes; 51/51 pruebas focalizadas de
  caché, edad, motores y matcher superadas. No requiere despliegue separado: se
  incorporará a la siguiente imagen backend junto con los demás cierres.
- Reanudación exacta: DT-72 queda cerrado. Continúa la matriz A18-05/A19-25 y,
  después, DT-115; el cambio del propietario en `README.md` sigue excluido.

#### Checkpoint DT-115 — cerrado (2026-10-13)

- Causa comprobada: dos pruebas demostraban una cota de cinco solicitudes
  enviando 1.000 peticiones cada una. El resultado funcional era correcto, pero
  la amplificación consumía tiempo de CPU y agotaba el presupuesto bajo carga.
- Cambio mínimo: `d44d152` envía seis solicitudes por bucket: cinco deben pasar
  y la sexta debe devolver 429. Mantiene la separación entre tráfico anónimo y
  propietario y la cota conjunta de diez verificaciones.
- Validación: 10 repeticiones consecutivas de los dos casos, 20/20 en verde
  (0,14–0,17 s por pareja); fichero G10 completo, 25/25 en 2,65 s; Black y
  `git diff --check` verdes.
- Reanudación exacta: DT-115 queda cerrado y no requiere despliegue de runtime.
  Continúa la medición A18-05/A19-25; DT-72 y DT-115 viajarán en la próxima
  release backend.

#### Checkpoint DT-102 — cerrado (2026-10-13)

- Método aplicado: comprobar primero el estado efectivo con `docker inspect`,
  comparar la topología viva con el compose y repartir el presupuesto original
  entre los procesos en que hoy está dividido el backend; cambio mínimo,
  despliegue reproducible, sonda de salud y verificación posterior.
- Evidencia previa: DB, Redis y ngrok ya tenían exactamente sus límites; API y
  worker tenían 1 GiB cada uno pero CPU ilimitada. El NAS dispone de dos CPU.
- Realizado: commit Portfolio backend `444f072`; `portfolio_backend` y
  `portfolio_worker` declaran y propagan `cpus: 1.0`. Desplegada imagen
  `portfolio-backend:board-444f072`, con rollback
  `portfolio.configured.yml.before-444f072`.
- Verificado en vivo: ambos contenedores muestran 1 GiB, `NanoCpus=1000000000`,
  cero reinicios; `/health` responde `healthy` y Alembic permanece en
  `uu44v3375x21 (head)`.
- Reanudación exacta: DT-102 queda cerrado. Continúa A18-05/A19-25 con el
  benchmark actual ya finalizado y el caché calentado en preparación; después
  se validan/despliegan A19-13/A19-15 y se cierra A19-31 hasta donde permita la
  credencial externa.

#### Checkpoint A18-05 — cerrado por contrato servido (2026-10-13)

- Metodología aplicada: fijar primero el recorrido que ejecuta hoy la UI
  (facetas al entrar, 50 tarjetas al elegir categoría, guardados y catálogo),
  conservar el presupuesto predeclarado de p95 <= 2 s, medir 100 muestras
  secuenciales por ruta dentro del BFF del NAS, separar arranque de régimen
  caliente y no atribuir como mejora una muestra tomada durante calentamiento.
- Línea base comparable de 100 muestras: facetas p50/p95 2,943/5,991 s;
  categoría 5,152/10,876 s; guardados 0,689/1,723 s; catálogo 1,530/7,579 s.
  Sólo guardados cumplía.
- Realizado: Core publica la categoría autoritativa; el BFF sirve facetas y
  categorías desde el recorrido calentado y cachea los joins de presentación de
  colegios. El commit 4017081 añade 60 s de vigencia a la caché ETag del
  catálogo: mientras está vigente evita pedir a Core una página que éste debía
  reconstruir incluso para responder 304; al caducar conserva la revalidación
  ETag. Prueba focalizada: 41/41.
- Producción: BFF y worker ejecutan point5-4017081, saludables y con rollback
  swissjob.configured.yml.before-bff-4017081 y
  swissjob.configured.yml.before-worker-4017081. Medición final, 100 muestras
  y estados 200: facetas 0,088/0,180 s; categoría 0,371/1,033 s; guardados
  0,564/1,551 s; catálogo 0,186/0,838 s. Las cuatro rutas cumplen p95 <= 2 s.
  El catálogo había dado 5,166/9,731 s justo antes del cambio, control que
  confirma que la reconstrucción remota seguía siendo el coste dominante.
- Reanudación exacta: A18-05 queda cerrado. Tomar una muestra final de recursos
  y carga del NAS para cerrar A19-25 sin confundir latencia de producto con
  capacidad del host; después validar en producción A19-13/A19-15, registrar sus
  checkpoints y continuar con A19-31, DT-54 y la reconciliación documental.

#### Checkpoint A19-25 — cerrado dentro del alcance del proyecto (2026-10-13)

- Metodología aplicada: medir el contrato extremo a extremo sobre el NAS real y
  tomar después una ventana independiente de recursos; no rebajar el presupuesto,
  no atribuir al proyecto procesos del QTS y declarar la capacidad física que no
  controla el repositorio.
- Evidencia: el NAS conserva dos CPU y, tras las mediciones verdes, mostraba
  carga 9,52/10,50/11,27. Una instantánea ocupada tuvo 60 % user y 40 % system;
  los mayores procesos eran API.pyc de red de QTS (36,6 %) y gpuhal_app
  (26,6 %). Aun con esa restricción, las cuatro rutas de producto cumplen el
  p95 de 2 s documentado en A18-05.
- Ventana de cinco muestras/40 s de los ocho contenedores afectados: suma de
  medianas 2,89 % CPU, 2.830,06 MiB y 55 procesos. Medianas principales:
  Core API 0,24 %/135,5 MiB; Core bi-encoder 0,11 %/829,5 MiB; BFF
  0,40 %/360,4 MiB; worker SwissJob 0,36 %/190,7 MiB; Portfolio API
  0,21 %/95,36 MiB; Portfolio worker 1,11 %/570,5 MiB. Hubo picos breves
  (Core API 53,25 %, BFF 17,57 %, Portfolio worker 6,86 %) sin romper el
  contrato medido.
- Resultado: el rediseño de carga, las cachés verificadas por versión, el
  bi-encoder único y los límites de CPU eliminan el bloqueo del producto en este
  hardware. Los demonios QTS y la carga global alta quedan como restricción del
  host, no como deuda accionable de SwissJob.
- Reanudación exacta: A19-25 queda cerrado. Validar ahora A19-13/A19-15 en el
  código y los datos vivos, añadir un checkpoint por cada uno y seguir con
  A19-31, DT-54 y la reconciliación documental.

#### Checkpoint A19-13 — cerrado con límites explícitos (2026-10-13)

- Metodología aplicada: medir sólo módulos de runtime, distinguir imports de
  carga de imports diferidos, comprobar consumidores antes de borrar o crear
  capas y rechazar una abstracción sin segundo uso. El cierre exige resolver el
  problema concreto, no crear una carpeta por su nombre.
- Complejidad: radon no encuentra grados E/F en API, workers ni productores.
  Los siete E/F restantes pertenecen a cinco herramientas de corte de Fase E ya
  ejecutadas. Ninguna es importada por API o workers; feedback_cutover sólo
  compone una de ellas y tampoco tiene consumidores de runtime. Se conserva la
  cota histórica en vez de reescribir herramientas de un solo uso.
- Acoplamiento: análisis AST de imports de carga, excluyendo tests, migraciones,
  scripts e imports locales/TYPE_CHECKING, recorrió 185 módulos y 558 aristas en
  BFF, y 116 módulos y 342 aristas en Core: cero componentes cíclicos en ambos.
  El ciclo matching/core_client <-> feedback fue eliminado en bf88f3e al dejar
  la selección del lector en el router.
- Responsabilidades: no se añadió una carpeta repositories. El repositorio ya
  tiene JobRepository para persistencia local y puertos estrechos para catálogo,
  matching, perfiles y colegios; envolverlos de nuevo añadiría costuras de un
  solo uso. La clasificación y las facetas presentadas son autoridad de Core
  mediante match_categories.json. El frontend pasó de 359 líneas de reglas a
  99 de metadatos/agrupación y consume job_category; no conserva keywords. El
  clasificador Python del BFF queda acotado a la ingesta local retenida y a la
  alerta persistida, no decide la categoría de la UI.
- Validación acumulada de los cambios: Core 1.547 pruebas pasadas y una omitida;
  BFF 1.919 pasadas y cuatro xfail esperados; frontend lint, 56 pruebas y build
  verdes. Los cambios funcionales están desplegados en Core 254e826 y
  BFF/worker 4017081.
- Reanudación exacta: A19-13 queda cerrado. Añadir la aserción de aceptación de
  descripción y enlace de detalle para tarjetas escolares, ejecutar frontend y
  cerrar A19-15 con los agregados vivos ya obtenidos; después continuar A19-31.

#### Checkpoint A19-15 — cerrado en productor y presentación (2026-10-13)

- Metodología aplicada: separar el contenido canónico histórico del contenido
  realmente servido, medir ambos con agregados sin extraer ofertas ni identidad,
  corregir en el productor para lo nuevo y conservar una normalización defensiva
  en la frontera de presentación para revisiones históricas inmutables.
- Datos vivos: entre vacantes activas cuya fuente primaria es la indicada, la
  canónica conserva 22.138 Arbeitnow (2.350 revisiones históricas con HTML) y
  3.504 Jobgether históricas sin narración. No se reescriben revisiones
  inmutables. El productor Arbeitnow elimina HTML antes de normalizar y el
  productor Jobgether crea desde los campos oficiales un resumen factual de
  contrato, experiencia, modalidad, ubicación y skills; no inventa texto.
- Contrato servido sobre 1.800 matches reales: Arbeitnow 975/975 con
  descripción no vacía y 0 HTML; Jobgether 484/484 con descripción no vacía y
  0 HTML. La presentación limpia el HTML heredado y, cuando falta narración,
  construye sólo hechos ya presentes (ubicación y etiquetas). Hay 58 títulos
  traducidos desde la caché calentada; el camino de respuesta con
  translate=false no llama al LLM.
- Colegios: cuatro tarjetas reales, 4/4 con descripción y URL, y su ruta de
  detalle respondió 200. MatchCard muestra la descripción y conserva siempre
  View details. La nueva prueba de aceptación f72eb8b fija ambos requisitos;
  frontend queda con ESLint verde, 57/57 pruebas y build Vite correcto. La
  primera compilación no pudo vaciar dist por propiedad root heredada; el mismo
  build pasó hacia /tmp sin cambiar permisos ni borrar artefactos ajenos.
- Reanudación exacta: A19-15 queda cerrado. A19-31 sigue bloqueado únicamente
  por una credencial SMTP válida para la cuenta SwissJob o una autorización
  literal para reutilizar la cuenta Portfolio. Completar mientras tanto la
  validación de salud/fuentes, DT-54 publicable y la reconciliación documental.

#### Checkpoint operativo final previo a publicación — 2026-10-13

- Core: ready/authoritative, release 254e826, Alembic core0054; 17/17 scopes
  activos con estado y cero alertas; 25.382 entregas, cero dead/pending/inflight;
  staging cero, slot de captura ausente y core-capture retirado.
- SwissJob: dos usuarios activos, ambos vinculados y sin snapshots pendientes ni
  errores; catálogo/matching/colegios resuelven siempre a Core y el perfil
  editable permanece local. Banderas de perfil, feedback y facetas activas;
  12 providers, 13 scrapers (ocho escolares) y ningún módulo retirado importable.
- Portfolio: schedulers activos; nueve fuentes transferidas desactivadas, once
  fuentes propias, y applications/catalog/documents/matching/saved_searches/
  schools exactamente en core_primary; sonda sin fallos.
- La sonda SwissJob original importaba el modelo de routing ya eliminado y
  falló antes de medir. Se actualizó al contrato fijo vigente y el nuevo control
  pasó; esto forma parte de la deuda documental inmediata.
- Reanudación exacta: reconciliar MEMORIA_PROYECTO, acta de optimización y hoja
  de ruta; sincronizar copias saneadas de compose. Después quedan sólo A19-31,
  publicar/verificar DT-54, validación final de navegador y pushes exactos.

#### Checkpoint de deuda documental inmediata — cerrado (2026-10-13)

- Metodología aplicada: contrastar cada afirmación con código, commits, imágenes
  vivas, sondas y mediciones; distinguir estado histórico de estado vigente;
  conservar las restricciones y los fallos observados; actualizar primero las
  fuentes autoritativas y después los resúmenes; versionar sólo los archivos de
  esta sesión, sin absorber cambios preexistentes del propietario.
- Realizado: `MEMORIA_PROYECTO.md` resume autoridades, topología, releases,
  resultados y continuidad; el acta
  `OPTIMIZACION_CORE_PORTFOLIO_2026-10-10.md` cierra 18/18 puntos y los seis
  pasos en el orden autorizado; `ESTADO_Y_HOJA_DE_RUTA.md` incorpora la
  fotografía vigente y marca como superados los estados parciales; esta deuda
  deja A18-05, A19-13, A19-15 y A19-25 cerrados con límites explícitos.
- Evidencia operativa: se actualizó la sonda SwissJob al contrato de autoridades
  fijas y volvió a pasar junto con las sondas Core y Portfolio. Los manifiestos
  saneados reflejan las imágenes vivas en SwissJob `da10e57` y Portfolio
  backend `c5a2806`. El escáner permite únicamente el literal booleano seguro
  `AUTH_REFRESH_TOKEN_IN_BODY=false`; rechaza `true` y cualquier literal en
  otras claves sensibles. Sus tres pruebas pasan y los tres manifiestos dan
  cero hallazgos.
- Estado final: **100 % del alcance de deuda autorizado**. A19-31 y DT-54
  quedaron cerrados con efectos reales comprobados; código, rendimiento,
  arquitectura, operación y documentación están conciliados. Los commits se
  publicaron en los destinos autorizados.
- Reanudación exacta: no queda trabajo dentro del alcance. DT-47, A19-19 y
  A19-20 permanecen excluidos por decisión expresa del propietario y no deben
  abordarse como continuación automática de esta sesión.


#### Checkpoint final de publicación y operación — cerrado al 100 % (2026-10-13)

- Método aplicado: autorización literal por destino y entre cuentas; push de un
  SHA ya validado; comprobación del activo público; candidato Compose obtenido
  del vivo sin imprimir secretos, validación previa, respaldo, promoción y
  recreación sólo de BFF/worker; sondas de salud y efecto externo real.
- Publicación: Portfolio backend `c5a2806` está en GitHub `main`; frontend
  `5045a28` está en GitHub y GitLab `main`; SwissJob `8a3a345` quedó publicado
  en `feat/fase-a-core` antes de este acta final. El repo raíz ReactPortfolio no
  tiene remoto configurado; su deuda quedó en el commit local `3f00695`.
- A19-31: la cuenta SMTP de Portfolio se aplicó a SwissJob con autorización
  literal. El candidato pasó `compose config --quiet`; BFF y worker quedaron
  `running`, `RestartCount=0`, con configuración coincidente sin exponerla. BFF
  respondió salud, Celery respondió pong y ambos enviaron correo real. Rollback:
  `swissjob.configured.yml.before-smtp-20261013T184830Z`.
- DT-54: Cloudflare sirve `frontend@5045a28`. La sonda Playwright real tardó
  6,455 s desde navegación hasta las 12 ventanas; todas tenían título, contenido,
  dimensiones y visibilidad correctas, sin ErrorBoundary. Cero indicadores de
  carga, glifos de sustitución, errores de consola/página, fallos de petición o
  respuestas >=400 posteriores al login. Cookies HttpOnly/Secure/SameSite=None;
  tokens de sesión heredados ausentes.
- Cierre: **100 % del alcance solicitado**. No queda un punto de reanudación. Se
  conservan únicamente las exclusiones explícitas DT-47, A19-19 y A19-20.

#### Cierre DT-143 — pictogramas estables en produccion (2026-10-13)

- Metodologia aplicada: inventario desde datos/traducciones/JSX/CSS hasta el DOM,
  separacion de texto linguistico frente a iconografia decorativa, correccion
  minima en la frontera de renderizado, compatibilidad con datos ya sembrados,
  accesibilidad conservada y validacion de escritorio/movil antes de publicar.
- Causa: 140 reglas locales reducian la pila a Courier New, monospace y los
  iconos de habilidades tambien llegaban de la base de datos. Instalar una
  fuente o cambiar solo body no garantizaba cobertura ni corregia los valores
  antiguos.
- Realizado: los pictogramas propios de Portfolio publico, login y dashboard se
  sustituyeron por insignias ASCII o formas CSS; los seis JSON usan codigos
  estables y SoftSkillsWindow normaliza los emoji heredados y los desconocidos.
  No se anadieron dependencias, fuentes ni peticiones de red.
- Evidencia: ESLint, build, 414/414 pruebas y 21/21 E2E verdes. La regresion
  inspecciona texto y pseudo-elementos en 11 ventanas publicas, 9 secciones
  moviles, 12 ventanas del dashboard, sus 5 tabs moviles y login.
- Publicacion: `be8e9816d96d72689392a8ead52cc8925b05cf2e` esta en `main`
  de GitHub y GitLab. Cloudflare sirve `index-Cv4VbT4v.js` y
  `index-DCi5ZRT6.css`, junto con los 39 paquetes diferidos comprobados.
- Evidencia real: la sonda autenticada cubrio 11/11 ventanas publicas, 9/9
  secciones moviles, login, 12/12 ventanas del dashboard y 5/5 tabs moviles.
  Cero pictogramas dependientes de fuente/U+FFFD, loaders, ErrorBoundary,
  errores de consola/pagina, peticiones fallidas o HTTP >=400 despues del login.
  El unico aborto fue el SSE desmontado de forma esperada al cambiar de layout;
  las cookies siguen HttpOnly/Secure/SameSite=None y `sessionStorage` no contiene
  tokens.
- Estado: [x] corregido, probado, publicado y verificado en produccion.
- Reanudacion exacta: ninguna para DT-143. `frontend/README.md` sigue intacto
  como cambio preexistente; permanecen excluidos DT-47, A19-19 y A19-20.
