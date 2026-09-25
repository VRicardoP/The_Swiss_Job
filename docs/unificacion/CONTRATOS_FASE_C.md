# Contratos ejecutables — Fase C (cutover piloto: ReactPortfolio) · v1.1

> Deriva de `PLAN_UNIFICACION_JOBHUNTING.md` §12/§15bis y `BACKLOG` C.0–C.6, sobre Fase A
> COMPLETA + Fase B completa en código + track A.SEAM (SwissJob) con APPROVE externo.
> Estado: **VIGENTE v1.1** — C-CC/C-PRE/C-ESQ/C-0/C-API-R/C-API-W/**C-1** ✅ (doble análisis Opus
> por etapa/vertical); **siguiente C-2** (runbook + kill-switch), luego C-3/C-4/C-6. Fecha: 2026-07-31.
> Inventario del portfolio verificado en código/contenedores (core head `core0012` en su momento,
> tras C-ESQ/C-API-R; **hoy head `core0023`** tras §4-LOCAL —core0013 manifiesto + core0014/15/16
> lifecycle del rollback + core0017 claim_token y core0018 heartbeat del lease; `/v1` ya NO
> read-only — feed keyset
> `GET /vacancies` (C-API-R) + `PUT /profiles/{id}`
> con Idempotency-Key/If-Match (C-API-W) además de los GET; arpones del lifespan en `main.py`).
> **v1.1**: C-ESQ (tablas core ausentes) · C-API-R/C-API-W (cotas del /v1) · C-2 real sin CDC +
> kill-switch · VACANTES-SOMBRA como caso normal · colapso cv_profiles · regla saved/follow_up_date
> · GATE C por capacidad · documentos y colegios diferidos a Fase E en LOCAL.
> **VALIDACIONES (orden del propietario)**: dedup semántico + despliegue NAS + racha 7/7 se
> lanzan CUANDO EL NAS ESTÉ DISPONIBLE. El **FLIP final (C-6/GATE C) queda BLOQUEADO por el
> GATE-SOMBRA**; toda la CONSTRUCCIÓN de C avanza sin él.

## 0. Alcance y estado de partida (inventario 2026-07-29)

Piloto = ReactPortfolio: FastAPI+SQLAlchemy async+PG15, compose PROPIO
(`ReactPortfolio/backend/docker-compose.yml`), SIN broker (BackgroundTasks+asyncio del
lifespan), Redis solo caché, notificaciones SSE EFÍMERAS (no hay tabla ni `is_read` — la
decisión del plan queda RESUELTA: nada que migrar). Datos de empleo de los 20 portales NO
persistidos (caches en memoria + snapshots Redis); único artefacto de matching en BD:
`seen_jobs` (ledger, → recomputar). Repos git backend/frontend independientes, limpios, main.
- **Roto HOY (no es deriva del plan, es entorno)**: `portfolio_backend` en crash-loop
  (RestartCount ~13.9k) porque `portfolio_db` NO EXISTE (DNS `db` irresoluble); imagen en
  ejecución de ABRIL desalineada del compose y del código; credenciales del contenedor vivo
  distintas del `.env` en disco; puertos declarados 8002/5435 COLISIONAN con el dev de
  SwissJob ahora en marcha.
- **Cotas del núcleo (verificadas, base de C-ESQ/C-API-\*)**: head Alembic del core =
  `core0010`; `applications`/`application_status_events`/`saved_searches` NO existen aún en el
  esquema (están en `CONTRATOS_FASE_A` §1 sin marca [A]); `api/v1.py` es read-only — solo
  `GET /vacancies/{id}`, `GET /profiles/{pid}`, `GET /profiles/{pid}/matches`.
- NO-alcance: Fase D (SwissJob), E, F; frontend del portfolio salvo lo mínimo del BFF.
  **Documentos y colegios QUEDAN en Fase E** y se sirven en LOCAL tras el flip (ver C-4/C-6).

## 1. Topología objetivo

Portfolio BFF → core `/v1` como **consumer PROPIO** `portfolio` (multi-tenant real: distinto
de `swissjob-shadow`) con credencial de scopes mínimos; su(s) perfil(es) = el/los usuarios
reales del portfolio. `jobhunt_routing` local al BFF (tabla idéntica al patrón A.SEAM).
Auth/analytics/chat/SSE/cv-export permanecen SIEMPRE del BFF (no son job-hunting).

## 2. Tickets (orden de ejecución; ciclo Fable + doble Opus + commits por etapa)

| Ticket | Alcance | DoD |
|---|---|---|
| C-PRE | Restaurar el entorno del piloto EN LOCAL: recrear `db` con VOLUMEN PROPIO (`name: portfolio` — HALLAZGO C-PRE: `backend_postgres_data` era el cluster de rss_reader/Novafeed por colisión del proyecto compose por defecto; el portfolio local JAMÁS tuvo durables — la fuente autoritativa es el NAS), imagen re-construida del código actual, credenciales del `.env`, RE-MAPEO de puertos (8004/5436), stack Up y healthy SIN ngrok | backend healthy en esquema FRESCO (create_all + stamp `kk44l3370n11`; 0 durables locales, registrado), cadena Alembic REPARADA (upgrade desde cero + roundtrip), 0 colisiones; pendiente del propietario: `name:` en el compose de Novafeed |
| C-ESQ | Migración core **[C]** sobre head `core0010`: crea `applications` + `application_status_events` + `saved_searches` + `idempotency_records` (la exige C-API-W; PLAN §4: consumer_id, key, route, request_hash, response, expires_at) **SEGÚN `CONTRATOS_FASE_A` §1** (UNIQUE(profile_id,vacancy_id), FK compuesta `(source_listing_incarnation_id, vacancy_id)`, snapshot JSONB, notes/follow_up_date). Es un **adelanto mínimo imprescindible** de esquema no-[A], **registrado como tal**; NO crea `generated_documents` ni tablas de colegios (Fase E) | upgrade limpio + downgrade→re-upgrade roundtrip; head único; invariantes §1 probados contra PG real (patrón A-02) |
| C-0 🔒 | Seguridad: ngrok FUERA del camino del cutover (el compose lo conserva solo como perfil opcional documentado, apagado por defecto); credencial core con scopes mínimos; ALLOWED_HOSTS concretado; verificación de secretos (0600, gitignore) | sin ngrok en el arranque por defecto; consumer `portfolio` + credencial emitida y verificada E2E contra el core |
| C-API-R | Sub-ticket **CORE**: feed/búsqueda de catálogo en `/v1` que C-1 necesita — hoy el `/v1` solo expone GET por UUID (cota conocida del A.SEAM). **Doble análisis propio al implementarse** | DoD propio: DTO vacante §2 de Fase A (solo ACTIVAS), paginación keyset, scope `vacancies:read`, contract tests. COTA `q` (1ª rev.): substring sin índice — barre el índice parcial de activas en peor caso (no seq scan del corpus); GIN trigram diferido hasta que el volumen lo exija |
| C-API-W | Sub-ticket **CORE**: escritura MÍNIMA del `/v1` con **idempotency key** (§15bis; activa `idempotency_records`, hoy NO-[A]): **PUT perfil/revisión** para el CV push (C-3); **POST candidatura/estado** SOLO si C-4 lo exige. **Doble análisis propio al implementarse** | DoD propio: reintento con misma key no duplica; ownership por tenant (cross-tenant 404); precondición ETag/412; purga de idempotency_records CABLEADA en el beat del core-worker (jobhunt.idempotency.purge_expired, no C-2); OBLIGACIONES C-ESQ al llegar el escritor: extender _ERASE_TABLES (erase GDPR) con applications+saved_searches y dbcleanup de tests con las 4 tablas nuevas (FK sin CASCADE — el teardown del primer test durable fallaría) |
| C-1 | BFF por capacidad con la PLANTILLA A.SEAM: `jobhunt_routing` + puertos — catálogo (las 20 fuentes + búsqueda unificada → catálogo/feed core) **CONDICIONADO explícitamente a C-API-R** (sin él no hay variante core del catálogo), matching (`ai-match` → matches core), candidaturas/búsquedas-guardadas (variante core vía C-ESQ/C-API-W; escrituras SIEMPRE locales hasta el flip), documentos/colegios SOLO variante local (Fase E), auth/analytics/chat/SSE fuera | contract tests por capacidad contra ambas impls; comportamiento local byte-idéntico; criterio unificador de A.SEAM aplicado |
| C-2 | Runbook de cutover del PILOTO **REAL: SIN CDC** — no hay tráfico de escritura continuo que capturar (solo durables + caches efímeras, §0). Cutover = **freeze corto + QUIESCE (kill-switch, ANTES del flip — §15bis paso 6, gate anti-doble-cosecha) → migración de durables (C-4) → verificación conteos/checksums → flip de lecturas**. Del orden ratificado §15bis: pasos 1/3/7 (watermark-LSN, replay, drenar outbox de captura) **NO aplican** — exigen sustrato CDC/outbox legacy que este piloto no tiene ni necesita (el freeze elimina el delta); pasos 2/4/8/9 aplican adaptados; paso 5 (canary de lecturas) lo cubre la paridad de C-6. **Registrado — no es desviación silenciosa.** Además **IMPLEMENTA el kill-switch**: flag de arranque para los arpones del lifespan — inventario exacto de `main.py`: `_load_apis_background`, `_periodic_document_cleanup`, `_periodic_seen_jobs_cleanup`, `_periodic_school_scrape`, `_periodic_saved_search_alerts` (hoy incondicionales) y `_periodic_circadian_harvest`/`_periodic_daily_match_report` (ya con flag propia, quedan subordinadas al switch global) | runbook ejecutable ENSAYADO sobre copia local (patrón A-12/B-05); kill-switch implementado Y probado con test (flag OFF ⇒ 0 arpones armados) |
| C-3 | CV push: `cv_profiles` (fuente autoritativa, tiene updated_at) → perfil core con ETag, vía **C-API-W**. **Regla de colapso** (cv_profiles es multi-tipo/idioma — UNIQUE(cv_type,language)): el perfil core recibe **cv_type=`extenso` del idioma primario** (configurable, default `en`); `simplificado` y demás idiomas quedan LOCALES (futuro: documentos Fase E) | round-trip con ETag; el CV extenso/idioma-primario del portfolio alimenta el perfil core del consumer `portfolio` |
| C-4 | Manifiesto de datos POR TABLA + migración de durables (§15bis campo-a-campo). **VACANTES-SOMBRA = CASO NORMAL** para candidaturas históricas Y para la rama saved (el bookmark necesita vacancy_id): vacancy + offer_revision mínima sintetizada desde el snapshot (title/company/url/description); identidad por URL si existe (`source_listings.url_normalized`); si no, fuente `portfolio-import` con external_id determinista; **staging SOLO para lo irrecuperable**. Mapeo: `job_applications.status=saved`→`profile_vacancy_state.saved_at` (bookmark) + `notes`→`profile_vacancy_state.notes` (existe §1); **bookmark CON `follow_up_date` ⇒ migra ADEMÁS como `application`** (status inicial = equivalente de saved en el enum §1) para no perder el dato — regla explícita; **CONSOLIDACIÓN UNIQUE(profile_id, vacancy_id)**: si ese `saved+follow_up_date` y una candidatura (`status≠saved`) resuelven a la MISMA `vacancy_id` (misma URL), producir UNA sola `application` (estado de la candidatura real, conservando `follow_up_date`/`notes`) — no dos filas que violarían el UNIQUE; `status≠saved`→`applications` core (enum mapeado + evento inicial SINTETIZADO en `application_status_events`; title/company/url/description→snapshot; notes/follow_up_date→columnas); `saved_searches`→migrar (posible tras C-ESQ); `generated_documents`→**QUEDAN LOCALES hasta Fase E** (se sirven en local tras el flip); `schools/school_jobs/school_applications`→**QUEDAN LOCALES hasta Fase E** (la designación de maestra única se decide EN Fase E; SwissJob sigue sin tablas de colegios); `cv_profiles`→conservar local + push (C-3); `seen_jobs`→recomputar; SSE→nada; analytics/users→conservar local | migración ENSAYADA sobre COPIA DE LOS DATOS DEL NAS (fuente autoritativa — precondición: NAS disponible, mismo gating que las validaciones) con conteos y checksums por tabla; reversible. Jamás contra el esquema local fresco (checksums de cero = confianza falsa). **DIVISIÓN DE ALCANCE C-4 / ENSAYO §4** (2026-08-02, con el propietario, tras 4 revisiones externas): **C-4** entrega la migración (síntesis/mapeo/consolidación, `import_portfolio*`), la corrección del **artefacto de corpus en colisión** (savepoint que revierte la cadena cross-source — integridad real), la reconciliación por **VALORES materiales** contra el origen (`reconcile` — atrapa el bug determinista) y un **inventario SCOPEADO** de rollback (`_captured_identities`, NO procedencia exacta). **El ensayo §4 (gated NAS)** entrega los artefactos que exigen un LEDGER del sink y datos reales (ver RUNBOOK §4, BLOQUEANTES del cutover): **ledger del sink por entrada** (created/reused/quarantine+razón/link), **manifiesto de PROCEDENCIA EXACTA** (RETURNING, insertada vs preexistente), **verificación estructural INDEPENDIENTE** (distingue cuarentena legítima de listing perdido) y **script de borrado FK-safe** + cierre de la carrera residual. El scaffold local NO sustituye esos entregables |
| C-5 | Fallback read-only del primer cutover — SOLO SI HACE FALTA (diferido salvo evidencia) | decisión registrada |
| C-6 | Paridad N días + 🚦 **GATE C POR CAPACIDAD** (sin mentir): 100% de lecturas contra el núcleo en **catálogo/matching** (el perfil del core se consume DENTRO de matching vía `GET /v1/profiles/{pid}/matches`; **CV/perfil declarado LOCAL hasta Fase E**, sin capacidad de routing propia); **candidaturas/búsquedas-guardadas** contra el núcleo tras C-4; **documentos/colegios declarados en LOCAL hasta Fase E** · outbox sin divergencia · sin pérdida (conteos/checksums) · harvesters propios APAGADOS vía kill-switch C-2 (gate anti-doble-cosecha) · UN escritor · **FLIP condicionado al GATE-SOMBRA superado** | checklist del gate en verde; flip solo con la racha 7/7 hecha |

## 3. Riesgos específicos

- Colisión de puertos con SwissJob dev (C-PRE la elimina y documenta el mapa).
- Imagen/env con deriva de meses: C-PRE reconstruye desde el código actual — cualquier
  migración pendiente de su Alembic se aplica con el tooling propio del portfolio.
- ngrok: prohibido en el camino del cutover (plan §13); su retirada no debe romper el uso
  actual del propietario — perfil opcional y documentado.
- Los arpones del lifespan (cosecha circadiana propia, cleanups, school scrape, saved-search
  alerts, daily report) son los "schedulers legacy" de este piloto: el QUIESCE de C-2 es el
  gate anti-doble-cosecha del plan, y el kill-switch de C-2 su mecanismo (flag de arranque).
- C-ESQ adelanta esquema no-[A] (solo las 4 tablas imprescindibles (incluida idempotency_records para C-API-W)): riesgo de deriva si Fase E
  retoca §1 — mitigado ciñéndose LITERALMENTE a §1 y registrando el adelanto aquí y en el ticket.
- Vacantes-sombra sintetizadas entran al corpus global: quedan auditables por su fuente
  `portfolio-import` (external_id determinista) para dedup/enlace posterior.
- RESUELTO EN C-PRE: `backend_postgres_data` NO era del portfolio (cluster rss_reader de
  Novafeed; datos rss intactos, contenedor recreable). Los durables reales viven SOLO en el
  NAS: todo ensayo de C-2/C-4 parte de una copia del NAS (gating del propietario).
