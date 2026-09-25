# Plan de unificación — `jobhunt-core` (servicio independiente)

> **Estado prevalente 2026-09-21 — ESTADO §43:** punto 4 abierto y trabajo
> funcional pausado; autorización actual sólo documental y commits locales.
> Perfiles y feedback ya transferidos; diez búsquedas SwissJob ejecutan en core
> y WorkingNomads tiene el primer corte nativo confirmado. Última evidencia
> operativa: `3d5d67a/core0050`, 21-09 16:53 UTC; no re-sondeada en esta edición.
> No repetir esos cortes. Restan fuentes/lectores de ambos proyectos, avisos
> Portfolio, producción escolar, autonomía sin captura CDC y retirada final.
> [Plan mínimo y estimaciones revisables](SwissJob/docs/PENDIENTES_PUNTO4_REVISION_EXTERNA_2026-09-21.md)
> y [runbook](SwissJob/docs/RUNBOOK_RETIRADA_PRODUCTORES_PUNTO4.md).
> Las horas iniciales se estimaron sin inventario completo; no hay nueva fecha
> garantizada. Primero conciliar activos y reutilización, después fijar alcance.
> Punto 5, cron/alarma, aceptación integral y calidad son hitos distintos.
> Esta actualización no modifica contratos, arquitectura ni criterios de calidad.

> **Checkpoint histórico 2026-09-14 — E.15 / ESTADO §41:** punto 3 (colegios)
> cerrado y desplegado, como punto 2 documental en E.14. Ambos BFF resuelven
> `schools=core_primary`, con escritor único del estado escolar en core.
> Productores conservados y adaptados: configuración por consumer, preferencias/
> candidaturas por perfil, observaciones nuevas al corpus; historia enlazada o
> en cuarentena explícita, nunca convertida en vacantes inventadas.
> Restore, importación/replay, rollback y canarios vivos comprobados.
> El corte de autoridad del estado NO equivale a retirar productores (punto 4)
> ni a certificar rendimiento global (punto 5) o calidad del ranker.
> Cron de retención sigue diferido por el propietario; incluir registro E.15.
> Acta vigente: [E.15](SwissJob/docs/DESPLIEGUE_E15_2026-09-14.md).
> Las marcas anteriores del cuerpo se conservan como historial, no como lista
> de pendientes para volver a ejecutar.

> **v3.1.2 — topología ratificada + runbook de cutover. Arquitectura APROBADA; NO es
> rediseño conceptual. Tras 6 rondas de supervisión (3 agentes + análisis externos, todo
> verificado en código). Ver §16/§20.**
> **Núcleo = servicio desplegable INDEPENDIENTE** (`jobhunt-core`, reutiliza el código del
> motor de SwissJob como paquete), **esquema Postgres propio**, **instancia Postgres/Redis
> compartida con aislamiento estricto**. **Ambas apps (SwissJob y Portfolio) = BFF del core.**
> Autor: Fable. Fecha: 2026-07-21.
>
> **Estado operativo actualizado 2026-09-04:** la arquitectura permanece vigente, pero las marcas
> de progreso históricas del cuerpo no son el estado de ejecución. Fase C está consumada en
> producción; el camino actual es cierre de dos vallas P1 de publicación → desarrollo/benchmark →
> holdout → racha 7/7 → Fase D → E → F. **Actualización 2026-09-06: ese orden se INVIRTIÓ por
> decisión ratificada — Fase D se ejecutó ANTES del examen (D no depende de la calidad del
> ranker) y está CONSUMADA (`SWISSJOB SOBRE CORE`); el examen único se ejecutó y dio NO-GO,
> con el holdout ya consumido, así que la racha 7/7 no se abre. Quedan E y F.**
> Fuente de estado: `ESTADO_Y_HOJA_DE_RUTA.md` §33;
> procedimiento: `SwissJob/PROMPT_CIERRE_TOTAL_UNIFICACION_2026-09-04.md`.
>
> **Actualización local 2026-09-08:** E.1 almacén/API, E.2 adaptadores y E.3 alta
> atómica CV+carta implementados, sin activar documentos core en el NAS. No equivale
> a E completa: faltan orquestación recuperable, PDF/inbox, migración/rollback y
> corte; colegios y F mantienen sus gates. Contrato/evidencia:
> `SwissJob/docs/FASE_E_DOCUMENTOS_2026-09-08.md`. El resultado histórico de matching
> 0.5856/0.7438 no es certificable; no se inicia una racha ni se modifica el umbral.
>
> **E.4, continuación local:** caché coherente y generación sin transacción durante
> LLM corregidas y verificadas. **E.5:** decisión ratificada de CONSERVAR CV/carta
> hasta borrado explícito/retención; referencia opaca + snapshot + binding de dueño
> implementados. Biblioteca de todas las versiones independiente del Kanban.
> Backend be5a1f1 / frontend 8836254, comprobaciones PG y suites completas verdes.
> Todo LOCAL, sin despliegue NAS: no sustituye operación recuperable, contexto de
> adaptadores, inbox, migrador/rollback ni canary. Evidencia y siguiente secuencia:
> `SwissJob/docs/AVANCE_E5_2026-09-08.md`.
>
> **E.6 local:** snapshot transportado y journal durable ensayado ante ACK perdido
> y reinicio real, aún sin activación de generador/UI/scheduler. Restore NAS→local
> bloqueado por permiso específico de copia privada; no saltar ese ensayo para
> desplegar. Secuencia completa y alcance exacto:
> `SwissJob/docs/AVANCE_E6_2026-09-08.md`.
>
> **Actualización operativa 2026-09-08 noche:** copia privada autorizada y restore
> ensayado; Portfolio **ddb6651 / rr11s0042u18** desplegado con retención, frontend
> **8836254** publicado y Cloudflare success. Túnel reparado. No se activó escritor
> documental core ni se retiraron schedulers legacy: E/F siguen abiertos.
> `SwissJob/docs/DESPLIEGUE_PORTFOLIO_E5_E6_2026-09-08.md`.

>
> **Prevalece E.7, 2026-09-08 noche:** Portfolio 2eb5f31 desplegado. Journal,
> generación/retry y autoridad lectura/PDF/retención integrados; writer documental
> aún local hasta migración/corte. El matching visible eludía core en background;
> corregido y canary NAS de 50 ofertas iguales. **09-09: frontend 3f6d7be PUBLICADO**
> con autorización expresa; CI y Cloudflare success. Publicación y permiso de restore
> ya están resueltos. ESTADO §33 y
> `SwissJob/docs/AVANCE_E7_Y_MATCHING_OPERATIVO_2026-09-08.md` son la referencia vigente.
> **E.9 local (09-09):** exportación documental por autoridad, freeze de borrado
> del propietario y recuperación del recibo ya eliminado. No cierra export/erase
> integral, inbox, migración histórica ni flip. Evidencia:
> `SwissJob/docs/AVANCE_E9_EXPORTACION_Y_RECUPERACION_2026-09-09.md`.
> **E.8 local:** documentos SwissJob conectados (journal/biblioteca/freeze),
> todavía sin corte NAS. Acta: SwissJob/docs/AVANCE_E8_DOCUMENTOS_Y_FUENTES_2026-09-09.md.
> Orden: completar inbox/export/erase de documentos →
> migración+rollback no vacío → corte documental → colegios → retiro F por fuente
> → rendimiento representativo/cierre. No reabrir tareas ya desplegadas ni
> equiparar este avance al GO de calidad.

---

## 0. Resumen ejecutivo

Unificar el job-hunting construyendo **`jobhunt-core` como servicio independiente** (API
`/v1` + worker), que **reutiliza el código del motor de SwissJob** pero con esquema propio y
aislamiento operativo. **SwissJob y ReactPortfolio se convierten ambos en BFF** que cablean
al core. Integridad primero: vacante canónica ↔ listings por fuente **con revisiones raw
persistidas antes de normalizar**, contenido/embeddings/traducciones **versionados por
modelo**, evaluaciones **append-only** con estado de usuario estable, **cursores por scope**,
**outbox/inbox**, y **merge/split con transferencia de estado**.

**Pre-fase de saneamiento** (5 P0 de SwissJob). **Ejecución sin caídas:** el core se
construye y prueba **en paralelo** (sombra) sin que ninguna app dependa de él; el corte de
cada app es **backfill + sync ANTES del flip**, con un **escritor definido en cada estado**,
routing gradual y **apagado explícito de los schedulers legacy** (§15bis).

**Infra:** sin nuevo Postgres (misma instancia, esquemas aislados) + **un Redis DEDICADO para
broker/locks del core** (`redis-core`, ADR-08); **sí un
contenedor API del core + su worker + cola dedicada + red privada** (+ render-worker opcional).

---

## 1. Principios

1. Un scraping, un corpus, un cómputo de embeddings local.
2. **Integridad primero:** raw persistido antes de normalizar; revisiones de listing
   obligatorias; embeddings/traducciones por modelo; evaluaciones append-only; estado de
   usuario estable; merges reversibles; archivar no borrar; constraints explícitos.
3. **Ámbito de cosecha con cursor por scope:** qué se descarga ≠ qué se puntúa.
4. Estado de usuario (feedback/dismissed/saved) no se pierde ni se duplica.
5. Sin degradar el LLM. 6. Coste honesto (rerank crece por perfil). 7. **Cutover sin pérdida:
   backfill+sync antes del flip, un escritor por estado, routing gradual, schedulers legacy
   apagados.** 8. Redis = caché; traducciones/veredictos pagados se persisten en BD.

---

## 2. Diagnóstico y P0 (verificado en código)

Bugs de SwissJob a sanear: upsert no versiona contenido (`job_repository.py:32-60`); **cascada
destructiva en TRES tablas** (`job_applications.py:31`, `match_result.py:34`,
`generated_document.py:29`) + `cleanup` a 180d; doble dedup (`embedding_tasks.py:188-191` +
`pipeline_tasks.py:42-43`); carga múltiple del modelo; dedup fuzzy inseguro (`str.replace` de
seniority, `deduplicator.py:81-82`); ingesta sesgada (`fetch_tasks.py:173`
`("", "Switzerland")` + anti-tech). Además: ambas apps arrancan **schedulers en su lifespan**
(portfolio `main.py:299,318`) y el **worker de SwissJob escucha `default,scraping,ai`**
(`docker-compose.prod.yml:126`) → riesgo de que un worker legacy consuma tareas del core.
Correcciones factuales: colegios sembrados (6 inactivos); SSE fetch+Bearer; colegios = 17
instituciones + 18 monitores; `ApplicationStatus.saved` primer estado (`enums.py:49`).

**Añadido 2026-08-19 (quinta revisión externa de la Fase 3 / TRACK V-DIFERIDO) — defecto de
IDENTIDAD, ausente del diagnóstico original y el más grave del legacy:** la PK de `jobs` es
`MD5(title|company|url)` (`models/job.py:18`; cálculo en `job_service.py:65`), `url` tiene índice
ÚNICO (`models/job.py:30-31`) y el upsert resuelve conflictos POR HASH (`job_repository.py:359`,
`on_conflict_do_update(index_elements=["hash"])`) ⇒ una corrección LEGÍTIMA de título o empresa
por el portal cambia el hash, el `ON CONFLICT` no encuentra la fila, el INSERT choca con el índice
único de URL (aborta solo su savepoint por-oferta), la oferta deja de refrescar `last_seen_at` y
`cleanup_stale_jobs` la borra/archiva a los 60 d (`maintenance_tasks.py:196`). **La identidad se
construye con campos MUTABLES**: pérdida de datos activa por diseño — y explica retroactivamente
las dos rondas que la Fase 3 dedicó a estabilizar la identidad en `financejobs`/`gastrojob`
(`0e6b821`): eran síntomas. Latente ya en `swiss_schools_hautlac`/`_iscs` (URL de listado
CONSTANTE para todas sus ofertas: `swiss_schools_hautlac.py:74`, `swiss_schools_iscs.py:89` ⇒
con el índice único solo
persiste UNA por colegio). NO se corrige en el legacy (rediseño + migración: `hash` tiene FKs
aguas abajo — las TRES tablas de arriba): tickets VD.12/VD.14 (backlog TRACK V-DIFERIDO) y
`DEUDA_TECNICA.md` §1.23/§1.25. **El modelo del core lo resuelve por diseño — nota en §6.**

---

## 3. Arquitectura — `jobhunt-core` servicio independiente (aprobada)

`jobhunt-core` = **servicio desplegable independiente** (API `/v1` + worker Celery), reutiliza
el código del motor de SwissJob, **esquema Postgres propio**. **Ambas apps son BFF del core:**
- **SwissJob backend** = auth + gestión de usuarios + su UI-facing API + **BFF de job-hunting**.
- **ReactPortfolio backend** = portfolio/chat/analytics/`cv_profiles` de contenido + **BFF**.

Auth service-to-service (consumer-key mín. privilegio, red interna, **no** por ngrok).
**Comparte la instancia de Postgres/Redis con AISLAMIENTO estricto** (§15bis): colas Celery
propias (`core.harvest/embedding/matching/notifications`), prefijos Redis propios,
**leader-lock separado** core vs legacy, **rol de BD sin privilegios sobre tablas/columnas/
secuencias/funciones SECURITY DEFINER legacy** (acceso solo a los objetos pgvector de `public`;
verificado exhaustivamente en cada migrate) + `search_path` explícito, **Alembic + migration job
propios**. Infra nueva: **contenedor API
del core + su worker** + cola + red privada (+ render-worker opcional); **no** un 3.er
Postgres (misma instancia, esquemas aislados) + **un Redis DEDICADO para broker/locks** (ADR-08).

---

## 4. Multi-inquilino, seguridad y ciclo de vida de datos

- `consumer_credentials(key_id, hash, scopes, expires_at, revoked_at)` — rotación sin corte.
- `idempotency_records(consumer_id, key, route, request_hash, response, expires_at)` — HTTP,
  distinto del integration-inbox.
- Núcleo no enrutable por ngrok; SSE `fetch`+Bearer scoped a (consumer, perfil).
- **GDPR:** erase por perfil cubre `profile_revisions`, `match_evaluations`,
  `profile_vacancy_state`, `profile_vacancy_events`, `applications`, `generated_documents`,
  `notification_outbox` del perfil. **NO** `offer_translations`/`offer_embeddings` (oferta
  compartida). **Backups:** expiración + restore controlado + **crypto-shredding**. RPO/RTO.

---

## 5. Componentes del núcleo (contratos en §6)

- **5.1 Ingesta:** `harvest_scopes` + **cursor/estado POR SCOPE** (`source_scope_state`); fuera
  los filtros de perfil de la ingesta. **Orden: fetch → persistir listing + revisión raw →
  normalizar → identidad (exact/fuzzy con confidence) → revisión canónica → embedding → dedup
  semántico → matching → outbox → commit del cursor.** Dedup por **tokens** (PF.5) + `dedup_candidates`.
  `merge_log` con evidencia + política de transferencia. Compliance por fuente (`is_restricted`).
  **ALCANCE TEMPORAL (ADR-10, decidido 2026-08-06): la cosecha inicial de una fuente baja SOLO lo
  publicado en la SEMANA EN CURSO** y a partir de ahí incremental; **excepción: `swiss_schools_*`
  → en el BOOTSTRAP todas las activas, sin ventana; después incremental como el resto**.
  NO es "todo lo activo" ni "todo lo de 180 días" (el corpus real del NAS medido el 2026-08-06
  tenía 66 días y un 43 % sin re-ver en 30 d con `is_active=true`).
  ⚠ Exige `published_at` en el modelo normalizado: HOY no existe (`jobs` solo tiene `first_seen_at`/
  `last_seen_at`, que NO sirve de sustituto) — ver ADR-10 para el bloqueante y la política por fuente.
- **5.2 Matching:** `offer_embeddings`/`profile_embeddings` por `model_id` + `embedding_models`
  (dimensión por modelo). `match_evaluations` append-only `UNIQUE(profile,vacancy,eval_key)`;
  estado en `profile_vacancy_state` + `profile_vacancy_events`. Caché rerank por `eval_key`.
  Prompts como políticas versionadas.
- **5.3 Traducción:** `offer_translations` persistente con `field(title|description)` + langs.
- **5.4 Documentos:** JSON→WeasyPrint; `generated_documents` con profile/offer_revision +
  versiones + async_state + output_hash + pdf_location; FK SET NULL. CV: fuente autoritativa + ETag.
- **5.5 Seguimiento:** **saved = bookmark en `profile_vacancy_state`; candidatura nace en
  `applied`**. `applications` con `source_listing_incarnation_id` + snapshot + `application_status_events`.
  `school_applications` = máquina separada. Merge/split con transferencia de estado.
- **5.6 Archivar no borrar.** **5.7 API multi-listing** (`primary_listing`+`listings[]`).
- **5.8 Entrega:** `notification_outbox` (marca `sent` tras confirmar) · `integration_outbox`
  (event_id determinista) + `integration_outbox_deliveries` (estado/reintento POR destino) ·
  `integration_inbox` **en la BD de cada BFF** (no en el core). **Un solo escritor por estado**
  (§15bis). *(Esquema autoritativo: `CONTRATOS_FASE_A.md` §1.)*
- **5.9 Colegios:** `schools`(17)+`school_monitors`(18)+**`school_job_details`→`vacancy_id`**
  (extensión, no corpus paralelo)+`school_applications`.

---

## 6. Modelo de datos — RESUMEN (esquema autoritativo: `CONTRATOS_FASE_A.md` §1)

Identidad/auth: `consumers` · `consumer_credentials` · `idempotency_records` · `profiles` ·
`profile_revisions`(sin embedding) · `profile_embeddings`(por model_id).
Cosecha: `sources`(+compliance{rate_limit,robots_ok,is_restricted,authorized_route}) ·
`harvest_scopes` · `source_scope_state`(scope_id, cursor, last_complete_at, consecutive_failures).
Corpus: `vacancies` · `source_listings`(slot estable: UNIQUE source+external_id / source+url_normalized) ·
`source_listing_incarnations`(binding slot→vacante en el tiempo; url/apply/timestamps; el reciclado
abre otra) · `source_listing_revisions`(cuelgan de la **incarnación**; UNIQUE incarnation+content_hash; **obligatoria**) ·
`offer_revisions`(UNIQUE vacancy_id+content_hash; sin vector) · `offer_revision_sources` ·
`offer_embeddings`+`embedding_models`(dimensión por modelo) · `offer_translations`(field:title|
description, source/target lang, input_hash, state) · `merge_log`(transfer_policy) · `dedup_candidates`.
Matching/estado: `match_evaluations`(UNIQUE profile+vacancy+eval_key, append-only, sin feedback) ·
`profile_vacancy_state`(feedback/dismissed/saved/current_eval_id) · `profile_vacancy_events` ·
`applications`(vacancy_id + source_listing_incarnation_id nullable, snapshot, status[start=applied]) ·
`application_status_events` · `generated_documents`(profile/offer_revision, versiones, async_state,
output_hash, pdf_location) · `saved_searches` · `job_filters`+`pattern_suggestions`.
Colegios: `schools`+`school_monitors`+`school_job_details`+`school_applications`.
Orquestación/entrega: `harvest_runs` · `source_harvest_runs`(UNIQUE run+scope) ·
`notification_outbox` · `integration_outbox` · `integration_inbox`.
`offer_identity_map` para migración. Redis = caché/coordinación (con prefijos propios del core).

**Nota (2026-08-19) — el defecto de identidad del legacy (§2, VD.12) desaparece AQUÍ por diseño
(verificado en código):** la identidad es el slot estable (`source_listings`, UNIQUE
source+external_id / source+url_normalized) y los cambios de contenido son REVISIONES versionadas
por hash (`source_listing_revisions`, `offer_revisions`) — título/empresa NO participan en la
identidad: en `jobhunt_core/harvest/` la empresa solo alimenta el guard DELIBERADO de reciclado
(`identity.py:83-92`; título/empresa difusos → `dedup_candidates`, jamás merge automático).
Corrección de título ⇒ nueva revisión en el MISMO slot; cambio de empresa ⇒ reciclado: incarnación
cerrada + vacante NUEVA con historial intacto — split visible, nunca pérdida silenciosa. Argumento
material a favor de la migración: el defecto se EXTINGUE al pasar la cosecha al core, sin
rediseñar el legacy. Tres matices verificados que acotan la afirmación: (1) mientras el legacy siga
de cosechador, la proyección sombra usa su `hash` como `external_id` (`shadow/projector.py:734`) —
hereda la identidad mutable y no puede recuperar lo que el legacy nunca persistió; la migración de
datos por sí sola NO extingue el defecto. (2, sexta revisión) la cosecha nativa TAMPOCO lo extingue
por sí sola: la garantía la da el `external_id` que elige CADA adaptador, y el único nativo que
existe hoy usa `slug or url` (`harvest/providers/arbeitnow.py:221`) con el título dentro del slug —
misma clase de identidad mutable que VD.12, ahora en el core. El modelo del core es condición
NECESARIA pero no suficiente: hace falta además que cada adaptador demuestre identidad estable
(ticket VD.15). (3) VD.14 NO desaparece: una fuente con URL de listado constante también
choca aquí (UNIQUE source+url_normalized ⇒ el segundo listing se SALTA — con warning,
visible, no en silencio: `harvest/sink.py:337-345`); la URL por-oferta hay que arreglarla en la fuente igualmente.

---

## 7. Stack y eficiencia
Stack de SwissJob reusado como paquete del core. Cola dedicada + no preload → 1 carga. ONNX
fuera del corte (aditivo por embeddings/modelo). render-worker Playwright. Rerank por `eval_key`.

## 8. API `/v1`
OpenAPI versionada, idempotency-key (`idempotency_records`), versionado optimista, cursor,
contract tests, `consumer_credentials`+scopes, SSE fetch+Bearer. DTO multi-listing con todos
los campos. Matches = `match_evaluations` vigente + `profile_vacancy_state`. **Contratos/DTOs
completos en `CONTRATOS_FASE_A.md` (CC.2, hecho + revisado).**

## 9. Qué mantiene cada app — ambas son BFF
**SwissJob** = auth + gestión de usuarios + su UI + **BFF de job-hunting** (su motor se retira,
sus routers de empleo pasan a proxy al core). **ReactPortfolio** = portfolio/chat/analytics/
`cv_profiles` + **BFF**; empuja su CV con `external_revision`/ETag. Cada app conserva su BD propia
(auth / visitas-chat); el **corpus, perfiles de matching, evaluaciones, estado, candidaturas,
documentos y colegios los posee el core**.

## 10. Scraping por tiers
Tier 0 API/RSS · Tier 1 SSR embebido · Tier 2 SPA→render-worker Playwright · Tier 3 off.
Los `is_restricted` **no se habilitan al unir registros**.

---

## 11. Coste, infra y esfuerzo (recalculado)

- Ahorro: una cosecha + un cómputo de embeddings (CPU, no API) + traducción/dedup compartidos.
  Rerank no baja; cuerpo = coste nuevo.
- **Infra:** sin nuevo Postgres (misma instancia, esquemas aislados) + **Redis DEDICADO broker/locks** (`redis-core`, ADR-08); **sí un contenedor
  API del core + su worker + cola dedicada + red privada** (+ render-worker opcional).
- **Esfuerzo (recalculado tras la estrategia de ejecución):** el runbook de cutover añade
  trabajo **material** no contemplado antes — extracción/despliegue del servicio, costuras y
  **BFF en AMBOS backends**, routing/canary, **outboxes de captura legacy**, namespacing
  Celery/Redis, Alembic propio, **apagado de schedulers**, **sync incremental + read-model de
  fallback**, manifiesto de datos por tabla. **Fuente única de estimación: el backlog**
  (≈153–229 días/persona; broker dedicado (ADR-08 `redis-core`) y CDC por replication slot ya
  DECIDIDOS — ver ADRs v4; recalcular al planificar Fase B con la Fase A real como calibración). Con
  20–30% de contingencia ≈ **~10–15 meses** (1 dev). Un equipo
  pequeño **no baja de ~6–8 meses**: B/C/D tienen **gates secuenciales + ventanas de observación
  que no se paralelizan**.
  *(Trayectoria honesta: 3–4 → 6–9 → 7–10 → 10–15 meses conforme afloró el rigor de integridad y
  de cutover sin caídas. Es una migración de plataforma, no una feature.)*

---

## 12. Secuencia

- **Pre-fase (5 fixes):** PF.1 upsert versiona · PF.2 un despacho de dedup · **PF.3 archivar/FK
  en las TRES tablas** · PF.4 carga única · PF.5 dedup por tokens + `dedup_candidates`.
- **Cierre de contratos (RATIFICADO 2026-07-22 — GATE-CC CERRADO):** ADRs en `ADR_JOBHUNTING.md`
  (CC.1) + contratos/DTOs/manifiesto/tickets en `CONTRATOS_FASE_A.md` (CC.2), revisados en 3 rondas
  (37 + 12 + 10 hallazgos aplicados). **Fase A COMPLETA 2026-07-24** (A-01..A-12 + ensayo GATE A
  superado + auditoría final + refactorización auditada, ver §24).
- **Fase A — Servicio aislado + esquema aditivo + VERTICAL MÍNIMA** (1 provider, 2 scopes, 2
  perfiles, listing revision, embedding, evaluación/estado, API read-only, una entrega outbox).
- **Fase B — Sombra** con set etiquetado (no el sistema viejo como oráculo); métricas
  precision/recall dedup, nDCG@K, overlap@K, falsos negativos, coste, latencia, outbox lag,
  cero pérdida. Fijar antes: nº ciclos, umbrales, RPO/RTO, outbox lag máx., % re-enlace, rollback.
- **Fase C — Portfolio (piloto): cutover por el runbook de §15bis.**
- **Fase D — SwissJob: mismo runbook con canary POR PERFIL.** E — colegios/docs. F — retirar
  (tras retención + backup probado + N ciclos limpios).
- Migraciones: down-migrations **solo antes del cutover**; después **expand/contract + replay**.

---

## 13. Riesgos
Integridad (P0 saneados; raw primero; merges reversibles) · cutover (backfill+sync antes del
flip; un escritor por estado; expand/contract) · **schedulers legacy activos tras el flip →
doble cosecha/email** (flags separados + gate) · **aislamiento de colas/Redis** (worker legacy
podría consumir tareas del core) · **fallback = solo continuidad de LECTURA** (write-continuity
requiere pending_sync local) · seguridad (ngrok, credenciales rotables, GDPR/backups/crypto-shred)
· esfuerzo **10–15 meses** · ONNX fuera del corte.

## 14. Requisitos → cobertura
(ver §5/§6; añade: continuidad de lectura garantizada, escritura vía un solo escritor.)

## 15. Decisiones — **en `ADR_JOBHUNTING.md` (CC.1 v4, RATIFICADO 2026-07-22)**
Estado (saved=bookmark, decidido) · merge/split (política de transferencia) · cursor por scope
(decidido) · retención/GDPR (expiración/restore/crypto-shred) · vacancies (identidad-only vs
proyección) · `vector` por `embedding_models` · reutilización de código (**DECIDIDO §21:**
paquete-en-repo de SwissJob + fronteras estrictas) · umbral de re-enlace · métricas/umbrales de
sombra · alcance de continuidad de escritura (read-only vs pending_sync). *(Topología standalone-core
decidida; repo → §21; modelos de ejecución → §22.)*

---

## 15bis. Estrategia de ejecución — Strangler Fig con cutover controlado

**Topología (ratificada, §3):** `jobhunt-core` servicio independiente + esquema propio +
instancia Postgres compartida (esquema propio) + **Redis DEDICADO para broker/locks** (`redis-core`, ADR-08); ambas apps BFF.

### Aislamiento operativo (obligatorio, incluso para el portfolio)
- Colas Celery propias `core.harvest/embedding/matching/notifications` (el worker legacy de
  SwissJob escucha `default,scraping,ai` → **NO** debe consumir las del core).
- **Redis: los prefijos NO aíslan** — el Redis actual usa `allkeys-lru` (`docker-compose.prod.yml:58`),
  que bajo presión de memoria puede **expulsar mensajes Celery, leader-locks o claves de
  coordinación** aunque tengan otro prefijo. **DECIDIDO (ADR-08): un Redis DEDICADO (`redis-core`,
  `noeviction`) para broker/locks del core**, separado del Redis de caché. (Namespaces evitan
  colisiones, NO aíslan memoria/CPU/eviction.)
  Leader-lock distinto core vs legacy. Rol de BD limitado al esquema del core + `search_path`.
  **Alembic independiente** + **migration job propio** (el core NO se cuelga del entrypoint de
  SwissJob). Pools separados.

### Costura primero (con todo en `local`)
- Subinterfaces **POR CAPACIDAD** (catálogo, matching, perfiles, candidaturas, documentos,
  colegios) — no una fachada única `JobHunting`. Implementación `local` (motor actual) y `core`
  (cliente `/v1`), validadas con **contract tests** (comportamiento idéntico).
- Control **separado por dimensión**, en una **tabla de routing en cada BFF** (NO una env var:
  la config por perfil+capacidad debe ser dinámica, transaccional, auditable **y local al BFF**,
  para poder enrutar aunque el core esté caído):
  `jobhunt_routing(consumer_id, profile_id, capability, mode[local|shadow|core_read|core_primary|
  rollback_pending], revision, updated_by, updated_at)`. Además `ENGINE/SCHEDULER/NOTIFICATIONS`
  por perfil.
- **Schedulers conscientes del routing (canary multiusuario):** en SwissJob NO se apaga el
  scheduler global al migrar el primer perfil; matching/digest/notificaciones **consultan el
  routing y omiten los perfiles en `core_read/core_primary`**. El scheduler global se desactiva
  solo cuando el ÚLTIMO perfil sale de `local`. Las **notificaciones nunca se emiten desde ambos
  motores para el mismo perfil**.

### Matriz de escritor por estado
| Estado | Escritor autoritativo | Outbox |
|---|---|---|
| Local | backend viejo | ninguno |
| Shadow | backend viejo | legacy → core (captura de deltas) |
| Cutover | freeze breve | drenaje + reconciliación |
| Core primary | core | core → read-model legacy (si se mantiene) |
| Rollback | core hasta replay final | core → legacy antes de reactivar escrituras |

- Escrituras interactivas del BFF = **síncronas contra el escritor activo + idempotency key**.
  El outbox replica **eventos**, no convierte los comandos de usuario en asíncronos.

### Runbook de cutover (captura ANTES del snapshot; schedulers detenidos ANTES del flip)
```
1. activar el outbox de captura legacy + fijar WATERMARK (LSN)   ← ANTES del snapshot
2. snapshot / bulk backfill CONSISTENTE (motor viejo aún de escritor) — ensayado sobre copia
3. replay de eventos posteriores al watermark → alcanzar sincronización
4. reconciliar recuentos/checksums
5. canary de LECTURAS (subconjunto de perfiles lee del core)
6. freeze breve + IMPEDIR nuevos jobs legacy + esperar/detener los en ejecución
   (QUIESCE de schedulers/engine legacy — ANTES del flip, no después)
7. drenar outbox + reconciliar delta final
8. flip de lecturas Y escrituras → core_primary
9. verificar: schedulers legacy siguen apagados (sin doble cosecha/email) y escrituras OK
```

### Mecanismo de captura (CDC) — cómo se vincula el outbox legacy al snapshot
El "outbox legacy + watermark" exige un **corte determinista**. **Decisión: CDC por replication slot
de Postgres** (crear el slot → `pg_export_snapshot` consistente con el LSN del slot → reproducir desde
el slot los cambios con LSN posterior). La alternativa "outbox + secuencia monotónica" se **descarta**:
el `seq` se asigna ANTES del commit, así que una Tx con `seq` menor puede commitear DESPUÉS del
snapshot y el replay `>seq_snapshot` la perdería (orden de commit ≠ orden de seq). Documentar:
frontera exacta (snapshot↔LSN), jobs en vuelo, replay idempotente y rollback.

### Manifiesto de datos por tabla (ambas apps): `migrar | recomputar | conservar local | archivar | eliminar`
- **Portfolio (campo-a-campo):** JobApplication tiene `title/company/url/source/description/status
  {saved,applied,phone_screen,technical,offer,rejected}/notes/follow_up_date` (**NO** `applied_at/
  applied_url` — esos son de **SwissJob**). Mapeo: `status=saved`→`profile_vacancy_state.saved_at`
  (bookmark, NO `applications`; **refinado en C-4 v1.1**: un `saved` CON
  `follow_up_date` migra ADEMÁS como `application` para no perder el dato);
  `status≠saved`→`applications` (status mapeado; `phone_screen/
  technical/offer/rejected`→`application_status` equivalente + **evento inicial sintetizado** en
  `application_status_events`); `title/company/url/description`→snapshot; **`notes`/`follow_up_date`→
  columnas**; identidades no enlazables→staging/reconciliación.
  saved_searches→migrar · generated_documents→**conservar local → Fase E** (v1.1 Fase C: el core no tiene la tabla; adelantarla no es imprescindible) ·
  schools/…→**conservar local → Fase E** (la designación de maestra única se decide EN Fase E) · cv_profiles→conservar local + push · seen_jobs→recomputar ·
  notificaciones/matching→recomputar.
- **SwissJob:** UserProfile→migrar · match_results→migrar · feedback→`profile_vacancy_state` ·
  job_applications→migrar · generated_documents→migrar · saved_searches/filters/suggestions→migrar
  · **notifications→DECIDIR (migrar/archivar; tienen historial + `is_read`, NO son efímeras
  — `notification.py`)** · cursores→recomputar (por scope) · compliance→migrar.

### Continuidad y escala
- **Fallback read-only = continuidad de LECTURA** (durante caída del core se ven datos, no se
  guarda/aplica/genera). **En el primer cutover se implementa SOLO read-only.** La continuidad
  de ESCRITURA (`pending_sync` local: cifrado PII, orden de comandos, expiración, conflictos, UX
  de errores) queda **diferida** — se añade solo si la experiencia real demuestra que hace falta.
- **Portfolio** (1 perfil, corpus efímero): runbook ligero (flag por capacidad + read-only +
  backfill de lo durable), **pero apagado de schedulers + aislamiento de colas OBLIGATORIOS**.
- **SwissJob** (multiusuario, datos durables): runbook completo + **canary por perfil**.

### Reutilización de código
- Core como **paquete + app desplegable dentro del repo de SwissJob**, con **API y esquema como
  fronteras estrictas**; congelar el motor legacy; fixes críticos al paquete compartido; SwissJob
  deja de importar sus servicios locales al pasar a BFF. (ADR §15.)

### Retirada
- Borrar motores/tablas solo tras retención acordada + backup probado + N ciclos sin divergencia.

---

## 16. Supervisión (6 rondas)
R1 (3 agentes): topología/coste/ONNX/seguridad. R2: integridad (vacancy/listings, versionado,
cascada, doble dedup, outbox). R3: ámbito de cosecha, revisiones de listing, embeddings por
modelo, eval append-only + estado, integration outbox/inbox, PF.5, DTO multi-listing,
traducciones persistentes, ciclo de vida. R4: cierre contractual (§19). R5: estrategia de
ejecución — topología no ratificada, orden backfill/flip, procesos en background, aislamiento
operativo, manifiesto de datos, matriz de escritor (§20). **R6 (esta): correcciones finales del
runbook — captura ANTES del snapshot, quiesce ANTES del flip, routing como TABLA en el BFF, Redis
broker aislado de la eviction (`allkeys-lru`), notificaciones no recomputables, fallback read-only
primero, estimación de fuente única. Baseline arquitectónica APROBADA (§20).** Todo verificado en código. **Organización de código/repo: §21. Modelo/esfuerzo de ejecución: §22.**

## 17. Changelog v2→v3 · 18. v3→v3.1 · 19. v3.1→v3.1.1 · 20. v3.1.1→v3.1.2

**§19 (v3.1.1):** persistir raw primero · cursores por scope · PF.3 3ª cascada ·
`source_listing_revisions` obligatoria · constraints + `offer_revision_sources` ·
`offer_translations` title/description · saved un hogar · merge/split policy + `dedup_candidates`
· integration outbox/inbox + `idempotency_records` + `consumer_credentials` · compliance por
fuente · GDPR corregido + crypto-shred · `embedding_models` · docs/eventos con auditoría ·
`school_jobs` como extensión de vacancies · CV ETag · sombra con set etiquetado ·
expand/contract · estimación 7–10 meses · vertical mínima.

**§20 (v3.1.2):**
- **Topología RATIFICADA** en todo el doc: `jobhunt-core` **servicio independiente**, **ambas
  apps BFF**; quitado "modular-core in-process" y "SwissJob = núcleo"; la topología deja de ser
  decisión abierta.
- **Aislamiento operativo** obligatorio: colas Celery `core.*`, prefijos Redis, leader-lock
  separado, rol de BD/`search_path`, **Alembic + migration job propios**.
- **Orden de cutover corregido:** **backfill + sync ANTES del flip** (captura incremental de
  deltas), no "flip + backfill una vez".
- **Matriz de escritor por estado** (Local/Shadow/Cutover/Core-primary/Rollback); escrituras
  interactivas síncronas + idempotency; outbox = eventos, no el command path.
- **Flags separados** (`REQUEST_BACKEND` por capacidad/perfil + `ENGINE/SCHEDULER/NOTIFICATIONS_ENABLED`)
  y **gate de apagado de schedulers legacy** (evita doble cosecha/email tras el flip).
- **Manifiesto de datos por tabla** para ambas apps (más que `JobApplication`).
- **Continuidad reformulada:** fallback = continuidad de LECTURA; write-continuity vía
  `pending_sync` local (opcional).
- **Reutilización de código** decidida (paquete-en-repo + fronteras estrictas).
- **Estimación recalculada a ~10–15 meses** (con la extracción del servicio, BFF en ambos,
  routing/canary, outboxes legacy, namespacing, apagado de schedulers, sync incremental).

**Correcciones finales de runbook (R6 — baseline APROBADA):** captura de deltas + watermark
ANTES del snapshot (no bulk-first) · quiesce de schedulers/engine legacy ANTES del flip (no
después) · schedulers conscientes del routing en el canary multiusuario (global off solo cuando
el último perfil sale de `local`; sin notificaciones duplicadas por perfil) · routing en tabla
`jobhunt_routing` **en cada BFF** (no env var; disponible con el core caído) · **Redis broker/locks
aislado de la eviction** (`allkeys-lru` expulsaría mensajes Celery/locks pese al prefijo) ·
notificaciones con `is_read`/historial → decidir migrar/archivar (no recomputar) · fallback **solo
read-only** en el primer cutover (`pending_sync` diferido) · **fuente única de estimación** (la
tabla del backlog). Arquitectura NO reabierta; solo endurecimiento del runbook.

---

## 21. ADR — Organización de código y repos (CC.1, decidido)

- **Decisión:** `jobhunt-core` vive como **paquete + app desplegable DENTRO del repo de SwissJob**,
  no en un repositorio independiente (aún). Reutiliza el motor de SwissJob por importación con
  **fronteras estrictas**: contrato API `/v1` + esquema Postgres propio; el paquete **no importa
  internals** de SwissJob; SwissJob deja de importar sus servicios locales al pasar a BFF.
- **Motivo:** **repo ≠ topología de producción.** En producción el core ya es un servicio
  independiente enlazado por API en ambos casos; el repo es solo organización de fuentes. Durante
  el Strangler Fig, core y motor legacy co-evolucionan → mismo repo = commits atómicos, un CI,
  refactor barato. Un repo propio el día 1 = versionado cruzado y duplicación prematuros.
- **Enlace en producción (idéntico sea cual sea el repo):** el core despliega como su propio
  contenedor, **puerto dedicado (p.ej. 8003; NO colisionar con 8001/8002/5174/5435/6380)**,
  expuesto SOLO en red interna. Ambas apps (BFF) lo llaman a `http://jobhunt-core:8000/v1` con
  consumer-key; **nunca por ngrok** (el túnel público del portfolio queda por delante).
- **Extraer a repo propio SOLO cuando:** aparezca un 3.er consumidor, la cadencia de release deba
  divergir, se separe la propiedad del equipo, o se retire el motor legacy (Fase F).

## 22. Política de modelo y esfuerzo para EJECUTAR el plan (investigado)

Regla base: **separación de funciones — Fable 5 ejecuta, Opus 4.8 verifica** (nunca el mismo
modelo implementa y se auto-certifica; Fable 5 tiende a memorizar parches → "todo verde" engañoso).

| Rol | Modelo | Esfuerzo |
|---|---|---|
| Ejecutor de migración (Fase A+, implementación, sesiones largas) | **Fable 5** | alto + **task-budgets/timeouts duros** |
| Verificador adversarial / review / gates de cutover (§16) | **Opus 4.8** | máx |
| Diseño / ADRs / contratos (CC.1/CC.2) | Opus 4.8 | alto |
| Volumen mecánico (boilerplate, andamiaje de tests) | Sonnet 5 | medio |
| Trivial (renombrados, edits) | Haiku 4.5 | bajo |

- **Salvaguardas Fable (obligatorias):** `task-budgets` (beta) + `effort` + gates por fase; medir
  **coste por tarea completada** (no precio/token); cachear agresivamente (descuento 90% de
  prompt-cache); fallback a Opus 4.8 en refusals. Esfuerzo **alto** en implementación, **máx** solo
  en diseño/verificación (el esfuerzo máx aumenta los timeouts).
- **NO Fable en el runtime del producto:** el rerank de matching y la generación de CV/carta por
  petición/usuario siguen en **Groq/Gemini** ($10/$50 por millón haría inviable el hot-path).
  "No degradar el LLM" aplica al desarrollo y al pipeline, NO a poner Fable en cada petición.
- Datos y fuentes: benchmarks (FrontierCode Diamond 29.3% vs 13.4% Opus; SWE-Bench Pro 80.3%),
  caveats (over-run/timeouts; memorización) y precio (~2× Opus) investigados en la ronda R6.

---

## 23. Estado de EJECUCIÓN — PRE-FASE de saneamiento (SwissJob)

> Rama `fix/prefase-saneamiento` (repo SwissJob, sobre `main`=`ce2b924`). NO mergeada ni pusheada.
> Estado a 2026-07-22: **5/5 saneamientos + 7 rondas de revisión de código** (la 5.ª dio APPROVE; la
> 6.ª (`bc01996`) y 7.ª (`ee5b353`): `duplicate_of` en `find_fuzzy_duplicate` + no reactivar duplicados). Contratos
> CC.1/CC.2 cerrados aparte: ADRs/CONTRATOS **v4, RATIFICADOS 2026-07-22** (GATE-CC CERRADO — ver §24).
> Suite backend: **750 passed**; los **3 fallos restantes son de scheduler, PRE-EXISTENTES en
> `main`** (mock incompleto, ver `SwissJob/HALLAZGOS.md` H-1), NO introducidos por este trabajo.
> **7 rondas de revisión** (16 interna + 6 + 4 + 3 + 3 externas; la 6.ª y 7.ª, arriba), **todos los hallazgos corregidos** (la 5ª: APPROVE).

### Tareas (todas con TDD falla-antes/pasa-después, ruff limpio, sin regresión nueva)
| Tarea | Qué | Commit |
|---|---|---|
| PF.2 | Un solo despacho de dedup en la cosecha diaria | `ceb1e47` |
| PF.5 | Normalización de título por token (bug substring) + regex de género | `54aadb4` |
| PF.1 | `jobs.content_hash` + upsert refresca contenido + invalida embedding | `2b2f723` |
| PF.4 | Modelo de embeddings: 1 sola carga (worker-ai + API sin preload) | `0a79d15` |
| PF.3 | **Archivar** (no borrar) ofertas caducadas con adjuntos del usuario | `14aeb1a` |

> Correcciones de las revisiones en commits aparte: `03ee955` (1ª), `3c0ae6f` (2ª),
> `ed0f0d4` (3ª), `ad7e9c2` (4ª) y el commit de la 5ª ronda.

### Ronda de revisión (adversarial: 6 revisores + verificación por hallazgo — 16 confirmados, 2 rechazados, **todos corregidos**)
- **Regresiones/correctitud:** PF.3 las ofertas archivadas seguían en el feed de match
  (`match_result_service.get_results` no filtraba `is_active`) → filtro añadido; **PF.4 el split
  estaba solo en `prod.yml`, pero el deploy real es `qnap.yml`** (+`prebuilt`+dev seguían con la
  cola `ai` en el worker general) → aplicado a los 4 compose; `attached` ignoraba
  `feedback_implicit` → añadido al predicado.
- **Optimalidad/eficiencia:** re-embed masivo del corpus al desplegar `content_hash` (invalidaba
  con hash viejo `NULL`) → guardado; `get_active_count` contaba duplicados reactivados → filtro
  `duplicate_of`; `worker-ai` se reciclaba cada 200 tareas (recarga del modelo) →
  `--max-tasks-per-child=0`; marcadores de género de 2 géneros `(m/w)`/`(h/f)` no se quitaban
  (fallo de dedup cross-source) → **regex generalizado** (elimina además 4 entradas redundantes).
- **Tests + docs:** rama `match_results` de `attached` sin test → 4 tests; `sr./jr.` y
  anti-falso-positivo del regex → tests; 4 comentarios/docstrings obsoletos + `HALLAZGOS.md` H-1
  corregidos; H-2 (trade-off de serialización de `worker-ai`, aceptado) documentado.

### 2ª revisión externa (tras el prompt de revisión) — 6 hallazgos, **todos corregidos**
Otro revisor externo (ejecutando pruebas) encontró **2 bloqueantes** (uno introducido por mis
propias correcciones de la 1ª ronda) + 4 menores:
- **BLOQUEANTE — `worker-ai` no arrancaba:** `--max-tasks-per-child=0` es inválido para el pool
  prefork (billiard exige `maxtasks > 0`) → la cola `ai` quedaba sin consumidor. Quitado de los 4
  compose (verificado con **smoke real**: el comando arreglado llega a `ready`; el roto crashea con
  `AssertionError`).
- **BLOQUEANTE — embedding obsoleto tras migrar:** mi guard "no invalidar si `content_hash` viejo
  es NULL" conservaba un embedding viejo cuando el TEXTO cambiaba en el primer upsert post-migración.
  **Rediseñado:** la invalidación del embedding se **desacopla de `content_hash`** y compara SOLO
  `description`/`tags` (los campos de `build_job_text`) → ni re-embed masivo, ni embedding obsoleto,
  ni re-embed por cambios de logo/salario (arregla también el hallazgo de eficiencia).
- **Regex de género:** `R&D/M&A` colapsaba con `R&A` (el `&` activaba el marcador) → endurecido
  (paréntesis obligatorios para la forma interna; la suelta solo como token completo).
- Tests de `draft_letter`/`application_status` de PF.3 + corrección de H-2 (`analyze_cv_and_autofill`
  SÍ usa `encode`, `profile_tasks.py:153`).
Suite tras estas correcciones: **740 passed** (mismos 3 de scheduler pre-existentes).

### 3ª revisión externa — 4 hallazgos, **todos corregidos**
- **ALTO — carrera upsert/embed:** `_embed_pending_batch` leía el texto, calculaba el vector fuera
  de la BD y lo escribía sin comprobar que el contenido siguiera igual → podía guardar un embedding
  obsoleto permanentemente (rompía la coherencia de PF.1). **Fix:** escritura con **concurrencia
  optimista** — `UPDATE ... WHERE content_hash IS NOT DISTINCT FROM :snapshot AND embedding IS NULL`;
  si cambió, la fila queda NULL y se re-embebe en el siguiente lote con el contenido fresco.
- **MEDIO — `worker-ai` reciclaba el modelo:** heredaba `worker_max_tasks_per_child=200` → recargaba
  el modelo cada 200 tareas (no "1 carga de por vida"). **Fix:** el valor es configurable por env;
  `worker-ai` fija `CELERY_MAX_TASKS_PER_CHILD=0` → `None` (sin reciclado); el worker general/scraping
  mantiene 200 (anti-fuga). Verificado: config resuelve `None`, y el worker arranca (`ready`).
- **MEDIO — regex confundía `H/W` (hardware) con género:** la forma suelta aceptaba cualquier
  combinación de `m/w/f/h/d/x`. **Fix:** **enumerar los pares reales** de género
  (`m/f|f/m|m/w|w/m|h/f|f/h` + `/d|/x` opcional) → `H/W` se preserva; `m/w`/`w/m`/`h/f` se quitan.
- **BAJO — §23 desactualizada** (esta sección): tabla y ficheros marcaban como "sin commitear"
  trabajo ya incluido en `14aeb1a`/`03ee955`/`3c0ae6f` → corregido.
Suite tras la 3ª ronda: **742 passed** (mismos 3 de scheduler pre-existentes).

### 4ª revisión externa — 3 hallazgos, **todos corregidos**
- **MEDIO — el drenado abandonaba una fila saltada por concurrencia:** `_embed_pending_batch`
  devolvía "seleccionados" (no escritos) y el bucle cortaba con un lote parcial → una fila saltada
  por el guard de la 3ª ronda quedaba sin embedding hasta la cosecha siguiente. **Fix:** devuelve
  `(seleccionados, escritos)`; el drenado acumula solo escritos y **continúa hasta `selected==0`**,
  reintentando las saltadas (con guarda anti-bucle si el contenido cambia sin parar).
- **MEDIO — el regex perdía marcadores con espacios:** `(m / w / d)` no se quitaba (solo permitía
  espacios en el 3.er componente). **Fix:** `\s*` alrededor de todas las barras de los pares.
- **BAJO — §23 con estados obsoletos** → corregido (esta sección + hash `ed0f0d4`).
Suite tras la 4ª ronda: **745 passed** (mismos 3 de scheduler pre-existentes).

### 5ª revisión externa — **APPROVE para merge** + 3 detalles de pulido (Baja), corregidos
- **Observabilidad:** al cortar el drenado por N pasadas sin progreso, devolvía `status="success"`
  con pendientes. Ahora: constante nombrada `_MAX_EMBED_STALLS`, `warning`, y `status="partial"`.
- **Test-gap:** añadido un test de la **carrera REAL** de `_embed_pending_batch` (otro upsert cambia
  el `content_hash` en una sesión separada durante el `encode` → el guard salta la escritura).
- **Doc:** fecha de estado y hash `ad7e9c2` corregidos en esta sección.
Suite tras la 5ª ronda: **746 passed** (mismos 3 de scheduler pre-existentes).

### Ficheros para REVISIÓN EXTERNA
**Commiteados (`ce2b924..HEAD`):** `backend/tasks/embedding_tasks.py`, `backend/services/deduplicator.py`,
`backend/models/job.py`, `backend/services/job_repository.py`,
`backend/alembic/versions/0a84258328f8_add_jobs_content_hash_pf_1.py`, `backend/config.py`,
`docker-compose.prod.yml`, `SwissJob/HALLAZGOS.md`, y sus tests
(`test_embedding_tasks.py`, `test_deduplicator.py`, `test_job_repository.py`).

**Correcciones de revisión (commiteadas en `03ee955`/`3c0ae6f`/`ed0f0d4`/4ª):** tocan `deduplicator.py`,
`job_repository.py`, `match_result_service.py`, `maintenance_tasks.py`, `embedding_tasks.py`,
`celery_app.py`, los **4 `docker-compose*.yml`**, `HALLAZGOS.md` y sus tests. **El working tree solo
contiene 2 cambios ajenos preexistentes** (`school_job_monitor_architecture.md`, `.docx`).

### Pendiente para cerrar GATE-PF
1. **(Despliegue)** `alembic upgrade head` aplica la migración de PF.1 (+ la HNSW ya pendiente en dev) — decisión de despliegue.
2. **(Merge/push)** la rama `fix/prefase-saneamiento` está lista y **en local** — mergear/pushear cuando se decida.
3. **(Opcional, tickets aparte)** H-1 (3 tests de scheduler pre-existentes) y `pytest-timeout` en la imagen.

---

## 24. Estado de EJECUCIÓN — FASE A (jobhunt-core)

> **GATE-CC RATIFICADO 2026-07-22** (`ADR_JOBHUNTING.md` v4 + `CONTRATOS_FASE_A.md` v4).
> Modelo de ejecución (§22): **Fable 5 implementa, agentes Opus 4.8 auditan** antes de revisión externa.

### A-01 ✅ — Servicio + Alembic + aislamiento (2026-07-22)
**Paquete `SwissJob/jobhunt_core/`** (nuevo, dentro del repo de SwissJob, ADR-08): config aislada
(`CORE_*` + fail-fast en prod) · engine/Base en el esquema propio · Celery con broker en
**`redis-core`** (dedicado, `noeviction`) y colas **`core.*`** · API `/v1` esqueleto (health/ready,
puerto 8003 dev / solo red interna en prod) · **Alembic independiente** (`jobhunt.alembic_version`,
head `core0001`) · **migration job propio**. *(Recuentos de tests por ronda: históricos; el vigente
está en el bloque "Estado VIGENTE" de abajo.)*
**Compose:** los 4 (`dev` activo; `prod`/`qnap`/`prebuilt` con `profiles: ["core"]` — nada arranca
en prod hasta Fase B).

**DoD verificado empíricamente:** version tables separadas (core `core0001` / legacy intacto) ·
`SET ROLE jobhunt_core` → `permission denied` en `public.jobs` · worker `ready` en `redis-core` solo
con colas `core.*` · disjunción de colas en los 4 compose (script) · API health en 8003 ·
**ping→pong E2E** por el broker dedicado · suite legacy tras A-01: **750 passed** (cero impacto).

**Auditoría Opus 4.8 (pre-revisión):** 22 hallazgos → **11 confirmados, todos corregidos**:
rotación de contraseña idempotente (`ALTER ROLE`), fail-fast `CORE_ENV=prod` con credencial dev,
tests de las guardas de inyección del bootstrap (`_PW_RE`/`_IDENT_RE`) + `version_table_schema`,
core en **qnap/prebuilt** (ADR-08 "los 4"), paridad prod (healthchecks/depends_on), allowlist de
boundaries ampliada, comentario honesto sobre grants de PUBLIC.

**Revisión externa de A-01 (REQUEST CHANGES → 7 hallazgos, TODOS corregidos y verificados):**
1. **Secretos aislados:** el core ya NO recibe el `.env.prod` legacy — dominio propio
   `.env.core.prod` (runtime) + `.env.core.admin.prod` (SOLO core-migrate); plantillas `.example`.
2. **Validador de prod REAL:** en `CORE_ENV=prod` se exige usuario `jobhunt_core`, esquema
   `jobhunt`, broker/backend en `redis-core` y contraseñas no-dev (5 tests negativos).
3. **Mínimo privilegio convergente:** `CREATE/ALTER ROLE` aplica SIEMPRE
   `NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS`, revoca memberships, y
   **`_verify_isolation()` comprueba el estado efectivo contra Postgres en cada corrida** (atributos,
   memberships, CREATE en public, SELECT en public.jobs) — el job FALLA si no se cumple. Política
   sobre PUBLIC documentada explícitamente.
4. **Orden de arranque:** api/worker dependen de `core-migrate: service_completed_successfully`
   (4 compose) + **`/v1/ready`** real (BD + head Alembic; evita el falso-verde del health estático).
5. **Persistencia del broker:** AOF (`appendonly yes` + `everysec`) + volumen `redis_core_data`.
6. **redis-core inalcanzable desde el legacy:** red **`core-net` exclusiva** + `requirepass` +
   puerto host solo en `127.0.0.1` (verificado: desde el backend legacy `redis-core` no resuelve).
7. **Imagen non-root** (usuario `core`).

**Ficheros nuevos:** `jobhunt_core/` completo (config, database, celery_app, api/, tasks/,
alembic/ + `core0001`, migrate.py, Dockerfile, requirements.txt, tests/ ×7 — 19 tests),
`.dockerignore`, `.env.core.prod.example`, `.env.core.admin.prod.example`.
**Modificados:** los 4 `docker-compose*.yml` (+ red `core-net`, volumen `redis_core_data`).
**Commit:** `017d369` en la rama **`feat/fase-a-core`** (creada sobre el HEAD de
`fix/prefase-saneamiento` — A-01 se apoya en el estado de compose de la pre-fase, p.ej. el split
de PF.4). Ambas ramas **locales, sin push**.

**2ª revisión externa de A-01 (REQUEST CHANGES → 6 hallazgos, 4 P1, TODOS corregidos):**
1. `.gitignore` ignora `.env.core.prod|admin|redis.prod` (verificado con `git check-ignore`);
   `.example` rastreables.
2. redis-core SOLO recibe su secreto (`.env.core.redis.prod` mínimo) — nunca URLs de BD/Celery.
3. Fail-fast no evadible: `CORE_ENV` es `Literal["dev","prod"]` (un "production" ya no desactiva
   guardas), se rechazan marcadores de plantilla (`CAMBIA_*`/`CHANGE_ME`) y se validan los
   esquemas de URL (+4 tests, incl. la plantilla literal sin rellenar).
4. `_verify_isolation()` EXHAUSTIVA: cero privilegios (todos los tipos) sobre las **13**
   relaciones/secuencias de `public`, cero objetos del core en `public`, owner del esquema
   verificado. (+fix de un bug real destapado al ejecutarla: el planner evaluaba
   `has_sequence_privilege` sobre tablas → `CASE` fuerza el orden.)
5. Bootstrap **transaccional** (rollback si algo falla, sin estados a medias) + **cross-check**
   `CORE_DB_PASSWORD` == contraseña de `CORE_DATABASE_URL` ANTES de tocar el rol (verificado en
   negativo: password desincronizada → aborta sin cambios).
6. `/v1/ready` ya no filtra el texto de la excepción (código genérico + log interno; 3 tests:
   ok / head incorrecto / BD caída sin fuga).
**3ª revisión externa de A-01 (REQUEST CHANGES → 4 hallazgos, 2 P1, TODOS corregidos):**
1. **pgvector irresoluble** con `search_path=jobhunt` (habría bloqueado A-02: tipo `vector` y
   operador `<=>` viven en `public`) → **`search_path = jobhunt, public`** (decisión: la
   protección del legacy son las **ACL verificadas**, no la invisibilidad del esquema; mover la
   extensión afectaría al legacy) + **`_verify_pgvector()`**: cast + `<=>` probados COMO el rol
   del core en cada migrate.
2. **ACL de columna**: un `GRANT SELECT(col)` evadía la verificación → se añade
   `has_any_column_privilege` (SELECT/INSERT/UPDATE/REFERENCES) + chequeo de funciones
   **SECURITY DEFINER** ejecutables en `public` (escalada) → cero.
3. Broker y result backend deben llevar la **misma** contraseña en prod (requirepass único). 
4. §24 consolidado (este bloque).

**Estado VIGENTE de A-01 (consolidado, 2026-07-22):**
- **Tests:** 27 unit + 2 de **integración contra Postgres real** (col-ACL detectada + estado
  limpio/pgvector; corren vía `docker compose run --rm core-migrate python -m pytest …` = 29).
- **Migrate:** transaccional; converge rol a mínimo privilegio (atributos+password+memberships);
  cross-check password URL⇔env; **verificación exhaustiva** (13 relaciones de `public`, ACL de
  tabla Y columna, secdef, ownership, owner del esquema) + **pgvector como el rol del core**.
- **Aislamiento:** `redis-core` con auth+AOF+`core-net` exclusiva (legacy no resuelve el host);
  secretos por dominio (`.env.core.prod` / `.env.core.admin.prod` solo migrate /
  `.env.core.redis.prod` solo redis; reales gitignoreados); imagen non-root; `search_path`
  `jobhunt, public` con legacy protegido por ACL.
- **Sondas:** `/v1/health` (liveness) + `/v1/ready` (BD+head, sin fuga de internals);
  api/worker esperan a `core-migrate` (`service_completed_successfully`).
- Legacy: **750 passed** (sin cambios).

### A-02 ✅ — Migración [A] del manifiesto (2026-07-22)
**`core0002_manifest_a.py`**: las **30 tablas [A]** del contrato (CC.2 §1) escritas a mano —
FKs COMPUESTAS de integridad de propietario (misma vacante/mismo perfil, ambos lados), ciclo
`vacancies↔offer_revisions/incarnations` resuelto con punteros nullable + ALTER, **UNIQUE parcial**
(una incarnación activa por slot), **UNIQUE por expresión** (par canónico dedup), `vector(384)`+HNSW
(coseno), **trigger de coherencia** en `offer_revision_sources`, enums nativos, y ON DELETE conforme
al contrato (nunca CASCADE sobre dato de usuario; `current_eval_id` **RESTRICT** = el ADR-03 impuesto
físicamente).

**DoD verificado:** upgrade limpio (30 tablas + `alembic_version` en `jobhunt`) · head único ·
**downgrade→re-upgrade roundtrip** · ready `core0002` · legacy **750 passed** (cero impacto).
**15 invariantes contractuales probados contra Postgres real** (44/44 tests vía core-migrate;
27+17 skip sin BD): punteros circulares por UPDATE · rechazo de revisión/incarnación/eval de OTRO
propietario (5 FKs compuestas, ambos lados) · UNIQUE parcial y slot reciclado · par canónico ·
`uq_eval_profile_vacancy_key` (append-only A-08) · RESTRICT de la eval vigente · trigger ORS ·
`SET NULL` por-columna (solo el puntero, la PK sobrevive) · vector+`<=>` · acks GDPR únicos.

**Auditoría Opus 4.8 (pre-revisión): 13 hallazgos → 11 confirmados, todos corregidos.** Los clave:
(1) el `SET NULL` plano de las FKs circulares era **no-funcional** (habría intentado nulificar la
PK) → **`ON DELETE SET NULL (columna)`** de PG15+ vía ALTER en crudo; (2) `RESTRICT` explícito en
`fk_pvs_current_eval_same_pair` (el comentario prometía RESTRICT pero el default era NO ACTION);
(3) 7 test-gaps de invariantes cubiertos (incl. el UNIQUE central de A-08 y el lado perfil de las
FKs compuestas). Durante la implementación: fix de enums duplicados (`create_type=False`).

**Ficheros:** `jobhunt_core/alembic/versions/core0002_manifest_a.py` (nuevo) ·
`jobhunt_core/tests/test_integration_schema.py` (nuevo) · `requirements.txt` (+pgvector) ·
`tests/test_alembic.py` (head único robusto). Commit `2dd5bf1`.

**Revisión externa de A-02 (REQUEST CHANGES → 2 P1 + 1 P2, TODOS corregidos):**
1. **P1 — el trigger ORS no mantenía la coherencia permanentemente** (probado por el revisor:
   mutar `slr.incarnation_id` o `incarnation.vacancy_id` tras el insert la rompía en silencio) →
   **triggers de INMUTABILIDAD** a nivel de BD sobre las 3 columnas de binding
   (`slr.incarnation_id`, `incarnation.vacancy_id`, `offer_revision.vacancy_id`), alineados con el
   ADR-04 v4 (merge = resolver `merged_into`; reciclado = nueva incarnación). +3 tests.
2. **P1 — el HNSW no era "por modelo"** (índice global: mezcla espacios vectoriales en
   expand/contract, degrada recall) → **`offer_embeddings` PARTICIONADA POR LIST(`model_id`)**:
   cada modelo = su partición = su propio HNSW heredado del índice padre particionado. Regla
   operativa documentada: registrar un modelo (A-06+) crea su partición. +test de catálogo
   (relkind `p`, `hnsw`+`vector_cosine_ops`, `vector(384)` exacto, partición hereda su índice).
3. **P2 — test-gaps del DoD** → +8 tests (inmutabilidad ×3, `primary_incarnation` negativo +
   SET NULL por-columna, FK compuesta de ORS con trigger pasando, catálogo HNSW, PK por partición).
**Estado tras la ronda: 51/51 tests vía core-migrate (27+24 skip sin BD) · downgrade→upgrade
roundtrip · ready `core0002`.**

**2ª ronda de revisión de A-02 (1 P1 de disciplina Alembic, resuelto):** `ce9a7be` editaba el
contenido de la revisión `core0002` DESPUÉS de aplicada — Alembic no registra checksum → una BD
ya en `core0002` divergiría en silencio. Resolución = **opción 2 del revisor** (sancionada para
ramas no compartidas): (a) verificado que la rama es PRIVADA (sin upstream; ningún remoto contiene
los commits); (b) **squash** de la corrección dentro del commit de A-02 (una sola revisión
`core0002` canónica); (c) **BD de dev RECREADA desde cero** (`DROP SCHEMA jobhunt CASCADE` —
solo contenía la fila de version — + reinstalación: cadena completa, 31 tablas, particionada,
4 triggers, 51/51, ready) → cero divergencia posible; (d) **regla permanente** documentada en
`alembic/env.py`: *una revisión aplicada es INMUTABLE; toda corrección = nueva revisión*.

### A-03 ✅ — Ingesta: 1 provider + cursor por scope (2026-07-22)
**`jobhunt_core/harvest/`** (nuevo): `BaseProvider` + `ListingSink` (costura para la persistencia
de A-04 — sin tarea Celery aún: un sink no-op avanzaría el cursor perdiendo ofertas) · provider
**Arbeitnow** (Tier 0 público, NO restringido) con cursor incremental **sin pérdida** · `runner`
con la disciplina ADR-05: **sink + cursor en UNA transacción, commit del cursor AL FINAL**; fallo →
rollback conjunto + `consecutive_failures` (cursor intacto).

**Auditoría Opus 4.8 (pre-commit): 19 hallazgos → 13 confirmados, todos corregidos.** El HIGH:
mi watermark saltaba por encima del backlog al cortar por presupuesto de páginas → **pérdida
permanente de ofertas** (y el verificador detectó que el fix simple del auditor era insuficiente).
Rediseño del cursor: **estado de backfill** (`pending_watermark` + `backfill_page` con solape de 1
página; el watermark SOLO avanza al drenar) + frontera **estricta** (`<`) que re-emite empates de
segundo hacia el sink idempotente + items sin timestamp/url se saltan sin disparar el corte +
`_record_failure_safe` (el error original nunca se enmascara).

**Verificado (66/66 vía core-migrate; 35+31 skip sin BD):** unión completa entre runs con
presupuesto < backlog (nada se pierde) · re-emisión de frontera · resume solo trae lo nuevo ·
2 scopes con cursor propio · **atomicidad real sink+cursor** (fila del sink commiteada con el
cursor; y si el cursor falla tras el sink, TODO se revierte junto) · fallo de provider/sink deja
cursor intacto +1 fallo · disabled se salta. HTTP 100% mockeado.

**Ficheros:** `jobhunt_core/harvest/` (types, provider, runner, providers/arbeitnow) ·
`tests/test_harvest_provider.py` · `tests/test_integration_harvest.py`.

**Revisión externa de A-03 (REQUEST CHANGES → 3 P1 + 1 P2, TODOS corregidos; cada uno
REPRODUCIDO por el revisor):**
1. **P1 — el solape de 1 página no resiste la deriva del feed** (borrados masivos entre runs →
   pérdida): **fuera el resume por página** → backfill SIEMPRE desde la página 1 con **ancla
   lógica** (`anchor_ts`): lo ya emitido se salta barato; borrados/desplazamientos solo causan
   re-emisión (jamás salto). Garantía documentada honestamente (retrodatados bajo el watermark:
   indetectables para cualquier esquema de watermark).
2. **P1 — lost-update entre runs concurrentes del mismo scope**: la tx de persistencia bloquea
   la fila del scope (**`FOR UPDATE`**) y re-lee el cursor; si otro run avanzó → aborta **`stale`**
   (sin pisar, sin contar fallo). Test de carrera real (run lento interleaved con run rápido).
3. **P1 — cambiar la keyword conservaba el watermark** (enterraba ofertas del filtro nuevo):
   **fingerprint de parámetros SEMÁNTICOS** (`SEMANTIC_PARAMS` por provider) guardado en el
   cursor; si cambia → reinicio con log. `max_pages` (operativo) excluido.
4. **P2 — livelock con `max_pages=1`**: el solape desapareció; validación **`max_pages ≥ 2`** +
   techo `MAX_SKIP_PAGES` que suelta el ancla (progreso por re-emisión idempotente).
**Estado tras la ronda: 71/71 tests. Commit `59fb545`.**

**2ª ronda de revisión de A-03 (1 P1 + 2 P2 + 2 doc, TODOS corregidos; repros del revisor):**
1. **P1 — el ancla filtraba inserciones tardías**: una oferta publicada ENTRE runs por encima del
   ancla se saltaba para siempre → **el ancla ya NO descarta nada**: solo contabiliza el progreso
   del backfill (páginas de re-escaneo vs de avance); durante el re-escaneo **TODO lo ≥ watermark
   se re-emite** al sink idempotente. Repro del revisor (x=325 sobre ancla 250) como regresión.
2. **P2 — dependencia de orden estricto no contractual**: el corte pasa a ser **por PÁGINA completa
   antigua** (no por el primer item viejo) — la página `[300,100,250]` ya no pierde el 250; y si se
   detecta **desorden**, se exigen 2 páginas antiguas consecutivas antes de cortar (conservador).
3. **P2 — la re-validación bajo `FOR UPDATE` solo miraba el cursor**: ahora re-lee **enabled,
   params (fingerprint) y fuente**: disable durante el fetch → `skipped`; cambio de params/fuente →
   `stale`. Tests de ambas mutaciones mid-fetch.
4. Doc: `types.py` documenta el estado `stale`; esta sección corregida.
**Estado tras la 2ª ronda: 76/76 tests. Commit `161b941`.**

**3ª ronda de revisión de A-03 (2 P1 + 1 P2, TODOS corregidos — con CAMBIO DE DISEÑO):**
El revisor demostró el punto de fondo: **con orden no contractual, ningún corte finito de
"páginas antiguas" prueba drenaje** (repro: `[300,250]/[150]/[240]` perdía el 240; y una página
sin timestamps se clasificaba "antigua" y cortaba). Decisión: adoptar su opción ESTRICTA →
1. **El watermark solo se consolida al AGOTAR `links.next` en un MISMO run.** Cada run barre el
   feed completo (tope de seguridad que, si corta, deja el watermark INTACTO — fallo conservador).
   La incrementalidad vive en la **EMISIÓN** (el watermark filtra qué va al sink idempotente), no
   en el fetch — para una API Tier 0 el barrido completo por run es lo estándar (es lo que ya
   hacen los providers del legacy). **Desaparecen ancla, backfill, pending y heurísticas de
   orden**: no queda estado del que pueda depender una pérdida.
2. Página sin timestamps: solo se saltan sus ITEMS; jamás decide cortes (repro como regresión).
3. Tests de disable/params endurecidos (`sink.batches == []`) + caso de **fuente re-apuntada**
   mid-fetch → `stale` (rama sin cubrir).
**Estado tras la 3ª ronda: 74/74 tests. Commit `a77b4e6`.**

**4ª ronda de revisión de A-03 (3 P1 + 2 doc, TODOS corregidos — DISEÑO FINAL):**
El hallazgo definitivo (#3): filtrar la emisión por watermark era **incompatible con A-04**
(`last_seen_at` en cada cosecha + detección de revisiones exigen ver TODO lo visible). Resolución:
1. **EMISIÓN TOTAL**: cada barrido entrega al sink TODOS los listings válidos del scope; dedup y
   refresco son del sink idempotente (como los providers del legacy). El watermark pasa a ser
   **solo metadato** (`last_top_seen`). Sin filtro de emisión, un borrado mid-paginación causa
   **omisión temporal** (el siguiente barrido lo ve), jamás pérdida (repro #2 como regresión).
2. **Objetivo adaptativo de páginas** (#1): si el barrido se corta por el tope, el objetivo se
   duplica (persistido en el cursor) hasta agotar el feed — liveness sin tocar config a mano
   (repro: feed 3 págs con `max_pages=2` → `e` visto en el run 2 con el MISMO config).
3. **`partial`**: un barrido incompleto persiste sink+cursor pero NO marca `last_complete_at` ni
   resetea fallos, y el runner reporta `partial` con warning (alerta operativa).
4. Doc: fila A-03 de CONTRATOS reescrita (sin backfill); esta sección al día.
**Estado tras la 4ª ronda: 73/73 tests. Commit `53c2046`. → 5ª ronda: APPROVE** (2 P2 de
operación, corregidos junto a A-04: **target aprendido persistente** — sin oscilación
partial/complete, test de 4 runs — y **hard cap contractual** configurable con alerta
persistente de capacidad).

### A-04 ✅ — Sink real (listing + incarnación + revisión raw) + tarea Celery (2026-07-23)
**`harvest/sink.py` — `RawListingSink`** (Fable, foco eficiencia): slot + incarnación (seq =
max+1 al reabrir un slot cerrado) + vacante fresca con puntero primario (identidad la refina
A-05) + **revisión raw por `content_hash` ANTES de normalizar** + `last_seen_at`/url en **cada**
cosecha. **Todo por lotes** (`ANY(...)`/`executemany`): queries O(1) respecto al lote.
**`tasks/harvest.py`** — `jobhunt.harvest.run_scope` (cola `core.harvest`; `def`+`asyncio.run`;
`partial/stale/skipped` sin retry; `error` con retry) — cableada ahora que existe el sink real.

**Auditoría Opus (pre-commit): 13 hallazgos → 5 confirmados (8 rechazados), todos corregidos:**
- **Concurrencia entre scopes de la MISMA fuente** (el lock del runner es por-scope): INSERT de
  incarnaciones con **ON CONFLICT contra el índice parcial** + limpieza de vacantes huérfanas del
  perdedor + **orden global determinista** en TODAS las escrituras por lote (anti-deadlock) —
  y el **test de carrera real** (2 scopes, lotes en orden inverso, `asyncio.gather`) cazó además
  un deadlock que la auditoría no vio (faltaba ordenar el INSERT de slots). Estable ×5.
- **Eficiencia**: pre-filtro indexado de revisiones (en régimen estable no se envían payloads que
  acabarían en DO NOTHING) + **una sola serialización canónica** (mismo string para hash y raw —
  cierra también el TypeError de payloads no-JSON-nativos).
- Tests de bordes: lote vacío, duplicado intra-lote (última gana), misma URL normalizada en un lote.

**Verificado: 90/90 tests vía core-migrate** (grafo completo, refresh sin revisión, revisión por
cambio, colisión URL, E2E runner+sink atómico, carrera cross-scope con invariantes, tarea).

**1ª revisión externa (REQUEST CHANGES, 2026-07-23) — 2 P1 + 3 P2, ambos P1 reproducidos por
la revisora. Todos corregidos:**
- **P1 deadlock cross-key** (`sink.py`): ordenar por `external_id` no cubre la SEGUNDA clave
  UNIQUE (`url_normalized`) — con lotes `T1[a→urlZ,b→urlA]` vs `T2[c→urlA,d→urlZ]` los locks
  del índice quedan en orden inverso (2/5 `DeadlockDetectedError` en su repro). **Fix de
  clase**: serialización POR FUENTE con `pg_advisory_xact_lock(hashtextextended(source_id))`
  — elimina todo el problema de orden de locks intra-fuente y conserva el paralelismo entre
  fuentes. Regresión: la repro exacta ×5 rondas con invariantes (sin dup de URL, sin huérfanas).
- **P1 envenenamiento del lote**: un `external_id` de 201 chars (o url>1000, NUL, surrogate
  suelto) revertía TODO el lote (`StringDataRightTruncation`) — y con la emisión total de A-03
  el dato tóxico reaparecía en cada cosecha bloqueando el scope PARA SIEMPRE. **Fix**:
  validación de frontera contra los límites reales del esquema (`_valid_listing` + guardia de
  serialización); el inválido se CUARENTENA con log, el lote válido sigue. De paso: la
  serialización canónica Y el hash se calculan UNA sola vez (validación) y se reutilizan.
  Regresión: lote mixto 2 válidos + 4 tóxicos → persisten 2, cuarentena ×4.
- **P2 pre-filtro por pares**: `ANY(ids) AND ANY(hashes)` era el producto cruzado (O(n²) con
  historiales que comparten hashes) → join contra `unnest(uuid[], text[])` por par exacto.
- **P2 config inválida**: `hard_max_pages=0` → 0 peticiones y cursor parcial eterno → ahora
  `ValueError` explícito (`hard_max_pages >= MIN_PAGE_TARGET` y `>= max_pages`).
- **P2 clasificación de retries en la tarea**: scope eliminado tras encolar → `not_found`
  (permanente y normal, sin retry); provider desconocido (`KeyError`) → falla explícito SIN
  consumir retry; el retry queda para transitorios (HTTP/BD/`status=error`).

**Re-verificado: 97/97 vía core-migrate** (90 + 7 regresiones); sin BD 49 passed + 48 skipped;
test de deadlock cross-key estable ×5 ejecuciones (25 carreras).

**2ª revisión externa (REQUEST CHANGES, 2026-07-23) — 2 P1 + 1 P2, los P1 reproducidos.
Todos corregidos:**
- **P1 event loop vs engine global** (`tasks/harvest.py` + `database.py`): cada `asyncio.run()`
  de la tarea crea un loop NUEVO, pero el `AsyncEngine` global (pool asyncpg) queda ligado al
  primero — la 2ª tarea del mismo proceso worker muere con `Future attached to a different
  loop` + `InterfaceError`. **Fix**: `task_session_factory()` en `database.py` — engine
  DESECHABLE por invocación (NullPool, dispose en el mismo loop; mismo patrón que
  `backend.database.task_session` del legacy), pasado a `run_scope`. Regresión: 2 tareas
  Celery REALES consecutivas en el mismo proceso (sin mockear `_run_scope_impl`).
- **P1 aislamiento de veneno incompleto** (`sink.py`): NaN en payload (jsonb lo rechaza),
  URL `https://[invalid` (`urlsplit` ValueError) y surrogates en external_id/url seguían
  abortando el lote entero; y el check de NUL por secuencia escapada daba FALSO POSITIVO con
  texto legítimo que contuviera literalmente `\u0000`. **Fix**: `_preprocess()` — TODO el
  trabajo por-listing (canónica con `allow_nan=False`, hash, `normalize_url`, validación
  UTF-8 de las tres cadenas) dentro de la cuarentena; URL normalizada PREcalculada y
  reutilizada en `_ensure_slots`; NUL detectado recorriendo los VALORES reales del payload
  (`_payload_has_nul`). Regresión: lote mixto 3 válidos (incl. el falso positivo literal)
  + 7 tóxicos.
- **P2 clasificación permanente/transitoria completa**: `ProviderConfigError` (provider.py,
  lo lanza arbeitnow y PROPAGA por el runner sin contar backoff) y `UnknownProviderError`
  (registro) → la tarea captura SOLO esas como permanentes (un `KeyError` interno vuelve a
  ser transitorio con retry — test de contraste); scope desaparecido en CUALQUIER punto
  (precheck de la tarea, arranque del runner, re-lectura bajo lock) → `not_found` sin retry.

**Re-verificado: 103/103 vía core-migrate** (97 + 6 regresiones); sin BD 51 passed + 52
skipped; carreras/veneno/loop estables ×3.

**3ª revisión externa (2026-07-23): APPROVE — los 2 P1 cerrados, A-04 cumple su DoD.** Un P2
residual (corregido en el acto): los TIPOS inválidos del JSON de config (`max_pages="abc"`,
`hard_max_pages=null`, `keyword=123`) lanzaban ValueError/TypeError/AttributeError genéricos
y consumían retries dejando `consecutive_failures` creciendo (reproducido por la revisora).
Fix: `_parse_config()` valida objeto/tipos/rangos y `_cursor_int()` clasifica el cursor
corrupto — todo `ProviderConfigError` permanente, sin retry. **Final: 105/105 vía
core-migrate; sin BD 53 passed + 52 skipped. A-04 CERRADO.**

### A-05 ✅ — Identidad/re-enlace determinista + guard de reciclado (2026-07-23)
**Solo lo DETERMINISTA en Fase A** (ADR-01): cosine/`SIM_RECYCLE`, nivel-3 semántico y la
RESOLUCIÓN de candidates quedan en Fase B — aquí jamás se funde ni recicla por semántica.
- **`harvest/identity.py`** (Fable): normalización de tokens **PF.5 PORTADA del legacy**
  (commit 54aadb4; fronteras estrictas — cero imports cruzados): título sin seniority/género
  por TOKEN, empresa sin sufijos legales; `fuzzy_key` (None si identidad incompleta);
  **registro de extractores por NOMBRE de fuente** (permite extraer también del raw HISTÓRICO
  para el guard sin acoplar el sink al provider); `should_recycle` = ambas empresas presentes
  y tokens distintos (falta de datos → conservador).
- **Sink** (por lotes, queries O(1)): guard de reciclado en el nivel exacto (pre-filtro de
  pares + raw vigente `DISTINCT ON` solo de lo cambiado → cierra incarnación y abre otra con
  **vacante NUEVA, jamás re-attach** — ADR-01 literal); **attach cross-source** por
  `url_normalized` vigente en otra fuente (vacante activa: ni archivada ni fundida; empate
  multi-fuente → min(vacancy_id); solo AL CREAR, nunca re-attach automático) +
  `link_evidence('url_normalized', 1.0)`; **alias external_id↔URL** (gana external_id;
  evidencia `url_alias` sin spam — pre-filtro por (slot, vacante, método)); **drift de URL**
  entre fuentes y **duplicados difusos intra-lote** → `dedup_candidates` (pending, par
  canónico idempotente).
- **`core0003`** (revisión NUEVA — core0002 inmutable): índice `url_normalized` para la
  búsqueda cross-source.

**Auditoría Opus (workflow 12 agentes, verificación adversarial): 7 hallazgos → 3
confirmados (4 refutados con traza), todos corregidos:**
- **P2 primary huérfano en vacante compartida**: reciclar el listing primario de una vacante
  attacheada por OTRA fuente dejaba `primary_incarnation_id` → incarnación CERRADA para
  siempre (la otra fuente refresca `last_seen` y el archivado nunca llega; A-06 derivaría el
  canónico del contenido equivocado). Fix doble: el slot reciclado NUNCA se attachea (ADR-01:
  vacante nueva — tampoco a la que acaba de dejar) + `_repair_primary_pointers` reasigna
  determinista (activa más antigua) las vacantes que pierden su primary y conservan activas.
- **P2 identidad no-string**: `title`/`company_name` numérico/bool/lista del feed reventaba
  `.lower()` FUERA de la cuarentena → rollback del lote en bucle. Fix: no-string = identidad
  ausente (conservador) en `extract_identity`.
- **P3 cobertura**: la exclusión `merged_into` del attach no tenía test (solo archived) →
  test espejo añadido.

**Verificado: 118/118 vía core-migrate** (guard recicla/conserva/conservador sin identidad,
attach sin robar primary, alias sin spam, drift y par canónico idempotentes, no-fusión por
semántica, repro del auditor con reasignación de primary, no-string, merged excluida); sin BD
56 passed + 62 skipped; carreras A-04 + identidad estables ×3.

**1ª revisión externa (REQUEST CHANGES, 2026-07-23) — 2 P1 (concurrencia cross-source,
reproducidos) + 1 P2. Todos corregidos con un PROTOCOLO ÚNICO DE LOCKS POR VACANTE:**
- **P1 attach sin revalidar**: entre la pre-selección (vacante activa) y el INSERT, un
  archive/merge concurrente podía commitear → contenido vivo enlazado a vacante inactiva.
- **P1 reparación con cierre sin confirmar**: dos fuentes reciclando a la vez su incarnación
  de una vacante compartida (3 fuentes) se elegían mutuamente la incarnación que la otra
  estaba cerrando → primary a incarnación cerrada pese a quedar una activa (la de C).
- **Fix (ambos)**: el sink DECIDE primero (guard sin cerrar, pre-selección de attach sin
  lock) y después bloquea TODAS las vacantes implicadas (recicladas ∪ candidatas) en UN
  `SELECT ... ORDER BY id FOR UPDATE` (orden global → sin deadlock) que además REVALIDA la
  vigencia (EPQ re-evalúa el WHERE tras el commit ajeno: la archivada cae del attach →
  vacante nueva). Cierre de incarnaciones y `_repair_primary_pointers` corren BAJO esos
  locks (la re-lectura solo ve estado commiteado o propio). Regresiones: archive concurrente
  al attach (tx abierta + commit durante la espera) y reciclado concurrente A+B con C activa
  → primary SIEMPRE a la incarnación activa de C. Protocolo documentado: archive/merge/
  attach/reciclado comparten estos locks por vacante.
- **P2 similitud dependiente del orden**: `setdefault`+DO NOTHING conservaban la primera
  evidencia (0.85 vs 0.9 según llegada) → MÁXIMO en el lote + `ON CONFLICT ... DO UPDATE
  SET similarity = GREATEST(...)` SOLO mientras `state='pending'` (un candidato resuelto no
  se toca — test con confirmed).

**Re-verificado: 121/121 vía core-migrate** (118 + 3 regresiones); sin BD 56 passed + 65
skipped; identidad completa estable ×3.

**2ª revisión externa (REQUEST CHANGES, 2026-07-23) — 2 P1 + 1 P2, reproducidos. Corregidos:**
- **P1 revalidación incompleta**: solo se recomprobaba la vigencia de la VACANTE, no la
  RELACIÓN que justificó el attach — si la otra fuente RECICLABA su única incarnación entre
  la pre-selección y el lock, la vacante quedaba nominalmente activa pero VACÍA (primary
  cerrado) y B se attachaba igual. **Fix**: la pre-selección SOLO determina qué bloquear;
  bajo el lock se repite el **join completo** (incarnación activa + vacante vigente + URL +
  otra fuente) restringido a las candidatas bloqueadas — la relación rota expulsa la
  candidata → vacante propia (+drift). El resultado pre-lock jamás se usa.
- **P1 lock filtrado por vigencia**: `_lock_vacancies` excluía archivadas/fundidas… incluso
  las RECICLADAS que sí se iban a modificar (cierre + reparación) — dos reciclados
  concurrentes sobre una vacante archivada compartida esquivaban el lock y recreaban la
  corrupción. **Fix**: se bloquea TODO lo que se modifica, SIN filtro; la elegibilidad de
  attach la decide la revalidación, no el lock.
- **P2 tests no deterministas**: `sleep`/`gather` no fijaban la intercalación. **Fix**:
  `PausingSink` (hook que pausa tras la pre-selección o tras el cierre, con los locks en
  mano) + las dos repros: archive-tras-pre-selección, reciclado-vacía-la-vacante, y
  reciclados concurrentes sobre archivada con **assert de serialización** (B bloqueado antes
  de poder cerrar mientras A retiene los locks).

**Re-verificado: 123/123 vía core-migrate**; sin BD 56 passed + 67 skipped; identidad ×3.

**3ª revisión externa (2026-07-23): APPROVE — A-05 cumple su DoD.** P2 residual (corregido en
el acto): el test de reciclados concurrentes sobre archivada asumía por `sleep` que B había
alcanzado el lock → espera VERIFICADA con `pg_blocking_pids` (Postgres confirma que B está
bloqueado POR A antes del assert de serialización). **A-05 CERRADO. Final: 123/123.**

### A-06 ✅ — Revisión canónica + embeddings por text_hash (2026-07-23)
- **`harvest/normalize.py`** (Fable): contenido canónico por registro de normalizadores por
  fuente (el fn solo ESCOGE campos; coerción central defensiva — lección A-05 #2: tipos
  basura degradan, jamás revientan; sin título → sin canónica, con log). **`text_hash` SOLO
  de title+company+description+tags** (ADR-02): salario/location dan OTRO content_hash con el
  MISMO text_hash → NO re-embebe. `build_offer_text` = misma composición que el legacy (los
  vectores deben ser comparables en la sombra de Fase B).
- **Sink `_canonicalize`** (misma tx del runner): **AUTO-REPARADOR** — cada barrido asegura
  que la vacante cuyo PRIMARY es este listing apunta a la offer_revision de su contenido
  ACTUAL (cubre alta, reverts a hash histórico y huecos previos; en régimen estable solo
  lecturas). Puntero `current_offer_revision_id` movido OPTIMISTA en una sentencia
  condicionada al primary vigente (no amplía el protocolo de locks de A-05). Fuentes NO
  primarias con revisión nueva → `offer_revision_sources` sin mover el puntero (ADR-01).
- **`embeddings.py` + tarea `jobhunt.embedding.run_pending`** (cola core.embedding):
  `register_model` idempotente crea la PARTICIÓN del modelo (regla core0002; dim≠384 →
  ValueError, expand/contract); pendientes = text_hash de canónicas VIGENTES de vacantes
  activas sin vector (DISTINCT: mismo texto en N vacantes = 1 embedding); escritura
  **OPTIMISTA** (pre-filtro + ON CONFLICT (text_hash, model_id) DO NOTHING — el rowcount de
  executemany en asyncpg no es fiable); **backend INYECTABLE** con import perezoso de
  sentence-transformers (mismo modelo multilingüe 384d del legacy; tests con backend fake,
  la imagen sin ML sigue funcionando); encode FUERA de transacción. requirements: torch
  CPU-only (mismo truco del backend).

**Auditoría Opus (workflow 13 agentes): 8 hallazgos → 3 confirmados (5 refutados con traza),
corregidos:**
- **P3 provenance huérfana**: la canónica creada por el AUTO-REPARADOR (raw antiguo no-fresh,
  p.ej. normalización fallida en un run anterior ya corregida) quedaba sin fila en
  `offer_revision_sources` para siempre → la agregación cubre fresh ∪ canónicas creadas en
  este run. Regresión: normalizador roto en run 1 → corregido → run 2 mismo raw enlaza.
- **P2 test-gap**: primary no-normalizable en lote mixto no envenena (canónica del válido,
  puntero NULL del fallido, raw persistido) — test añadido.
- **P3 test-gap**: modelo activo dim≠384 colado por otra vía → la tarea lo SALTA con error
  (solo embebe el 384) — test añadido.
Fixtures de sink/identidad actualizados (las canónicas nuevas exigen limpiar offer_* y
nullificar el puntero antes de borrar vacantes).

**Verificado: 138/138 vía core-migrate** (sin BD 61 passed + 77 skipped): canónica+puntero+
fuentes, location cambia sin re-embed, revert re-apunta sin duplicar, no-primario agrega sin
mover puntero, partición por modelo idempotente + guard de dimensión, tarea e2e con fake
backend (2→0→1), doble store y carrera optimista → 1 fila; offer ×3 estable.

**1ª revisión externa (REQUEST CHANGES, 2026-07-23) — 3 P1 + 2 P2, reproducidos. Corregidos:**
- **P1 canónica huérfana del primary reparado**: al reciclarse el primary A de una vacante
  COMPARTIDA, `_repair_primary_pointers` la reasignaba a B pero nadie reconstruía la canónica
  (el contenido de A cerrado seguía vigente — A-08 evaluaría contenido incorrecto). **Fix**:
  la reparación DEVUELVE los cambios y `_rebuild_canonical_after_repair` (bajo los locks ya
  tomados) reconstruye `current_offer_revision_id` desde la ÚLTIMA revisión raw del NUEVO
  primary con el normalizador de SU fuente; sin revisión/normalizador/título → **NULL, jamás
  el contenido del primary anterior**.
- **P1 canónica obsoleta con contenido inválido**: primary antes normalizable que recibe
  contenido nuevo SIN título conservaba la canónica anterior mientras `last_seen_at` se
  refrescaba (oferta obsoleta servida indefinidamente). **Fix**: CAS a NULL condicionado a
  `(vacancy_id, primary_incarnation_id)`. Regresión válido → inválido → revert (restaurado).
- **P1 backend global para N modelos**: la tarea iteraba modelos activos pero encodeaba con
  UN backend global (`CORE_EMBEDDING_MODEL_NAME`) — vectores del mismo encoder bajo
  model_ids que decían ser modelos distintos. **Fix**: fábrica de backends por
  `(name, version)` cacheada e inyectable; `SentenceTransformerBackend(name, version)` carga
  el modelo con `revision=version` (revisión INMUTABLE de HF). A-07 reutilizará esta
  resolución. Regresión: dos modelos activos → backends distintos, vectores distintos.
- **P2 register_model no validaba la fila existente**: tras el DO NOTHING, una fila previa
  dim=512 daba éxito → ahora se relee BAJO LOCK, dim distinta = ValueError (inmutable);
  `active` SÍ se actualiza (declarativo, documentado).
- **P2 text_hash vs texto del encoder**: hash sobre JSON estructurado pero encoder sobre
  concatenación (colisiones de estructura rompían "mismo texto = un embedding") → el hash
  deriva del MISMO `build_offer_text` que alimenta al encoder.
`_ensure_offer_revisions` extraído (lookup+insert+re-select compartido por canónica y
reconstrucción).

**Re-verificado: 144/144 vía core-migrate** (138 + 6 regresiones); sin BD 62 passed + 82
skipped; offer/identidad estables ×3.

**2ª revisión externa (REQUEST CHANGES, 2026-07-23) — 2 P1 + 1 P2, reproducidos. Corregidos:**
- **P1 reparación vs revisiones concurrentes del nuevo primary**: una fuente no-primaria con
  incarnación existente persistía revisión nueva SIN lock de vacante — leía un primary a punto
  de cambiar y la canónica quedaba en el contenido antiguo de B con la última revisión de B
  más nueva. **Fix**: el lock por vacante cubre TODAS las vacantes con incarnación en el lote
  (batch_vacs ∪ recicladas ∪ candidatas de attach) — revisiones y canonicalización se deciden
  BAJO el lock. Regresión determinista (PausingSink + pg_blocking_pids): B bloqueado durante
  la reparación de A → al entrar ve el primary nuevo (él mismo) → canónica = 'B NUEVO'.
- **P1 hash raw como clave de la canónica**: `(vacancy, raw_hash)` no identifica el resultado
  de un NORMALIZADOR — mismo raw en dos fuentes reutilizaba la canónica ajena (saltándose el
  normalizador de B) y hasta RESUCITABA una canónica tras el NULL de la reparación. **Fix**:
  `offer_revisions.content_hash` = hash del contenido CANÓNICO normalizado
  (`offer_content_hash`; el hash del raw queda en source_listing_revisions); se normaliza
  SIEMPRE antes de buscar revisión reutilizable; None FUERZA NULL aunque exista una revisión
  con hash raw coincidente. Regresiones: mismo raw + normalizador distinto → canónica
  distinta ('B:Mismo raw'); NULL no resucitado por el siguiente barrido.
- **P2 revisión móvil del modelo**: `revision=version` aceptaba refs móviles ('main') que
  resolverían a pesos distintos tras un reinicio → `register_model` EXIGE un commit SHA
  inmutable (40 hex); regresión: 'main' rechazado.

**Re-verificado: 147/147 vía core-migrate** (144 + 3 regresiones); sin BD 62 passed + 85
skipped; offer estable ×3.

**3ª revisión externa (2026-07-23): APPROVE, confianza alta — sin nuevos P1/P2; los tres
anteriores verificados resueltos (lock del lote, canónica por contenido normalizado, SHAs
inmutables); el revisor verificó además raw distinto → misma canónica (2 raw, 1 canónica,
2 enlaces de procedencia). A-06 CERRADO sobre `c9121f2`. Final: 147/147.**

### A-07 ✅ — Perfiles + `profile_revisions`/`profile_embeddings` (2026-07-24)
- **`profiles.py`** (Fable): `ensure_consumer`/`upsert_profile` idempotentes (UNIQUE por
  (consumer, external_ref)); **`save_profile_revision`** = revisión INMUTABLE idempotente por
  `(profile_id, content_hash)` — mismo contenido = misma revisión, jamás duplica.
  **Disciplinas heredadas de A-06 rev.2**: `content_hash` = hash del contenido NORMALIZADO
  (coerción central defensiva; sin texto embebible → None) y `text_hash` = hash del TEXTO del
  encoder. Texto embebible = **title + cv_text + skills** (espejo del legacy
  `profile_tasks:151` — vectores comparables en la sombra de Fase B); salario/idiomas/
  ubicaciones cambian el content_hash pero NO el texto. **Vigente = ÚLTIMA ACTIVACIÓN
  (max(seq) en `profile_revision_activations`, core0004 — ver 1ª revisión abajo)**; A-08
  fija la revisión evaluada vía FK compuesta.
- **Embeddings**: `pending_profile_revisions` (solo la revisión VIGENTE de cada perfil; el
  histórico no se re-embebe) + `store_profile_embeddings` (pre-filtro + ON CONFLICT sobre la
  PK (revision, model); la **FK COMPUESTA** (revision, profile) impide mezclar perfiles —
  test negativo). La tarea `jobhunt.embedding.run_pending` embebe ofertas Y perfiles con el
  **MISMO backend por (name, version)** (exigencia de la rev. externa de A-06), resuelto
  perezoso una vez por modelo; el guard dim≠384 protege ambas familias.

**Auditoría Opus (workflow 9 agentes): 5 hallazgos → 2 confirmados (ambos P3 de COBERTURA —
el código salió limpio; 3 refutados con traza), corregidos:**
- Test combinado ofertas+perfiles en el MISMO pase: `embedded==1 ∧ profiles_embedded==1` con
  el encoder instanciado UNA sola vez (rama de reuso por cortocircuito ejercitada).
- Test del guard de dimensión con PERFIL pendiente: el rogue dim=512 queda fuera de
  `profiles_embedded` y sin filas en `profile_embeddings`.

**Verificado: 158/158 vía core-migrate** (sin BD 65 passed + 93 skipped): idempotencias de
consumer/perfil/revisión, vigente=última, solo-la-vigente se embebe, 2 perfiles E2E, FK
compuesta rechaza cross-profile, doble store y carrera optimista → 1 fila, dim guard en ambas
familias, backend compartido; perfiles/offer estables ×3.

**1ª revisión externa (REQUEST CHANGES, 2026-07-24) — 2 P1 + 1 P2, reproducidos. Corregidos:**
- **P1 "vigente = última por created_at" no representable**: una reversión A→B→A reutiliza la
  revisión inmutable A pero B seguía vigente; y dos guardados en una transacción comparten
  `created_at` (now() constante) con desempate UUID no determinista. **Fix (RATIFICADO por la
  revisora)**: `profile_revision_activations` (core0004, revisión NUEVA) — relación
  APPEND-ONLY con `seq` monotónico por perfil bajo lock del perfil; **vigente = max(seq)**;
  guardar contenido conocido lo RE-ACTIVA. §1 actualizado con la tabla ratificada.
- **P1 text_hash inerte en perfiles**: cambiar solo el salario re-encodeaba texto idéntico.
  **Fix**: `copy_profile_vectors_by_text` — el vector existente del MISMO (text_hash, model)
  se COPIA bajo la revisión vigente (incluso entre perfiles: mismo texto ⇒ mismo vector), y
  el lote restante se DEDUPLICA por text_hash (un encode por texto único, distribuido).
- **P2 vector de revisión sustituida**: snapshot R1 → llega R2 → el store persistía R1.
  **Fix**: `save_profile_revision` y el guard de embeddings comparten el LOCK ordenado por
  perfil (`FOR UPDATE`); el store revalida la vigencia bajo el lock y DESCARTA lo sustituido.
Regresiones: reversión re-activada (historial de 3 activaciones), misma-tx segunda vigente,
copia sin re-encode, dedup de lote entre perfiles, store de sustituida → 0.

**Re-verificado: 163/163 vía core-migrate** (158 + 5 regresiones); sin BD 65 passed + 98
skipped; perfiles estables ×3; BD en core0004.

**2ª revisión externa (REQUEST CHANGES, 2026-07-24) — 1 P1 + 2 P2. Corregidos:**
- **P1 core0004 sin backfill**: las revisiones pre-existentes quedaban sin activación → sin
  vigente, fuera del matching y del worker. **Fix**: backfill en la migración (activa
  preservando la ordenación antigua `(created_at, id)` con `row_number()`). core0004 se
  editó ANTES de su primer commit (rama privada, regla de env.py) con downgrade/upgrade
  explícito de la BD de dev. **Test REAL de migración**: `core0003 → seed → core0004` →
  activaciones (1,r1),(2,r2), vigente = r2, y el perfil vuelve al worker.
- **P2 índices**: `ix_pract_profile_seq (profile_id, seq DESC) INCLUDE (revision_id)` y
  `ix_profrev_text_hash_id (text_hash, id)` en core0004 (el max(seq) por perfil y el lateral
  por text_hash hacían Seq Scan; la reutilización escalaba lote × histórico).
- **P2 doc**: el resumen de A-07 aún definía vigente como (created_at, id) → corregido a
  última activación max(seq).

**Re-verificado: 164/164 vía core-migrate** (163 + test de migración); sin BD 65 passed +
99 skipped; perfiles estables ×3; índices verificados en catálogo.

**3ª revisión externa (REQUEST CHANGES, 2026-07-24) — 1 P1: el test de migración hacía
downgrade contra la BD COMPARTIDA de la suite (destruía reactivaciones históricas ajenas:
el backfill solo reconstruye una activación por revisión). Fix: el test corre sobre una
BD DESECHABLE (CREATE DATABASE única → bootstrap mínimo → core0003 → seed → head →
verificar → DROP ... WITH (FORCE)); verificada la repro de la revisora (historial A→B→A
ajeno con seq [1,2,3] INTACTO tras ejecutar el test). Final: 164/164; sin BD 65+99;
perfiles ×3.**

**4ª revisión externa (2026-07-24): APPROVE, confianza alta — sin P0/P1/P2 en `4e67912`;
verificación independiente: BD temporal única y sin residuos, centinela A→B→A intacto, BD
compartida en core0004. A-07 CERRADO. Final: 164/164.**

### A-08 ✅ — `match_evaluations` + `profile_vacancy_state` + feed (2026-07-24)
- **`matching.py`** (Fable): `eval_key` DETERMINISTA (hash de offer_revision +
  profile_revision + model + policy) — **el reintento no duplica** (UNIQUE(profile, vacancy,
  eval_key) + DO NOTHING, append-only, componentes como COLUMNAS, ADR-03).
  `evaluate_profile`: LOCK por perfil (FOR UPDATE, mismo protocolo que
  save_profile_revision — evaluaciones serializadas: current_eval jamás retrocede a una
  revisión vieja por carrera); candidatos = top-K por coseno pgvector (HNSW de la partición
  del modelo, `SET LOCAL hnsw.ef_search = max(limit, 40)` — sin truncado silencioso del
  ANN); score_final Fase A = coseno escalado 0..100 (NUMERIC(6,2); multi-factor/rerank =
  política versionada de Fase B, weights reservado). Estado ESTABLE: el matching solo mueve
  current_eval_id/updated_at — feedback/dismissed/saved/notes intocables.
- **Feed (DoD)**: evaluación VIGENTE + no-dismissed + vacante ACTIVA, keyset
  (score_final DESC, vacancy_id ASC) — con test de empate REAL (mismo texto ⇒ mismo vector
  ⇒ mismo score) y paginación sin repetir ni saltar. `set_dismissed`/`set_saved` solo tocan
  su columna. FK compuesta RESTRICT probada (la eval vigente no se poda).
- **Tarea `jobhunt.matching.run_profile`** (core.matching): modelos activos 384 × políticas
  activas en ORDEN determinista; **evaluador CANÓNICO** = primer combo que evalúa de verdad
  — solo él mueve current_eval_id, el resto corre en SOMBRA (append-only): con varios
  modelos el feed muestra SIEMPRE el mismo score. `not_found` sin retry (disciplina A-04).

**Auditoría Opus (workflow 17 agentes): 13 hallazgos → 9 confirmados (5 temas; 4 refutados
con traza), corregidos:**
- **P2 current_eval no determinista con ≥2 modelos 384 activos** (last-writer-wins con orden
  de heap) + **P2 no monótono bajo evaluaciones concurrentes** → evaluador canónico + lock
  por perfil + ORDER BY en active_models/políticas. Regresión: 2 modelos con backends
  distintos → current SIEMPRE del canónico (×2 runs), la sombra apendea.
- **P2 hnsw.ef_search=40 truncaba el top-K en silencio** → SET LOCAL; regresión con 45
  vacantes activas → evaluated 45.
- **P3**: clave del dict de resultados colapsaba modelos same-name (→ incluye versión);
  feed(limit=0) IndexError (→ guard); tests de política inactiva y de re-revisión del
  perfil (sin_vector → embed → append + current avanza) añadidos.

**Verificado: 179/179 vía core-migrate** (sin BD 67 passed + 112 skipped): idempotencia del
reintento, estado preservado, feed (orden/filtros/keyset/empate real/limit 0), RESTRICT,
sin_vector, canónico multi-modelo ×2 runs, ef_search, política inactiva, task e2e +
not_found; matching estable ×3.

**1ª revisión externa (REQUEST CHANGES, 2026-07-24) — 2 P1 + 1 P2, reproducidos. Corregidos:**
- **P1 canónico consumido por 'ok/evaluated=0'**: el modelo A con vector de perfil pero sin
  embeddings de ofertas consumía el evaluador canónico → 0 estados y feed VACÍO con B en
  sombra. **Fix**: `moved_current` explícito en el resultado — el canónico solo se consume
  cuando el combo DE VERDAD movió el estado. Regresión: A vaciado de vectores de ofertas →
  B mueve, feed poblado.
- **P1 inanición del scan ANN filtrado**: embeddings HISTÓRICOS/huérfanos más cercanos
  consumían el HNSW completo (con 20k huérfanos y ef_search: 0 candidatos pese a 1.000
  activas). **Fix en tres capas**: `hnsw.iterative_scan='strict_order'` (pgvector 0.8.1) +
  tope `hnsw.max_scan_tuples` explícito + **FALLBACK EXACTO** (sin índice) cuando el top-K
  queda corto. Regresiones: 200 huérfanos máximamente cercanos → 30 activas evaluadas;
  EXPLAIN valida el camino del índice; tope forzado a 50 → el exacto llena el top-K.
- **P2 retroceso temporal entre tx solapadas**: now() = inicio de tx — una tx vieja que
  escribía tarde hacía retroceder saved_at/updated_at. **Fix**: `clock_timestamp()` +
  `GREATEST(updated_at, clock_timestamp())` en los tres upserts de estado (inserción
  incluida). Regresión con tx solapadas: updated_at/saved_at ≥ dismissed_at.

**Re-verificado: 183/183 vía core-migrate** (179 + 4 regresiones); sin BD 67 passed + 116
skipped; matching estable ×3.

**P2s residuales del APPROVE de A-08 (corregidos en el acto):** limit validado (>=1) y
ef_search acotado a [40..1000] (rango del GUC; limit>1000 lo cubre el fallback) + el fallback
exacto solo dispara si el ANN devuelve menos que el OBJETIVO REAL (conteo acotado de
elegibles: un corpus menor que limit ya no paga doble búsqueda). 185/185. **A-08 CERRADO.**

### A-09 ✅ — API `/v1` read-only multi-tenant (2026-07-24)
- **`credentials.py`**: token `key_id.secret` (sha256 en BD; `compare_digest` + comparación
  fantasma — el timing no revela si el key_id existe); 401 INDISTINGUIBLE por causa
  (formato/secreto/revocada/caducada/consumer inactivo).
- **`api/deps.py`**: `ApiError` con el sobre del contrato {code, message, details};
  `require_scope` (matriz ruta→scope §2, sin scope → 403 con required_scope);
  ownership tenant: cross-tenant → **404 idéntico al ausente**.
- **`api/v1.py`** (3 endpoints, queries por LOTES O(1) por página):
  `GET /v1/vacancies/{id}` (corpus GLOBAL, solo ACTIVAS y presentables — archivada/fundida/
  sin canónica → 404) con DTO multi-listing §2 (primary_listing 6 campos + listings[] +
  translations=[] sin escritor en Fase A); `GET /v1/profiles/{pid}` (tenant; current_revision
  null si no hay); `GET /v1/profiles/{pid}/matches` (feed A-08 con DTO match completo,
  cursor keyset OPACO base64(score_final|vacancy_id), limit 1..100). **ETag** = sha256 de la
  representación canónica → If-None-Match → 304. `api/schemas.py` = esquema FORMAL Pydantic
  (OpenAPI en /v1/openapi.json).

**Auditoría Opus (workflow 15 agentes): 11 hallazgos → 8 confirmados (5 temas; 3 refutados
con traza), corregidos:**
- **P2 sobre del contrato roto en malformados**: el 422 por defecto de FastAPI ({'detail':…})
  rompía a un BFF que parsea resp.json()['code'] → handler de RequestValidationError con
  {code:'invalid_request',…} (400 se suma al catálogo para input malformado, documentado) +
  handler de 500 con el mismo sobre (detalle solo al log).
- **P2/P3 cursor con Decimals NO finitos** (NaN = mayor numeric en PG → primera página en
  bucle; -Infinity vacía la paginación): `is_finite()` en decode_cursor → 400.
- **P3**: ErrorDTO documentado en OpenAPI (responses del router); tests añadidos: vacante
  FUNDIDA → 404, perfil sin revisión (current_revision null + ETag), ETag del perfil cambia
  con revisión nueva. (Extra cazado por la suite: el test de merge filtraba su vacante
  ganadora — registrada en el fixture y residuos purgados; 3 suites consecutivas limpias.)

**Verificado: 197/197 vía core-migrate ×3 consecutivas** (sin BD 70 passed + 127 skipped):
catálogo negativo de credenciales (401 uniforme), matriz de scopes (403), cross-tenant 404
indistinguible + corpus global, DTOs §2 completos, ETag/304/cambio, paginación keyset por
API con cursor opaco, errores 400 con sobre del contrato, OpenAPI con ErrorDTO; API ×3.

**1ª revisión externa (REQUEST CHANGES, 2026-07-24) — 3 P1 + 4 P2, reproducidos. Corregidos:**
- **P1 ownership en Python + TOCTOU**: el consumer se comparaba tras leer, y ownership/
  contenido iban en sentencias distintas (READ COMMITTED) — una reasignación de tenant a
  mitad de request sirvió el secreto del nuevo tenant al antiguo. **Fix**: el filtro por
  `consumer_id` va EN la query (§2) — perfil = identidad+revisión vigente en UNA sentencia
  (mismo snapshot); el feed acepta `consumer_id` y lo filtra en SQL; la query de
  evaluaciones también filtra por profile. Regresión: reasignación + revisión 'secreta' →
  404 al antiguo, 200 al nuevo, feed directo con consumer viejo = 0 filas.
- **P1 /matches sin ETag** → la página es una representación: `_with_etag` (estable, 304,
  cambia con dismiss) + 304 documentado.
- **P1 HTTPException de Starlette fuera del sobre** (ruta inexistente → {'detail':…}) →
  handler que mapea 404/405/429 al sobre del contrato. Tests de 404 de router y 405.
- **P2 timing del 401**: la caducada ejecutaba una 2ª query (295µs vs 149µs) → UNA query
  con la expiración evaluada en SQL, hash candidato calculado una vez y compare_digest
  SIEMPRE ejecutado (dummy precomputado).
- **P2 If-None-Match literal** → semántica HTTP real: comodín `*`, W/ débil y listas.
- **P2 OpenAPI infiel** → HTTPBearer como dependencia (securityScheme + security por
  operación), Query(ge/le) declarado (el 400 'invalid_request' sustituye a invalid_limit),
  openapi custom: 422→400 con ErrorDTO y 500 documentado; test que inspecciona cada
  operación (security, 400/401/403/404/304/500, sin 422).
- **P2 falso verde del fallback**: `evaluated==2` pasaba con 1 o 2 búsquedas → contador de
  ejecuciones REALES de CANDIDATES_SQL (evento before_cursor_execute): corpus < limit ⇒
  exactamente UNA pasada; primera pasada corta (< objetivo) ⇒ exactamente DOS. (A esta
  escala el planner usa el plan exacto desde vacancies — la inanición FÍSICA del HNSW a
  escala la validó la revisora con 20k huérfanos.)

**Re-verificado: 201/201 vía core-migrate ×2 consecutivas** (sin BD 70 passed + 131
skipped); API/matching ×3.

### A-10 ✅ — Entrega `match.evaluated` outbox→inbox por consumidor (2026-07-24)
- **Emisión (ADR-05)**: en la MISMA transacción de `evaluate_profile`, SOLO para evaluaciones
  NUEVAS: `integration_outbox` con **event_id determinista** uuid5(ns, 'match.evaluated:'+
  eval_key) + DO NOTHING (re-emisión imposible) y payload = SOLO IDs (+subject); la entrega
  por destino (`integration_outbox_deliveries`) apunta al **BFF del consumidor del perfil**
  (§3), pending.
- **Despacho (`delivery.py` + tarea `jobhunt.delivery.dispatch_outbox`, cola core.default)**:
  claim con `FOR UPDATE SKIP LOCKED` + **lease** (attempts+1 y lease en el MISMO claim; un
  inflight con lease caducado se RE-reclama — at-least-once real); transporte FUERA de la tx
  del claim; marks por lotes; backoff exponencial (base 60s, cap 1h); **DEAD-LETTER con
  ALERTA** al agotar MAX_ATTEMPTS (DoD). **Transporte INYECTABLE** (`set_transport`; el real
  HTTP al inbox del BFF llega con el cutover de Fase C) — sin transporte, release a pending
  SIN consumir intentos. El **inbox vive en la BD del consumidor** (ADR-06): en tests, un
  inbox simulado con dedup por (consumer_id, event_id) demuestra el consumo idempotente.

**Auditoría Opus (workflow 10 agentes): 6 hallazgos → 4 confirmados + 1 sin verificar
(caída de red del verificador; evaluado a mano y añadido), 2 refutados. Corregidos:**
- **P2 destination VARCHAR(60) < consumers.name VARCHAR(100)**: un consumer de 61-100 chars
  reventaba el INSERT de la entrega y — misma tx — revertía la evaluación COMPLETA (matching
  sin persistir jamás para ese tenant) → **core0005** alinea a 100. Regresión con nombre de
  62 chars.
- **P2 marks sin FENCING**: un claim superado (lease caducado y re-reclamado) podía pisar el
  estado terminal del nuevo dueño (delivered→pending/dead, alerta falsa) → el lease es el
  TOKEN de fencing (timestamp único por lote calculado en BD): mark_delivered/mark_failed/
  release solo escriben `WHERE state='inflight' AND lease = :suyo` (+ack_at limpiado en
  retry). Regresión: mark tardío del claim viejo no toca un delivered.
- **P2 tests**: routing a dos consumidores DISTINTOS (cada BFF recibe exactamente sus
  event_id) + forma utilizable del evento transportado (hallazgo no verificado, cubierto).

**Verificado: 210/210 vía core-migrate ×2** (sin BD 70 passed + 140 skipped): emisión misma-tx
determinista sin re-emisión, E2E pending→delivered+ack, re-entrega con inbox que deduplica
(at-least-once + consumo idempotente), backoff creciente → dead con alerta (y dead no se
reintenta), lease caducado re-reclamado, sin transporte sin quemar intentos, fencing,
routing multi-consumidor; delivery ×3. Fixtures de matching/api limpian el outbox emitido.

**2ª revisión Opus post-commit (pedida por el usuario; workflow 11 agentes sobre `42e3e06`):
8 hallazgos → 5 confirmados (3 temas; 3 refutados con traza). Corregidos:**
- **P2 alerta/contadores no gobernados por el fence**: la ALERTA de dead-letter se emitía por
  la clasificación LOCAL del claim viejo (página falsa por un evento que otro dispatcher SÍ
  entregó) y delivered/dead contaban intención → los marks usan `UPDATE … FROM unnest …
  RETURNING`: la alerta y los contadores salen SOLO de transiciones REALES; el resultado del
  dispatcher añade `fenced_out`. Regresión: mark tardío con MAX_ATTEMPTS=1 → {'dead': 0},
  sin "DEAD-LETTER" en el log, delivered intacto.
- **P3 attempts inflado sin transporte** (release + retry de la task sumaba intentos jamás
  transportados) → solución de RAÍZ: sin transporte NO se reclama nada (release eliminado).
- **P2 sin índices para claim/lag** → **core0006**: índices PARCIALES por estado
  (pending(next_attempt_at), inflight(lease)) + `delivery.stats()` (conteos por estado +
  edad del pending más antiguo — el "monitorizar lag + dead-letter" de ADR-06/GATE A).

**Final A-10: 211/211 vía core-migrate ×2** (sin BD 70 passed + 141 skipped); delivery ×3;
BD en core0006.

### A-11 ✅ — `harvest_runs`/`source_harvest_runs` idempotentes (2026-07-24)
- **`runs.py`**: idempotencia SIN run_key en el esquema ratificado — **id determinista**
  `uuid5(RUNS_NAMESPACE, run_key)`; `start_run` con ON CONFLICT **RE-abre** (status='running',
  finished_at=NULL); `claim_scope_run` **ATÓMICO** (INSERT DO NOTHING RETURNING como claim
  exclusivo, o UPDATE condicional que solo re-arma 'error' terminado o colgado con
  **SCOPE_LEASE_S=900 vencido** — dos workers solapados no ganan el mismo scope);
  `finish_run` cierra huérfanos de scopes DESHABILITADOS como 'skipped', agrega SOLO
  habilitados y devuelve **"running" SIN escribir** si hay un running con lease vigente
  (otro worker legítimo — cierra el último).
- **Tarea `jobhunt.harvest.run_all`** (`tasks/harvest.py`): start_run → scopes habilitados →
  claim/skip/ejecutar `_run_scope_impl`/finish_scope_run → finish_run; el retry del MISMO
  run_key reutiliza la fila y SALTA lo ya hecho (DoD: reintento no duplica).

**3 pasadas Opus** (pedidas por el usuario): 1ª — 5 confirmados corregidos (claim no atómico
read-modify-write, start_run sin re-abrir mentía al monitor, scope deshabilitado envenenaba
el run para siempre + 2 huecos de test); 2ª — 1 confirmado corregido (running con lease
vigente de un worker solapado agregado como 'error' falso → finish_run devuelve "running"
sin tocar harvest_runs); 3ª — **0 confirmados** (el P2 "run atascado si el último worker
crashea" refutado: `task_acks_late=True` + prefetch 1 re-entregan la tarea y el reintento
cierra el run). Tests: 9 de integración (determinismo, reintento salta hechos/re-ejecuta
errores, colgado re-armado, claim concurrente 1 ganador, deshabilitado entre intentos,
re-agregado a ok, run abierto con worker en vuelo que converge).

### A-12 ✅ — Ensayo de migración sobre copia (2026-07-24)
- **`test_integration_migration_rehearsal.py`**: BD DESECHABLE (CREATE DATABASE + extensión
  vector + bootstrap de esquema) → `upgrade head` → grafo representativo sembrado **VÍA
  SERVICIOS REALES** (sink de cosecha, perfiles/revisiones, modelo+política, vectores,
  evaluate_profile con outbox) verificando ≥1 fila en 9 tablas → **frontera de datos de
  core0005 EJERCITADA**: entrega con destination de 61 chars ⇒ `downgrade core0004` FALLA
  CONTROLADO (returncode≠0, "character varying(60)" en la salida — el ancho NO trunca en
  silencio), se retira la fila y el ciclo continúa → **downgrade ESCALONADO**
  core0005→core0004→core0003→core0002→core0001→base → `upgrade head` → verificación
  (version=core0006, smoke insert, los 5 índices nombrados existen). Engine con
  `server_settings.search_path` (NullPool renueva conexiones por commit). DROP DATABASE
  WITH (FORCE) en finally.
- Ruta ABSOLUTA al `alembic.ini` vía `Path(__file__)` (independiente del CWD) — también
  aplicada al test de migración de A-07.

**2 pasadas Opus**: 1ª — 2 confirmados corregidos (P2 la frontera de core0005 nunca se
ejercitaba — un destination corto pasaba trivialmente el downgrade; P3 ini relativo acoplado
al CWD); 2ª — **0 hallazgos** (lista vacía deliberada tras revisión con lectura de código).
DoD: down-migrations válidas ✅ sobre copia, nunca sobre la BD de desarrollo.

### GATE A ✅ — Ensayo superado (2026-07-24)
- **`test_gate_a.py`**: los 7 criterios de la puerta en UN test end-to-end, cada uno con assert
  explícito: cosecha REAL vía `run_all_task` con `httpx.MockTransport` acotado (2 scopes con
  keyword tech/no-tech) → attach cross-source **BILATERAL** ((encarnaciones, fuentes,
  vacantes) == (2,2,1)) → embeddings (2 canónicas + 2 perfiles) → matching → feed vía API real
  con tenant (cross → 404) → dismiss estable tras re-evaluación → archivada fuera del feed →
  entrega con re-entrega **DEMOSTRADA** (2º dispatch: delivered≥1, transporte re-invocado, filas
  del inbox sin crecer) → **RECICLADO** (bloque 4b, al final para no perturbar conteos previos):
  slot arbeitnow re-cosechado con empresa distinta y URL nueva ⇒ encarnación vieja cerrada,
  vacante NUEVA, primary de la compartida reasignado a la activa de otherboard y canónica
  reconstruida desde ese primary con título DISCRIMINANTE ("Python Engineer" ≠ el de la
  revisión vieja — un puntero stale no pasa) → aislamiento (broker redis-core, colas core.*).
- Extractor+normalizador de `otherboard` vía `monkeypatch.setitem` (retirada automática).

**3 pasadas Opus**: 1ª — 5 confirmados corregidos (attach unilateral que pasaba con el listing
del 2º board perdido; dedup de re-entrega no demostrado — el 2º dispatch podía no entregar nada;
reciclado prometido en docstring pero jamás ejercitado ×2; + duplicado); 2ª — 1 tema confirmado
corregido (assert de canónica reconstruida no discriminaba: título idéntico en la revisión vieja
y la nueva — un stale pointer daba el mismo verde); 3ª — **0 hallazgos**. Suite 222/222.

### Auditoría final A-09→GATE A ✅ (2026-07-24)
Workflow Opus de cierre del tramo (5 dimensiones: concurrencia entre módulos, seguridad API,
eficiencia, contratos entre módulos, residuos) + verificación adversarial: 4 confirmados
corregidos, resto refutado con traza.
- **P2 seq scan en cada lectura /v1**: la query de listings activos por vacante
  (`_vacancy_dtos`, en get_vacancy y CADA página del feed) filtraba
  `source_listing_incarnations` por vacancy_id sin ningún índice que lidere por esa columna
  (la FK no crea índice) → **core0007**: índice PARCIAL `ix_incarnation_vacancy_active
  (vacancy_id) WHERE ended_at IS NULL`; el ensayo A-12 incorpora la revisión al ciclo
  (downgrade desde core0007, 6 índices nombrados verificados).
- **P3 cursor con magnitud desbordante → 500**: `decode_cursor` solo rechazaba no-finitos;
  un exponente ≥262144 lanza DataError del driver (→ 500 internal_error en vez del 400
  invalid_cursor del contrato) y ~[131072, 262143] se codificaba EN SILENCIO como 0
  (paginación corrupta) → cota `CURSOR_SCORE_BOUND = 1E6` (score_final es NUMERIC(6,2));
  regresión con 1E262144 y 1E200000.
- **P3 ×2 código muerto**: import `profiles` en api/v1.py (residuo del fix anti-TOCTOU) y
  variable `vec` en el seed del ensayo A-12 (residuo del formateo movido a embeddings).
Suite 222/222; BD dev en core0007.

### Refactorización Fase A ✅ (2026-07-24) — FASE A TERMINADA
Behavior-preserving, acotada a la deuda REAL medida:
- **`tests/dbcleanup.py`** (nuevo): la limpieza FK-safe duplicada en 10 fixtures (144
  `DELETE FROM` repartidos — añadir una tabla obligaba a editar 10 ficheros) pasa a 5
  funciones de purga compartidas (runs, grafo de consumer, grafo de fuentes con
  `extra_vac_ids`, políticas, modelo+partición) derivadas como UNIÓN exacta de los fixtures;
  11 ficheros de test reescritos SOLO en su bloque cleanup: **-897/+69 líneas**.
- **`tests/alembic_runner.py`** (nuevo): `run_alembic()` único (ini absoluto,
  CORE_DATABASE_URL en env) para el ensayo A-12 y el test de migración de A-07.
- **Decisión documentada — `sink.py` (1235 líneas) NO se trocea**: los seams de métodos son
  load-bearing para los tests de concurrencia (PausingSink los sobreescribe), la clase es UN
  pipeline con SRP a nivel de método y es el fichero más auditado del repo — trocearlo sería
  churn de alto riesgo sin ganancia (YAGNI).
Producción intacta. Suite 222/222 en DOS ejecuciones consecutivas (la 2ª caza huecos de
limpieza). **Auditoría Opus de la refactorización (3 dimensiones: pérdida de cobertura
fixture a fixture vs HEAD, corrección de dbcleanup contra las FKs reales, equivalencia del
runner): 0 hallazgos.**

🏁 **FASE A COMPLETA**: A-01..A-12 ✅ + GATE A ensayado ✅ + auditoría final del tramo ✅ +
refactorización auditada ✅. BD dev en core0007 (Fase B la avanza: §25). Siguiente: Fase B — estado de ejecución en §25.

---

## 25. Estado de EJECUCIÓN — FASE B (sombra)

> Ciclo por etapa (orden del propietario, 2026-07-24): Fable implementa → análisis Opus 1 →
> correcciones → commit → análisis Opus 2 → correcciones → commit; ninguna etapa nueva hasta
> cerrar ese ciclo. Contrato ejecutable: `CONTRATOS_FASE_B.md` (registro por-ticket ALLÍ; este
> bloque es el índice).

- **B-CC ✅ (2026-07-24)** — `CONTRATOS_FASE_B.md` v1.2 con doble análisis completo (1º: 18
  confirmados → 12 temas corregidos; 2º: 5 → 4 corregidos). Commits docs `fc4d18b` + `8095cee`.
- **B-01 DESBLOQUEADO (2026-07-24)** — el propietario confirmó el cambio de compose
  (wal_level=logical + imagen postgres con wal2json) y la ventana de reinicio del Postgres
  compartido (`de66b94`). Umbrales §6: ratificación pendiente (antes del PRIMER ciclo).
- **B-03 ✅ (2026-07-24)** — set etiquetado: `core0008a` + `shadow/labels.py` + 8 tests
  (commit SwissJob `deb5a81`, suite 230/230 ×2; doble análisis Opus 0+0). Curación real
  pendiente del arranque de la sombra.
- **B-01 ✅ (2026-07-25)** — infra CDC completa (commits `31507a6`+`71d68f2`, doble análisis 6+5 corregidos, 244/244 ×2, core-capture healthy streaming; SOLO LOCAL): imagen postgres+wal2json, wal_level=logical con reinicio en
  ventana, rol `jobhunt_capture`, GRANTs RO enumerados + `_verify_isolation` actualizado,
  `core0008b` (staging/estado/lotes) y servicio `core-capture` (slot→snapshot→backfill→replay,
  ack tras commit). Solo compose de DEV; alinear prod/QNAP = decisión posterior.
- **B-02 ✅ (2026-07-25)** — proyector completo (commits `52679a9`+`2b8ab38`, doble análisis 4+2 corregidos, 262/262 ×2).
- **B-04 ✅ (2026-07-25)** — métricas por ciclo + purga (doble análisis 5+1 corregidos, 283/283 ×2).
- **B-05 ✅ (2026-07-25)** — harness completo (doble análisis 2+2 corregidos, 291/291; beat embebido).

🏁 **FASE B COMPLETA EN CÓDIGO (2026-07-25)**: B-CC, B-01..B-05 ✅ con doble análisis Opus por etapa. Sets etiquetados **YA congelados** (3, el 2026-07-28) y **6 ciclos sellados** hasta el 2026-07-30; siguiente: **recuperar calidad** (el último ciclo salió ROJO) y encadenar los **7 ciclos LOCALES en verde** del 🚦 GATE-SOMBRA (beat activo: sampler+salud 5 min, ciclo 06:05). Cifras verificadas en `DEUDA_TECNICA.md` §2.2.

### Revisión EXTERNA de la Fase B + paso de fase (2026-07-27/28)
Primera revisión externa desde A-09 (tramo `d8fc653..HEAD`): **REQUEST CHANGES con 8
hallazgos (4 P1 + 4 P2), TODOS confirmados y corregidos** con regresión del escenario del
revisor (commits `06ea66c` + `fc98f0d` + infra `21252ef`): suite aislada en BD desechable de
sesión, gate `labels_ready` (DoD del oráculo como precondición), run_cycle sin medir en
caliente, ciclos sellados inmutables, cadencias de proyector/entrega cada 5 min, entrega
sombra REAL (`core0009`: `shadow_inbox` — 100 eventos delivered verificados), lag del outbox
por edad de evento + gate `outbox_dead`, intención de lote durable, heartbeat de liveness.
**Revisión de paso de fase: NO-GO a Fase C** (correcto — evidencia operativa): workers legacy
recuperados con restart (7 días caídos por DNS sin política), cosecha reparada (2 migraciones
legacy pendientes), oráculo congelado (2 sets contables + persona de evaluación vía API
legacy, `b513c55`+`d6cb4d1`), exclusión de inactivos compartida proyector↔métricas.
**Paquete de calidad del matching CERRADO 2026-07-28**: el diagnóstico confirmó que ANN y
exacto coincidían; el fallo era la representación truncada a 128 tokens (skills/tags al final)
y un top-K insuficiente, no CDC, índice ni harness. `core0010` versiona la receta como parte
del modelo y activa `role_composite_v2` (un vector normalizado 60% fields-first + 40% título),
con top-K=1800 y feed canónico exacto. Backfill 5261/5261 + 2/2; nDCG@10 real `0.611221` y
`0.619797` (legacy `0.199351`/`0.173711`); falsos negativos 0 en ambos; outbox 8234/8234,
sin fallos/dead; suite `312/312 ×2`.
**Estado real del gate (§6)**: calidad e infra ya satisfacen sus umbrales en preview; el NO-GO
continúa por acumulación natural (`labels_ready`: pares dedup mapeables) y después exige 7

### A.SEAM — costura por capacidad en el BFF SwissJob (track paralelo, arrancado 2026-07-28)
Reprioridad del propietario: validaciones del gate (dedup semántico nivel 3, NAS —paquete
INERTE commiteado `31aafdc`—, racha 7/7) AL FINAL; el proyecto avanza por las costuras.
- **Catálogo ✅ (2026-07-28)** — `jobhunt_routing` exacta + CatalogPort local(verbatim)/core
  con matriz de escritor; commits `6a37217`+`efe5d10`; doble análisis Opus 3+1 corregidos
  (destacado: el MD5 legacy parseaba como UUID y mataba el cortocircuito; severidades del
  canary separadas). Suites legacy 790 + core 312.
- **Matching ✅** y **Perfiles ✅ (2026-07-29**, `c501704`, doble análisis 0+0**)**. Pendientes: candidaturas, documentos, colegios;
  schedulers conscientes del routing; credencial real del consumer; costura ReactPortfolio.
- **Matching ✅ (2026-07-28)** — lecturas core (ETag/cursor/identidades) con escrituras SIEMPRE
  locales hasta Fase C; criterio unificador "nada visible no-accionable, ningún estado local
  inaccesible" aplicado simétricamente (explícito+implícito); motor idempotente; exclusión por
  accionabilidad medida. Commits `4ffc19b`+su fix; doble análisis Opus 4+3 corregidos; bonus:
  bug latente del legacy (señales implícitas perdidas por mutación JSONB) descubierto y
  corregido. Suites legacy 831 + core 312.
- **Candidaturas+Documentos+Colegios ✅ (2026-07-29**, `1c79a4d`, doble análisis 2+0**)** —
  variante ligera (core=Unsupported sin cliente, cero peticiones por construcción; estado del
  escritor local accesible en TODOS los modos; co-propiedad de match_results y cota de flip
  Fase C registradas contra el split-brain).

🏁 **TRACK A.SEAM COMPLETO (2026-07-29)**: las 6 capacidades con costura, doble análisis Opus
por etapa (3+1, 4+3, 0+0, 2+0) y contract tests. Suites finales: legacy 923/923, core 312/312.
Pendientes registrados: schedulers conscientes del routing, credencial real del consumer,
frontend, costura ReactPortfolio (portfolio_backend en crash-loop por diagnosticar). Pendiente
de análisis EXTERNO (prompt entregado al propietario).

### Revisión EXTERNA del track A.SEAM (2026-07-29) — REQUEST CHANGES → CERRADA
4 hallazgos (1 P1 + 3 P2), TODOS confirmados y corregidos con regresión (commit SwissJob del
cierre): P1 el enrolamiento era INOPERABLE (ownership cross-tenant) → el consumer del BFF ES
`swissjob-shadow` con credencial real emitida (E2E 200 verificado); P2 fallback real ante
payloads 200 inválidos; P2 alias legacy no-primary conserva su MD5 (external_id en TODOS los
listings del DTO core); P2 implícito concurrente atómico (concatenación JSONB). Verificación
DEFINITIVA en solitario: legacy 945/945, core 313/313 (los fallos de pasadas mixtas eran
deadlocks de TRUNCATE concurrente en la BD de test — limitación de entorno documentada:
suites SIEMPRE en serie). Áreas declaradas limpias por el revisor: routing, escrituras
locales, co-propiedad, round-trip MD5/UUID, frontera §21, PII, canary, migraciones.

### Fase C — ejecución (índice; registro por-ticket en CONTRATOS_FASE_C.md)
- **C-CC ✅ (2026-07-29)** — contrato v1.1 con doble análisis (10+2 resueltos; commits docs
  `c5488fc`+`2c264df`+`0ab98cd`).
- **C-PRE ✅ (2026-07-29)** — piloto restaurado (repo portfolio `e8c44d9`+fix): volumen propio
  (el local era el cluster de rss_reader — durables reales SOLO en el NAS), puertos 8004/5436,
  cadena Alembic reparada Y gobernando el runtime (migración-primero), ngrok tras perfil.
  Doble análisis 4+3 corregidos. Pendiente propietario: name: en compose de Novafeed.
- **C-ESQ ✅ (2026-07-29)** — core0011 (applications, application_status_events, saved_searches,
  idempotency_records según §1/§4); commit `060fca2`+`6ba82b2`; doble análisis 0+2 (huecos de
  registro de erase GDPR, con dueño). Suite core 322/322 ×2, BD dev en core0011.
- **C-1 ✅ COMPLETO (2026-07-30)** — BFF por capacidad del portfolio, LAS 5 verticales: catálogo y matching (pesadas, feed del /v1), candidaturas y saved-searches (ligeras: Unsupported total + estado local en todos los modos hasta C-4), auth/analytics/chat/SSE fuera de la costura. jobhunt_routing propio + resolve_mode resiliente; criterio A.SEAM (nunca un 200 incorrecto, nunca estado local inaccesible). Commits portfolio 5532f38→a563575 (+ fix precedencia); doble análisis Opus por vertical; suite backend 932. Siguiente: C-2 (runbook + kill-switch).
- (histórico) C-1 vertical CATÁLOGO (jobhunt_routing propio + CatalogPort local-verbatim/core sobre el feed de C-API-R; resiliencia ante BD caída que honra el modo autoritativo; sobre de paginación unificado local/core). Commits portfolio `5532f38`+`dbe63b2`+`006ae73`; doble análisis Opus 2+2 corregidos; suite backend 873. Siguiente en el BFF: matching/applications; luego C-2.
- **C-API-W ✅ (2026-07-30)** — escritura /v1: Idempotency-Key (idempotency_records, una tx atómica) + PUT /v1/profiles con profiles:write/If-Match-fuerte/412; obligaciones GDPR de C-ESQ cableadas; purga en el beat del core. Commits `63a78af`+`2bc1cd7`+cierre; doble análisis 1+1. Suite 338/338 ×2.
- **C-API-R ✅ (2026-07-30)** — GET /v1/vacancies (feed keyset, cursor opaco, q mínimo, core0012); commit `86b1449`+`403f8b9`; doble análisis 0+0 (cota de q registrada). Suite core 328/328 ×2.
- **C-0 ✅ (2026-07-29)** — seguridad del piloto: consumer `portfolio` + credencial core (E2E
  200/404-cross-tenant/401), ngrok tras perfil, inspector 4040 a loopback, host-header rewrite;
  commits portfolio `7221b40`+`5d7bd43`; doble análisis 2 confirmados (seguridad del perfil
  público) corregidos. Pendiente propietario: ALLOWED_HOSTS en .env real.
- ⏸️ **PAUSA (orden del propietario 2026-07-29)**: tras cerrar C-0. Siguiente al reanudar:
  **C-API-R** (feed de catálogo del /v1) → C-API-W → C-1 → C-2 → C-3 → C-4 → C-6. **← toda la
  secuencia COMPLETADA (ver abajo).**
- **C-2 ✅ · C-3 ✅ · C-4 ✅ · C-6 ✅** — reanudada y cerrada la secuencia. C-4 con **división de
  alcance C-4/§4** (2026-08-02, ver [[division-alcance-c4-ensayo4]] y CONTRATOS fila C-4). C-6
  (verificador GATE C) aprobado en revisión externa (commits portfolio `4ca4e78`→`ea217af`,
  docs `01fdd91`+`07041f3`); evalúa el routing de TODOS los perfiles + allowlist de escritor local.
- 🏁 **CONSTRUCCIÓN de la Fase C COMPLETA (2026-08-02)**: no queda ningún ticket de construcción
  (C-5 diferido salvo evidencia). Lo que resta está **gated al NAS** (flip, racha GATE-SOMBRA 7/7,
  ejecución del ensayo §4 sobre datos reales).
- 🔨 **§4-LOCAL — decisión POR DELEGACIÓN (2026-08-02)**: el propietario aplaza el NAS "posiblemente
  hasta el final del proyecto" y delega ejecutar el trabajo NO-NAS más óptimo. Se adelanta el
  **CÓDIGO** de los 4 entregables del ensayo §4 (RUNBOOK §4), probado en LOCAL con fixtures
  sintéticos sobre la misma vía de síntesis de C-4; SOLO la ejecución sobre datos reales del NAS
  queda gated. Los 4: (1) **ledger del sink** por entrada (created/reused/quarantine+razón+vacancy_id);
  (2) **procedencia EXACTA** (snapshot antes/después de la fuente single-writer `portfolio-import` +
  RETURNING en los durables — NO el inventario scopeado); (3) **verificador estructural INDEPENDIENTE**
  (distingue cuarentena legítima de listing perdido usando el ledger); (4) **script de borrado
  FK-safe** (orden child→parent, ciclo `vacancies.current_offer_revision_id`, abort-on-RESTRICT).
  Diseño clave: todo dentro de `import_portfolio*` — NO se toca el `RawListingSink` compartido (23
  fuentes vivas). Mismo ciclo Fable/doble-Opus/commits por parte; revisión externa al terminar.
- ✅ **§4-LOCAL APROBADO (2026-08-04)**: los 4 entregables cerrados en `SwissJob/jobhunt_core`
  (`import_portfolio_ledger.py` · `import_portfolio_provenance.py` · `import_portfolio_verify.py` ·
  `import_portfolio_rollback.py` + core0014/0015/0016 del lifecycle del manifiesto), **suite core
  419/419**, doble análisis por parte. **Revisión externa: APPROVE tras 9 rondas iterativas** (cada
  hallazgo con regresión + verificación adversarial multi-agente; convergencia). Bugs reales
  cazados: crash con URL surrogate, TypeError del verificador, scope-change de dedup_candidates,
  tóxico-titulado ganando consolidación, disciplina Alembic (no reescribir migraciones publicadas),
  validación fail-closed del rollback, CASCADE de eventos ajenos (r7), falso verde por verdict
  estructural no propagado (r8), razón de cuarentena dependiente del orden (r8), completitud del
  verificador unidireccional (r9). Commits `88d9adc`→`92169ea` (APPROVE). Detalle en
  [[division-alcance-c4-ensayo4]] y RUNBOOK §4 (split §4-LOCAL/§4-REAL). Lo que resta es §4-REAL
  (ejecución sobre datos reales del NAS + oráculo plenamente independiente vía ledger del sink) —
  gated.
- ✅ **REVISIÓN INTEGRAL DEL MOTOR APROBADA (2026-08-05)**: revisión externa adversarial de TODO
  `jobhunt_core` (Fase A→§4-LOCAL, legacy excluido — se retira en Fase F), **APPROVE tras 5 rondas**
  con convergencia 12 → 4 → 2 → 1 → 0 hallazgos. **Suite core 439/439**, head Alembic `core0017`.
  Los 19 defectos se corrigieron con regresión dirigida; los de las rondas 1–3 además con
  verificación adversarial multi-agente (que refutó una primera versión del fix de r3). Categorías
  cazadas: falsos verdes de gate (`labels_ready` contaba sets, no perfiles medidos, y bajo READ
  COMMITTED podía leer un snapshot distinto del medido), **fencing del lease** (un worker desahuciado
  pisaba cursor y `consecutive_failures` del vigente — cerrado con `claim_token` en el camino con
  token y con el lock de `harvest_scopes` en el camino sin token, r4), readiness del CDC ciega a
  columnas contractuales ausentes (`jobs.source` → drop silencioso de vacantes nuevas), recuperación
  del proyector limitada a perfiles sombra, fronteras de datos externos (Arbeitnow, backend de
  embeddings) y matching repetido por lote. Commits `523dcf7`→`298a09f` en SwissJob; docs `0ce14e3`,
  `65e6928`. Detalle y RESIDUALES conocidos en [[revision-integral-jobhunt-core]].
  **Precondición añadida al GATE-C** (RUNBOOK): antes del flip, cada perfil activo debe tener
  embedding + `match_evaluation` de su revisión VIGENTE.
- ✅ **RESIDUALES PRE-FASE D CERRADOS (2026-08-05, por delegación)**: de los residuales que dejó la
  revisión integral, los dos que dejan de ser benignos en multi-tenant se cierran ANTES de abrir
  Fase D (descubrirlos con tenants reales sale mucho más caro). (1) **Recuperación del proyector**:
  la exclusión por `users.is_active` es el mecanismo de la SOMBRA y se aplicaba a los perfiles de
  cualquier consumer (colisión de `external_ref` → exclusión silenciosa de otro tenant); ahora es
  consumer-aware y el descarte va dentro de la consulta. Además el lote queda ACOTADO
  (`RECOVERY_MAX_PROFILES = 200`, menos recientemente evaluados primero) porque `corpus_max`
  enciende la señal de todos los perfiles con cada corpus nuevo; y los candidatos exigen vector del
  modelo (sin él la evaluación es un no-op que bloquearía la cabeza del orden). (2) **Heartbeat del
  lease** (`core0018`): el lease se medía desde `started_at` inmutable, así que un fetch >900 s
  hacía que otro `run_all` re-armara el scope y AMBOS golpearan la fuente externa; ahora el worker
  vivo renueva `heartbeat_at` (mismo fencing por token) y el lease usa
  `COALESCE(heartbeat_at, started_at)` — sin backfill, las filas previas conservan la semántica.
  **Revisión externa ronda 1: REQUEST CHANGES → 2 P1, corregidos** (`729cbfb`): un modelo activo
  SIN corpus embebido también es un no-op que clavaba perfiles en la cabeza del lote (fix: los
  combos sin corpus no generan señal); y el latido moría con la primera excepción, reabriendo la
  doble cosecha (fix: timeout por intento, reintento con sesión nueva y watchdog que aborta el
  fetch con `LeaseLostError` si el worker es desahuciado o no logra latir dentro del margen).
  **Ronda 2: REQUEST CHANGES → 3 P1, corregidos** (`ea6e2d2`): la señal de recuperación pasa a ser
  por INTENTO (`core0019`, `profile_recovery_state`) porque una evaluación puede no escribir NINGUNA
  fila (top-K ya evaluado) y el perfil se quedaba clavado en la cabeza del lote; el watermark del
  corpus usa `offer_embeddings.created_at` (una revisión antigua embebida después perdía la señal en
  silencio); `uncancel()` ya no puede tragarse una cancelación externa concurrente (apagado del
  worker); y el margen del lease se calcula con deadline ABSOLUTO, recortando esperas y timeouts.
  **Ronda 3: REQUEST CHANGES → 3 P1 + 1 P2, corregidos** (`6801bad`): el intento debe registrarse
  DENTRO de la transacción de la evaluación y con la revisión que esta leyó (costura `on_evaluated`)
  — hacerlo después, re-consultando la vigente, apagaba la señal de revisiones que nadie evaluó y de
  combos `sin_vector`; la versión del corpus pasa de timestamp a HUELLA
  `count|max(oe.created_at)|max(orv.created_at)` comparada por DESIGUALDAD (`core0020`), porque
  desarchivar o reutilizar un `text_hash` no mueve ninguna fecha; y el ORDEN de la cola solo mira la
  revisión vigente y los combos activos (los intentos históricos nunca avanzan y daban prioridad
  perpetua).
- ✅ **Ronda 4: REQUEST CHANGES → 4 P1 + 1 P2, corregidos** (`78972e4`): un modelo activo sin corpus
  reventaba la costura (un tercer `return` de éxito sin la revisión) y además no debe contar como
  intento; la huella pasa a `count|bit_xor(vacante:revisión)` porque la de recuentos y máximos NO
  identificaba el CONJUNTO (intercambiar dos vacantes daba la misma versión con otro corpus) y se
  calcula DENTRO de la evaluación, con la versión que ESA evaluación vio; el orden ignora los
  intentos de combos sin corpus o sin vector (nunca se actualizan ⇒ prioridad perpetua); y
  `core0021` vacía el estado derivado para que el downgrade de `core0020` no apague trabajo
  pendiente.
- ✅ **Ronda 5: REQUEST CHANGES → 4 P1, corregidos** (`31c70dd`): tres tenían la MISMA raíz —derivar
  la versión del corpus de su contenido— y se cierran con una **generación MONOTÓNICA global
  mantenida por triggers** (`core0022`): el XOR de `hashtext` (32 bits) colisiona de verdad (el
  revisor lo demostró contra PG) y no es persistible entre versiones; bajo READ COMMITTED la huella,
  el conteo y los candidatos veían hasta tres estados (un A→B→A quedaba sin detectar); y el agregado
  costaba un escaneo del corpus por (perfil, modelo, política). Ahora es una lectura por PK, leída
  ANTES de mirar el corpus (registrar la versión VIEJA es la dirección segura) y los triggers
  impiden que ningún camino de escritura se olvide de moverla. El 4º P1 (downgrade escalonado) se
  cubre vaciando el estado derivado en el downgrade + requisito operativo en el RUNBOOK.
- ✅ **Análisis PROPIO del cierre (2026-08-05, `acc665b`)**: dos bugs reales y una optimización, con
  la cobertura de los triggers COMPROBADA contra PG16 (no inferida). (a) Una sentencia dirigida a
  una PARTICIÓN de `offer_embeddings` no dispara el trigger de SENTENCIA del padre, y `TRUNCATE`
  tampoco dispara los de INSERT/UPDATE/DELETE: en ambos casos el corpus cambiaba con la generación
  quieta ⇒ señal apagada con trabajo pendiente. `core0023` añade `AFTER TRUNCATE` y replica el
  trigger en cada partición (`register_model` en las nuevas). (b) La definición de "corpus elegible"
  estaba duplicada entre el conteo de elegibles y el fragmento compartido: unificada, porque una
  divergencia significa evaluar contra un conjunto distinto del que gobierna la señal.
  (c) La subconsulta del ORDEN se evaluaba una vez por perfil × combo aunque solo depende del
  perfil: ahora los candidatos se filtran primero y la antigüedad se calcula solo sobre ellos.
  **Suite core 461/461**, head Alembic `core0023`. Commits `6bd72ce`+`256c086`+`729cbfb`+
  `ea6e2d2`+`6801bad`+`78972e4`+`31c70dd`+`acc665b` (SwissJob).
  **RIESGO documentado, no resuelto:** el contador de una fila SERIALIZA a los escritores del corpus
  (cada transacción que lo toca retiene su lock hasta el commit) y hay un deadlock teórico si otra
  toma los locks en orden inverso; PG lo detectaría y Celery reintenta. Las alternativas sin bloqueo
  (secuencia o log append-only) REINTRODUCEN el fallo: `last_value` es visible antes del commit, así
  que se registraría una versión que incluye cambios que la evaluación no vio. Vía si molesta:
  `CONSTRAINT TRIGGER DEFERRABLE` (bump en el commit, orden de locks uniforme, a costa de ser por
  fila).
  Contrapartida aceptada: un worker COLGADO pero que LATE retiene el scope más de `SCOPE_LEASE_S`.

### TRACK V-DIFERIDO (Fase 3 — recuperación de fuentes legacy) — 🏁 CERRADO (2026-08-15; 8ª revisión 2026-08-20)
Track legacy-side, no toca el core: fuentes mudas recuperadas + garantía «un 200 ilegible acaba
como `error`, nunca como `empty`» en las fuentes tocadas. Cerrado con OCHO revisiones externas
(commits SwissJob `3e11420`/`91aa1c6` + revisiones `259a1ec`→`747630f` + `3cb91a7` + `d24cbe6` + la
octava); suite legacy **1381 passed, 3 skipped**. La quinta (2026-08-19) encontró TRES defectos ESTRUCTURALES
PREEXISTENTES, verificados en código y NO corregidos ahí (son rediseño): identidad con campos
mutables (VD.12 — añadido a §2; el core lo resuelve por diseño, con los matices de §6), ~9 fuentes
sin validar el host de las URLs (VD.13) y URL de listado constante en `hautlac`/`iscs` (VD.14).
La sexta (2026-08-19) cerró dos defectos legacy —la FORMA de `externalPath` en ISP, que dejaba
persistir URLs sin oferta detrás reportando `ok`, y el byte NUL en el `logo`, que costaba la
oferta entera— y dejó dos hallazgos que NO son de esta fase: el contrato de identidad por
adaptador nativo del core (VD.15, **corrige §6**: la cosecha nativa no extingue VD.12 por sí
sola) y el NUL en el resto de campos de texto (VD.16, con el mapa campo a campo; los de identidad
no admiten la solución del logo).
La séptima (2026-08-20) cerró el hueco que la sexta dejó abierto sin saberlo: el validador de forma
miraba los caracteres CRUDOS, así que el percent-encoding lo eludía (`%00`, `%GG`, `%252E%252E`,
`%255C`, `%2F`) y volvían las ofertas fantasma con `outcome=ok`. Suite **1374 passed, 3 skipped**.
La octava (2026-08-20) cerró los dos escapes que quedaban en ese validador —el anidamiento de tres
niveles y los octetos que no forman texto— y retiró una regla que era falso positivo propio. También
demostró que la corrección documental de la séptima fue **incompleta**: quedaban diez copias vivas
del estado obsoleto repartidas por los tres documentos. De ahí la regla de `DEUDA_TECNICA.md` §5:
corregir una afirmación de estado obliga a barrer TODAS sus copias antes de darla por corregida.
Aportó además una corrección de ESTADO que no es de código y que importa para planificar: este
inventario afirmaba que el GATE-SOMBRA "jamás se ejecutó" y que el contador estaba "a cero", y era
**falso** — hay 3 sets congelados, 91 juicios y 6 ciclos sellados. El bloqueo real es distinto: la
racha está **rota** y la última medida (2026-07-30) salió **ROJA**. Detalle y cifras verificadas en
`DEUDA_TECNICA.md` §2.2.
Quedan abiertos: VD.5, VD.8, VD.11–VD.16 — registro completo en
`BACKLOG_UNIFICACION_JOBHUNTING.md` (TRACK V-DIFERIDO) y `DEUDA_TECNICA.md` §1.23–§1.29 y §2.19,
sin duplicar aquí. ⚠ Nada de este trabajo está desplegado en el NAS (`DEUDA_TECNICA.md` §1.21).

#### Gatings activos y pendientes del propietario (consolidado)
- **Gating GATE-SOMBRA**: el FLIP de C (C-6) está BLOQUEADO hasta la racha 7/7 en verde.
- **Gating T-PRE-FLIP** (nuevo, 2026-08-20 — 9ª revisión externa): el FLIP está BLOQUEADO además
  hasta tener **restore probado con RPO/RTO registrados**, **rotación de credencial ejercitada** y
  **ensayo de borrado GDPR/crypto-shred**. TRACK T se declaraba obligatorio antes del flip pero
  **ningún gate lo exigía**, así que el procedimiento permitía flipar sin ellos. El resto de TRACK T
  sigue siendo diferible: no se convierte el track entero en un megagate.
- **Gating de corpus** (nuevo, misma revisión): la recuperación de fuentes de la Fase 3 **cambia el
  corpus vivo** —el elegible son las vacantes vivas con embedding y cada transición incrementa
  `corpus_generation`—, así que debe **desplegarse y drenarse ANTES** de iniciar la racha 7/7 que se
  quiera considerar definitiva. La afirmación contraria, que circulaba en backlog y ESTADO, era
  falsa.
- **Gating NAS**: las VALIDACIONES (dedup semántico nivel 3 → despliegue NAS → racha) y el
  ensayo de C-2/C-4 sobre copia de datos del NAS esperan a que el NAS esté disponible.
- **Pendientes de la mano del propietario** (ninguno bloquea la construcción de C):
  1. `ALLOWED_HOSTS=["localhost","127.0.0.1","backend"]` en el `.env` real del portfolio (regla dura del .env).
  2. Excepción `!.env.example` en el `.gitignore` del portfolio (para versionar la plantilla).
  3. `name:` en el `docker-compose.yml` de Novafeed + recrear su contenedor `db` (datos intactos).
  4. `pytest-timeout` en `backend/requirements.txt` de SwissJob (el `--timeout` de CLAUDE.md).
