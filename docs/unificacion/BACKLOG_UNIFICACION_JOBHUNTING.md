# Backlog de implementación — `jobhunt-core` · v3.1.2

> Compañero de `PLAN_UNIFICACION_JOBHUNTING.md` (v3.1.2). Topología: **jobhunt-core =
> servicio independiente; ambas apps = BFF**. Ejecución: **Strangler Fig con backfill+sync
> ANTES del flip, un escritor por estado, routing por capacidad/perfil y apagado de
> schedulers legacy**. Fecha: 2026-07-21.
>
> **Actualización 2026-09-21:** este backlog conserva estimaciones y tickets
> históricos; no usarlos como pendientes sin contrastar ESTADO §43 y las actas.
> Punto 4 abierto, ejecución funcional pausada. Perfiles, feedback, diez búsquedas
> SwissJob y WorkingNomads ya transferidos; no repetir esos cortes.
> Cola de reanudación R1–R7 y criterios de cierre en el
> [informe detallado](SwissJob/docs/PENDIENTES_PUNTO4_REVISION_EXTERNA_2026-09-21.md)
> y [runbook](SwissJob/docs/RUNBOOK_RETIRADA_PRODUCTORES_PUNTO4.md).
> Primero censo efectivo; sin fecha garantizada, nuevas funcionalidades ni
> trasladar aquí la racha de calidad o la optimización integral del punto 5.

## Convenciones
`ID` · Talla S/M/L/XL · 🚧 Depende · 🔒 Gate · **DoD**.

## Mapa
```
PRE-FASE (5 P0) → GATE-PF
  → 🔒 CIERRE DE CONTRATOS (ADRs + tickets/DTOs + reutilización de código)
  → FASE A  (servicio aislado + esquema aditivo + VERTICAL MÍNIMA) → GATE A
  → FASE B  (sombra: legacy=escritor; core en paralelo aislado; set etiquetado) → GATE-SOMBRA
  → FASE C  (Portfolio: costura+backfill+sync+canary+freeze+drain+flip+apagar legacy) 🔒 → GATE C
  → FASE D  (SwissJob: mismo runbook, canary POR PERFIL)
  → FASE E (colegios/docs) → FASE F (retirar tras retención+backup+N ciclos)
TRACK T (paridad, observabilidad, seguridad, ciclo de vida de datos) · TRACK O (fuera del corte)
```

---

## PRE-FASE — Saneamiento de SwissJob
PF.1 [M] Upsert versiona contenido · PF.2 [S] un despacho de dedup · **PF.3 [L] archivar/FK en
las TRES tablas** (job_applications, match_results, **generated_documents**) · PF.4 [M] carga
única (cola dedicada + no preload) · PF.5 [M] dedup por tokens + `dedup_candidates`.
🚦 **GATE-PF: ✅ CERRADO** — 5 fixes con tests · cleanup sin pérdida de candidaturas/documentos ·
dedup una vez · 1 carga · tokens sin corromper substrings. La rama `fix/prefase-saneamiento`
(`ee5b353`) está mergeada en `feat/fase-a-core` y las migraciones desplegadas (todo lo posterior
—Fase A/B/C— ya se ejecutó sobre ella).

## 🔒 CIERRE DE CONTRATOS (bloquea Fase A)
CC.1 [M] ADRs: estado, merge/split, cursor por scope, retención/GDPR, **reutilización de código
(DECIDIDO: paquete-en-repo de SwissJob + fronteras API/esquema; extraer a repo propio solo cuando
lo gane — plan §21)**. ·
CC.2 [L] **Regenerar tickets/DTOs** → `CONTRATOS_FASE_A.md` v4 (migrations, DTO multi-listing,
eventos, constraints). 🚦 **GATE-CC: ✅ CERRADO (RATIFICADO 2026-07-22)** — ADR_JOBHUNTING.md v4 +
CONTRATOS_FASE_A.md v4, tras 3 rondas de revisión adversarial (37+12+10 hallazgos aplicados).

---

## FASE A — Servicio aislado + esquema aditivo + VERTICAL MÍNIMA — ✅ COMPLETA (2026-07-24)
> Ejecutada como tickets **A-01..A-12** de `CONTRATOS_FASE_A.md` v4 (detalle en plan §24); los
> ítems A.SVC..A.15 de abajo quedan como alcance histórico.

**A.SVC — [L] Extraer y desplegar `jobhunt-core` AISLADO (NUEVO v3.1.2).** Paquete + app
desplegable (API `/v1` + worker) dentro del repo de SwissJob; **colas Celery `core.*`**,
**Redis DEDICADO para broker/locks** (`redis-core`, contenedor propio `noeviction`, ADR-08 — los
prefijos NO aíslan y el `allkeys-lru` de la caché expulsaría mensajes Celery/locks),
**leader-lock separado**, **rol de BD limitado al esquema del core + `search_path`**, **Alembic +
migration job propios** (no colgado del entrypoint de SwissJob), pools separados. **DoD:** el
worker legacy NO consume tareas del core ni puede expulsar sus mensajes; migraciones del core
independientes. **Aborda:** aislamiento operativo (R5-#7, R6-Redis).
**A.SEAM — [L] Costuras POR CAPACIDAD en AMBOS backends (NUEVO v3.1.2).** Subinterfaces
(catálogo, matching, perfiles, candidaturas, documentos, colegios) con impl `local` + `core`,
**contract tests** de comportamiento idéntico. Routing en **tabla `jobhunt_routing` en cada BFF**
(por perfil+capacidad, NO env var; local al BFF para enrutar aun con el core caído) +
`ENGINE/SCHEDULER/NOTIFICATIONS` por perfil; **schedulers conscientes del routing**. **DoD:** todo
en `local`, sin cambio de comportamiento. **Aborda:** costura en ambos backends, routing persistente
(R5-#4/#5, R6-routing).
**A.0b [M]** `harvest_scopes` + `source_scope_state` (cursor por scope); quitar el
`("", "Switzerland")`+anti-tech de `fetch_tasks.py:173`.
**A.1 [L]** corpus dos niveles + **`source_listing_revisions` obligatoria** + constraints +
`offer_revision_sources` + `merge_log` + **`merge_transfers`** + **`source_listing_incarnations`** + `link_evidence`; **persistir raw antes de normalizar** (revisiones cuelgan de la incarnación).
**A.1b [M]** `offer_embeddings`/`profile_embeddings` + `embedding_models` + `offer_translations`
(field title|description).
**A.2 [L]** backfill `jobs`→(vacancy, listing, revisión, embedding).
**A.3 [M]** `profiles`/`profile_revisions`/`consumers`/`consumer_credentials`. **A.3b [S]**
`idempotency_records`.
**A.4 [M]** scoring_policy + prompts versionados. **A.5 [L]** `match_evaluations` append-only +
`profile_vacancy_state` + `profile_vacancy_events`.
**A.7 [M]** `harvest_runs` + `source_harvest_runs` (por scope). **A.8 [M]** outbox tri-canal.
**A.9 [M]** domain_tags/segmentación. **A.10 [M]** `since` real.
**Diferidos post-vertical (antiguos A.11/A.12/A.13; NO son los A-11/A-12 ejecutados de
`CONTRATOS_FASE_A.md` v4):** traducción cuerpo · documentos WeasyPrint +
`generated_documents`/`application_status_events` · colegios (`school_job_details`→vacancy) —
siguen pendientes para fases posteriores (colegios/docs → Fase E).
**A.14 [XL]** API `/v1` (multi-listing, scopes, idempotency, SSE fetch+Bearer, contract tests).
**A.15 [L]** ensayo de migración sobre copia (down-migrations válidas solo pre-cutover).
> **A.MIN (arrancar aquí):** 1 provider · 2 scopes · 2 perfiles · listing revision · embedding ·
> evaluación/estado · API read-only · una entrega outbox. Cuerpo/documentos/colegios después.
🚦 **GATE A: ✅ ENSAYO SUPERADO (2026-07-24)** (`test_gate_a.py`, 7 criterios con assert explícito;
auditoría final del tramo A-09→GATE A: 4 hallazgos corregidos, `b14868e`) — SwissJob idéntico ·
A.MIN validada extremo a extremo · ingesta por scopes cubre tech+no-tech · eval append-only con
descartes estables · core aislado (worker legacy no cruza).

## FASE B — Sombra (legacy sigue de escritor) — ✅ COMPLETA EN CÓDIGO (2026-07-25)
> Ejecutada como tickets B-CC/B-01..B-05 de CONTRATOS_FASE_B.md (doble análisis Opus por etapa).
> GATE-SOMBRA: sets **YA congelados** (3, `frozen_at` 2026-07-28) y **6 ciclos sellados**
> (2026-07-25 → 2026-07-30). Pendiente: **recuperar calidad** —el último ciclo salió ROJO— y
> **7 ciclos consecutivos en verde**; la racha efectiva es 0/7 porque se rompió, no porque no se
> empezara. Cifras y consulta en `DEUDA_TECNICA.md` §2.2.
B.1 [M] core en paralelo (aislado), **legacy = escritor**, `legacy→core` captura de deltas.
B.2 [M] **set ETIQUETADO por perfil** (no el sistema viejo como oráculo) + métricas
(precision/recall dedup, nDCG@K, overlap@K, falsos negativos, coste, latencia, outbox lag, cero
pérdida). B.3 [S] fijar nº ciclos, umbrales, RPO/RTO, outbox lag máx., % re-enlace, rollback/replay.
🚦 **GATE-SOMBRA:** N ciclos con todas las métricas dentro de umbral.
> **2026-08-23: RACHA EN MARCHA.** Oráculo curado por el propietario (48 juicios + 81 pares) y
> CONGELADO (08:51 UTC). Preview: dedup 0.977/1.0 ✓. Ciclo 08-22: perdida=0 (primera vez),
> latencia 278 s. Primer candidato a verde: ciclo 08-23 (sella 24-08 06:05). **CODE FREEZE del
> core hasta 7 verdes.** F-5 (nivel 3) ENTREGADO: ANN cross (`0e70cc6`) + exacto intra
> (`650a34c`, regla multi-ciudad ratificada); umbrales recalibrados (`876add6`).

## FASE C — Portfolio (piloto) · runbook de cutover — 🏁 CONSTRUCCIÓN COMPLETA (2026-08-02)
> ⚠ **Estado canónico: `PLAN_UNIFICACION_JOBHUNTING.md` §25.** No dupliques el estado aquí: esta
> cabecera decía "FRENTE ACTUAL = C.2" **hasta el 2026-08-20**, tres semanas después de que la
> secuencia C-2→C-6 se cerrara, y la séptima revisión externa lo cazó. Si esto y el plan vuelven a
> discrepar, **manda el plan**.
> Ejecutada como tickets C-CC/C-PRE/C-ESQ/C-0/C-API-R/C-API-W/C-1 de CONTRATOS_FASE_C.md v1.1
> (doble análisis Opus por etapa). C.0 ✅ · C.1 ✅ · C-2 ✅ · C-3 ✅ · C-4 ✅ · C-6 ✅ — no queda
> ticket de construcción (C-5 diferido salvo evidencia). Lo que resta está **gated al NAS**: FLIP
> (C.6/GATE C) BLOQUEADO por GATE-SOMBRA, cuya racha está **rota** y cuya última medida es del
> 2026-07-30 y **roja** (`DEUDA_TECNICA.md` §2.2).
C.0 🔒 ✅ [M] seguridad (key mín. privilegio; ngrok NO en el arranque — bajo profile `public`,
inspector 4040 en loopback; consumer `portfolio` + credencial). C.1 ✅ [M] BFF por capacidad
(las 5 verticales del portfolio).
> **2026-08-22/23:** runbook COMPLETO (pasos A-F + rollback por paso) y **§4-REAL EJECUTADO
> sobre copia real** (verified + rollback prístino + guards LIFO/CASCADE demostrados). Hallazgo:
> durables reales del portfolio VACÍOS ⇒ el riesgo del flip es el camino de lectura — cuyo
> **canario está ACTIVO en producción** desde el 22-08 (agente del Portfolio). Queda del flip:
> GATE-SOMBRA + credencial `profiles:write` (C-3) + variante RETURNING (solo cutover vivo).
**C.2 [L] Runbook (orden corregido):** **activar outbox de captura legacy + watermark/LSN** →
snapshot/backfill consistente (legacy escritor) → **replay post-watermark** → sync → reconciliar
checksums → **canary de lecturas** → **freeze + QUIESCE de schedulers/engine legacy (ANTES del
flip)** → drenar + delta final → **flip a core_primary** → **verificar schedulers apagados (gate
anti-doble-cosecha/email)**. **Aborda:** R5-#2/#3/#5, R6-orden.
C.3 [S] CV push (fuente autoritativa + ETag). **C.4 [M] Manifiesto de datos por tabla** (migrar/
recomputar/conservar/archivar/eliminar) + migración de lo durable (JobApplication, saved_searches,
documentos, colegios; seen_jobs→recomputar; **notificaciones con `is_read`→decidir migrar/archivar,
NO recomputar**). **Aborda:** R5-#6, R6-notif. **C.5 [S] fallback SOLO read-only en el primer
cutover** (`pending_sync` con escritura diferido; se añade solo si hace falta). C.6 [M] paridad N días.
🚦 **GATE C:** 100% contra el núcleo · outbox sin divergencia · sin pérdida · **schedulers legacy
apagados (gate)** · un escritor.

## FASE D — SwissJob (canary por perfil) · E — colegios/docs · F — retirar
> **Estado 2026-08-05: D.1 ✅ y D.2 ✅ en CÓDIGO** (commits `6a8b9d4`, `7b2bb9c` en SwissJob; suite
> del backend legacy 969 passed). Entregado el **gate anti-doble-motor** completo: las tareas
> periódicas (D.1) y la vía interactiva `/analyze` (D.2) consultan `jobhunt_routing` y omiten los
> perfiles ya servidos por el core. Lo que NO entra todavía y por qué: el **manifiesto de datos de
> SwissJob** y el **runbook de D** son doc pendiente; y "BFF puro" en su sentido literal exige que el
> `/v1` exponga applications/documents/schools/búsquedas guardadas — que es Fase E/F, no D. La
> EJECUCIÓN del canary sigue gated al flip de C (GATE-SOMBRA + NAS).
> Residual asumido (§15bis línea 260): omitir también `core_read` congela la copia local del
> fallback. Pendiente de UI: el botón de análisis sigue habilitado para perfiles migrados.

D.1 [L] mismo runbook con **routing por perfil** (core_read→core_primary gradual) + **schedulers
conscientes del routing** (omiten perfiles ya en core; el scheduler global se apaga solo cuando el
ÚLTIMO perfil sale de `local`; notificaciones nunca desde ambos motores por perfil) + manifiesto
de datos de SwissJob (UserProfile, match_results→evaluations, feedback→state, applications,
documentos, saved_searches/filters, cursores→recomputar, **notificaciones `is_read`→decidir**,
compliance). D.2 [M] SwissJob deja de importar sus servicios locales (pasa a BFF puro). 🚦 sin regresión.
E [S] colegios/docs. F [L] retirar motores/tablas (retención + backup probado + N ciclos).
> Migraciones tras el cutover: **expand/contract + replay desde outbox**.

---

## TRACK V — Alcance temporal de la cosecha (ADR-10, nuevo 2026-08-06)
> Decidido por el propietario tras medir el corpus real del NAS: 66 días de acumulación, 43 % sin
> re-ver en 30 d y `is_active=true` en el 97 % ⇒ `is_active` no es señal de vigencia.
V.1 [M] ✅ **HECHO 2026-08-14 (`926d814`) — solo LEGACY.** `published_at` en el modelo normalizado +
extracción POR PROVIDER/SCRAPER desde el dato del portal (24 fuentes; `utils/dates.py` centraliza los
7 formatos reales). **DoD cumplido y fijado con test**: ninguna fuente lo puebla con `first_seen_at`.
🔻 **PENDIENTE la mitad del CORE** — ver V.1c abajo.
V.2 [M] ✅ **HECHO 2026-08-14.** Ver **ADR-10bis** para las dos precisiones de implementación (ventana
móvil de 7 días; se aplica SIEMPRE y solo a ALTAS, no solo en el bootstrap, porque el pipeline de
providers no tiene incremental y la lectura literal resultaba decorativa: el run siguiente volvía a
guardar justo lo que la ventana acababa de descartar). Excepción `swiss_schools_*` intacta: política
`FULL`, sin ventana en ningún run.
V.3 [S] ✅ **HECHO 2026-08-14.** Registro `SOURCE_POLICY` (54 fuentes, con el motivo escrito una a
una) + test que falla si una fuente registrada en el código no tiene política. Sin default silencioso.

**V.1c [M] 🔻 NUEVO — `published_at` en el CORE.** Fuera de V.1 a propósito: el core recibe del legacy
por CDC, así que la columna sin el legacy poblándola sería un no-op, y la sombra está corriendo en el
NAS. ⚠ **Trampa ya verificada leyendo `jobhunt_core/shadow/capture.py`:** el capturador usa **lista
blanca** de columnas, así que hoy ignora `published_at` sin error — pero al añadirla a
`TABLE_WHITELIST`, **las filas legacy que ya no se re-tocan no emitirán un UPDATE al WAL** y su
`published_at` histórico **no llegará por streaming**. Misma familia que el P1 externo ya documentado
en `capture.py:90-99`. **DoD:** el ticket incluye plan de backfill dirigido, o acepta la pérdida por
escrito.
V.4 [M] ✅ **HECHO 2026-08-06 (`c8c1183`)** — **Regla de caducidad**: cerrar la incarnación cuando
`last_seen_at` supere N barridos, en vez de fiarse de `is_active`. **Aborda:** el 43 % de corpus
rancio proyectado como vacantes vivas.
**V.0 [M] ✅ HECHO 2026-08-06 (`c8c1183`) — 🔒 OBSERVABILIDAD DE FUENTES — BLOQUE 0, va PRIMERO.**
> Marcados el 2026-08-15 al revisar el estado real: ambos se entregaron en `c8c1183` (contextvar de
> diagnóstico + `source_health` con el porqué + rachas de error y vacío por separado + reintento
> programado del kill-switch), pero la entrada del backlog se quedó sin marcar. El TRACK V-DIFERIDO
> completó después las dos mitades que a V.0 le faltaban: la persistencia (VD.3) y la descarga de
> los scrapers (VD.10), que seguía leyéndose como `empty`.

Hoy un fetch fallido devuelve
`OK 0 ofertas`: **un 404, un 403 y un feed vacío son indistinguibles**, y por eso 9 fuentes llevan
66 días mudas sin que salte nada. (a) separar `error` de `0 resultados` en el resultado de
provider/scraper; (b) salud por fuente (TRACK T "sources health"): último éxito, último error + su
código, ofertas del último barrido; (c) alerta con N barridos sin datos; (d) **reintento programado
del kill-switch** — hoy apaga para siempre (`gastrojob` y `swiss_schools_isb` NUNCA han funcionado).
🚧 Bloquea a V.5. **DoD:** una fuente que devuelve 404/403 NO puede terminar en estado de éxito.

V.5 [S] **Cobertura real de fuentes**: 18 de 43 registradas no traen NADA. 9 con causa esperada
(5 partner + 4 sin API key). Las otras 9, **DIAGNOSTICADAS EN VIVO el 2026-08-06** — ya no hay que
investigar, hay que arreglar: `authenticjobs`/`dailyremote`/`translatorscafe` = **RSS HTTP 404**
(feed muerto, buscar el nuevo) · `proz`/`zebis` = **HTTP 403** (aplicar `scraper_stealth`, la misma
capa que resucitó myscience/financejobs; ⚠ `zebis` es el portal de DOCENCIA suiza = el perfil
objetivo) · `remoteco` = **timeout** · `gastrojob`/`swiss_schools_isb` = **kill-switch tras 3
bloqueos, sin un solo éxito** · `stelle_admin` = permitido y con "éxito", pero **0 ofertas**.
🚧 Depende de V.0 (si no, se vuelven a apagar en silencio).
**⚡ 2026-08-15:** las 9 fuentes de esta lista quedan resueltas (`stelle_admin` VD.1 · `zebis`
VD.9 · `gastrojob`/`swiss_schools_isb` VD.4) o retiradas (`authenticjobs`/`dailyremote`/
`translatorscafe` VD.6) en el TRACK V-DIFERIDO, ya cerrado — salvo `proz` y `remoteco`, que
siguen en VD.5.

---

## TRACK V-DIFERIDO — recuperación de fuentes (CERRADO 2026-08-15 — quedan VD.5, VD.8 y VD.11 a VD.16)
> Aparcado a propósito el 2026-08-06: **ninguno bloquea la CONSTRUCCIÓN**. Todo aquí está
> **diagnosticado en vivo**: no hay que volver a investigar, solo implementar.
>
> ⚠ **CORREGIDO el 2026-08-20 (novena revisión externa).** Este bloque decía que "el corpus que mide
> el gate son las 19.284 vacantes ya proyectadas" y que arreglar fuentes "no cambia lo que se está
> midiendo". **Las dos cosas son falsas.** La cifra 19.284 es una **fotografía del 2026-08-07**; hoy
> la BD tiene **5.953 vacantes** y 5.953 `source_listings` en el core, frente a **9.173 ofertas
> legacy activas** (medido el 2026-08-20 sobre `swissjobhunter`). Y el corpus **no está congelado**:
> el corpus elegible son las vacantes VIVAS con embedding (`matching.py`, `ELIGIBLE_CORPUS_FROM`:
> `archived_at IS NULL AND merged_into IS NULL`) y cada transición incrementa `corpus_generation`,
> que reactiva la evaluación de perfiles (`shadow/projector.py`, `_RECOVERY_NEEDED_SQL`). Una fuente
> recuperada **sí** puede mover el top-K y las métricas.
> **Redacción correcta:** la recuperación de fuentes no bloquea la construcción, pero **su despliegue
> cambia el corpus vivo**, así que debe desplegarse y drenarse **antes** de iniciar la racha 7/7 que
> se quiera considerar definitiva.

> ⚠ **Las cifras de suite de los bloques de ACTUALIZACIÓN son snapshots históricos con su fecha**,
> no el estado actual: la cifra viva está en el **🏁 Estado de cierre** del final del track.

> **⚡ ACTUALIZACIÓN 2026-08-14: VD.1, VD.2 y VD.3 están HECHOS** (commit `ab03024`). Se adelantaron
> porque son **el mismo bucle**, no tres tickets independientes: el bug de VD.1 envenenaba el cursor
> por VD.2 y VD.3 hacía que todo ello se viera como `ok`. Arreglar solo una pata dejaba el bucle
> abierto. Quedan VD.4, VD.5 y VD.6.

> **⚡ ACTUALIZACIÓN 2026-08-15: FASE CERRADA** en dos commits — `3e11420` (motor y observabilidad:
> VD.4a + VD.4c + VD.10) y `91aa1c6` (recuperación de fuentes: VD.6 + VD.7 + VD.9 + VD.4b).
> HECHOS: VD.4 (con sus tres derivados nuevos VD.4a/VD.4b/VD.4c), VD.6, VD.7, VD.9 y VD.10.
> VD.5 se corrige (`zebis` sale: resuelto en VD.9; quedan `proz` y `remoteco`). VD.8 sigue sin
> tocar a propósito. Suite legacy: **1207 passed, 3 skipped**. Estado de cierre y residuales al
> final de la sección.

> **⚡ ACTUALIZACIÓN 2026-08-15 (bis): revisión externa post-cierre** — la fase pasó una revisión
> externa más dos rondas internas que encontraron huecos reales de la misma familia en la capa de
> **parseo**; cerrados en `259a1ec`. Después, una **segunda revisión externa** (2026-08-16), más
> otras dos rondas internas, encontró entradas patológicas en los bordes que aún rompían la letra
> de la garantía; cerradas en `db0d444`. Suite legacy — **snapshot del 2026-08-19**: **1325 passed,
> 3 skipped**. Detalle y decisiones en el estado de cierre al final de la sección.

> **⚡ ACTUALIZACIÓN 2026-08-18 (ter): tercera revisión externa** — 3 hallazgos importantes + 3
> menores, más los que sacaron cuatro rondas internas; cerrados en `0e6b821`. Dos de los
> importantes eran **bugs de identidad** (el hash oscilaba con la misma URL — no es cosmética:
> acaba en borrado a 60 días); detalle en el estado de cierre. El barrido sistemático posterior
> muestra que la garantía error≠vacío es un problema ESTRUCTURAL de ~29 fuentes → ticket nuevo
> **VD.11**. Suite legacy — **snapshot del 2026-08-19**: **1325 passed, 3 skipped** (tras la
> quinta revisión — ver
> las notas siguientes).

> **⚡ ACTUALIZACIÓN 2026-08-18 (quater): cuarta revisión externa** — el hallazgo principal se les
> había escapado a las tres revisiones anteriores: `irishjobs` aceptaba una URL **absoluta de
> cualquier host** sin validar y acababa **clicable para el usuario**; cerrado en `7d63d8d` con un
> `_resolve_job_url` del criterio de `stelle_admin`. Además, dos campos crudos podían perder la
> cosecha entera (uno, la de AMBOS hosts). Detalle en el estado de cierre. Suite legacy:
> **snapshot del 2026-08-19: 1325 passed, 3 skipped** (tras la quinta revisión — ver la nota
> siguiente).

> **⚡ ACTUALIZACIÓN 2026-08-19 (quinquies): quinta revisión externa** — más dos rondas internas.
> Dos hallazgos eran de la fase y están cerrados en `747630f`: el falso `empty` de ISP con
> ofertas ilegibles **del propio colegio** (que además emitía la página de carreras como URL de
> oferta) y el `logo` ausente que seguía destruyendo el almacenado (la premisa del test que lo
> fijaba era equivocada). Los otros tres son **defectos ESTRUCTURALES PREEXISTENTES** que no se
> corrigen aquí porque son rediseño → tickets nuevos **VD.12** (identidad construida con campos
> mutables — el más importante), **VD.13** (~9 fuentes sin validar el host de las URLs) y
> **VD.14** (`hautlac` e `iscs` emiten una URL de listado constante). Detalle en el estado de
> cierre. Suite legacy — **snapshot del 2026-08-19**: **1325 passed, 3 skipped**.

### VD.1 [S] ✅ HECHO — `stelle_admin`, causa raíz encontrada y cerrada
El scraper extrae bien el título pero **no la URL de cada oferta**: cuando el registro no tiene
`<a href>`, `url = f"{BASE_URL}{href}"` con `href=""` produce **la URL base**, así que las 7 ofertas
colisionan contra `ix_jobs_url` (UniqueViolationError ×7, 0 guardadas). Verificado ejecutando el
camino real de persistencia. Los enlaces buenos SÍ existen en el DOM renderizado, con la forma
`https://jobs.admin.ch/offene-stellen/<slug>/<uuid>`, pero el patrón del fallback (estrategia 3)
busca `/job/`, `/stelle/`, `/vacancy/`, `/detail/` y **ninguno casa con `/offene-stellen/`**.
**Arreglo:** añadir el patrón real + **SALTAR el registro si no hay URL propia** (una oferta sin URL
no es utilizable — misma regla que ya aplica el proyector del core). **DoD:** ninguna oferta sale
con la URL base; las 7 se guardan.

✅ Además del patrón `/offene-stellen/` y del salto de registros sin URL propia, la revisión encontró
un **bypass de la validación de host reproducido extremo a extremo**: `urlsplit` no corta la
autoridad en `\` pero el navegador sí, así que `https://evil.com\@jobs.admin.ch/...` pasaba el filtro
y habría acabado clicable para el usuario. Un único `_resolve_job_url()` resuelve con `urljoin`,
exige http(s), rechaza userinfo y `\`, valida host `admin.ch` y exige path propio.

### VD.2 [M] ✅ HECHO — ⚠ SISTÉMICO — el cursor aprendía identidades de ofertas que NUNCA se guardaron
`scraping_tasks` registra `store.update_after_run(cursor, fetched_identities, ...)` con TODAS las
descargadas, mientras que los fallos de persistencia se capturan uno a uno en el `try/except`
interno y solo suman a `summary["errors"]`. Consecuencia: un fallo de guardado **envenena el cursor
de forma permanente** — el early-stop da esas URLs por conocidas, la fuente devuelve 0 novedades
para siempre, la racha de vacíos crece y el backoff la aparca. Es lo que convirtió el bug de VD.1
en una fuente muda durante meses. **Arreglo:** registrar solo las identidades REALMENTE persistidas.

### VD.3 [M] ✅ HECHO — ⚠ SISTÉMICO — la salud de fuente (V.0) no cubría la PERSISTENCIA
V.0 clasifica `ok` | `empty` | `error` mirando la DESCARGA. `stelle_admin` descarga 7 ofertas ⇒ `ok`,
aunque se guarden 0. Una fuente puede estar sanísima en `source_health` y no aportar ni una fila.
**Arreglo:** añadir el resultado de persistencia a la señal de salud (p.ej. `stored=0` con
`fetched>0` ⇒ degradada). Misma familia que §3.3: la señal medida no es la realidad.

✅ Resuelto con racha propia (`consecutive_unstored`, umbral 2) separada de las de descarga. Dos
matices que costó acertar: el denominador es lo que se **intentó** guardar (los descartes
deliberados —filtro tech, ventana de cosecha— NO pueden degradar una fuente sana), y **perder el
lote entero en el commit también cuenta**, porque si no el fallo se presentaba como éxito un nivel
más arriba.

### VD.7 [S] ✅ HECHO (2026-08-15) — `financejobs` leía una ruta de `__NEXT_DATA__` que ya no existe
Verificado con sonda en vivo contra el portal: `props` solo tiene `pageProps` y `__N_SSP`; la ruta
real es `props.pageProps.jobsSSR.jobs` y el scraper lee `props.initialProps.pageProps.jobsSSR.jobs`
⇒ **devuelve lista vacía siempre**. Corrobora que `financejobs` esté entre los 12 scrapers aparcados
con `consecutive_empty_runs=6`. Otro "fallo que parece éxito": HTTP 200, 0 ofertas, veredicto
`empty`. Arreglo de una línea; el `published_at` ya está añadido y es código muerto hasta entonces.

✅ No era de una línea. Además de la ruta (el scraper navega ahora **ambas formas**, la actual y la
histórica, y si NINGUNA existe el fallo es **VISIBLE** vía fetch_diagnostics — no un "0 ofertas"
silencioso), aparecieron dos defectos más: `PAGE_SIZE` era 20 cuando el real del portal es 10 —con
20 el motor daba por terminada la paginación tras la primera página— y el interior de cada oferta
iba sin blindar: un `location: null` (JSON perfectamente normal) reventaba la página **entera**;
ahora un campo raro degrada esa oferta, no la página. En vivo: 10 ofertas/página de 1551 totales,
`published_at` poblado.

### VD.8 [S] 🔻 NUEVO (2026-08-14) — el filtro tech salta re-vistas sin comprobar si ya están guardadas
Mismo defecto que se corrigió para la ventana de cosecha, vivo en el filtro de al lado
(`fetch_tasks.py`): una oferta **ya guardada** cuyo título pase a casar con una palabra tech deja de
refrescar `last_seen_at` y acaba **borrada** por `cleanup_stale_jobs` a los 60 días. Se dejó sin
tocar a propósito: cambiarlo altera qué ofertas viven en el corpus, que es **decisión de producto**.
Está comentado en el punto exacto del código.

### VD.9 [S] ✅ HECHO (2026-08-15) — dos anomalías de fuente detectadas de paso
- **`zebis`** figura como muda por HTTP 403, pero su feed **respondió con items y `pubDate`** en la
  sonda del 2026-08-14. Es el portal de docencia suiza = el perfil objetivo: merece re-verificación
  antes de dar por buena la clasificación.
- **`thehub`** está activa en el registro pero `/api/jobs` devolvió una página de error, no JSON.

✅ Ambas resueltas:
- **`zebis` VIVO** (26-28 ofertas, todas con `pubDate`). El 403 no era el fallo real: el portal
  tiene mal configurado su `xml:base` y emite `<link>`/`<guid>` como
  `https://0.0.0.0:3000/stellen/<slug>` — sin arreglo, la siguiente cosecha habría persistido
  filas con URLs inservibles. La canonicalización nueva **nunca confía en el host del feed**: toma
  solo el path y lo reconstituye sobre `BASE_URL` con las defensas de `stelle_admin` (31 vectores
  de host confusion probados, 0 falsos negativos sobre las ofertas reales). NO se adivina el
  empleador: el feed no ofrece un patrón fiable.
- **`thehub`** migrado a `api.thehub.io/v2/jobs` + paso de detalle `/jobs/single/<id>` (el listado
  v2 ya no trae URL, description ni fechas). La URL pública se construye como `thehub.io/jobs/<id>`
  — el mismo formato de las 40 filas antiguas en BD ⇒ **dedup conservada sin migración**. Política
  de ventana FULL → WINDOW con la fecha real. En vivo: 46 ofertas, 46/46 con fecha.
- ⚠ **Deriva importante y transversal**: como el paso de detalle puede fallar en una re-vista, el
  upsert (`services/job_repository.py`) **ya no deja que un valor vacío entrante pise uno bueno
  almacenado** (description, snippet, tags, location) y la invalidación del embedding compara
  contra el valor **efectivo** de la fila. Sin esto, un fallo del detalle borraba la descripción y
  forzaba re-embeds sobre texto vacío, degradando el matching. **Afecta a TODO el corpus**, no solo
  a thehub. Un valor real entrante de location/canton sí actualiza siempre (una oferta puede
  reubicarse); solo se conserva el almacenado cuando el entrante viene vacío.

### VD.4 [L] ✅ HECHO (2026-08-15) — `swiss_schools_isb` y `gastrojob` — listados por JS
Sitios vivos (200) pero el HTML estático no trae ni un enlace de oferta. **No estaban bloqueados**:
selectores obsoletos ⇒ 200 sin datos ⇒ el detector de soft-block lo lee como anti-bot ⇒ kill-switch.
**Arreglo:** reescribir con Playwright. **Prioridad: `isb` primero** (es watchlist de colegios, el
perfil objetivo); `gastrojob` después.

✅ Cerrado en dos mitades, y ninguna necesitó Playwright:
- **`swiss_schools_isb` no estaba rota**: el board está genuinamente vacío ("No post to display")
  y los selectores actuales manejan bien ese estado. Lo que la mantenía apagada era el kill-switch
  (ver VD.4a). **No se escribieron selectores de posts** porque no hay ni uno publicado que permita
  verificarlos: queda documentado y pendiente de que el board tenga vacantes.
- **`gastrojob` (VD.4b) reescrito**: el endpoint AJAX de paginación del propio frontend responde a
  httpx puro — 1104 vacantes reales accesibles **sin Playwright**. Fecha real convertida de hora
  suiza a UTC, URL canónica (la dedup no se rompe si el portal añade parámetros de tracking),
  política de ventana FULL → WINDOW. En vivo: 10 ofertas/página, todas con URL propia y fecha.

### VD.4a [M] 🔻 NUEVO ✅ HECHO (2026-08-15) — el kill-switch apagaba fuentes SANAS (el hallazgo más importante de la fase)
Dos defectos que se realimentaban:
1. `DEFAULT_SOFT_BLOCK_MARKERS` incluía `/cdn-cgi/challenge-platform`, que es el **beacon PASIVO**
   de telemetría de Cloudflare, presente en el HTML normal de cualquier sitio servido por
   Cloudflare — no solo en las pantallas de challenge. Board vacío legítimo + beacon ⇒ "bloqueo"
   en CADA visita ⇒ 3 reportes ⇒ kill-switch ⇒ y cada reintento de la ventana de 24h refrescaba
   `last_blocked_at`: **bucle perpetuo**. Marcador retirado (con comentario fechado en el código);
   los challenges reales se siguen cazando por texto, con 3 frases de alta confianza añadidas y
   cero falsos positivos sobre las 18 fixtures reales del repo.
2. `reset_blocks` solo se llamaba `if results:`. Para una watchlist de colegios, **0 vacantes es el
   estado NORMAL durante meses**: la fuente no se rehabilitaba nunca. Ahora rehabilita todo run que
   termina SIN bloqueo (con resultados o con vacío verificado); un no-200 en Playwright ya no
   cuenta como "vacío verificado".

El bucle queda roto también para `swiss_schools_zis` y `gastrojob`. **Consecuencia de método que
merece quedar escrita: durante meses se creyó que estas fuentes estaban bloqueadas por los
portales cuando el bloqueo era NUESTRO.**

### VD.4c [S] 🔻 NUEVO ✅ HECHO (2026-08-15) — `swiss_schools_nae` e `inspired` arrastraban el mismo `if results:`
Ambos sobreescribían `fetch_jobs` entero con el bug de rehabilitación duplicado (5 colegios;
`inspired` llevaba `last_success_at` congelado desde 2026-06-22 y el healthcheck lo reportaba
"silent" a diario). Overrides eliminados; el bucle por colegio hereda ahora pre-check, rearme de
flags y la condición nueva de VD.4a. Comportamiento por colegio verificado idéntico al anterior.

### VD.10 [M] 🔻 NUEVO ✅ HECHO (2026-08-15) — los fallos de descarga de los scrapers se leían como sequía
Mitad "scraper" del bucle de fuentes mudas: `BaseScraper` descarga sin pasar por `utils/http.py`
(donde vive el diagnóstico de los providers), así que un 404, un timeout, un error de red, el
circuito abierto o un no-200 en Playwright no registraban NADA y `classify(0, [])` devolvía
`empty` ⇒ una fuente **ROTA** se presentaba como fuente **SECA** y la alerta daba el diagnóstico
equivocado. Ahora todos esos fallos (y el soft-block, y el fetch de detalle) registran diagnóstico.
Cerrada además la asimetría de `scraping_tasks`: un scraper cuyo `fetch_jobs` lanzaba no dejaba
ninguna señal de salud — un scraper que petara en cada run era invisible. Mapa de 30 escenarios de
fallo verificado; un 200 bien parseado con 0 ofertas sigue dando `empty` limpio y rehabilitando.

### VD.5 [?] `proz`, `remoteco` — bloqueo de infraestructura, NO de User-Agent
> **⚠ CORRECCIÓN 2026-08-15: `zebis` SALE de esta entrada** — resuelto en VD.9. Aquí se afirmaba
> que servía el challenge de Cloudflare en TODO el sitio; **es falso para su feed RSS**, que
> responde 200 con el User-Agent del proyecto (verificado en 3 pasadas). El diagnóstico de 403
> venía de otro camino; el fallo real era el `xml:base` roto del feed (ver VD.9).

`proz` sirve el challenge de Cloudflare (`Just a moment...`) en TODO el sitio, también
con UA de navegador real; `remoteco` no completa el handshake TLS. Igual que `medjobs`.
**Opciones, por orden de preferencia:**
1. **⭐ Alertas por email del propio portal**: el usuario se suscribe CON SU CUENTA y el sistema
   parsea el correo. Canal AUTORIZADO por el portal, llega solo lo nuevo del día, datos
   estructurados con URL real, y el patrón ya está contemplado en el ADR ("import de alertas del
   usuario") con `EmailService` disponible. **Es la vía recomendada.** (La versión anterior de esta
   recomendación decía "sobre todo para `zebis`"; ya no aplica — `zebis` cosecha por su RSS.)
2. **Pedir acceso al portal**: la formulación original apuntaba a zebis ("es una fundación
   educativa; un correo pidiendo el RSS tiene probabilidades razonables") — ya resuelta. Para
   `proz`/`remoteco`, portales comerciales, la probabilidad es menor pero la vía sigue abierta.
3. Browser stealth remoto de pago vía `SCRAPER_BROWSER_CDP_URL` (ya soportado como opt-in).
   Decisión de gasto, pendiente del propietario.

**❌ RECHAZADO — enrutar la descarga por un LLM (Gemini/Groq) para esquivar el bloqueo.**
Evaluado el 2026-08-06 y descartado por DOS motivos independientes:
- **Inservible técnicamente**: Groq no navega (inferencia pura). Gemini puede vía grounding/URL
  context, pero devuelve **texto redactado por el modelo**, no el feed: sin garantía de completitud,
  sin URLs ni IDs estables para deduplicar o para aplicar, con riesgo de alucinación en campos que
  se guardarían como datos reales, y con cuotas de free tier que no se controlan.
- **Es evasión de un control de acceso**: el 403 no es un fallo, es el portal rechazando tráfico
  automatizado. Usar un tercero cuya IP no se asocie con la nuestra contradice frontalmente la
  postura del proyecto (ComplianceEngine, lista de portales prohibidos, principio de no-evasión de
  `docs/SCRAPING_HUMANO.md`). **No reabrir sin cambiar antes esa postura de forma explícita.**

### VD.6 [S] ✅ HECHO (2026-08-15) — `authenticjobs`, `dailyremote`, `translatorscafe` — feeds RETIRADOS
Los tres sitios viven pero sus feeds ya no existen (404/403 en todas las rutas probadas).
`authenticjobs.com/feed/` responde 200 pero es el **blog** (artículos editoriales; WordPress no
expone ningún tipo de contenido "job") — usarlo **contaminaría el corpus**. Nunca han aportado una
sola oferta y son tableros remotos genéricos, lejos del perfil objetivo.
**Recomendación: RETIRAR del registro documentando el motivo**, en vez de mantener una cobertura
que no existe. Alternativa (mayor coste): escribir scrapers nuevos.

✅ Ejecutada la recomendación, tras re-sondeo del 2026-08-14: los tres feeds siguen 404 y
`authenticjobs.com/feed/` sigue siendo el blog editorial. Retirados del registro (28 → **25
providers**) con motivo y fecha en el propio código, **ficheros conservados** (el parser vale si el
portal republica), política de ventana conservada y fuentes añadidas a `SOURCES_DECIDED_IN_ADVANCE`
para que el registro siga completo.

### VD.11 [L] 🔻 NUEVO (2026-08-18) — la garantía «un 200 ilegible acaba como `error`, no como `empty`» solo la cumplen 6 de ~53 fuentes
**Diagnóstico (barrido sistemático del 2026-08-18, por lectura de código de TODAS las fuentes de
`backend/providers/` y `backend/scrapers/`):** solo `financejobs`, `gastrojob`, `thehub`, `zebis`,
`irishjobs` y `swiss_schools_isp` cumplen hoy la garantía que esta fase declara. Las ~29 restantes
tienen al menos un camino que devuelve `[]` sin registrar diagnóstico, así que una fuente **ROTA**
se presenta como **SECA** — exactamente el patrón que dejó nueve fuentes mudas 66 días. La clase
de fallo no es de fuentes sueltas: es estructural.

**Focos por rentabilidad:**

| Foco | Por qué |
|---|---|
| **`swiss_schools_base`** | La base HTML no registra ningún fallo de estructura y de ella heredan **7 colegios de la watchlist**, exentos del backoff: se consultarían a diario en vano y en silencio. Un arreglo cubre los 7 |
| **`publicjobs`** | No usa el helper HTTP común: `return []` ante excepción, ante no-200 y ante JSON ilegible ⇒ **un 404 sale como `empty`** — el bug original de la fase, intacto en un provider activo |
| **`ostjob` / `zentraljob`** | Clones sobre `base_chmedia`: un redeploy de la API de CH Media las silencia a las dos a la vez |
| **`stelle_admin`** | Es la fuente cuyo bug abrió este track |

Detrás, por severidad: `schuljobs`, `myscience`, `tes`, `arbeitnow`, `jobgether`, los RSS
(`euremotejobs`, `globaljobs`, `jobspresso`, `weworkremotely`, `proz`), `remotive`,
`workingnomads`, `nav_arbeidsplassen`, y las key-gated (`jsearch`, `adzuna`, `careerjet`,
`jooble`).

**Patrón a replicar:** el de `financejobs` — `isinstance` por nivel + guard "N elementos y ninguno
parseable" + `diag.record`. **Con la cautela que costó descubrir:** si la fuente filtra DESPUÉS
del parseo (como el tenant compartido de Workday en `swiss_schools_isp`), el guard debe mirar el
**tipo**, nunca el número de coincidencias — o se convierte en un falso positivo que apaga
fuentes sanas y rompe la vía de rehabilitación.

**DoD:** cada fuente tocada, con test que falle sin su arreglo y evidencia en vivo de que un
vacío legítimo sigue dando `empty` con 0 issues.

**Por qué no se hizo en la Fase 3:** cerrar 29 fuentes excede su alcance, y hacerlo sin las dos
rondas de análisis por sección contradiría la disciplina que lo encontró. Registrado también en
`DEUDA_TECNICA.md` §1.13 (el hallazgo más importante del inventario).

### VD.12 [L] 🔻 NUEVO (2026-08-19) — ⚠ ESTRUCTURAL — la identidad de oferta se construye con campos MUTABLES: pérdida de datos activa por diseño
**El más importante de los tres de la quinta revisión. Diagnóstico (verificado en el código):**
la clave primaria es `MD5(title|company|url)` (`models/job.py:18`; el cálculo vive en
`job_service.compute_hash`), la columna `url` tiene **índice único** (`models/job.py:30-31`) y el
upsert resuelve conflictos **por hash** (`job_repository.py:359`,
`on_conflict_do_update(index_elements=["hash"])`). Consecuencia: si un portal **corrige un
título** —algo perfectamente normal—, el hash cambia, el `ON CONFLICT` no encuentra la fila,
intenta insertar y **choca con el índice único de URL**: la oferta deja de refrescar
`last_seen_at` y `cleanup_stale_jobs` la borra a los 60 días. Es pérdida de datos activa por
diseño, no una garantía sin extender.

**La lectura que da valor:** esto explica retroactivamente las dos rondas que se dedicaron a
estabilizar la identidad en `financejobs` y `gastrojob` (tercera revisión, `0e6b821`) — **eran
síntomas; la causa es que la identidad se construye con campos mutables**. Mientras la identidad
dependa de `title` y `company`, cada fuente nueva es candidata a reproducir la misma clase de bug.

**Secuencia de migración propuesta (séptima revisión externa, 2026-08-20)** — expand/contract, en
este orden exacto: (1) añadir `job_id` UUID inmutable y `source_external_id`; (2) backfill de UUID
e IDs de fuente, **fuente a fuente y con informe de colisiones**; (3) añadir `job_id` a
candidaturas, matches y documentos generados, con backfill desde `job_hash`; (4) dual-write y
validación de las FKs nuevas; (5) cambiar el upsert a `(source, source_external_id)`, con URL
canónica solo como fallback acreditado; (6) cambiar las lecturas; (7) retirar `job_hash` **solo**
tras la ventana de observación. **Qué se rompe en el orden equivocado:** cambiar primero la PK
rompe las FKs; cambiar solo el `conflict target` puede **fusionar URLs recicladas**.

**Contención mínima mientras el legacy siga escribiendo** (misma revisión): ante misma URL con hash
nuevo, bloquear la fila, **conservar su hash histórico** y actualizar contenido solo cuando fuente
e identidad acrediten que es la misma oferta; si no, cuarentena y alerta. El revisor **no
recomienda la migración completa** si el legacy va a dejar de escribir a corto plazo — decisión que
depende del calendario de la cosecha nativa, no de este ticket.

**DoD:** identidad estable — preferentemente `(source, source_id)` con URL canónica como
fallback — **y migración cuidadosa**: `hash` está referenciado con FK desde candidaturas
(`models/job_application.py:31`), matches (`models/match_result.py:34`) y documentos generados
(`models/generated_document.py:29`) — cambiar solo el `conflict target` no arregla el contrato
aguas abajo.

**Matiz verificado (2026-08-19, redacción completa en `PLAN_UNIFICACION_JOBHUNTING.md` §6):** el
modelo del core resuelve este defecto **por diseño** (slot estable + revisiones versionadas por
hash), pero **la migración de datos por sí sola NO lo extingue**: mientras el legacy siga siendo
el cosechador, la proyección sombra usa su `hash` como `external_id`
(`jobhunt_core/shadow/projector.py:734,745`) — el core **hereda la identidad mutable** y no puede
recuperar lo que el legacy nunca persistió. Tampoco basta la cosecha **NATIVA** por sí sola: la
identidad la fija el `external_id` que elige cada adaptador, y el único nativo que existe hoy usa
`slug or url` con el título dentro del slug (ver **VD.15**). "Migrar al core lo arregla", tal cual,
es falso; el modelo del core es condición necesaria, no suficiente.

### VD.13 [M] 🔻 NUEVO (2026-08-19) — ⚠ ESTRUCTURAL — barrido de G3: ~9 fuentes aceptan URLs del portal sin validar el host
**Diagnóstico (verificado en el código):** el mismo agujero que la cuarta revisión cerró en
`irishjobs` (`7d63d8d`: una URL absoluta de cualquier host acababa clicable para el usuario),
replicado en ~9 fuentes que emiten URLs derivadas de datos del portal sin validar el host
resultante: `swiss_schools_ecolint` (:62), `swiss_schools_isb` (:78), `swiss_schools_nae` (:110),
`swiss_schools_inspired` (:96), `swiss_schools_zis` (:50-71 — filtra solo por substring
`schoolspring.com` en el href y lo emite crudo), `schuljobs` (:60), `myscience` (:54), `tes`
(:78) y `publicjobs` (:133). Verificado que hoy **no hay contaminación en el corpus**, pero el
camino es alcanzable.

⚠ Dos cautelas que impiden un rechazo global ingenuo: `zis` necesita permitir explícitamente
`zurichinternational.schoolspring.com` (su ATS legítimo), y los agregadores que enlazan al
empleador necesitan una excepción declarada — rechazar hosts ajenos globalmente crearía falsos
positivos y rompería fuentes sanas.

**Propuesta de diseño de la revisión (recogida para el ticket):** un registro
`fuente → política de URL` (portal propio / ATS permitido / enlace externo), validación común en
la frontera de persistencia, y un **test parametrizado que falle si una fuente carece de
política** — así una fuente nueva no puede nacer sin declarar la suya.

**DoD:** ninguna fuente emite una URL cuyo host no cubra su política declarada; el test
parametrizado recorre el registro completo; las ~9 fuentes listadas migradas sin falsos
positivos sobre sus ofertas reales.

### VD.14 [S] 🔻 NUEVO (2026-08-19) — `hautlac` e `iscs` emiten una URL de listado CONSTANTE: solo puede persistir una oferta por colegio
**Diagnóstico (verificado en el código):** ambos scrapers emiten la URL del listado como URL de
TODAS sus ofertas (`swiss_schools_hautlac.py:74` — `HAUTLAC_URL`; `swiss_schools_iscs.py:89` —
`ISCS_URL`). Con el índice único de URL (`models/job.py:30-31`), si un colegio publica dos
vacantes a la vez la segunda choca contra el índice y **se pierde en silencio**. Hoy hay 1 fila
por colegio en el corpus, así que el efecto está **latente**, no activo. Ambos scrapers **sí**
producen `source_id` (slug del título), así que hay material para la solución.

**DoD:** dos vacantes simultáneas del mismo colegio persisten las dos. Arreglo transitorio
posible (fragmento determinista sobre la URL, p. ej. `#<source_id>`); mejor, resolverlo junto
con la identidad estable de VD.12, que elimina la colisión de raíz.

**Matiz verificado (2026-08-19):** este defecto **NO desaparece en el core**: con
`UNIQUE(source_id, url_normalized)`, el segundo listing de una fuente con URL constante se
**salta** (`jobhunt_core/harvest/sink.py:337-345`, con `logger.warning` en :343) — pasa de
pérdida silenciosa a pérdida **visible**, que es mejor, pero la URL por-oferta hay que arreglarla
en la fuente igualmente.

### VD.15 [M] 🔻 NUEVO (2026-08-19) — ⚠ CONTRATO — ningún adaptador nativo del core acredita identidad estable
**Origen:** sexta revisión externa (hallazgo importante). **Código del core — lo lleva otro
agente; este ticket documenta el contrato, no lo implementa.**

**Diagnóstico (verificado en el código):** el único adaptador nativo que existe hoy resuelve la
identidad como `external_id = item.get("slug") or url`
(`jobhunt_core/harvest/providers/arbeitnow.py:221`; mismo criterio en el log de :124). El slug de
arbeitnow **contiene el título**, así que una corrección de título en el portal produce un
`external_id` distinto: se abre un slot NUEVO en vez de una revisión del existente. Es la MISMA
clase de defecto que VD.12, replicada dentro del core.

Agravante: los fixtures de test usan slugs sintéticos (`"a"`, `"b"`, `"c"`…), así que **ningún
test ejercita un slug realista** ni verifica la estabilidad. El contrato no está probado en
ninguna parte.

**Por qué importa más allá de un adaptador:** el plan usaba "el defecto se extingue con la cosecha
nativa" como argumento material a favor de la migración. Es falso tal cual: el modelo del core
(slot estable + revisiones versionadas) es condición **necesaria pero no suficiente** — la
garantía la aporta cada adaptador al elegir su `external_id`. Sin un contrato explícito, cada
adaptador nuevo puede reintroducir VD.12 y la migración no cierra nada.

**DoD:** (1) contrato escrito en `CONTRATOS_FASE_A.md`: qué cualifica como identidad estable
(identificador del portal ajeno a título/empresa/ubicación; URL canónica solo como fallback
declarado y justificado por fuente); (2) `arbeitnow` migrado a un identificador que no derive del
título, o justificación documentada de por qué su slug es inmutable aguas arriba —con evidencia
del portal, no por suposición; (3) un test por adaptador que demuestre que un cambio de título
NO cambia el `external_id`, con fixture de slug realista; (4) checklist de alta de adaptador que
exija esa prueba antes de aceptar la fuente.

**Cautela:** si el slug de arbeitnow resultara efectivamente inmutable, el arreglo correcto sigue
siendo declararlo y probarlo — no dejarlo implícito. El coste de equivocarse es pérdida de
historial, que no se recupera a posteriori.

**Séptima revisión externa (2026-08-20) — confirmación independiente y borrador de contrato.** Un
revisor externo verificó **los dos matices** contra el código y los suscribe. Sondeó además la API
pública de arbeitnow: **conservar el sufijo numérico cambiando el título devolvió 410**, lo que
**no demuestra** que el sufijo sea inestable pero **tampoco acredita** lo contrario — sigue sin
haber inmutabilidad documentada, así que VD.15 se mantiene en severidad alta. Borrador de contrato
propuesto, como punto de partida para `CONTRATOS_FASE_A.md`:

> Cada adaptador debe emitir un `external_id` no vacío, **opaco, estable y único** dentro de la
> fuente o tenant. Debe derivarse de un **identificador inmutable del sistema origen** y no de
> título, empresa, descripción, ubicación, fecha ni slug mutable. La URL solo puede usarse como
> fallback si es una URL canónica individual, estable, sin tracking y **con estabilidad
> documentada**. Sin identidad acreditada, la entrada se **cuarentena**.

DoD por adaptador, en la forma propuesta por el revisor: cambio de título/empresa/URL con el mismo
ID ⇒ **mismo slot y nueva revisión**; reciclado real del ID ⇒ **nueva encarnación** sin sobrescribir
historia; falta de ID estable ⇒ **cuarentena**; y **fixtures realistas**, nunca `"a"`/`"b"`.

### VD.16 [M] 🔻 NUEVO (2026-08-19; AMPLIADO 2026-08-20) — texto no almacenable cuesta la OFERTA entera
**Origen:** sexta revisión externa (H2) — el hallazgo era solo el `logo`, **ya arreglado**; el
barrido posterior muestra que el logo era un caso particular de algo general.

**Diagnóstico (verificado empíricamente contra la BD del contenedor, sin tocar tablas):** un byte
NUL en un campo `text`/`varchar` aborta el INSERT con `CharacterNotInRepertoireError`, y en `jsonb`
con `UntranslatableCharacterError`. Vector de entrada realista: un JSON de portal con el escape
`\u0000` — `json.loads` lo convierte en `"\x00"` sin protestar. Coste: en un alta la oferta se
pierde; en una re-vista no refresca `last_seen_at` y a 60 días `cleanup_stale_jobs` la borra.

**Mapa por campo (del informe del agente):**
- `title`, `company`, `url` — **identidad** (`hash = MD5(title+company+url)`, además NOT NULL):
  omitir NO es opción. Camino recomendado: `ValueError` en frontera, el mismo patrón que la URL
  >2048, que degrada SOLO esa oferta con mensaje claro. Sanitizar (quitar el NUL) se **descarta**:
  almacenaría una identidad distinta de la hasheada y, si el portal corrige el NUL, el hash cambia
  igual y duplica.
- `description`, `description_snippet`, `location`, `canton`, `salary_*`, `language`,
  `employment_type` — contenido: `pop` con rastro, como el logo ya arreglado.
- `tags` (JSONB) — **verificado**: un `\u0000` en un elemento aborta el INSERT. NO popear el campo
  entero (perdería tags de matching, incluido `school.id`): filtrar solo las tags con NUL.
- `salary_period`, `seniority`, `contract_type` (ENUM) — falla antes, en el bind de SQLAlchemy
  (`LookupError`); es el caso general "valor no-miembro", no específico de NUL.
- `hash`, `fuzzy_hash`, `content_hash`, `source`, `category` — NUL imposible en la práctica.

**AMPLIACIÓN (séptima revisión, 2026-08-20) — no es solo el NUL.** Un **surrogate Unicode
aislado** (`\ud800`, que `json.loads` acepta sin protestar) revienta **antes de llegar a
PostgreSQL**: `_content_hash()` usa `ensure_ascii=False` y luego `.encode()`
(`backend/services/job_repository.py:100` y `:230`), y eso lanza `UnicodeEncodeError`. El problema
real, entonces, no es "el byte NUL" sino **texto que no es representable como UTF-8 válido**, y hay
dos fronteras distintas donde estalla (el hash de contenido en Python, y el driver de Postgres).
El DoD de abajo cubre ambas. Cautela añadida: los tests deben incluir **Unicode válido**
(acentos, CJK, emoji) para que el endurecimiento no cree falsos positivos.

**Por qué no se abordó ahora:** el encargo de la sexta revisión era el `logo`; tocar los campos de
identidad exige decidir entre degradar la oferta y alterar la identidad, que es una decisión de
producto, no de implementación. **Severidad: media** — pérdida total de oferta, pero **nunca
observado en producción**: hace falta que un portal emita `\u0000`. **Coste:** una ronda con sus
dos análisis. **DoD:** un NUL en cualquier campo degrada como mucho ese campo (o esa oferta, si es
de identidad), nunca en silencio, con test por familia de campo.

### 🏁 Estado de cierre del TRACK V-DIFERIDO (2026-08-15)
Fase de recuperación de fuentes **CERRADA** — commits `3e11420` (VD.4a + VD.4c + VD.10),
`91aa1c6` (VD.6 + VD.7 + VD.9 + VD.4b), `259a1ec` (huecos de la revisión externa, ver abajo),
`db0d444` (segunda revisión externa: entradas patológicas en los bordes, ver abajo), `0e6b821`
(tercera revisión externa: identidad inestable, valores en blanco y la garantía de parseo en
irishjobs e ISP, ver abajo), `7d63d8d` (cuarta revisión externa: la validación de host que
faltaba en irishjobs, campos crudos que perdían la cosecha y el logo del upsert, ver abajo) y
`747630f` (quinta revisión externa: el falso `empty` de ISP con ofertas ilegibles del propio
colegio y el `logo` ausente que destruía el almacenado, ver abajo), `3cb91a7` (**sexta revisión
externa**: forma de `externalPath` en ISP y el logo con byte NUL que costaba la oferta entera) y la
`d24cbe6` (**séptima revisión externa**: el percent-encoding que eludía esa misma validación de
forma) y la **octava revisión externa** (el tercer nivel de codificación, los octetos que no son
texto, y la retirada de una regla propia que era falso positivo).
Suite legacy: **1381 passed, 3 skipped** (base 1207 + 28 tests de la primera revisión + 12 de la
segunda + 39 de la tercera + 28 de la cuarta + 11 de la quinta + 29 de la sexta + 20 de la
séptima + 7 netos de la octava).

**Séptima revisión (2026-08-20) — lo que cerró y lo que dejó abierto.** El validador de forma que
introdujo la sexta miraba solo los caracteres CRUDOS, así que `%00`, `%GG`, `%252E%252E`, `%255C` y
`%2F` lo atravesaban enteros y volvían a producir ofertas fantasma con `outcome=ok` — el mismo
defecto por la puerta del encoding. Ahora la sintaxis `%HH` es estricta, la inspección decodifica
**en bytes** hasta dos niveles (para cazar la doble codificación) y la ruta que se persiste es
siempre la **original**. Se rechaza además el `;` crudo (parámetro de path de RFC 3986: los stacks
Java lo recortan ANTES del routing, así que la URL puede direccionar otro recurso) mientras que el
`%3B` codificado se acepta como dato. La ronda de análisis 2 del implementador encontró por su
cuenta un **escape de `UnicodeEncodeError`** —un sustituto UTF-16 suelto que `json.loads` acepta—
que salía de `fetch_jobs` con 0 issues: G1 exacta, cerrada con guard y dos tests. Quedaron fuera y
van a ticket: el Unicode inválido del **repositorio** (VD.16, que ya no es solo el NUL) y el estado
documental desincronizado (corregido en este mismo commit, ver `DEUDA_TECNICA.md` §2.2 y §5).

**Octava revisión (2026-08-20) — dos escapes y una lección sobre las afirmaciones.** El techo de
dos decodificaciones dejaba pasar la misma evasión un nivel más allá (`%25252E%25252E` → 404 en la
API real, pero `outcome=ok` para nosotros); se arregla **fallando cerrado** si tras las dos
inspecciones sobrevive un triplete `%HH`, no subiendo el techo — subirlo solo movería el problema
al cuarto nivel. Y operar en bytes **no validaba** que esos bytes fueran texto: pasaban un
surrogate codificado y una barra overlong, ambos 400 en la API real. Esto **desmiente una
afirmación del commit anterior**: operar en bytes solo evitaba que la inspección crashease, es
decir, cambiaba una excepción por una aceptación silenciosa — G1 por otra puerta. Se retira además
el rechazo del `;` crudo, que era un **falso positivo propio**: la sonda en vivo demuestra que
`...JR210499;foo` devuelve 200 y lleva a la misma oferta. **Lección registrada:** que algo no lance
excepción no significa que valide, y una afirmación de seguridad en un mensaje de commit merece su
propio test adversarial.

**Revisión externa post-cierre (2026-08-15, commit `259a1ec`).** Tras el cierre, la fase pasó por
una revisión externa que encontró defectos reales, más dos rondas internas que encontraron cinco
huecos adicionales de la misma familia. El patrón es lo que merece quedar escrito: la garantía que
la fase declara —un 200 ilegible acaba como `error` en source_health, nunca como `empty`— se
cumplía en la capa de **DESCARGA** (lo que cubrió VD.10: 404, timeouts, errores de red) pero **no
en la de PARSEO**: los parsers que devuelven `[]` sin pasar por el diagnóstico. Es la misma familia
de fallo que toda la fase vino a cerrar, un nivel más adentro. Cerrado en `259a1ec` para `thehub`,
`zebis`, `gastrojob`, `financejobs`, el upsert (`job_repository`) y `utils/http.py`.

**Decisión registrada — regla de la revisión externa RECHAZADA para `gastrojob`.** La revisión
proponía acumular stubs y exigir `observados >= anunciados`; se **rechazó tras verificarla en
vivo**: habría marcado como ROTA una fuente sana, porque el contador del portal cuenta también los
anuncios de partner que el parser descarta a propósito. Se implementó en su lugar el rango derivado
del contador (`ceil(anunciadas / PAGE_SIZE)`), que no tiene ese falso positivo. Es exactamente el
fallo inverso al que la fase vino a eliminar —declarar rota una fuente sana rompería la vía de
rehabilitación de las fuentes secas— y por eso queda escrito aquí.

**Segunda revisión externa (2026-08-16, commit `db0d444`).** La fase pasó una segunda revisión
externa que encontró 6 defectos, más 3 que hallaron las dos rondas internas posteriores. **Ninguno
bloqueante** —la red de seguridad de los tasks convierte todos esos casos en veredicto `error`
visible, nunca en `empty` silencioso— pero violaban la letra de la garantía ("el parseo no lanza
excepciones que escapen"). Lo interesante para el registro es de qué clase eran: **entradas
patológicas en los bordes** — un cuerpo 200 no UTF-8; un título que queda vacío al limpiarlo; un
`tags=None` que aborta el savepoint; un contador con miles de dígitos que desborda un `math.ceil`;
un id decimal no canónico que duplicaría filas. Cerrados en `db0d444` (13 mutantes aplicados, los
13 cazados por su test).

**Tercera revisión externa (2026-08-18, commit `0e6b821`).** 3 hallazgos importantes + 3 menores,
más los que sacaron cuatro rondas internas. Lo que merece quedar escrito es **de qué clase eran**:
- **(i) Dos bugs de IDENTIDAD** — un `companyName` en blanco en `financejobs` (pasaba el fallback
  y acababa como cadena vacía, mientras un `None` acababa como "Unknown": dos identidades para la
  misma oferta) y la empresa del detalle en `gastrojob` (sustituía a "Unknown" ANTES de calcular
  el hash, así que un fallo transitorio del detalle cambiaba la identidad) — que hacían **oscilar
  el hash con la misma URL**. Y eso no es cosmética: la re-vista choca con el índice único, la
  oferta deja de refrescar `last_seen_at` y `cleanup_stale_jobs` la borra a los 60 días. En
  `gastrojob` se eliminó el fallback — **la identidad manda sobre el enriquecimiento** — con coste
  medido y documentado: entre un 3 % y un 13 % de ofertas quedan sin empresa (varía por día; no es
  un fallo del regex sino anuncios que el portal publica anonimizados).
- **(ii) Valores de solo espacios** que el NULLIF del upsert no cubría: una re-vista con `"   "`
  destruía descripción, snippet, tags, ubicación y cantón buenos, y de paso invalidaba el
  embedding. Y un `tags=None` que abortaba el savepoint entero (jsonb_array_length no puede medir
  un escalar): la oferta no se persistía.
- **(iii) La garantía de parseo extendida a `irishjobs` e `ISP`**, las dos que quedaban a medias.
  En `irishjobs`, además de los tres caminos que devolvían `None` sin registrar nada, la
  estructura interna del blob; se capturó una búsqueda sin resultados en ambos hosts para
  confirmar que un vacío legítimo SÍ trae el blob — sin esa comprobación, el arreglo habría creado
  el fallo inverso. En `swiss_schools_isp`, el guard mira el TIPO y nunca el filtro de colegio
  (tenant de Workday compartido: cero coincidencias con objetos válidos es lo normal).

Verificación de la ronda: 19 mutantes aplicados y los 19 cazados; equivalencia de la reestructura
de `irishjobs` byte a byte contra HTML real de los dos hosts; matriz completa location × canton
(9 combinaciones) contra Postgres; sondas en vivo de las cinco fuentes tocadas. De paso, tres
arreglos de forma: la preferencia de causa raíz en el resumen de salud pasa de emparejar prefijos
de string a un campo `root_cause` tipado en FetchIssue; la cota "la URL cabe en la columna" pasa a
aserción central del repositorio derivada del propio modelo; y ese guard usa `raise` en vez de
`assert` (bajo `-O` los assert desaparecen). El barrido sistemático posterior de las ~53 fuentes
abre el ticket **VD.11** (ver arriba).

**Cuarta revisión externa (2026-08-18, commit `7d63d8d`).** Más dos rondas internas. El hallazgo
principal **se les había escapado a las tres revisiones anteriores**: `irishjobs` emitía la URL de
la oferta con `rel_url if rel_url.startswith("http") else host + rel_url` — una URL **absoluta de
cualquier host** pasaba sin validar esquema, userinfo ni hostname, llegaba al corpus y se hacía
**clicable para el usuario**. Es la misma garantía que sí se aplicó con cuidado en `zebis`,
`gastrojob` y `stelle_admin`, y se escapó en la fuente que llegó más tarde. Cerrado con un
`_resolve_job_url` del mismo criterio: rechaza `\` y `%5C`, caracteres de control, userinfo,
esquemas que no sean http(s) y cualquier puerto explícito; resuelve las relativas con `urljoin`;
las absolutas solo hacia los hosts derivados de la constante; y emite la URL reconstruida sobre el
host propio, sin query ni fragmento. Lo demás:
- **Dos campos viajaban crudos** hasta código que asumía su tipo: los escalares de texto hacían
  escapar un `AttributeError` que perdía la cosecha de la página; y el `id` sin sanear reventaba
  el set de deduplicación con un `TypeError` y perdía la cosecha de **AMBOS hosts** — una sola
  oferta corrupta. También `int(inf)` quedaba fuera del try en el parseo de salario.
- **`swiss_schools_isp`**: un `locationsText` no-string tumbaba el lote entero; ahora degrada ese
  item y sigue con los válidos. El guard de página sigue mirando **el tipo** y nunca los aciertos
  del filtro (tenant de Workday compartido: cero coincidencias con objetos válidos es lo normal).
- **`job_repository`**: un logo desbordado se degradaba a `None`, y ese `None` entraba en el
  `ON CONFLICT` y **pisaba el logo bueno almacenado** — contradecía el objetivo declarado de
  degradar solo el dato inválido. Ahora el campo se omite del INSERT.

Verificación de la ronda: ~45 vectores adversariales sobre `_resolve_job_url` —userinfo,
homoglifos, punycode, punto ideográfico y fullwidth, doble encoding, trailing dot, IPv6
malformado, puertos, `data:` y `blob:`, traversal, URL de 5 KB— sin emitir un solo host ajeno ni
dejar escapar una excepción; y, lo que más importa para no romper la garantía inversa, las 48 URLs
reales de los dos portales llegan **relativas** y ninguna se pierde. Se verificó además el camino
completo de la URL desde el blob hasta la fila: nadie la recompone ni la decodifica aguas abajo. Y
la auditoría del **corpus real (407 filas de irishjobs)** da cero URLs con query, fragmento o host
en mayúsculas, así que la reconstrucción preserva las identidades ya almacenadas. 22 mutantes de
comportamiento aplicados y todos cazados; los 5 tests que no discriminan están rotulados como
controles, y la ronda corrigió cuáles son: el de ambos hosts SÍ discrimina —es el único que caza
una derivación incorrecta de la lista blanca— y el de backslash resultó ser defensa redundante
bajo fallo único, que se conserva documentada porque protege frente a un refactor que devolviera
la URL cruda (ver residuales).

**Quinta revisión externa (2026-08-19, commit `747630f`).** Más dos rondas internas. Solo dos
de sus hallazgos eran de esta fase; los otros tres son **defectos estructurales preexistentes**
que van a ticket propio (**VD.12**, **VD.13** y **VD.14**, ver arriba) porque corregirlos es
rediseño, no un fix de la fase. Los dos cerrados:
- **El falso `empty` de ISP con ofertas ilegibles del propio colegio.** El guard anterior solo
  validaba `locationsText`: una oferta que SÍ coincide con el colegio pero con `title` no-string
  o `externalPath` vacío llegaba a la normalización, se descartaba con log y **sin registrar
  issue**, y el run salía `empty` — fuente rota presentada como seca, la clase de fallo que la
  fase vino a eliminar. Peor: un `externalPath` vacío emitía **la página de carreras del tenant
  como URL de la oferta**, que no es una URL propia. Ahora ese caso registra un issue por item y
  se descarta, nombrando cuál de los dos campos falló; y un `bulletFields` degenerado ya no
  revienta — se repara con `externalPath`, que es determinista, así que no introduce oscilación
  de identidad. El guard vive DESPUÉS del filtro por colegio **a propósito**: el tenant de
  Workday es compartido y cero coincidencias con objetos válidos de otros colegios es el estado
  normal — diagnosticarlo apagaría una fuente sana. Medido en vivo: 0 descartes sobre las 2
  ofertas reales del colegio y 0 sobre 20 del tenant completo.
- **El `logo` ausente que destruía el almacenado.** El test previo que fijaba ese comportamiento
  **partía de una premisa equivocada**, y así queda escrito: los productores construyen el valor
  con `.get("logo")`, así que no pueden distinguir "el portal lo retiró" de "este fetch no lo
  trajo" — tratar la ausencia como borrado autoritativo es interpretar como intención lo que es
  falta de dato. Ahora `None`, cadena vacía, solo espacios y tipos no-string se omiten del
  INSERT. Solo `logo` — sin coalesce genérico: `False`, `0` y algunos `None` son datos legítimos
  en otras columnas. De propina se cierra un vector de pérdida de oferta entera: un logo
  no-string abortaba el savepoint con un error del driver y se perdía la oferta completa (ese
  caso sí deja rastro en el log: es bug del productor, no ausencia normal).

Se comprobó **campo por campo** si algún otro arrastraba la misma premisa que `logo`: **solo
`employment_type`**, y por el mismo motivo — sus productores lo sacan de un fetch de detalle que
puede fallar por su cuenta (ver residuales). `salary_*` y `language` NO deben cambiar: degradan
atómicamente con el listado, así que ahí un valor ausente sí significa plausiblemente que el
portal no lo publica — protegerlos enmascararía retiradas legítimas.

Verificación de la ronda: 6 mutantes aplicados y cazados; los 5 tests que no discriminan son
controles declarados, y uno resultó más fuerte de lo previsto — caza que alguien mueva el guard
delante del filtro por colegio, que es la regresión que custodia. El guard no altera la
paginación: la decisión de pedir más páginas usa el conteo crudo, no el de ofertas emitidas.

**Nota operativa — caída completa de zebis.ch (2026-08-15).** El portal estuvo caído **entero**
unas 24 horas: 404 en todo el sitio. **No era un bloqueo**: es un frontend Next.js sobre Netnode
detrás de Cloudflare (evidencia: cabecera `content-security-policy: frame-ancestors ...
https://frontend-zebis-ch-main.netnode.app`, `x-powered-by: Next.js`, `server: cloudflare`), y el
404 de 19 bytes en `text/plain` es la respuesta de un router sin ruta, no una página de error de
Cloudflare ni de Next.js. Eso explica también el `xml:base` con `0.0.0.0:3000` que motivó VD.9.
Límite de la evidencia, dicho honestamente: **no se capturaron las cabeceras durante la caída**,
solo el cuerpo, así que la causa exacta (un redespliegue, una ruta perdida) es la lectura más
plausible, no un hecho probado. Y lo importante: durante toda la caída el pipeline lo clasificó
como `error` con su issue, no como `empty` — la validación en producción real de la garantía que
esta fase vino a instalar. Ya recuperado (2026-08-16): 24 ofertas, URLs canónicas y fecha.

**Queda vivo:**
- **VD.5** — solo `proz` y `remoteco` (vía recomendada: alertas por email del propio portal).
- **VD.8** — sin tocar a propósito: es decisión de producto.
- **VD.11** — la garantía error≠vacío en las ~29 fuentes restantes (ticket nuevo del barrido
  sistemático del 2026-08-18, ver arriba).
- **VD.12** — la identidad de oferta se construye con campos mutables (quinta revisión,
  2026-08-19, ver arriba): pérdida de datos activa por diseño; explica la clase de bugs de
  identidad de toda la fase.
- **VD.13** — ~9 fuentes aceptan URLs del portal sin validar el host (quinta revisión, ver
  arriba).
- **VD.14** — `hautlac` e `iscs` con URL de listado constante: solo una oferta persistible
  por colegio (quinta revisión, ver arriba).
- **Selectores de posts de `swiss_schools_isb`** — pendientes de que el board publique alguna
  vacante contra la que verificarlos (hoy está genuinamente vacío).

**Residuales conocidos** (documentados en los commits y en el código; revisados tras `259a1ec` —
los seis primeros siguen abiertos, los tres siguientes son nuevos de esa ronda —, de nuevo tras
`db0d444` —ninguno de los anteriores se cierra, y esa ronda añade dos—, otra vez tras `0e6b821`
el 2026-08-18: **se cierran tres**, marcados abajo con su commit, y se añaden dos, y de nuevo tras
`7d63d8d` el 2026-08-18 —ninguno de los anteriores se cierra, verificado en el código, y esa ronda
añade el suyo—, y una vez más tras `747630f` el 2026-08-19 —**ninguno de los anteriores se
cierra**, verificado en el código, y la quinta ronda añade los dos últimos—):
- Selectores obsoletos sobre un 200 siguen leyéndose como "vacío verificado" (los cubre la racha
  de vacíos de source_health).
- Una fuente apagada por compliance se lee como `empty` en source_health.
- `content_hash` versiona el payload entrante: oscila un run tras una re-vista degradada (su único
  lector es un CAS auto-consistente).
- El paso de detalle de `thehub` re-baja las 46 ofertas cada run aunque no haya novedades.
- `schuljobs.py:172` atribuye el fallo del scroll a LISTING_URL.
- `swiss_schools_isp` con JSON válido no-dict escapa al backstop en vez de degradar solo esa
  página — ~~sigue abierto: `259a1ec` añadió el chequeo de tipo a `thehub`, no a ISP~~
  → **✅ CERRADO en `0e6b821`**: `isinstance` por nivel (dict raíz, lista de `jobPostings`, dict
  por posting), verificado en el código.
- **NUEVO — `gastrojob`, techo del "partner-only"**: una página compuesta solo por anuncios de
  partner **dentro** del rango es un estado sano real (sonda 2026-08-15: p1–p5 son 100 % propias,
  p10 ya mezcla 8+2, y p20/p30/p40/p50/p109–111 son 100 % partner) e **indistinguible** de "las
  ofertas propias se volvieron irreconocibles y solo quedaron visibles los partner". No tiene
  arreglo sin falsos positivos que romperían la vía de rehabilitación de las fuentes secas. Está
  documentado en el código **como techo, no como garantía**; hoy lo mitiga que las páginas
  cosechadas (1–5 de techo, ~2 de presupuesto real) no contienen partner.
- **NUEVO — `gastrojob`, hora del cambio de horario**: la hora ambigua de otoño sale con `fold=0`
  (primer instante) — techo de error de 1 hora, una noche al año, sobre una ventana de 7 días.
  **Decisión razonada**: se rechazó devolver `None` (lo que proponía la revisión externa) porque
  cambiaría esa imprecisión por la **pérdida completa de la fecha**. La hora inexistente de
  primavera no puede imprimirla el portal.
- **NUEVO — `gastrojob`, rearme de `_current_page` en `fetch_jobs`**: sin comportamiento observable
  hoy (el flujo normal siempre pasa por `build_listing_url`) y ningún test lo discrimina — es el
  único superviviente de los 20 mutantes de la verificación. Se conserva como defensa en
  profundidad y el código lo declara como tal.
- **NUEVO (2026-08-16) — `RecursionError` en el parseo JSON**: `json.loads` con anidamiento
  extremo sigue escapando del parseo en `financejobs` y en `fetch_with_retry`. La red por-fuente
  lo convierte igualmente en `error` con su detalle, así que la garantía material se cumple;
  cerrarlo es una línea en dos sitios cuando toque.
  → **✅ CERRADO en `0e6b821`**: `RecursionError` capturado junto a `ValueError` en
  `fetch_with_retry` (`utils/http.py`) y en los parsers (`financejobs`, `irishjobs`,
  `swiss_schools_isp`), verificado en el código.
- **NUEVO (2026-08-16) — la cota de longitud de URL vive en un solo scraper**: la cota "la URL
  cabe en la columna `url` (`String(2048)`)" solo la comprueba hoy `financejobs`. Otros
  (`gastrojob`, `tes`, `medjobs`, `schuljobs`, `myscience`, `zebis`, `publicjobs`) construyen URLs
  con datos del portal sin acotar; un desborde aborta el savepoint de esa oferta, con log y contada
  en errores, sin tumbar el lote. Lo suyo sería una aserción central en `JobRepository.upsert_job`,
  donde vive la columna, en vez de replicarla scraper a scraper.
  → **✅ CERRADO en `0e6b821`** exactamente así: aserción central en el repositorio, derivada del
  propio modelo (`_column_max_len(Job.__table__.c.url)`), con `raise` en vez de `assert` (bajo
  `-O` los assert desaparecen). Residual nuevo asumido del cierre: una URL desbordada se rechaza
  en cada run, para siempre — ruido de log permanente, preferible a truncar la identidad o
  envenenar el cursor.
- **NUEVO (2026-08-18) — pico puntual de identidad tras el despliegue**: los arreglos de identidad
  de `0e6b821` recalculan el hash de filas existentes, así que tras el despliegue habrá un pico
  puntual de "nuevas" y de caducadas en `gastrojob` y `financejobs`. Asumido por escrito: es el
  precio de estabilizar la identidad.
- **NUEVO (2026-08-18) — `gastrojob`, empresas anonimizadas quedan "Unknown" (~3–13 %, varía por
  día)**: la microdata del detalle sí conoce la empresa, pero NO se usa — participa en la
  identidad y solo el listado es estable. Decisión razonada en `0e6b821`: la identidad manda sobre
  el enriquecimiento (usarla hacía oscilar el hash y la oferta dejaba de actualizarse).
- **NUEVO (2026-08-18, `7d63d8d`) — `irishjobs`, pre-check de `\` y `%5C` como defensa redundante
  bajo fallo único**: los vectores con backslash en posición de autoridad los para ya el check de
  userinfo, y los de posición de path se emiten reconstruidos sobre el host propio; su test está
  rotulado como control porque no discrimina. La defensa SE CONSERVA documentada porque protege
  frente a un refactor futuro que devolviera la URL cruda en vez de reconstruirla — el diferencial
  urllib/WHATWG (y el `%5C` reactivado por un decode aguas abajo) volvería a ser explotable.
  Misma familia que el rearme de `_current_page` en `gastrojob`.
- **NUEVO (2026-08-19, `747630f`) — `employment_type` arrastra la misma premisa equivocada que
  tenía `logo`**: sus productores lo obtienen de un fetch de detalle que puede fallar por su
  cuenta (`schuljobs.py:297-298` — JSON-LD del detalle; `myscience.py:123-124` — tabla del
  detalle), así que un fallo de ese fetch en una re-vista pisa con `NULL` un valor bueno
  almacenado. Es el único campo que queda con esa premisa tras el barrido campo a campo de
  `747630f`; el arreglo es el mismo que el de `logo` (omitir del INSERT el valor ausente) y se
  dejó fuera de la ronda para no ampliarla sin su propia verificación.
- **NUEVO (2026-08-19, `747630f`) — skip silencioso de configuración en ISP**:
  `swiss_schools_isp.py:56-57` — un colegio con `tenant` o `site` vacíos se salta con
  `continue`, **sin log ni issue**: una fila mal configurada de la watchlist desaparece en
  silencio (la clase de fallo de esta fase, en la capa de configuración). Y un `school_filter`
  vacío pasaría el filtro para TODO el tenant compartido (`"" in cualquier_string` siempre es
  cierto) y emitiría ofertas de otros colegios bajo el nombre del colegio configurado.

**Inventario de deuda (2026-08-18):** los residuales de esta sección — junto con los del resto
del proyecto — están consolidados en **`DEUDA_TECNICA.md`** (mismo directorio), el inventario
único de deuda reconocida y no abordada, con severidad, coste y porqué de cada ítem. Si el backlog
y ese inventario se contradicen, manda el código.

---

## TRACK P — Preferencias del usuario por conducta (post-gate; diseñado 2026-08-23)
> Decidido con el propietario. Base YA existente: `match_results.feedback` + columna
> `feedback_implicit` JSONB (legacy, sin poblar) · `interaction_events`/`seen_jobs` (portfolio) ·
> `profile_vacancy_state`/`profile_vacancy_events` append-only (core, ADR-03) ·
> **`pattern_analysis_service`** (legacy): ya infiere patrones de los rechazos y genera
> `PatternSuggestion` pendientes de aprobación — TRACK P lo extiende, no lo reinventa.

**P.1 [M] Captura con ATRIBUCIÓN del rechazo.** El problema central es el credit assignment:
"rechazo traducción-en-Canadá" sin motivo enseñaría "no le gusta traducción". Al descartar, chips
OPCIONALES de un toque: `tipo · ubicación · salario · idioma · empresa · otro`. Descarte seco =
válido, queda `sin atribuir` (lo cubre la inferencia de P.2). Eventos `viewed` (con duration_ms),
`clicked`, `saved`, `dismissed(reason?)` → `feedback_implicit` (legacy) / `profile_vacancy_events`
(core). **DoD:** cero fricción añadida al descarte simple.

**P.2 [M] Conversión a señal POR FACTOR.** Los motivos mapean 1:1 sobre el matching multi-factor
existente (rol 35% · salario 25% · ubicación 15% · …): un rechazo por `tipo` = negativo FUERTE en
rol (generaliza); por `ubicación`/`salario` = negativo SOLO en su factor y **positivo DÉBIL en rol**
(le interesó lo bastante como para evaluarla — la clave anti-malaprendizaje del propietario).
Sin-atribuir → inferencia estadística (extensión de `pattern_analysis`: rechaza 8 traducciones
todas-Canadá pero guarda 5 remotas ⇒ las dimensiones se separan solas) SIEMPRE con ratificación
del usuario. Obligatorio: corrección de SESGO DE POSICIÓN (lo del top recibe clics por estar en el
top) y decaimiento temporal.

**P.3 [M] Uso — dos destinos.** (a) Término de afinidad personal en el matching (empresas/fuentes/
keywords/rangos). (b) **Semi-curación del oráculo**: las rondas futuras (`nas-ronda-2+`) llegan
PRE-puntuadas por conducta; el usuario RATIFICA un snapshot en un clic y ESE snapshot se congela.
⚠ LÍMITE RATIFICADO (evaluado 2026-08-23, propuesta del propietario de sustituir la curación
manual por juicios auto-ajustados): un oráculo que se ajusta con el uso NO puede alimentar el gate
— (1) circularidad: solo se rechaza lo que el ranker mostró, el sistema se calificaría a sí mismo;
(2) el §8.1 exige sets CONGELADOS — un oráculo móvil reinicia la racha por definición; (3) los
rechazos generan ceros; el nDCG necesita 2s y 3s graduados (saves/applies, raros). La conducta
PROPONE, el humano RATIFICA, el snapshot SE CONGELA. La curación manual de la ronda 1 es el precio
de arranque una sola vez: sin historia conductual previa no hay nada que proponer.

---

## TRACK T — paridad · observabilidad (harvest/scope runs, outbox lag, **gate de schedulers**,
sources health) · seguridad + ciclo de vida (rotación credenciales, cifrado PII, RPO/RTO,
restore, GDPR multi-almacén con crypto-shred) · tests + contract tests.
## TRACK O — ONNX (aditivo) · render-worker · slim. Fuera del corte.

---

## Estimación (RECALCULADA v3.1.2)

| Bloque | Días (1 dev) |
|---|---|
| Pre-fase (5 P0) | 10 – 15 |
| Cierre de contratos (ADRs + tickets/DTOs) | 6 – 10 |
| **A.SVC + A.SEAM (extracción/aislamiento + costuras ambos backends)** | **20 – 30** |
| Resto de Fase A (esquema + vertical mínima + API + backfill) | 45 – 65 |
| Fase B (sombra + set etiquetado + captura de deltas) | 12 – 18 + ciclos |
| Fase C (Portfolio: runbook + manifiesto + last-known-good) | 18 – 28 + observación |
| **Fase D (SwissJob: canary por perfil + manifiesto)** | **20 – 30** + observación |
| E–F (colegios/docs + retirada) | 10 – 15 |
| Track T | 12 – 18 |

- **Suma ≈ 153–229 días/persona ≈ 7,7–11,5 meses ANTES de contingencia** (broker dedicado
  (ADR-08 `redis-core`) y CDC ya DECIDIDOS en GATE-CC 2026-07-22; recalcular los bloques
  restantes con los datos reales de ejecución de Fase A si procede; **fuente única de
  estimación**; el plan referencia esta tabla). Con 20–30% ≈ **~10–15 meses** (1 dev). Equipo
  pequeño **~6–8 meses** (NO menos: B/C/D tienen gates secuenciales + ventanas de observación que
  no se paralelizan).
- **Trayectoria honesta de la estimación:** 3–4 → 6–9 → 7–10 → **10–15 meses**, conforme afloró
  el rigor de integridad de datos y de cutover sin caídas. Es una **migración de plataforma**.
- **Camino crítico:** PF → GATE-PF → CC → A.SVC/A.SEAM → A.0b/A.1/A.1b → A.MIN → A.5 → A.14 →
  A.15 → GATE A **[tramo ✅ recorrido: PF/GATE-PF cerrado · GATE A 2026-07-24 · Fase B COMPLETA
  EN CÓDIGO 2026-07-25 · A.SEAM SwissJob (APPROVE externo) · C.0/C.1 hechos 2026-07-30]** →
  · **construcción de C COMPLETA 2026-08-02** (C-2/C-3/C-4/C-6 cerrados)] → **FRENTE ACTUAL: GATE
  C** [BLOQUEADO por GATE-SOMBRA: racha rota, última medida ROJA del 2026-07-30 — `DEUDA_TECNICA.md`
  §2.2 — más dedup semántico N3 y despliegue al NAS] → D → E → F.
  ⚠ Estado canónico en `PLAN_UNIFICACION_JOBHUNTING.md` §25; esta línea lo resume, no lo define.

## Modelo y esfuerzo (investigado R6 — plan §22)
**Fable 5 ejecuta, Opus 4.8 verifica** (separación de funciones: Fable tiende a memorizar → tests
"verde" engañosos). Ejecutor de migración = **Fable 5** (alto + `task-budgets`/timeouts duros);
verificación/review/gates de cutover = **Opus 4.8** (máx); diseño/ADRs/contratos = Opus 4.8; volumen
mecánico = Sonnet 5; trivial = Haiku 4.5. Medir coste/tarea (no precio/token); cachear; fallback a
Opus en refusals. **Runtime del producto (rerank/CV): NO Fable — sigue Groq/Gemini** (coste del hot-path).

## Trazabilidad (adiciones v3.1.2)
| Hallazgo R5 | Tareas |
|---|---|
| #1 topología ratificada | (plan §3/§9; A.SVC) |
| #2 backfill+sync antes del flip | C.2, D.1 |
| #3 matriz de escritor | C.2, D.1 (plan §15bis) |
| #4 routing por capacidad/perfil + canary | A.SEAM, D.1 |
| #5 flags separados + apagar schedulers + gate | A.SEAM, C.2 (GATE C) |
| #6 manifiesto de datos por tabla | C.4, D.1 |
| #7 aislamiento operativo (colas/Redis/Alembic/rol) | A.SVC |
| #8 reutilización de código | CC.1 |
| #9 fallback read-only (pending_sync diferido) | C.5 |
| #10 estimación recalculada | (esta tabla) |

## TRACK R — recall del dedup cross-portal (abierto 2026-08-24, tras el examen del holdout)

El holdout congelado midió al detector real: precision 0.636 / recall 0.259 (umbrales
0.95/0.90). H1 cross-portal: 0/15. El gate saldrá ROJO en dedup hasta cerrar este track.
REGLA DURA: toda mejora se diseña y ajusta SOLO con el development (81 pares
re-adjudicados, `development_analisis.csv` en holdout_artefactos_2026-08-23/); el holdout
es el examen y NO se toca (además el agente actual ha VISTO sus pares: cualquier decisión
debe justificarse exclusivamente con mediciones del development — conflicto declarado).

- R.1 ✅ HECHO (2026-08-24, `ANALISIS_TRACK_R_2026-08-24.md`): la precisión la hunde
  el ANN SIN regla de ubicación (17/53 distinct a sim>=0.95; con guard: 1/53 ⇒ ~0.96);
  el recall cross-portal NO es resoluble con este development (solo 3 cruces, fáciles,
  y la empresa se escribe distinta en cada portal — la igualdad exacta falla)
- R.2a Guard de ubicación en el generador ANN (tokens/subcadena; vacío no veta) —
  justificado en development, implementable ya
- R.2b Development-2: protocolo corto pre-registrado + ~40 pares cross-fuente por bloqueo
  laxo (solape de tokens de empresa) EXCLUYENDO holdout y development; etiquetado ciego
  del propietario; con ellos se ajusta el generador cross-portal
- R.3 Implementación + tests + revisión externa del diseño (mismo revisor)
- R.4 Re-medición del gate (el holdout la hace solo, ciclo a ciclo)
- Pendiente menor NAS: renombrar swissjob-core.tar.new → .tar y borrar *.sql temporales
  de /share/Public/swissjob/

- R.6 (NUEVO 2026-08-24): capturar `apply_url`/enlace de solicitud en los
  providers/scrapers del LEGACY y proyectarlo al core — la señal de mayor
  precisión posible para dedup cross-portal; hoy no existe en ningún dato
  (`ANALISIS_TRACK_R_FASE3_2026-08-24.md`). Solo beneficia a pares futuros.
