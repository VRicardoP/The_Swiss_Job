# Estado del proyecto y hoja de ruta — `jobhunt-core`

> **Documento vivo.** Consolida en un solo sitio: dónde estamos, qué se ha decidido, qué está
> roto y en qué orden se va a arreglar.
>
> **📍 SI CONTINÚAS EL PROYECTO, EMPIEZA POR EL §43 (lo último).**
> Última escritura: **2026-09-21, actualización documental; trabajo funcional pausado**.
> ⚠ **Este documento es un DIARIO: cada sección es una foto fechada y NO se reescribe.** Las cifras
> de una sección valen para su fecha y nada más — las de §0 y §1 son del **2026-08-06/07**, las de
> §18.9 (`backend 1097`, `frontend 315`) del 25-08. **Para actuar manda §43 y sus actas
> enlazadas. No reutilizar contadores de una foto anterior sin re-medirlos.**
> §19 fue superado por §20; §21 corresponde a la mañana del 04-09 y fue superado
> por §22–§25. Esas secciones se conservan como historial, no como instrucciones
> operativas vigentes ni como certificación de calidad actual.
>
> Complementa (no sustituye) a los documentos contractuales:
> `PLAN_UNIFICACION_JOBHUNTING.md` (qué se construye) · `ADR_JOBHUNTING.md` (por qué así) ·
> `BACKLOG_UNIFICACION_JOBHUNTING.md` (tickets) · `RUNBOOK_CUTOVER_PILOTO.md` (cómo se corta) ·
> `jobhunt_core/shadow/RUNBOOK.md` y `.../DEPLOY_NAS.md` (operación de la sombra).

---

## 21. Estado vigente — cierre de matching y camino a la unificación total (2026-09-04)

### 21.1 Qué ya está fusionado

- **Fase C consumada en producción:** ReactPortfolio usa el core para catálogo/matching
  (`core_read`) y applications/saved_searches (`core_primary`).
- `cosine-baseline:v1` mantiene un feed productivo funcional. El NO-GO actual afecta a la
  promoción de un ranker aprendido y a la certificación, no a la disponibilidad del producto.
- El corpus, CDC, dedup, embeddings, API y costuras están construidos y han sido desplegados en
  etapas anteriores. Las cifras históricas de §20 no deben copiarse sin re-medición.

### 21.2 Matching: estado exacto

- Último punto formal medido: RankNet n=279, P1=0.5097 y P2=0.5063; umbral predeclarado
  0.60/0.60. Holdout intacto y racha sin abrir.
- HEAD comunicado del core: `809e24a`; suite completa declarada 1036/1036. Las tres regresiones
  nuevas se reejecutaron focalmente sobre el árbol actual: 3/3.
- `809e24a` cierra las reproducciones de sello de generación, worker G1 terminando después de G2
  y desactivación del propio modelo. La revisión posterior deja **dos cierres P1 antes de
  promoción**: mantener bloqueada la fila de `corpus_generation` hasta el commit y comprobar el
  modelo canónico exacto bajo el mismo protocolo que su activación, no solo que el antiguo siga
  activo.
- Entrenamiento v10 (~436 juicios) y benchmark OpenVINO en NAS: comunicados como en curso por el
  ejecutor; el siguiente agente debe comprobar proceso, release, logs, artefactos y resultado, sin
  reiniciarlos a ciegas.

### 21.3 Qué falta para la unificación

1. Cerrar los dos P1 anteriores y sus interleavings; suite completa sobre el código actual.
2. Desarrollo >=0.60/0.60 + benchmark NAS con paridad; después holdout único, promoción y racha
   7/7. Los siete ciclos diarios imponen **168 h mínimas** desde el primer cierre elegible.
3. **Fase D:** poblar mapas/perfiles, migrar o recomputar durables y activar el canary por perfil de
   SwissJob, apagando matching/alertas legacy por perfil.
4. **Fase E:** migrar documentos y colegios.
5. **Fase F:** portar/retirar harvesters restantes, drenar CDC/outboxes, probar backup/restore y
   retirar el motor y tablas legacy tras la retención acordada.

El procedimiento completo, invariantes, pruebas, tiempos y DoD están en
`SwissJob/PROMPT_CIERRE_TOTAL_UNIFICACION_2026-09-04.md`. “GO de calidad”, “SwissJob sobre core” y
“unificación completa” son hitos distintos; no volver a publicar un “GO” sin apellido.

---

## 0. Última actualización — DESPLIEGUE DEL TRACK V (2026-08-07, 12:11)

Todo el trabajo de fiabilidad del dato está **corriendo en el NAS**. Desplegado por CLI
(`bin-docker-compose -p swissjob -f docker-compose.sombra.yml up -d`), 3 min 45 s de recreación de
`backend`/`worker`/`frontend`; **postgres y los 5 contenedores del core NO se tocaron** — la sombra
siguió capturando sin un solo corte (16 h de uptime, slot activo durante toda la operación).

| Verificación post-despliegue | Resultado |
|---|---|
| `alembic_version` | **`d3e5a91c74b2`** — 4 migraciones aplicadas (la de `source_health` + las 3 legacy pendientes) |
| Tablas nuevas | `source_health`, `jobhunt_routing`, `jobhunt_profile_map` + `jobs.content_hash` |
| ⚠ Chromium en la imagen | **verificado EN EL NAS** (lanza v151), no solo en local — la trampa `INSTALL_BROWSERS` esquivada |
| `source_health` | **15 fuentes** con veredicto: 7 `ok`, 8 `empty`, 0 `error` |
| **Colegios desaparcados** | **los 8 consultados** pese a `consecutive_empty_runs=7`; la cosecha reportó **`skipped: 0`** (antes saltaba 12 fuentes) |
| **Ofertas recuperadas** | **17 de 5 colegios** (nae 9 · ecolint/hautlac/iscs/isp 2 c/u) que no se estaban recogiendo |
| Legacy | frontend `:4000` → 200, cosecha 207 descargadas / 140 nuevas |

**Estado de la sombra ahora:** embeddings **17 000 / 19 284 (88,2 %)** · evaluaciones **3 600** ·
`shadow_inbox` **3 600** · outbox 3 600 · 2 ciclos con métricas (06 y 07). El pipeline funciona de
punta a punta: captura → proyección → embeddings → evaluaciones → entrega.

**Dato nuevo de confianza:** `cleanup_stale_jobs` borró 61 ofertas del legacy y **el CDC capturó los
61 como eventos `D`**. Primera validación en producción de que la sombra maneja borrados.

**⚠ Evidencia OBSERVADA de VD.2/VD.3** (ya no son hipótesis): la cosecha terminó con
`{'errors': 4, 'fetch_failed': 0, 'unhealthy': []}` — cuatro ofertas que NO se pudieron guardar y
**cero señal de fuente degradada**, porque la salud mira la descarga y no la persistencia. Además
esas 4 identidades ya entraron en el cursor sin haberse guardado.

**Pendiente del propietario:** devolver el stack a la UI de Container Station (§6.1) — ahora que las
imágenes están cargadas y el YAML valida, es posible y eliminaría el riesgo operativo nº 1.

---

## 1. Dónde estamos (2026-08-06, 21:03)

**Fase B (sombra) DESPLEGADA EN PRODUCCIÓN (NAS QNAP).** Se ejecutó el paquete
`jobhunt_core/shadow/DEPLOY_NAS.md` completo: ventana de mantenimiento, arranque ordenado §6,
bootstrap §7 y checklist §9.

| Componente | Estado verificado |
|---|---|
| Postgres | `swissjob-postgres-core:pg16`, `wal_level=logical`, slots/senders = 4 |
| CDC (`core-capture`) | Slot `jobhunt_shadow` **activo**, streaming wal2json, heartbeat al día |
| Backfill | **23 817 = 23 817** filas del legacy (DoD B-01 cumplido) |
| Proyección | **19 284 vacantes** + revisiones + **2 perfiles** proyectados |
| Embeddings | **1 400 / 19 284 (7 %)**, ~14/min ⇒ **~22 h** para cubrir el corpus |
| Evaluaciones / `shadow_inbox` | 0 — van detrás de los embeddings |
| `core-migrate` | `Exited (0)`: roles, GRANTs RO, aislamiento exhaustivo, cadena `core0023` |
| `core-worker` | beat vivo, 4 cadencias despachando, transporte sombra → `jobhunt.shadow_inbox` |
| `core-api` | `/v1/ready` → 200 (sin puerto de host) |
| **Contrato §0** | **CUMPLIDO**: legacy intacto, frontend `:4000` → 200, cero efectos a usuarios |

**GATE-SOMBRA: NO ha empezado.** Su contador exige `labels_ready` (≥2 sets de etiquetas
congelados) y no existe ninguno. No hay nada que "parar": la sombra mide, el gate aún no cuenta.

> ⚠ **El stack quedó desplegado por CLI, fuera del control de la UI de Container Station.**
> Ver §6 "Riesgos operativos abiertos".

---

## 2. Decisiones tomadas

### 2.1 Qué se descarga y con qué ventana temporal → **ADR-10**

Decisión del propietario del 2026-08-06, motivada por los datos reales del corpus (§3.1):

- **Cosecha inicial de una fuente: SOLO lo publicado en la SEMANA EN CURSO.** No "todo lo activo"
  ni "todo lo de los últimos 180 días".
- **Después: cosecha periódica incremental** (cursor por scope de ADR-05 + presupuesto/early-stop).
- **EXCEPCIÓN — `swiss_schools_*`: SOLO en el BOOTSTRAP.** Partiendo de BD en blanco se bajan
  TODAS las activas, sin ventana. Después NO son especiales: periódica e incremental, solo
  novedades, igual que el resto de portales. Son pocas (61 en el corpus) y de rotación lenta, así
  que la ventana semanal perdería el histórico vigente — pero solo la primera vez.

**Bloqueante conocido:** hoy **no existe fecha de publicación** en el sistema (`public.jobs` solo
tiene `first_seen_at`/`last_seen_at`; el core no guarda `posted_at`). `first_seen_at` **no vale de
sustituto**: es cuándo lo vio nuestro crawler. La decisión está tomada pero **no es aplicable**
hasta cerrar el ticket V.1. Detalle completo en ADR-10.

### 2.2 Estrategia de ejecución → **ADR-11**

Se descartó explícitamente la alternativa "construirlo todo y probar/conectar al final"
(integración big-bang). Se mantiene el Strangler Fig del plan, con estos matices:

- **La sombra se queda corriendo** mientras se desarrolla el resto. No consume tiempo de
  desarrollo y acumula ciclos en paralelo.
- **Conectar un consumidor real al `/v1` cuanto antes** (Portfolio, solo lectura). Es la única
  validación que ninguna prueba sintética cubre.
- **Primero fiabilidad del dato, luego medición, y solo después cortar.**
- **Durante los 7 ciclos del gate se congela también el CÓDIGO**, no solo las etiquetas.

Motivo y análisis de riesgo en ADR-11.

### 2.3 Orden de trabajo acordado

Bloques 0 → 4 de la §5. La regla que los ordena: **no medir sobre datos en los que no se confía.**

---

## 3. Fallos encontrados (todos verificados, no supuestos)

### 3.1 Composición del corpus — el supuesto de partida era falso

Medido sobre el corpus real del NAS (23 813 ofertas, 25 fuentes):

- **Rango temporal = 66 días** (2026-06-01 → 2026-08-05): toda la vida del despliegue. La
  retención de 180 d **nunca ha recortado nada**.
- **10 191 ofertas (43 %) llevan >30 días sin volver a verse**, pero **23 060 de 23 813 siguen
  `is_active = true`** (753 inactivas). **`is_active` no es señal de vigencia.**
- Concentración: `arbeitnow` + `ostjob` = **56 %** del corpus.

### 3.2 Cobertura de fuentes — 18 de 43 registradas no traen NADA

9 con causa esperada: 5 restringidas (partner) + 4 sin API key (`adzuna`, `careerjet`, `jooble`,
`jsearch`). **Las otras 9, diagnosticadas en vivo el 2026-08-06:**

| Fuente | Causa REAL (sonda en vivo) |
|---|---|
| `authenticjobs` | RSS **HTTP 404** — feed muerto |
| `dailyremote` | RSS **HTTP 404** — feed muerto |
| `translatorscafe` | RSS **HTTP 404** — feed muerto |
| `proz` | RSS **HTTP 403** — bloqueo anti-bot |
| **`zebis`** | RSS **HTTP 403** — bloqueo anti-bot ⚠ *portal de docencia suiza: el perfil objetivo* |
| `remoteco` | **Timeout** — el host no responde |
| `gastrojob` | Kill-switch de compliance: 3 bloqueos, **nunca un éxito** (jun-2026) |
| `swiss_schools_isb` | Kill-switch de compliance: 3 bloqueos, **nunca un éxito** (jun-2026) |
| `stelle_admin` | `is_allowed=t` y "éxito" el 19-jul, pero **0 ofertas** |

*(`swiss_schools_zis` funcionaba hasta el 13-jul; también lo apagó el kill-switch.)*

### 3.2bis ⚠ 12 de 15 scrapers APARCADOS por el backoff — incluida toda la watchlist de colegios

Descubierto el 2026-08-06 al investigar las fuentes mudas. Estado de `source_cursors` en el NAS:

| Scrapers | `consecutive_empty_runs` | Último run |
|---|---|---|
| `financejobs`, `myscience`, `gastrojob`, `stelle_admin` y **los 8 `swiss_schools_*`** | **6** | **2026-08-01** (5 días antes) |
| `tes` | 3 | 08-04 |
| `irishjobs`, `schuljobs` | 0 | 08-05 (diario) |

Es el backoff del `CrawlerBudgetService` funcionando **según lo diseñado**: a partir de 3 runs
vacíos el intervalo se duplica por cada vacío extra, con tope 4× ⇒ de diario a cada 4 días.

**El fallo de diseño:** el backoff penaliza a las fuentes de BAJA ROTACIÓN, que son justo las que
no se pueden perder. Un colegio publica una vacante cada varios meses ⇒ `avg_new_jobs_per_run=0.0`
⇒ backoff máximo PERMANENTE. El sistema aparca precisamente las fuentes cuyo evento raro es el más
valioso para el perfil objetivo (docencia).

**Decisión del propietario (2026-08-06): opción "eximir la watchlist del backoff".** Implementada
como atributo del scraper (`WATCHLIST_SOURCE`), no como prefijo mágico en el servicio: el
`CrawlerBudgetService` no debe conocer nombres de fuentes. Los 8 colegios exentos; los otros 7
scrapers siguen con backoff. **Exime SOLO de la frecuencia**: el early-stop y el presupuesto de
páginas siguen aplicando, así que la cosecha sigue siendo incremental de solo novedades (ADR-10).
⚠ `swiss_schools_isp` necesitó marcado manual: no hereda de `SwissSchoolBaseScraper` (usa la API
de Workday), y se habría quedado aparcado mientras los otros 7 se consultaban a diario.

### 3.3 **EL FALLO SISTÉMICO: los errores se presentan como éxitos**

Los seis providers de arriba devuelven **`OK 0 ofertas`**. Un 404, un 403 y un feed legítimamente
vacío producen **el mismo resultado observable**. Por eso llevan 66 días mudos sin que salte nada.
El kill-switch lo agrava: apaga tras 3 bloqueos y **no reintenta nunca** — `gastrojob` e
`swiss_schools_isb` jamás han funcionado.

Es el mismo patrón que apareció tres veces más el mismo día (§3.4): **fallos que se presentan como
éxitos**. Atacarlo es el bloque 0 de la hoja de ruta.

### 3.4 Defectos del despliegue (solo visibles ejecutando en el NAS)

| # | Defecto | Efecto | Estado |
|---|---|---|---|
| 1 | Volumen `core_hf_cache` nace **`root:root`**; el core corre como uid 100 | `PermissionError` → **embeddings parados 1,5 h** con todo `Up`/`healthy` | ✅ `chown 100:101` |
| 2 | Healthcheck de `core-capture` con `timeout: 10s`; el comando tarda **32 s** en el NAS | `unhealthy` permanente con el CDC sano | ✅ 60 s / interval 120 s |
| 3 | Consulta GDPR del §9 filtra `src_table='public.users'`; el valor real es `users` | **Devuelve vacío en vez de fallar**: falso OK en la verificación de PII | ⏳ corregir el runbook |
| 4 | `docker-compose.qnap.yml` del repo referenciaba `swissjob-worker:prod` inexistente | `pull access denied`: el compose **no era desplegable** | ✅ alineado con lo desplegado |
| 5 | Healthcheck del frontend usa `localhost` → `::1`; nginx solo escucha IPv4 | `unhealthy` **16 351 veces** sirviendo HTTP 200 | ✅ `127.0.0.1` (pendiente de aplicar) |

**Ninguno es un bug de lógica: los cinco son defectos de entorno.** Es la justificación empírica
de ADR-11: no aparecen en dev, solo ejecutando en producción.

### 3.5 Trampas y deudas abiertas

- ⚠ **`swissjob-backend:prod` DEBE construirse con `INSTALL_BROWSERS=true`.** El worker ejecuta
  los scrapers Playwright. La imagen del NAS lo lleva (verificado, Chromium 149), pero
  `docker-compose.prod.yml:84` construye el backend esbelto: un rebuild desde ahí **rompe todo
  scraper Playwright en silencio**.
- **Deuda PF.4**: worker único con `concurrency=2` puede cargar 2 copias del modelo. La separación
  `worker-ai` del repo (que nunca se desplegó) existía para dejarlo en 1.
- **Corpus del NAS 3 migraciones por detrás** del head legacy (`f7a9c1e2b3d4` vs `c81f4d2e9a57`).
  No bloquea la sombra (`content_hash` no es `required`; las de A.SEAM son de Fase D).
- **El oráculo del gate no es viable con los datos actuales**: 18 filas de feedback en producción
  frente a los **≥30 juicios × 2 perfiles congelados** que exige el DoD B-03 ⇒ **curación manual
  obligatoria** (§8 del DEPLOY_NAS de la sombra).

---

## 4. Tiempos

- **Suelo irreducible del GATE-SOMBRA: 7 ciclos diarios CONSECUTIVOS en verde.** El contador lee
  hacia atrás desde el último ciclo cerrado y **corta con un ciclo rojo, sin computar o
  recomputado tras sellado** ⇒ un mal día **reinicia a cero**, no pausa.
- Embeddings del corpus actual: **~22 h** desde el 2026-08-06 21:00.
- Ruta completa hasta poder congelar: bloques 0-2 (≈1 semana de trabajo) + curación manual (tu
  tiempo) + **7 días de conteo** ⇒ **~2 semanas** si nada se tuerce.
- **Regla de congelación**: desde que se congelan los sets hasta que el gate pasa, **no se
  redespliega el core**. Un cambio en embeddings/scoring/dedup a mitad de medición invalida la
  comparabilidad, y un ciclo recomputado corta la cuenta igual que uno rojo.
- ⚠ **Y la congelación incluye las ETIQUETAS, no solo el código** (§8.1): `labels_ready` y las
  métricas §6 se calculan sobre el set congelado **MÁS RECIENTE** de cada perfil, no sobre
  "cualquier set válido". Añadir o re-etiquetar un set a mitad de la cuenta **cambia el set
  efectivo**, la medición salta a él y, si queda por debajo del umbral del oráculo, los gates
  por-perfil hard-fallan (semántica ratificada: set no medible ⇒ rojo) ⇒ **racha a cero**.
  Corolario práctico: **curar bien A LA PRIMERA**. Un segundo set "mejorado" a mitad de camino
  no mejora nada — reinicia los 7 días.
- Corolario: **aplicar el compose corregido por la UI se hace ANTES de congelar**, nunca durante.

---

## 5. Hoja de ruta — qué se hace y en qué orden

### Bloque 0 — Observabilidad de fuentes ✅ **HECHO (providers) — 2026-08-06**
> Multiplica todo lo demás: hasta que el dato no sea fiable, cualquier métrica se construye sobre arena.

1. ✅ **Un fetch fallido ya FALLA**: `utils/fetch_diagnostics.py` (nuevo) recoge el fallo definitivo
   vía `contextvars` — elegido para NO tocar la firma de `fetch_jobs()` en 28 providers + 15
   scrapers. `classify()` devuelve `ok` | `empty` | `error`. `utils/http.py` registra el último
   status (404/403…) o el error de red al agotar reintentos.
2. ✅ **Salud por fuente**: modelo `SourceHealth` + `services/source_health.py` + migración
   `d3e5a91c74b2`. Separada de `SourceCompliance` a propósito (permiso ≠ funcionamiento; compliance
   solo cubre scrapers). Rachas de error y de vacío **contadas por separado**: piden acciones distintas.
3. ✅ **Alerta** por racha: `SOURCE_HEALTH_ERROR_STREAK=3` / `SOURCE_HEALTH_EMPTY_STREAK=5`.
   El pipeline loguea `FUENTE DEGRADADA <x> — <motivo>` y lo acumula en `summary["unhealthy"]`.
4. ✅ **Reintento del kill-switch**: `COMPLIANCE_RETRY_AFTER_HOURS=24`. `can_scrape` es PURO (sin
   efectos); la rehabilitación duradera la hace `reset_blocks` al primer éxito. `robots_txt_ok=False`
   NO se reintenta: es una prohibición, no un fallo.

**Verificación:** 13 tests nuevos (`tests/test_source_health.py`) + suite completa **982 passed**
(969 previos + 13). `ruff` limpio.

5. ✅ **Cubre providers Y scrapers**: `record_and_alert()` en `services/source_health.py` es el
   punto de entrada único de los dos pipelines. En scrapers importa el doble, porque el early-stop
   incremental hace que "0 ofertas" sea un resultado NORMAL y ahí un fallo pasaba aún más inadvertido.
6. ✅ **Migración aplicada en dev** y **verificada reversible** (downgrade borra la tabla, upgrade
   la recrea). **Pendiente de aplicar en el NAS.**

**Verificación EXTREMO A EXTREMO contra portales reales (2026-08-06):**

| Fuente | Veredicto | Ofertas | Racha error | `last_error_detail` |
|---|---|---|---|---|
| `arbeitnow` | `ok` | 375 | 0 | — |
| `zebis` | **`error`** | 0 | **3** → alerta | `HTTP 403 en https://www.zebis.ch/stellen/stelleninserate/rss` |

Esa misma ejecución, antes de V.0, producía `OK 0 ofertas` y no dejaba rastro alguno.

> ⚠ **Hallazgo colateral: `tests/` es FLAKY, y por DOS causas distintas** (§8.6 — no confundirlas):
> 1. Métodos de servicio que hacen `commit()` dentro de la transacción del fixture, expirando
>    objetos ORM de otros tests (`Could not refresh instance`).
> 2. **Operativa**: dos suites contra la MISMA BD de tests se deadlockean en el `TRUNCATE` del
>    teardown y producen fallos en tests ajenos, **indistinguibles de (1)**.
>
> **Corrección de atribución (2026-08-06):** los `3 failed / 979 passed` de la primera pasada de V.0
> se achacaron a la causa (1), pero encajan con la (2): esa suite corría en segundo plano MIENTRAS
> se lanzaban ejecuciones de `test_compliance.py` en primer plano. Las dos pasadas posteriores, ya
> sin concurrencia, dieron **982 passed / 0 failed**. La causa (1) existe y está documentada, pero
> **no está probado que interviniera aquí**.
>
> **Regla práctica: una suite cada vez.** Y antes de diagnosticar flakiness, comprobar
> `pg_stat_activity` y que no queden procesos `pytest` vivos en el contenedor — `docker compose exec`
> los deja corriendo aunque se mate el cliente (un `pytest` fantasma dejó clavada una suite entera).

### Bloque 1 — V.5: las 9 fuentes mudas — 🔬 **DIAGNOSTICADAS EN VIVO (2026-08-06)**

> Sondas reales contra cada portal. La estimación previa ("4-6 recuperables") era **optimista**.

| Fuente | Diagnóstico REAL | ¿Arreglable? |
|---|---|---|
| `zebis`, `proz` | **Cloudflare** (`Just a moment...`) en TODO el sitio, con UA de navegador también — no es bloqueo por User-Agent | ❌ requiere `SCRAPER_BROWSER_CDP_URL` (browser stealth de pago), igual que `medjobs` |
| `remoteco` | Handshake TLS que nunca completa (bloqueo por huella TLS) | ❌ igual |
| `authenticjobs` | El feed de ofertas ya no existe. `/feed/` responde 200 pero es el **blog** (artículos editoriales); WordPress no expone tipo de contenido "job" | ❌ sin scraper nuevo — usarlo contaminaría el corpus |
| `dailyremote`, `translatorscafe` | Feeds retirados: 404/403 en todas las rutas probadas | ❌ sin scraper nuevo |
| `gastrojob`, `swiss_schools_isb` | Sitio vivo (200) pero el listado se carga **por JS**: el HTML estático no trae ni un enlace de oferta | ⚠ sí, reescribiendo con Playwright |
| **`stelle_admin`** | **FUNCIONA: 7 ofertas** al ejecutarlo a mano | ✅ pero 0 filas almacenadas — **sin explicar** |

**Corrección de un diagnóstico anterior:** `gastrojob` y `swiss_schools_isb` **no estaban
bloqueados**. Tienen los selectores obsoletos → devuelven 200 sin datos → el detector de soft-block
lo interpreta como anti-bot → reporta bloqueo → a los 3, el kill-switch los apaga. Un bucle de mal
diagnóstico, de la misma familia que §3.3: **la señal medida no era la realidad**.

**➡ TODO ESTO QUEDA APARCADO** como **TRACK V-DIFERIDO** del backlog (decisión del propietario,
2026-08-06): se implementa **al finalizar el proyecto**. Motivo: **ninguno bloquea la
CONSTRUCCIÓN**.

> ⚠ **CORREGIDO el 2026-08-20 (novena revisión externa de la Fase 3).** Aquí decía que "ninguno
> bloquea el GATE-SOMBRA" porque "el corpus que el gate mide son las 19.284 vacantes ya proyectadas"
> y arreglar fuentes "no cambia lo que se está midiendo". **Ambas cosas son falsas.** 19.284 es una
> foto del 2026-08-07: hoy hay **5.953 vacantes** en el core frente a **9.173 ofertas legacy
> activas** (medido el 2026-08-20). Y el corpus **no está congelado** — el elegible son las vacantes
> VIVAS con embedding y cada transición incrementa `corpus_generation`, que reactiva la evaluación
> de perfiles. Una fuente recuperada **sí** puede mover el top-K. **Lo correcto:** no bloquea la
> construcción, pero **su despliegue cambia el corpus vivo** y debe desplegarse y drenarse ANTES de
> iniciar la racha 7/7 definitiva. (Corrección mínima y marcada: este documento lo mantiene otro
> agente.) Todo está diagnosticado en vivo,
así que no habrá que reinvestigar: VD.1 `stelle_admin` (causa raíz encontrada) · VD.2 y VD.3 (dos
fallos SISTÉMICOS descubiertos por el camino) · VD.4 reescrituras Playwright · VD.5 bloqueos de
infraestructura, con la vía de **alertas por email** como recomendada y el enrutado por LLM
**RECHAZADO y documentado** para que no se reabra · VD.6 feeds retirados.

### Bloque 1 (continuación) — trabajo restante
Con el bloque 0 hecho es mecánico: localizar los feeds nuevos de los tres 404; aplicar
`services/scraper_stealth.py` a los dos 403 (la misma capa que resucitó `myscience` y
`financejobs`); diagnosticar el 0-ofertas de `stelle_admin`; reactivar las apagadas por kill-switch
**después** de corregirlas.

### Bloque 2 — V.4: caducidad real ✅ **HECHO — 2026-08-06**

> **La propuesta original se DESCARTÓ tras mirar los datos.** Iba a cerrar ofertas por "N barridos
> sin verla" (`last_seen_at`), pero el % de ofertas que se refrescan varía **del 3 % (`jobgether`)
> al 92 % (`schuljobs`)** según cómo pagine cada fuente: un umbral uniforme habría cerrado ofertas
> VIVAS en masa. `last_seen_at` mide profundidad de crawl, no vigencia.

**Lo que sí había: `tasks.check_job_urls` ya existía** — sondea la URL real de cada oferta (HEAD,
follow-redirects) y la marca `is_active=False` solo con 404/410. Señal directa, no inferida. Estaba
**mal calibrada**: 200 ofertas/SEMANA ⇒ **~2,3 años** por pasada completa de 19k activas. Por eso
solo 272 de 23 813 se habían comprobado jamás.

Cambios (decisión del propietario: pasada completa cada 7 días):
- `MAINTENANCE_URL_CHECK_LIMIT`: 200 → **3000**
- Cadencia: semanal (domingos) → **diaria** a las 03:00 CET
- **Concurrencia por HOST = 2** (nueva): con 3000 sondas/día y `arbeitnow`+`ostjob` = 56 % del
  corpus, el límite global de 10 no bastaba — un solo portal recibiría ~1200 peticiones a ráfagas
  y podría bloquearnos, que es justo el fallo que estamos arreglando en otras fuentes.

**Verificación:** 32 tests de mantenimiento/scheduler en verde, `ruff` limpio.
**Pendiente:** desplegar al NAS (hoy solo está en el repo).

### Bloque 3 — Conectar el Portfolio al `/v1` en SOLO LECTURA — 🔄 **PASO 1 HECHO, resto BLOQUEADO**
C.0 y C.1 ya estaban hechos. Sin flip, sin migración de datos, sin riesgo. Es lo único que valida el
contrato de la API contra un cliente real (ADR-11).

- ✅ **Paso 1 (2026-08-07):** consumidor `portfolio` + credencial de SOLO LECTURA
  (`vacancies:read`, `matches:read`) provisionados **en el core del NAS**. `key_id =
  a02cbff1481dee59`; secreto en `/share/Public/swissjob/.core.portfolio.key` (chmod 600).
  **NO emitir otra**: el secreto no es recuperable de la BD y habría dos vigentes.
- ⏸ **Pasos 2-4 BLOQUEADOS** esperando una edición del propietario en Container Station.
  **Detalle completo, con el YAML exacto y por qué no vale ningún atajo: §9.2.**

### Bloque 4 — Curar, congelar y contar
Solo cuando 0, 1 y 2 estén cerrados y el corpus deje de moverse: curación manual (§8.2), freeze
(§8.3), y a partir de ahí **7 días sin tocar el core**.

### Después
Flip de Fase C (Portfolio) → Fase D (SwissJob, canary por perfil) → E (colegios/docs) → F (retirada).

### En paralelo y sin bloquear
V.1 (`published_at`) y V.2 (ventana semanal de ADR-10): gobiernan la cosecha futura, no la equidad
de la comparación del gate.

---

## 6. Riesgos operativos abiertos

1. **El stack del NAS se gestiona por CLI, no por la UI.** Se desplegó con
   `bin-docker-compose -p swissjob -f docker-compose.sombra.yml up -d` porque el validador de la UI
   rechazó el YAML en su momento.
   > **Causa CORREGIDA (2026-08-07):** no eran los `service_completed_successfully` —
   > `docker-compose.sombra.yml` los lleva y **SÍ valida** (comprobado por el propietario). La
   > diferencia era la **imagen inexistente** `swissjob-worker:prod` del `qnap.yml` de entonces:
   > Container Station no construye, así que exige que toda `image:` esté ya cargada.
   > ⇒ **La UI vuelve a ser vía válida**, y devolver el stack a su gestión eliminaría este riesgo
   > entero. Requiere pegar el YAML actual y aplicar (con las imágenes cargadas primero).
   **Pulsar "Recreate" en la UI revertiría a la definición vieja** (sin sombra, `wal_level=replica`)
   y **con el slot lógico presente el Postgres NO ARRANCA**. Ver §10.2 del DEPLOY_NAS de la sombra.
   - El nombre de proyecto `swissjob` es **obligatorio**: reutiliza `swissjob_pgdata`. Con otro
     nombre, compose crea volúmenes vacíos y la BD aparece en blanco.
   - **Nunca borrar la aplicación en la UI** para "recrearla limpia": puede llevarse los volúmenes.
2. **Pendiente**: validar el `docker-compose.qnap.yml` corregido en la UI (pegar + *Validate*, que
   no aplica nada) para saber si el rechazo lo causan los 3
   `condition: service_completed_successfully`. Variante sin ellos preparada.
3. **WAL retenido por el slot**: umbral de alerta 2 GiB. En reposo se mantiene en pocos MB.

---

## 7. Próximos pasos inmediatos

| # | Paso | Quién |
|---|---|---|
| 1 | Bloque 0: observabilidad de fuentes (error ≠ vacío + salud + alerta + reintento del kill-switch) | Claude |
| 2 | Bloque 1: arreglar las 9 fuentes mudas | Claude |
| 3 | Decidir el criterio de caducidad de V.4 (nº de barridos sin ver) | **Propietario** |
| 4 | Pegar el YAML corregido en la UI y pulsar *Validate* | **Propietario** |
| 5 | Bloque 3: Portfolio → `/v1` en solo lectura | Claude |
| 6 | Curación manual de los sets de etiquetas (§8.2) | **Propietario** |
| 7 | Freeze + 7 días de conteo, con el core congelado | ambos |

---

## 8. Traspaso desde la sesión del MOTOR (2026-08-05/06) — para quien opera el NAS

> Escrito por la sesión que hizo la revisión integral del motor, el cierre de residuales pre-Fase D
> y la Fase D. Solo lo que **no se deduce leyendo el código** y puede morder en el NAS.

### 8.1 ⚠ Trampa de MEDICIÓN del gate — lo más importante de esta lista

`labels_ready` y las métricas §6 se calculan sobre el set congelado **MÁS RECIENTE** de cada perfil,
no sobre "cualquier set válido" (se corrigió así en la ronda 2 de la revisión integral: antes el gate
abría con un set viejo mientras medía sobre otro nuevo de 1 juicio). Consecuencias operativas
durante los 7 días:

- **Añadir o re-etiquetar un set a mitad de la cuenta cambia el set efectivo** y la medición salta a
  él. Si ese set nuevo queda por debajo del umbral del oráculo, los gates por-perfil **hard-fallan**
  (semántica RATIFICADA: set no medible ⇒ rojo) y **un ciclo rojo REINICIA la racha a cero**.
- Por tanto la regla "congelar y no tocar" incluye **las etiquetas**, no solo el código.

Es un residual conocido y decidido: cambiarlo alteraría una semántica §6 ratificada y con tests.

### 8.2 La señal de recuperación depende de TRIGGERS de BD — mismo patrón "fallo que parece éxito"

Desde `core0022`/`core0023` la versión del corpus es un contador monotónico mantenido por triggers
(`jobhunt.corpus_generation` + `bump_corpus_generation()`). **Si no avanza, un CV nuevo deja de
re-evaluarse sin ningún error, en silencio** — exactamente la familia de fallos del §3.3.
Verificación en `DEPLOY_NAS.md` §9 (existe la fila, hay ≥4 triggers, y la generación AVANZA con el
backfill). Hueco conocido y cerrado en core0023, pero que reaparecería con scripts manuales: **una
escritura dirigida DIRECTAMENTE a una partición de `offer_embeddings` NO dispara el trigger del
padre** (verificado contra PG16). `register_model` crea el trigger en cada partición que él cree;
una partición creada a mano por SSH se lo saltaría.

### 8.3 Nunca bajar la cadena del core por debajo de `core0021` sin purgar `profile_recovery_state`

El downgrade de `core0020` reconstruye su columna con `now()`, lo que **APAGA el trabajo de
recuperación pendiente** (perfiles con CV nuevo sirviendo matching viejo). Los downgrades de
`core0021`/`core0022` ya vacían la tabla, pero si el sistema se deja CORRIENDO en un escalón
intermedio vuelve a poblarse. Precondición ya escrita en `RUNBOOK_CUTOVER_PILOTO.md` §0.

### 8.4 Aplicar V.0 en el NAS arrastra 3 migraciones legacy más — y son baratas

`d3e5a91c74b2` (source_health) cuelga de `c81f4d2e9a57`, así que el `alembic upgrade head` del NAS
aplicará también: `0a84258328f8` (jobs.content_hash), `b7d1a5c9e402` (**jobhunt_routing**) y
`c81f4d2e9a57` (jobhunt_profile_map). **Comprobado que ninguna necesita ventana**: un `add_column`
sin backfill y dos `create_table` vacías (con un índice sobre tabla nueva). Sobre 23 817 filas no
hay reescritura. Efecto colateral bueno: deja la BD lista para el gate de Fase D.

### 8.5 Fase D ya viaja dentro de la imagen legacy — inerte, pero con una condición

D.1 (tareas periódicas) y D.2 (`/analyze`) consultan `jobhunt_routing` y omiten los perfiles ya
servidos por el core. Con la tabla **vacía** el comportamiento es idéntico al de hoy (ausencia de
fila ⇒ modo `local`). Pero **imagen y migraciones van juntas**: desplegar la imagen nueva sin aplicar
`b7d1a5c9e402` haría fallar las tareas periódicas al leer una tabla inexistente. El entrypoint corre
`alembic upgrade head`, así que el flujo normal ya lo cubre; el riesgo es solo si alguien las salta.

### 8.6 La flakiness de `tests/` tiene DOS causas distintas — no confundirlas al depurar

1. La documentada en §5 (commits dentro de la transacción del fixture, `Could not refresh instance`).
2. **Operativa**: dos suites contra la MISMA base de datos de tests se deadlockean en el `TRUNCATE`
   del teardown (`AccessExclusiveLock` vs `RowExclusiveLock`) y producen fallos en tests ajenos,
   indistinguibles de (1). Agravante: **`docker compose exec` deja el `pytest` VIVO dentro del
   contenedor aunque se mate el cliente** — hay que matarlo recorriendo `/proc` dentro del
   contenedor. Un `pytest` fantasma de 3 h dejó clavada una suite entera al 15 %.

Regla práctica: **una suite cada vez**, y antes de diagnosticar flakiness comprobar
`pg_stat_activity` y que no queden procesos `pytest` en el contenedor.

### 8.7 Menor: la consulta GDPR del §9 del DEPLOY_NAS sigue mal en el checklist

Está diagnosticada en su Apéndice B.3 (filtra `public.users`; el valor real es `users`), pero el
checklist §9 conserva la versión que **devuelve vacío en vez de fallar**. Conviene corregirla in
situ para que nadie la ejecute de nuevo y la dé por buena.

---

## 9. TRASPASO al siguiente agente (escrito 2026-08-14)

> Escrito por la sesión que desplegó la sombra en el NAS y ejecutó el TRACK V (bloques 0, 2 y el
> paso 1 del 3). **Va a continuar otro agente sin esperar a los resultados del NAS.** Aquí está lo
> que NO se deduce leyendo el código ni los commits.

### 9.0 ⚠ LO PRIMERO: los números de este documento son de 2026-08-07, no de hoy

Las cifras de §0 (embeddings 17 000/19 284, evaluaciones 3 600, 2 ciclos con métricas) son una foto
del **7 de agosto**. Desde entonces la sombra ha seguido corriendo sola y **nadie las ha vuelto a
medir**. NO las cites como estado actual: vuelve a consultarlas antes de decidir nada. Lo esperable
es que los embeddings estén al 100 % y haya ~7 ciclos más en `shadow_cycle_metrics`, pero **eso es
una expectativa, no un dato**.

### 9.1 Acceso al NAS — cómo se hace (y el problema que te vas a encontrar)

- IP `192.168.1.2`, usuario `Ricardo`. **No hay clave SSH instalada**: la contraseña se pide con
  diálogo gráfico —
  `SSH_ASKPASS=/usr/bin/ssh-askpass SSH_ASKPASS_REQUIRE=force setsid -w ssh ...` — que funciona
  porque los comandos corren sin tty. Multiplexar con
  `-M -o ControlPath=/tmp/claude-1000/nas.sock -o ControlPersist=4h` para pedirla UNA sola vez;
  **al expirar las 4 h la maestra muere sin avisar** y el siguiente comando falla con
  `Permission denied`.
- ⚠ **El 2026-08-14 la conexión falló con `Host key verification failed`**, que NO es lo mismo que
  la maestra expirada. **No lo aceptes a ciegas**: puede ser que el NAS regenerara sus claves (p.ej.
  tras una actualización de QTS) o algo peor. Verifícalo con el propietario antes de tocar
  `known_hosts`.
- `docker` NO está en el PATH: vive en
  `/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker`. `Ricardo` es del grupo
  `administrators` y el socket es `rw` de grupo ⇒ funciona sin sudo.
- **No hay plugin `compose`** en el NAS. Se copió el binario estático de dev a
  `/share/Public/swissjob/bin-docker-compose`.

### 9.2 Estado del bloque 3 (Portfolio → `/v1`) — PARADO, esperando al propietario

**HECHO (no lo repitas):** el consumidor `portfolio` y su credencial **ya existen en el core del
NAS**, con scopes de SOLO LECTURA `["vacancies:read","matches:read"]` y `key_id = a02cbff1481dee59`.
**El secreto está en `/share/Public/swissjob/.core.portfolio.key` (chmod 600) y no es recuperable de
la BD** (solo se guarda su sha256). **NO emitas otra credencial**: tendrías dos vigentes para el
mismo consumidor sin saber cuál usa nadie. Si hiciera falta rotarla, revoca la vieja con
`jobhunt_core.credentials.revoke_credential(session, key_id)`.

**BLOQUEADO en la acción del propietario:** añadir al servicio `backend` de la app
`porfolio_backend` en Container Station (`Applications → porfolio_backend → editar YAML`):

```yaml
    environment:
      - CORE_CONSUMER_KEY=<valor de /share/Public/swissjob/.core.portfolio.key>
    networks:
      - default            # ⚠ IMPRESCINDIBLE: sin él, backend pierde su db y su redis
      - swissjob-core
networks:
  swissjob-core:
    external: true
    name: swissjob_core-net
```

**Por qué hace falta la edición de YAML y no vale un atajo** (se intentó y se descartó):
`portfolio_backend` **no monta ningún fichero de configuración** — todo son variables de entorno
fijadas al crear el contenedor. Y `CORE_CONSUMER_KEY` solo puede venir de ahí: por diseño, vacía ⇒
**el cliente del core no emite ni una petición** (arranque local seguro). Como cambiar env exige
recrear, y un recreate se lleva por delante un `docker network connect`, la vía "conectar la red a
mano sin tocar el compose" **no funciona**. Está descartada; no la reintentes.

**QUEDA, una vez el propietario aplique lo anterior:**
1. Verificar que `portfolio_backend` resuelve y alcanza `core-api:8000`.
2. Insertar la fila de routing en `core_read` para UNA capacidad (catálogo) en la tabla
   `jobhunt_routing` **de la BD del Portfolio** (es local al BFF, no del core).
3. Comprobar el canario contra el corpus real. Es la primera vez que un consumidor real ejercitaría
   el contrato del `/v1` — la validación que ADR-11 identifica como insustituible.

Contexto útil: los modos de routing son `local` | `shadow` | `core_read` | `core_primary` |
`rollback_pending` (`ReactPortfolio/backend/services/routing.py`), y **sin fila explícita el
comportamiento es `local`**, así que nada de esto cambia lo que ven los usuarios.

### 9.3 Reglas de operación que NO se pueden saltar

- **Una suite de tests cada vez** (§8.6). Dos suites concurrentes contra la misma BD de tests
  producen fallos en tests ajenos indistinguibles de bugs reales. Esta sesión se autoengañó con eso:
  atribuyó 3 fallos a un bug de fixtures cuando la causa era su propia concurrencia.
- **Durante los 7 ciclos del gate se congela el CÓDIGO Y las ETIQUETAS** (§4 y §8.1). Un set nuevo o
  re-etiquetado a mitad de la cuenta cambia el set efectivo y puede reiniciar la racha a cero.
- **Rebuild del backend ⇒ `INSTALL_BROWSERS=true`** (§3.5). `docker-compose.prod.yml:84` lo
  construye esbelto; hacerlo mal deja al worker sin Chromium y rompe TODO scraper Playwright **en
  silencio**. Verifícalo DENTRO de la imagen cargada en el NAS, no solo en local.
- **El stack `swissjob` se gestiona por CLI**, no por la UI (§6.1). Pulsar Recreate ahí lo revertiría
  a la definición vieja y, con el slot lógico presente, **el Postgres no arranca**. Devolverlo a la
  UI es posible y recomendable (el YAML ya valida), pero es acción del propietario.

### 9.4 El patrón que más daño ha hecho en este proyecto

**Fallos que se presentan como éxitos.** Aparecieron SEIS variantes en dos sesiones: el volumen
`root:root` que paró los embeddings con todo en `healthy`; el healthcheck de 10 s imposible; la
consulta GDPR que devolvía vacío en vez de fallar; el healthcheck del frontend rojo desde hacía
meses sirviendo 200; los providers que devolvían `OK 0 ofertas` con un 404 debajo; y el backoff que
aparcaba las fuentes más valiosas *funcionando según lo diseñado*.

Cuando algo "no da datos pero no da error", **sospecha de la señal antes que del sistema**. Y quedan
dos variantes conocidas SIN arreglar, documentadas como VD.2 y VD.3 del backlog: el cursor aprende
identidades de ofertas que nunca se guardaron, y la salud de fuente mide la descarga pero no la
persistencia — observado en producción con `errors=4` y `unhealthy=[]` a la vez.

### 9.5 Qué está verificado y qué no

**Verificado en producción:** el despliegue del TRACK V (§0), los 8 colegios desaparcados con 17
ofertas recuperadas, `source_health` poblada con las 15 fuentes, el CDC capturando borrados, y el
pipeline de la sombra de punta a punta.

**Escrito pero NO verificado:** que los embeddings hayan terminado; que los ciclos posteriores al 7
de agosto estén en verde; que el chequeo de URLs diario (V.4) haya empezado a desactivar ofertas
caducadas — su primera ejecución era esa madrugada y **nadie ha mirado el resultado**.

**Nunca ejecutado:** el GATE-SOMBRA. No hay ni un set de etiquetas congelado, así que su contador
está a cero y seguirá ahí hasta que el propietario cure a mano (§8 del `DEPLOY_NAS.md` de la
sombra). Con 18 filas de feedback en producción, las semillas automáticas no llegan a los 30
juicios por set que exige el DoD.

---

## 10. Sesión del 2026-08-14 — trabajo OFFLINE sobre el legacy (sin acceso al NAS)

> Escrito por la sesión que continuó el proyecto **sin conexión al NAS**, mientras las pruebas de la
> sombra siguen su curso. Todo lo de aquí está en el repo y **nada se ha desplegado**.
> Método por fase: implementación → dos rondas de revisión adversarial con revisores independientes
> y lentes distintas → correcciones → suite completa → commit.

### 10.1 Qué se ha cerrado

| Commit | Qué |
|---|---|
| `ab03024` | **VD.1 + VD.2 + VD.3** — el bucle que dejaba fuentes mudas |
| `926d814` | **V.1** — `published_at` real en el modelo normalizado (24 fuentes) |
| `11d69be` | **V.2 + V.3** — ventana de cosecha con política explícita por fuente |

Suite del backend legacy: **984 → 1103 passed**, 3 skipped, `ruff` limpio. Sin desplegar.

### 10.2 Por qué VD.1/VD.2/VD.3 se adelantaron al TRACK V-DIFERIDO

Porque **no son tres tickets, son un bucle**: `stelle_admin` descargaba 7 ofertas, guardaba 0 por
colisión de clave única, el cursor aprendía las 7 URLs igualmente, el early-stop las daba por
conocidas para siempre, la fuente devolvía 0 novedades, el backoff la aparcaba… y `source_health` la
marcaba `ok`. Arreglar una sola pata deja el bucle abierto. Coste real: pequeño, y cierra la
variante más dañina del §9.4.

### 10.3 Lo que ADR-10 pedía y no era aplicable tal cual — **leer ADR-10bis**

Dos precisiones tomadas **por delegación** y registradas en el ADR:

1. **Ventana móvil de 7 días**, no semana natural (con la natural, un bootstrap en lunes por la
   mañana captura ocho horas y el resultado depende del día de arranque).
2. **La ventana se aplica SIEMPRE y solo a las ALTAS**, no solo en el bootstrap. La letra de ADR-10
   se apoya en que "después manda la cosecha incremental" y **eso hoy no se cumple**: el pipeline de
   providers no tiene cursor ni early-stop, y son 20 de las 23 fuentes con ventana. Con la lectura
   literal, el run siguiente al bootstrap volvía a guardar justo lo que la ventana acababa de
   descartar. Era una función que parecía implementada y no lo estaba — el patrón del §9.4 otra vez,
   esta vez en nuestro propio código.

⚠ **Operativa que no es obvia:** el cursor de los scrapers ahora aprende las identidades descartadas
por antigüedad. **Apagar `HARVEST_WINDOW_ENABLED`, reclasificar una fuente a `FULL` o subir el número
de días exigen vaciar `recent_identities` de las fuentes afectadas**, o esas ofertas no se
re-descargarán. El SQL está en el docstring del módulo y en el propio mensaje de alerta.

### 10.4 Lo que hay que MIRAR cuando esto llegue al NAS

- **Los contadores `window_skipped` de la primera semana**, sobre todo en `zebis`, `schuljobs` y
  `tes` (perfil objetivo), para decidir si 7 días es el número correcto. ⚠ En los **providers** ese
  contador es **flujo por run, no ofertas únicas** (la misma oferta se re-descarta cada día hasta que
  el portal la retira): sobreestima la pérdida real por un factor de ~7 a 23. En los scrapers sí se
  acerca a ofertas únicas.
- **El ERROR de "posible deriva de identidad"**. Es la alerta más importante que se ha añadido: si
  `ostjob` o `arbeitnow` cambian su esquema de URLs, todas las re-vistas pasan a parecer altas, se
  descartan por viejas, dejan de refrescar `last_seen_at` y **el cleanup las borra a los 60 días**.
  Antes esa deriva solo producía duplicados, que son recuperables.
- **Orden de despliegue**: migrar ANTES o a la vez que la imagen. Con la imagen nueva sobre la BD sin
  migrar, el SELECT del ORM incluye columnas que no existen y **toda la observabilidad de salud
  degrada a `None` en silencio**. El entrypoint del backend aplica `alembic upgrade head`, pero el
  del worker no: arrancar backend primero.

### 10.5 La sombra del NAS NO se ve afectada por esto — verificado, no supuesto

`published_at` es una columna nueva en `public.jobs`, que se replica al core por el slot lógico. El
capturador (`jobhunt_core/shadow/capture.py`) usa **lista blanca** de columnas: la nueva ni entra al
staging ni produce error, y el proyector nunca la ve, así que el `content_hash` del sink no cambia y
no hay revisiones espurias. Tampoco añade tráfico WAL (cada cosecha ya emitía un UPDATE por oferta
vista). **La mitad del core de V.1 queda como ticket V.1c**, con su trampa ya documentada: al añadir
la columna a `TABLE_WHITELIST`, las filas que ya no se re-tocan no emitirán UPDATE y su histórico no
llegará por streaming.

### 10.6 Hallazgos nuevos, verificados y anotados en el backlog

- **VD.7 — `financejobs` está roto**: lee `props.initialProps.pageProps.jobsSSR.jobs` y la ruta real
  del portal es `props.pageProps.jobsSSR.jobs` ⇒ devuelve lista vacía siempre. Sonda en vivo.
  Explica que esté entre los 12 scrapers aparcados. Arreglo de una línea.
- **VD.8 — el filtro tech tiene el mismo defecto que se corrigió para la ventana**: salta re-vistas
  sin comprobar si ya están guardadas, así que una oferta cuyo título pase a casar con una palabra
  tech deja de refrescarse y acaba borrada a los 60 días. **No se ha tocado**: cambiarlo altera qué
  ofertas viven en el corpus, y eso es decisión del propietario.
- **VD.9 — `zebis` respondió con items y `pubDate`** el 2026-08-14, pese a figurar como muda por 403.
  Es el portal de docencia suiza: merece re-verificarse. Y **`thehub`** está activa en el registro
  pero su API devolvió una página de error.

---

## 11. Sesión 2026-08-22 — el gate tenía un bloqueo PERMANENTE, encontrado y arreglado

> Verificado en vivo contra el NAS antes de tocar nada. Corrige además dos datos de la auditoría
> del 21-08 que ya no eran ciertos.

### 11.0 Estado real medido (no el documental)

- **B-1 RESUELTO operativamente**: los 10 contenedores llevan 2 días arriba; staging **0
  pendientes** (eran 22.550), última aplicación 22-08 02:03. El drenado de 3 semanas de atraso
  **no dañó nada**: slot `active/reserved` con solo **53 MB** de WAL retenido, 563 GB libres.
  `legacy:zebis` y `legacy:irishjobs` **ya existen** en `jobhunt.sources` (la auditoría decía que no).
- **El gate corre y FALLA**: 17 ciclos con métricas, contador **0/7**, los 7 últimos en FALLO.
- **Sin oráculo**: `labeled_sets` y `labeled_dedup_pairs` **VACÍAS en el NAS**. El §6.1 de la deuda
  ("oráculo caducado") describe dev: en producción el oráculo **nunca existió**. `labels_ready=0`,
  `dedup_precision/recall=-1` (centinela de no-medible), `ndcg@10` sin evaluar.
- **F-2 peor que lo documentado**: core **27.717 activas** vs legacy 23.748 — ~**4.000 de más**
  (la auditoría decía 914): al drenar el atraso, el barrido de archivado inexistente dejó entrar
  todo lo que el legacy cerró en esas 3 semanas.

### 11.1 ★ perdida=6 — bloqueo PERMANENTE del gate, causa raíz y fix (`dec33c3`)

`PERDIDA_MAX=0` es estricto: 6 ofertas sin slot tumbaban CADA ciclo, indefinidamente. Las 6 son de
`ostjob` → ATS **pi-asp.de**, que publica todas sus ofertas bajo la MISMA query y distingue la
oferta **SOLO en el fragmento** (`...?company=X#position,id=<uuid>`). `normalize_url` descartaba el
fragmento a propósito → 7 ofertas colapsaban en 2 slots → el sink saltaba el resto con un
`logger.warning` (séptima variante del patrón "fallo que se presenta como éxito"). `zentraljob`
tiene 40 ofertas con fragmento: exposición idéntica, misma base CH Media.

**Fix** (`jobhunt_core/harvest/sink.py`): el fragmento se conserva cuando lleva identidad de SPA
(`=` o hash-routing `/`/`!`) y se sigue descartando como ancla de documento. Fallo de la heurística
BENIGNO por diseño (duplicado que el dedup caza > oferta perdida). `metrics` importa la misma
función ⇒ partición `perdida`/`no_ingeribles` consistente por construcción. Verificado antes del
cambio: cero slots con fragmento en producción y el import del portfolio nunca corrió allí ⇒ **sin
migración**. Los 7 tests de colisión del import fabricaban la colisión justo con hash-routing;
fixtures movidas a anclas (la cuarentena sigue ejercitada). **Suite core: 465 passed.**

**Curación**: las 6 ya están aplicadas en staging ⇒ no se re-proyectan solas. Toque no-op
(`title=title`) de las filas legacy con `#` en la url → el CDC captura el UPDATE → el sink (código
nuevo) crea los slots. Sin efecto visible (contrato §0: `jobs` no tiene `updated_at`).

### 11.2 latencia_p95 — causa raíz ENCONTRADA, arreglo NO trivial (decisión pendiente)

Patrón medido: días de cosecha diaria → p95 **2.200-2.950 s** (techo 600); días tranquilos → **85 s**.
No es lentitud del drenado: `project_pending` ya drena hasta vaciar. El cuello es que
`_drain_embeddings` + la evaluación (ANN por perfil, `EVAL_LIMIT=1800`) corren **bajo el mismo
advisory lock** que el drenado: mientras el worker computa embeddings 30-40 min en la CPU del NAS,
los beats salen con `already_running` y el staging ESPERA — eso es lo que mide la métrica.
⚠ Ese diseño ("evaluar UNA vez tras drenar todo") fue un fix deliberado de la revisión integral
(evitaba B×P evaluaciones): NO deshacerlo a la ligera. Opciones: (a) liberar el lock del staging
antes de embeddings/evaluación y re-drenar al final — cambio estructural del proyector, con su
suite; (b) subir el umbral 600s→3600s — decisión de contrato del PROPIETARIO (B.3/§6 ratificados).

### 11.2bis ★ EL MECANISMO REAL DE B-1 — capture se perseguía la cola (fix mismo día)

Descubierto al verificar la curación de 11.1: el toque de 61 filas NO llegaba al staging. La
cadena de diagnóstico (wal2json emite bien — sonda con slot desechable; código idéntico entre
imágenes — diff 0 líneas; heartbeat fresco) acabó en dos números que lo explican todo:

- Generación de WAL en reposo: **13,4 KB/s** — y el único escritor activo era el propio heartbeat
- Avance de capture: **~11,7 KB/s**

**El `_flush` de cada tx VACÍA hacía un UPDATE de heartbeat — y ese UPDATE genera otra tx WAL
vacía que wal2json decodifica y capture vuelve a consumir.** En cabeza, lazo 1:1 estable; con
CUALQUIER retraso, consumo ≈ generación ⇒ **no converge jamás**. El consumidor anterior llevaba
**11,5 h clavado** en el LSN de las 02:02: el burst del url-check de las 03:00 (~53 MB, 3.000
UPDATEs con vectores) lo dejó atrás y ya nunca alcanzó — con el heartbeat FRESCO mintiendo
liveness. **Novena variante del patrón §9.4, la peor: la señal de vida ERA la avería.** Es también
el mecanismo profundo de la parada de 22 días que la auditoría atribuyó al broker caído: cualquier
burst deja a capture detrás para siempre, en silencio.

**Fix** (`accc10e`, desplegado y VERIFICADO el mismo día): throttle del latido en la rama de tx
vacía a máx. 1/s (5 órdenes de magnitud bajo el umbral de salud de 26 h). Resultado medido en
producción: retraso 25 MB → **6,4 KB** en minutos; tasa de WAL en reposo 13,4 KB/s → **~53 B/s**
(el lazo roto); liveness intacta. Los toques de 11.1 stagearon (64/64 aplicados),
**jobs_sin_slot = 0** y las ofertas pi-asp tienen 7 encarnaciones activas ⇒ `perdida` leerá 0 en
el cierre de ciclo de mañana 06:05. El healthcheck EXTERNO que propone la auditoría (umbral sobre
`min(received_at) WHERE applied_at IS NULL`) sigue siendo necesario — este fix quita la causa,
aquel añadiría la alarma.

### 11.4 Segunda tanda del 2026-08-22 (prioridad acordada) — F-2, alarma externa y umbrales

Commits `876add6` + `accc10e`/`dec33c3` previos + fix `del barrido`. Todo desplegado y verificado:

1. **F-2 · BARRIDO DE ARCHIVADO (ADR-07)** — `jobhunt_core/archive.py` + beat diario 05:35 (antes
   del cierre de ciclo: las métricas miden el corpus YA podado). Dos ramas: muertas (gracia 3 d;
   seguro ante reactivación — un slot que revive abre vacante NUEVA) y rancias ADR-07 (120 d sin
   visto, sin candidatura — PF.3). **Primera ejecución real: 3.502 archivadas en 47 s**; core
   activas 27.723 → **24.221** vs legacy 23.748 (los 473 restantes son la gracia, convergen solos).
   ⚠ Lección de la primera versión: subqueries correlacionadas con `max()` + índice PARCIAL =
   seq-scan por fila (~750M visitas, 10 min con locks retenidos, y la tx SOBREVIVE al cliente —
   hubo `pg_terminate_backend`). Reescrito como agregación GROUP BY en un pase.
2. **Alarma EXTERNA del staging** (auditoría B-1): el `--health` de core-capture (OTRO contenedor)
   falla si el cambio pendiente más viejo supera `CORE_CAPTURE_STAGING_STALE_MAX_S` (2 h). La
   alarma que faltó en las DOS paradas silenciosas.
3. **Umbrales recalibrados** (decisión delegada del propietario): `outbox_lag_p99` 300→900 s (F-4:
   umbral = cadencia era rojo estructural) · `latencia_p95` 600→3600 s (mide un mecanismo
   solo-sombra que se retira en Fase F; p95 real en días de cosecha 2.200-2.950 s; la comparación
   del gate es diaria).

Suite core: **471 passed**.


### 11.6 F-5 ENTREGADO — dedup semántico nivel 3 (`0e70cc6`, 2026-08-22)

Generador de candidatos cross-source sobre el índice HNSW (misma forma que el kNN del matching).
**Alcance deliberado: SOLO detección** — la métrica cuenta el par en `dedup_candidates` con
`state <> 'rejected'`, así que no hace falta fusionar, y el auto-merge es donde el legacy se hizo
daño (B-2). **Solo cross-source**: el 94 % de los falsos positivos del legacy eran intra-fuente.
Beat diario 05:20 · incremental 48 h · backfill inicial (window=0) ejecutado sobre el corpus real.
Umbral 0,95 heredado del legado como punto de partida (B.3 dejó los SIM_* abiertos): la precisión
contra el oráculo dirá si se sube. Suite core: **474 passed**.

**⇒ El único bloqueo restante del GATE-SOMBRA es el ORÁCULO (curación del propietario).**
La fusión ADR-04 (merge controlado con transferencia de estado) queda como paso posterior — no la
exige el gate.

### 11.5 Con esto, el gate queda esperando EXACTAMENTE dos cosas

Tras el ciclo de mañana (06:05) deberían quedar en verde: `perdida` (=0), `latencia_p95` (<3600),
`outbox_lag_p99` (<900), `outbox_dead`, `no_ingeribles`, `coste`. Quedan en rojo SOLO:

1. **`labels_ready` + `dedup_precision` + `ndcg@10`** → el ORÁCULO: curación manual del
   PROPIETARIO (§8 del DEPLOY_NAS de la sombra). Ya es seguro sembrar.
2. **`dedup_recall`** → **F-5**: el core no tiene generador de candidatos semánticos (techo medido
   0,073 vs umbral 0,90). O se construye el dedup semántico nivel 3 (alcance de Fase B sin
   terminar; también bloquea GATE C según el backlog) o se cambia el contrato. **Siguiente trabajo
   de código.**

### 11.3 Lo que sigue bloqueando el gate, por orden

1. **Oráculo inexistente** (`labels_ready`) — curación manual del PROPIETARIO, §8 del DEPLOY_NAS.
   Sembrar AHORA es por fin seguro: el `duplicate_of` legacy ya está reparado (2e9c108).
2. **latencia_p95** — decisión (a) o (b) de §11.2.
3. **F-2, barrido de archivado** — no tumba el gate directamente pero infla el corpus elegible
   (~4.000 muertas servibles) y contaminaría el nDCG medido. Es la salida del corpus que ADR-07
   promete y nadie escribió.
4. **dedup_recall** — F-5 de la auditoría: exige una capacidad que la Fase B no construyó.
   Decisión de contrato del propietario.

### 11.7 ★ La siembra de dedup desde `duplicate_of` es INSERVIBLE por construcción (2026-08-22)

Previsualización de la métrica con los 3.566 pares sembrados: `dedup_precision/recall = -1` con
**`no_evaluables_sin_mapeo: 3566` — TODOS**. Causa estructural: el proyector NUNCA creó slot para
los jobs que ya eran `duplicate_of` en el backfill (solo cierra los que se vuelven duplicados
DESPUÉS), así que **cada par sembrado tiene un miembro sin slot** ⇒ 0 evaluables, para siempre.
De 5.644 refs etiquetados solo 1.671 tienen slot (la cara canónica).

`labels_ready` exige **≥20 pares MAPEABLES** (`LABELS_MIN_MAPPED_DEDUP_PAIRS`): los 3.566 cumplen
el ≥50 total pero aportan CERO al mínimo de mapeables. **La curación del propietario debe etiquetar
los pares de la sección nueva de `CURACION_ORACULO.md`** (32 pares con AMBOS miembros activos:
22 mismo-título cross-source como probables `duplicate` + 10 de control como `distinct`).

Backfill semántico (F-5) sobre el corpus real: **5 candidatos** ≥0,95 cross-source — coherente con
la escasez real (la auditoría contaba 46 cross-source por duplicate_of). Si el recall medido contra
el oráculo curado saliera corto, el mando es `CORE_DEDUP_SIM_MIN` (bajar de 0,95) ANTES del freeze.

Nota de backlog: `seed_dedup_pairs` debería filtrar por mapeabilidad (o el DoD reconsiderar el
seed) — hoy siembra peso muerto.

## 12. Canario del Portfolio (Fase C, paso de lectura) — EJECUTADO 2026-08-22

> Escrito por el agente del Portfolio tras completar la misión del traspaso (§9). El canario
> está ACTIVO en producción. Rollback en una línea (abajo).

### 12.1 Qué se hizo

1. **Imagen desplegada desde HEAD del backend (`c7d14a6`)** — incluye los fixes de seguridad
   `4716d48`/`c7d14a6` que pedía coordinar el traspaso, además de TODA la Fase C (C-1 routing,
   C-2 kill-switch, C-3 CV push, C-6 verificador GATE C). La imagen previa del NAS era del
   21-jul (pre-Fase C, sin `routing.py`). Suite verificada antes de desplegar: **1091 passed**
   (2 errores de `test_migrations.py::test_roundtrip_base_to_head` solo en la suite completa —
   en aislamiento 9/9; interacción entre tests, no fallo real).
2. **Cableado** (el deploy del Portfolio NO usa Container Station: la app de la UI murió en el
   reset de julio; el mecanismo real es `deploy_stack.sh` + `docker run`, ver memoria del agente):
   - `CORE_CONSUMER_KEY` se inyecta EN TIEMPO DE DEPLOY leyéndola de
     `/share/Public/swissjob/.core.portfolio.key` (600, Ricardo; línea 3 formato
     `CORE_CONSUMER_KEY=...`) — el secreto NO vive en el script (644) ni en el compose (644).
   - `docker network connect swissjob_core-net portfolio_backend` tras el arranque (idempotente,
     en el script) — el backend queda en `portfolio_default` + `swissjob_core-net`.
   - El compose documental (`/share/Container/portfolio/docker-compose.yml`) refleja ambas cosas
     (networks + comentario de la credencial); backup `docker-compose.yml.bak-20260822`.
3. **Migración `ll55m4480o12`** (tabla `jobhunt_routing`): aplicada limpia, `alembic current =
   ll55m4480o12 (head)`. Esquema verificado: PK compuesta nombrada, check de modos, defaults de
   servidor — sin choque con create_all esta vez.
4. **Canario activado**: fila única
   `('portfolio', 00000000-0000-0000-0000-000000000000, 'catalog', 'core_read',
   updated_by='canario-fase-c')`, insertada 2026-08-22 20:21 UTC. TTL de la caché de routing: 5 s.

### 12.2 Verificación (Paso 2 de la misión, todo desde dentro del contenedor)

| Prueba | Resultado |
|---|---|
| Socket `core-api:8000` | RED OK (alias `core-api` verificado en la red) |
| `GET /v1/ready` sin auth | **200** |
| `GET /v1/vacancies` con Bearer | **200** (credencial y scope OK; primera página 20 items) |
| `GET /v1/vacancies?q=python` | **200**, relevancia correcta (Senior Python Engineer…) |
| BFF `GET /api/v1/jobs/search?q=python` (público, 8002) | Sirve del CORE: ids UUID, `source: legacy:*`, resultados relevantes |
| BFF `GET /api/v1/jobs/stats` | Sirve LOCAL **por diseño**: `CoreCatalog.stats()` lanza `CatalogUnsupportedError` y `FallbackCatalog` cae a local |
| Latencia búsqueda vía core (BFF, e2e) | 2,06 s la primera (frío) → **0,42 s estable** |
| Logs | Sin `cayo a local` en search, sin `CoreUnavailableError`, la credencial no aparece en ningún log |

### 12.3 Hallazgos para el propietario

1. **Duplicado en el feed del core**: `q=python&limit=3` devolvió dos veces la MISMA oferta
   ("Senior Python Engineer, Platform Libraries (m/f/d)", ambas `legacy:arbeitnow`). Coherente
   con la curación de dedup pendiente (§11.7) — el canario lo confirma desde el lado consumidor.
   No bloquea el canario; sí es dato para el oráculo.
2. **Sin divergencias graves** local vs core en las muestras comparadas (títulos/urls del mismo
   corpus legacy proyectado). La única "divergencia" investigada resultó ser un error de la
   propia prueba (param `query` vs `q` del router).
3. `total` no viene en la respuesta del BFF en modo core (el DTO no lo mapea o el core no lo da
   en el feed) — menor, anotado para el contrato de paginación.

### 12.4 Límites respetados + rollback

- NO se escribió en el core ni en el esquema `jobhunt`; NO se tocó ningún contenedor `swissjob-*`;
  NO se emitieron credenciales. La credencial usada es la emitida (solo lectura).
- **Rollback instantáneo del canario** (lo ven los requests en ≤5 s):
  `DELETE FROM jobhunt_routing WHERE capability='catalog' AND updated_by='canario-fase-c';`
- Pendiente fuera de mi alcance: push de los 29 commits del backend (el repo va ahead de
  `github/main`; producción ya corre HEAD).

### 12.9 Anexo al canario — el duplicado del feed, diagnosticado (sesión core, 2026-08-22)

El hallazgo del agente del Portfolio ("misma oferta ×2 en el feed, ambas arbeitnow") se investigó
la misma tarde. **No es fanout del feed** (0 vacantes con >1 encarnación activa) **ni bug del
core**: son vacantes distintas con contenido BYTE-IDÉNTICO (mismo `text_hash`).

- **Escala en el core**: ~700 "repetidas de más" (513 arbeitnow en 363 grupos —hay grupos de 9—,
  107 irishjobs, resto menor).
- **PARIDAD CONFIRMADA**: el legacy sirve los MISMOS repetidos (1.099 en arbeitnow por
  título+empresa). El canario refleja el corpus fielmente — la comparación local↔core no diverge.
- **Raíz**: la identidad intra-fuente es SOLO URL (el dedup fuzzy legacy EXCLUYE la misma fuente
  por diseño), y portales como arbeitnow publican el mismo anuncio con N URLs.
- **Acción tomada**: 10 pares intra-fuente de contenido idéntico añadidos a
  `CURACION_ORACULO.md` (con la LOCATION visible: el propietario decide si son duplicado real o
  multi-ciudad legítimo) — es la clase de riesgo que `dedup_precision` debe tener calibrada.
- **Decisión diferida (post-gate)**: política de dup intra-fuente por contenido EXACTO
  (candidatos sim=1.0 son seguros por construcción — no comparten la ambigüedad de los stubs que
  motivó excluir intra-fuente del generador F-5 — pero cambiarían feed/matching vía merge).

---

## 13. ORÁCULO CONGELADO — la racha del GATE-SOMBRA está EN MARCHA (2026-08-23, 10:51 UTC)

**El único bloqueante activo del proyecto ha caído.** Curación completada y verificada:

| Pieza | Estado |
|---|---|
| Juicios de relevancia | ali: **32** (2 positivos) · amo: **34** (18 seeds + 16, 6 positivos) — ambos ≥30 ✓ |
| Pares dedup etiquetados | 3.647 (3.566 seeds no-mapeables + **81 curados y ratificados** por el propietario) |
| Pares mapeables | **81 ≥ 20** ✓ |
| `labels_ready` | **= 1**, `perfiles_ok = 2` (verificado vía `_measured_profiles`, el constructor real) |
| Sets | `nas-ronda-1` ×2, **frozen_at = 2026-08-23 08:51 UTC** — INMUTABLES |

**Preview del dedup con el oráculo curado** (antes de congelar, como debía ser):
`dedup_precision = 0.977` ✓ (umbral 0,95; tp=42, fp=1, tn=38) · `dedup_recall = 1.0` ✓ (fn=0).
El recall=1.0 lo habilitó el generador EXACTO intra-fuente (`650a34c`, regla multi-ciudad
ratificada en la curación) + backfill de **713 candidatos**; la precisión la protegió resolver a
`rejected` los 5 candidatos seniority-variant que el propietario marcó `distinct`.

**Ciclo 2026-08-22 (cerrado 06:05): `perdida=0` POR PRIMERA VEZ** · `latencia_p95=278 s` (bajo
hasta el umbral antiguo) · outbox limpio. Solo rojos los gates de oráculo — que este freeze
resuelve. **El primer ciclo candidato a verde es 2026-08-23 (sella mañana 06:05).**

### ⚠ CODE FREEZE EN VIGOR — desde 2026-08-23 08:51 hasta 7 ciclos verdes consecutivos
Ni despliegues del core, ni cambios de etiquetas (un set nuevo cambia el set efectivo — §8.1).
Permitido: docs, tests locales sin desplegar, trabajo en el Portfolio (app aparte), legacy SOLO si
no toca scoring/dedup/corpus.

### Lo que puede pasar mañana — expectativa honesta
`ndcg@10` se mide por primera vez con juicios reales. Los juicios del propietario fueron DUROS
(2 positivos de 32 en un perfil): si los positivos no están en el top-10 de cada motor, el nDCG
puede salir <0,60 EN AMBOS — eso sería una medición HONESTA de que el matching no acierta con
estos perfiles, no un fallo del gate. Si ocurre: el trabajo pasa a calidad de matching (o decisión
de umbral del propietario), con el oráculo ya estable para medir cada intento.

### 13.1 Nota de freeze (2026-08-23): recálculo de categorías DIFERIDO
El recálculo dirigido (auditoría §6.2.3) se intentó dos veces (24k filas × clasificador en bucle
Python = horas; una tx de 19 h terminada sin commitear — cero cambios aplicados). En freeze
alteraría el scoring legacy a mitad de racha (mueve la referencia `ndcg_legacy`): **post-gate**,
en versión set-based con commits por lote. Manifiesto de datos de Fase D entregado
(`MANIFIESTO_DATOS_SWISSJOB.md`): la migración real de D son ~35 filas (job_applications=0).

## 14. Auditoría externa del gate (2026-08-23) — veredicto NO-GO y su CIERRE

`AUDITORIA_EXTERNA_GATE_2026-08-23.md`: REQUEST CHANGES — 4 bloqueantes + 1 importante.
**La racha iniciada el 2026-08-23 08:51 queda ANULADA** (ningún ciclo previo cuenta) y el
code freeze de §13 se rompió deliberadamente para corregir (coste ~0: no había sellado ni
un ciclo). Estado del cierre, en el orden mínimo del auditor:

| Hallazgo | Estado | Dónde |
|---|---|---|
| B-2 ANN: LIMIT antes de excluir fuente propia | ✅ CORREGIDO + regresión | `dedup.py` (`_KNN_SQL` con `v.id <> :vid AND sl.source_id <> :src` antes de ORDER BY/LIMIT); test `test_b2_concentracion_intra_no_oculta_al_vecino_cross` (6 intra sim 1.0 + 1 cross 0.96, k=5 ⇒ 6 pares) |
| B-3 carrera sink/archive_sweep | ✅ CORREGIDO + regresión | `sink.py`: revalidación BAJO el lock (encarnación activa + vacante vigente); slot obsoleto ⇒ huérfano/reaparición (vacante nueva). Test `test_b3_sink_no_refresca_snapshot_archivado_por_el_barrido` (interleaving del auditor) |
| B-4 SLOs 900/3600 vs contrato 300/600 | ✅ RATIFICADO por el propietario 2026-08-23 | `CONTRATOS_FASE_B.md` §6 enmendado (definición, razón, fecha; opción elegida: 900/3600 explícitos); `metrics.py` ahora REFERENCIA el contrato |
| I-1 healthcheck ciego al lag del slot | ✅ CORREGIDO + regresión | `capture.py health_check()`: `pg_wal_lsn_diff` vs `confirmed_flush_lsn` contra los 2 GiB ratificados (env `CORE_CAPTURE_SLOT_LAG_MAX_BYTES`); test con umbral −1 |
| B-1 oráculo dedup contaminado | 🔶 PROTOCOLO CONGELADO — ejecución pendiente de sesión NAS | `PROTOCOLO_HOLDOUT_DEDUP.md` (estratos H1–H5 y SQL con semilla fijados ANTES de muestrear; sets actuales reclasificados como *development*; re-adjudicación de los 17 positivos multi-ciudad incluida) |

**Auditoría de cierre preparada**: `PROMPT_AUDITORIA_EXTERNA_CIERRE.md` (verifica el cierre
de los 5 hallazgos + busca regresiones nuevas; artefactos del muestreo preservados en
`holdout_artefactos_2026-08-23/`). Lanzarla cuando el holdout esté congelado y evaluado.

**Para reanudar la racha (en este orden):** (1) desplegar los fixes al NAS, (2) sesión con el
propietario: muestrear + etiquetar EN CIEGO el holdout y congelarlo, re-adjudicar los 17,
(3) racha desde CERO bajo el contrato enmendado. El freeze de §13 vuelve a entrar en vigor
en ese momento.

## 15. Auditoría externa Nº2 (cierre) — NO-GO, y su cierre (2026-08-23)

`AUDITORIA_EXTERNA_CIERRE_2026-08-23.md`: B-3/I-1 CERRADOS, B-2/B-4 PARCIALES, B-1 NO
CERRADO + 3 bloqueantes nuevos. **Corrección de acta**: la "anomalía del despliegue de las
10:45" que el §14 atribuía a origen desconocido FUE del propio agente del cierre (misma
sesión, antes de la compactación de su contexto: build tras `650a34c` + deploy para el
backfill/curación del oráculo). La afirmación "no salió de esta sesión" era FALSA — el
historial primario (`a1a93cc8….jsonl`) lo demuestra. Registrado como error del agente.

Cierre implementado (local, pendiente de deploy):
- **BLOQUEANTE 1** — gate mezclaba development+holdout: `_dedup_rows`/`_labels_ready_row`
  filtran `DEDUP_EVAL_COHORT` + regresión (development perfecto + holdout rojo ⇒ rojo).
- **BLOQUEANTE 2** — congelado inexistente: migración `core0025` (`labeled_dedup_cohorts`
  + trigger-guard de inmutabilidad) + `freeze_dedup_cohort()` idempotente con manifest +
  regresión de las 4 mutaciones bloqueadas.
- **BLOQUEANTE 3** — ciclo mixto contaría: elegibilidad en `gate_status()` (ventana ≥
  frozen_at de la cohorte, persistido en BD) + regresión. Sin cohorte congelada, 0 elegibles.
- **IMPORTANTE 1** — underfill HNSW en dedup: patrón de matching (ef_search + iterative_scan
  strict_order + fallback exacto por objetivo real) + regresión con scan estrangulado.
- **IMPORTANTE 2** — evidencia sin versionar: manifest SHA-256 en el repo
  (`jobhunt_core/docs/MANIFEST_HOLDOUT.md`), v2 tras enriquecer la hoja.
- **IMPORTANTE 3 / MENOR 1** — protocolo enmendado (2ª): resultado por estrato, global =
  challenge-set, hoja enriquecida sin re-muestrear; acta corregida 17→22.

Pendiente (requiere al propietario): deploy de la imagen con core0025+fixes, hoja
enriquecida (NAS), etiquetado ciego 58+22, freeze (manifest v2), evaluación publicada por
estrato, y SOLO entonces primer ciclo elegible.

**Revisión intermedia preparada**: `PROMPT_REVISION_CODIGO_CIERRE2.md` — solo-código
(sin NAS), certifica los 5 commits del cierre ANTES de gastar la sesión de etiquetado;
veredicto APTO PARA ETIQUETAR / CAMBIOS. La auditoría completa con opción a GO
(`PROMPT_AUDITORIA_EXTERNA_CIERRE.md`, actualizable) queda para después de la cadena
operativa.

### 15.1 Revisión solo-código del cierre (2026-08-23) — CAMBIOS pedidos y APLICADOS

`REVISION_CODIGO_CIERRE2_2026-08-23.md`: CAMBIOS ANTES DE ETIQUETAR — 3 bloqueantes
(carrera del freeze; sello descongelable/reescribible sin rastro; manifest vacío activaba
la elegibilidad), 2 importantes (la regresión HNSW no mordía; APTO/INELEGIBLE ambiguo) y
1 menor (rutas del manifest). Los tres bloqueantes reproducidos por el revisor contra HEAD.

Cierre en commits `802db66..d7f17d0` (suite 486/486): LOCK TABLE en el freeze; migración
`core0026` (sello inmutable + CHECK de manifest, límite owner-DDL declarado); helper con
manifest obligatorio y getter fail-closed; `cycle_ok = verde ∧ elegible` con log INELEGIBLE;
regresión del underfill vía espía que trunca el kNN (hallazgo documentado: la inanición
real del HNSW es irreproducible determinista en corpus de test — 46 y 301 nodos probados);
manifest v1.1. Pendiente igual que §15: deploy (ahora con core0025+core0026), hoja
enriquecida, etiquetado, freeze real, evaluación por estrato, primer ciclo elegible.

### 15.2 Re-confirmación preparada (2026-08-23)

Agujero adicional encontrado POR EL AUTOR al preparar el prompt: sello retrodatable por
INSERT directo (el trigger no cubría INSERT) — cerrado en el commit posterior a
`d7f17d0` (frozen_at = now() obligatorio, regresión de INSERT retrodatado; suite
486/486). Prompt de re-confirmación: `PROMPT_RECONFIRMACION_CIERRE2.md` — pide veredicto
APTO PARA ETIQUETAR / CAMBIOS y pronunciamiento sobre 3 cuestiones abiertas: retrodatado
vía tx larga (now() = timestamp de transacción), límite owner-DDL, y el reto de refutar
la irreproducibilidad del underfill (si el revisor la refuta, su geometría sustituye al
espía-truncador).

### 15.3 Ronda 2 de la re-confirmación (2026-08-23) — CAMBIOS aplicados

`RECONFIRMACION_CIERRE2_2026-08-23.md`: CAMBIOS — 3 bloqueantes (retrodatado por tx larga;
manifest JSON null/no-objeto; sellado DML sin lock), 2 importantes (metrics.render_report
decía APTO; mi hipótesis de irreproducibilidad del underfill REFUTADA con geometría 350+5
estable ⇒ 0/5). Cierre en `b7ac557..b1117a0`, suite 488/488:
- sello = statement_timestamp() obligatorio (trigger+helper) — now() era timestamp de tx;
- CHECK/getter exigen jsonb_typeof='object' y no-vacío (fail-closed doble);
- LOCK TABLE en el TRIGGER (frontera común: helper y DML directo esperan al escritor);
- render_report: ventana mixta ⇒ CICLO INELEGIBLE, jamás APTO (+ fail-closed sin cohorte);
- test del underfill con la geometría REAL del revisor (0/5 verificado; espía solo-observa
  y gestiona los vetos del test durante la rama exacta). Cuestión owner-DDL: ACEPTADA por
  el revisor como límite declarado. Pendiente operativo idéntico: deploy core0025+core0026
  → hoja enriquecida → etiquetado 58+22 → freeze real → evaluación por estrato.

### 15.4 Ronda 3 (2026-08-23) — P-1/P-2 cerrados

`RECONFIRMACION_CIERRE2_R3_2026-08-23.md`: B2/I1/I2 CERRADOS; B1/B3 parciales por P-1
(el sello se fechaba ANTES de drenar al escritor — falso elegible en la ventana de
espera) y P-2 (inversión de locks helper↔UPDATE directo — deadlock detected
reproducido). Cierre en `622e9e4` (suite 491/491): el trigger persiste
clock_timestamp() POST-lock (valida la intención con statement_timestamp() antes); el
helper ya no toma locks y sella UPDATE-primero/INSERT-después (orden fila→pares idéntico
al DML directo; ON CONFLICT habría conservado una inversión residual), idempotencia
concurrente con savepoint. 3 regresiones (boundary, no-deadlock, congeladores
concurrentes). Prompt ronda 4: `PROMPT_RECONFIRMACION_CIERRE2_R4.md`.

### 15.5 Ronda 4 (2026-08-23) — solo las pruebas mordían en falso; cerrado

`RECONFIRMACION_CIERRE2_R4_2026-08-23.md`: implementación funcional de P-1/P-2 CORRECTA
(8/8 verificaciones, 491/491), pero mis dos regresiones pasaban también contra el padre
— P-1 usaba el helper (su lock viejo retrasaba el inicio de la sentencia) y P-2 esperaba
al helper en vez de pausarlo dentro de su LOCK TABLE. Cierre en `7a5cf6f`: cuerpos
FIELES del revisor (DML directo bloqueado en el trigger + espía que pausa solo ante el
LOCK TABLE viejo), mordida verificada también por el autor (2 failed contra 622e9e4^),
y los 2 comentarios de P-3 corregidos. Suite 491/491. Prompt ronda 5:
`PROMPT_RECONFIRMACION_CIERRE2_R5.md` (trámite: solo tests+comentarios cambiaron).

### 15.6 APTO PARA ETIQUETAR (2026-08-23, ronda 5) — arranca la cadena operativa

`RECONFIRMACION_CIERRE2_R5_2026-08-23.md`: P-1/P-2/P-3 CERRADOS, delta solo
tests+comentarios, mordidas 2/2, suite 491/491, sin hallazgos nuevos. **El APTO autoriza
SOLO la sesión de etiquetado.** Cadena operativa (en orden, NO-GO vigente hasta el final):
1. Deploy NAS de swissjob-core:prod @ 7a5cf6f (aplica core0025+core0026)
2. Hoja enriquecida (holdout_enriquecer.sql, misma muestra) + manifest v2 commiteado
3. Etiquetado CIEGO del propietario: 58 pares + 22 re-adjudicaciones (máx. 8 unsure)
4. Inserción de juicios + freeze_dedup_cohort con manifest v2 EN LA MISMA SESIÓN
5. Evaluación del holdout publicada POR ESTRATO tal cual salga
6. Primer ciclo elegible: automático (ventana posterior al frozen_at)
7. 7 ciclos verdes consecutivos → auditoría externa final → GO

## 16. HOLDOUT CONGELADO Y EVALUADO (2026-08-24) — el detector suspende el examen

Etiquetado ciego completado (58: 27 dup/31 distinct/0 unsure + 14 re-adjudicaciones),
freeze inmutable a las 07:14:17 UTC con manifest v2.1, guards verificados en producción.
Evaluación publicada tal cual (`ACTA_HOLDOUT_DEDUP_2026-08-24.md`): **precision 0.636 /
recall 0.259 — ROJO** (umbrales 0.95/0.90). Por estrato: H4 perfecto (exacto-intra),
H5 sin falsos positivos, pero **H1 cross-portal 0/15** — el caso que motivó el dedup
semántico no se detecta (SIM_MIN=0.95 sobre texto completo; descripciones entre portales
muy distintas o vacías). El primer ciclo elegible (ventana del 2026-08-25) saldrá rojo en
dedup: la racha NO puede completarse sin mejorar el detector cross-source. La mejora se
ajusta SOLO con development; el holdout queda como examen fijo. Los 4 FP de H3
(candidatos de la era pre-fix) quedan pendientes sin resolver.

### 16.1 Decisión del propietario (2026-08-24): development-2 SIN ronda de etiquetado humano

El propietario RATIFICA el criterio y DELEGA su aplicación al agente (textual): la misma
oferta con pequeñas variantes de redacción en título/empresa («Kanton Zug» vs «Kantonale
Verwaltung Zug») = duplicate; mismo texto con ciudades distintas = pueden ser dos ofertas
(regla multi-ciudad). Mitigaciones registradas: muestreo development-2 MECÁNICO y
pre-registrado (sin selección manual), etiquetas por criterio escrito del propietario,
conflicto del agente declarado (ha visto el holdout), y el gate solo puntúa el holdout
congelado — un sesgo en el tuning no puede aprobar el examen, solo suspenderlo. La
revisión externa de diseño (R.3) recibe esta acta.

## 17. TRACK R implementado (2026-08-24) — pendiente de revisión R.3 y deploy

- R.2a (`815e84e`): guard de ubicación en el ANN (dev-1: FP 17/53 ⇒ 1/53). Suite 492.
- R.2b (`4c62c26`): generador LÉXICO cross-portal — token significativo de empresa +
  trgm título >= 0.65 + ubicación v2; medido en development-2 (60 pares etiquetados por
  el agente bajo criterio ratificado §16.1, manifest v3): 9/9 dup, 0 FP; el ANN daba
  0/9. core0027 (pg_trgm) + bootstraps. Suite 493/493.
- SIGUIENTE: lanzar `PROMPT_REVISION_TRACK_R.md` (revisión de diseño + etiquetas dev-2
  par a par + circularidad D2A); tras su APTO → deploy al NAS (imagen con core0027 y
  los dos generadores) + limpieza menor pendiente (tar .new + *.sql temporales) → el
  gate re-mide SOLO contra el holdout congelado, ciclo a ciclo. Los 4 FP de H3 siguen
  pendientes y NO se resuelven con el holdout.

### 17.1 Revisión Track R cerrada (2026-08-24) — pendiente re-confirmación

REVISION_TRACK_R: 4 P1 + 3 P2 + 1 P3, cerrados en `e955fe9` (código, suite 496/496) y
P1-4 en los dos repos (artefactos versionados `1733dd0`; dev-2 reproducible v2 con
consulta única, re-etiquetado de 42 diferencias sin ajustar: 11 dup/49 distinct,
TP 11/11 FP 3 con modos declarados — lemon.io roles a trgm 0.70 y multi-ciudad mismo
cantón). Backfill léxico one-shot: tarea `jobhunt.maintenance.dedup_lex_backfill` —
OBLIGATORIA tras el deploy, antes del primer ciclo que evalúe Track R. Falta:
re-confirmación del revisor → deploy NAS (imagen + core0027 + backfill + limpieza tar).

### 17.2 Track R DESPLEGADO (2026-08-24) — el examen mejora pero sigue suspendiendo

Deploy con APTO del revisor (RECONFIRMACION_TRACK_R_R2): imagen `79d47cb`, core0027
aplicada, pg_trgm ✓, backfill léxico one-shot ejecutado (93 candidatos sobre el corpus
completo), limpieza del NAS hecha (tar consolidado, temporales fuera).

Vista previa del examen (holdout congelado, `_dedup_rows` real):
**precision 0.692 (antes 0.636) · recall 0.333 (antes 0.259)** — TP 7→9, FP fijo en 4
(los candidatos pre-fix de H3, irresolubles vía holdout). El gate seguirá ROJO en dedup.

Lectura SIN disecar el holdout (prohibido para tuning): los FN restantes pertenecen
estructuralmente a (a) pares intra-fuente con hash distinto — el léxico es cross-source
por diseño; (b) gemelos remoto-vs-concreto y ubicaciones tipo dirección postal que el
guard veta; (c) variantes de título bajo trgm 0.65. El camino legítimo para atacarlos:
el ESTRATO POSITIVO INDEPENDIENTE de development (seguimiento obligatorio del revisor)
que capture esos modos con datos frescos — nunca ajustando contra el examen.

Mientras: los ciclos elegibles saldrán rojos en dedup (el resto de métricas se mide
igual); desplegar en rojo es gratis; si algún día sella verde, el core queda CONGELADO.

### 17.3 FASE 2 DESPLEGADA (2026-08-24) — dedup_precision VERDE (1.0); recall 0.370

Secuencia autorizada ejecutada íntegra: deploy `f08ce6e` → backfill léxico (1.666
candidatos, tres vías) → barrido autorizado (preview 271 = apply 271, hash idéntico,
2ª apply 0; resolved_by='rule:track-r-location-v1') → examen:
**precision 1.000 (VERDE, primer gate de dedup superado) · recall 0.370 (rojo)**.
TP 9→10 con toda la fase 2: los 17 FN restantes resisten a las reglas seguras. Clases
estructurales conocidas que quedan (sin disecar el holdout): gemelos remoto-vs-concreto
(el revisor exige positivos independientes + rama explícita más fuerte antes de tocar el
veto bilateral), reformulaciones intra en banda 0.75-0.90 (3 FN vistos en dev-3),
variantes cross bajo 0.65, y pares sin ningún token de empresa común. El siguiente salto
de recall necesita otra ronda de diseño con evidencia nueva (rama remoto-concreto
condicionada, señal de URL/dominio de aplicación, o embedding título+empresa) — no más
ajuste de umbrales. Los ciclos diarios ya miden precision en verde; el gate sigue rojo
por recall.

### 17.4 TRACK R CERRADO EN SU TECHO (2026-08-24 noche)

Fase 3 desplegada con APTO de revisión independiente (husos horarios = remoto;
mordida verificada, 0 colisiones en corpus, suite 501/501). Backfill: 2 candidatos,
1 era del examen. ESTADO FINAL DEL EXAMEN: **precision 1.000 (verde) · recall 0.407
(rojo)** — desde 0.636/0.259 al inicio del track, sin un solo FP añadido en ninguna
fase. Techo documentado con datos de ambos corpus (ANALISIS_TRACK_R_FASE3): las señales
restantes no existen en los datos almacenados. Vías de futuro: R.6 (capturar apply_url
en legacy — solo pares futuros), rama remoto↔concreto si algún día hay positivos
minables. Los ciclos sellan cada madrugada; el gate queda rojo por recall hasta nueva
evidencia. Esfuerzo redirigible a: ADR-04 (fusión — condicionada al estrato positivo
independiente), TRACK P, TRACK V-DIFERIDO, legacy.

## 18. Sesión 2026-08-25 — R.6 implementado, ADR-04 diseñado, ciclos de auditoría

- **R.6 COMPLETO** (`78c9a01`): tubería apply_url legacy→core (migración a1f2e3d4c5b6,
  modelo, guard de frontera patrón-logo, 3 providers con el dato en mano, whitelist CDC,
  proyector por ambos caminos). Fuera de content_hash (cero churn de embeddings) y fuera
  de JOB_PAYLOAD_MAP (jamás en content). Suites: core 502/502, legacy 1423/1423.
  PENDIENTE deploy (backend+core al NAS) tras los ciclos de auditoría.
- **ADR-04 diseñado** (`fe9f296`): fusión con 3 niveles de confirmación, superviviente
  determinista, transferencia de estado, reversibilidad total (dedup_merges). Bloqueado
  para implementar por estrato positivo independiente + gate.
- **Directiva del propietario**: ciclos de auditoría independiente (bugs→fix→re-audit
  hasta limpio → auditoría de optimización). C1 en marcha sobre f08ce6e..HEAD.

### 18.1 Ciclos de auditoría C1/C2 (2026-08-25)

C1 (2 P2 + 3 P3) cerrado en `27fc83c`; C2 verificó los 5 cierres (mordidas incluidas) y
cazó 1 P2 nuevo (doble omisión TOAST consecutiva de apply_url — cerrado con la tercera
vía al valor almacenado + distinción omitido/NULL-explícito) y 2 P3 (residuo Unicode
[[:alpha:]] + husos DE/FR — cerrados). LIMITACIÓN DOCUMENTADA (C2-P3, exposición local
0): los candidatos resueltos con firma rule:…-v1 son irrecuperables por diseño — en el
NAS hay 271 (barrido autorizado); si alguna futura versión de la regla los volviera
compatibles, la vía sería un one-shot versionado que re-abra SOLO resoluciones
por-regla (jamás humanas), con revisión previa. No implementado: sin exposición real.

### 18.2 Ciclos C3–C8 y residual DIFERIDO del contenido en cerrado (2026-08-25)

C3-C8 cerrados (informes en Public; convergencia 5→3→2→1→3→4→3→LIMPIO). RESIDUAL
DIFERIDO POR DISEÑO (C5-P2-1, prometido aquí desde entonces): un cambio de CONTENIDO
(título/descripción/…) llegado con el slot CERRADO no genera revisión — solo url y
apply_url se persisten en el cierre; escribir revisiones en cerrado reabriría el
pipeline de canónicas. La reactivación TOAST-omitida puede revivir con contenido de la
última revisión previa al cierre. Acotado: el legacy re-emite el contenido completo en
la mayoría de U de reactivación. C8: veredicto LIMPIO ⇒ auditoría de OPTIMIZACIÓN
desbloqueada.

### 18.3 Auditoría de OPTIMIZACIÓN e implementación OPT-1/OPT-2 (2026-08-25)

Auditor independiente de rendimiento (informe `AUDITORIA_OPTIMIZACION_2026-08-25.md`),
todo medido en BD desechable con 24k vacantes sintéticas representativas antes de
proponer. Dos hallazgos OPT-ALTA, ambos en `jobhunt_core/dedup.py`, IMPLEMENTADOS:

- **OPT-1** — la CTE `firma` de `_lex_sql` era O(n²) (subconsulta correlada que
  escaneaba la CTE `tok` entera por fila del corpus: 64,3 de los 66,8 s del beat
  diario). Reescrita por `GROUP BY`: **66,8 s → 2,7 s (24,6×)** en la ventana y
  97,4 s → 35,4 s en el backfill, resultado byte-idéntico verificado (md5 del
  conjunto insertado). Mordida `test_opt1_...`: fixture con par detectable SOLO por
  firma (token capado a >50 empresas distintas) + empresas de 0 tokens (arista
  JOIN⇔EXISTS), compara el conjunto contra la forma correlada conservada como oráculo.
- **OPT-2** — `_KNN_COUNT_SQL` por fila era matemáticamente redundante con k vecinos
  llenos (por su propio LIMIT, objetivo ≤ k): 500/500 redundante en el corpus, −30 %
  del tiempo de BD del bucle ANN. Cortocircuito `len(vecinos) < k` antes del conteo.
  Mordidas: (a) k llenos + conteo saboteado ⇒ el scan NO lo ejecuta (falla contra el
  padre); (b) underfill ⇒ el conteo SÍ se ejecuta y el fallback P2-3 sigue intacto
  (pasa en ambos POR DISEÑO: protege contra sobre-optimización futura).

Efecto agregado: fase dedup del beat ~69,6 s → ~4,6 s en dev (~15×); en el NAS, de
minutos a decenas de segundos con la transacción abierta 15× menos. Todo lo demás
medido y documentado como NO-HACER con números (refresh set-based 68 ms/lote — receta
en el apéndice B del informe, condicionada a lotes de cierre masivos reales; frontera
de perdida 108 ms/día — moverla a SQL rompería el espejo C6–C8; encode del sink
0,1 ms/lote; guard legacy 22,5 µs/oferta). El espejo sombra (sink/proyector/métrica)
declarado LIMPIO en C8 no se tocó. Suite core 514/514.

### 18.4 Ciclo C9 sobre el delta OPT — LIMPIO (2026-08-25)

Auditor independiente sobre f13c236..HEAD (solo dedup.py + sus tests). OPT-1
verificado equivalente también por sondas diferenciales propias (corpus hostil +
aleatorizado con seed, 113 pares idénticos, incluida la ventana del beat); OPT-2
confirmado identidad matemática en vivo (objetivo ≤ k por el LIMIT del conteo; el
caso «k llenos pero no los k más cercanos» es aceptación PREEXISTENTE del diseño
ANN, no regresión del delta). Veredicto LIMPIO con 2 P3 en tests, despachados: el
oráculo OPT-1 ahora cubre también window=True (beat), y el fixture de opt2 lee
CORE_DEDUP_KNN de settings en vez de hardcodear 5. Con esto la directiva de
auditorías (bugs hasta LIMPIO → optimización → re-auditar lo optimizado) queda
CUMPLIDA de punta a punta. Pendiente único del frente: deploy NAS de la cadena
acumulada (R.6 + C1-C8 + core0028 + OPT) — requiere SSH interactivo del owner.

### 18.5 DESPLEGADO EN EL NAS (2026-08-25) — cadena completa R.6 + C1–C9 + OPT

Deploy ejecutado con autorización del propietario («Lánzalo tú»). Backup previo
íntegro (db-pre-deploy-opt-20260825.sql.gz, 225 MB, «dump complete» verificado).
Imágenes `swissjob-core:prod` y `swissjob-backend:prod` (con Chromium) construidas
de b739af4+44a107f, verificadas POR DENTRO antes y después del load (OPT-1/OPT-2,
core0028, a1f2e3d4c5b6, fix alembic), checksums idénticos, tars previos rotados a
.prev. Recreate vía `bin-docker-compose -f docker-compose.sombra.yml up -d` (CLI,
nunca la UI). Migraciones aplicadas en el arranque: core core0027→core0028; legacy
d3e5a91c74b2→f2b7d94a1c63→30d0bb87a4bd→a1f2e3d4c5b6 (el NAS arrastraba VD.3 y
published_at pendientes — ahora al día). Verificado en producción: 4 columnas del
contrato 2048 vivas (jobs.apply_url incluida), capture reanudó el slot desde
last_applied sin gap, beat embebido arrancado, /v1/ready 200, 0 errores en workers.
Desde hoy el apply_url legacy fluye por el CDC al core y la señal se ACUMULA para
pares futuros. Los ciclos del gate siguen su cadencia; recall sube solo con
evidencia nueva. El beat dedup corre ya con OPT-1/OPT-2 (próximo ciclo lo medirá).

### 18.6 Barrido de deuda (2026-08-25 tarde) — push de respaldo + VD.7/VD.9 cerrados

- **Respaldo remoto HECHO**: `feat/fase-a-core` empujada a GitHub (217 commits que solo
  vivían en local). Remoto `github` cambiado a SSH (la clave ya estaba autorizada).
- **VD.7 CERRADO — ya estaba arreglado**: `financejobs.py` prueba ambas rutas del
  `__NEXT_DATA__` (`pageProps` actual primero, `initialProps` histórica después) y el
  scraper está activo en el registry desde el 19-06 (~10 ofertas/pág con stealth). La
  entrada del backlog era una foto vieja del 14-08.
- **VD.9 CERRADO por verificación en vivo** (providers reales, 1 petición/fuente):
  `zebis` 34 ofertas y `thehub` 47 ofertas — thehub ya apuntaba a `api.thehub.io/v2`.
  Ninguna de las dos está muda.
- **VD.8 sigue abierto** (decisión del propietario: cambia qué ofertas viven en el
  corpus). **VD.4/VD.6** siguen aparcados en TRACK V-DIFERIDO por decisión del 06-08;
  nota de timing: la corrección del 20-08 exige desplegar y drenar esas fuentes ANTES
  de la racha 7/7 definitiva — con el gate rojo por recall (espera de evidencia
  apply_url), ESTE es el hueco natural para hacerlo si el propietario lo desaparca.
- **TRACK P**: figura como frente redirigible desde el cierre del Track R pero no tiene
  definición en ninguna doc — necesita una frase del propietario sobre su alcance antes
  de poder diseñarse.

### 18.7 TRACK V-DIFERIDO DESAPARCADO Y CERRADO CON EVIDENCIA (2026-08-25 tarde)

Autorización del propietario («adelante»). Re-verificación EN VIVO de las 9 fuentes mudas
del diagnóstico del 06-08 — cinco fotos estaban caducas:

| Fuente | Estado real 25-08 | Acción |
|---|---|---|
| zebis | **34 ofertas** (provider real) | nada — viva |
| thehub | **47 ofertas** | nada — viva |
| gastrojob | **50 ofertas** | nada — viva (selectores ya servían) |
| stelle_admin | **7 ofertas** (VD.2/VD.3 arreglados en ab03024) | nada — viva |
| swiss_schools_isb / _zis | 0 ofertas y tableros REALMENTE vacíos (Finalsite sin postings; agosto, curso arrancando) | nada — el bucle «vacío→soft-block→kill-switch» está roto (Bloque 0); el reintento 24h las rehabilita solo cuando publiquen |
| authenticjobs | /rss/custom.php muerto; /feed/ = BLOG; **/jobs/feed/ existe pero VACÍO** (tablón abandonado) | sigue retirada, documentado |
| dailyremote | sin RSS (solo sitemap-jobs → sería scraper nuevo) | sigue retirada |
| translatorscafe | rss.aspx 404 en todas las rutas | sigue retirada |
| proz | 403 duro incluso con cabeceras Chrome (clase med-jobs: solo CDP de pago) | sigue retirada, documentado |

Conclusión: **no queda reescritura pendiente** (VD.4 sin objeto). La reactivación de las
apagadas por kill-switch es AUTOMÁTICA con el retry de 24h ya en producción desde el
deploy de esta mañana — verificar en el ciclo de mañana que gastrojob/stelle_admin
cosechan y que isb/zis quedan en «éxito 0» sin re-bloqueo. La condición de la corrección
del 20-08 (fuentes recuperadas ANTES de la racha 7/7) queda satisfecha: todo lo
recuperable está recuperado y drenará con las cosechas normales.

### 18.8 TRACK P — resulta que la costura del portfolio YA ESTÁ CONSTRUIDA

Exploración del backend ReactPortfolio (agente independiente): implementa el patrón
A.SEAM completo con consumer `portfolio` — cuarteto port/local/core_client/seam en
catálogo y matching (pesadas), applications/saved_searches (ligeras, siempre local),
push de perfiles C-3 con ETag+Idempotency-Key, tabla jobhunt_routing con resolve_mode
que falla cerrado (503), GATE-C por capacidad, write-freeze C-2, y tests de contrato.
Frontend sin rastro del core (correcto: BFF). **Bloqueantes reales del flip a
core_read**: (1) el compose del portfolio no comparte red con core-api (falta external
network — toca compose de producción ⇒ confirmación del propietario); (2) dos cotas de
frontend registradas: paginación por cursor (has_more/next_cursor, sin total) y
normalización de ai_score a la escala del core; (3) C-4 (escrituras candidaturas/
búsquedas) no existe en /v1 — seams inertes por diseño hasta que el core lo exponga.
Documentos y colegios: capacidades vírgenes sin puerto/seam.

### 18.9 TRACK P — cotas de frontend CERRADAS (2026-08-25 noche)

Agente independiente sobre ReactPortfolio (OJO: son DOS repos git — backend/.git y
frontend/.git). Las cotas estaban registradas como comentarios COTA en los seams, no
en docs. Cerradas ambas:
- **Cursor** (frontend a37620c): useJobFilter reescrito a «cargar más» con
  has_more/next_cursor (core) u offset (local), mismo code path; panel sin paginador
  por total; contador honesto «N+»; i18n 6 locales; tests del hook reescritos.
- **Escala ai_score** (backend 8a6c4b3): normalización determinista en el seam vía
  setting CORE_SCORE_SCALE_MAX (0..100 Fase A; jamás se adivina por ítem) + clamp;
  DE PROPINA un bug real: el fallback de embedding_score colaba score 0-100 en un
  campo que el frontend lee como fracción 0..1.
Suites reales: backend 1097 passed (EXIT=0), frontend 315 passed + build OK (EXIT=0).
Con la red (overlay f413c75, ready 200 verificado) y estas cotas, **el flip a
core_read del portfolio solo espera decisión** (los filtros estructurados caen a
local bajo core_read — cota 501 del contrato, documentada). C-4 diseñado (v1) a
falta de revisión independiente.

### 18.10 🏁 FASE C: FLIP core_read EN PRODUCCIÓN (2026-08-25 noche)

Con permisos interactivos del propietario, tramo final ejecutado por CLI: contenedor
portfolio_backend del NAS reemplazado (imagen nueva con clientes /v1; el anterior
queda como portfolio_backend_pre_flip parado = rollback), env replicado + credencial
consumer, redes portfolio_default + swissjob_swissjob-net (core-net RETIRADA a
propósito — ADR-08; estaba conectada de la era del push C-3), salud 200, y la fila
jobhunt_routing (portfolio/comodín/catalog/core_read) insertada como interruptor.
VERIFICADO EN PRODUCCIÓN: /api/v1/jobs/search devuelve vacantes del CORE (id UUID,
source legacy:*, cursor keyset, total null, degraded false, cero «cayó a local»).
El catálogo del portfolio en producción lo sirve jobhunt-core: primer cutover de
lecturas del Strangler Fig consumado. Rollback = UPDATE de esa fila a mode='local'
(o revivir el contenedor pre_flip). Matching sigue en local (flip aparte cuando se
decida); C-4 escrituras esperan activación de seams tras contract tests en real.

### 18.11 🏁 C-4 EN PRODUCCIÓN: escrituras del portfolio en core_primary (2026-08-25 madrugada)

Secuencia completa con el propietario en interactivo para los interruptores:
backfill verificado trivial (0 filas locales), perfil del consumer portfolio
PROVISIONADO en el core del NAS (el push C-3 nunca corrió en prod — hallazgo),
CORE_PROFILE_ID añadido a la credencial, contenedor recreado (salud 200), y las
DOS filas de routing: applications y saved_searches → core_primary (catálogo
sigue core_read). VERIFICADO EN PRODUCCIÓN: ciclo real crear→listar→borrar de
búsqueda guardada contra /v1 (creada efc59c69…, lista 1, borrada, lista 0).
El core es desde ahora el ESCRITOR de candidaturas y búsquedas del portfolio.
Además: VD.8 cerrado (9114ffa, backend 1456 — el filtro tech ya no mata por
inanición a las guardadas; mordida verificada con stash) y minería del estrato
sobre las 24k del NAS corriendo desatendida (contenedor estrato_mineria →
ESTRATO_NAS_RAW_20260825.md). Matching del portfolio: aparcado con causa —
requiere push del CV al core + ciclo de evaluación antes de tener matches que
servir.

### 18.12 DIRECTIVA «agotar lo no bloqueado por NAS» — CUMPLIDA (2026-08-26)

Lote paralelo de 4 agentes + cierres de la sesión principal:
- **Decisiones** (5b0993a): las 4 del propietario con recomendación razonada.
- **Ensayo cutover en dev** (d3be374): runbook EJECUTABLE con las piezas reales
  (freeze <1min, rollback con continuidad CDC); 5 huecos concretos documentados
  (métrica = encarnaciones abiertas; sumar coste de embeddings a la ventana; etc.).
- **Cadena matching dev CERRADA** (2d230ba+2f5582c): push C-3 → embedding →
  1.800 evals → 30 matches core_feed; secuencia prod de 5 pasos escrita; DOS
  trampas cazadas (scope profiles:read ausente también en la credencial NAS;
  jamás flipear sin evals>0 en BD).
- **SwissJob→C-4** (7d6b2c6, backend 1473): CoreApplications real multi-perfil
  (identidad por jobhunt_profile_map, enums 8=8, sin fallback silencioso);
  cotas: state machine de match_results siempre local; búsquedas guardadas SIN
  costura a propósito — su motor (search_tasks) es escritor local y un flip del
  CRUD rompería las alertas en silencio ⇒ DECISIÓN nueva para el propietario
  (5ª del brief).

**RE-INVENTARIO: lista de «ejecutable sin NAS» = VACÍA.** Todo lo restante es:
(a) decisiones del propietario — las 4+1 del brief, los 19 ambiguos del estrato,
curación del oráculo; (b) lote NAS — corregir credencial (+profiles:read),
secuencia matching prod, carga+freeze del estrato, deploy VD.8+cohorte+wiring
C-4 en la próxima imagen, minería NAS (su contenedor sigue trabajando), ensayo
NAS del cutover; (c) el gate acumulando. La construcción sin dependencias
externas está agotada.

### 18.13 🏁 FASE C COMPLETA + GATE RE-RATIFICADO EN PRODUCCIÓN (2026-08-26 madrugada)

Delegación total del propietario («las decisiones tómalas tú… la sesión con
contraseña puedes iniciarla»). Ejecutado de una tirada:
- **ACTA_DECISIONES (97eac82)**: 6 decisiones (flip catálogo global; recall
  vinculante 0.40; cotas /v1 no-permanentes; Legion dup con cap; saved_searches
  SwissJob = BFF-local; 19 ambiguos excluidos).
- **D2 en código y EN PRODUCCIÓN** (625e15f, core 547/547): DEDUP_RECALL_MIN=0.40
  con comentario-acta y mordida (0.333 sigue rojo). Verificado en el NAS tras el
  deploy: umbral 0.4 vivo ⇒ **el ciclo de esta madrugada puede ser el 1º VERDE
  de la racha de 7**.
- **El CV existía como FICHEROS** (CV_Extenso/cv_web_*.md — «estado diferente»):
  sembrado en producción por su seeder oficial (7 perfiles, 6 idiomas+simplificado)
  → push-cv 200 pushed → run_pending → 1.800 evaluaciones → **flip matching** →
  analyze 200 source=core_feed 30 resultados (1ª llamada fría ~90s; después ms).
- **Las 4 capacidades del portfolio sobre el core**: catalog/matching=core_read,
  applications/saved_searches=core_primary. FASE C CONSUMADA.
- Deploy de ambas imágenes (backend: VD.8+C-4 wiring+catálogo D; core: D2+cohorte
  +stratum): healthy, migraciones al día, temporales purgados.
- Minería NAS v3 corriendo (v1 permisos mount, v2 statement_timeout del prod —
  parcheado SET statement_timeout=0 en la sesión solo-lectura).

---

## 19. Nueve ciclos de auditoría global + fase de optimización (2026-08-26/27)

> ⚠ **SUPERADA POR §20** (misma fecha, por la tarde). Se conserva como foto de la mañana
> del 2026-08-27. Sus contadores ya no son los vigentes —lo son los de §20.0— y sus §19.4
> y §19.5 describen como PENDIENTE lo que §20.2, §20.1 y §20.5 documentan ya ejecutado.
> Los de §0–§18 son fotos de su fecha y no se han vuelto a medir.

### 19.0 Contadores verificados (2026-08-27, comprobados EJECUTANDO)

| Qué | Valor | Cómo se verificó |
|---|---|---|
| Suite legacy (`backend/`) | **2 277 passed · 3 skipped · 4 xfailed** · 3:57 | `pytest --collect-only` da 2 284 = 2277+3+4 |
| Suite core (`jobhunt_core/`) | **668 passed** · 5:31 | `pytest --collect-only` da 668 |
| Suite portfolio backend | **1 823 passed · 1 skipped** | rama `main`, HEAD `73d9212` |
| Suite portfolio frontend | **319 tests** (vitest) | — |
| Cadena de migraciones core | **`core0001..core0032`** · head ÚNICO `core0032` · 33 ficheros de revisión (`core0008` va partida en `a`/`b`) | `ScriptDirectory.get_heads()` en `core-migrate` |
| Providers registrados | **25** = 20 sin restricción + 5 restringidos *gated* | `get_provider_names()` en el contenedor |
| Providers instanciables aquí | **16** (los 4 de API key —adzuna/careerjet/jooble/jsearch— y los 5 restringidos no arrancan sin credencial) | `get_all_providers()` |
| Scrapers registrados | **15** = 7 base + 8 `swiss_schools_*` | `get_scraper_names()` |
| Cadencias del beat del core | **9** (no 5) | enumerando `celery_app.conf.beat_schedule` |
| Puertos host | 5435 · 6380 · 6381 (loopback) · 8002 · 8003 · 5174 | `docker compose config` |

### 19.1 La regla de oro que dejaron los nueve ciclos

**Un documento —docstring, comentario, mensaje de commit o encabezado de script— puede
afirmar por escrito una garantía que el código no da.** No es una anécdota: es el hallazgo
transversal. Los ciclos formularon la clase dos veces:

- G5: «el commit que los introduce **declara por escrito una garantía que su código no da**».
- G6: «el código hace algo distinto de lo que **su commit, su docstring o su test de
  regresión** afirman».

El caso extremo del core: la línea del informe de la gracia que imprimía
`(slot cerrado + cambio pendiente <1h)` sobre una población que **jamás tuvo slot**. El
mecanismo se **reinterpretó cuatro veces** (G2 → G3 → G4 → G5) y en cada reescritura la
línea del informe sobrevivió intacta; el docstring de `_huecos_en_transicion` lo dice hoy
en su encabezado: *«lleva cuatro ciclos reinterpretándose»*. Corregida en G5-P3-1.

Su equivalente en el legacy: el **parser de salarios rompió CUATRO veces por el mismo
sitio** (la regla de desempate), y las cuatro versiones se validaron contra el mismo
corpus — los 637 valores de `jobs.salary_original`, todo ASCII generado por máquina — que
**no puede refutar nada**: no contiene ni una referencia, ni un año, ni una escala
salarial, ni un `bis`.

**Corolario operativo, y es lo que hay que hacer al leer esta documentación:** verifica
ejecutando. Ninguna cifra de §19.0 está copiada de un informe; todas se volvieron a medir.

### 19.2 Cambios de comportamiento que un lector DEBE conocer

**Dedup cross-idioma: RETIRADO** (`0465681`, G5/P2-2). La excepción que se saltaba la
puerta léxica cuando los idiomas declarados diferían (`a63745c`) se retira entera. Tres
motivos, los tres MEDIDOS con el encoder real: (1) **la separación estaba invertida** — una
maestra de primaria (DE) y un contable (FR) del mismo municipio puntúan **0,8220**, por
encima del duplicado real, que puntúa **0,8195**: no existe umbral que recoja el segundo
sin el primero; (2) **no servía de nada** — a umbral 0,95 el prefiltro SQL mata el par una
capa antes, y sobre 200 activas los pares cross-idioma son **0 a los cuatro umbrales**
probados (0,95 · 0,86 · 0,80 y el intermedio); (3) **el precio de equivocarse no es
cosmético**: `mark_duplicate` escribe `duplicate_of` **y `is_active=False`**, y este
proyecto ya perdió **664 vacantes reales** por un dedup mal calibrado.
*Cota aceptada por escrito:* la misma vacante publicada en dos idiomas **no** se deduplica
por esta vía; lo cubre `fuzzy_hash` cuando el título coincide. Reabrirlo NO es bajar el
umbral: el discriminante que sí cruza idiomas es el **coseno de los títulos solos**
(medido: min(reales)=0,6067 > max(falsos)=0,5033), y haría falta ese discriminante Y su
umbral Y abrir el prefiltro.

**Parser de salarios: una sola regla.** Compiten tres patrones —divisa en los dos extremos,
divisa solo a la derecha, y `plain` sin divisa— y **gana el primero del texto**. `plain`, si
va primero **y hay otro candidato**, debe superar **las dos** pruebas: MAGNITUD
(`_low_looks_like_salary`) **Y** ANCLA LÉXICA (abrir el texto, o ir tras una palabra de
sueldo dentro de `_ANCLA_VENTANA`=28 caracteres). La cláusula «y hay otro candidato» no es
cosmética: exigirlas siempre rompe tres filas reales (`12-42508 EUR`, `21-42508 EUR`,
`720-2400 EUR`). Sin el ancla, un año o una referencia secuestran el parseo:
`Réf. 2025-0043 — Salaire CHF 92'000 - CHF 108'000` daba `(2025, 43)` y tras el swap se
persistía `salary_min_chf = 43`. **Cuatro cotas como `xfail(strict=True)`** en
`backend/tests/test_g8_corpus_prosa_salarios.py` — estrictas a propósito: si un ciclo
futuro las arregla, la suite avisa con un XPASS en vez de dejarlo pasar en silencio.
Ver el registro de cotas.

**Marcas de agua con lag e idempotencia por elemento.** El healthcheck de `core-capture`
mide el lag con una marca **re-anclable**: cualquier mejora re-ancla (una ráfaga legítima
que luego se drena no puntúa) y solo puntúa quedarse por encima del suelo SIN mejorar
durante toda la ventana. El gate no sella un ciclo si queda una fila de
`shadow_change_log` sin aplicar con `lsn <= watermark`. La idempotencia es **por elemento,
no por lote**: `shadow_inbox` tiene `PK(consumer_id, event_id)` con `ON CONFLICT DO
NOTHING`, y en delivery los marks se persisten **por entrega**, no al final del lote —
antes, un solo evento venenoso condenaba a sus hasta 99 vecinos ya entregados.

**Redacción de secretos.** Vive en **un solo sitio** (`backend/utils/redact.py`) y se aplica
en las dos raíces —el filtro de logging sobre los handlers del root, y
`fetch_diagnostics.record()`—, no parcheando cada llamada. Cubre `nombre=valor` en query
(con guion interno: `x-api-key=`), cuerpos JSON de error, cabeceras, `Authorization:
Bearer` y el `usuario:secreto@host` de una URL con userinfo. **Lo que NO cubre, y se dice
para no repetir la afirmación falsa del docstring anterior:** la credencial que viaja en el
PATH sin nombre (jooble, `/api/<clave>`), indistinguible de un segmento de ruta; se tapa en
origen con el `diag_url` que el provider ya pasa. **Falso positivo conocido y aceptado**
(medido sobre 18 025 líneas del journal vivo): de 36 líneas que cambian, 31 son el banner
de arranque de Celery (`key=ai` es una *routing key*, no una credencial). Se acepta porque
no pierde información; un umbral de longitud lo taparía pero dejaría fuera las credenciales
cortas.

**Gracia del gate: criterio definitivo = EVIDENCIA POSITIVA.** La gracia exige evidencia
positiva de que el proyector obró bien; **la ausencia de evidencia nunca la concede**
(*ausencia de evidencia ≠ evidencia de ausencia*). Dos fuentes, por prioridad: (1) el
**último cambio APLICADO** del pk, espejando `projector._is_close` — si es CIERRE ⇒ gracia;
si es APERTURA y aun así no hay slot ⇒ **pérdida**, y esta rama manda sobre la 2; (2) sin
ningún cambio aplicado que consultar, el estado durable: si el pk tiene slot `legacy:*` ⇒
gracia, y si no hay slot NI cambio aplicado ⇒ pérdida. Costó **cuatro reinterpretaciones**,
cada una con su modo de fallo reproducido: por `first_seen_at` (falso ROJO en
reactivaciones), por «existe un cambio pendiente» (falso VERDE: una pérdida real
enmascarada por un UPDATE rutinario), por la FORMA del slot (falso ROJO de vuelta), y por
`u.pk IS NULL` (falso VERDE que **la purga de retención fabrica sola** — en el clúster real
6 979 de 10 805 jobs ya no tenían fila en el log).

**El contador `claims` y la retirada por veneno** (`core0032`). `attempts` = transportes
EJECUTADOS, y se consume en el **resultado**, nunca en el claim (garantía: no gastar
intentos sin transporte). Eso dejaba un agujero: un payload que **mata al proceso** del
dispatcher (OOM, segfault) nunca marca, así que no consume `attempts`, el dead-letter por
agotamiento **jamás llega**, y como el claim ordena por `next_attempt_at NULLS FIRST` ese
mensaje ocupa la **cabeza de la cola** y bloquea al resto. De ahí un contador propio:
`claims` = **reclamos consecutivos SIN resultado**, a 0 en cuanto la entrega produce uno.
Dos dead-letters distintos: `retire_exhausted` (`attempts >= 8` = destino caído) y
`retire_poisoned` (`claims >= 25` = veneno). **Por qué 25:** con el beat cada 5 min son
~2 h de crash-loop ininterrumpido sobre el MISMO mensaje, y como **triplica** `MAX_ATTEMPTS`
un destino simplemente caído siempre muere antes por la vía normal. Backfill
`claims = attempts`: cota inferior honesta que jamás mete una fila sana en el umbral.

**La garantía de «un solo proceso» no está donde se creía.** Se creía que la daba una
guarda de test que comprobaba que uvicorn no viera `WEB_CONCURRENCY` ni `UVICORN_WORKERS`.
No: **lo que importa es cuántos procesos arranca uvicorn, y eso no lo contaba nadie**. La
garantía real vive en el **`--workers 1` explícito del entrypoint** — con él,
`Config.__init__` recibe `workers=1` y la rama `if workers is None and "WEB_CONCURRENCY" in
os.environ` no se ejecuta, cerrando las tres puertas de una vez. La guarda vigente **cuenta
procesos de verdad**: arranca un uvicorn real con las tres puertas abiertas y cuenta
`Started server process` (4 con la mutación, 1 sin ella). Importa porque el rate limiter
—incluida la puerta de fuerza bruta del login— usa `MemoryStorage`, o sea **estado de
proceso**, y el README lo omitía de su lista de subsistemas dependientes.

**Coacción de repertorio.** Forzar toda cadena que se persiste o se sirve al repertorio que
Postgres/UTF-8 admiten: lo que `json.loads` acepta pero el driver no puede codificar. Dos
vectores, **U+0000 (NUL)** y **surrogate suelto (U+D800–U+DFFF)**, y dos niveles —valores y
**claves de dict**, porque una clave hostil revienta donde nadie puede atraparla: al
construir el error de validación de pydantic, antes de que exista un
`RequestValidationError`. Es idempotente: una cadena sana se devuelve tal cual, así que
aplicarla en una frontera no cambia ningún payload legítimo. La clase se cerró **caso a
caso durante cinco ciclos** antes de convertirse en invariante de módulo; la lección
escrita es que **una sola prueba `payload.encode()` cubre NUL y surrogates a la vez**.

### 19.3 Fase de optimización — qué cambió y qué se ganó

Método declarado: contra la BD real **solo `SELECT` y `EXPLAIN (ANALYZE, BUFFERS)` sobre
`SELECT`**; las escrituras se midieron en bases desechables `bench_*`; ningún servicio
reiniciado por el auditor. **Advertencia de instrumento** que costó una medición falsa: el
muestreo de `pg_stat_*` debe hacerse **desde conexiones distintas** — dentro de una misma
transacción, `stats_fetch_consistency = cache` sirve una instantánea cacheada, y la primera
medición de la auditoría dio «0 escrituras» por eso.

| Cambio | Antes → después (medido) | Fidelidad comprobada |
|---|---|---|
| Fixture de la suite legacy: esquema a ámbito de sesión + truncar solo tablas con filas | **922,92 s → 239,21 s** (3,86×); teardown 658,63→93,57 s; setup 108,75→4,81 s; WAL 227→37 MB | mismo resultado test a test, **y en orden aleatorio** |
| Frontera LLM mockeada (130 ficheros salían a Groq/Gemini reales) | `test_match.py` fase *call* 26,65→11,49 s; su test más caro 11,03→1,06 s | el doble sustituye SOLO la llamada saliente; prompt, parseo, saneo, batching y caché siguen siendo código real |
| Dedup semántico: usar de verdad el índice HNSW | **93,2 → 2,84 ms** por llamada (32,8×); por cosecha **46,6 → 1,4 s** | diff sobre el **corpus entero** (8 162 candidatos): **0 diferencias** |
| Matching etapa 1/2: no transportar el embedding ni recalcular el coseno | total **1 321,3 → 607,9 ms** y 1 181,9 → 461,0 ms (2,2× y 2,6×) | `score_final` **idéntico en las 19 538 filas**; `score_embedding` cambia en 3 filas y en 1e-4 |
| Caché de re-ranking: por OFERTA en vez de por lote | de **cero claves vivas** (nunca acertaba) a acertar | los tests **cuentan las llamadas** al modelo: 2ª corrida sin cambios ⇒ 0 llamadas |
| `run_alembic` en proceso en vez de por subproceso | **1 111,3 → 373,5 ms** por llamada; ~**69 s** por corrida (94 invocaciones) | cada test sigue creando su BD desechable y migrándola desde cero |
| `encode_views` con numpy | **64,10 → 5,45 ms** por lote de 200 (11,75×) | desviación máxima **2,776e-17** = el mismo vector |
| Retención en 4 tablas sin cota | 28,9 MB en 28 días ⇒ **~377 MB/año** acotados | guardas con contraejemplo: las `dead` no se purgan **jamás** |
| `core-capture` reiniciado (O-1) | **228 GB/día de WAL → ~0,2**; 559–979 UPDATE/s → ≤1/s | verificado hoy: slot con **5 088 bytes** retenidos, latido sin variación |

**Resultado agregado:** core **668 tests en 5:31** (antes 654 en 8:12) · backend **2 277 en
3:57** (antes 2 260 en 15:22) · portfolio **1 823** (antes 1 768).

**La mayor ganancia no fue de código.** Fue O-1: `core-capture` llevaba **cinco días**
ejecutando una versión anterior a su propio arreglo, **con el fichero ya corregido dentro
del contenedor**. Python importa cada módulo una vez; el proceso arrancó 2 h 14 min antes
del commit del throttle y nadie lo reinició. El healthcheck estuvo **VERDE los cinco días**
porque miraba que el latido fuera reciente — y a 559 UPDATE/s el latido era fresquísimo:
**la frescura del latido no desmentía la avería, ERA la avería**.

> ⚠ **Dos cifras del propio proyecto se contradicen aquí, y gana la medida.** El commit de
> consolidación `fbe22f0` dice «1.083 escrituras/s, 42 GB de WAL al día». El informe
> La auditoría de optimización del core (retirada del árbol; en git) midió **559–979 UPDATE/s** (48,3 M/día) y **228 GB/día**
> con **dos instrumentos independientes que coinciden dentro del 0,6 %** (diferencia de LSN
> y `pg_stat_wal`). **Las cifras válidas son las del informe.** Se deja anotado porque es,
> otra vez, exactamente la clase de fallo que estos ciclos persiguen.

**Donde los números contradijeron al informe, se documentó.** El barrido de dedup: la
auditoría cifraba el bucle en «14-30 s» y estimaba «2-5 s» después; medido fueron **324,6 s
antes y 307,5 s después** (−5,3 %). El bucle ANN cuesta **un orden de magnitud más** de lo
que decía el informe y el transporte no era su parte cara. Se hizo igualmente porque los
78 MB de socket y los 39 MB de RAM del worker son reales y el cambio son dos líneas de SQL.

### 19.4 Estado operativo — las DOS maniobras pendientes, en orden

> ⚠ **DESFASADA — LEER §20.2.** La canonización **se ejecutó** el 2026-08-27 (commit
> `2462717`), con sus dos mitades, y la regla de «no reiniciar `worker`/`worker-ai`/`backend`»
> **queda levantada**. Lo que sigue vale como descripción del procedimiento y de lo que
> estaba en juego, no como estado.

**Primero: canonización de identidad legacy. Tiene DOS MITADES y solo una vive en
`backend/`.** Los scripts `g3_canonizacion_identidad_arbeitnow_jobgether.sql` y
`g6_canonizacion_identidad_irishjobs.sql` reescriben `jobs.hash`, y eso toca la sombra por
dos caminos:

| Qué | Quién lo arregla | Si falta |
|---|---|---|
| El slot CDC de `jobhunt.source_listings` queda huérfano | **PASO 7c** del script | 6 553 slots huérfanos; la fila legacy se vuelve invisible para la sombra |
| Los `job_ref` de las ETIQUETAS se quedan con el hash viejo | **`shadow/canonical_refs.py`** | 10 de 91 juicios y 1 de 260 pares dejan de resolver, **SIN error** |

Las etiquetas viven en el espacio de nombres del `hash` legacy, no tienen FK y ningún PASO
las toca; `map_job_refs_to_vacancies` deja fuera del dict los refs sin slot **sin error**.
Medido contra producción (SOLO SELECT): de los 91 juicios de los 3 sets congelados se
pierden 10 — **8 del MISMO set** y **6 con `relevance > 0`** de sus 20 relevantes.

**Orden de CINCO pasos** (detalle ejecutable en `jobhunt_core/shadow/RUNBOOK.md` §7):

1. Parar los workers: `docker compose stop core-worker worker worker-ai`.
2. `pg_dump` **incluyendo el esquema `jobhunt`**, y ensayo sobre la COPIA.
3. Los dos scripts SQL, con COMMIT, en cualquier orden.
4. **La otra mitad, con los workers TODAVÍA parados**: `canonical_refs --dry-run` primero
   (se mide), y solo después sin `--dry-run` (se aplica).
5. Arrancar: `docker compose start core-worker worker worker-ai`.

*Por qué el paso 4 va DESPUÉS y no antes:* el mapa `old→new` se reconstruye de la propia
`jobs` sin duplicar la lógica de canonización de URL. Medido ANTES de la maniobra: de
10 805 filas, **0** no reproducen su hash, luego tras la maniobra el conjunto «no reproduce»
es exactamente el de las canonizadas. Y si la maniobra legacy aborta —va entera en una
transacción— este paso simplemente no se ejecuta: no hay nada que deshacer. **Con los
workers parados ningún ciclo de métricas observa el estado intermedio**, que es lo que hace
innecesario que las dos mitades compartan transacción. No hace falta migración nueva: es
una maniobra de DATOS, sin DDL.

*Ventana que hay que respetar:* el re-mapeo **aborta** si una cohorte dedup SELLADA tiene
pares que tocar (`core0025` los hace inmutables). Hoy no hay ninguna cohorte registrada, así
que la ventana está abierta.

**Después: carga del estrato positivo.** Sus **187 pares** se cargan **DESPUÉS** de la
maniobra (G8-N-7): cargarlos antes graba refs que la maniobra invalida, y `--excluir` no
sirve para eso **porque el daño no lo detecta ninguna guarda del loader**.

**Regla vigente hasta entonces: NO reiniciar `worker`, `worker-ai` ni `backend`.** La
maniobra los para ella misma en su paso 1; pararlos antes no compra nada y abre la ventana
en la que un ciclo de métricas podría observar el estado intermedio.
**`core-capture` queda FUERA de esa regla: ya se reinició**, con medidas tomadas antes y
después (contenedor de 2026-08-26T23:06:59Z; slot con 5 088 bytes retenidos y latido sin
variación entre dos muestras — la avería O-1 está cerrada).

### 19.5 Acción de seguridad ABIERTA

> Sigue ABIERTA al cierre de §20 (ver §20.5). Esta es la explicación larga.

✅ **`GEMINI_API_KEY`: riesgo aceptado (2026-08-29), no se rotará y no bloquea nada.** Lo que sigue explica el alcance, no una acción pendiente. El commit `7ef7d89` (G6/P2-2) cerró el canal
—el logger de httpx emitía a nivel INFO la URL completa de cada petición: **13 446 líneas
`HTTP Request:` en el journal del worker, 32 con la clave real de 39 caracteres**— pero
**cerrar el canal no borra lo ya publicado**. Una clave que se publicó está comprometida.
El commit es del **2026-08-26**; el `.env` local sigue con `mtime` del **2026-07-02**, así
que la rotación **no se ha hecho**. Es acción del propietario y no la puede hacer esta
documentación (no se toca `.env`).

---

## 20. Sesión 2026-08-27 (tarde) — LAS DOS MANIOBRAS EJECUTADAS + auditoría externa cerrada

> **Esta sustituye a §19 como sección vigente.** §19 sigue siendo válida como foto de la
> mañana del 2026-08-27, pero sus §19.4 (dos maniobras «pendientes») y §19.5 quedan
> **superadas por completo** por §20.4 y §20.5. Los contadores vigentes son los de §20.0.
>
> Convención de esta sección: **verificado ejecutando o leyendo el código** salvo donde
> diga lo contrario. Lo que no he podido medir va marcado explícitamente.

### 20.0 Contadores vigentes

| Qué | Valor | Procedencia |
|---|---|---|
| Suite core (`jobhunt_core/`) | **675 passed** | aportado por el propietario, **no re-ejecutado** en esta pasada (el encargo lo prohíbe) |
| Suite legacy (`backend/`) | **2 280 passed** · 3 skipped · **4 xfailed** | ídem |
| Suite portfolio backend | **1 823 passed** · 1 skipped | ídem |
| Suite portfolio frontend | **330 tests** (vitest) | ídem; el README del frontend ya lo dice (`fc0b525`) |
| Cadena de migraciones core | `core0001..core0032`, head único `core0032` | **verificado**: `/v1/health` publica `"alembic_expected":"core0032"` y `/v1/ready` `"alembic":"core0032"` |
| Cadena de migraciones legacy | **`b3c7d1a95e42`** (era `a1f2e3d4c5b6`) | **verificado**: `SELECT version_num FROM alembic_version` |
| Release del core desplegada | **`ae7fbf2`** en los tres procesos | **verificado**: `printenv RELEASE_SHA` en `core-api`, `core-worker` y `core-capture`, y `release` en las dos sondas |

El resto de contadores de §19.0 (25 providers, 16 instanciables, 15 scrapers, 9 cadencias,
puertos) no se han tocado hoy y siguen valiendo.

---

### 20.1 ✅ Migración legacy `b3c7d1a95e42` — APLICADA

Aplicada en ventana controlada. La base pasó de `a1f2e3d4c5b6` a `b3c7d1a95e42`.
**37 regresiones de marcas de agua en verde.**
Copia previa: `/home/lothar/Documents/swissjob_pre_b3c7d1a95e42_20260827.sql.gz`.

**Verificado en la base real** (no en el fichero de migración), leyendo
`information_schema.columns`:

| Columna | `column_default` |
|---|---|
| `jobs.first_seen_at` | `clock_timestamp()` |
| `jobs.last_seen_at` | `clock_timestamp()` |
| `match_results.created_at` | `clock_timestamp()` |

Por qué importaba: `now()` es `transaction_timestamp()` y **se congela al ABRIR la
transacción**, así que muchas filas de una misma cosecha compartían marca temporal al
microsegundo y las marcas de agua por tiempo no podían desempatarlas.

---

### 20.2 ✅ Canonización de identidad legacy — EJECUTADA (commit `2462717`)

Autorizada por el propietario. Los dos scripts en la **misma parada de workers**, siguiendo
el orden operativo de G8 paso a paso: parar `worker`/`worker-ai`/`core-worker` → `pg_dump`
con el esquema `jobhunt` (117 MB) → **ensayo en seco contra los mismos datos** → ejecución
en firme con una copia temporal en `COMMIT` → re-mapeo de las referencias del core →
rearranque. **Ensayo y ejecución dieron cifras idénticas.**

Copia previa: `/home/lothar/Documents/swissjob_pre_canonizacion_20260827.sql.gz`.

| Script | Reescritas | Clones fusionados | `match_results` descartados | Slots reapuntados | Slots de clones |
|---|---|---|---|---|---|
| g3 (arbeitnow + jobgether) | **5 419** | **406** | **30**, y **0 con señal del usuario** | **5 263** | **371** |
| g6 (irishjobs) | **879** | **40** | **0** | **879** | **40** |

**La otra mitad**, `jobhunt_core/shadow/canonical_refs.py`, corrió en la misma parada con
los workers todavía detenidos: **6 298** filas canonizadas en el mapa, **10 juicios** y
**162 pares** re-mapeados. Sin ella las etiquetas del oráculo se habrían roto **sin un solo
error**, porque no tienen FK y ningún PASO de los scripts las toca.

**Verificaciones posteriores, re-medidas hoy con SELECT** (no copiadas del informe):

| Comprobación | Esperado | Medido |
|---|---|---|
| Slots huérfanos en las 3 fuentes canonizadas | 924 + 371 + 40 = **1 335** | **1 335** (arbeitnow 1 274 · jobgether 21 · irishjobs 40) ✅ |
| La cifra que delataría el fallo que evita el PASO 7c | **7 477** | no alcanzada ✅ |
| Juicios que siguen resolviendo | 91 de 91 | **91 de 91 — cero etiquetas perdidas** ✅ |
| Pares con sus DOS refs resueltos | ≥ 260 | **261** de 779 de `seed_duplicate_of`; 0 con `job_ref_a = job_ref_b` ✅ |
| `jobhunt.shadow_change_log` sin aplicar | 0 | **0** de 16 173 ✅ |
| Slot `jobhunt_shadow` | activo, retención baja | activo, pocos KB ✅ |

La consulta de huérfanos es la **literal del PASO 7** del script; la de etiquetas reproduce
`map_job_refs_to_vacancies` (`source_listings` de fuentes `legacy:%` con alguna encarnación).

**El GATE-SOMBRA no se invalidó**: no hubo que soltar ni recrear el slot, ni re-sembrar el
snapshot. Los `op=U` llegaron con la pk canónica y encontraron su slot ya reapuntado por el
PASO 7c; los `op=D` de los clones cerraron sus encarnaciones por el camino normal.

> ⚠ **Los ficheros del repo siguen terminando en `ROLLBACK`.** Son seguros por defecto:
> ejecutarlos tal cual es un ensayo. Para repetir la maniobra hay que cambiar esa línea a
> `COMMIT` **en una copia**, nunca en el fichero versionado.

---

### 20.3 ✅ Release inmutable del core (`f728518` + `ae7fbf2`) — DESPLEGADA

P1-3 de la auditoría externa. Cierra la vía por la que un proceso podía servir la release A
con los ficheros y el esquema ya en la B.

- Los **cuatro** servicios del core (`core-api`, `core-worker`, `core-capture`,
  `core-migrate`) dejan de montar `./jobhunt_core` en el compose base. Corren lo que lleva
  la imagen. `docker inspect` sobre `core-api` devuelve **0 mounts**.
- `RELEASE_SHA` se hornea como *build arg* (ARG → ENV en el Dockerfile) y **no** aparece en
  ningún `environment:`, para que no pueda desligarse del código que identifica.
- `/v1/health` publica `release` + `alembic_expected`; `/v1/ready` publica `release` +
  `authoritative`.
- `core-api` gana **healthcheck de compose** contra `/v1/ready`. Sin él, la API estuvo dos
  días en 503 sin que nadie se enterara.

**Verificado en vivo:** los tres procesos publican `release=ae7fbf2`, `/v1/ready` responde
`{"status":"ready","alembic":"core0032","release":"ae7fbf2","authoritative":true}` y
`core-api` reporta *healthy*.

> ### ⚠ 20.3.1 QUÉ CAMBIA PARA CUALQUIERA QUE TRABAJE AQUÍ
>
> **Los comandos del core que deban ver el árbol de trabajo necesitan ahora los dos `-f`:**
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.dev.yml \
>   run --rm core-migrate python -m pytest jobhunt_core/tests
> ```
>
> Sin el override se prueba el código **de la imagen**, y la suite puede salir verde sobre
> código que no es el que acabas de editar. El override es **explícito a propósito**: un
> `docker-compose.override.yml` implícito se aplicaría también al desplegar y devolvería el
> defecto en silencio. Olvidarse del `-f` deja el perfil **seguro**, no el mutable.
> `CORE_CODE_MUTABLE=1` hace que `/v1/ready` conteste `authoritative: false`: verde
> **informativo**, no autorización para operar.

#### 20.3.2 El arreglo que abrió el fallo simétrico — no cites `bf3fbfd` como cierre

Caso didáctico de la regla de oro de §19.1, y lo encontró una auditoría **externa**:

| Versión | `_expected_head()` | Fallo |
|---|---|---|
| Antes de `bf3fbfd` | `@lru_cache` sin clave: expectativa fijada de por vida del proceso | **Falso ROJO** — dos días de 503 con la BD sana |
| `bf3fbfd` (2026-08-26) | releía la cadena del volumen montado, en caliente | **Falso VERDE** — certifica la release B con los handlers todavía en A |
| `f728518` + `ae7fbf2` | `_EXPECTED_HEAD` se lee **una vez al importar**, de la misma imagen que trae los handlers; si no se puede leer, **el proceso no arranca** | — |

Lo que cierra la incoherencia no es la lectura, sino el **despliegue**: sin código montado,
cambiar la cadena exige cambiar la imagen, y eso recrea el proceso.
**Cualquier documento que presente `bf3fbfd` como el cierre definitivo está desfasado.**

---

### 20.4 Auditoría externa independiente del 2026-08-27 — veredicto NO-GO, 4 hallazgos + 2 hipótesis

Informes: `/home/lothar/Public/AUDITORIA_EXTERNA_BUGS_2026-08-27.md` y
`AUDITORIA_EXTERNA_DISENO_2026-08-27.md`. Veredicto **NO-GO con cinco condiciones**.

| Id | Repo | Commit | Qué era |
|---|---|---|---|
| **P1-1** | core | `5a4a7ac` | Un **sobre inválido de Arbeitnow** (cuerpo no-objeto, `data` no-lista, `links` no-objeto) se degradaba a «página vacía» y era **indistinguible del final contractual del feed**. El runner confiaba en ese `complete`: persistía cursor, refrescaba `last_complete_at` y ponía `consecutive_failures=0` — corpus truncado registrado como cosecha completa y, pasado `CORE_CORPUS_STALE_DAYS`, archivado de vacantes todavía publicadas. Arreglo: `ProviderResponseError` (RuntimeError ⇒ **transitorio**, frente al permanente `ProviderConfigError`). **Dos pruebas que FIJABAN el bug fueron reescritas.** El aislamiento **por ítem** dentro de una página bien formada se conserva |
| **P1-3** | core | `f728518` + `ae7fbf2` | §20.3 |
| **P1-2** | frontend | `d0007b6` | Un **refresh en vuelo resucitaba la sesión tras el logout**: `logout()` limpiaba, pero el `tryRefresh()` ya iniciado reescribía los tokens al terminar. En un equipo compartido, «cerrar sesión» dejaba de significar nada (el backend cierra la familia de refresh, pero un access JWT ya emitido no queda revocado). Arreglo: contador de generación **`authEpochRef`** |
| **P2-1** | frontend | `fd1d39d` | **Dos búsquedas concurrentes cruzaban resultados y token de secuencia**: una respuesta lenta aterrizaba sobre la actual, y el «cargar más» siguiente componía la página 2 con los filtros nuevos y el cursor/`seq` viejos — la mezcla de corpus que el token de secuencia se introdujo en G8 para cerrar. Arreglo: **`requestGenerationRef`** |

**Y dos hipótesis que el auditor externo NO elevó a hallazgo resultaron CIERTAS**, ambas
reproducidas antes de arreglarlas:

- **`62ada94` — `useSavedSearches`**: `handleRun` confirmaba cualquier respuesta sin
  comprobar que la tarjeta que la lanzó siguiera expandida. **Dos variantes más** que la
  hipótesis original: plegar o **borrar** la expandida mientras volaba su petición repoblaba
  una vista que ya no existía, y `searchLoading` se quedaba encendido. Misma guarda de
  generación (`runGenerationRef`).
- **`798bbba` — `useKanban`**: aquí el arreglo **NO** fue la guarda de generación. Cada
  mutación optimista guardaba `previousApps = [...applications]` y al fallar restauraba la
  lista **entera**, así que el fallo de un movimiento antiguo revertía también el éxito de
  uno posterior ya aceptado por el servidor. **El rollback SÍ debe ocurrir; lo que sobraba
  era su alcance.** Arreglo: **rollback quirúrgico** — `revertStatus(id, status)` devuelve
  solo la tarjeta afectada, y `handleDelete` reinserta únicamente la borrada en su índice.

> **La lección de método**: la clase de bug («respuesta tardía confirma estado inválido»)
> se repitió en cuatro sitios, pero el arreglo correcto **no fue el mismo en los cuatro**.
> Aplicar la guarda de generación a `useKanban` habría dejado la vista mintiendo igual.

---

### 20.5 Estado operativo — qué sigue abierto

| Qué | Estado |
|---|---|
| **Rotar `GEMINI_API_KEY`** | ✅ **RIESGO ACEPTADO — decisión expresa del propietario, 2026-08-29.** No se rotará y **NO bloquea nada**: ni el despliegue, ni el cutover, ni el merge a `main`. `7ef7d89` (2026-08-26) cerró el canal que la publicaba, verificado, así que no hay exposición nueva; lo que persiste es lo ya escrito en el journal del host. Alcance: consumo de la cuota de Gemini del propietario, sin acceso a datos del proyecto. Ficha completa en `SwissJob/docs/COTAS_Y_DECISIONES.md` |
| **`core-api` sin healthcheck en producción** | ⚠ **ABIERTO.** `docker-compose.prod.yml` y `docker-compose.qnap.yml` definen `core-api` **sin bloque `healthcheck:`** (verificado leyendo los dos ficheros). **No se tocaron a propósito**: son de producción y requieren confirmación explícita. El compose base sí lo tiene |
| **El NAS** | ⚠ **DESACTUALIZADO.** Corre imágenes anteriores a toda esta jornada. **Cuando se suban las imágenes nuevas hay que aplicar allí la MISMA canonización, en el MISMO despliegue**: el código nuevo ya emite la identidad canónica, y una cosecha con código nuevo sobre datos sin canonizar es pérdida silenciosa **y** duplicación del corpus a la vez. Procedimiento: `docs/DEPLOY_NAS.md` §5.4 y `jobhunt_core/shadow/RUNBOOK.md` §7. **Las cifras del NAS serán distintas: hay que re-medirlas allí, no copiarlas** |
| **Carga y congelado de la cohorte de dedup** | 🔄 **EN CURSO** por otro agente al cierre de esta sesión. `jobhunt.labeled_dedup_cohorts` estaba **vacía** al medirlo. **Su resultado NO está documentado aquí porque no se conocía al escribir esto — hay que rellenar este hueco.** El bloqueo que lo retenía (había que canonizar primero) **ya no existe** |
| **El reloj del GATE-SOMBRA** | Sigue **sin arrancar** mientras no haya cohorte congelada. La cadena era: canonizar → cargar el estrato → congelar la cohorte → empiezan los 7 ciclos. **El primer eslabón ya está hecho** |

#### ✅ Regla LEVANTADA: ya se puede reiniciar `worker` / `worker-ai` / `backend`

La prohibición de §19.4 y del prompt del agente externo existía **solo** para proteger la
canonización de un rearranque que hiciera cosechar con código nuevo sobre datos sin migrar.
**La maniobra los paró ella misma en su paso 1, se ejecutó y terminó**; los tres corren desde
el rearranque de hoy. La regla ya no aplica.

---

### 20.6 Dónde está la versión larga

| Tema | Documento |
|---|---|
| Cotas aceptadas, y el estado operativo con marcas `[V]`/`[I]` | `SwissJob/docs/COTAS_Y_DECISIONES.md` §9 (reescrita) y §9.1 |
| Acta de la canonización + orden para repetirla + verificaciones | `SwissJob/jobhunt_core/shadow/RUNBOOK.md` §7 |
| Perfiles de compose, verificación de release y la deuda del NAS | `SwissJob/docs/DEPLOY_NAS.md` §1.1 y §5.4 |
| Los dos informes de la auditoría externa | `/home/lothar/Public/AUDITORIA_EXTERNA_{BUGS,DISENO}_2026-08-27.md` |
| Encabezados-acta de los scripts de canonización | `SwissJob/backend/scripts/g3_…sql` y `g6_…sql` |


---

## 22. Estado vigente — D en canary, calidad desbloqueada (2026-09-04, noche)

> Foto VERIFICADA ejecutando (no copiada). Supera a §21: sus «dos cierres P1
> pendientes», el «benchmark en curso» y su Fase D «por hacer» quedaron
> ejecutados este mismo día. Evidencia sellada con sha256 en los paquetes de
> Public citados; comandos exactos en el historial de commits.

### 22.1 Producción (verificado en vivo)

- **Fase C intacta** (portfolio→core) y **Fase D en CANARY**: `catalog` global
  y `matching` de los 2 perfiles SwissJob en `core_read`; paridad BYTE A BYTE
  del feed BFF↔BD core; 0 fallbacks; motor legacy auto-apagado por perfil
  (gate D.1); harvest/CDC legacy vivos. Rollback de routing ENSAYADO en ambas
  direcciones. Durables migrados (18 feedback/10 búsquedas/7 exclusiones) con
  ensayo previo sobre copia, huella estable y manifiesto de rollback por
  valores. Hallazgo corregido: 1ª página fría del feed ~43 s en J1800 ⇒
  `CORE_HTTP_TIMEOUT_SECONDS=60`. Paquetes: `FASE_D_MIGRACION_2026-09-04/`
  (+CANARY.md), runbook `RUNBOOK_CUTOVER_FASE_D.md`.
- **Checkpoint pendiente de D** (única espera real): corrida diaria del 05-09
  debe registrar `skipped_routing=2` (la del 04-09, 12:08, fue pre-flip con
  0). Después: `core_primary` + DoD ⇒ veredicto `SWISSJOB SOBRE CORE`.
- Backups frescos del 04-09 con **restore PROBADO y RTO medidos** (legacy
  535 s, core 1.296 s) en `/share/Public/swissjob/backups/fase-d-*`.

### 22.2 Matching — los dos P1 de §21.2: CERRADOS (commit `fda0843`, suite 1038/1038)

- Generación con **FOR SHARE hasta el commit** (el trigger bump se serializa
  con la publicación; test con barrera post-revalidación: la mutación de B
  muerde LockNotAvailable).
- **Modelo canónico EXACTO**: `canonical_model_id()` única definición (tarea
  y valla F3 la comparten; comparación por id bajo el lock);
  `declare_active_models` = autoridad única (UPDATE de todas las filas);
  `register_model(active=None)` = bootstrap sin tocar canonicidad. Mordida:
  activar un modelo anterior durante la inferencia publicaba; ahora descarta
  con feed byte-equivalente.

### 22.3 Calidad — bloqueo de hardware RESUELTO (P7-b APTO, Public `2fbad1e`)

- Vía incremental por watermark implementada sobre la frontera única
  (`_persist_eval_rows`; la valla F3 ES el cierre de fotografía) + backend
  `onnx-cpu` en la receta (paridad integrada 6e-06; `onnxruntime` ya en
  requirements de la imagen). Suite del día: **1044/1044** (HEAD `e8043fa`).
- **Bootstrap frío EXTERNO ejecutado en producción**: `xenc-ranknet:v1`
  declarada (inactiva) con el artefacto ONNX del RankNet n436 (huella
  `58483c94…`); 3.600/3.600 pares en caché con eventos. Techo real de
  candidatos CE = 1.800/perfil ⇒ el «frío» son minutos en local.
- **J1800 medido**: día sin delta ~6 min; pico 100 misses = **30 min**
  (18 s/doc; el bucle v1 daba 59 min — corregido y medido); corte por
  presupuesto ⇒ backlog CON señal y fotografía previa intacta; reanudación
  exacta 68/68 sin duplicados. Capacidad ~200 docs/h, margen ≥4×.
- **Holdout de matching localizado y VIRGEN**: `labeled_sets` 'nas-ronda-1'
  (frozen 23-08): P1=34 (`8578eca8…`), P2=32 (`237d36be…`). Runbook del
  examen preparado (`trabajo_v8/RUNBOOK_EXAMEN_HOLDOUT.md`, se sella en el
  freeze). Último dev formal: RankNet n=436 = 0.9872/0.8604 (cobertura
  100 %, v11=440) — con la cautela de solape dev/entrenamiento: certifica el
  holdout.

### 22.4 Siguiente acción (en orden)

1. Checkpoint D del 05-09 → `core_primary` → DoD → `SWISSJOB SOBRE CORE`.
2. Release nueva de árbol limpio (incluye onnxruntime + watermark +
   revalidación atómica) → deploy NAS verificado (`/v1` autoritativo).
3. FREEZE (código+receta+modelo+política+cohortes+universo+juicios) →
   **examen ÚNICO de holdout** → si pasa: promoción transaccional + racha
   7×24 h (reinicia ante cualquier despliegue que toque lo congelado).
4. E y F según el plan, solapando la racha con preparación local.


---

## 23. SWISSJOB SOBRE CORE — Fase D CERRADA (2026-09-05, tarde)

- **Veredicto declarado: `SWISSJOB SOBRE CORE`.** DoD completo con evidencia
  en `FASE_D_MIGRACION_2026-09-04/CANARY.md` (checkpoint en forma fuerte: la
  cadena diaria OMITE la etapa de matching entera; flip a `core_primary` de
  catálogo+matching; servicios puros sin fallback verificados; un solo
  escritor por capacidad).
- Release `099cf6d` en los 4 servicios core del NAS (authoritative). Ráfaga
  fría post-redeploy cubierta: timeout 120 s + calentamiento en el runbook.
- Escrituras durables (feedback/candidaturas/búsquedas) siguen en el
  escritor LOCAL hasta el flip de escritura (patrón del piloto E.3); el
  harvest legacy + CDC continúan (Fase F los porta).
- **Siguiente**: ventana de estabilización breve de D → FREEZE
  (código+receta+modelo+política+cohortes+universo+juicios) → examen ÚNICO
  de holdout (nas-ronda-1 virgen) → si pasa, promoción transaccional +
  racha 7×24 h → E/F.


---

## 24. Examen consumido — NO-GO DE CALIDAD (2026-09-06, noche)

- **Fase D CONFIRMADA en operación** (no solo declarada): ciclo diario
  completo del 06-09 en `core_primary`, gate D.1 omitiendo el matching
  legacy, core evaluando por su cuenta, **0 errores de core en el BFF en
  24 h**. `SWISSJOB SOBRE CORE` se sostiene.
- **Examen único EJECUTADO y CONSUMIDO** (`EXAMEN_HOLDOUT_2026-09-06/`,
  veredicto `b848e71`): candidata `xenc-ranknet:v1` = **0.5856 (P1) /
  0.7438 (P2)** sobre universo de pares NO VISTOS, cobertura 100 %.
  Baseline: 0.4662 / 0.3502. **NO-GO DE CALIDAD**: el umbral 0.60/0.60 es
  absoluto y P1 queda a 0.0144. `cosine-baseline:v1` sigue canónica; la
  racha NO se abre.
- **Holdout CONSUMIDO**: no se reabre, no baja el umbral, no pasa a
  desarrollo. Otra promoción exige holdout independiente nuevo.
- **Corrección del registro histórico**: el 0.9872/0.8604 de desarrollo
  (ronda 3) estaba inflado por memorización — su top-10 tenía 9/10 pares
  vistos en entrenamiento. NO citar números de desarrollo de esta campaña
  como calidad. El dato válido de calidad del ranker aprendido es
  0.5856/0.7438.
- **Lo que el examen deja construido y utilizable**: circuito de examen
  reproducible (diseño sellado antes de diagnosticar, pool sellado antes de
  etiquetar, evaluación única), vía P7-b incremental APTA, release
  `099cf6d` autoritativa, y la constatación de que la candidata SÍ
  generaliza mejor que la baseline (+0.12/+0.39).
- **Siguiente**: Fases E y F (unificación completa). La calidad del ranker
  vuelve a desarrollo con una palanca distinta: el etiquetado ciego del
  examen señala que el techo de P1 lo pone el CORPUS (4 «2» en 36 ítems,
  24 sin descripción), no el ranker — atacar datos y sourcing antes que
  otro entrenamiento.


---

## 25. Cierre de la revisión externa 2026-09-07 (estado por fase)

> Separado como pide la revisión: **corregido en código · ensayado ·
> desplegado · confirmado en operación** son cosas distintas.

| Contrato | Corregido | Ensayado | Desplegado | Confirmado en operación |
|---|---|---|---|---|
| A presupuesto + publicación cache-only | ✅ | ✅ (repro determinista) | ✅ `38c51ab` | n/a (sin política CE activa) |
| B1 feed vacío publicable | ✅ | ✅ | ✅ | ✅ |
| B2 escritor único de exclusiones | ✅ | ✅ (BFF+core) | ✅ | ✅ **feed servido con 0 infractores** |
| C sello con restricción efectiva | ✅ | ✅ | ✅ | n/a (sin examen en curso) |
| D señal del proyector | ✅ | ✅ | ✅ | n/a |
| P1-1..P1-5 y P2 (ronda anterior) | ✅ | ✅ | ✅ | ✅ |

**Suite: 1060/1060 en dos pasadas.** Las 5 reproducciones de la revisión
están incorporadas a la suite oficial y pasan.

### Ensayo VÁLIDO (el anterior no lo era)

`pg_restore --exit-on-error` sin errores y **paridad estructural verificada**
contra el origen: 145 constraints, 97 índices, 33 triggers, 0 constraints sin
validar. Sobre esa copia: `core0041` aplica limpia, el migrador escribe 33
filas + 7 exclusiones con 0 sin resolver, y el rollback devuelve el estado
**byte a byte**. El ensayo del 04-09 corría sobre una copia SIN constraints
(`--no-owner` + errores suprimidos) y por eso no podía validar nada de esto.

### Defecto que el ensayo fiel destapó

El rollback pasaba `dismissed_at` como cadena ISO a un parámetro timestamptz
y asyncpg lo rechaza. Solo se dispara al restaurar una fila que YA tenía esa
marca — los 14 thumbs_down migrados. Consecuencia honesta: **el rollback de
la migración del 04-09 nunca fue ejecutable**; no hizo falta usarlo, pero la
reversibilidad que se declaró entonces no era real. Corregido con regresión.

### Desplegado y confirmado (2026-09-07)

- Release **`38c51ab`** en los 4 servicios core; `alembic core0041`;
  `/v1/health` y `/v1/ready` `authoritative: true`. Paridad por CONTENIDO
  (177 ficheros, `ae75ad21…`) — nunca `docker save` en el NAS con la BD viva.
- Remigración en producción: **33 filas de feedback nuevas** (los clones de
  ofertas rechazadas que faltaban) + **7 exclusiones**, 0 sin resolver.
- **Canary sobre el FEED SERVIDO** (no filas): ambos perfiles con
  `CoreMatching` puro; P1 1.598 y P2 1.595 ofertas. **0 ofertas que las
  exclusiones deban filtrar** en el feed de P2, con control negativo: 1.547
  ofertas vivas casan esos patrones y 780 tenían evaluaciones históricas de
  P2 — es decir, filtran de verdad.

### Sigue abierto

1. **Campaña de calidad**: el instrumento está reparado (sello + CLI +
   frontera), pero reabrir exige **holdout independiente nuevo**. El
   `0.5856/0.7438` NO se certifica; tampoco puede deducirse que reparar el
   instrumento no pueda elevarlo — recuperar candidatos antes perdidos puede
   cambiar el ranking. El NO-GO se sostiene por **ausencia de examen válido**.
2. **Fases E y F** — hitos distintos, no bloqueados por lo anterior.
3. Tres pruebas de heartbeat con sensibilidad temporal (ajenas a este delta):
   estabilizarlas con sincronización observable y plazos de BD realistas.



---

## 26. Continuación local de Fase E — 2026-09-08

Esta sección prevalece sobre las fotografías anteriores; no reescribe sus cifras.
**SwissJob sobre core** sigue siendo distinto de **GO de calidad** y de
**unificación completa**. No hay un examen de calidad nuevo ni una racha iniciada.
El 0.5856/0.7438 histórico NO es certificable (véase §25).

- Operación: el [acta del 08-09](SwissJob/docs/CIERRE_DESPLEGADO_2026-09-08_CODEX.md)
  registra el despliegue posterior d908ea2/core0042. Las continuaciones E.1–E.3 son
  LOCALES: no despliegan documentos al NAS ni cambian sus escritores.
- **E.1:** almacén/API de documentos inmutables y core0043 implementados y probados;
  esquema nuevo no desplegado en operación.
- **E.2:** adaptadores SwissJob y Portfolio probados con HTTP/PG reales, SIN vincular
  al routing vivo. Canary de documentos: escrituras exclusivamente locales.
- **E.3:** alta atómica CV+carta y adaptador de lote. La transacción incluye documentos,
  eventos y recibo; siguen pendientes orquestación recuperable, PDF, inbox,
  migración histórica/rollback y flip. Evidencia exacta de suites y commits en
  [acta E.3](SwissJob/docs/CIERRE_LOCAL_E3_2026-09-08.md), no extrapolar los números
  de §25 a una release o despliegue nuevo.
- Runtime LOCAL alineado a d908ea2/core0042 tras backup/restore estricto y ensayo;
  3.000 cambios drenados, staging pendiente 0 en la comprobación de cierre E.2.
  **Cuatro ofertas locales activas sin listing core** siguen pendientes de
  reconciliación dirigida, no resueltas por drenar la cola. Identidades y prueba:
  [acta E.2](SwissJob/docs/CIERRE_LOCAL_E2_2026-09-08.md).

Siguiente orden: identidad/body durable de generación → caché coherente y goldens
JSON/PDF → inbox/retención → migración con manifiesto y vuelta ensayada → build,
freeze/drenaje y corte por consumer. Colegios aparte; F solo tras verificar cada
sustituto de tareas/fuentes legacy. La regla única y sus criterios están en
[contrato de Fase E](SwissJob/docs/FASE_E_DOCUMENTOS_2026-09-08.md).

---

## 27. E.4 — correcciones locales y decisión previa al corte (2026-09-08)

Prevalece sobre §26. **E/F no están cerradas; NAS sin cambios en este tramo.**
Acta: [avance E.4](SwissJob/docs/AVANCE_E4_2026-09-08.md).

- SwissJob `eb70efd`: caché por inputs/receta, guarda solo UUID y confirma
  existencia/ownership en el almacén. Cierra borrado→SET tardío→generar y cambios
  de CV/oferta/matching/modelo. Diez pruebas de caché; BFF completo 2316 passed,
  4 xfailed, más controles adicionales detallados en el acta.
- Portfolio `f74838b`: ninguna transacción durante los LLM, revalidación de
  candidatura/propietario y pareja confirmada junta. Suite 1892 passed, 1 skipped,
  3 ensayos de migración deseleccionados. Probado además con PostgreSQL real;
  mordida del padre: `CV held a database transaction`.
- Acceso NAS LAN `nas` comprobado. API/captura/BFF healthy, Portfolio arrancado;
  solo lectura, sin nuevas imágenes/routing ni certificación renovada del feed.
- **Decisión solicitada al propietario:** al eliminar una candidatura,
  ¿conservar documentos hasta baja explícita/retención, o borrarlos también?
  Portfolio actual usa CASCADE; core E.1 no tiene FK a la candidatura.
- **Prueba obligatoria del siguiente tramo:** candidatura creada SOLO en core →
  generar/listar/PDF/borrar. El generador aún consulta la candidatura LOCAL y
  no puede darse por integrado por pasar fixtures locales. Inventario de hoy:
  cero candidaturas persistidas en ambas tablas (no incluye bookmarks puros).
- Siguen pendientes operación recuperable/body durable, goldens PDF, inbox y
  retención, migrador/rollback, canary, colegios y F. No activar escritor core
  antes de esos cierres. Calidad: holdout independiente nuevo; no racha iniciada.

---

## 28. E.5 — conservación ratificada e interfaz de documentos (2026-09-08)

Prevalece sobre §27. **La decisión del propietario ya está tomada:** conservar
CV/carta al borrar candidatura, hasta borrado explícito o vencimiento de retención
(Portfolio mantiene 180 días). No volver a pedir esa decisión como bloqueo.

- Backend Portfolio **be5a1f1**: referencia opaca + snapshot; generación consulta
  autoridad local/core sin clonar candidaturas. Binding explícito
  `CORE_DOCUMENT_OWNER_USER_ID` ↔ `CORE_PROFILE_ID`; ausente/ajeno falla antes
  de red. Sigue siendo escritor documental LOCAL.
- Migración nueva **qq00r9931t17**: backfill sin pérdida y retirada de CASCADE,
  bloqueos NOWAIT y chequeo de ownership. Downgrade con documentos se niega sin
  modificar datos; vuelta vacía ensayada. Inbox previo integrado por separado
  en **26a3374**, no activado en NAS.
- Frontend **8836254**: biblioteca con todas las versiones sin tarjeta de
  candidatura; PDF/JSON y borrado individual confirmado. No quedan inaccesibles
  solo porque la tarjeta desaparezca del Kanban.
- Verificación: backend **1908 passed, 1 skipped** (migraciones PG incluidas);
  frontend **376 passed**; 3 pruebas de PG/HTTP reales; mordida del padre por
  CASCADE y de los dos accesos sin binding. Lint dirigido y build completados,
  con avisos de build local registrados en el acta.
- Evidencia, alcance exacto de simulaciones y comandos:
  [acta E.5](SwissJob/docs/AVANCE_E5_2026-09-08.md).
  No se ha ejecutado canary de generación core en NAS en este tramo.

**No desplegado. NAS sin cambios; E/F y unificación completa siguen pendientes.**
Última referencia operativa: d908ea2/core0042; no equiparar esos servicios con
los commits locales. Siguiente secuencia: contexto/binding de adaptadores →
operación/body durable con retry sin otro LLM → inbox/PDF/retención → migración
histórica y vuelta fiel → build/freeze/canary/flip documental → colegios → F.
El cambio ajeno de arquitectura escolar se conserva fuera de estos commits.

La calidad sigue sin examen válido nuevo. No se movieron criterios, no se abrió
holdout ni racha. La racha exigible consumiría 168 horas reales una vez apta.

---

## 29. E.6 — transporte recuperable local y bloqueo de restore (2026-09-08)

Prevalece sobre §28. E.5 conserva su decisión e implementación, aún sin desplegar.
Commits E.6: Portfolio `092c415`, SwissJob `cf435b5`. Suite Portfolio completa:
**1924 passed, 1 skipped**, 183,99 s; ningún test deseleccionado, suites en serie.
El adaptador Portfolio ya transporta/verifica el snapshot. Journal de resultados
terminados con UUID estable, commit antes de HTTP, sesiones cortas y retry sin
LLM. ACK borra el cuerpo local; a las 23 h se bloquea el reenvío para no superar
la vida del recibo core. Migración nueva `rr11s0042u18`, downgrade con filas prohibido.

Ensayo HTTP/auth/PG: tres procesos independientes prueban commit core→ACK perdido→
reinicio→recuperación, sin duplicados; 3 pruebas del arnés pasan. Tres escenarios
de migración PG reales pasan. Evidencia y suite final en [acta E.6](SwissJob/docs/AVANCE_E6_2026-09-08.md).
**El journal NO está conectado todavía al generador, UI ni scheduler.**

Preflight NAS: vínculo usuario Portfolio 1 comprobado contra external_ref del
perfil core, dependencias base equivalentes, configuración de red/puerto real
identificada. NAS sin cambios. Se bloqueó por revisión automática de permisos
la exportación completa de la base privada del NAS a una carpeta privada local;
no se ejecutó ni se eludió. Requiere autorización específica de payload/destino.
Sin restore estricto y ensayo no proceder al despliegue que depende de ellos.

Restan integración recuperable completa, PDF/inbox/retención y borrado de dueño,
migración histórica/rollback/canary, colegios y retirada F. GO de calidad separado,
sin examen válido nuevo; no se abren holdout ni racha por estas pruebas.

---

## 30. Portfolio actualizado en NAS y túnel reparado (2026-09-08 noche)

Prevalece sobre §29. El propietario autorizó explícitamente la copia privada de
`proyecto`; restore y ensayo completados. **El bloqueo de permiso queda cerrado.**

- Backend **ddb6651 / rr11s0042u18** desplegado y confirmado por HTTP: health/deep,
  biblioteca y candidaturas 200; DB/Redis healthy. Retención E.5 activa; journal
  E.6 instalado pero todavía no conectado al generador/UI/scheduler.
- Frontend **8836254** publicado por fast-forward en GitHub; Cloudflare Pages y
  check de tests terminados con success. Configuración de API público verificada.
- Se encontró y reparó un 502 previo del túnel: faltaba alias backend en la red
  Portfolio. Se conserva su IP; confianza solo en el proxy concreto, sin rango /16.
- Restore estricto con 24 constraints/51 índices. Ida/vuelta final: datos,
  secuencias y estructura iguales. El ensayo detectó índices ya existentes que
  rompían oo88; nuevo invocador transaccional conservador, sin reescribir revisiones.
- Suite final **1928 passed, 1 skipped**. Contenedor anterior retenido, backup
  pre-cutover privado; no hubo rollback vivo con nuevas escrituras.
- **Schedulers siguen ON**, como antes: no confundir este relevo con quiesce ni
  retirada de harvesters. Se debe resolver el inventario de escritores al cerrar E/F.

Acta completa, límites, hashes y procedimiento de recuperación:
[despliegue Portfolio](SwissJob/docs/DESPLIEGUE_PORTFOLIO_E5_E6_2026-09-08.md).
E/F siguen abiertos. SwissJob/core/modelos/holdout sin cambios; no GO de calidad nuevo.

---

## 31. E.7 desplegado y bypass de matching corregido (2026-09-08 noche)

Prevalece sobre §30. Portfolio **2eb5f31 / rr11s0042u18** activo en NAS.
No se alteraron core/modelos/holdout. La ventana de matching eludía routing con
sus rutas de background: ahora consume core, con fallback canary explícito y sin
warm-up local automático. Canary autenticado: GET/POST/directo, mismos 50 UUID;
3,918 s primera lectura y 1,086 s actualización. Progreso persistido, no LLM local.

E.7 conecta journal/generación/retry y autoridad lectura/PDF/retención; freeze
respetado. Sigue autoridad documental local hasta el corte completo. No declarar
el writer core activo por instalar esta lógica. 1951 tests backend + 387 frontend
+ 17 E2E; contrato HTTP/auth/PG 4/4 y transacción PG 1/1.

Rendimiento NAS: p95 candidaturas 808→186 ms, búsquedas 1909→229 ms;
documentos 351→473 ms. CPU observada 70,4→0,28 %, memoria 1,076 GiB→174,8 MiB.
Muestras acotadas y colecciones vacías, no certificación general de capacidad.

Frontend **3f6d7be local**, publicación detenida por revisión automática que exige
autorización explícita para el GitHub público. Se solicitó permiso para a88649d,
4bfabe7 y 3f6d7be. Sigue publicado 8836254; no falsear CI/Cloudflare ni el alcance
del smoke público. Backend compatible confirmado; backup y contenedor anterior
retenidos. Huellas de datos durables y estructura iguales tras relevo.

Acta con evidencia, límites y siguiente secuencia:
[E.7/matching/rendimiento](SwissJob/docs/AVANCE_E7_Y_MATCHING_OPERATIVO_2026-09-08.md).
Corte documental SwissJob, inbox/erase/migración/rollback, colegios y F son trabajo
abierto, no cubierto por el canary del feed. Logs de cosecha tienen errores de
proveedores pendientes de clasificación/corrección. NO-GO de calidad sigue por
ausencia de examen independiente válido; no se cambió el criterio.

## 32. E.8 local: documentos SwissJob y clasificación de EOF (2026-09-09)

La publicación Portfolio está CERRADA: autorización expresa recibida, frontend
3f6d7be publicado en GitHub/main y checks test/Cloudflare Pages en success.
No reutilizar el bloqueo de publicación de §31 como estado actual.

SwissJob tiene implementados localmente conservación al desaparecer la oferta,
journal recuperable, autoridad documental fresca, freeze, biblioteca paginada y
PDF/retry en UI. Migraciones nuevas d5e9f3071b28 y e6fa04182c39; las publicadas
no se han reescrito. El NAS sigue con la autoridad documental anterior: no se ha
activado core0043 ni efectuado el corte documental en esta sesión.

Portfolio: EOF WordPress corregido localmente para SwissTechJobs/ICTjobs; el
error reproducido abría circuitos por una página terminal válida. No se retiró
ningún productor. Suite dirigida 97 verdes; suites completas documentadas en el acta.

Los CINCO trabajos siguen siendo el encargo, no cinco cierres ya logrados:
documentos (ensayo/corte aún pendiente), colegios, productores/retirada,
erase/export/backups y carga representativa. No sustituirlos por un GO de tests.
La calidad exige examen independiente nuevo, y no se abre racha por estos cambios.

Evidencia, límites y siguiente secuencia:
[acta E.8](SwissJob/docs/AVANCE_E8_DOCUMENTOS_Y_FUENTES_2026-09-09.md).

## 33. Continuación E.9 — portabilidad documental y recuperación (2026-09-09)

**Local, sin despliegue ni cambio de routing NAS.** Evidencia:
[acta E.9](SwissJob/docs/AVANCE_E9_EXPORTACION_Y_RECUPERACION_2026-09-09.md).

- SwissJob exporta documentos de la autoridad activa, copias locales retenidas y
  output preparado del journal; sin truncado silencioso ni transacción durante HTTP.
- El freeze documental cubre también el borrado de cuenta. El recibo confirma solo
  la base local y declara pendientes la confirmación core y el borrado de backups.
- Una operación confirmada cuyo documento ya fue borrado libera la interfaz sin
  regenerar automáticamente. 401/403/503 siguen siendo errores recuperables.
- Contrato HTTP/auth/PG real: 5 pruebas pasadas, incluida exportación core 20+1.
  Frontend: 14 pruebas, lint/build y dos recorridos de navegador sintético correctos.
  Suites completas y duración exacta constan en el acta; no reutilizar cifras E.8.
- NAS comprobado solo en lectura: Portfolio sigue en e7-2eb5f31; servicios core R5
  y SwissJob en marcha. EOF WordPress cefb70a sigue local, no desplegado.

**No se declara cierre integral:** la exportación nueva especifica su alcance y
no incluye todavía todos los durables/eventos/backups. Falta el workflow de erase
remoto confirmado, inbox, migrador histórico y rollback después de nuevas escrituras.
Colegios, sustitución de productores, retirada legacy y carga representativa siguen
siendo trabajo pendiente, no permisos pendientes. Conservar las fuentes actuales.
Calidad requiere holdout independiente nuevo; no abre racha ni cambia umbrales.

Orden inmediato: inbox y erase/export integral → copia fiel/migrador/rollback →
canary/flip documental → colegios/productores → rendimiento/recuperación → retirada.

## 34. E.10 desplegado; canary real y límite del cierre (2026-09-09)

**Prevalece sobre §32–§33.** Portfolio `715c347/rr11s0042u18`, core
`613f1d5/core0043`, BFF `613f1d5/f70b15293d40` y frontend `4dcfa0e`
(bundle `21c07c6`) están desplegados. Core ready autoritativo, worker pong,
sondas sanas; navegador/PDF reales conservan los dos documentos históricos.
Fix EOF WordPress también desplegado. No se ha parado ningún productor.

**Ensayo documental cerrado en copia fiel; corte vivo NO ejecutado.** Inbox y
migrador/rollback están implementados y verificados; siguen pendientes scopes,
delivery, export/erase integral y el flip documental. No confundirlos con las
lecturas de catálogo/matching, que ya están en core_primary.

Durante el canary se descubrió y corrigió un defecto de red: el alias `backend`
mezclaba producción y ensayo R5. Regresión real Docker roja con imagen anterior
y verde con destinos exclusivos. Manifiesto operativo R5 actualizado también.

Suites en serie: core 1136 passed/1 skipped; BFF 2359 passed/3 skipped/4 xfailed;
Portfolio 1975 passed/1 skipped; contratos HTTP/PG/CLI 9 passed; frontend 16 passed.
Los skips y límites están declarados en el acta; no certifican ausencia de bugs.

**Rendimiento sigue abierto:** biblioteca p95 137 ms (2 documentos), journal
135 ms (vacío), pero búsqueda general `/api/v1/jobs/search?limit=20` excede 30 s.
Perfilar el recorrido completo del catálogo core antes de rediseñar la paginación;
no aumentar el timeout ni utilizar una colección vacía para declarar optimización.

**Bloqueo concreto de autorización:** auto-review rechazó crear una API nueva de
exportación integral de datos personales. No se creó ni se sorteó el rechazo.
Requiere autorización explícita de esa interfaz privada autenticada, alcance y
destino del propietario. El permiso previo de dumps/despliegue sigue concedido.
Colegios, productores/F, erase/acks/backups y carga representativa son además
trabajo técnico abierto, no cinco trabajos terminados.

Acta con imágenes, backups, mordidas, límites y secuencia:
[despliegue E.10](SwissJob/docs/DESPLIEGUE_E10_2026-09-09.md).
NO-GO de calidad: sin examen independiente válido; no hay promoción ni racha.

## 35. E.11 — alcance corregido y búsqueda desplegada (2026-09-09)

**Prevalece sobre §34:** el propietario ha descartado la API nueva de exportación
integral. No se implementa ni se exige como condición de cierre; no queda una
confirmación pendiente para añadirla. Se mantienen solo correcciones y los cinco
trabajos acordados. Las exportaciones que ya existían no se eliminan por ello.

Core/BFF c1924d8 desplegados, mismos esquemas core0043/f70b15293d40. Búsqueda de
32.102 ofertas: p95 1,54 s (20 peticiones), frente a >30 s antes. Core 1139 passed
/1 skipped, BFF 2367/3 skipped/4 xfailed. Navegador real: búsqueda, detalle, enlace
de candidatura y móvil correctos. **Remoto aún excedió 10 s: rendimiento no cerrado.**

La siguiente release be65fb5 corrige ese filtro con índice medido sobre copia y
las referencias UUID del generador/importador documental. Está construida y en
verificación completa; no confundirlo con despliegue. Captura efectiva escucha
swissjobhunter_r5_rehearsal, no la base swissjobhunter del BFF público: el borrado
local no acredita borrado remoto. Confirmación erase/copias, corte documental,
colegios y productores/F siguen como trabajo técnico, no permisos pendientes.

## 36. E.12 — catálogo y referencias documentales desplegados (2026-09-13)

Prevalece sobre §35. Core API/worker/captura **44fe6b8 / core0045**, BFF
**be65fb5 / a91c06e3df72**, identidades verificadas en ejecución. API/BFF/captura
healthy; worker pong. Portfolio y frontend sin cambio de imagen; Nginx recargado.
Huellas de routing, políticas, modelos y fuentes idénticas antes/después.

Core **1143 passed, 1 skipped**; BFF **2374 passed, 3 skipped, 4 xfailed**,
en serie. Migraciones y recuperación compatible ensayadas en copia privada.
La recuperación conserva el esquema ampliado; no ejecutar downgrade ni restore
global después de escrituras nuevas.

El primer canary volvió a fallar remoto: índice válido, pero Seq Scan y
relallvisible=0 en NAS. Mantenimiento acotado VACUUM (ANALYZE) de offer_revisions,
sin FULL ni parámetros globales: SQL 8,65 s →159 ms, Index Only Scan, cero heap
fetches. Esta condición operativa se incorpora al runbook tras restore/índices.

Canary final en serie, **33.933 ofertas**: p95 general **1,67 s**, remoto
**0,72 s**, fuente **1,23 s**, texto **0,28 s**; también offset y vacíos correctos.
Navegador: búsqueda/detalle/enlace al portal/móvil y cero errores JS; sin enviar
candidaturas. Muestra acotada, no certificación de toda carga o filtros futuros.

No cambian los pendientes sustantivos: confirmación distribuida de borrado y
copias antes del corte documental, scopes/entrega/flip, colegios y sustitución
con paridad de productores. La exportación integral descartada no reaparece
como requisito. La calidad sigue sin examen independiente válido; no hay racha.

Evidencia, huellas, límites y recuperación:
[E.12](SwissJob/docs/DESPLIEGUE_E12_2026-09-13.md).

## 37. E.13 — borrado coordinado en verificación; Portfolio recuperado (2026-09-13)

No equivale a despliegue del borrado ni a cierre del punto 1. Core/BFF del NAS
conservan las versiones de §36. El código nuevo añade solicitudes durables,
recibos de supresión, confirmaciones por réplica, saneamiento offline y protección
frente a CDC/snapshots antiguos. Core: 1160 pruebas superadas y 1 omitida; BFF:
2384 superadas, 3 omitidas y 4 fallos esperados, en serie. El ajuste final de
retención tiene además 6 pruebas dirigidas. Se ha probado el borrado y rollback
del perfil en una copia privada con datos.

ADR-07 recoge la decisión de backups delegada por el propietario: retención de
7 días, temporales 48 h y restauración saneada obligatoria, sin añadir KMS por
perfil. No se ha retirado ninguna copia real; quedan programación, caducidades
y confirmación operativa. El propietario confirma que Portfolio es personal y su
cuenta no se eliminará: el borrado de esa cuenta y la retirada del CV público/chatbot
quedan FUERA DE ALCANCE, no como bloqueo ni tarea pendiente. Backups, restauración
y las protecciones existentes de SwissJob/core mantienen su alcance.

**Incidencia real corregida:** portfolio_backend llevaba detenido desde
2026-09-10 por una IP secundaria fija que colisionaba con swissjob-worker-r5.
Se retiró esa asignación y se arrancó la misma imagen 715c347, sin modificar datos
ni versión. /health público mediante túnel e interno: 200; consulta autenticada
de su perfil core: 200. No se confunde el 401 anónimo de health/deep con un fallo.

El origen CDC tiene un esquema BFF anterior: actualizar su app completa activaría
también otra sincronización de exclusiones. La réplica de borrado debe ser un
proceso dedicado, sin esos escritores. En el ensayo, el downgrade antiguo bloqueó
correctamente una exclusión pendiente; no se ha vaciado ni falseado esa cola.

Evidencia, decisiones y pendientes:
[E.13](SwissJob/docs/BORRADO_COORDINADO_E13.md).

## 38. E.13 — borrado desplegado y confirmado; backups pendientes (2026-09-13)

Prevalece sobre §37: core d2a38e9/core0046 y BFF público d2a38e9/b46e1230a901
ya desplegados. Origen CDC migrado a b46, con reconciliador dedicado; no se
actualiza su backend ni se enciende otro escritor de exclusiones.

Canary sintético: DELETE 200, token 401, recibo core y acks de ambas réplicas,
re-alta bloqueada, cero payloads privados del canary en captura. Una fila antigua
reinsertada solo para ensayo se purga de nuevo. Inventario real del NAS probado
en la copia privada aislada: ensayo reversible, aplicación e idempotencia;
otros perfiles intactos. Cuentas reales antes/después: 2 público, 2 CDC, 3 core.

Backups actuales creados; 16 archivos inventariados; 8 copias antiguas trasladadas
a custodia privada, ninguna eliminada. Preview detecta 7 vencidos. Su retirada
fue bloqueada por la revisión automática de permisos y está solicitada expresamente.
La programación necesita sesión administrativa autenticada; scripts preparados,
no cron instalado. Siguen pendientes las copias temporales locales. No es cierre
del punto 1. La cuenta propietaria de Portfolio queda fuera de cualquier borrado.

La sonda de feed excedió 30 s dos veces. Comparación puntual en serie: consulta
anterior 21,436 s, nueva 9,970 s, mismo total 1533. Último canary HTTP: ambos
perfiles 200, 20 ofertas, 10,939 s / 11,445 s. No se ocultan los timeouts ni se
confunde esta muestra con un SLO universal. Evidencia y límites actuales:
[Despliegue E.13](SwissJob/docs/DESPLIEGUE_E13_2026-09-13.md).
La vigilancia debe incluir contenedores esperados detenidos, no solo los activos.
No cambia el estado del examen de calidad ni se abre una racha.

## 39. E.13 — punto 1 cerrado en alcance acordado; cron diferido (2026-09-13)

Prevalece sobre §38: el propietario autoriza las retiradas pendientes y difiere
expresamente el cron al cierre final del proyecto. Se han eliminado 7 backups NAS
vencidos (9 vigentes conservados), 9 dumps locales (2.492.020.922 bytes) y el clúster
local desechable con sus cinco bases restauradas y PGDATA/WAL. Cero sesiones antes
de retirarlo; red aislada y ruta exacta verificadas. No se borraron cuentas reales
ni bases de producción. El inventario mínimo anti-resurrección se conserva aparte.

El borrado coordinado y la restauración saneada ya estaban verificados operativamente.
La retirada manual cierra el pendiente autorizado; NO acredita una tarea automática.
Cron, primera ejecución y alarma siguen como deuda explícita del cierre final.
Se mantienen los límites de 7 días/48 horas; entretanto requieren operación manual.
Esto no modifica calidad, racha ni los demás hitos. Evidencia:
[Acta E.13](SwissJob/docs/DESPLIEGUE_E13_2026-09-13.md).

## 40. E.14 — punto 2 cerrado y confirmado en operación (2026-09-13)

Prevalece sobre las referencias históricas a autoridad documental local.
SwissJob y Portfolio resuelven `documents=core_primary`, escritores abiertos,
scopes read/write y entrega HTTP efectiva. Dos históricos migrados sin pérdida,
replay con cero inserciones; origen local permanece en 2/0 documentos.
Doce eventos de importación/alta/baja recibidos y cero journals pendientes.
Aislamiento entre consumers: 404. Ida/vuelta ensayada en restore estricto y fiel.

Core API/worker/captura y BFF SwissJob conservan d2a38e9; core0046/b46e1230a901.
Portfolio ecf1c0a/rr11s0042u18. CLI 0de9101 corrige autenticación de la sonda
privada sin debilitarla. Portfolio corrige respuesta inválida de IA (502 sin
pareja parcial) y presupuesto Groq, medido frente a la cuota real: la primera
ampliación a 8192 no era suficiente y se sustituyó por 4096/razonamiento medio.

Core: 1162 passed/1 skipped. Portfolio: 1979 passed/1 skipped en la versión
final. Canarios reales SwissJob CV/carta y Portfolio carta/PDF, sin duplicación;
borrados únicamente sus tres IDs de prueba. Imagen final Portfolio: CV/carta
sintéticos con proveedor y PDF reales, no se presentan como canary del CV personal.

**PUNTO 2 CERRADO:** con autorización explícita para Groq, la operación original
de pareja CV+carta pasó por HTTP con el CV real y ecf1c0a en 28,16 s.
Lecturas 200, ambos PDF válidos (15.619/10.287 bytes), replay con los mismos
IDs y bajas exactas 204→404. Sus cuatro eventos recibidos; core conserva solo
los dos históricos, hashes idénticos, cero journals y aislamiento 404.
El archivo temporal del canary quedó retirado. No se enviaron candidaturas.
No se cambió código funcional ni se reejecutaron las suites en esta confirmación.
No quedan pendientes del corte documental, migración, scopes o canary.

Biblioteca: 20 GET/BFF, todos 200; p95 683 ms SwissJob / 887 ms Portfolio.
Muestra pequeña, no certificación de carga global. Copias locales del ensayo
retiradas; dos sellos NAS registrados por siete días. Cron sigue diferido.
Sin cambios a colegios, productores, modelos, calidad o racha; sin push.
Evidencia y procedimiento: [Acta E.14](SwissJob/docs/DESPLIEGUE_E14_2026-09-13.md).

## 41. E.15 — punto 3 cerrado y desplegado: colegios (2026-09-14)

Prevalece sobre referencias históricas a colegios pendientes o autoridad local.
Ambos BFF están en `schools=core_primary`, escrituras abiertas. Core tiene
autoridad única del estado escolar; los productores existentes siguen activos,
adaptados a leer configuración y entregar observaciones nuevas al core.
No se han añadido crawlers, schedulers ni funcionalidades de usuario.

Core API/worker/captura 1b8d910/core0047; BFF y worker SwissJob 4e40ffe;
Portfolio 9f8c85a. CLI daf2fad en los dos compose de migración; servicios activos
no requieren esa corrección exclusiva del comando de recuperación.
Seis procesos en ejecución, controles operativos conservados y cero reinicios.
Nginx recargado tras reemplazar el BFF: canary autenticado por frontend público
HTTP 200 y 18 monitores. Sonda directa no sustituye comprobar el proxy.

Importados y reejecutados sin duplicados: Portfolio 6 monitores/92 ofertas;
SwissJob 18 monitores/53 ofertas/54 estados/2 preferencias. Identidades,
contactos y datos materiales conservados; borradores probados también con
ediciones en copia. 35 ofertas enlazadas con GET del corpus 200; 110 históricas
en cuarentena explícita y todavía visibles, no vacantes artificiales.
Cruce entre consumers 404; canarios congelados 503 y abiertos 200, sin escribir
el estado local antiguo ni enviar correos/candidaturas reales.

Restore estricto con estructura contrastada; ida/vuelta íntegra y replay
idempotente. Suites en serie: SwissJob 2396 passed/4 xfailed; Portfolio
1991 passed/1 skipped; core 1205 passed, después 40 escolares sobre el sello
final y 14 sobre el CLI final. No se presenta el total como rerun del último
commit. CLI probado en NAS con los lotes reales sellados, sin escrituras.
GET escolares p95 195–269 ms; 92 ofertas Portfolio 1108 ms (10 GET/ruta,
no prueba de carga global).

Retirados dos clústeres de ensayo y tres dumps locales; otras evidencias privadas
registradas por 48 h y nueve artefactos NAS por siete días. Cron diferido por
el propietario, operación manual hasta entonces. No se tocaron las bases
originales con la retirada ni se hizo push. Productores/F, rendimiento global
y examen de calidad conservan su alcance propio; no hay cambio de políticas.
Acta y recuperación: [E.15](SwissJob/docs/DESPLIEGUE_E15_2026-09-14.md).

## 42. Punto 4 — preparación verificada, corte todavía pendiente (2026-09-20)

El propietario restringió el objetivo activo a **migración de productores y
retirada legacy**. Punto 5, cron y entrega final no se declaran cerrados ni se
ejecutan como parte de esta continuación, salvo precondiciones del corte seguro.
Producción sigue E.15/core0047; no se han habilitado scopes nativos ni cambiado
los escritores. Políticas, holdout y GO de calidad no se modifican.

Código SwissJob: `65150ec` nativos NAV/TheHub; `b19c865` control de retirada por
fuente y protección de tareas CV conservadas. Core completo: **1.462 passed**
(941,12 s, un aviso previo). BFF `b19c865`: **2.440 passed, 3 skipped, 4 xfailed**
(357,77 s, cinco avisos previos), tras 147 dirigidas. Suites en serie, no aceptación NAS.

Avances anteriores del mismo trabajo: JSON/RSS, Zebis, CH Media y PublicJobs
nativos; admisión de 7 días sólo para ALTAS, sin inventar fechas. Las revisiones
del contenido se contrastaron con legacy sobre los mismos objetos públicos.
CH Media usa ID del portal y URL de detalle: su ID ATS/URL de aplicación no
identifican una sola plaza. Falta reconciliar ese cambio con el histórico antes
de activar; no se autoriza escoger vínculos ambiguos por orden.

TheHub: dos descargas completas de 42 ofertas, cero diferencias canónicas.
NAV: paridad sobre 978 ofertas recibidas, pero la rama híbrida quedó parcial
(429 confirmado); segundo intento falló al inicio. Jobgether: 403 observado.
La disponibilidad/cobertura no se declara aprobada por tener pruebas unitarias.

Feedback: ensayo PRE-activación sobre copias aisladas DENTRO del NAS, restore
estricto y equivalencia estructural contrastada. 112 cambios, replay cero,
reversión 112 y replay cero; hashes del estado restaurados, sin cambio del
origen. No se exportó la base SwissJob al ordenador. Copia redundante local de
core retirada; la aislada NAS conserva su plazo/registro de retirada.

Pendientes que impiden cerrar el punto 4: rollback coordinado POST-activación
del feedback; entrega duradera de perfiles públicos (CDC R5 lee otra base);
portado/traspaso de productores restantes, incluidos colegios; búsquedas/avisos,
drenado CDC y retirada de motores con canary por las rutas realmente servidas.
El control por fuente exige drenar/recrear workers: no cancela tareas en vuelo.

Detalle, evidencias y secuencia operativa:
[avance](SwissJob/docs/CIERRE_PUNTOS_4_5_2026-09-19.md) y
[runbook punto 4](SwissJob/docs/RUNBOOK_RETIRADA_PRODUCTORES_PUNTO4.md).
No hay push, eliminación de datos productivos ni certificación de cierre global.

### 42.1 Checkpoint worker NAS — 20-09, 02:11 Europe/Madrid

Sólo `swissjob-worker` actualizado a `d89b6ee` (imagen
`swissjob-worker:point4-d89b6ee`). Orden por intento más antiguo evita relegar
siempre los mismos productores al cortarse el barrido; empates conservan el
registro original. Incluye fencing CV. Mismas dependencias, entorno, colas,
volúmenes y autoridades; listas de retirada vacías, feedback core deshabilitado.
Parada ordenada, salida 0; Celery pong, API healthy, cero reinicios. Core sigue
core0047 y los demás servicios E.15. Ninguna activación de fuentes nativas.

Última validación: core local `cf1be3b` **1.482 passed**; BFF desplegado
**2.444 passed, 3 skipped, 4 xfailed**, suites en serie. La siguiente cosecha debe
confirmar el progreso efectivo; el orden calculado por sí solo no acredita
recuperación de cada portal. **Punto 4 abierto**, con los pendientes de §42.
[Evidencia, límites y reversión](SwissJob/docs/audits/WORKER_PREPARACION_PUNTO4_NAS_2026-09-20.md).

## 43. Punto 4 — primeros cortes confirmados; revisión de pendientes (2026-09-21)

**Estado: abierto y ejecución funcional pausada por el propietario.** Autorizada
la actualización documental y los commits locales, no reanudar cortes ni publicar.
Esta sección consolida actas hasta el 21-09 16:53 UTC; no es una nueva sonda NAS.

### 43.1 Cerrado, sin repetir

- Perfiles: canal durable desplegado y confirmado para ambos perfiles; entrega
  posterior sin pendientes/errores. [Acta](SwissJob/docs/audits/PROFILE_DEPLOYMENT_NAS_2026-09-20.md).
- Feedback: autoridad core activada, 112 cambios conciliados y recuperación
  post-corte ensayada. [Acta](SwissJob/docs/audits/FEEDBACK_DEPLOYMENT_NAS_2026-09-20.md).
- Diez búsquedas SwissJob: IDs/valores conservados, ejecutor core habilitado,
  legacy bloqueado por autoridad; 277 pendientes y 92 ofertas recuperadas.
  Barridos correctos sin búsquedas vencidas: aún no acreditan entrega natural.
  [Acta](SwissJob/docs/audits/SEARCH_CUTOVER_OPERATOR_2026-09-21.md).
- WorkingNomads: primera fuente transferida; 40 ofertas, replay sin segundo
  fetch, cinco vacantes primarias nativas servidas por HTTP y feeds conservados.
  Core `3d5d67a/core0050`, suite registrada **1645 passed**, 2 warnings.
  [Acta y límites](SwissJob/docs/audits/WORKINGNOMADS_CUTOVER_2026-09-21.md).

### 43.2 Pendientes reales y estimación

R1 avisos no vacuos; R2 fuentes ya portadas; R3 productores activos sin sustituto;
R4 lectores/avisos/digest Portfolio; R5 producción escolar; R6 postprocesado sin
captura CDC; R7 retirada final y recuperación. Detalle, referencias, pruebas y
encargo de validación/refutación en el
[informe de revisión externa](SwissJob/docs/PENDIENTES_PUNTO4_REVISION_EXTERNA_2026-09-21.md).

La estimación inicial de unas horas omitió dependencias. El rango posterior no
es una cota: falta conciliar el inventario efectivo de ambos proyectos. El
informe desglosa un subtotal condicionado, excluye portados todavía no censados
y separa esperas. Al reanudar: censo acotado antes de prometer fecha; reutilizar
lo probado y comprobar postprocesado existente antes de exigir reescritura.

No reabrir E.14/E.15 ni confundir autoridad escolar con productor transferido.
Punto 5, cron/alarma y entrega integral quedan separados. El NO-GO de calidad
permanece por falta de examen válido; no añadir su racha al cierre de punto 4.

La copia temporal indicada en el acta caduca el 21-09 a las 20:00 UTC / 22:00
Madrid, sin borrado automático acreditado. Esta actualización no comprueba su
retirada; la pausa no prorroga retención. Conservar separados copia, recibos y
corpus vivo. Sin nuevos cambios funcionales, despliegues ni push en esta sesión.

## 44. Punto 4 — las 16 fuentes cosechadas por el core (2026-09-22)

**La cosecha ya es NATIVA.** 17 scopes habilitados, todos con cosecha completa
del día y 0 fallos; replay 16/16 `skipped`; 45 vacantes nativas servidas por
`CoreCatalog` a través del BFF desplegado, 0 fallos, solo lectura. Release core
`51be757/core0050`, `authoritative: true`; suite 1732 passed.
[Acta](SwissJob/docs/audits/POINT4_CUTOVER_2026-09-22.md).

Portado lo que no existía: `irishjobs` (193 ofertas/7 d) y `financejobs` (144),
ausentes del corpus core desde el 01-09. Paridad pública 25/25 y 10/10.

Cuatro defectos destapados por el corte, **todos nuestros, ninguno del portal**:
NAV pedía 100 páginas donde su productor pide 3 (de ahí el 429); jobgether no
enviaba las cabeceras que su productor documenta como obligatorias (el 403);
irishjobs pedía una novena página a un portal que se ralentiza progresivamente;
y el patrón del slug de jobgether rechazaba el punto de `next.js` o `.net`,
perdiendo el 4% de su feed.

Una decisión aplicada, medida y **revertida**: cerrar las encarnaciones legacy
en la transacción del corte restaba cobertura viva, porque con ventana de
admisión de 7 días el nativo no readmite lo que el portal publicó antes. Las
28.767 se reabrieron por id exacto dentro de la ventana de gracia. Vivas hoy:
45.564, frente a 40.406 antes del corte.

Retirados: disparador R5, worker R5 (exit 0, CDC a 0), frontend R5 huérfano y
la copia temporal caducada (2,6 GB). Recuperación por fuente acreditada con una
ida y vuelta completa sobre jobspresso, sin pérdida de datos.

Primera entrega natural de avisos **acreditada** el mismo 22-09 (evento 07:15:45
→ ACK y notificación 07:19:18); ver §7ter del acta. Abierto: retirada del slot
tras 48 h y aceptación final. `shadow.project` se CONSERVA: es el postprocesado nativo.
Excluidas con causa medida: myscience, gastrojob, tes (bajo volumen),
stelle_admin y schuljobs (sin altas desde agosto).

## 46. Punto 5 — desplegado y medido, contrato AÚN INCUMPLIDO (2026-09-23)

`point5-9d6b46e` en los **cinco** servicios (`core-api`, `core-worker`,
`core-capture`, `backend`, `worker`): se acabó el desfase entre lo declarado y
lo que corre, abierto desde el punto 4. El worker del core ya contiene
`CORE_CAPTURE_ENABLED`, que era la precondición para retirar el slot CDC.

**Dos cambios, cada uno atacando un cuello medido, no supuesto:**

1. **El idioma sale del camino de respuesta.** Se deriva una vez por título y
   se persiste (`job_title_languages`); servir sólo lee. En producción: 1.547
   títulos, 0 pendientes, 0 desconocidos. Tres estados explícitos — ausente /
   encolado / resuelto-como-desconocido—, y este último evita reintentar para
   siempre un título indecidible. Antes se detectaba por oferta servida: 50,1 ms
   × 1.800 ≈ 90 s por petición.
2. **El recorrido del feed se cachea por VERSIÓN.** Trazado por fases: recorrer
   el feed era el **87-89 %** del coste (47,5 s frío, 11,5 s caliente, 18
   páginas). La caché por ETag no lo evitaba porque **el ETag se deriva del
   payload**: el core construye la página igual para contestar 304. Ahora
   `GET /v1/profiles/{id}/matches/version` da un digest sobre
   `vacancy_id:current_eval_id:current_offer_revision_id` y el BFF reutiliza su
   recorrido sólo si coincide exactamente. El frontend no cambia.

**Matriz de aceptación (2026-09-23, secuencial, ≤20 muestras, sólo lectura,
sin proveedores facturables, tras drenar la cola de idioma y reiniciar el BFF
para tener frío real):**

| Escenario | Presupuesto | Medido | Veredicto |
|---|---|---|---|
| Readiness / lectura ligera | p95 ≤ 1 s | health **0,323 s**, ready **0,039 s** | **CUMPLE** |
| Catálogo, 20 | p95 ≤ 2 s | p50 0,718, p95 **2,420 s** | **NO** |
| Feed, 20 | p95 ≤ 2 s | p50 1,284, p95 **3,040 s** | **NO** |
| Pantalla principal, 3.000 | p95 ≤ 2 s | p50 **2,147**, p95 **2,554 s** | **NO** |
| Primera carga — catálogo / feed 20 | ≤ 5 s | 2,920 s / 1,002 s | **CUMPLE** |
| Primera carga — 3.000 | ≤ 5 s | **10,196 s** | **NO** |
| ≥100 muestras en copia · escrituras · frontend · traducción | — | — | **PENDIENTES** |
| Fondo | no crece | `alertas: []`, 17/17 scopes | **CUMPLE** |

Mejora del recorrido principal: **79,265 s → 2,147 s de mediana** (37×), y la
primera carga de ~54 s a 10,2 s. Sin regresión: 0 reinicios, 0 OOM, 0 5xx.
Suites en serie: BFF **2.562 passed + 4 xfailed**, core **1.765 passed**.

**Tarde del 23-09 — regresión retirada el mismo día.** La auditoría profunda
(`SwissJob/docs/audits/AUDITORIA_PROYECTO_2026-09-23.md`) encontró que la caché
por versión servía `feedback` rancio con `CORE_FEEDBACK_ENABLED=True` (el digest
no cubría el estado de usuario y nada invalidaba en escritura), y tres huecos
más: el listing primario fuera del digest, un `total` que podía sobre-contar y
apagar la caché en silencio, y la lectura desgarrada de un recorrido de 18
páginas. Corregido en `17e2b9e` con 9 pruebas rojas→verdes; `core-api` y
`backend` recreados (los otros tres siguen en `9d6b46e` y así lo declaran).
Suites: core **1.770**, BFF **2.567 + 4 xfail**. Un tropiezo con lección:
poner la cláusula de canónica en `feed()` bajó dos nDCG del gate a 0,0 — se
retiró de ahí; la semántica del gate no se toca desde un arreglo de caché.
Pendiente: el canario de escritura con un «me interesa» real del propietario.

**T4 cerrado (23-09).** `ruff check` **31 → 0** y `ruff format` aplicado a los
99 ficheros que nunca habían pasado por él (commit aparte, con su hash en
`.git-blame-ignore-revs`). Cuatro imports muertos retirados, `ProfileErasure`
al `__all__` que le correspondía, dos imports tardíos de `main.py` declarados
con su motivo, y 19 avisos que eran un **falso positivo** del linter sobre el
patrón de fixture compartida de pytest. El CI ahora dispara en **toda rama**
—626 commits se escribieron sin que corriera—, fija la versión de `ruff`,
quita el `--passWithNoTests` que dejaba pasar una colección rota, y añade
`core-lint` (informativo: `jobhunt_core` nunca se ha linted) y `compose-config`
(los composes de despliegue no se validaban nunca). Suite 2.572 passed.
Dos tropiezos anotados: borrar imports «muertos» se llevó un `User` que sí se
usaba —lo cazó `F821`— y el formateo partió firmas largas dejando los `noqa`
en otra línea, reintroduciendo 6 errores. **Queda subir SwissJob** (204 commits).

**T1 cerrado (23-09): borrado el slot huérfano `jobhunt_shadow`.** Retenía
**40 GB de WAL** sobre la base legacy, sin consumidor y creciendo. Autorizado
por el propietario; siete precondiciones verificadas antes de una acción
irreversible —inactivo y sin PID, sin conexiones de replicación, sin walsender,
ningún contenedor ni compose lo nombra, `archive_mode=off`, `wal_keep_size=0`—,
que es lo que prueba que **era la única causa de la retención**. Resultado:
`pg_wal` **41 GB → 81 MB**, libre **432 → 472,3 GB**. No se recicló solo en
4,5 min (PostgreSQL libera en el siguiente checkpoint): se forzó `CHECKPOINT`,
1 min 18 s. El slot vigente `jobhunt_shadow_r5_rehearsal` **sigue activo e
intacto**; core `ready`, CDC 0 pendientes, 0 reinicios. Recibo con el estado
previo completo en `audit-fixes-20260923/T1-drop-slot.receipt`.

**T3 cerrado (23-09, `351c5a0`).** `CLAUDE.md` afirmaba que el camino de
respuesta no detecta idioma; era **falso para `translate=true`**, que es el
default de `/match/results` y lo que usan `/saved` y `/history`. Medido en el
proceso desplegado, con espía en ese mismo proceso y sobre 100 títulos reales:
**127,8 ms por título → 230 s** para las 1.800 ofertas del feed. Ahora el
idioma sale del core, del almacén derivado o de la heurística de caracteres:
**0,08 ms por título y 0 llamadas a langdetect**. Desplegado, 0 reinicios;
suite 2.572 passed. Incluye A19-05: `.gitignore` por prefijo `.env.core.*`
(el fichero del DSN de replicación no existe en el árbol — riesgo latente).
Nota de método: el primer intento de medición contaba las llamadas en un
proceso distinto del que sirve la petición y habría «demostrado» 0 sin probar
nada.

**T6 cerrado (24-09): la sesión del frontend se renueva y sólo muere con 401/403.**
Eran dos defectos, no uno: **no había refresh en ninguna parte** —el access token
vive 30 min, así que a los 30 min había que volver a entrar a mano— y
`useAuthHydration` llamaba a `logout()` ante CUALQUIER error de `/auth/me`, de
modo que un 502 de un segundo borraba los tokens del navegador. Ahora un 401
dispara un refresh compartido (N peticiones en vuelo ⇒ UN refresh) y reintenta;
sólo cierran sesión el 401 y el 403, y un refresh que falla por 500 o por red NO
la cierra. Las tres rutas con `fetch` crudo (CV, filtros, .ics) también renuevan.
28 pruebas nuevas y **tres mutantes que mueren**: quitar el refresh tumba 5,
`endsSession` siempre true tumba 13, y quitar la promesa compartida tumba
exactamente la prueba del refresh único. Contrato comprobado contra el backend
real, no contra el mock.

**Punto 5: matriz de aceptación EJECUTADA (24-09).** Acta:
`SwissJob/docs/audits/MATRIZ_ACEPTACION_PUNTO5_2026-09-24.md`. **En hardware
holgado el contrato SE CUMPLE** en los cuatro recorridos, frío y caliente, n=100.
**En el NAS no**, y queda expresamente pendiente. Lo que el acta dejaba «sin
atribuir» ya está medido: **2 CPUs**, carga 9,43-14,91 y el mayor consumidor de
CPU es `tinymediamanager` (71,55 %), ajeno al proyecto; lo nuestro, postgres
9,45 % y BFF 0,40 %. El mismo código cuesta **0,084 s en copia y 1,45-3,09 s
allí**. Cierra por decisión del propietario: dar CPU, rediseñar la carga (§10.5)
o aprobar otro presupuesto.

Dos cambios desplegados (`point5-6286ca2`): el tope de página del core sube de
100 a 500 —aditivo— y el recorrido pasa de 18 idas y vueltas a **4**, verificado
en producción; y el recorrido se calienta en segundo plano, de modo que el peor
caso del usuario en copia baja de 1,668 s a **0,182 s**. Honestidad sobre el
efecto: en un A/B **controlado por carga** en el NAS la mejora de latencia (22,50
→ 19,53 s, n=3, muestras solapadas) **no es significativa**; lo que es un hecho
es el cambio estructural.

Antes de medir hubo que hacer fiel la copia: en local `/match/results` ni
siquiera pasaba por el core. Y poner `CORE_FEEDBACK_ENABLED=true` para lograrlo
puso **89 pruebas en rojo**: la suite heredaba el entorno del operador. Ahora
`conftest` declara su propia línea base.

**T5 cerrado (24-09): el compose base ya no publica nada a la LAN.**
`postgres` (5435) y `redis` (6380) escuchaban en `0.0.0.0` —alcanzables desde
cualquier equipo de la red— con la contraseña de dev **publicada en el
repositorio**, y `redis` además **sin contraseña alguna**. Ahora todo salvo el
frontend se publica por `${HOST_BIND_IP:-127.0.0.1}`, `redis` exige
`--requirepass` y el compose **no arranca** sin `POSTGRES_PASSWORD` ni
`REDIS_PASSWORD` (`${VAR:?}`, sin defecto). El BFF muere al arrancar si
`DATABASE_URL` trae una contraseña de dev o un marcador de plantilla, salvo
`ALLOW_DEV_CREDENTIALS=true` declarado a propósito.

**Producción no estaba afectada y no se ha tocado**: el NAS corre
`unification-e15-20260914/*.configured.yml` y un compose de Container Station,
no este fichero; los cuatro composes de despliegue publican **sólo el frontend**.

Verificado ejecutando: `ss -ltn` con los cinco puertos en `127.0.0.1`; PING crudo
al 6380 → `NOAUTH`; los cuatro puertos **rechazados desde el NAS** hacia esta
máquina; `compose config` sin contraseñas falla con el mensaje de cada variable;
`control.ping()` de Celery → **2 workers** por el broker autenticado; guardia de
credenciales **7/7** casos.

Dos trampas que el plan no preveía y que sólo se vieron midiendo: (a) `docker
compose port` rinde el **contenedor** y `config` el **fichero** — `core-api`
parecía en loopback por deriva de un contenedor viejo, y el siguiente `up -d` lo
habría reabierto; (b) **Celery lee `CELERY_BROKER_URL` del entorno y esa variable
gana** sobre la que le pasa el código: con la URL sin credencial en `.env` el BFF
entraba y **los dos workers quedaban fuera** en bucle de `NOAUTH`. Esas tres URLs
ya no se declaran en `.env`; `config.py` las construye desde `REDIS_PASSWORD`.
Costura nueva: `scripts/check_compose_exposure.py`, en CI, con 3/3 controles
negativos que muerden por su propia condición.

**Hallazgo nuevo (H15), pendiente de decisión.** De todos los contenedores del
NAS, **`portfolio_db` es el único que publica un puerto: `0.0.0.0:5435`**, y se
comprobó alcanzable desde otro equipo de la LAN. La contraseña no es trivial (20
caracteres), pero es una base de **producción** escuchando en toda la red. Si no
es a propósito para conectarse con un cliente gráfico, va a `127.0.0.1:5435`.
Queda también sin resolver el `redis` de producción **sin contraseña** (sólo
alcanzable dentro de la red de Docker) → T13, con confirmación.

**Segundo hallazgo (H16), destapado al recrear.** El compose base fijaba
`image: swissjob-core:dev`, un build del **04-09**, mientras los contenedores del
core que corrían usaban `swissjob-core:d908ea2`, del **08-09**: al recrear,
`core-api` quedó `not_ready` (imagen esperando `core0040`, base en `core0042`).
**Cualquier `docker compose up -d` habría hecho lo mismo** — era una mina puesta,
no un efecto de T5. Restaurado apuntando `:dev` a la imagen viva (la anterior se
conserva como `swissjob-core:dev-20260904`): `ready`, `release d908ea2`,
**`authoritative: true`**, mejor que el `unknown`/`false` de antes.

**H16 / A19-21 cerrado (24-09): el core local sube de `core0042` a `core0051`.**
La deriva de fondo era de 142 commits y 9 migraciones. Lo que la hizo aplicable,
comprobado antes y no supuesto: **ningún `upgrade()` tiene una sola sentencia
destructiva** —los `DROP` están todos en el `downgrade()`— y **producción ya
corría `core0051`**. Esto último costó un susto: la primera consulta dio
`core0029` porque pregunté a la base equivocada. El core de producción **no usa
`swissjobhunter` sino `swissjobhunter_r5_rehearsal`**; en la primera queda un
esquema `jobhunt` residual (50 tablas, 389 MB, congelado en `core0029`) que no
usa nadie y que es una trampa de diagnóstico (A19-22).

Hecho en este orden: imagen reconstruida del árbol con
`RELEASE_SHA=$(git rev-parse --short HEAD)` → **`d63f74b`** (antes `unknown`),
fijada también como `swissjob-core:d63f74b`; `core-migrate core0043→core0051`;
recreados `core-api`, `core-worker` y `core-capture`. Verificado ejecutando:
`ready`, `alembic core0051`, `release d63f74b`, **`authoritative: true`**; los
tres contenedores sobre la **misma** imagen que resuelve el compose;
`shadow_change_log` sin aplicar **0**; slot activo con 344 kB; **0 errores** en
los logs; el BFF alcanza el core nuevo; suite del core **1.769 passed, 1 skipped**.
Recibo (antes y después, con el camino de vuelta) en
`SwissJob/audit-fixes-20260923/A19-21-core-migrate.receipt`.

Costura nueva: `scripts/check_core_release.py` compara **lo que el compose dice
con lo que corre** —la comparación que nadie hacía— y exige `ready`, release
nombrable y `authoritative`. Cuatro controles negativos, cada uno mordiendo por
su propia condición, más un control del control que pasa.

**A19-23 cerrado (24-09): el linter cubre ya los 799 ficheros del repositorio.**
`scripts/` (20) y los `.py` de `docs/` (5) no los miraba nadie —`backend-lint`
corre con `working-directory: backend`, así que su `ruff check .` no llegaba— y
`jobhunt_core` arrastraba 358 avisos tolerados desde T4. **411 → 0**, y ninguno
silenciado en bloque.

Lo que **no** se tocó, y por qué se supo: 263 sitios eran fixtures de pytest, 39
eran `pytestmark` (un `skipif` re-exportado) y uno era un import por efecto
lateral (`arbeitnow` llama a `register_handlers()` al cargarse). Quitarlos es lo
que un `ruff --fix` habría hecho. **Medido**: al retirar el `db` de
`test_integration_school_feedback.py`, el test pasa de `1 passed` a ERROR de
fixture, porque `school_db(db)` la resuelve en el espacio de nombres del módulo.
`--collect-only` **no** lo detecta: las fixtures se resuelven en el setup, no en
la recogida. Los `pytestmark` son peores: quitarlos haría **correr** tests que
deben saltarse, y en verde.

Arreglado de verdad: 4 avisos en código de producción del core (tres imports
muertos verificados uno a uno y un `lambda` asignado), 21 imports muertos más en
tests, y `test_review_evidence_20260907.py`, que eran tres sondas pegadas con su
bloque de imports cada una (`asyncio` importado tres veces) — imports unidos y
secciones marcadas, con los mismos 5 tests que antes.

El core queda además formateado (251 ficheros, commit aparte y en
`.git-blame-ignore-revs`), con **AST idéntico en los 335**, y por eso
`core-lint` deja de ser informativo y pasa a bloquear.

Suites tras todo ello: core **1.769 passed, 1 skipped**; BFF **2.590 passed, 4
xfailed**. Y un efecto colateral que conviene anotar: el BFF pasa de «2.587
passed, 3 skipped» a «2.590 passed», **sin skips**. Los tres eran los extremo-a-
extremo de `test_aseam_e2e.py`, que se saltan si `/v1/ready` no devuelve 200 —
es decir, llevaban saltándose en silencio mientras `core-api` estuvo `not_ready`
por H16. Arreglar H16 los devolvió a la suite.

Una caída aislada en una corrida:
`test_heartbeat_advances_on_keepalive_without_traffic` falló una vez bajo la
carga de una suite de 20 minutos y pasó **3/3** ejecutado aparte, con el fichero
de AST idéntico. Es flaky por temporización, no regresión (A19-24).

**Dos falsos verdes propios, que es lo que más conviene recordar.** (1) Formatear
partió los imports largos y dejó 32 `# noqa` en la línea equivocada: los errores
REAPARECIERON después de formatear — el orden es arreglar → formatear →
RE-comprobar. (2) Mi bucle de convergencia contaba líneas que empiezan por la
ruta, y con el formato de salida por defecto ninguna lo hace: declaró cero
errores cuando quedaban cuatro. Se descubrió al comprobar el repo entero y mirar
el **código de salida**. Y un tercero evitado por poco: `--select RUF100`
desactiva las demás reglas y hace parecer inútil TODO `noqa`; lo correcto es
`--extend-select`.

**23-09 16:02 UTC — INCIDENTE: el NAS se quedó sin contenedores.** Los **42**
parados en seis segundos (SwissJob completo, Portfolio, Novafeed y servicios
ajenos) porque **se reinició el daemon de Docker**; `restart: unless-stopped`
no levantó ninguno. ~30 min de servicio caído, **sin pérdida de datos**:
tras restaurar, 22 contenedores en pie, `core-api` `ready`/`17e2b9e`/
`authoritative: true`, CDC a 0 y 15/17 scopes sin fallos. Descartados OOM,
reinicio del NAS, disco lleno y el despliegue. Sin atribuir el reinicio del
daemon; anotado que el volumen de sistema de QTS está al 84 % con 64 MB libres.
Acta: `SwissJob/docs/audits/INCIDENTE_NAS_2026-09-23.md`.

**Bolsa de empleo (Portfolio).** Corregido el diagnóstico: la ventana que el
propietario describía era `JobBoardTabbedWindow` del Portfolio, no `/match` de
SwissJob. Su tarjeta no mostraba **ninguna** descripción y el backend servía
marcado crudo en 33 de cada 40 ofertas. Desplegado `board-483fad0`: HTML
limpiado en la frontera, título traducido que **enlaza a la oferta** y resumen
de 2-3 frases; en producción, 0 ofertas con marcado. Un corte de base ya no
convierte la decoración en un 500 (lo cazó `test_jobhunt_routing`). Suites:
2.041 + 394. **Pendiente: volver a publicar el frontend** (`f2ce188`, sin push).

**Tarde del 23-09 — panel de ofertas del Portfolio.** El propietario reportó que
«AI Job Match» mostraba títulos sin traducir, ninguna descripción y un 503
«Core unavailable and no local fallback is ready». Medido: el 28,5 % del feed
no tiene descripción en ninguna fuente (jobgether por diseño, colegios), el
5,7 % lleva HTML crudo (`arbeitnow` nativo no limpia; el legacy sí) y la
traducción era un botón manual con 7 títulos en caché. El mensaje era del
**Portfolio** (`ai_match.py:171`), cuyo backend tragaba la causa de un fallo
transitorio del feed. Implementado allí (`c01a192`, `32fe475`): tabla
`job_enrichments` + bucle de fondo acotado con Groq + decoración de sólo
lectura; título traducido y resumen de 2–3 frases en la tarjeta; la causa del
fallo, registrada y servida. Producción: 38/38 resúmenes y 35 títulos
resueltos en < 5 min. Suites del Portfolio: 2.026 + 390. Tropiezos: id de
Alembic reutilizado, e imagen que no arrancó por `working_dir: /release`
(~1 min sin servicio, restaurada; ahora `--build-arg APP_DIR=/release`).
**Pendiente:** el frontend del Portfolio (Cloudflare Pages) no se publica sin
`push`; jobgether sin texto (decisión B); SwissJob `/match` sin lo mismo.
Detalle: `SwissJob/docs/audits/DIAGNOSTICO_PANEL_OFERTAS_2026-09-23.md`.

**El contrato NO se cumple y el punto 5 sigue ABIERTO.** Una mejora de 37 veces
no convierte un incumplimiento en cumplimiento. Lo que queda está identificado:
~1,4 s de trabajo del BFF por petición sobre 1.800 items; la primera carga, que
sigue recorriendo el feed en la cara del usuario en vez de calentarse en
segundo plano; y la cola de latencia de las rutas de 20, **sin atribuir** (con
n=20 el p95 es el máximo, y un solo pico decide). Detalle en §9-bis y §10 del
acta. **A18-05 sigue abierto.**

## 45. Punto 5 — optimización desplegada, aceptación PENDIENTE (2026-09-22)

> ⚠ **FOTO DEL 22-09, SUPERADA POR §46.** Las cifras de esta sección
> (`p50 1,924 s`, `8,6 s`, versiones `point5-cf260b1`/`f331c0d`) describen ese
> día y **no son el estado vigente**. Se conservan porque documentan qué se
> midió y qué se corrigió; para saber dónde está el proyecto, lee **§46**.

**Punto 5 ABIERTO.** La optimización del recorrido del feed está implementada,
desplegada y verificada; la aceptación integral de rendimiento **no** lo está.

Lo conseguido, verificado: servir una página pasó de **18 peticiones internas a
1** y el método `CoreMatching.results` de 9,3-12,9 s a **0,56-1,19 s de
mediana**, con ids, orden, scores y total idénticos en 8/8 casos sobre datos
reales, sin regresión funcional. Dos cambios medidos por separado: el core
informa del `total` en su primera página (contado en una pasada con el feedback
por lotes) y la migración `core0051` añade un índice parcial de 280 kB.

**Hallazgo posterior, mayor que el anterior.** Al trazar una petición completa
—como exigió la revalidación, en vez de restar medianas— apareció el cuello real
del recorrido que usa la pantalla principal: `MatchPage` pide **3.000 ofertas con
`translate=false`**, y esa petición tardaba **79,3 s**, de los cuales **65,7 s
eran serialización**. La causa: `_to_match_response` detecta el idioma de cada
oferta servida (50,1 ms × 1.800 ofertas ≈ 90 s), también sin traducción, porque
el indicador de la UI lo necesita y **casi ninguna oferta del feed trae
`language`**.

Atribuí ese hueco a mi decisión del punto 4 (no exponer `language` en los
normalizadores nativos porque «ninguna búsqueda lo filtra») y afirmé que el
escritor legacy sí lo rellenaba. **Una segunda revalidación externa y una
medición desmontaron las dos mitades:**

- **El transporte estaba roto en tres capas**, así que rellenarlo en el origen
  no habría servido: `VacancyDTO` no declaraba el campo, `_vacancy_dtos` no lo
  leía y `_job_view` no lo asignaba —pese a que `CoreJobView.language` existía
  con un comentario que prometía lo contrario. **Reparado y fijado** por
  `backend/tests/test_language_transport.py` (12) y
  `jobhunt_core/tests/test_vacancy_language.py` (11).
- **El dato tampoco existe**: medido sobre 2.000 filas del feed, sólo el
  **3,1 %** (62) trae idioma utilizable. `legacy:arbeitnow` está al 1,5 % y las
  fuentes nativas al 0 %; lo traen al 100 % sólo aquellas cuyo metadato lo
  declara literalmente. **El corpus nunca tuvo cobertura.** Reparar el
  transporte recupera el 3,1 %, no el 100 %.

Corregido memoizando la detección (`f331c0d`): **79,3 s → 10,3 s** en ese
recorrido y **1,8 s → 0,007 s** en la serialización de 20 ofertas. Por HTTP:
`/jobs/search` p95 **1,810 s (cumple)**; `/match/results` 20 ofertas p50 0,823 s;
el caso de 3.000, de ~79 s a **8,6 s**. Suites: BFF **2.527 passed, 4 xfailed**.

La memoización es una **mitigación, no el arreglo de fondo**, y su cota importa
para la aceptación: **no cubre la primera carga**. Con 1.544 títulos únicos a
50,1 ms, la primera petición tras cada arranque o expulsión vuelve a pagar del
orden de 77 s. La matriz final debe medir **frío y caliente por separado**.
Hallazgo colateral: `langdetect` **no es determinista** en títulos cortos —30
vaciados de caché de «Sviluppatore software» dan en=21, sv=6, it=3—, así que el
indicador de idioma podía cambiar entre cargas. La caché **estabiliza** esa
respuesta; no la hace correcta ni igual entre procesos. La docstring que
prometía «función pura» está corregida.

Lo que estaba mal en la primera acta, corregido tras una
[revalidación externa](SwissJob/docs/audits/REVALIDACION_INFORME_DESPLIEGUE_2026-09-22.md):

1. Se declaró **cerrado** un contrato que no se cumple: la predeclaración exige
   p95 ≤ 2 s y varios escenarios dan 2,1-3,0 s.
2. Se midió el **método intermedio**, no el endpoint. `GET /api/v1/match/results`
   da p50 **1,924 s** sin traducción y **2,586 s** con ella, mínimo 1,177 s.
3. Se atribuyó al host **toda** la latencia residual sin demostrarlo — y la
   traza posterior lo desmintió: el cuello era nuestro. Una segunda afirmación
   («el mínimo es trabajo propio») tampoco tenía rigor: un mínimo no separa
   trabajo de espera, ni restar medianas de muestras distintas atribuye nada.
4. La tabla de versiones era **falsa**: worker y captura del core siguen en
   `point4-51be757`; sólo `core-api` está en `point5-cf260b1`. El despliegue fue
   selectivo y queda declarado como tal. **El worker en ejecución no contiene
   `CORE_CAPTURE_ENABLED`**: verificarlo antes de apoyarse en él para el slot.

**Las sondas mintieron dos veces.** La original leía `jobs` donde la respuesta
trae `data` y habría aprobado un 200 vacío. Su sustituta añadió cinco controles
negativos… que fallaban **por la causa equivocada**: tres usaban la clave
`results` y saltaban en la primera guarda, antes de llegar a la que decían
probar; desactivando las guardas de cardinalidad y total, `self_test()` seguía
verde. Ahora **cada rechazo lleva un motivo nombrado y cada control exige ESE
motivo**, cada negativo rompe una sola propiedad de un cuerpo válido, y hay
controles positivos. Verificado con prueba de mutación: **12 guardas, 12
mutantes detectados, 0 supervivientes** (la primera pasada dejó 4).

**Corrección de criterio:** el acta llegó a decir que la carga de 3.000 ofertas
«no es una lectura habitual» y carecía de presupuesto. Es al revés — es lo que
pide la pantalla principal en cada entrada, y la predeclaración ya la cubre.
Descubrir tarde su tamaño no la convierte en excepción; excluirla tras medirla
sería cambiar el criterio al ver el resultado. Un rediseño tendría que conservar
categorías y contadores, Watchlist, top score, matches ≥ 70 y las tarjetas
visibles, que salen todos de ese mismo lote: es un cambio de funcionalidad y lo
decide el propietario.

Falta para cerrar (§10 del acta): escenarios de **frío** (que es donde duele el
3,1 % de cobertura), escrituras en copia y frontend; muestra ≥100 en copia;
separar la cola de latencia con observación correlacionada; **deducir el idioma
al INGERIR** en vez de al servir; decidir si se rediseña la carga de 3.000
conservando su semántica; presupuesto propio para la traducción; y decidir
contra qué presupuesto se acepta. **A18-05 sigue abierto.**
