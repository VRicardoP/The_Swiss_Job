# Contratos ejecutables — Fase B (Sombra) · v1.4

> Deriva de `PLAN_UNIFICACION_JOBHUNTING.md` §12/§15bis y `BACKLOG` B.1–B.3, sobre la Fase A
> COMPLETA (CONTRATOS_FASE_A v4; core en `core0007` al redactarse esta fase, hoy `core0012` por Fase C). Estado: **VIGENTE v1.4 — FASE B COMPLETA EN CÓDIGO 2026-07-25** (B-CC, B-01..B-05 ✅ con doble análisis Opus; GATE-SOMBRA corriendo en LOCAL). Historial del ciclo de contrato: **ciclo de
> DOBLE análisis Opus COMPLETO** (1º: 18 confirmados → 12 temas corregidos; 2º: 5 confirmados
> → 4 corregidos — `shadow_cycle_metrics` en core0008a en las TRES secciones, `coste`
> [alerta], overlap/ndcg legacy sobre el feed VISIBLE espejo de get_results, alerta de
> handler sin registrar separada de la degradación normal de identidad).
> **B-01 DESBLOQUEADO 2026-07-24**: el propietario CONFIRMÓ el cambio de compose
> (wal_level=logical + imagen con wal2json) y la ventana de reinicio del Postgres compartido.
> **Umbrales de §6 RATIFICADOS 2026-07-24 por delegación del propietario** ("toma la decisión
> más óptima y eficiente tú mismo"): se adoptan tal cual quedaron tras el doble análisis —
> revisables al cierre de cada ciclo con registro en este documento.
> **SOLO LOCAL (orden del propietario 2026-07-24)**: nada de pruebas en producción — los
> ciclos de la sombra corren contra el entorno LOCAL de desarrollo; prod/QNAP intactos y
> FUERA de la delegación (exigen autorización explícita nueva).
> Fecha: 2026-07-24.
> Inventario legacy verificado en código a 2026-07-24 (tablas/columnas/escritores reales).

---

## 0. Alcance y NO-alcance

**Sombra**: el legacy SIGUE de escritor autoritativo y de cara al usuario; el core recibe los
deltas del legacy, los procesa con SU pipeline (identidad → canónica → embeddings → matching →
outbox) y se mide contra un **set etiquetado** — el sistema viejo NO es el oráculo (plan §12).
- **NO-alcance**: BFF/costuras (`jobhunt_routing`, A.SEAM — track aparte); notificaciones a
  usuarios desde el core (PROHIBIDO en sombra: cero efectos visibles); cutover (Fase C);
  migración de datos durables (C.4). El core NO escribe JAMÁS en el esquema `public` legacy.
- La sombra ENSAYA la maquinaria del cutover (§15bis: slot → snapshot exportado → replay) con
  la frontera snapshot↔LSN documentada — riesgo de Fase C retirado aquí.

## 1. Topología y aislamiento (heredados, ADR-08 + §15bis)

1 BD física `swissjobhunter`: esquema `public` = legacy (rol `swissjob`), esquema `jobhunt` =
core (rol `jobhunt_core`). Broker core = `redis-core` (noeviction), colas `core.*`. Nuevos
permisos [B, GRANTs ENUMERADOS en B-01]: rol `jobhunt_core` obtiene **SELECT read-only** sobre
EXACTAMENTE `public.jobs`, `public.user_profiles`, `public.users`, `public.match_results`
(reconciliación de `perdida`, overlap@10 y seed de labels) — nunca escritura; el invariante de
aislamiento de A-01 (`_verify_isolation`) se ACTUALIZA para permitir ese conjunto acotado en
sombra (deja de ser "cero privilegios"). La captura usa un rol de REPLICATION dedicado
(`jobhunt_capture`).

## 2. Captura de deltas legacy→core (CDC) — B-01/B-02

**Mecanismo (decisión ratificada en GATE-CC, §15bis): CDC por replication slot lógico.**
Concreción [EJECUTADA — B-01 ✅ 2026-07-25]:
- **Infra**: `wal_level=logical`, `max_replication_slots>=4`, `max_wal_senders>=4` vía
  `command:` del servicio postgres; imagen custom `FROM pgvector/pgvector:pg16` +
  `postgresql-16-wal2json` (el plugin NO viene en la imagen; `test_decoding` se descarta:
  parsing de texto frágil con Text/JSONB). Formato **wal2json v2** con `add-tables=
  public.jobs,public.user_profiles,public.users`.
  ⚠️ **Cambio de docker-compose/imagen: REQUIERE CONFIRMACIÓN EXPLÍCITA del propietario antes
  de aplicarse** (regla del proyecto); [HISTÓRICO: el OK llegó el 2026-07-24 y B-01 se ejecutó — este párrafo se conserva como registro de la condición]. La
  confirmación cubre EXPLÍCITAMENTE que `wal_level` NO es recargable: **exige REINICIO del
  Postgres COMPARTIDO con el legacy** (ventana de mantenimiento breve con parada de
  producción) + build de la imagen custom — arrancar la sombra NO es "legacy intacto" en ese
  instante puntual.
- **Slot**: `jobhunt_shadow` (un único slot; pausar el consumidor retiene WAL → alerta de
  retención en §6). Arranque: `CREATE_REPLICATION_SLOT ... LOGICAL` + snapshot exportado en la
  MISMA conexión → **backfill consistente** de las 3 tablas leyendo con ese snapshot → replay
  desde el LSN del slot. Frontera snapshot↔LSN registrada en `shadow_capture_state`.
- **Consumidor**: servicio `core-capture` (proceso dedicado, psycopg2 `START_REPLICATION`).
  Por cada mensaje wal2json aplica **whitelist de columnas POR TABLA** (jobs: las
  contractuales de §3 + is_active/duplicate_of/content_hash; user_profiles: user_id, title,
  cv_text, skills, updated_at; users: **SOLO id, is_active** — `hashed_password`, `email` y
  `gdpr_*` JAMÁS llegan al staging) y descarta las columnas pesadas no contractuales
  (`embedding`, `search_vector`, `cv_embedding` — el core computa los SUYOS). **Matiz TOAST
  (REPLICA IDENTITY default)**: wal2json OMITE del mensaje U las columnas TOASTeadas que el
  UPDATE no tocó (ausente ≠ NULL) — el consumidor stagea lo presente y registra SIEMPRE en el
  payload la clave meta `_omitted` (columnas whitelisted ausentes del mensaje; B-02 preserva
  lo omitido no completado). Para `user_profiles` el consumidor COMPLETA el payload en el
  staging re-leyendo las ausentes por PK con el SELECT RO de §1 (marcadas además en
  `_backfilled`; el valor re-leído puede ser MÁS NUEVO que el del mensaje — convergente:
  cualquier cambio posterior también llega por WAL). Para `jobs` NO se re-lee: el pre-filtro
  por content_hash del sink absorbe los updates sin cambio de contenido y un cambio real de
  contenido llega con `description` completa en el upsert de cosecha. **SIN filtro de
  "no-cambios" en el consumidor**: con REPLICA IDENTITY default el WAL no trae los valores
  VIEJOS (no hay con qué comparar) y un filtro por contenido perdería transiciones
  contractuales sin cambio de content_hash (`is_active`, `duplicate_of`) — se stagea TODO y
  los no-cambios los absorbe el pre-filtro de revisiones del sink (DO NOTHING, ya existente).
  Escribe en `jobhunt.shadow_change_log` **en la misma tx** que avanza `confirmed_flush_lsn`
  (ack solo tras commit): pérdida imposible por diseño; re-entrega posible → apply idempotente
  por `(lsn, seq_in_tx)`.
- **Tablas capturadas y por qué** (inventario 2026-07-24):
  - `public.jobs` (PK `hash` MD5; SIN `updated_at`; **DELETE físico diario** en
    `cleanup_stale_jobs` 03:30 y `UPDATE` masivo en la cosecha diaria 12:00±4h) — el WAL es la
    ÚNICA señal completa (un diff por timestamp pierde DELETEs y updates de `match_results`).
  - `public.user_profiles` (PK `id`, `updated_at` fiable) — perfiles/CV.
  - `public.users` (solo `id`, `is_active` tras whitelist) — contexto de FK y BORRADO: el
    endpoint GDPR legacy (`DELETE /profile/delete-all`) hace hard-delete de `users` que
    CASCADEA a `user_profiles`.
  - `match_results`, `job_applications`, etc. **NO se capturan**: las métricas los leen
    read-only en el momento del ciclo (§5); menos WAL y menos superficie.
- **REPLICA IDENTITY**: default (PK) basta, con MATIZ documentado: para `jobs` la PK (`hash`)
  ES la clave de mapeo (§3). Para `user_profiles` NO lo es (su PK es `id`, la proyección va
  por `user_id`): su DELETE en cascada se maneja vía el **op=D de `users`**, cuya PK `id` SÍ
  es el `external_ref` del perfil sombra — el op=D de `user_profiles` se registra en staging y
  se ignora en proyección. Sin `REPLICA IDENTITY FULL` (evita duplicar filas grandes en WAL).

### Estado y staging [B] (esquema `jobhunt`, migración **`core0008b`** — bloqueada con B-01)
- `shadow_capture_state`(id=1, slot_name, snapshot_lsn, snapshot_exported_at, last_applied_lsn,
  updated_at) — la frontera y el progreso, en UNA fila.
- `shadow_change_log`(lsn, seq_in_tx, src_table, op[I|U|D], pk TEXT, payload JSONB,
  received_at, applied_at NULL; PK(lsn, seq_in_tx)) — buffer idempotente; `applied_at` lo
  sella el proyector (§3). Retención: ciclos cerrados + 7 días (purga: B-04, que define el
  ciclo).
- `shadow_projection_batches`(id, first_lsn, last_lsn, min_received_at, started_at,
  finished_at, changes, revisions_new, recovered [core0009]) — marcas de LOTE del proyector:
  son la fuente de `latencia_p95` (§5; `offer_revisions.created_at` no enlaza con el cambio
  origen). **INTENCIÓN DURABLE [EJECUTADA 2026-07-28, P2-5 rev. externa parte 2]**: la fila
  se INSERTA al PLANIFICAR el lote (started_at, min_received_at, finished_at NULL) y se
  FINALIZA en la MISMA tx que sella la última fuente; al arrancar, `project_pending` cierra
  las intenciones huérfanas de invocaciones muertas (detectables porque el single-flight
  está libre) con finished_at=ahora y `recovered=true` — conservador: cuentan como lotes
  LENTOS en `latencia_p95`, jamás desaparecen (un lote lento + crash ya no es invisible).
El esquema de labels y métricas va APARTE en **`core0008a`** (§4/§5): NO depende del bloqueo
de compose y avanza ya.
**`core0009`** [EJECUTADA 2026-07-28, rev. externa parte 2]: `shadow_inbox`(consumer_id,
event_id, payload JSONB, received_at; PK(consumer_id, event_id)) — inbox sombra PERSISTENTE
e idempotente del outbox (destino del transporte de producción de Fase B, ver §8); columna
`heartbeat_at` en `shadow_capture_state` (LIVENESS del consumidor: cada keepalive del stream
y cada tx aplicada — el healthcheck deja de dar falso unhealthy con slot activo y días sin
tráfico legacy [P2-7]; `last_applied_lsn`/`updated_at` quedan como progreso de DATOS);
columna `recovered` en `shadow_projection_batches` (intención durable, arriba).

## 3. Proyección al pipeline del core — B-02

El proyector (tarea `jobhunt.shadow.project`, cola `core.harvest`; **cadencia beat cada 5
min** [EJECUTADA 2026-07-28, P1-1 rev. externa parte 2: proyectar solo a las 06:05 producía
lotes con ~20 h de latencia — `latencia_p95<=600s` era imposible; su single-flight tolera
solapes], setting `CORE_SHADOW_PROJECT_EVERY_S=300`) consume `shadow_change_log`
en orden LSN y lo convierte en el LENGUAJE del core — sin rutas nuevas de escritura:
- **`jobs` → `RawListingSink.handle`** (el MISMO sink de A-04..A-06): una fuente core por
  fuente legacy — `sources.name = "legacy:" + jobs.source` (tier 0, un `harvest_scope` sombra
  por fuente, `params={"shadow": true}`); `external_id = jobs.hash`; `url = jobs.url`;
  payload = {title, company_name: company, description, tags, location, canton, language,
  seniority, contract_type, remote, salary_*}. Extractor/normalizador registrados
  **PROGRAMÁTICAMENTE por cada fuente `legacy:<source>` observada** (el registry del core es
  exact-match, SIN comodines): el proyector da de alta el handler genérico en caliente al ver
  una fuente nueva (`harvest/providers/legacy_shadow.py`; title/company crudos, misma coerción
  central). ALERTA (no solo warning) cuando: `normalize_offer` devuelve None para una fuente
  `legacy:*`, o la fuente NO tiene handler registrado. `extract_identity` = `(None, None)` con
  payload sin título/empresa NO alerta: es la degradación conservadora normal de A-05 (esa
  función nunca devuelve None a secas).
  - `op=D` o `is_active=false` ⇒ cierre de la encarnación activa del slot (ended_at) — sin
    tocar la identidad compartida (el attach/recycle del sink decide, como siempre).
  - El upsert diario que solo refresca `last_seen_at` (content_hash idéntico) ⇒ ya lo absorbe
    el pre-filtro de revisiones del sink (DO NOTHING) — no genera revisión ni canónica nueva.
- **`user_profiles` → `profiles.save_profile_revision`**: consumer sombra único
  `"swissjob-shadow"`; `external_ref = user_id`; content = {title, cv_text, skills} —
  EXACTAMENTE el texto de perfil legacy portado en A-07 (PF.5): vectores comparables, requisito
  del plan §20. `users.is_active=false` ⇒ perfil excluido de evaluación (sin borrar).
  **`users` op=D (borrado GDPR legacy)** ⇒ ERASE del perfil sombra con ese `external_ref`
  (revisiones, vectores, evaluaciones, estado y outbox del perfil) — retención cero de
  `cv_text` huérfano en el core.
- Tras proyectar cada lote: `run_pending` de embeddings + `evaluate_profile` de los perfiles
  con corpus nuevo (el flujo NORMAL del core; nada especial de sombra).

**Receta de calidad del matching [EJECUTADA 2026-07-28, `core0010`]:** la identidad del
espacio vectorial es `(name, version, recipe_version)`; cambiar el preprocesado crea otro
`model_id` y otra partición, sin mezclar vectores aunque los pesos del modelo sean los mismos.
La receta canónica `role_composite_v2` produce UN único vector normalizado por entidad:

- oferta: `0.60 × encode(title + tags + company + description) + 0.40 × encode(title)`;
- perfil: `0.60 × encode(title + skills + cv_text) + 0.40 × encode(title)`.

La combinación se normaliza una sola vez y mantiene el matcher como coseno/pgvector, sin
reranker, FTS ni una segunda infraestructura. `legacy_v1` queda conservada e inactiva para
rollback. El proyector evalúa `top-K=1800`: el benchmark congelado necesitó como máximo 1496
candidatos relevantes y el margen evita barrer las 5261 ofertas vigentes. Cada ejecución
canónica retira `current_eval_id` de vacantes fuera de su top-K (preserva saved/dismissed,
feedback y notes), por lo que el feed es el conjunto vigente y no la unión histórica.

Evidencia LOCAL previa a abrir racha: cobertura 5261/5261 textos y 2/2 perfiles; nDCG@10
`0.611221` y `0.619797` frente a legacy `0.199351` y `0.173711`; falsos negativos `0/10` y
`0/14`; outbox drenado 8234/8234, 0 fallos y 0 dead-letter; suite `312/312 ×2`.

## 4. Set etiquetado por perfil — B-03

Migración **`core0008a`** (INDEPENDIENTE del bloqueo de compose — avanza ya) añade [B]:
- `labeled_sets`(id, profile_id→profiles, name, notes, created_at, **frozen_at**) — un set por
  perfil y ronda; **CONGELADO antes de contar ciclos** (frozen_at NOT NULL para el gate): el
  oráculo no se mueve durante la medición.
- `labeled_judgments`(set_id→labeled_sets, job_ref TEXT [= jobs.hash legacy], relevance
  SMALLINT 0..3 [0 irrelevante · 1 marginal · 2 relevante · 3 ideal], source
  [seed_feedback|manual], labeled_at; PK(set_id, job_ref)).
- `labeled_dedup_pairs`(id, job_ref_a, job_ref_b, verdict [duplicate|distinct], source,
  labeled_at; UNIQUE(LEAST(a,b), GREATEST(a,b))).
- `shadow_cycle_metrics` (definida en §5) — la tabla de MÉTRICAS también vive en `core0008a`:
  B-04 persiste y testea sin esperar al desbloqueo de B-01.

Semillas (comando `jobhunt.shadow.seed_labels`, luego CURACIÓN MANUAL obligatoria antes de
congelar): feedback legacy `thumbs_up→2`, `applied→3`, `thumbs_down/dismissed→0` (leído
read-only de `match_results.feedback`); pares dedup desde `jobs.duplicate_of` (verdict
duplicate) + muestreo de vecinos fuzzy_hash con verdict manual. Mapeo job_ref→vacante core
para MÉTRICAS: `source_listings.external_id = job_ref` en fuentes `legacy:*` → **CUALQUIER
encarnación del slot (activa O cerrada) → su vacancy_id** (la vacante persiste aunque el job
legacy se desactive/borre — imprescindible para dedup_precision/recall, cuyos pares sembrados
de `duplicate_of` apuntan por definición a jobs YA desactivados). La encarnación ACTIVA solo
se exige donde se mide corpus vivo (`perdida`, feed).

**Decisiones DELEGADAS 2026-07-28 (cierre de la discrepancia NO-GO 2 del revisor, por
delegación del propietario):**
- **Sets de perfiles INACTIVOS fuera de `labels_ready` y de la medición.** Las métricas usan
  EL MISMO mecanismo de exclusión que el proyector (§3): helper compartido
  `inactive_user_refs` (último estado `users` por pk del staging YA aplicado — jamás una
  consulta duplicada). Un perfil con `external_ref` inactivo NO se mide (sin filas
  ndcg/overlap/falsos_negativos ni gate por perfil: el proyector no lo evalúa y medirlo
  generaría rojos vacuos sobre un feed congelado) y sus sets congelados NO cuentan para el
  `>= 2` de `labels_ready` (evidencia vacua). El set congelado se CONSERVA inmutable y
  `details.sets_excluidos_inactivos` deja el rastro; re-activar al usuario (staging
  `users.is_active=true` aplicado) lo devuelve a la medición y al conteo sin re-congelar.
- **Personas de EVALUACIÓN documentadas permitidas en dev** (SIN rebajar el umbral
  ratificado de `>= 2` sets contables): en el entorno LOCAL de la sombra, un set contable
  puede pertenecer a una persona de evaluación creada por la PROPIA API del legacy
  (register + profile + CV coherente y realista de un rol REAL — no un usuario fingido con
  perfil vacío), con curación manual conservadora `>= 30` juicios y `notes` del set
  DECLARANDO que es una persona de evaluación y sus criterios. Primera aplicación:
  `persona-eval-2@dev.swissjob.ch` (Senior Backend Developer Python, rol distinto al del
  primer set; 31 juicios, 10 rel>=2 ⇒ falsos_negativos en modo 0-permitidos). En producción
  los sets siguen exigiendo usuarios reales.

## 5. Métricas por ciclo — B-04 (fórmulas exactas)

Un **ciclo** = ventana CALENDARIO **[06:00 CET, 06:00 CET del día siguiente)**: corta DESPUÉS
del mantenimiento legacy (cleanup 03:30, dedup 04:00) y ANTES del arranque más temprano de la
cosecha diaria (12:00−4h = 08:00) — cada ciclo contiene exactamente UNA cosecha completa con
su mantenimiento, y el corte es determinista (no "alineado" a la hora variable). La tarea
`jobhunt.shadow.metrics` computa y persiste en `shadow_cycle_metrics`(cycle_id, started_at,
finished_at, metric, scope [global|profile:<id>], value NUMERIC, details JSONB) [**core0008a**]:

| Métrica | Fórmula | Fuente |
|---|---|---|
| `ndcg@10` | por perfil con set congelado: DCG@10 = Σ (2^rel_i −1)/log2(i+1) sobre el feed core (§A-09) mapeado a job_ref; IDCG con las 10 mejores etiquetas del set; nDCG = DCG/IDCG | core + labels |
| `overlap@10` | \|top10_core ∩ top10_legacy\| / 10, por perfil — top10_legacy = el feed VISIBLE del usuario (espejo de `get_results`: JOIN `jobs` con is_active AND duplicate_of IS NULL, excluyendo NEGATIVE_FEEDBACK dismissed/thumbs_down) ORDER BY score_final DESC, read-only | core + legacy RO |
| `dedup_precision` | sobre `labeled_dedup_pairs`: TP/(TP+FP), donde "core dice duplicate" = misma vacante (attach) o `dedup_candidates` state≠rejected | core + labels |
| `dedup_recall` | TP/(TP+FN) sobre los pares verdict=duplicate | core + labels |
| `falsos_negativos` | #{job_ref con relevance≥2 en el set, presentes en corpus core, AUSENTES del feed core del perfil} / #{relevance≥2 presentes} | core + labels |
| `perdida` | #{PK en `public.jobs` WHERE **is_active AND duplicate_of IS NULL** AND no-cuarentenado} − #{slots `legacy:*` con encarnación activa} tras drenar + #{change_log sin applied_at > 1h} — ambos lados sobre el MISMO corpus vivo (el legacy conserva como fila inactiva duplicados/404/archivadas: nunca entran al minuendo). Los cuarentenados por límite de esquema del sink (p.ej. url > 1000; legacy admite 2048) se cuentan APARTE en `no_ingeribles` (alerta si > 0) | capture + core |
| `outbox_lag_p99` | p99 de `delivery.stats().oldest_pending_s` muestreado cada 5 min en el ciclo — **EDAD DEL EVENTO** [P2-6, 2026-07-28]: `clock_timestamp() − integration_outbox.created_at` del evento NO entregado más viejo (estados pending E inflight; jamás negativa). NUNCA la distancia a `next_attempt_at`: aquello medía el próximo reintento y un fallo con backoff futuro aplanaba el lag justo cuando crecía | core |
| `outbox_dead` | [P2-6, 2026-07-28] max(`dead_total` muestreado en el ciclo, conteo `state='dead'` actual al cómputo — dead es terminal y sin purga automática); el muestreador guarda `dead_total` en cada sample | core |
| `latencia_p95` | p95 por LOTE de (`finished_at` − `min_received_at`) sobre `shadow_projection_batches` (§2) — `offer_revisions.created_at` NO enlaza con el cambio origen, la traza es del lote. INCLUYE los lotes `recovered` (P2-5: intención huérfana cerrada por la recuperación = lote LENTO visible) | capture + core |
| `coste` | embeddings computados + evaluaciones nuevas + segundos de worker por ciclo (proxy de €; el core no usa LLM en A) | core |
| `reenlace_pct` | (attaches + recycles del ciclo) / encarnaciones tocadas — churn de identidad | core |

## 6. Umbrales, ciclos y operación — B.3 [RATIFICADOS 2026-07-24 por delegación del propietario]

- **N = 7 ciclos diarios CONSECUTIVOS** con TODO dentro de umbral (un fallo → se corrige y el
  contador vuelve a 0).
Cada métrica queda marcada **[gate]** (incumplir resetea el contador de ciclos) o **[alerta]**
(se registra y avisa, no resetea):
- `ndcg@10` **[gate]** ≥ 0.60 por perfil medido Y ≥ nDCG@10 del feed VISIBLE legacy (§5, mismo
  espejo de `get_results`) contra el MISMO set − 0.05 (misma fórmula; no se exige superarlo,
  sí no degradar).
- `overlap@10` **[alerta]** — informativa (el set etiquetado es el oráculo, plan §12).
- `dedup_precision` **[gate]** ≥ 0.95 · `dedup_recall` **[gate]** ≥ 0.90.
- `falsos_negativos` **[gate]**: con < 50 juicios rel≥2 en el set del perfil ⇒ 0 permitidos
  (2% no es medible en sets pequeños); con ≥ 50 ⇒ ≤ 0.02.
- `perdida` **[gate]** = 0 (estricto, DoD B.2 "cero pérdida") · `no_ingeribles` **[alerta]** > 0.
- `outbox_lag_p99` **[gate]** ≤ **900 s** · `outbox_dead` **[gate]** = 0 [P2-6, 2026-07-28: un
  evento en DEAD-LETTER durante el ciclo ⇒ rojo — dead exige intervención del operador; el
  conteo va en details] · `latencia_p95` **[gate]** ≤ **3.600 s** · `reenlace_pct`
  **[alerta]** ≤ 5%/ciclo · `coste` **[alerta]** (proxy informativo de €, sin umbral duro).
  > **Enmienda RATIFICADA por el propietario el 2026-08-23** (cierre de B-4 de la auditoría
  > externa; antes 300/600 s — los ciclos previos a esta fecha NO cuentan para ninguna racha):
  > - `outbox_lag_p99` 300→900 s. Razón: el umbral original era IGUAL a la cadencia de
  >   despacho (300 s) — un evento emitido justo tras un despacho esperaba 300 s POR
  >   CONSTRUCCIÓN y un solo backoff transitorio (60→120→240) ponía rojo sin fallo real.
  >   3× la cadencia absorbe un burst de backoff. No convierte ningún ciclo histórico.
  > - `latencia_p95` 600→3.600 s. Definición: espera del staging del CDC (p95 por lote,
  >   §5) — mecanismo SOLO-SOMBRA que se retira en Fase F; la comparación del gate es
  >   DIARIA y un pico de 1 h en días de cosecha (lock del proyector en la CPU del NAS,
  >   p95 medido 1.426–3.229 s) no degrada la evidencia de la proyección. Decisión
  >   EXPLÍCITA de producto/operación, tomada conociendo que convierte 12 de 16 ciclos
  >   históricos (la auditoría B-4 lo exigía así, no como consecuencia de la racha).
  > Fuente única de verdad: ESTE contrato; `metrics.py` debe referenciarlo, no redefinirlo.
- **Slot**: retención WAL > 2 GiB o consumidor parado > 30 min ⇒ ALERTA (el slot retiene WAL:
  riesgo de disco real).
- **RPO sombra = 0** (replay desde slot; ack solo tras commit). **RTO consumidor ≤ 1 h**
  (re-arranque desde `last_applied_lsn`; runbook en B-05).
- **Rollback/replay sombra** (ensaya el de C): parar consumidor → `DROP_REPLICATION_SLOT` →
  truncar staging + desactivar fuentes `legacy:*` (archivar sus vacantes) → re-crear slot +
  re-backfill por snapshot. Documentado y EJECUTADO al menos 1 vez en B-05.

## 7. Tickets

| Ticket | Alcance | DoD |
|---|---|---|
| B-01 ✅ | Infra CDC — **HECHO 2026-07-25** (commits `31507a6`+`71d68f2`; slot wal2json real streaming, backfill 5033 == legacy, TOAST _omitted/_backfilled, readiness pre-slot, healthcheck active; doble análisis Opus: 6+5 confirmados corregidos; suite 244/244 ×2; SOLO LOCAL) | ⚠️ bloqueado hasta OK del propietario al cambio de compose Y a la ventana de reinicio. Backfill consistente verificado (conteos legacy = staging); ack tras commit demostrado (kill −9 del consumidor sin pérdida ni duplicado aplicado) |
| B-02 ✅ | Proyector staging→sink/perfiles — **HECHO 2026-07-25** (commits `52679a9`+`2b8ab38`; tx por fuente con invariante del sink, single-flight de sesión, merge TOAST, erase GDPR, replay condicionado tras el drenado; doble análisis Opus: 4+2 confirmados corregidos; suite 262/262 ×2; SOLO LOCAL) | corpus core espejo del legacy activo (perdida=0 tras drenar); re-proyección idempotente; DELETE cierra encarnación sin romper identidad compartida |
| B-03 ✅ | Set etiquetado — **CÓDIGO HECHO 2026-07-24** (`core0008a` + `shadow/labels.py`: seed feedback→relevancia y pares desde duplicate_of trazables, guard de congelado sin TOCTOU, mapeo por cualquier encarnación; commit `deb5a81`, suite 230/230 ×2; doble análisis Opus: 0+0 hallazgos) | set congelado ≥ 30 juicios/perfil en ≥ 2 perfiles reales (y consciente del umbral de §6: < 50 rel≥2 ⇒ falsos_negativos en modo 0-permitidos); pares dedup ≥ 50; seeds trazables a su origen — *la CURACIÓN sobre datos reales queda para el arranque de la sombra (requiere B-01/B-02)* |
| B-04 ✅ | Métricas por ciclo + purga — **HECHO 2026-07-25** (commits `a2962ea`+su fix; 10 fórmulas exactas + gates ratificados + informe + purga con guards de sellado y de users; doble análisis Opus: 5+1 confirmados corregidos; suite 283/283 ×2; SOLO LOCAL) | las 10 métricas de §5 computadas sobre un ciclo real con las fórmulas EXACTAS; informe por ciclo legible; purga del staging aplicado en marcha |
| B-05 ✅ | Harness GATE-SOMBRA — **HECHO 2026-07-25** (commits `387eae3`+fix; contador N=7 con corte anticipado, alertas de slot, RUNBOOK, rollback/replay EJECUTADO en tests, beat embebido en core-worker; doble análisis Opus: 2+2 corregidos; suite 291/291; SOLO LOCAL) | 1 rollback/replay completo ejecutado; simulacro de fallo (consumidor caído 30 min) con alerta y recuperación sin pérdida |

**Qué avanza SIN tocar compose** (mientras B-01 espera el OK): `core0008a` + seed + curación
(B-03), código de métricas (B-04) y el código+tests del proyector con staging SINTÉTICO (B-02
parcial — su DoD final sí requiere B-01). B-05 requiere B-01/B-02 completos.

🚦 **GATE-SOMBRA**: 7 ciclos consecutivos con los [gate] de §6 en verde + ensayo de rollback
hecho + umbrales ratificados por el propietario. Cierra la Fase B y habilita la Fase C.

> **ENMIENDA 2026-08-29 — qué significa «consecutivos» (core0036).** El contador
> trataba igual un ciclo ROJO y uno AUSENTE, y no son lo mismo: el primero es
> evidencia de que algo falló, el segundo es ausencia de evidencia. Con esa
> equivalencia, «siete consecutivos» solo era medible en un anfitrión que no se
> apaga nunca — apagar el equipo una noche reiniciaba la cuenta.
>
> Ahora una ausencia **se salta** si está DECLARADA (`shadow_declared_downtime`,
> con motivo, fecha y firma). Lo que **no** cambia:
>
> - un ciclo **rojo sigue rompiendo** la racha;
> - una declaración **no puede tapar un ciclo computado**: si hay métricas para
>   ese día, mandan las métricas. Solo cubre la ausencia TOTAL;
> - máximo **3 paradas declaradas**, y los siete verdes deben caber en **14 días**
>   de calendario — sin topes, «consecutivos» dejaría de significar nada;
> - el informe **publica siempre** las paradas: nadie puede leer «7 verdes» sin
>   ver las que hubo entre medias.
>
> Se declaran explícitamente y no se perdonan en silencio porque **desde dentro no
> hay forma fiable de distinguir «lo apagué yo» de «se cayó solo»**, y lo segundo
> es justo lo que el gate debe cazar. La declaración traslada esa afirmación a una
> persona y deja rastro.
>
> **Esta enmienda DEBILITA una propiedad de seguridad** y está pendiente de que la
> revisión externa dirigida intente romperla.

## 8. Riesgos específicos de la sombra

- **Slot lógico retiene WAL** si el consumidor cae → disco lleno de la BD COMPARTIDA con
  legacy: alerta §6 + runbook drop-slot de emergencia (perder la sombra ≪ tumbar producción).
- **Tormenta de updates** en la cosecha diaria (upsert masivo refresca `last_seen_at` de todo
  lo re-visto): se stagea TODO (§2: sin filtro de no-cambios en el consumidor — REPLICA
  IDENTITY default no da valores viejos y un filtro por contenido perdería `is_active`/
  `duplicate_of`); el coste real son filas de staging (retención 7 días) y WAL leído — el
  pre-filtro del sink evita que lleguen a revisión/canónica.
- **Reinicio del Postgres compartido** al activar `wal_level=logical` (no recargable):
  ventana de mantenimiento pactada con el propietario; riesgo de amplificación de WAL del
  clúster entero tras el cambio (monitorizar disco).
- **GDPR en sombra**: el borrado legacy (`users` op=D) dispara el ERASE del perfil sombra
  (§3); `hashed_password`/`email`/`gdpr_*` jamás entran al staging (whitelist §2). Sin esto la
  sombra sería un fuga de retención.
- **Cuarentena por límites de esquema** (url > 1000 vs 2048 legacy): contador `no_ingeribles`
  con alerta y EXCLUSIÓN explícita del minuendo de `perdida` (§5) — jamás pérdida silenciosa.
- **Payloads grandes** (description/cv_text): columnas vector/tsvector se descartan en el
  consumidor; el resto es contractual (el core los necesita para canónica/embeddings propios)
  CON el matiz TOAST de §2: en un UPDATE que no los toca llegan AUSENTES del mensaje (no
  NULL) — `_omitted` lo hace explícito, `user_profiles` se completa por re-lectura RO y en
  `jobs` la ausencia es aceptada (la absorbe el pre-filtro del sink).
- **Trigger legacy `tsvector_update_jobs`** escribe server-side: aparece en WAL como parte del
  UPDATE normal — sin caso especial.
- **Doble motor visible**: prohibición dura de notificaciones/entregas a usuarios reales desde
  el core en sombra — el outbox core entrega SOLO al consumer sombra (inbox de prueba).
  **Entrega sombra REAL [EJECUTADA 2026-07-28 — decisión delegada del propietario, P1-1b
  rev. externa parte 2]**: transporte de producción `shadow/inbox.py` → tabla
  `jobhunt.shadow_inbox` (core0009; INSERT síncrono ON CONFLICT DO NOTHING sobre
  PK(consumer_id, event_id)) — inbox PERSISTENTE e idempotente que demuestra at-least-once +
  consumo idempotente EN CONTINUO, sin ningún efecto visible (la tabla vive en el esquema del
  core). Se registra en el ARRANQUE del worker SOLO si no hay transporte ya inyectado (los
  tests inyectan el suyo); despacho `jobhunt.delivery.dispatch_outbox` en beat cada 5 min
  (`CORE_DELIVERY_DISPATCH_EVERY_S=300`). El transporte real **HTTP al inbox del BFF llega en
  Fase C** y sustituye a este por la misma costura (`delivery.set_transport`).
