# Deuda técnica — proyecto jobhunting (SwissJob + jobhunt-core)

> **22-09-2026 — punto 4, cosecha nativa.** Las 16 fuentes del corpus core las
> cosechan productores nativos; ver `SwissJob/docs/audits/POINT4_CUTOVER_2026-09-22.md`.
> A18-06 (configuración de proveedores divergente) queda comprobada para el core:
> la release desplegada se verifica por `/v1/ready` y las listas de retirada se
> validan DENTRO de cada contenedor con tripwire antes de cada maniobra.
> Nueva cota aceptada: el `canton` del histórico legacy no se repara (~4.500
> canónicas por un campo que hoy consume una sola búsqueda semanal); lo nuevo sí
> lo lleva.

## Estado prevalente — 2026-09-21: punto 4, pendientes y evidencia

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

Actas: [perfiles](SwissJob/docs/audits/PROFILE_DEPLOYMENT_NAS_2026-09-20.md),
[feedback](SwissJob/docs/audits/FEEDBACK_DEPLOYMENT_NAS_2026-09-20.md),
[búsquedas](SwissJob/docs/audits/SEARCH_CUTOVER_OPERATOR_2026-09-21.md) y
[WorkingNomads](SwissJob/docs/audits/WORKINGNOMADS_CUTOVER_2026-09-21.md).

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
[informe de pendientes](SwissJob/docs/PENDIENTES_PUNTO4_REVISION_EXTERNA_2026-09-21.md).

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
| A18-05 | P2 · escalabilidad | **PARCIALMENTE ATENDIDO 2026-09-22, no cerrado.** El recorrido está corregido y verificado: servir 20 ofertas pasó de 18 peticiones internas y 9,3-12,9 s a **1 petición**, con ids, orden, scores y total idénticos en 8/8 casos sobre datos reales. Pero el **endpoint servido** `/api/v1/match/results` da p50 1,924 s sin traducción y 2,586 s con ella, con p95 de 4,0-5,4 s: **no cumple el p95 ≤ 2 s** que fija la predeclaración. Una primera acta lo declaró cerrado apoyándose en el p50 del método intermedio; una revalidación externa lo corrigió. Una segunda revalidación encontró el cuello REAL, que estaba fuera de ese recorrido: la pantalla principal pide `limit=3000` y **el 83 % del tiempo era serializar** —detectar el idioma de cada oferta servida, 50,1 ms x 1.800 ≈ 90 s—. Memoizado: **79,3 s → 10,3 s**. Reparado además el transporte de `language`, roto en tres capas (DTO del core, `_vacancy_dtos`, `_job_view`), aunque eso sólo recupera el **3,1 %** del coste: medido, el corpus casi no trae el dato, así que la memoización sigue sosteniendo el recorrido y **no cubre la primera carga**. Desplegado el 2026-09-23 en los cinco servicios: la pantalla principal pasa de **79,3 s a p50 2,147 s** (p95 2,554 s) gracias a cachear el recorrido del feed por VERSIÓN declarada por el core — recorrerlo era el **87-89 %** del coste y un `If-None-Match` no lo evitaba, porque el ETag se deriva del payload. **Sigue ABIERTO**: ninguna lectura habitual baja del p95 de 2 s (catálogo 2,420 s; feed 20 3,040 s) y la primera carga de 3.000 cuesta 10,196 s contra un presupuesto de 5 s. Matriz con veredicto por escenario en §9-bis del acta; lo que falta, en §10. [Acta](SwissJob/docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md) · [Revalidación](SwissJob/docs/audits/REVALIDACION_INFORME_DESPLIEGUE_2026-09-22.md). |
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

### A18-05 · El coste de una página depende del feed y del inventario completos

**Ubicación:** `SwissJob/backend/services/matching/core_client.py:390,496,542`;
`backend/services/schools/presentation.py:20,51`.

Se recorre todo el feed, resuelven identidades y overlays, y solo después se
aplica offset/limit. Los apoyos escolares también recorren colecciones completas
para resolver un subconjunto. Es un coste estructural comprobado; **no** se ha
medido hoy un p95 ni se atribuye al catálogo el antiguo tiempo >30 s, corregido
en E.12. La caché ETag no elimina por sí sola todo el recorrido/hidratación.

**Acción mínima:** instrumentar peticiones core, filas, bytes, tiempo SQL y
memoria por consulta con tamaños representativos y caché fría/caliente. Llevar
filtros/accionabilidad y paginación a una frontera que conserve total, orden,
exclusiones, alias y estados; acotar consultas auxiliares al lote cuando el
contrato lo permita. No añadir índices sin EXPLAIN ni cachear entre usuarios.

**Cierre del punto 5:** presupuesto predeclarado y medición NAS del recorrido
completo, incluyendo concurrencia, reinicio y backlog; demostrar mejora sin
variar resultados/aislamiento. Evitar una refactorización global antes de medir.

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

- Evidencias y comandos: [auditoría reproducible](SwissJob/docs/audits/2026-09-18/README.md).
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
  [índice histórico](SwissJob/docs/ARCHIVO_HISTORICO.md); no restaurarlos todos.

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
| A19-13 | Alto · mantenibilidad | 275 funciones con CC > 10 (estándar 10; 6 de grado F); sin capa `repositories/`; ciclos de import; 4.869 LOC de scripts `import_*` como dependencia de runtime; taxonomía de categorías duplicada Python/JS | T16 | **PARCIAL 2026-09-28** — radon sin grado E/F en el código VIVO: en el BFF `_fetch_providers_async` (E 37→D 24) y `_fetch_scrapers_async` (F 47→D 23) reparten el bucle por oferta en `tasks/harvest_persist.py` (`persist_harvested_job`, `persist_batch`, `record_lost_batch`, `identity_drift_notes`: antes duplicado línea a línea), `routers/documents.py`, `job_repository.upsert_job`, `watchlist_state.get_match` y `core_client.results` van a C; en el núcleo `sink._canonicalize` (F 49) y `_resolve_new_incarnations` (E 34), `matching.compute_policy_feed` (E 36), `school_source.reverse_sync` (F 41), `admission.admit_listings`, los tres `fetch_new`/`_content` nativos y `backup_retention.expire` bajan a ≤ D con el orden de sentencias SQL intacto (suites: 330 + 150 + BFF completa). **Cota aceptada**: los siete E/F que quedan son las herramientas de corte de la Fase E (`import_portfolio_manifest/durables/verify`, `import_swissjob_searches/feedback`), de un solo uso, ya ejecutadas y fijadas por sus tests de integración; reescribirlas no protege nada que corra. Siguen abiertos: capa `repositories/`, ciclos de import y la taxonomía duplicada |
| A19-14 | Producto | Feature 3 del producto (móvil/PWA) no existe: ni manifest, ni SW, ni shells; `VITE_*` cero; 7 dependencias Capacitor y 3 hooks muertos | T15 | **CERRADO 2026-09-27 (T15, vía A)** — vía móvil retirada: hooks sin consumidores, 8 dependencias Capacitor, `capacitor.config.ts` y 4 meta de app instalable. `PORTALES_EMPLEO_SUIZA.md` §11 marcada como descartada |
| A19-15 | Datos · UX | `arbeitnow` nativo guarda HTML crudo (111/111); `jobgether` sin descripción en ninguna fuente (27 % del feed); tarjeta de colegios sin descripción ni «View details»; SwissJob `/match` sin títulos traducidos ni resumen | Diagnóstico del panel, bloques A/B/D | **PARCIAL 2026-09-28** — §D hecho: la pantalla principal de SwissJob (`translate=false`, 3.000 ofertas) sirve los títulos traducidos que el calentamiento de fondo deja en Redis (`TranslationService.cached_translations`, un MGET; `warm._traducir_en_fondo` traduce sólo lo que falta, ≤ `TRANSLATION_WARMUP_MAX_PER_PASS`=100 por pasada, con el Redis de la app). El camino de respuesta NO llama al LLM: `tests/test_translation_warmup.py` instala un Groq que LANZA si se le pide algo. B sigue siendo decisión del propietario; A (HTML de `arbeitnow`, tarjeta de colegios) abierto |
| A19-17 | Crítico · operación | **Nada avisa de que los contenedores estén PARADOS.** El 23-09 los 42 del NAS se pararon (reinicio del daemon de Docker) y `restart: unless-stopped` no levantó ninguno; se descubrió al desplegar. Acta: `SwissJob/docs/audits/INCIDENTE_NAS_2026-09-23.md` | T13 (amplía H10: el supervisor debe cubrir «parado», no sólo «unhealthy») | **CERRADO 2026-10-07** — el supervisor corre por el **crontab del QTS** (que sí estaba armado: `/etc/config/crontab` línea 33) y **entrega correo** usando el `sendmail`/ssmtp del QTS, así que la entrega ya no depende de la credencial de Gmail de A19-31. El contenedor `swissjob-supervisor` se RETIRÓ el 2026-10-07 por duplicado: los dos caminos compartían el fichero de estado, cada uno tomaba por cambio lo que escribía el otro y avisaba — **3.729 correos en 24 h**, con el agravante de que la copia del host era anterior a la retirada de `swissjob-redis-r5` y pedía un contenedor que ya no existe (falso positivo permanente). Arreglado: script del repo subido al host, un solo supervisor y **guard de ejecución única** (240 s) que impide la duplicación aunque se rearme el otro camino; verificado con control negativo (`EXTRA_ESPERADOS` con un nombre inexistente sigue dando MAL). Antes decía: supervisor DESPLEGADO como contenedor (`restart: always`, socket en solo lectura, 64 MB) que cada 5 min comprueba los 15 contenedores esperados (`portfolio_worker` entre ellos desde el 2026-09-30) (15 hasta la limpieza del 2026-09-28: se retiraron `swissjob-redis-r5` —sin clientes ni claves, solo en una red vacía del ensayo r5—, 10 contenedores parados del ensayo r5 y de retrocesos del Portfolio, y `estrato_mineria3`, una minería de estratos del dedup de agosto) y escribe estado y log en `/share/Public/swissjob/supervisor/`. Probado en el NAS con un esperado inexistente (alerta) y con recuperación. Lo que falta NO depende del código: el correo está bloqueado por la credencial SMTP (A19-31), y el crontab del QTS exige suid (la entrada queda en `/etc/config/crontab`). Fuente: `scripts/nas/` |
| A19-18 | Alto · operación | Volumen de sistema de QTS (`/`) al **84 %, 64 MB libres**; Container Station vive ahí. Sospecha no confirmada del reinicio del daemon | — | **COTA DE PLATAFORMA 2026-09-28** — el volumen `/` del QTS es un rootfs de 400 MB con el firmware (`/usr` 124 MB, `/lib` 87 MB, `/home` 76 MB); nada nuestro vive ahí (`/tmp` 3,5 MB, ningún fichero >5 MB). Docker está en `/share/CACHEDEV1_DATA` (1,8 TB, 75 %). El 84 % es el estado normal de ese volumen; no hay nada que liberar |
| A19-16 | Despliegue | Frontend del Portfolio con título traducido y resumen (`a0bf889`) y con «Me interesa» (`0a8b8a8`) **sin publicar**: Cloudflare Pages despliega desde GitHub y no hay push | El **backend** ya está desplegado (`board-45d6ec4`, 2026-09-25) y su base tiene el estado `interested`, así que el push del frontend funciona en cuanto se haga — antes habría dado error | **CERRADO 2026-09-28** — push del frontend hecho el 25-09 a GitHub y GitLab; Cloudflare Pages sirve `index-D6v4vXBE.js` con las claves `interested`/`interestedDone`/«Me interesa» (comprobado con `curl` sobre https://vicentepau.is-a.dev) |
| A19-19 | Crítico · seguridad | **La base de PRODUCCIÓN del Portfolio (`portfolio_db`) publica `0.0.0.0:5435`** y se comprobó alcanzable desde otro equipo de la LAN. Es el único contenedor del NAS que publica puerto alguno. Contraseña de 20 caracteres, no trivial: el riesgo es la superficie. Hallazgo H15 de la auditoría del 23-09, descubierto el 24-09 al verificar T5 | Compose del Portfolio en el NAS → `127.0.0.1:5435` | **COTA ACEPTADA 2026-09-27** — el propietario no se conecta hoy con cliente gráfico pero quiere conservar la posibilidad. Se deja abierto A PROPÓSITO: la contraseña es de 20 caracteres y el riesgo asumido es la superficie, no la fuerza bruta. Si algún día se cierra, es una línea en el compose |
| A19-21 | Alto · entorno local | El compose base fija `image: swissjob-core:dev`, etiqueta **mutable** que apuntaba a un build del **04-09** mientras los contenedores del core corrían el del **08-09**: un `docker compose up -d core-api` lo dejaba `not_ready` | Imagen reconstruida del árbol con `RELEASE_SHA` real (`d63f74b`, antes `unknown`) + pin `swissjob-core:d63f74b`; `core-migrate` **core0042 → core0051** (9 migraciones, todas aditivas, ya probadas en producción); recreados los tres servicios. Costura: `scripts/check_core_release.py` (compose vs lo que corre, `ready`, release nombrable, `authoritative`), 4 controles negativos | **CERRADO 2026-09-24** — `ready`, `core0051`, `release d63f74b`, `authoritative: true`, CDC 0 pendientes |
| A19-23 | Bajo · calidad | **`scripts/` (20) y los .py de `docs/` (5) nunca se habían pasado por el linter**: `backend-lint` corre con `working-directory: backend`, así que su `ruff check .` no los alcanzaba. 22 y 31 avisos. Sumados a los 358 de `jobhunt_core`, **411** | **411 → 0**, sin silenciar nada en bloque: 263 sitios eran fixtures de pytest, 39 `pytestmark` (un `skipif` re-exportado) y uno import por efecto lateral — todos anotados con su motivo tras **comprobar ejecutando** que quitarlos rompe los tests; 21 imports muertos y 4 avisos en código de producción, arreglados de verdad. Core formateado (251 ficheros, AST idéntico en los 335). CI: `core-lint` pasa a bloqueante y `backend-lint` gana un paso para `scripts`+`docs`; los 799 .py del repo quedan cubiertos | **CERRADO 2026-09-24** — los 6 pasos de lint salen con código 0 |
| A19-25 | **Crítico · capacidad** | **El NAS no da para el contrato del punto 5**: 2 CPUs, `load average` 9,43-14,91, y el mayor consumidor de CPU es `tinymediamanager` (**71,55 %**), ajeno al proyecto. El mismo recorrido cuesta **0,084 s en copia y 1,45-3,09 s allí**. `pantalla-principal` y `página-20` NO cumplen y no los arregla el software | Límites/reservas de CPU, mover el contenedor ajeno, rediseñar la carga (§10.5) o aprobar otro presupuesto | **PARCIAL 2026-09-28** — además de limitar `tinymediamanager` (80→17 %), los cuatro healthchecks de infraestructura nuestros pasan de 5-10 s a 60 s (postgres, redis, redis-r5, redis-core-r5): de ~40 arranques de proceso por minuto a 4. Quedan los de `portfolio_db`/`portfolio_redis` (contenedores sin compose, creados a mano: 10 s) y los 34 % de `hal_daemon`+`gpuhal_app` de QTS, que no son nuestros. La RAM disponible subió de 243 MB a 3,6 GB tras recrear con límites. Sigue siendo una máquina de 2 núcleos con carga ~10 |
| A19-26 | Medio · despliegue | **CERRADO 2026-09-24** (`scripts/deploy_nas.sh`) — **El BFF de producción se construye con `backend/Dockerfile.prod`, no con `backend/Dockerfile`**: sólo el primero crea el usuario `app` que exige el compose del NAS. Con el otro el contenedor ni arranca. Y recrear en producción necesita `-p swissjob` / `-p swissjob-r5`, o choca con el nombre | Documentado en CLAUDE.md; falta un script de despliegue que no permita equivocarse | **CERRADO 2026-09-28** — `scripts/deploy_nas.sh` ya existía desde el 24-09 con las tres trampas, pero al usarlo el 25-09 se vio que retagueaba `core-capture` sin recrearlo (deriva A19-21 por construcción) y no corría `core-migrate` ni recreaba `core-worker`. Reescrito: retag por servicio, migración con contenedor desechable en la red de la API antes de recrear, y `core-api`+`core-worker` juntos |
| A19-27 | Alto · reproducibilidad | **31 de 32 dependencias eran RANGOS**, así que cada reconstrucción de la imagen era una lotería. En una sola tarde mordió dos veces: ruff saltó de 0.15 a 0.16 (**917 «errores»** sin cambiar una línea) y pgvector/numpy pasaron a devolver `list` donde daban ndarray (**3 pruebas de fencing rojas solas**) | Todas fijadas a la versión instalada y verificada; `pip install --dry-run` resuelve; `ruff` a la MISMA versión que CI | **CERRADO 2026-09-24** |
| A19-24 | Bajo · tests | ~~Flaky~~ **CERRADO 2026-09-24**: espera a que el latido AVANCE, no a que pase un segundo de reloj. `test_heartbeat_advances_on_keepalive_without_traffic` era flaky bajo carga: transmite 1 s con `status_interval=0.1` y exige que el latido avance. Falló una vez en una suite completa de 20 min y pasó **3/3 aislado**; el fichero tenía AST idéntico, así que no era regresión | Dar holgura al intervalo o esperar al latido en vez de a un reloj de pared | **CERRADO 2026-09-24** — `test_heartbeat_advances_on_keepalive_without_traffic` espera a que el latido avance en vez de a un reloj de pared; la suite del núcleo pasó dos veces íntegra el 2026-09-27/28 (1817 passed) sin reproducirlo |
| A19-22 | Medio · limpieza | En el NAS, la base `swissjobhunter` conserva un esquema **`jobhunt` residual: 50 tablas, 389 MB, congelado en `core0029`**. El core de producción NO la usa (usa `swissjobhunter_r5_rehearsal`, en `core0051`) y ese esquema no registra actividad. Es además una **trampa de diagnóstico**: consultarlo hace creer que producción va nueve migraciones atrasada | `DROP SCHEMA` tras confirmar que nada lo lee | **CERRADO 2026-09-27** — `DROP SCHEMA jobhunt CASCADE` en `swissjobhunter`: la base pasa de **844 MB a 455 MB**. Verificado ANTES que el esquema tenía 0 escrituras en toda su vida, que las 23 tablas vivas están en `public` con 40.351 escrituras, y que ninguna clave ajena cruzaba entre ambos. Recibo en `nas:/share/Public/swissjob/recibos/` (inventario de las 50 tablas + estructura `pg_dump --schema-only`, 72 KB); los DATOS no se conservan, por decisión. BFF `healthy` y `/health` 200 después |
| A19-20 | Alto · seguridad | **`redis` de producción sin contraseña** en los cuatro composes de despliegue. No publica puerto, así que sólo es alcanzable dentro de la red de Docker; el local ya lo exige desde T5 | T13 (exige reinicio de producción) | **COTA ACEPTADA 2026-09-27** — se deja sin contraseña a propósito. No publica puerto: sólo lo alcanzan los contenedores vecinos. Pesó también que ponerla reinicia las colas y que hacerlo mal deja a los dos workers en bucle de NOAUTH (pasó el 2026-09-24 en local) |
| A19-28 | **Alto · seguridad** | **Dos credenciales PERPETUAS abandonadas en producción.** El consumer `portfolio` del core tiene 3 credenciales vivas y sin caducidad; sólo `48c26fe326c924f4` la usa `portfolio_backend`. Las otras dos (`a02cbff1481dee59` sólo lectura, `de7924a343980d97` **con `profiles:write`, `applications:write` y `saved_searches:write`**) no las usa ningún contenedor. Descubierto el 2026-09-25 con `scripts/rotate_credential.py listar`, que se escribió para T9 | **Revocadas con autorización el 2026-09-25**: queda viva una sola credencial, `48c26fe326c924f4`, que es exactamente la que declara `portfolio_backend`. Verificado después de revocar que el Portfolio sigue leyendo el core. Revocar pone fecha, no borra la fila: la vuelta atrás es un UPDATE | **CERRADO 2026-09-25** |
| A19-29 | Bajo · documentación | **`CLAUDE.md` documentaba `pytest --timeout=30` y `pytest-timeout` no está instalado NI LO ESTUVO NUNCA** (`git log -S` sobre `requirements.txt` no devuelve nada): el comando canónico de tests fallaba al arrancar, no al probar. Descubierto al intentar usarlo. Misma clase que la regla de oro del proyecto | Corregido el comando en `CLAUDE.md` y añadido el aviso de que la imagen puede traer un `ruff` distinto al que fija `requirements.txt` (la local tenía 0.16.8 frente al 0.15.14 del CI: 48 rojos que el CI no ve) | **CERRADO 2026-09-25** |
| A19-30 | Medio · tests | **Una prueba de migración usaba el modelo ORM de HOY contra una base clavada en una revisión de AYER** (`test_profile_snapshot_migration`): cada columna nueva en `users` la rompía con un `UndefinedColumnError` ajeno a lo que medía. Saltó al añadir `users.token_version` (T9) | INSERT explícito con las columnas de esa revisión; la prueba queda inmune a las columnas futuras y además es honesta sobre qué esquema mide | **CERRADO 2026-09-25** |
| A19-31 | **Crítico · operación** | **El correo de producción está MUERTO**: desde `swissjob-backend`, `smtplib` contra `smtp.gmail.com:587` cierra la conexión tras `STARTTLS` (`SMTPServerDisconnected`) con el usuario configurado (`lotharsan`) y con la dirección completa; desde el contenedor supervisor, `curl` llega al login y Gmail responde «Login denied». La contraseña almacenada tiene **14 caracteres** y las de aplicación de Gmail tienen 16. Consecuencia: los cuatro avisos por email (profesor de primaria cada 6 h, resumen diario, digest de watchlist, fin de cosecha) y el supervisor (A19-17) no entregan nada, y como `alert_tasks` retira los marcadores al fallar, lo reintenta cada vez sin ruido visible | Crear una contraseña de aplicación de Gmail (o cambiar de relay) y ponerla en `swissjob.configured.yml`, `portfolio.configured.yml` y `supervisor.env`; verificar con `python -c` de `smtplib` desde el contenedor. Es acción del propietario (cuenta de Google) | **ABIERTO — requiere al propietario** |

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
[Acta E.15](SwissJob/docs/DESPLIEGUE_E15_2026-09-14.md).

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
[DESPLIEGUE E.14](SwissJob/docs/DESPLIEGUE_E14_2026-09-13.md).
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
prevalente: ESTADO §36 y [acta E.12](SwissJob/docs/DESPLIEGUE_E12_2026-09-13.md).
Las cifras de E.10 que siguen debajo se conservan como historial, no como
estado ejecutable actual. Rendimiento cerrado solo para el canary indicado.

> **Prevalece esta actualización sobre las tablas históricas de abajo.**
> Estado ejecutable: ESTADO §34 y
> [acta de despliegue E.10](SwissJob/docs/DESPLIEGUE_E10_2026-09-09.md).
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

## Mantenimiento

Este documento se actualiza **al cerrar cada fase**, junto con el backlog. Reglas:

1. Un ítem entra aquí cuando se decide **no** abordarlo, con su porqué — no cuando se descubre.
2. Un ítem sale cuando se cierra, y su cierre se referencia por commit.
3. Si el backlog y este documento se contradicen, **manda el código**: verifícalo antes de escribir.
4. Los ítems marcados ★ son los que cambian decisiones de planificación; revísalos antes de cada
   fase nueva.
