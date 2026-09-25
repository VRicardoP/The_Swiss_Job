# Contratos ejecutables — Fase A (CC.2) · v4

> Regenera tickets/DTOs a la versión actual (plan §6 + `ADR_JOBHUNTING.md` v3). **Supersede**
> `FASE_0_TICKETS_Y_ESTIMACION.md`. Contrato de la **vertical mínima** de Fase A.
> **v4**: 3ª revisión (10 hallazgos): incarnaciones en los punteros, FKs compuestas, corpus global,
> entrega por destino, merge sin mutar, Redis/CDC decididos. Estado: **RATIFICADO** (2026-07-22,
> GATE-CC CERRADO) — **FASE A COMPLETA** (2026-07-24, ensayo del GATE A superado).
> Fecha: 2026-07-22 (últ. act. 2026-07-24).

---

## 0. Aislamiento (decidido, ADR-08)
El **broker de Celery + leader-locks del core** van a un **Redis DEDICADO** (2.º contenedor,
`noeviction`), separado del Redis de caché (`allkeys-lru` no debe expulsar mensajes). Alternativa:
RabbitMQ. **No** un 3.er Postgres, pero **sí** un Redis dedicado. Los 4 `docker-compose*.yml` añaden
el servicio `redis-core` y el core apunta su `CELERY_BROKER_URL`/locks ahí.

## 1. Manifiesto de migración (orden de dependencias)

> **[A]** = crea la migración de la vertical mínima. Nullabilidad, ON DELETE y **FKs compuestas**
> explícitas (integridad de propietario, hallazgo #3).

**Identidad/auth**
- `consumers`(id, name, active) **[A]** · `consumer_credentials`(id, consumer_id→consumers, key_id UNIQUE, hash, scopes JSONB, expires_at, revoked_at) **[A]**
- `profiles`(id, consumer_id→consumers, external_ref, created_at; **UNIQUE(consumer_id, external_ref)**) **[A]**
- `profile_revisions`(id, profile_id→profiles, content JSONB, content_hash, text_hash, created_at; UNIQUE(profile_id,content_hash); **UNIQUE(id, profile_id)** [FK compuesta]) **[A]**
- `profile_embeddings`(profile_revision_id, profile_id, model_id→embedding_models, vector(384); **FK compuesta (profile_revision_id, profile_id)→profile_revisions(id, profile_id)**; UNIQUE(profile_revision_id,model_id)) **[A]**
- `profile_revision_activations`(profile_id→profiles, revision_id, seq, created_at; **PK(profile_id, seq)**; **FK compuesta (revision_id, profile_id)→profile_revisions(id, profile_id)**) **[A — core0004, RATIFICADA en la rev. externa de A-07 #1]** — *vigencia APPEND-ONLY monotónica: vigente = max(seq); una reversión re-activa la revisión inmutable histórica (created_at/UUID jamás deciden vigencia).*
- `idempotency_records`(...) — *NO [A]: API Fase A read-only*

**Cosecha**
- `sources`(id, name, tier, is_restricted, authorized_route, rate_limit, robots_ok) **[A]** · `harvest_scopes`(id, source_id→sources, params JSONB, tier, enabled) **[A]** · `source_scope_state`(scope_id PK, cursor JSONB, last_complete_at, consecutive_failures) **[A]**

**Corpus** (ADR-01/02)
- `embedding_models`(id, name, version, dim, active; UNIQUE(name,version)) **[A]**
- `vacancies`(id, current_offer_revision_id **NULLABLE**, **primary_incarnation_id NULLABLE**, merged_into→vacancies **NULLABLE**, **archived_at [idx]**, created_at) **[A]** — *Activa = archived_at IS NULL AND merged_into IS NULL. `(current_offer_revision_id, id)→offer_revisions(id, vacancy_id)` y `(primary_incarnation_id, id)→source_listing_incarnations(id, vacancy_id)` [FKs compuestas, #2]. Ciclo resuelto por nullable/DEFERRABLE.*
- `source_listings`(id, source_id→sources, external_id, url_normalized; UNIQUE(source_id,external_id), UNIQUE(source_id,url_normalized)) **[A]** — *identidad ESTABLE del slot; NO cuelga de vacante ni guarda URL/timestamps de una oferta concreta (van en la incarnación, #2).*
- **`source_listing_incarnations`**(id, source_listing_id→source_listings, vacancy_id→vacancies, seq, **url**, **apply_url**, **first_seen_at**, **last_seen_at**, ended_at; **UNIQUE(source_listing_id) WHERE ended_at IS NULL**; **UNIQUE(id, vacancy_id)** [para FK compuesta]; **vacancy_id [idx parcial WHERE ended_at IS NULL, core0007 — lecturas /v1 de listings activos por vacante]**) **[A]** — *binding slot→vacante en el tiempo, con la URL/apply/timestamps de ESA oferta; `last_seen_at` en cada cosecha; el reciclado cierra una y abre otra (ADR-01, #2).*
- `source_listing_revisions`(id, **incarnation_id→source_listing_incarnations**, content_hash, raw JSONB, fetched_at; UNIQUE(incarnation_id,content_hash)) **— obligatoria [A]**
- `offer_revisions`(id, vacancy_id→vacancies, content_hash, **text_hash**, content JSONB, created_at; UNIQUE(vacancy_id,content_hash); **UNIQUE(id, vacancy_id)** [FK compuesta]) **— inmutable, sin vector [A]**
- `offer_revision_sources`(offer_revision_id, source_listing_revision_id, **vacancy_id**; **PK(offer_revision_id, source_listing_revision_id)**; **FK compuesta (offer_revision_id, vacancy_id)→offer_revisions(id, vacancy_id)**; check/trigger: la incarnación de la revisión raw es de la MISMA `vacancy_id`) **[A]** — *impide mezclar raw de otra vacante (#3).*
- `offer_embeddings`(**text_hash**, model_id→embedding_models, vector(384); PK(text_hash,model_id)) **[A]** — *clavado en text_hash; **PARTICIONADA POR LIST(model_id)**: cada modelo = su partición = su propio HNSW (heredado del índice padre); registrar un modelo crea su partición (A-06+); multi-dim = expand/contract.*
- `link_evidence`(id, source_listing_id, vacancy_id, method, confidence, created_at) **[A]** · `dedup_candidates`(id, vacancy_a, vacancy_b, similarity, **state ENUM(pending,confirmed,rejected)**, resolved_by, resolved_at, merge_log_id→merge_log **NULL**; **UNIQUE(LEAST(vacancy_a,vacancy_b), GREATEST(vacancy_a,vacancy_b))**) **[A]**
- `merge_log`(id, winner_id→vacancies, loser_id, evidence JSONB, confidence, actor, created_at) **[A]** · **`merge_transfers`**(id, merge_log_id→merge_log, entity, row_key, before JSONB, after JSONB, created_at) **[A]** — *manifiesto por fila del estado mutable transferido; el split lo repone (ADR-04, #4).* · `offer_translations`(offer_revision_id, field, source_lang, target_lang, input_hash, text, state; UNIQUE(...))

**Matching/estado** (ADR-03)
- `scoring_policies`(id, name, prompt_version, weights JSONB, active) **[A]**
- `match_evaluations`(id, profile_id→profiles, vacancy_id→vacancies, offer_revision_id, profile_revision_id, model_id→embedding_models, scoring_policy_id→scoring_policies, eval_key, **score_final NUMERIC**, scores JSONB, explanation, created_at; UNIQUE(profile_id,vacancy_id,eval_key); **UNIQUE(id, profile_id, vacancy_id)** [FK del feed]; **FK compuesta (offer_revision_id, vacancy_id)→offer_revisions(id, vacancy_id)**; **FK compuesta (profile_revision_id, profile_id)→profile_revisions(id, profile_id)** [#3]; idx(profile_id, score_final DESC, vacancy_id)) **— append-only [A]**
- `profile_vacancy_state`(profile_id, vacancy_id, feedback, dismissed_at, saved_at, **notes**, current_eval_id, updated_at; PK(profile_id,vacancy_id); **FK compuesta (current_eval_id, profile_id, vacancy_id)→match_evaluations(id, profile_id, vacancy_id)**) **[A]**
- `profile_vacancy_events`(id, profile_id, vacancy_id, kind, data JSONB, created_at) **[A]**
- `applications`(id, profile_id, vacancy_id, **source_listing_incarnation_id NULLABLE**, snapshot JSONB, status[start=applied], **notes**, **follow_up_date**, created_at; UNIQUE(profile_id,vacancy_id); **FK compuesta (source_listing_incarnation_id, vacancy_id)→source_listing_incarnations(id, vacancy_id)** [#2]) · `application_status_events`
- `generated_documents`(id, profile_id, offer_revision_id **NULLABLE→SET NULL**, doc_type, version, async_state, content, output_hash, pdf_location, created_at) · `saved_searches` · `job_filters` · `pattern_suggestions`

**Colegios** — `schools`(17) · `school_monitors`(18) · `school_job_details`(…, vacancy_id→vacancies) · `school_applications`

**Orquestación/entrega** (ADR-06)
- `harvest_runs`(id, started_at, finished_at, status) **[A]** · `source_harvest_runs`(run_id, scope_id; UNIQUE(run_id,scope_id)) **[A]**
- `integration_outbox`(**event_id UNIQUE**, aggregate, aggregate_id, **subject_profile_id [idx]**, version, type, payload JSONB, created_at) **[A]** — *sin estado de entrega aquí (va por destino, #5).*
- **`integration_outbox_deliveries`**(event_id→integration_outbox, **destination**, **state ENUM(pending,inflight,delivered,dead)**, attempts, **next_attempt_at**, **last_error**, lease, ack_at; **UNIQUE(event_id, destination)**) **[A]** — *estado/reintento/backoff/dead-letter POR destino (#5/#8).*
- `erase_requests`(id, subject_profile_id, **required_consumers JSONB** [conjunto CONGELADO al crear la solicitud], requested_at, completed_at) **[A]** · `erase_acks`(erase_id→erase_requests, consumer_id, acked_at; UNIQUE(erase_id, consumer_id)) **[A]** — *`profile.erased`: completa cuando llegan los acks de TODOS los `required_consumers` (#6/#8/GDPR).*
- `notification_outbox`(id, **subject_profile_id [idx]**, kind, payload, provider_idempotency_key, state, sent_at)
- *`integration_inbox`* — **NO es tabla del core**: vive en la **BD de cada consumidor (BFF)**, transaccional con su efecto; `UNIQUE(consumer_id-implícito, event_id)`, conserva agregado/payload (#5).
- `offer_identity_map`(legacy_hash, vacancy_id, source_listing_id) — solo migración

> FKs a corpus **no destructivas**; nunca CASCADE sobre dato de usuario (PF.3). FKs **compuestas**
> donde el propietario debe coincidir (#3). Claves estables + `event_id` determinista → idempotencia.

---

## 2. DTOs y API `/v1` (read-only en Fase A) — multi-tenant (ADR-09)
- **Ownership (solo recursos TENANT-OWNED):** las queries a **perfiles/matches/estado/candidaturas**
  se filtran por `consumer_id` (`profile.consumer_id == credential.consumer_id`; cross-tenant → **404**).
  El **corpus (`vacancies`/`offer_*`) es GLOBAL**: `GET /vacancies/{id}` es legible por cualquier
  credencial con scope `vacancies:read`, **sin** ownership por consumidor (#4). **Scopes**:
  `vacancies:read`, `matches:read`, `profiles:read` (matriz ruta→scope; sin scope → 403). Errores
  `{code,message,details}` (401/403/404/409/429). **Contract tests negativos cross-consumer** obligatorios.

  **Matriz ruta→scope→ownership:**
  | Ruta | Scope | Ownership |
  |---|---|---|
  | `GET /v1/vacancies/{id}` | `vacancies:read` | GLOBAL |
  | `GET /v1/profiles/{pid}/matches` | `matches:read` | tenant (`pid.consumer == cred`) |
  | `GET /v1/profiles/{pid}/matches/version` | `matches:read` | tenant (misma regla: 404 indistinguible) |
  | `GET /v1/profiles/{pid}` | `profiles:read` | tenant |

  **ETag** = hash de la representación (versión optimista) · **cursor** = keyset opaco
  `base64(score_final|vacancy_id)` · **tipos/nullabilidad** completos en los esquemas Pydantic/OpenAPI
  que produce **A-09** (este contrato fija la FORMA; A-09 el esquema formal + contract tests).
- **DTO vacante** (multi-listing): title/company/description/salary/tags de la offer_revision vigente;
  `primary_listing` {source, external_id, url, apply_url, first_seen_at, last_seen_at} + `listings[]`
  {source, url, apply_url}; `translations`. Solo vacantes **ACTIVAS**.
  **`language`** (añadido 2026-09-22, punto 5): ADITIVO y OPCIONAL, tomado de la canónica.
  Ausente significa **desconocido**, nunca «no tiene idioma», y un valor que no sea una cadena
  utilizable se sirve como ausente en vez de tumbar la página — es un indicador, no la identidad.
  El consumidor debe tener su propio plan para el caso ausente: hoy sólo el 3,1 % del feed
  productivo lo trae.
- **DTO versión del feed** (`MatchesVersionDTO`, añadido 2026-09-23, punto 5): `{version, total}`.
  Deja saber si el feed cambió **sin descargarlo** — recorrerlo era el 87-89 % del coste de servir
  la pantalla principal, y un `If-None-Match` no lo evitaba porque el ETag se deriva del payload.
  `version` es **OPACA**: su composición puede cambiar sin aviso y el consumidor sólo debe
  compararla con la que guardó. Lo único garantizado, y lo que un consumidor puede asumir:
  **si el feed servido cambia, la versión cambia** — incluido el estado de usuario del core
  (`feedback`, `saved`) y el listing primario (2026-09-23; la versión inicial no los cubría y
  un consumidor cacheado sirvió feedback rancio). Quedan FUERA, declarado: `url`/`apply_url`/
  `last_seen_at` de listings NO primarios y `notes`. Lo contrario NO se garantiza — dos
  versiones distintas pueden describir feeds iguales. Un recorrido paginado no es atómico:
  el consumidor debe releer la versión al terminar y descartar el recorrido si cambió.
- **DTO match**: `vacancy` + `evaluation` {eval_key, model, policy, **score_final**, scores,
  explanation, matching/missing_skills} + `state` {saved, dismissed, feedback, notes}. Feed: excluye
  dismissed + no-activas; orden `score_final DESC`, **keyset** por `(score_final, vacancy_id)`.

---

## 3. Catálogo de eventos (at-least-once + destino, ADR-05/06)
| type | clave-natural de `event_id` | destino |
|---|---|---|
| `vacancy.upserted` | `vacancy_id + content_revision` | (caché BFF, opcional) |
| `match.evaluated` | `eval_key` | BFF del consumidor del perfil |
| `profile_vacancy.state_changed` | `profile_id+vacancy_id+updated_at` | BFF del consumidor |
| `application.status_changed` | `application_id + status + version` | BFF |
| `saved_search.changed` | `saved_search_id + revision` | BFF del consumidor del perfil (añadido C-4 v2.1, Decisión 6) |
| `notification.requested` | `profile_id+kind+dedupe_key` | core (email, provider-idempotency-key) |
| `profile.erased` | `profile_id + erase_id` | **todos los BFF** (borran/anonimizan + confirman) |

> `event_id = uuid5(ns, type||':'||clave-natural)`, insert en la txn de la escritura,
> `ON CONFLICT DO NOTHING`; **`version` monotónica** por agregado; consumo idempotente por
> **`(consumer_id, event_id)`**. Fase A entrega **`match.evaluated`** extremo a extremo.

---

## 4. Tickets — VERTICAL MÍNIMA de Fase A

| ID | Ticket | DoD |
|---|---|---|
| A-01 ✅ | **Servicio + Alembic + aislamiento** (paquete-en-repo, **`redis-core` dedicado** broker/locks, rol BD, migration job) — **HECHO 2026-07-22** (paquete `SwissJob/jobhunt_core/`; auditado por agentes Opus: 11 hallazgos corregidos) | ✅ verificado: worker legacy no cruza (disjunción en los 4 compose); broker en `redis-core` (worker ready + ping E2E); migra solo el core (`jobhunt.alembic_version`, legacy intacto; rol **sin privilegios sobre tablas, columnas, secuencias ni funciones SECURITY DEFINER legacy** — acceso únicamente a los objetos pgvector de `public`, verificado en cada migrate) |
| A-02 ✅ | **Migración [A]** (§1; FKs compuestas + circulares nullable) — **HECHO 2026-07-22** (`core0002`, 30 tablas; auditoría Opus: 11 hallazgos corregidos) | ✅ verificado: upgrade limpio; head único; **downgrade→re-upgrade roundtrip**; sin ciclo (punteros nullable + ALTER + **SET NULL por-columna PG15+**); 15 invariantes contractuales probados contra Postgres real (FKs compuestas ambos lados, UNIQUE parcial/canónico/eval_key, RESTRICT ADR-03, trigger ORS, vector+HNSW) |
| A-03 ✅ | **Ingesta 1 provider + scopes + estado por scope** — **HECHO 2026-07-23** (Arbeitnow Tier 0; auditoría Opus + 4 rondas externas). **Diseño final:** barrido COMPLETO por run con **EMISIÓN TOTAL** (el sink idempotente dedup/refresca — A-04 ve todo lo visible en cada cosecha) + **objetivo adaptativo de páginas** (crece hasta agotar el feed); barrido incompleto = `partial` (sin `last_complete_at`). El cursor del scope guarda metadatos/objetivo + fingerprint semántico | ✅ verificado: sink+estado en UNA tx (commit al final; rollback conjunto); `FOR UPDATE` + re-validación total (cursor/enabled/params/fuente) → `stale`/`skipped` sin pisar; borrado mid-run y feed > tope: solo omisión temporal, jamás pérdida |
| A-04 ✅ | **Listing (slot) + incarnación + revisión raw ANTES de normalizar; refrescar `last_seen_at`** — **HECHO 2026-07-23** (`RawListingSink` por lotes + tarea `jobhunt.harvest.run_scope`; auditoría Opus: 5 hallazgos corregidos, incl. carrera cross-scope con ON CONFLICT parcial + orden determinista) | ✅ verificado: revisión cuelga de la incarnación (por `content_hash`, idempotente); `last_seen_at`/url en CADA fetch; seq=max+1 al reabrir; vacante fresca + puntero primario; carrera entre scopes de la misma fuente sin duplicados/deadlock (test ×5) |
| A-05 ✅ | **Identidad/re-enlace determinista** — **HECHO 2026-07-23** (`harvest/identity.py` PF.5 portado + extractores por fuente; guard de reciclado por tokens de empresa; attach cross-source por `url_normalized` vigente — índice `core0003` — solo al crear; alias external_id↔URL; drift/difusos intra-lote → `dedup_candidates` pending; auditoría Opus: 3 hallazgos corregidos, incl. reasignación determinista del primary en vacante compartida) | ✅ verificado: no funde por semántica sola (candidatos, jamás merge); reciclado no corrompe (vacante NUEVA sin re-attach; primary reasignado a incarnación activa; historial intacto); sin identidad completa NUNCA recicla. *Cosine/`SIM_RECYCLE`, nivel-3 semántico + resolución de candidates → Fase B (esquema en A).* |
| A-06 ✅ | **`offer_revisions`(+text_hash) + `offer_embeddings`(text_hash, vector(384)+HNSW)** — **HECHO 2026-07-23** (`harvest/normalize.py` canónica por fuente + `_canonicalize` auto-reparador en el sink + `embeddings.py`/tarea `jobhunt.embedding.run_pending` con partición por modelo y backend inyectable; auditoría Opus: 3 hallazgos corregidos) | ✅ verificado: embedding por text_hash (mismo texto en N vacantes = 1 vector); concurrencia optimista (pre-filtro + ON CONFLICT, carrera → 1 fila); no re-embebe por salario/location (text_hash solo title+company+description+tags); puntero vigente sigue al contenido del primary (reverts incluidos); fuentes no primarias agregan sin mover el puntero |
| A-07 ✅ | **2 perfiles + `profile_revisions`/`profile_embeddings`** — **HECHO 2026-07-24** (`profiles.py`: consumer/perfil idempotentes + revisiones inmutables por content_hash normalizado; texto embebible = title+cv_text+skills espejo del legacy; tarea de embeddings extendida con el MISMO backend por (name, version); auditoría Opus: 2 gaps de cobertura corregidos, código limpio) | ✅ verificado: embebido por model_id (solo la revisión VIGENTE; re-run → 0); FK compuesta (revision, profile) rechaza cross-profile (test negativo); 2 perfiles E2E; escritura optimista (carrera → 1 fila); dim guard cubre perfiles |
| A-08 ✅ | **`match_evaluations`(columnas + score_final) + `profile_vacancy_state`** — **HECHO 2026-07-24** (`matching.py`: eval_key determinista + evaluate_profile con lock por perfil y evaluador CANÓNICO multi-modelo + feed keyset + set_dismissed/set_saved; tarea `jobhunt.matching.run_profile`; auditoría Opus: 9 hallazgos corregidos, incl. determinismo del current_eval y ef_search) | ✅ verificado: eval_key UNIQUE (reintento no duplica; append-only con la vieja intacta y current avanzando); feed = vigente + no-dismissed + activa, keyset por (score_final DESC, vacancy_id) con empate real probado; estado estable (feedback/dismissed/saved/notes jamás pisados); RESTRICT del current_eval probado |
| A-09 ✅ | **API `/v1` read-only multi-tenant** — **HECHO 2026-07-24** (`credentials.py` + `api/{deps,schemas,v1}.py`: matriz ruta→scope, sobre de errores del contrato en TODOS los caminos —incl. malformados y 500—, ETag/304, cursor keyset opaco con guard de no-finitos, DTOs formales en OpenAPI; auditoría Opus: 8 hallazgos corregidos) | ✅ verificado: ownership por tenant (cross-tenant 404 IDÉNTICO al ausente; corpus vacancies GLOBAL); contract tests negativos cross-consumer (401 uniforme por cualquier causa, 403 con required_scope, 404); DTOs §2 (vacante multi-listing solo ACTIVAS, match completo, perfil con current_revision null); 197/197 ×3 |
| A-10 ✅ | **Entrega `match.evaluated` outbox→inbox POR consumidor** — **HECHO 2026-07-24** (emisión misma-tx con uuid5 por eval_key + `delivery.py`/tarea `jobhunt.delivery.dispatch_outbox`: SKIP LOCKED + lease con FENCING, backoff, transporte inyectable — el real llega en Fase C; core0005 alinea destination al ancho del consumer; auditoría Opus: 5 hallazgos corregidos) | ✅ verificado: at-least-once (lease caducado se re-reclama; re-entrega con ack perdido) e idempotente por (consumer_id, event_id) en el inbox del consumidor; event_id determinista (re-evaluar no re-emite); dead-letter con ALERTA al agotar intentos (y dead no se reintenta); routing por consumidor correcto |
| A-11 ✅ | **`harvest_runs`/`source_harvest_runs` idempotentes** — **HECHO 2026-07-24** (`runs.py`: id determinista uuid5 por run_key + ON CONFLICT re-abre; claim por scope ATÓMICO con lease 900s — error/colgado se re-arma, running vigente se salta; finish_run agrega solo habilitados, cierra deshabilitados como 'skipped' y deja el run abierto si otro worker sigue en vuelo; tarea `jobhunt.harvest.run_all`; 3 pasadas Opus: 5+1+0 confirmados corregidos) | ✅ verificado: reintento no duplica (mismo run_id, scopes hechos se saltan, errores/colgados se re-ejecutan; claim concurrente con 1 solo ganador) |
| A-12 ✅ | **Ensayo de migración sobre copia** — **HECHO 2026-07-24** (BD desechable + datos vía servicios reales en 9 tablas; frontera de core0005 ejercitada: destination 61 chars ⇒ downgrade falla CONTROLADO sin truncar; downgrade escalonado hasta base y vuelta a head con verificación de versión, smoke e índices; 2 pasadas Opus: 2+0 confirmados) | ✅ verificado: down-migrations válidas en todo el chain core0007→base→head (core0007 —índice parcial `ix_incarnation_vacancy_active` de encarnaciones por vacante— incorporada al ciclo en la auditoría final del tramo A-09→GATE A, `b14868e`) |

> **A.SEAM** (costuras por-capacidad + `jobhunt_routing` en cada BFF) es un ticket **del backlog**
> (track de BFF), NO del núcleo — no está en A-01..A-12 por diseño; se ejecuta en paralelo.

🚦 **GATE A** (tras ratificar GATE-CC) — **ENSAYO SUPERADO 2026-07-24** (`test_gate_a.py`, cada
criterio con assert explícito; 3 pasadas Opus: 5+2+0 confirmados corregidos):
✅ A.MIN extremo a extremo (cosecha run_all real con HTTP mockeado → raw → identidad → canónica →
embedding → evaluación → feed → entrega) · ✅ ingesta por scopes cubre tech+no-tech · ✅ eval con
descartes estables (dismiss sobrevive a re-evaluación con estado intacto) · ✅ re-enlace no colapsa
ni **corrompe** identidad (attach BILATERAL 2 fuentes/1 vacante; reciclado abre vacante nueva,
primary reasignado a activa y canónica reconstruida desde el nuevo primary con título
DISCRIMINANTE) · ✅ feed filtra activa + tenant (archivada fuera; cross-tenant 404) · ✅ entrega
at-least-once idempotente (re-entrega DEMOSTRADA — transporte re-invocado — e inbox deduplica) ·
✅ core aislado (broker redis-core, colas core.*).

---

## 5. Pendiente (Fase B / decisiones de infra)
Umbrales `SIM_*`, resolución activa de `dedup_candidates`, nivel-3 semántico, métricas de sombra,
RPO/RTO, outbox lag. **Infra DECIDIDA:** Redis dedicado (`redis-core`, ADR-08) + CDC por replication
slot (§15bis). **Pendiente:** recalcular la estimación con ambos.
