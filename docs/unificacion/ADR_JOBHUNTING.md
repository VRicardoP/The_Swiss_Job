# ADRs — `jobhunt-core` (CC.1, cierre contractual) · v4

> Cierra las decisiones abiertas de `PLAN_UNIFICACION_JOBHUNTING.md` §15. Contrato estable para el
> esquema (§6) y la API `/v1` (CC.2 = `CONTRATOS_FASE_A.md`).
> **v4**: 3ª revisión adversarial (10 hallazgos) — **incarnaciones** en los punteros (primary/
> application → incarnación), **FKs compuestas** en `match_evaluations`, **corpus GLOBAL** (no
> tenant-owned) en la API, **entrega por destino** (`integration_outbox_deliveries` + inbox en el
> BFF + `erase_requests/acks`), **merge sin mutar** append-only (resolver `merged_into` + `merge_transfers`),
> **Redis dedicado** propagado, CDC por replication slot, migración Portfolio corregida.
> Estado: **RATIFICADO** por el propietario del proyecto el 2026-07-22 (**GATE-CC CERRADO**).
> Open-items conocidos al ratificar: recalcular estimación (broker/CDC), OpenAPI formal en A-09,
> umbrales SIM_* en Fase B. Fecha: 2026-07-22.

> **Principio de integridad referencial compuesta (aplica a todo el esquema):** cuando un puntero
> debe pertenecer al MISMO propietario que la fila, se usa **FK COMPUESTA** (no FK simple): p.ej.
> `vacancies.(current_offer_revision_id, id) → offer_revisions(id, vacancy_id)`,
> `profile_vacancy_state.(current_eval_id, profile_id, vacancy_id) → match_evaluations(id, profile_id,
> vacancy_id)`, `profile_embeddings.(profile_revision_id, profile_id)`. Evita mostrar contenido/eval
> de OTRA vacante o perfil aun con FKs simples válidas (hallazgo #3).

---

## ADR-01 · Identidad de vacante, listings, incarnaciones, vigencia y re-enlace

**Contexto.** Dos niveles: `vacancies` ↔ `source_listings` (una por fuente) + revisiones. Una fuente
puede **reciclar** un `external_id`/URL para OTRA oferta; `source_listings` es UNIQUE(source,
external_id) → no se puede "crear otra fila". Hay que modelar la **incarnación** del slot en el tiempo.

**Decisión.**
- `vacancies` = **identidad + punteros + vigencia**: `id`, `current_offer_revision_id` (**NULLABLE**),
  `primary_incarnation_id` (**NULLABLE**, → `source_listing_incarnations`, FK compuesta a la misma
  vacante), `merged_into` (NULLABLE), **`archived_at` (idx)**, `created_at`.
  **Activa = `archived_at IS NULL AND merged_into IS NULL`**. Punteros nullable (o FK DEFERRABLE)
  resuelven el ciclo con `offer_revisions.vacancy_id`; **FK compuesta** para current_offer_revision.
- **`source_listings`** = **identidad estable del slot**: UNIQUE(source, external_id) y
  UNIQUE(source, url_normalized). **No** guarda la URL/apply/timestamps de una oferta concreta (van en
  la incarnación) ni cuelga directamente de una vacante. *(Columnas exactas: `CONTRATOS_FASE_A.md` §1.)*
- **`source_listing_incarnations`** = el binding slot→vacante en el tiempo: `id`, `source_listing_id`,
  `vacancy_id`, `seq`, **`url`, `apply_url`, `first_seen_at`, `last_seen_at`**, `ended_at`; **`UNIQUE(source_listing_id) WHERE ended_at IS NULL`**
  (una incarnación activa). Las **revisiones cuelgan de la incarnación** (`source_listing_revisions.
  incarnation_id`), no del listing. Así un `external_id` reciclado **cierra** la incarnación actual
  (`ended_at`) y **abre** una nueva (nuevo `seq`, nueva vacante); el historial raw viejo queda con su
  incarnación/vacante y NO se arrastra.
- **Contenido canónico determinista**: `offer_revision` vigente = contenido del `primary_listing`
  (su incarnación activa); `current_offer_revision_id` cambia solo si cambia el `content_hash` de ESE
  listing. `offer_revision_sources` agrega las demás fuentes sin mover el puntero.
- **Re-enlace por niveles** (al ingerir): 1) **Exacto** (slot existe): si el `content_hash` cambió,
  **re-evaluar** — empresa (tokens) distinta o coseno con la revisión vigente < `SIM_RECYCLE`
  (**0.60**) → **reciclado** → cierra incarnación + abre nueva (vacante nueva); si no, nueva revisión.
  2) **cross-source fuerte** (url_normalized/tokens) → nueva incarnación apuntando a la vacante
  existente + `link_evidence`. 3) **semántico alto** (≥`SIM_AUTOLINK` 0.93 **y** empresa igual) →
  igual (Fase A puede diferir el 3). 4) **medio** (0.85–0.93) → `dedup_candidates`. 5) **bajo** →
  vacante nueva. **Conflicto `external_id`↔URL a listings distintos**: gana `external_id` (más
  estable); la URL se registra como alias en `link_evidence`.
- Umbrales = config versionada (`SIM_AUTOLINK`, `SIM_CANDIDATE`, `SIM_RECYCLE`), calibrados en sombra.

**Motivo.** Las **incarnaciones** resuelven el reciclado sin violar la UNIQUE ni arrastrar historial
(hallazgo #2). `archived_at`+`last_seen_at` recuperan el `is_active` (PF.3). Contenido canónico
determinista elimina "qué título mostrar". Re-enlace nunca por semántica sola.

**Consecuencias.** Las revisiones referencian incarnaciones. `source_listing_incarnations`,
`dedup_candidates`, `link_evidence` son [A]. El barrido de archivado (ADR-07) fija `archived_at`.

---

## ADR-02 · Versionado de contenido, embeddings y traducciones

*(Sin cambios de fondo respecto a v2.)* `offer_revisions` inmutable con `text_hash` (=hash de
title+company+description+tags). **`offer_embeddings(text_hash, model_id, vector(DIM))`** clavado en
`text_hash` (dos revisiones con mismo texto comparten vector; cambiar salario NO re-embebe). pgvector
fija la **dimensión por COLUMNA**: Fase A `vector(384)` + HNSW para el modelo activo; un modelo de
otra dim = **expand/contract** (columna/tabla nueva). `offer_translations` persistente por
`(offer_revision_id, field, target_lang, input_hash)`. Escritura del embedding con **concurrencia
optimista** (guardada por `text_hash`/estado, lección pre-fase).

---

## ADR-03 · Estado de usuario y evaluaciones (append-only + estado estable)

*(v2, con notas/seguimiento por hallazgo #7.)* **`match_evaluations`** append-only con **componentes
como columnas**: `offer_revision_id`, `profile_revision_id`, `model_id`, `scoring_policy_id`,
`eval_key` (`UNIQUE(profile_id, vacancy_id, eval_key)`; y **UNIQUE(id, profile_id, vacancy_id)** para
la FK compuesta del feed), **`score_final NUMERIC`** (idx `(profile_id, score_final DESC, vacancy_id)`),
`scores JSONB`, `explanation`. Sin feedback. **`profile_vacancy_state`** (estable): `feedback`,
`dismissed_at`, `saved_at`, **`notes`**, `current_eval_id` (FK **compuesta**), `updated_at` — **saved =
bookmark aquí**. **`applications`** nacen en `applied`; `UNIQUE(profile_id, vacancy_id)`; snapshot;
**`notes`, `follow_up_date`** (para no perder los del legado, #7); `application_status_events`. Feed =
eval vigente + no-dismissed + **vacante activa**, keyset por `(score_final, vacancy_id)`.

---

## ADR-04 · Merge/split de vacantes y transferencia de estado

`merge_log(winner, loser, evidence, confidence, actor)` reversible. Transferencia:
- **Append-only/inmutable NO se muta** (`match_evaluations`, `generated_documents`, revisiones,
  incarnaciones): conservan su `vacancy_id`; las lecturas **resuelven `merged_into`** (id canónico =
  seguir la cadena). (No re-apuntar evaluaciones append-only, #6.)
- **Estado mutable** (`profile_vacancy_state`, `applications`, con UNIQUE) SÍ se transfiere con
  **resolución de conflicto** (gana el más fuerte / status más avanzado; fusiona `*_status_events`),
  registrando cada fila en **`merge_transfers`(merge_id, entidad, key, before JSONB, after JSONB)**
  para que el **split** la reponga. Loser `merged_into`. Split solo con `merge_log` + `merge_transfers`
  (retención ≥ retención del estado).

---

## ADR-05 · Cursor por scope, idempotencia de cosecha y de eventos

*(v2.)* `harvest_scopes` + `source_scope_state` (cursor por scope). `harvest_runs`/`source_harvest_runs`
→ pipeline idempotente; **commit del cursor al final**. `integration_outbox` se inserta en la MISMA
transacción que la escritura, con **`event_id` determinista** (`uuid5(ns, type||':'||clave-natural)`;
`match.evaluated`→`eval_key`; `application.status_changed`→`application_id||status||version` con
**version monotónica** por agregado) y **`ON CONFLICT(event_id) DO NOTHING`**.

---

## ADR-06 · Entrega at-least-once, destino y "un solo escritor"

**Contexto.** `event_id UNIQUE` da **deduplicación**, no "exactamente-una-vez" de cara al efecto
externo: un efecto puede confirmarse y perderse el ack; el reintento se repite. Un inbox global no
representa la entrega a **ambos** BFF; el outbox no tenía **destino**.

**Decisión.**
- Contrato de transporte **at-least-once + consumo idempotente**: el productor garantiza ≥1 entrega;
  el consumidor **desduplica por `(consumer_id, event_id)`** y procesa **transaccional e idempotente**.
- **Estado de entrega POR destino**: `integration_outbox_deliveries(event_id, destination, state,
  attempts, lease, ack_at)` — el outbox NO lleva un lease/dead-letter único (#5). El **inbox vive en la
  BD de CADA consumidor (BFF)**, transaccional con su efecto (NO en el core), y desduplica por
  `(consumer_id, event_id)` conservando agregado/payload. El **erase distribuido** usa
  `erase_requests`/`erase_acks` + evento `profile.erased`: el core espera un ack por consumidor (#6).
- **`version` monotónica por agregado** para ordenar/descartar eventos viejos.
- **Emails**: `notification_outbox` marca `sent` tras confirmar **y** usa la **idempotency-key del
  proveedor** (evita doble email si se pierde el ack del proveedor).
- **`idempotency_records`** para HTTP: idempotency-key de cliente en escrituras (Fase C+), TTL.
- **Un solo escritor por estado** (§15bis). **Payloads = solo IDs** (+`subject_profile_id`); el
  consumidor resuelve por `/v1`.

**Motivo.** At-least-once + idempotencia por `(consumer_id, event_id)` es la garantía real sin 2PC.
Destino explícito e inbox por consumidor representan la entrega a ambos BFF. Idempotency-key del
proveedor cierra el doble-email.

**Consecuencias.** El outbox despacha por destino; el inbox es por consumidor. Monitorizar lag +
dead-letter.

---

## ADR-07 · Retención, GDPR y backups

**Decisión (v3, con sujeto y protocolo de borrado):**
- **Corpus**: archivar (`vacancies.archived_at`) a `CORPUS_STALE_DAYS`=**120 d** sin visto y sin
  adjunto; con adjunto se conserva (PF.3). `match_evaluations` no vigentes se podan a **90 d**.
- **Sujeto indexado**: `integration_outbox`/`integration_inbox`/`notification_outbox` llevan
  **`subject_profile_id` (indexado)** + `owner`/`destination`. Sin él, el erase "del perfil" no era
  ejecutable (#6).
- **GDPR erase por perfil**: borra local (transaccional) `profile_*`, `match_evaluations`,
  `profile_vacancy_state/events`, `applications`, `generated_documents`, `notification_outbox`, y las
  filas **por `subject_profile_id`** de outbox/inbox del propio core; **NO** borra `offer_*`
  (compartido). Para los inbox **propiedad de otro servicio** (BFF), el protocolo
  **`profile.erased`** exige confirmación posterior al commit del borrado en cada réplica.
  **Concreción en implementación, 2026-09-13:** petición durable en el BFF + recibo/tombstone
  transaccional en el core + consulta autenticada de recibos y ack de cada BFF. No depende de
  conservar un evento en el outbox del perfil que acaba de borrarse. La ausencia de ack sigue
  pendiente; este transporte se está verificando y no se declara desplegado todavía.
- **Alcance Portfolio — decisión expresa 2026-09-13:** portfolio personal con cuenta
  propietaria permanente. No se requiere implementar su eliminación ni retirar el CV
  público/chatbot; no bloquea el cierre. Se mantienen las protecciones existentes de
  borrado del core/SwissJob y las reglas de backups y restauración de datos personales.
- **Backups — decisión sustitutiva 2026-09-13, delegada por el propietario**:
  retención limitada + restauración saneada; NO KMS por perfil ni afirmación de crypto-shred.
  Backups: máximo **7 días** desde creación. Copias temporales: **48 horas**; reservas de rollback
  individualizadas con motivo y vencimiento explícito (máximo 7 días). No reiniciar el plazo
  copiando un archivo. Reemplazo periódico verificado; si falla, conservar el último recuperable
  y declarar retención incumplida, nunca borrado completado. Expiración por inventario de rutas y
  huellas, con ensayo previo, sin borrado recursivo indiscriminado.
  Mantener inventario mínimo y vigente de supresiones fuera de los dumps antiguos. Todo restore
  queda aislado hasta re-aplicar esas supresiones y verificar core, BFF y origen CDC; sin inventario
  vigente no se abre tráfico. El borrado sigue **pendiente de backups** hasta comprobar su retirada.
  No cambia el borrado de embeddings ni permite eliminar corpus compartido. RPO ≤ 24 h y RTO ≤ 4 h
  siguen siendo objetivos que requieren medición. Implantación operativa: verificación en curso;
  esta decisión NO acredita por sí sola retirada de copias ni automatización desplegada.

---

## ADR-08 · Aislamiento operativo: broker/locks dedicados

**Contexto.** El plan pedía "sin nuevos Redis" con solo prefijos; pero **los prefijos NO aíslan
memoria/eviction**, y el Redis de caché usa `allkeys-lru` → podría expulsar mensajes Celery/leader-locks.
`noeviction` en la misma instancia evita expulsiones pero deja que la caché llene la memoria y bloquee
publicaciones (#1).

**Decisión.** El **broker de Celery + los leader-locks del core** van a un **Redis DEDICADO** (segundo
contenedor/proceso, `maxmemory-policy noeviction`, su propia maxmemory), **separado** del Redis de
caché. Alternativa equivalente: **RabbitMQ** como broker (locks en Redis dedicado). Se **relaja
explícitamente** la afirmación "sin nuevos Redis": hay **un Redis dedicado** para broker/locks (no un
3.er Postgres). Actualiza Compose (los 4), A-01 y la estimación.

**Consecuencias.** Un contenedor Redis más (pequeño). El aislamiento pasa de "prefijo" (insuficiente)
a instancia dedicada. Estimación a recalcular.

---

## ADR-09 · API `/v1`: multi-tenant, scopes y errores

**Contexto.** Se definió consumer-key, pero **no** la regla de propiedad ni el vocabulario de scopes:
un consumidor válido podría pedir `/profiles/{pid}/matches` de OTRO consumidor (#4).

**Decisión.**
- **Ownership por tenant (recursos TENANT-OWNED)**: las queries a perfiles/matches/estado/candidaturas
  se filtran por el `consumer_id` de la credencial (`profile.consumer_id == credential.consumer_id`;
  cross-tenant → **404**, no 403, para no filtrar existencia). El **corpus (`vacancies`/`offer_*`) es
  GLOBAL** (compartido): legible con scope `vacancies:read`, **sin** ownership por consumidor (#4).
- **Scopes** (vocabulario mínimo): `vacancies:read`, `matches:read`, `profiles:read`, `profiles:write`
  (Fase C+), `applications:write` (Fase C+). Matriz **ruta→scope** explícita; sin scope → 403.
- **Errores uniformes** `{code, message, details}`; `401` sin credencial, `403` sin scope, `404`
  cross-tenant/no-existe, `409` conflicto de versión, `429` rate-limit.
- **Contract tests negativos cross-consumer** obligatorios en el DoD de la API (A-09).

**Motivo.** Sin ownership + scopes, la API filtra por identidad de red pero no por propietario → fuga
cross-tenant. 404 en cross-tenant evita el oráculo de existencia.

---

## ADR-10 · Alcance TEMPORAL de la cosecha: semana en curso al arrancar, incremental después

> Decidido por el propietario el **2026-08-06**, a la vista de los datos reales del corpus del NAS.
> Corrige el supuesto implícito de ADR-05/§5.1: hasta ahora el alcance era "todo lo que dé el portal",
> con la retención (180 d) como único límite.

**Contexto (MEDIDO en producción el 2026-08-06, no estimado).** El corpus legacy del NAS tenía 23 813
ofertas en 25 fuentes, y su composición desmiente el supuesto de partida:

- **Rango temporal = 66 días** (2026-06-01 → 2026-08-05), que es toda la vida del despliegue. La
  retención de 180 d **nunca ha recortado nada**: no había ventana, había "todo lo acumulado".
- **10 191 ofertas (43 %) llevaban >30 días sin volver a verse**, pero **23 060 de 23 813 seguían
  `is_active = true`** (solo 753 inactivas). El corpus arrastra una masa de ofertas probablemente
  caducadas que nadie cierra: `is_active` **no** es señal fiable de vigencia.
- Concentración extrema: `arbeitnow` + `ostjob` = 56 % del total; los 5 `swiss_schools_*` juntos, 61.

**Decisión.**
1. **Cosecha inicial (bootstrap de una fuente): SOLO las ofertas publicadas en la SEMANA EN CURSO.**
   No "todo lo activo" ni "todo lo de los últimos 180 días".
2. **A partir de ahí, cosecha periódica incremental** (cursor por scope de ADR-05 + presupuesto y
   early-stop del crawler incremental legacy).
3. **EXCEPCIÓN — colegios suizos (`swiss_schools_*`): SOLO en el bootstrap.** Partiendo de BD en
   blanco se descargan **TODAS las activas**, sin ventana temporal (los demás portales bajan solo la
   semana en curso). Son pocas (61 en todo el corpus), de rotación lenta y de altísimo valor para el
   perfil objetivo (docencia): la ventana semanal perdería el grueso del histórico vigente.
   **A partir de ahí NO son especiales**: cosecha periódica e incremental, solo novedades, *igual
   que el resto de portales*. (Precisado por el propietario el 2026-08-06; la redacción anterior
   decía "siempre", que era incorrecta.)

**⚠ Bloqueante de implementación: HOY NO EXISTE FECHA DE PUBLICACIÓN.** Verificado el 2026-08-06:
`public.jobs` solo tiene `first_seen_at` / `last_seen_at` / `url_last_check`, y el esquema del core
tampoco guarda `posted_at`/`published_at`. **`first_seen_at` NO sirve como sustituto**: es cuándo la vio
NUESTRO crawler, no cuándo se publicó — en un bootstrap todo el corpus tendría `first_seen_at` de hoy
y la ventana semanal no filtraría nada. Esta decisión exige, antes de poder aplicarse:

- Un campo **`published_at` en el modelo normalizado** (legacy y core), poblado por cada provider/scraper
  desde el dato del portal.
- Una **política explícita por fuente para las que NO lo exponen**: o se excluyen del filtro semanal
  (se cosechan enteras como los colegios), o se descartan, o se aproxima. Decidir fuente por fuente
  y **dejarlo registrado**, porque un default silencioso reintroduce el problema que esta ADR corrige.
- Revisar de paso el **cierre por vigencia**: con 43 % del corpus rancio y `is_active` inservible, hace
  falta una regla de caducidad (p.ej. cerrar la incarnación si `last_seen_at` supera N barridos).

**Motivo.** Descargar "todo lo activo" produce un corpus dominado por ofertas viejas que nadie puede
solicitar ya: infla el coste de embeddings y rerank, ensucia el matching y hace que las métricas del
GATE-SOMBRA se calculen sobre ~43 % de ruido. La ventana semanal + incremental da un corpus vivo; la
excepción de los colegios preserva la cobertura donde el volumen es bajo y el valor alto.

**Consecuencias.** El corpus arrancará mucho más pequeño (orden de cientos, no de decenas de miles).
Afecta a `harvest_scopes` (un scope necesita expresar su ventana), al `CrawlerBudgetService` y a los
seeds de compliance. **No** se aplica retroactivamente al corpus ya proyectado en la sombra.

---

### ADR-10bis · Precisiones de IMPLEMENTACIÓN (2026-08-14, por delegación del propietario)

> Al implementar V.1/V.2/V.3 aparecieron dos puntos en los que la letra de ADR-10 no era aplicable
> tal cual. Se resolvieron **por delegación** (el propietario delegó las decisiones del proyecto el
> 2026-07-24) y quedan aquí registrados con su motivo. El bloqueante de arriba **ya no existe**:
> `published_at` se puebla desde 24 fuentes con el dato real del portal (commit `926d814`).

**1. La ventana es MÓVIL de 7 días, no la semana natural.** Con la semana natural, un bootstrap
lanzado un lunes a las 08:00 capturaría **ocho horas** de publicaciones, y el resultado dependería
del día de la semana en que se arrancara la fuente. La ventana móvil es lo que "semana en curso"
significa en la práctica y elimina ese acantilado. Configurable: `HARVEST_WINDOW_DAYS` (default 7,
validado `>= 1`).

**2. La ventana se aplica SIEMPRE, y solo a las ALTAS — no solo en el bootstrap.** La redacción
original ("solo en el bootstrap; después manda la cosecha incremental") se apoya en una premisa que
**hoy no se cumple**: el pipeline de providers **no tiene cursor ni early-stop**, y son 20 de las 23
fuentes con política de ventana. Con la lectura literal, el run siguiente al bootstrap vuelve a
descargar y guardar exactamente lo que la ventana acababa de descartar: la función parecía
implementada y no lo estaba.

- **Solo puede rechazar ALTAS.** Una oferta que ya está en el corpus sigue pasando por el upsert con
  normalidad. Si se la saltara, dejaría de refrescar `last_seen_at` y `cleanup_stale_jobs` acabaría
  borrándola por "desaparecida del feed" — se habría introducido una pérdida de datos al arreglar
  otra cosa.
- **La excepción de los colegios no cambia**: los 8 `swiss_schools_*` tienen política `FULL` y la
  ventana no los toca en ningún run.
- **El compromiso, dicho en voz alta:** una oferta que un portal nos muestre por primera vez más de
  N días después de publicarse no se ingesta. Por eso N es configurable, hay interruptor
  (`HARVEST_WINDOW_ENABLED`) y hay contadores (`window_skipped`, `window_no_date`). **Revisar N con
  los datos de la primera semana**, en particular en las fuentes del perfil objetivo (`zebis`,
  `schuljobs`, `tes`). ⚠ En los providers ese contador es **flujo por run, no ofertas únicas**
  (la misma oferta se re-descarta cada día hasta que el portal la retira), así que sobreestima la
  pérdida real; en los scrapers se acerca a ofertas únicas porque el cursor aprende los descartes.

**3. Política EXPLÍCITA por fuente, sin default silencioso** (lo que ADR-10 exigía en su tercer
punto): registro `SOURCE_POLICY` en `services/harvest_window.py` con las 54 fuentes, cada una con su
motivo escrito, y un test que falla si una fuente registrada en el código no tiene política. Solo
hay dos políticas: `WINDOW` (23) y `FULL` (31 — colegios, restringidas sin conector, y toda fuente
que no expone fecha, porque descartarlo todo la dejaría muda en silencio).

**4. ⚠ Operativa que NO es obvia:** el cursor de los scrapers aprende las identidades descartadas
por antigüedad (para que el early-stop no las re-descargue cada día). Por eso, **apagar el
interruptor, reclasificar una fuente a `FULL` o subir N exigen vaciar `recent_identities` de las
fuentes afectadas**, o esas ofertas seguirán sin re-descargarse. El SQL exacto está en el docstring
del módulo y en el propio mensaje de alerta.

---

## ADR-11 · Estrategia de ejecución: validación continua en producción, NO integración big-bang

> Decidido por el propietario el **2026-08-06**, tras evaluar explícitamente la alternativa
> contraria. Formaliza y matiza el principio 7 del §0 del plan.

**Contexto.** Se planteó terminar el proyecto entero (Fases C-F) **sin** probar en el NAS, sin
conectar ReactPortfolio y sin frontend de SwissJob, y hacer al final las pruebas + 7-15 días de
observación + la sustitución de ambas apps a la vez. Argumento a favor: el sistema tiene **2
usuarios**, así que el radio de impacto de un fallo es mínimo y el rigor del plan resulta caro.

**Decisión.** Se DESCARTA el big-bang. Se mantiene el Strangler Fig con estos matices:

1. **La sombra se queda corriendo** durante el desarrollo del resto de fases. Ya está desplegada y
   es automática: no consume tiempo de desarrollo y acumula ciclos en paralelo.
2. **Conectar un consumidor real al `/v1` cuanto antes** (Portfolio, SOLO LECTURA, sin flip ni
   migración de datos). C.0/C.1 ya están hechos.
3. **Orden invariable: fiabilidad del dato → medición → corte.** No se mide calidad de matching
   sobre un corpus del que no se conoce la cobertura ni la vigencia.
4. **Congelación de código durante el gate**: desde el freeze de los sets hasta que pase el gate,
   no se redespliega el core.
5. Aplazar o agrupar los **flips** sigue siendo una decisión de calendario legítima; aplazar la
   **validación**, no.

**Motivo (empírico, no teórico).** El 2026-08-06, en unas horas de ejecutar la sombra en el NAS,
aparecieron **cinco defectos que no existían en dev** (ver §3.4 de `ESTADO_Y_HOJA_DE_RUTA.md`):
volumen `root:root` que paraba los embeddings en silencio, healthcheck imposible en ese hardware,
consulta GDPR que devolvía vacío en vez de fallar, compose no desplegable y healthcheck de frontend
roto desde hacía meses. **Ninguno es un bug de lógica: los cinco son defectos de ENTORNO**, y por
definición solo aparecen ejecutando en ese entorno.

Construir meses sin desplegar no los evita: los **acumula** y los entrega todos juntos, al final,
sobre datos reales y con el reloj corriendo — y mezclados es carísimo saber cuál causa cuál.
Además **los 7 días del gate no se comprimen, se mueven**: ejecutados ahora corren en paralelo al
desarrollo; aplazados, pasan al camino crítico. Y un flip simultáneo de las dos apps concentra en
un solo evento dos migraciones de datos de usuario y dos apagados de scheduler, con un rollback
cuyo orden ya demostró tener trampas (§10.2: dropear el slot ANTES de bajar `wal_level`, o el
Postgres no arranca).

**Sobre el contraargumento de los 2 usuarios:** es legítimo y justifica **aligerar los cortes**
(agrupar flips, saltarse canarios de días, aceptar ventanas de caída). No justifica aplazar la
validación, porque el coste de no validar no es el impacto del fallo — es el **tiempo de
depuración**, y ese se paga igual con 2 usuarios que con 2 000.

---

## Resumen (§15) + Cambios v2→v3

Todas las decisiones §15 **ratificadas** el 2026-07-22 (GATE-CC CERRADO). **Cambios v2→v3** (2ª revisión, 12
hallazgos): (1) **Redis dedicado** para broker/locks (ADR-08; #1). (2) **Incarnaciones** de listing
para el reciclado sin violar UNIQUE (ADR-01; #2). (3) **FKs compuestas** por integridad de propietario
(principio + ADR-01/03; #3). (4) **ADR-09** API multi-tenant + scopes + errores (#4). (5) Entrega
**at-least-once + destino + inbox por consumidor + idempotency-key de email** (ADR-06; #5). (6)
**`subject_profile_id`** + protocolo **`profile.erased`** para el erase distribuido (ADR-07; #6).
(7) Migración **campo-a-campo** (saved→state, notas/seguimiento como columnas) en §15bis + `notes`/
`follow_up_date` en el esquema (ADR-03; #7). (8) CDC por replication slot/secuencia en §15bis (#8).

## ADR-04 — Fusión de duplicados con transferencia de estado (DISEÑO, 2026-08-25)

**Estado**: diseño aprobable; implementación BLOQUEADA por (a) estrato positivo
independiente del oráculo dedup (condición del revisor, ronda R2 del Track R) y
(b) gate en verde. **Contexto**: el detector alcanzó precision 1.000 sobre el
holdout — la fusión deja de ser tabú, pero el legado del legacy (664 ofertas
desactivadas en falso por auto-merge) obliga a un diseño reversible y gradual.

### Decisiones

1. **Nunca merge automático de entrada**: tres niveles de confirmación —
   `pending` (detección) → `confirmed` (regla determinista de alta precisión O
   ratificación humana vía API) → `merged` (acto explícito). El paso
   confirmed→merged lo ejecuta una tarea one-shot auditada (patrón del barrido:
   preview/apply + hash + procedencia `merged_by`).
2. **Superviviente**: la vacante con la encarnación activa más antigua
   (first_seen_at menor) — la identidad histórica gana; empate: mayor longitud
   de descripción. El perdedor recibe `merged_into = superviviente` (el corpus
   ya lo excluye: `WHERE merged_into IS NULL` en matching/dedup/feed).
3. **Transferencia de estado** (la lección del legacy): applications,
   evaluaciones y feedback del perdedor se RE-APUNTAN al superviviente en la
   misma transacción; conflicto (el usuario ya tenía estado en ambas) → se
   conserva el del superviviente y se archiva el del perdedor en
   `merge_log.estado_perdedor` (jsonb).
4. **Reversibilidad total**: tabla `dedup_merges` (perdedor, superviviente,
   candidato_origen, merged_by, merged_at, estado_perdedor) — `unmerge()`
   restaura `merged_into = NULL` y re-apunta el estado archivado. Sin esta
   tabla no hay merge.
5. **Las encarnaciones NO se tocan**: siguen colgando de su vacante original;
   el puntero `merged_into` es la única mutación sobre vacancies. El sink que
   re-vea al perdedor refresca su encarnación con normalidad (la vigencia del
   par la gobierna el merge, no la cosecha).
6. **Gate del propio merge**: la tarea rechaza fusionar si el par no tiene
   candidato `confirmed`, si alguna vacante ya está merged, o si el par viola
   la regla de ubicación vigente (coherencia con el detector).

### Camino de activación
estrato positivo independiente → revisión externa del diseño+implementación →
merges manuales vía API (Fase C) → regla determinista de auto-confirmación SOLO
para la vía exacto-intra (texto y ubicación idénticos) → evaluación → ampliar.
