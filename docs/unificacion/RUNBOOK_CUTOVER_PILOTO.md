# RUNBOOK — Cutover del piloto (ReactPortfolio → jobhunt_core)

> **Ticket C-2** (Fase C). Cutover del PILOTO **SIN CDC**. Estado: **runbook
> redactado**; el ENSAYO sobre copia y el FLIP real están **BLOQUEADOS** (ver §0).
> Complementa: `PLAN_UNIFICACION_JOBHUNTING.md` §15bis (orden de cutover),
> `CONTRATOS_FASE_C.md` (C-2/C-4), `jobhunt_core/shadow/RUNBOOK.md` (GATE-SOMBRA).
>
> **Orden AUTORITATIVO = el de este runbook** (QUIESCE ANTES del flip, §15bis
> paso 6). El resumen de una línea de C-2 en el contrato es una paráfrasis laxa;
> si difiere, manda §15bis y este runbook.

---

## 0. Alcance, precondiciones y gating

**Por qué SIN CDC.** El piloto (ReactPortfolio) no tiene tráfico de escritura
continuo que capturar: solo **durables** (`job_applications`, `saved_searches`,
`cv_profiles`) escritos por el ÚNICO propietario vía HTTP, y **caches efímeras**
de empleo (memoria + snapshots Redis, recomputables). No hay slot de replicación
ni outbox legacy. Un **freeze breve** (mono-propietario) elimina el delta y hace
innecesario el sustrato CDC del §15bis (ver §1).

**Variables de entorno del runbook** (parametrizadas — NO hardcodear puertos):
- `PORTFOLIO_URL` = base del piloto. En el NAS es el puerto de host de su compose;
  en el ENSAYO/local es el puerto del compose de ensayo (distintos — ver §4). Se
  toma del `docker-compose.yml` del portfolio, no se asume.
- `CORE_URL` = `$CORE_API_BASE_URL` del BFF = **`http://core-api:8000/v1`** (red
  INTERNA de compose, plan §21). **El puerto 8003 es solo del dev de SwissJob y NO
  existe en el NAS**: el BFF llega al core por el nombre de servicio en la red
  común, no por un puerto de host.
- `CORE_KEY` = credencial del consumer `portfolio` (`Bearer key_id.secret`). **DEBE
  incluir el scope `profiles:write`** (no solo `vacancies:read`/`profiles:read`/
  `matches:read`): el seed de C-3 hace `PUT /v1/profiles/{pid}`, que exige
  `profiles:write` — una credencial de solo lectura pasa el pre-flight de lectura y
  falla el seed con **403**. Emitir/rotar la credencial con ese scope (enrollment).
- `ADMIN_TOKEN` = JWT de admin del portfolio (para el endpoint de seed C-3). Se lee
  del entorno, nunca por argv.

**Precondiciones (todas obligatorias antes de ENSAYAR o ejecutar):**
- **Downgrade por debajo de `core0021`**: purgar ANTES `profile_recovery_state`
  (`DELETE FROM jobhunt.profile_recovery_state`) y verificarlo. Los downgrades de `core0022` y
  `core0021` ya lo hacen, pero si el sistema se deja CORRIENDO en un escalón intermedio vuelve a
  poblarse, y el de `core0020` reconstruye `corpus_watermark` con `now()` — lo que APAGARÍA el
  trabajo de recuperación pendiente (perfiles con CV nuevo sirviendo matching viejo). Es estado
  derivado: vaciarlo solo cuesta una pasada idempotente del proyector.
- Core en **`core0023` (Alembic head actual)** con `/v1` operativo: `GET /vacancies` (keyset),
  `GET /vacancies/{id}`, `GET /profiles/{pid}/matches`, `PUT /profiles/{pid}`. Arrancar por debajo
  de head deja consultas de harvest fallando (p.ej. `source_harvest_runs.claim_token`, core0017;
  `source_harvest_runs.heartbeat_at`, core0018).
- **Red compose común**: el contenedor del BFF del portfolio está unido a la red
  del core (`core-net`/red de `core-api`) — verificar con
  `docker exec <bff> getent hosts core-api` (resuelve) antes de nada.
- Portfolio en `main` a HEAD, migraciones aplicadas (incl. `jobhunt_routing`).
- **Perfil core provisionado (C-3)**: el consumer `portfolio` está matriculado
  (enrollment) en el core y `CORE_PROFILE_ID` apunta a su perfil. El push de C-3
  ACTUALIZA ese perfil, NO lo crea (un 404 = perfil no provisionado ⇒ matricular
  primero).
- **🚦 GATE-SOMBRA superado** (racha 7/7 + rollback ensayado). **El FLIP (paso E)
  está BLOQUEADO hasta esto.**
- **🚦 T-PRE-FLIP superado** (añadido 2026-08-20, novena revisión externa). `DEUDA_TECNICA.md`
  declaraba TRACK T como "severidad alta antes de cualquier flip", pero **ningún gate lo exigía**:
  se podía atestar GATE-SOMBRA, NAS y §4, poner GATE C en verde y ejecutar el flip **sin restore
  probado y sin cierre real del ciclo GDPR**. El procedimiento no lo detenía. Bloquean el FLIP,
  y solo estos tres —el resto de TRACK T sigue siendo diferible—:
  1. **Restore probado** sobre copia real, con **RPO/RTO medidos y registrados** (no estimados).
  2. **Rotación/revocación** de la credencial del consumer ejercitada al menos una vez.
  3. **Ensayo de borrado GDPR / crypto-shred** verificando que el dato desaparece TAMBIÉN de los
     backups. Hoy ese borrado es papel.
  **El FLIP (paso E) está BLOQUEADO hasta esto.**
- **NAS disponible**: el ENSAYO (§4) y la migración de durables (C-4) se hacen
  sobre **copia de los datos del NAS** (fuente autoritativa), nunca contra un
  esquema local fresco (checksums de cero = confianza falsa).

**Gating (orden del propietario):** dedup semántico nivel 3, despliegue NAS y la
racha 7/7 se lanzan cuando el NAS quede disponible. Este runbook se PUEDE redactar
y revisar sin ellos; NO se ejecuta el flip sin el GATE-SOMBRA.

---

## 1. Mapeo al orden §15bis (registrado — NO es desviación silenciosa)

| §15bis | Paso | En el piloto |
|---|---|---|
| 1 | outbox de captura + WATERMARK (LSN) | **NO APLICA** — sin CDC; el freeze breve fija el corte |
| 2 | snapshot / bulk backfill consistente | **APLICA (adaptado)** → **snapshot** en paso B4 (con el freeze en efecto); **backfill/migración** en paso C (C-4) |
| 3 | replay de eventos > watermark | **NO APLICA** — no hay eventos que reproducir (freeze elimina el delta) |
| 4 | reconciliar recuentos/checksums | **APLICA** → paso D |
| 5 | canary de LECTURAS (subconjunto de perfiles) | **lo cubre C-6** (paridad N días por capacidad); el piloto es mono-perfil, el canary es `core_read` por capacidad |
| 6 | freeze breve + **QUIESCE** de schedulers legacy, ANTES del flip | **APLICA (adaptado)** → paso B (kill-switch C-2 + freeze de durables) |
| 7 | drenar outbox + reconciliar delta final | **NO APLICA** — sin outbox de captura; el freeze ya cerró el delta |
| 8 | flip de lecturas → core_primary | **APLICA (adaptado)** → paso E (flip de LECTURAS por capacidad; escrituras de durables siguen locales hasta C-4) |
| 9 | verificar schedulers apagados + escrituras OK | **APLICA** → paso F |

**Invariante conservado:** el **QUIESCE va ANTES del flip** (gate anti-doble-cosecha).

---

## 2. Runbook ejecutable

> El routing se cambia con `set_routing` (services/routing.py). No hay CLI dedicada;
> es una acción de runbook — invocación concreta (una capacidad/modo por llamada):
> ```
> docker compose exec <bff> python -c "
> import asyncio
> from database import AsyncSessionLocal
> from services.routing import set_routing
> async def _go():
>     async with AsyncSessionLocal() as db:
>         await set_routing(db, 'catalog', 'core_read', updated_by='cutover')
> asyncio.run(_go())
> "
> ```
> **Propagación**: `set_routing` invalida la caché en proceso TRAS el commit; entre
> procesos/workers la cota es `ROUTING_CACHE_TTL_SECONDS`. Con varias réplicas del
> BFF, aplicar en cada una o esperar el TTL. Verificar el modo efectivo por el
> comportamiento observado (paso F), no solo por la fila.

### Paso A — Pre-flight (sin efectos)
1. Core vivo desde el BFF. **`curl` NO está en la imagen del contenedor** y no se
   debe pasar la credencial por argv (queda visible en la lista de procesos): se
   usa Python/httpx (ya instalados) leyendo `CORE_CONSUMER_KEY` del ENTORNO del
   contenedor:
   ```
   docker compose exec <bff> python -c "
   import os, httpx
   r = httpx.get(os.environ['CORE_API_BASE_URL'].rstrip('/') + '/vacancies?limit=1',
                 headers={'Authorization': 'Bearer ' + os.environ['CORE_CONSUMER_KEY']},
                 timeout=5)
   print(r.status_code)
   "
   ```
   → `200`. (Las sondas de `$PORTFOLIO_URL/health/deep` de abajo corren desde el
   shell del OPERADOR, no dentro del contenedor, donde `curl` sí suele estar; aun
   así pueden hacerse con `python -c ... httpx` si el host no lo tiene.)
2. Portfolio vivo (desde el host): `curl -fsS "$PORTFOLIO_URL/health/deep" | jq '.checks.database.status'` → `"healthy"`.
3. Credencial del consumer válida (el 200 del punto 1 ya lo prueba; sin credencial ⇒
   CoreUnavailable). **Y con el scope `profiles:write`** (el pre-flight de lectura NO
   lo cubre): un PUT NO-MUTANTE contra un UUID inexistente distingue autorización de
   scope — **404** confirma `profiles:write`; **403** = falta el scope (rotar la
   credencial, §0):
   ```
   docker compose exec <bff> python -c "
   import os, httpx
   r = httpx.put(os.environ['CORE_API_BASE_URL'].rstrip('/') + '/profiles/00000000-0000-0000-0000-000000000000',
                 headers={'Authorization': 'Bearer ' + os.environ['CORE_CONSUMER_KEY']},
                 json={'title': 'preflight'}, timeout=5)
   print(r.status_code)  # 404 = scope OK; 403 = falta profiles:write
   "
   ```
4. **GATE-SOMBRA verde** (7/7 + rollback ensayado) — evidencia del harness de sombra.
5. NAS disponible (la copia autoritativa se toma en el paso B, DESPUÉS del freeze — no aquí).

### Paso B — QUIESCE + freeze + snapshot — ANTES del flip
1. **Kill-switch OFF**: `BACKGROUND_SCHEDULERS_ENABLED=false` en el `.env` del
   piloto + **redeploy** (el flag es de ARRANQUE).
2. **Verificar el quiesce por sonda**:
   `curl -fsS "$PORTFOLIO_URL/health/deep" | jq '.checks.schedulers'`
   → `{"background_schedulers_enabled": false, "state": "quiesced"}`.
   Garantiza (C-2, análisis 2) que **NO se cosecha ni programado NI on-demand**:
   `run_cache_update` y `_scrape_and_alert` cortan en seco aunque llegue tráfico a
   `/recent`, `/stats` o un `/refresh-cache`/`/schools/refresh` manual.
3. **Freeze de durables — bloqueo REAL con el flag `WRITES_FROZEN`** (implementado,
   no "el propietario se abstiene"): fijar `WRITES_FROZEN=true` en el `.env` +
   **redeploy**. Con el flag ON, toda MUTACIÓN (POST/PUT/PATCH/DELETE) de los 3
   routers de estado durable (candidaturas, búsquedas-guardadas, CV) da **503** —
   antes de auth y de cualquier I/O (dependencia de router `block_writes_when_frozen`,
   services/write_freeze.py); las LECTURAS siguen vivas. El **redeploy DRENA las
   peticiones en vuelo** (el shutdown grácil del proceso viejo termina las escrituras
   ya aceptadas antes de parar; el proceso nuevo arranca con el freeze activo).
   **Verificar el freeze por sonda**:
   `curl -fsS "$PORTFOLIO_URL/health/deep" | jq '.checks.schedulers.writes'` → `"frozen"`;
   y una mutación de prueba (`POST /api/v1/applications/`) → 503. Mantener
   `WRITES_FROZEN=true` hasta cerrar el corte (paso B4).
   **Verificación de estabilidad por HUELLA COMPLETA** (no `max(updated_at)`:
   `saved_searches` NO tiene `updated_at` y una edición de `name/filters/min_score/
   is_active` no toca `created_at` — quedaría invisible): tomar un hash del contenido
   material por tabla (p.ej. `md5(string_agg(fila ordenada))` sobre las columnas
   migrables de `job_applications`, `saved_searches`, `cv_profiles`) y comprobar que
   es IDÉNTICO en dos lecturas separadas dentro de la ventana. Cualquier diferencia
   ⇒ el bloqueo no está completo: abortar y revisar.
4. **Snapshot autoritativo** (§15bis 2): tomar la copia de los durables del NAS
   **AHORA, con el freeze en efecto** — no antes (una copia pre-freeze perdería en
   silencio las escrituras de la ventana copia↔freeze, y el paso D no lo detectaría).
5. **Seed del perfil core (C-3)**: con el CV local congelado/estable, empujar el CV
   `extenso`/idioma-primario al perfil core. SIN exponer el token admin en argv
   (visible en `ps`/historial — mismo anti-patrón que evitamos para
   `CORE_CONSUMER_KEY`): con `ADMIN_TOKEN` en el ENTORNO,
   ```
   python -c "
   import os, httpx
   r = httpx.post(os.environ['PORTFOLIO_URL'] + '/api/v1/admin/cutover/push-cv',
                  headers={'Authorization': 'Bearer ' + os.environ['ADMIN_TOKEN']}, timeout=30)
   print(r.status_code, r.json())
   "
   ```
   → `200 {"status":"pushed", ...}`. El endpoint está EXENTO del write-freeze (lee lo
   local congelado y escribe en el CORE — justo lo que el freeze permite) e traduce
   sus errores a HTTP. Diagnóstico de las respuestas del ENDPOINT:
   - `200 skipped`/`no_core_profile_id` ⇒ falta fijar `CORE_PROFILE_ID` (precondición §0).
   - `404` ⇒ perfil core no provisionado — matricular el consumer primero (§0).
   - `503` ⇒ core caído, 412 persistente, **o la credencial del consumer sin
     `profiles:write`** (un 403 del CORE lo mapea el cliente a CoreUnavailableError →
     503; usar el chequeo de scope de A.1 para distinguirlo).
   - `401`/`403` del ENDPOINT ⇒ `ADMIN_TOKEN` ausente o no-admin (NO es el scope del
     consumer). Idempotente.

### Paso C — Migración de durables (C-4) — sobre el snapshot congelado
Ejecutar la migración campo-a-campo **de C-4** (el manifiesto autoritativo vive en
`CONTRATOS_FASE_C.md`; aquí van las reglas clave).

**IDEMPOTENCIA (re-ejecutable con seguridad):** cada vacante-sombra se sintetiza
`source→source_listing→incarnation→vacancy→offer_revision` en **UNA sola
transacción atómica** (así el check por URL nunca encuentra una vacante sin
`offer_revision` — una cadena a medias sería incompletitud silenciosa que el paso D
no detecta). Los inserts en `applications`/`profile_vacancy_state`/`saved_searches`
usan existence-check o `ON CONFLICT DO NOTHING`. **Si el Paso C aborta a mitad**
(crash/red/OOM): aplicar el borrado acotado (§3) ANTES de reintentar. **NO
redeployar el BFF durante el Paso C.**

**El paso C EMITE dos artefactos** — y su nivel de fidelidad se REPARTE entre C-4 y el
ensayo §4 (**DIVISIÓN DE ALCANCE**, decidida 2026-08-02 con el propietario; C-4 tras 4
revisiones externas convergió en que la procedencia exacta y el oráculo independiente
exigen un LEDGER del sink, infraestructura de ejecución que se construye y PRUEBA con
datos reales del NAS en §4):
1. **Manifiesto de rollback** (§3). En **C-4** (`import_portfolio_manifest._captured_
   identities`): un **INVENTARIO SCOPEADO** (filas alcanzables por la fuente
   `portfolio-import` y el consumer `portfolio`, incl. `dedup_candidates`/`link_evidence`)
   + `new`/`reused` vacancies — de partida y cross-check, NO procedencia exacta (un re-run
   o un `offer_revision` reutilizado reaparecen). En **§4**: la **procedencia EXACTA**
   (`RETURNING` de cada INSERT + ledger del sink `created/reused/quarantine/reason/link`)
   y el **script de borrado FK-safe** (orden child→parent, punteros circulares, merge,
   abort-on-RESTRICT, cierre de la carrera residual de attach).
2. **Manifiesto de resultados esperados** (paso D). En **C-4** (`reconcile`): verificación
   de **VALORES materiales** del destino contra el origen (notes/follow_up/snapshot,
   cardinalidad de eventos, contenido canónico de oferta, tupla de saved_searches;
   staging contable por identidad) → atrapa el bug determinista. En **§4**: la
   **verificación estructural INDEPENDIENTE COMPLETA** que, con el ledger del sink,
   distingue una cuarentena legítima de un fallo del sink que pierda listings válidos.

El **artefacto de corpus en colisión SÍ se corrige en C-4** (savepoint que revierte la
cadena de una colisión cross-source — integridad de datos real).

**Sintetizar PRIMERO las vacantes-sombra** (ambas ramas las necesitan: el bookmark
y la candidatura precisan `vacancy_id`):
- **Vacante-sombra (caso normal)**: por cada URL de bookmark/candidatura sin vacante
  core, sintetizar la cadena de identidad del core: `source`(name=`portfolio-import`)
  → `source_listing`(external_id determinista, `url_normalized`) → incarnation
  (enlaza listing↔vacancy) → `vacancy` → `offer_revision` vigente (title/company/url/
  description desde el snapshot). Identidad por URL si ya existe en el corpus; si no,
  fuente `portfolio-import`.
- `job_applications.status=saved` → `profile_vacancy_state.saved_at` (bookmark) +
  `notes`; bookmark con `follow_up_date` ⇒ **además** `application` (regla C-4).
- `status≠saved` → `applications` core (enum mapeado: phone_screen/technical/offer/
  rejected → estado equivalente + **evento inicial sintetizado** en
  `application_status_events`; notes/follow_up_date → columnas).
- **Dedup de UNIQUE(profile_id, vacancy_id)**: si un `saved+follow_up_date` y una
  candidatura (`status≠saved`) resuelven a la MISMA `vacancy_id` (misma URL),
  consolidar en UNA sola `application` (estado de la candidatura real, conservando
  follow_up_date/notes) — no dos filas que violarían el UNIQUE.
- `saved_searches` → migrar (tras C-ESQ). `cv_profiles` → **conservar local + push
  (C-3)** — el CV se sirve LOCAL, no del core. `seen_jobs` → recomputar.
  `generated_documents`/colegios → **LOCAL hasta Fase E**. `analytics`/`users` →
  **conservar local** (son del BFF, no se migran). SSE → nada.

### Paso D — Verificación (contra un MANIFIESTO de resultados esperados)
`conteo origen == conteo destino` es **INCORRECTO** aquí: la consolidación (paso C:
un `saved+follow_up_date` + una candidatura con la misma URL → UNA sola
`application`) hace que, por diseño, `2` filas legacy produzcan `1` fila core. Una
migración CORRECTA fallaría un conteo bruto.

En su lugar, **ANTES de migrar** se genera un **manifiesto de resultados esperados
por tabla y clave transformada** (derivado del snapshot congelado + las reglas de
C-4): nº de `applications` distintas TRAS consolidación, nº de bookmarks
(`profile_vacancy_state`), nº de eventos iniciales sintetizados
(`application_status_events`), nº de `saved_searches`, y **vacantes NUEVAS vs
REUTILIZADAS** (sombra sintetizada vs identidad global ya existente por URL).
Paso D compara el destino (core) **contra ese manifiesto**, no contra conteos brutos
del legacy, más un checksum de columnas materiales por fila esperada. Divergencia
manifiesto↔destino ⇒ **abortar** (rollback, §3).

> **DIVISIÓN C-4 / §4** (ver Paso C): el scaffold de C-4 (`reconcile`) ya compara los
> VALORES materiales del destino contra el origen (atrapa el bug determinista) y
> conteos/checksums por tabla. La generación del **manifiesto de resultados esperados
> ANTES de migrar** derivado independientemente + la verificación ESTRUCTURAL
> independiente (con el ledger del sink) son entregables del **ensayo §4** — sin ellos
> el Paso D corre con la fidelidad del scaffold (valores + contable), no con la
> independencia completa.

### Paso E — Flip de LECTURAS por capacidad  🚦 (bloqueado por GATE-SOMBRA)
`set_routing` por capacidad, en escalera con verificación entre modos.
`services/routing.py` define CUATRO capacidades, pero **solo DOS tienen costura de
LECTURA con efecto hacia el core** (`catalog`, `matching`); `applications` y
`saved_searches` están registradas pero su modo `core_*` es INERTE hasta C-4 (E.3).
**Nota — flip PARCIAL**: llevar `catalog` a core y dejar `matching` en local (o
viceversa) sirve corpus DIVERGENTES entre ambas. Estado transitorio y acotado
(piloto mono-usuario, ventana breve); orden recomendado: mover ambas al mismo modo
(las dos a `core_read`, luego las dos a `core_primary`) antes de exponer al usuario.
1. **catálogo** (`catalog`): `local` → `core_read` (canary con fallback) → paridad
   (C-6) → `core_primary`.
2. **matching** (`matching`): idem. El **perfil del core se consume DENTRO de esta
   capacidad** — el feed `GET /v1/profiles/{pid}/matches` (vía `CORE_PROFILE_ID`) ya
   puntúa con el perfil que recibió por el push de C-3. **No hay capacidad
   «perfiles» de routing** (el CV/portfolio-data se sirve local, sin costura).
3. **candidaturas** y **búsquedas-guardadas**: SIGUEN locales (el `/v1` no expone su
   escritura; flip de escritura es trabajo de C-4). Su modo `core_*` es INERTE hoy.
4. **documentos/colegios/CV**: declarados LOCAL hasta Fase E (sin routing).

> **GATE C — checklist ejecutable ANTES de core_primary (C-6)**: `POST /api/v1/admin/
> gate-c/readiness` (admin) evalúa el checklist y devuelve verde/rojo por criterio. Lee
> FRESCO (sin caché) el routing por capacidad de **TODOS los perfiles** (comodín + cada
> override — un override por perfil NO queda invisible tras el comodín): catálogo/matching
> en core en todos ellos; durables en escritor LOCAL (allowlist local/shadow/core_read —
> nunca `core_primary` ni `rollback_pending`) → un escritor. Por eso el flip se hace sobre
> el **comodín** (no por overrides sueltos). Compone además con el kill-switch C-2 y con los hechos del NÚCLEO
> que el OPERADOR atesta en el body (`CoreFacts`): veredicto del GATE-SOMBRA (`gate_passed`
> del core, NO la racha cruda — evita duplicar el N), outbox sin divergencia, veredicto de
> la reconciliación C-4, y **§4 listo** (bloqueante). **RIESGO RESIDUAL**: hasta que el
> core exponga esos hechos por endpoint, el gate CONFÍA en lo que teclea el operador —
> el operador DEBE leerlos del core real (`shadow_cycle_metrics`/`integration_outbox`/
> `portfolio_migration_manifest`), no estimarlos. El gate es NECESARIO, no suficiente:
> `green` no reemplaza confirmar el estado real del core ni el ensayo §4.
>
> **Manifiesto vigente (§4-LOCAL, core0014):** `portfolio_migration_manifest` es DURABLE y
> sobrevive a un rollback. El operador SOLO atesta `migration_verdict` de una fila con
> `status='applied'`. Tras deshacer una migración su fila queda `status='rolled_back'` (o
> `rollback_aborted`) y su `verdict='ok'` es OBSOLETO — leerlo alimentaría un falso GATE C.
> El verificador estructural independiente (`import_portfolio_verify`) persiste su veredicto
> en `manifest.verification`; un `verification.verdict='discrepant'` (listing perdido, vacante
> impresentable, desacuerdo de oráculos) es un **§4 NO listo** aunque la reconciliación diga `ok`.
>
> **Matching FRESCO por perfil (P1 rev. externa integral):** `PUT /profiles/{id}` activa una
> revisión pero NO dispara embedding+matching directamente — quien lo garantiza es el **PROYECTOR**
> (beat `shadow-project`, cada ~5 min): su recuperación de salida (`_recovery_targets`) cubre
> AHORA a TODOS los perfiles activos —no solo los sombra— y embebe+evalúa la revisión vigente sin
> evaluación. **PRECONDICIÓN del flip**: antes de `core_primary`, verificar que CADA perfil activo
> tiene embedding Y `match_evaluation` de su revisión VIGENTE (si no, `matching.feed` serviría la
> evaluación ANTERIOR); dar tiempo a ≥1 pasada del proyector tras el último `PUT /profiles`.

### Paso F — Verificación post-flip (§15bis 9)
1. `curl "$PORTFOLIO_URL/health/deep" | jq '.checks.schedulers.state'` → `"quiesced"`
   (schedulers del piloto SIGUEN apagados: sin doble cosecha ni emails).
2. 100% de lecturas de **catálogo y matching** contra el core. Señal según el modo:
   - `core_read` (canary): 0 líneas «cayó a local» en los logs del BFF (esas SOLO
     las emiten FallbackCatalog/FallbackMatching en core_read).
   - `core_primary`: **0 respuestas servidas de local** y **ausencia de
     503/RoutingUnavailable/CoreUnavailable** (en core_primary NO hay fallback por
     construcción; el síntoma de un core que sirve mal serían errores 503/501, no
     fallbacks — no medir «0 fallbacks», que ahí es trivialmente cierto).
3. Sin pérdida: conteos/checksums estables (paso D). *(No hay «outbox» que verificar
   en el piloto — §0: sin CDC/outbox de captura.)*
4. Escrituras de durables OK (siguen locales, sin errores).

---

## 3. Rollback (reversible en cada paso)

- **Antes del flip (A–D):** `BACKGROUND_SCHEDULERS_ENABLED=true` + redeploy re-arma
  los schedulers; el propietario reanuda escrituras. El core no fue autoritativo.
- **Después del flip (E), a local SERVIBLE (el orden importa):** el motor local
  sigue completo PERO sus caches de lectura pueden estar RANCIAS — el quiesce (paso
  B) apagó `_load_apis_background` (único poblador de las caches al arranque) y los
  snapshots Redis tienen TTL. Por eso: (1) `BACKGROUND_SCHEDULERS_ENABLED=true` +
  redeploy → re-arma schedulers y re-cosecha; (2) esperar a que `/recent` devuelva
  datos frescos; (3) SOLO ENTONCES `set_routing` de vuelta a `local` por capacidad
  (`catalog`, `matching`). Rollback INMEDIATO (sin esperar) ⇒ asumir caches frías
  hasta la primera cosecha. («motor local completo» está condicionado a caches calientes.)
- **Migración corrupta (paso D falla) o Paso C abortado a mitad:**
  - En **ENSAYO** (§4): el destino es una **copia DESECHABLE del core** → se descarta
    ENTERA y se repite (sin borrado quirúrgico).
  - En **REAL**: el destino es el **corpus core VIVO y COMPARTIDO** (multi-tenant).
    NO se descarta el core; se hace un **borrado ACOTADO por PROCEDENCIA EXACTA**.
    La heurística `created_at ≥ freeze` + `source portfolio-import` + `profile_id`
    NO PRUEBA que una fila la creara ESTE run (el core compartido sigue creando
    vacantes tras el freeze; el run pudo REUTILIZAR listings/durables pre-existentes;
    borrar por todo el `profile_id` eliminaría estado ANTERIOR al cutover). Por eso:
    - **La migración (paso C) EMITE un MANIFIESTO DE ROLLBACK** con los **PKs EXACTOS
      que INSERTÓ** (cada `vacancy`, `source_listing`, incarnation, `offer_revision`,
      `application`, `application_status_events`, `profile_vacancy_state`,
      `saved_searches`), y **por separado** las filas PRE-EXISTENTES que solo
      REUTILIZÓ (de esas se borra únicamente el ENLACE que el run creó — p.ej. la
      incarnation `portfolio-import` sobre una vacante global— jamás la fila
      compartida).
    - El borrado opera **solo sobre los IDs de ese manifiesto**, no sobre heurísticas.
    - Invariantes de SEGURIDAD ADICIONALES (además del manifiesto): **NUNCA** borrar
      una vacante que sea winner o loser de un merge (dedup nivel 3 / `merged_into` /
      `merge_log`; la FK `merged_into` es `ON DELETE SET NULL` ⇒ resucitaría o dejaría
      colgando datos COMPARTIDOS) — revertir el merge primero (`merge_log`+
      `merge_transfers`) o RECHAZAR. Orden **FK-safe** child→parent, nulificando ANTES
      los punteros CIRCULARES (`current_offer_revision_id`, `primary_incarnation_id`)
      y tratando `merge_transfers`/`dedup_candidates`/`link_evidence`. Un fallo
      RESTRICT = señal de tocar algo compartido → abortar, no forzar.
    - Las durables del portfolio se borran por los **PKs del manifiesto**, no por el
      `profile_id` entero (que podría arrastrar estado pre-cutover).
    - El script exacto se **produce y PRUEBA en el ensayo** (§4) antes de producción.
- **`core_read` NUNCA cae a local con un cursor** (el cursor keyset es opaco): una
  página con cursor que falla se propaga (503/501), no reinicia en local. El
  rollback es del OPERADOR (set_routing), no un fallback silencioso.

---

## 4. Ensayo (patrón A-12/B-05) — ✅ §4-REAL EJECUTADO (2026-08-22)

> **EJECUTADO sobre copia real del NAS** (dump del esquema `jobhunt` sin embeddings/staging,
> restaurado en BD desechable `ensayo_c2`; durables REALES del portfolio extraídos de su BD).
> Resultados y hallazgos:
>
> 1. **⚠ LOS DURABLES REALES ESTÁN VACÍOS**: 0 candidaturas, 0 búsquedas, 0 CVs, 1 usuario. El
>    cutover del piloto migrará ~nada — el riesgo real del flip vive en el CAMINO DE LECTURA
>    (canario) y los schedulers, no en la migración de datos.
> 2. **Pasada con durables reales (vacíos)**: verdict `verified`, reconcile `ok`, procedencia
>    mínima (solo source+scope `portfolio-import`), rollback `rolled_back`, copia intacta.
> 3. **Pasada con durables SINTÉTICOS sobre el corpus REAL** (attach a URL real pi-asp — ejercitó
>    el fix de identidad-en-fragmento del mismo día —, oferta nueva, bookmark, saved_search):
>    verdict `verified`; rollback borró EXACTAMENTE el grafo (2 vacancies, 3 listings/incarnations/
>    revisions, 2 applications+2 eventos, 1 saved_search, 1 pvs, link_evidence, source+scope) y el
>    corpus quedó PRÍSTINO (conteos idénticos al dump).
> 4. **Guards demostrados con datos reales**: LIFO (aborta si hay manifiesto posterior con datos) y
>    CASCADE (aborta si borraría eventos ajenos a la procedencia) — ambos dispararon en el edge de
>    manifiesto duplicado.
> 5. **Hallazgos operativos**: (a) `migrate_and_reconcile` YA persiste el manifiesto — llamar
>    `persist_manifest` además DUPLICA y el duplicado deja la pila LIFO en `rollback_aborted`
>    (resolución manual de status); (b) el rollback NO borra el `profile` provisionado (residual
>    consciente: fuera del inventario); (c) al excluir datos del outbox en el dump, sus
>    `deliveries` fallan un FK en el restore (benigno).
>
> **Queda de §4-REAL** (solo para el cutover REAL sobre core VIVO): las variantes inmunes a
> concurrencia (procedencia por RETURNING) — sobre la copia single-writer el snapshot-diff es
> correcto, como preveía la división de alcance.

### (texto original del gating, pre-ejecución)
#### 4.antiguo Ensayo — BLOQUEADO por NAS

El runbook se ENSAYA extremo a extremo sobre **copia de los datos del NAS** y contra
una **copia DESECHABLE del core** antes de la ejecución real: pasos B→F sobre la
copia, con los conteos/checksums de D y un rollback completo (§3) verificado. DoD:
migración reversible, conteos/checksums por tabla en verde, borrado acotado del
rollback real probado sobre la copia, kill-switch probado (flag OFF ⇒ 0 arpones +
0 cosecha on-demand — ya cubierto por `tests/test_lifespan_killswitch.py` y
`tests/test_cutover_quiesce.py`).

**Entregables de §4 — SPLIT §4-LOCAL (código, APROBADO) / §4-REAL (ejecución, gated NAS).**
El CÓDIGO de los 4 se adelantó en LOCAL (delegación 2026-08-03), probado con fixtures
sintéticos (suite core **419/419**) y **APROBADO por revisión externa** (9 rondas iterativas,
veredicto APPROVE en `92169ea` el 2026-08-04); vive ÍNTEGRO en `import_portfolio*` SIN tocar el
`RawListingSink` compartido (23 fuentes vivas). Lo que resta es la EJECUCIÓN §4-REAL sobre datos
reales del NAS y las variantes inmunes a concurrencia:

| Entregable | §4-LOCAL (APROBADO) | §4-REAL (gated NAS) |
|---|---|---|
| **Ledger del sink** por entrada (created/reused/quarantine+razón+vacancy_id) | ✅ `import_portfolio_ledger.py` — derivado de la síntesis + snapshot pre-síntesis (created vs reused EXACTO); NO modifica el sink (portfolio-import es single-writer) | Validación sobre datos reales |
| **Procedencia EXACTA** (insertada vs preexistente-reutilizada) | ✅ `import_portfolio_provenance.py` — snapshot antes/después de PK-sets (después−antes) | Variante INMUNE a concurrencia (RETURNING por INSERT) para el core VIVO que sigue cosechando; el snapshot-diff es correcto sobre la **copia DESECHABLE single-writer** que usa §4-REAL, y toda sobre-captura la detecta el verificador |
| **Verificación ESTRUCTURAL INDEPENDIENTE** (distingue listing PERDIDO de cuarentena legítima) | ✅ `import_portfolio_verify.py` — usa el ledger; completitud BIDIRECCIONAL url↔origen; cross-check created-del-ledger == procedencia-de-vacancies | Oráculo PLENAMENTE independiente (enumera el corpus real, inmune a un ledger equivocado / a un sink que cree incarnaciones fuera de la síntesis) → exige el LEDGER A NIVEL DEL SINK; ejecución sobre datos reales |
| **Script de borrado FK-safe** + lifecycle del manifiesto | ✅ `import_portfolio_rollback.py` — child→parent, ciclo `current_offer_revision_id`/`primary_incarnation_id` (SET NULL), guard de abort para vacantes reutilizadas, borrado en SAVEPOINT con verificación de completitud; `status` applied/rolled_back/rollback_aborted/unknown + LIFO por `seq` (core0014/0015/0016) | Probado sobre la copia real; **cierre de la carrera residual de attach** (concern del bloqueo del sink) |

- **Condición BLOQUEANTE (sin cambios)**: sin la EJECUCIÓN §4-REAL sobre datos reales del NAS
  (+ las variantes inmunes a concurrencia) **no hay cutover ni flip**. El código §4-LOCAL NO
  sustituye esa ejecución; el scaffold de C-4 (valores materiales + inventario scopeado) tampoco.
  El GATE C lee `verification.verdict` del manifiesto (§4-LOCAL, ver Paso E): `discrepant` = §4
  NO listo aunque `reconcile` diga `ok`.

**Puertos**: el ENSAYO/local usa el compose de ensayo (puertos propios, distintos de
los del NAS); el FLIP real corre contra el NAS. Tomar `PORTFOLIO_URL` del compose
correspondiente en cada caso — NO reutilizar el puerto del NAS en el ensayo.
**Precondición: NAS disponible** (mismo gating que las demás validaciones).
