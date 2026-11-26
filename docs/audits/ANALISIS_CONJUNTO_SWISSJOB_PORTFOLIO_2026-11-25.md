# Análisis conjunto de SwissJob y ReactPortfolio (2026-11-25)

> Foto del 25-11, antes de corregir nada. Qué se corrigió y qué queda: `docs/PLAN_ACCION_EFICIENCIA_2026-11-26.md` §0.

Qué no funciona, dónde se va el consumo y qué código sobra en los tres componentes que trabajan juntos: el núcleo (`jobhunt_core`), el BFF de SwissJob y Portfolio.

## Cómo se hizo y qué alcance tiene

- **Producción, medida en solo lectura el 25-11 entre las 23:10 y las 23:40:** `pg_stat_statements` (4,3 días de historial, desde el 21-11), registros de 24 h de cada contenedor, Redis, tamaños de tablas e índices y variables de entorno (solo nombres e igualdad, nunca valores).
- **Código, leído por cuatro análisis independientes:** núcleo, BFF con su frontend, Portfolio y las costuras entre los tres. Ninguno ejecutó tests.
- Cada punto dice si está **medido** en producción, **confirmado** leyendo el código o **sin confirmar**.
- No se ha cambiado nada. No hay ningún plan de ejecución real salvo el de N3.

Los identificadores (F = fallo, R = recursos, S = simplificación) son de este documento.

## Lo esencial

1. **PostgreSQL gasta de media el 36 % de un núcleo en consultas, y seis consultas del núcleo se llevan el 83 %.** Cuatro de ellas son trabajo que nadie aprovecha o que se hace de más (R1 a R5).
2. **La consulta híbrida de matching se ejecuta siempre dos veces, y la segunda con los índices desactivados.** Es el 27 % del tiempo de base de datos: 214 s de media por ejecución y 483 GB leídos de disco en cuatro días (R1).
3. **Portfolio agota cada día su cuota de Groq y sigue llamando una vez por minuto las 24 horas.** Hay 8.010 resúmenes en cola desde el 6 de noviembre (F1).
4. **El 72 % de las peticiones a la API del núcleo son el sondeo de borrados GDPR** para un único recibo histórico (R6).
5. **Cuatro funciones están rotas en silencio:** el informe diario de Portfolio, la traducción de títulos en los dos proyectos, las alertas de búsquedas guardadas de Portfolio y el botón principal de `/match` (F2 a F6).

## Fallos

| | Qué | Evidencia | Corrección mínima | Esf. |
|---|---|---|---|---|
| F1 | **Cola de resúmenes de Portfolio atascada.** Reintenta la misma fila cada 60 s, sin tope ni pausa, contra un límite de 200.000 tokens/día de `openai/gpt-oss-20b`. | Medido: 1.132 respuestas 429 y 193 timeouts en 24 h, ~57 errores por hora sin interrupción; 8.010 resúmenes pendientes, el más antiguo del 6-11. `backend/services/enrichment.py:294-304`, `groq_service.py:71` (ReactPortfolio). | Pausa hasta el reinicio de la cuota cuando el 429 es de tokens diarios; saltar la fila fallida; `Groq(timeout=25, max_retries=0)`. Decidir además si se quieren resúmenes de 8.000 ofertas con esa cuota. | S |
| F2 | **El informe diario de Portfolio lanza `TypeError` en cada ejecución** desde el 21-10. | Medido: `Daily match report error` con traza a las 05:59 UTC del 25-11. `backend/services/daily_match_report.py:279-284` llama a `analyze` con una firma que ya no existe. Los tests parchean la función entera. | Quitar los tres argumentos y un test que no la parchee. | S |
| F3 | **Un fallo de Groq marca títulos como «no necesita traducción» para siempre** (Portfolio). | Medido: 15.839 títulos vacíos frente a 2.387 traducidos; al menos 933 de los vacíos llevan diacríticos alemanes o franceses. `enrichment.py:286-291`, `translation_service.py:171-178`. | Guardar solo los títulos que el traductor decidió; volver a encolar los envenenados. | S |
| F4 | **La traducción de fondo del BFF no avanza.** Los 100 primeros títulos sin clave en Redis son siempre los mismos, porque los que están en inglés o español nunca se cachean. | Medido: `traducidos: 200` en 282 de 284 pasadas del calentador en 24 h. `backend/services/matching/warm.py:98-100`. | Excluir los idiomas que no se traducen antes del corte de 100. | S |
| F5 | **El buzón de Portfolio es solo de escritura.** Recibe los eventos del núcleo, los guarda enteros y nada los lee ni los purga; las alertas de búsquedas guardadas de Portfolio no avisan a nadie. | Medido: 13.659 filas y 9 MB en `integration_inbox`. Confirmado: solo lo referencian su router y su modelo. | No emitir a `portfolio` hasta que haya suscriptor, o purga por antigüedad. | S/M |
| F6 | **El botón «Find new matches» de `/match` siempre devuelve 409.** | Confirmado: `backend/routers/match.py:72-93`, `frontend/src/pages/MatchPage.jsx:237,334`. | Borrar botón, hook, endpoint y esquemas. | S |
| F7 | **El atajo por categoría del BFF caduca a los 20 min** aunque la versión se haya verificado: regresión del arreglo de A-03. | Confirmado: `core_client.py:913-916` devuelve de caché sin sellar `_feed_verified_at`. | Sellar en esa línea y un test. | S |
| F8 | **Los buzones rechazan cualquier campo nuevo** (`extra="forbid"`) y un 4xx permanente se reintenta ocho veces antes del dead-letter. Lo mismo `_FacetsDTO`, cuyo fallo manda al frontend al camino de 3.000 filas. | Confirmado: `backend/routers/integration_inbox.py:61,94`, `core_client.py:196,205`, `jobhunt_core/tasks/delivery.py:119-131`. | `extra="ignore"` en los receptores; dead-letter inmediato para 4xx salvo 408 y 429. | S |
| F9 | **`SoftTimeLimitExceeded` se trata como fallo reintentable** en cosecha y matching: un scope que agota 30 min pierde lo descargado y se reintenta. | Confirmado: `tasks/harvest.py:92-99`, `harvest/runner.py:140-151`. Sin medir cuántas veces ocurre. | Relanzar esa excepción antes del `except Exception`. | S |
| F10 | **Las limpiezas diarias de Portfolio esperan 24 h tras cada arranque**, incluida la retención de datos personales: con despliegues frecuentes no corren nunca. | Confirmado: `backend/app/scheduling.py:88,101,114,127`. | Retardo inicial de minutos, escalonado. | S |
| F11 | **Una caída del núcleo borra la copia buena del panel de fuentes** de Portfolio durante una hora. | Confirmado: `job_sources_feed.py:136-160`. | No escribir si no respondió ninguna fuente. | S |
| F12 | **`swiss_schools_vis` lleva más de 7 días sin cosecha completa.** | Medido: 14 avisos de `harvest_health` en 24 h. `jsearch` también avisa, pero está previsto hasta su primera cosecha del 30-11. | Revisar el productor. | ? |

## Consumo de recursos

### Dónde se va el tiempo de PostgreSQL

135.695 s de ejecución en 375.042 s de ventana. Sin `statement_timeout`, con `work_mem` de 4 MB.

| % | Consulta | Llamadas | Media | Origen |
|---|---|---|---|---|
| 27 | Candidatos híbridos (ANN + léxico) | 170 | 214 s | `matching.py:105-155` |
| 17 | Facetas del feed | 9.905 | 2,3 s | `GET …/matches/facets` |
| 11 | Agregado por fuente | 783 | 19,8 s | `source_aggregate.py:355` |
| 11 | Vecinos de una vacante | 10.617 | 1,4 s | barrido de dedup |
| 10 | Versión del feed | 29.963 | 0,44 s | `GET …/matches/version` |
| 7 | Ofertas sin embedding | 1.139 | 8,5 s | `embeddings.py:234-253` |

### Acciones, por ahorro

| | Qué | Evidencia | Corrección mínima | Esf. |
|---|---|---|---|---|
| R1 | **El respaldo exacto del matching híbrido se dispara siempre.** El recuento de filas semánticas se hace después de fusionar y cortar, así que nunca llega al objetivo y la consulta se repite con `enable_indexscan = off`. | Medido: 85 `SET LOCAL enable_indexscan = off` para 170 ejecuciones, exactamente la mitad; mínimo 109 s, media 214 s, máximo 655 s; 92 GB de temporales y 483 GB leídos de disco. `matching.py:984-995`, `:1612-1618`. `jobhunt.matching.recover` suma 8.156 s al día. | Contar sobre el CTE `ann` y registrar cuándo se cae al respaldo. Incluso la ejecución «rápida» tarda 109 s: después hay que sacar su plan. | S-M |
| R2 | **Dos políticas se evalúan a la par** (unas 230 evaluaciones al día cada una). Si una es sombra, la mitad del trabajo de R1 no lo sirve nadie y además emite eventos. | Medido el reparto; qué política es la canónica, sin confirmar. `tasks/matching.py:244-295`. | Desactivar la sombra con `policy_ctl` si ya no se compara. Decisión del propietario. | S |
| R3 | **El barrido de dedup no tiene lector.** `dedup_candidates` solo se escribe y `merged_into` no tiene escritor. | Medido: 11 % del tiempo de base de datos y 941 s de tarea al día. Confirmado por grep que nada lee la tabla. | Sacarlo del beat. Antes, confirmar que la fusión de duplicados no está prevista a corto plazo. | S |
| R4 | **La búsqueda de ofertas sin embedding recorre el corpus cada 5 min** aunque no haya nada nuevo. | Medido: 8,5 s por pasada, 262 al día; el plan es un anti-join por mezcla sobre 313.000 revisiones. | Saltarla si `corpus_generation` no cambió desde el último drenaje vacío. | S |
| R5 | **Los calentadores trabajan para usuarios que no entran.** El BFF calienta feed y facetas cada 10 min desde sus dos procesos de gunicorn, sin exclusión entre ellos. | Medido: 0 peticiones de usuario al BFF en 24 h frente a 1.732 lecturas de versión, 568 de facetas y 554 de feed al núcleo. Las facetas son el 17 % del tiempo de base de datos. | Calentar solo a quien haya entrado en las últimas N horas, y desde un solo proceso. | S |
| R6 | **El reconciliador de borrados GDPR sondea cada 30 s desde tres procesos** y reescribe el acuse en cada pasada. | Medido: 8.564 lecturas y 8.564 acuses en 24 h, 17.128 de las 23.879 peticiones del núcleo, para 1 recibo y 2 acuses. `backend/services/profile_erasure.py:235-249`. | Intervalo de 10-15 min y un solo proceso. | S |
| R7 | **`swissjob-erasure-cdc` apunta a la base del núcleo** con la réplica `swissjob-cdc`, que era la de la sombra retirada. Lo que protege son las tablas `public` heredadas de esa base. | Medido: su `DATABASE_URL` es `swissjobhunter_r5_rehearsal`; el esquema `public` de esa base ocupa 501 MB, casi todo `jobs` (497 MB, 0 filas vivas según estadísticas). | Decidir si se retiran juntos el esquema heredado y el contenedor (256 MB de límite). Decisión del propietario: es un borrado. | M |
| R8 | **Cada oferta revista sin cambios reescribe su revisión.** | Medido: 186.101 actualizaciones de `source_listing_revisions`, ninguna HOT; `vacancies`, 121.558 y ninguna HOT. `harvest/sink.py:1504-1514`. | No reenviar `raw` y tocar `fetched_at` solo si esa revisión no es ya la última. | M |
| R9 | **El 87 % de las encarnaciones «activas» no se ve desde hace más de 7 días.** Nada cierra una oferta que desaparece del portal hasta los 120 días. | Medido: 20.216 vistas en 2 días, 1.358 en 2-7, 48.431 en 7-30 y 95.363 hace más de 30. Parte es legítima en fuentes incrementales; cuánta, sin medir. | En fuentes de barrido completo, cerrar lo no visto en N barridos. Reduce el corpus que recorren R1, R3 y R4. | M |
| R10 | **Portfolio drena el feed entero cada hora con páginas de 100** y sin preguntar la versión. | Confirmado: `_MAX_PAGE_LIMIT = 100` cuando el núcleo admite 500; no usa `/matches/version`. | Página de 500 y saltar el drenaje si la versión no cambió. | S |
| R11 | **Índices sin un solo uso.** | Medido: `ix_offrev_search_document` 263 MB, tres `ix_offer_revisions_catalog_*` 88 MB, `ix_vacancies_primary_source_feed` 17 MB; en `jobs`, 98 MB. | No borrar el de texto hasta resolver R1: la rama léxica debería usarlo y no lo hace. Los demás, tras revisar quién los creó y para qué. | S |
| R12 | **`saved_search_observations` tiene 1,59 millones de filas para 10 búsquedas** (190 MB). | Medido. Retención sin revisar. | Revisar su purga. | S |
| R13 | **Cliente HTTP y cliente de Groq nuevos en cada petición del BFF.** | Confirmado; 14 ms por construcción medidos en un portátil, sin medir en el NAS. `backend/routers/match.py:51-54`, seis `default_client_factory`. | Un `GroqService` y un cliente del núcleo por proceso. | S/M |
| R14 | **El feedback implícito vacía las cachés del feed** en los dos procesos, aunque no cambia la versión. | Confirmado: `backend/services/matching/feedback.py:166-170`. | Invalidar solo con `kind == "feedback"`. | S |
| R15 | **Trabajos periódicos de Portfolio sin demanda:** resumen de 20 fuentes cada hora aunque nadie abra el panel, y avisos de colegios cada 5 min. | Medido: 264 lecturas `recent` y 286 de colegios al día. | Refrescar bajo demanda; colegios cada 30 min. | S |
| R16 | **Celery sin `task_ignore_result` ni límite de memoria por hijo** en los dos proyectos; el worker del BFF tiene concurrencia 2 para una tarea cada 5 min. | Medido: 296 resultados de tarea en Redis sin lector; ningún OOM ni reinicio. | `task_ignore_result=True`; concurrencia 1 en el BFF. Toca compose. | S |
| R17 | **BFF y Portfolio comparten la clave de Groq** y traducen el mismo corpus cada uno por su lado; Portfolio no tiene clave de Gemini, así que su proveedor «primario» nunca corre. | Medido: misma huella de clave en los cuatro contenedores; `GOOGLE_API_KEY` ausente en Portfolio. | Traducir una vez en el núcleo (`title_en` aditivo en `VacancyDTO`) es la solución de fondo. | M |

Redis no es un problema: 8 MB usados de 200, todas las claves con caducidad salvo cinco de Kombu, colas vacías.

## Simplificación

| | Qué se puede borrar | Tamaño | Estado |
|---|---|---|---|
| S1 | **Portfolio: rutas por fuente sin cliente** (`core_source_compat.py`, `jobicy_jobs.py`, casi todo `jsearch_jobs.py`). Son además los llamadores del agregado caro. | ~500 líneas | Confirmado por grep en el frontend |
| S2 | **Portfolio: `folium` y sus dependencias** (`branca`, `xyzservices`, `numpy`) para una ruta sin cliente; restos del motor local de AI Match; botón «traducir títulos» redundante con `title_en`. | 4 dependencias | Confirmado |
| S3 | **BFF: ramas de `CORE_FEEDBACK_ENABLED=False`**, muertas en producción y únicas lectoras de `jobs` y `match_results`; `match_result_service.py`; respaldo `limit=3000` del frontend. | >500 líneas | Confirmado; los tests fijan el flag a `False`, así que es el cambio largo |
| S4 | **BFF: `send_watchlist_digest`** (no-op diario, A-10), `orjson` sin importar, `weights_used` (una consulta por petición para un campo que el frontend no lee), la rama de re-ranking de `groq_service.py` (DT-148 B1). | — | Confirmado |
| S5 | **Núcleo: tareas sin llamador** (`run_all_task`, `run_profile_task`, `run_pending_task`, `ping`), cola `core.embedding` sin productor, recetas híbridas v1-v3 si están inactivas. | — | Confirmado por grep; las recetas dependen de R2 |
| S6 | **Base del núcleo: tablas muertas.** Esquema `public` heredado (501 MB) y `shadow_change_log` (216 MB). | ~720 MB | Medido; ligado a R7 |
| S7 | **Variables de entorno sin lector o mal colocadas:** el contenedor de PostgreSQL lleva `CORE_CONSUMER_KEY`; `POSTGRES_*` en el BFF; `SCHEDULER_DAILY_HARVEST_ENABLED` en `erasure-cdc`. | — | Confirmado en lo versionado |

## Lo que la documentación dice y ya no es cierto

- **`CLAUDE.md` da por abiertos A-03, A-19 y A-68: están cerrados** en el código. Solo sigue abierto A-09 (`CORE_HTTP_TIMEOUT_SECONDS` × 2 en cuatro sitios).
- **Los correos del BFF sí salen:** la alerta de profesor se envió el 23, el 24 y el 25 de noviembre. El párrafo sobre la contraseña SMTP de 14 caracteres está desfasado.
- **Las credenciales de consumidor vivas no tienen fecha de caducidad:** el riesgo de corte a los 90 días no aplica a las actuales, pero tampoco hay alerta si se emite una con fecha.
- **La regla «desplegar el núcleo antes que el BFF»** por el parámetro `profile` ya no tiene quien lo envíe; para los eventos el orden seguro es el contrario mientras F8 siga abierto.
- `ReactPortfolio/backend/CLAUDE.md` cita módulos que no existen (`sentence-transformers`, `worker_bridge`, `saved_search_runner.py`).

## Orden propuesto

1. **Parar el desperdicio sin tocar lógica de negocio:** F1 (pausa de Groq), R6 (intervalo de borrados), R4 (drenaje de embeddings), R5 (calentar solo a usuarios activos), R10 (página de 500 en Portfolio). Son cambios de una constante o de una condición.
2. **R1, el respaldo híbrido.** Es el mayor ahorro individual y tiene tests de SQL que lo acompañan. Después, medir de nuevo y sacar el plan de la ejecución rápida.
3. **Lo roto en silencio:** F2, F3, F4, F6, F7.
4. **Decisiones del propietario:** R2 (política en sombra), R3 (barrido de dedup), R7 y S6 (esquema heredado y `erasure-cdc`), F5 (buzón de Portfolio), la cuota de Groq frente a 8.000 resúmenes.
5. **Limpieza de código:** S1 a S5, con S3 al final por su coste en tests.

Tras el paso 1 y el 2 conviene repetir la medición de `pg_stat_statements` con el contador a cero, para comparar con las cifras de este documento.

## Lo que no se revisó

- Planes de ejecución reales de las consultas, salvo R4.
- En el núcleo: el resto de productores, `cross_encoder.py`, `source_aggregate.py`, `search_execution.py`, migraciones.
- En el BFF: `routers/documents.py`, `profile.py`, `auth.py`, generación de documentos, páginas del frontend distintas de `/match`, tamaño real del bundle.
- En Portfolio: `auth.py`, generación de CV y PDF, componentes y CSS, la auditoría de seguridad del frontend.
- Paridad campo a campo de los DTO entre los tres lados.
- Quién origina hoy las llamadas al agregado por fuente: en las últimas 24 h fueron 36, todas del BFF, frente a una media histórica de 180 al día.
