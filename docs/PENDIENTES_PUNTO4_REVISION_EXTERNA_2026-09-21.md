# Punto 4: pendientes reales, plan mínimo y revisión de las estimaciones

Fecha: 21 de septiembre de 2026. Destinatarios: propietario y revisor externo.

## 1. Qué afirma este documento y qué no

El punto 4 es **transferir los productores de empleo y retirar las responsabilidades del motor legacy sin perder comportamiento**. No es rehacer ambos proyectos, mejorar el ranker ni conseguir otro examen de calidad.

La aplicación ya sirve ofertas mediante core. Sin embargo, parte de la adquisición de ofertas, los avisos y el postprocesado siguen dependiendo de procesos o datos anteriores. Apagarlos indiscriminadamente puede dejar funcionando la pantalla mientras dejan de llegar novedades o notificaciones.

**La estimación inicial de «unas horas» fue insuficientemente fundamentada.** Estimé el cambio de productores sin haber cerrado el inventario de escritores, lectores indirectos y estado de notificaciones de los dos proyectos. Eso fue un error de planificación mío; no demuestra que el propietario haya ampliado el encargo. Tampoco sería riguroso sustituir aquella promesa por otra de «días» sin desglosar lo pendiente.

La estimación posterior de **16–32 horas efectivas / 2–4 jornadas** era orientativa, no un cálculo contrastado de todas las fuentes activas. Esta revisión detecta que tampoco se puede tratar como un límite superior: existen productores registrados sin equivalente nativo cuyo uso efectivo aún debe conciliarse. A la inversa, hay tareas que pueden resolverse con configuración o reutilización y no requieren una reescritura.

Este documento permite cuestionar ambas estimaciones. Cada pendiente distingue evidencia, necesidad, solución mínima, prueba de cierre e incertidumbre. No presupone que todas mis propuestas deban implementarse.

### Base y límites de la comprobación

- Código SwissJob inspeccionado: HEAD `a859bf5`; último cambio funcional `3d5d67a`.
- Código Portfolio inspeccionado: backend HEAD `0f7ac3f`.
- Última evidencia operativa utilizada: actas del 21-09, hasta el corte WorkingNomads de las 16:53 UTC. **En esta preparación documental no se ha vuelto a inspeccionar el NAS**, por lo que las cifras operativas son una fotografía, no una sonda actual.
- Se han leído código, historial, runbook y actas. **No se han ejecutado suites nuevas**, hecho cambios funcionales, desplegado, enviado avisos ni reactivado la ejecución del punto 4.
- Las pruebas citadas proceden de las actas identificadas. Se distingue lo ensayado en copia, lo servido realmente y lo aún no comprobado.
- Se aplica YAGNI: reutilizar los ejecutores, fronteras y pruebas existentes; no inventar un framework de migración, otro motor ni una funcionalidad nueva.

Referencias de alcance: `docs/RUNBOOK_RETIRADA_PRODUCTORES_PUNTO4.md:19`, `docs/CIERRE_PUNTOS_4_5_2026-09-19.md:67` y `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md:2625`. Las actas más recientes prevalecen sobre sus checkpoints históricos.

## 2. Qué ya está terminado y no debe repetirse

| Componente | Evidencia de cierre disponible | Límite de esa evidencia |
|---|---|---|
| Entrega de perfiles SwissJob al core | Canal durable con outbox, autoridad y recuperación; acta `PROFILE_DEPLOYMENT_NAS_2026-09-20.md`. | No implica haber retirado la captura de ofertas. |
| Feedback y referencias nativas | Corte de 112 cambios, lector/escritor core, recuperación post-corte en copia; `FEEDBACK_DEPLOYMENT_NAS_2026-09-20.md`. | No hay que volver a migrar este estado para cada portal. Un binario antiguo puede incumplir la autoridad nueva. |
| Diez búsquedas SwissJob | IDs y valores conservados; ejecución core habilitada; legacy rechaza las diez por `core_authority`. | Falta observar la primera entrega natural, no rehacer su migración. |
| Pendientes de esas búsquedas | 277 pendientes conservados; 92 ofertas recuperadas; replay sin nuevas aplicaciones del plan. | Son cifras del snapshot del corte, no constantes que deban seguir iguales después de operar. |
| WorkingNomads | Guardas en los tres productores SwissJob afectados, drenaje, CDC al día y un scope nativo activado. Lote de 40 ofertas, replay sin segunda cosecha. | Es la primera fuente transferida, no todos los productores. |
| Paridad WorkingNomads | Misma respuesta pública: 45 registros, mismos IDs y 12 campos comparados. Reparación histórica con ensayo de rollback e idempotencia. | No certifica los campos de Remotive, Jobicy ni otros portales. |
| Lecturas después del corte | Cinco vacantes primarias nativas servidas por HTTP/CoreCatalog; ambos feeds por CoreMatching, páginas de 20 y totales 1800/1799. | Lectura real; no se crearon candidaturas ni se enviaron correos sintéticos en producción. |
| Infraestructura de productores | Runner, exclusión por scope, dispatcher nativo, admisión, registro de adaptadores y guardas selectivas ya implementados. | Existencia de código no equivale a activación ni a paridad de cada fuente. |
| Documentos y estado de colegios | Puntos 2 y 3 cerrados según sus actas E.14/E.15. | La autoridad del estado escolar es distinta de quién descarga las páginas y publica observaciones. |

Evidencia principal:

- [Corte WorkingNomads](audits/WORKINGNOMADS_CUTOVER_2026-09-21.md).
- [Corte de búsquedas y recuperación](audits/SEARCH_CUTOVER_OPERATOR_2026-09-21.md).
- [Corte de feedback](audits/FEEDBACK_DEPLOYMENT_NAS_2026-09-20.md).
- [Entrega de perfiles](audits/PROFILE_DEPLOYMENT_NAS_2026-09-20.md).
- [Preflight de los productores Remotive](audits/REMOTIVE_ALL_PRODUCERS_PREFLIGHT_2026-09-21.md).

Última suite core registrada: **1645 passed, 2 warnings, 907,32 s**. La suite BFF registrada en el corte de búsquedas: **2508 passed, 4 xfailed, 5 warnings**, 328,52 s. No se suman pruebas dirigidas que ya están dentro de una suite. Un verde de tests no sustituye el recorrido servido, pero tampoco justifica repetir toda la suite sobre código intacto.

## 3. Inventario: qué existe y qué falta conocer

### 3.1 Adaptadores nativos ya implementados

El registro ejecutable `jobhunt_core/harvest/providers/__init__.py:13` compone **15 nombres**:

| Familia | Fuentes | Situación al último checkpoint |
|---|---|---|
| JSON | WorkingNomads, Remotive, Jobicy | WorkingNomads transferida. Las otras dos tienen adaptador, no corte acreditado. |
| API Arbeitnow | Arbeitnow | Adaptador existente; falta acreditar su traspaso completo. |
| RSS | WeWorkRemotely, EURemoteJobs, Jobspresso, GlobalJobs, Zebis | Código y evidencias de paridad disponibles; falta cierre operativo por fuente. |
| CH Media | Ostjob, Zentraljob | Adaptador existente; identidades históricas necesitan reconciliación específica. |
| Otros | PublicJobs, NAV, TheHub, Jobgether | Adaptadores existentes. TheHub tiene paridad documentada; NAV/Jobgether registraron problemas de acceso/completitud. |

Referencias: `native_json.py:29`, `native_rss.py:22`, `native_chmedia.py:21` dentro de `jobhunt_core/harvest/providers/`.

**No faltan catorce adaptadores de esta tabla:** falta verificar y transferir catorce fuentes ya representadas en código, según su actividad real y dependencias. Tampoco se debe activar todo el registro por el mero hecho de existir.

### 3.2 Registro SwissJob sin equivalente en ese registro nativo

- Providers: Adzuna, Careerjet, Jooble, JSearch, RemoteCo, Proz y los cinco providers de acceso autorizado/partner.
- Scrapers generales: Gastrojob, Stelle Admin, TES, SchulJobs, MyScience, Financejobs e IrishJobs.
- Ocho scrapers escolares: NAE, ISP, Inspired, ZIS, ISB, Ecolint, Haut-Lac e ISCS.

Referencias: `backend/providers/__init__.py:40`, `backend/providers/__init__.py:100`, `backend/scrapers/__init__.py:22`.

**Ausencia de adaptador core no demuestra que todos deban portarse.** Algunos providers exigen credencial y no se construyen sin ella; otros estaban retirados o fallan por causas externas. Es obligatorio distinguir:

1. Activo y útil: preservar su comportamiento al transferirlo.
2. Configurado pero fallando: conservar diagnóstico; resolver acceso autorizado o documentar una decisión explícita. No declarar saludable un 403/429.
3. Inactivo o sin credencial: no activar ni portar especulativamente; registrar su exclusión del alcance operativo.
4. Código histórico sin ejecución: no debe convertirse en un bloqueo automático.

No se ha renovado en esta revisión el censo de ejecución NAS. Por tanto, **no puedo afirmar cuántos de estos productores requieren código nuevo ni presupuestarlos como cero**.

### 3.3 Portfolio tiene su propio inventario

`/home/lothar/Public/ReactPortfolio/backend/services/job_provider.py:957` enumera 20 proveedores de lectura de caché:

Jobicy, Remotive, Arbeitnow, JSearch, RemoteOK, Himalayas, Adzuna, WeWorkRemotely, Ostjob, Zentraljob, SwissTechJobs, ICTJobs, SwissDevJobs, JobScout24, DynamiteJobs, JobRoom, TheHub, Jobgether, NAV e IrishJobs.

No equivale a 20 fuentes efectivamente sanas o a 20 trabajos nuevos. Sí demuestra que revisar solamente el registro SwissJob es insuficiente. JSearch, RemoteOK, Himalayas, Adzuna, SwissTechJobs, ICTJobs, SwissDevJobs, JobScout24, DynamiteJobs, JobRoom e IrishJobs no tienen un provider homónimo en el registro nativo inspeccionado. Debe comprobarse actividad y cobertura antes de decidir su tratamiento.

La guarda selectiva Portfolio existe en `0f7ac3f` y se aplica antes de cosechar en `services/job_cache_updater.py:354`; la última acta dice **no desplegada**. No usar el apagado global de schedulers como sustituto: también detendría funciones que deben conservarse.

### Entregable mínimo del censo pendiente

Una única tabla operativa, no otra arquitectura: fuente, consumer/productor, imagen, entrada periódica/manual/on-demand, consultas/categorías, filtros, última ejecución completa, destino, lector dependiente, sustituto, decisión y evidencia. Incluir procesos público, R5 y Portfolio. No imprimir secretos ni datos de CV.

Este censo es la primera tarea al reanudar. Debe producir un resultado acotado en **60–120 minutos de trabajo**, o declarar exactamente qué acceso/evidencia lo impide. Es una caja de tiempo propuesta, no una medición ya hecha. Sin él, una fecha final cerrada sería especulativa.

## 4. Pendientes concretos y cómo cerrarlos

### R1. Observar el primer recorrido natural de avisos SwissJob

**Qué falta.** Confirmar una búsqueda realmente vencida: evaluación, observaciones, outbox, recepción y presentación/entrega correspondiente, preservando los contadores. Los barridos observados terminaron correctamente pero con cero búsquedas vencidas. Una sonda inválida al inbox devolvió 422; eso acredita acceso a la ruta, no entrega.

**Por qué.** «El ejecutor no falla» no prueba que un usuario reciba el aviso. Pero esta comprobación no impide preparar ni cortar otras fuentes.

**Acción mínima.** Consultar la próxima ejecución efectiva y observarla; correlacionar identidad del evento entre productor, outbox y receptor. Verificar reintento/idempotencia con la evidencia aislada existente. Si hay coincidencias y el canal es correo, distinguir aceptación SMTP de recepción final: no prometer exactamente una entrega si el transporte no lo garantiza.

**Si no hay novedades.** Un resultado vacío correcto no debe declararse fallo ni entrega demostrada. Conservar la prueba determinista en copia con receptor de pruebas y registrar «sin evento real disponible». No crear candidaturas, alterar preferencias ni mandar avisos falsos para fabricar un verde. No esperar indefinidamente a que aparezca una oferta como condición artificial de cierre.

**Cierre.** Recorrido completo acreditado mediante evento natural o ensayo controlado no vacuo del mismo camino desplegado, con el límite operativo declarado. No hace falta volver a reconciliar las diez búsquedas.

**Referencia.** `docs/audits/SEARCH_CUTOVER_OPERATOR_2026-09-21.md:1`. Próximo vencimiento estimado allí: 22-09, ~07:10 UTC, sujeto a cambios del usuario. No es una fecha garantizada.

### R2. Transferir las fuentes ya portadas sin perder campos ni cobertura

**Qué falta.** Para cada fuente activa de §3.1: configuración equivalente, identidades históricas, metadatos usados por filtros, único escritor y lote servido. Reutilizar lo ya ensayado; repetir sólo si cambian código, datos relevantes o condiciones del corte.

**Por qué.** Una oferta visible puede quedar excluida de búsquedas por perder idioma, contrato o seniority. Un ID mal enlazado puede desvincular guardados/candidaturas. Dos productores pueden seguir cosechando aunque la lectura ya sea core.

**Brechas concretas, no suposiciones generales:**

- `native_json.py:123` añade el enriquecimiento recién corregido sólo a WorkingNomads. Remotive/Jobicy requieren comparación de los campos efectivos antes de su corte; no afirmar sin prueba que heredan la paridad WorkingNomads.
- Remotive core admite `query`, no `category` (`native_json.py:156`). Portfolio descarga por categorías (`ReactPortfolio/backend/routers/remotive_jobs.py:90`); el acta cuenta 14 categorías. Un feed global de 200 no demuestra cobertura equivalente. La mínima corrección candidata es conservar esos scopes/categorías en el adaptador existente, si el censo confirma que siguen activos.
- Jobicy documenta feed de 50 por tag/geo: preservar las consultas reales, no convertir varios scopes en una sola descarga arbitraria.
- CH Media: ID ATS y URL de aplicación pueden compartirse entre plazas. Conservar ID de portal y URL de detalle; reconciliar históricos por evidencia, sin elegir un ganador arbitrario ni fusionar plazas.
- NAV y Jobgether: resultados parciales/403/429 no son lotes completos. Tratar el problema de acceso como dependencia externa concreta, no reintentar agresivamente ni rebajar la prueba para cerrar.

**Secuencia mínima de un grupo compatible:**

1. Comparar viejo/nuevo sobre los mismos objetos públicos y las mismas consultas. Identidad, título, texto, fecha, URL de solicitud, ubicación, filtros y metadatos consumidos.
2. Preparar el mapa histórico y ensayar sólo las transformaciones nuevas con constraints reales. Conservar alias, decisiones del usuario y procedencia.
3. Mantener desactivado el scope nuevo; cerrar entradas legacy programadas y manuales de **todos** los productores de esa fuente.
4. Drenar trabajos en vuelo; recrear los procesos que leen flags al arrancar. Sin purga indiscriminada de colas.
5. Drenar los cambios CDC confirmados y registrar el corte.
6. Activar el sustituto, ejecutar lote real, repetir la misma operación y comprobar no duplicación ni avance falso ante parcial.
7. Verificar la oferta por BFF: lectura/enlace y, en entorno de prueba, guardar/rechazar/candidatura/documento con UUID nativo. Reutilizar el contrato común probado; no repetir todas las combinaciones por cada portal si no cambia su semántica.
8. Registrar autoridad, imagen, configuración saneada, conteos y recuperación. Confirmar además que el scheduler real despacha, no sólo que una invocación manual funciona.

**Cierre.** Fuente sin productor antiguo efectivo, cobertura preservada o excepción ratificada, ofertas nuevas accionables, retry seguro, operación programada y recuperación verificadas.

**Agrupación.** Una imagen y suites por lote compatible; varios cortes independientes con recibos separados. No agrupar sólo porque dos portales devuelvan JSON: categorías, cursores o identidades diferentes pueden exigir preparación distinta.

### R3. Resolver únicamente los productores activos todavía no portados

**Qué falta.** El subconjunto real de §3.2–3.3 que el censo confirme necesario. Aquí está la principal incertidumbre de plazo.

**Por qué.** No se puede retirar su ejecutor y conservar una fuente si no existe quien la consulte. Pero tampoco tiene sentido portar una API sin credenciales o resucitar una fuente descartada.

**Solución mínima.** Reutilizar parser/extractor probado y la infraestructura core. Separar descarga/normalización de persistencia legacy; no importar el backend dentro de `jobhunt_core` ni duplicar un motor completo. Mantener pacing, errores parciales, fechas, protección de contenido anterior e identidad. Una extracción mecánica compatible es preferible a un scraper nuevo.

**Alternativa que debe evaluar el revisor.** Un extractor existente puede seguir como componente especializado y publicar al core, si no conserva un segundo corpus/motor autoritativo y cumple el contrato ratificado de retirada. Eso puede evitar una reescritura. No dar por cerrado «todos los productores en core» si el alcance exige precisamente trasladar su ejecución: la alternativa debe registrarse como decisión de arquitectura, no ocultarse con un cambio de nombre.

**Cierre.** Cada fuente activa tiene destino acreditado; cada excluida tiene causa verificable. Una fuente inaccesible requiere solución o decisión explícita, no «cierre por omisión». No aumentar cobertura ni contratar servicios nuevos como parte tácita del trabajo.

### R4. Portfolio: desacoplar búsquedas y digest de sus cachés recolectoras

**Evidencia de la dependencia.**

- `services/saved_search_runner.py:175` rechaza ejecutar cuando el escritor está en core; su evaluación local lee `collect_normalized_jobs()` en la línea 230.
- `services/daily_match_report.py:330` también obtiene ofertas de `collect_normalized_jobs()`; conserva lógica propia de selección, umbral, ledger y entrega.
- `services/job_provider.py:991` reúne los proveedores de caché. Apagar la descarga no convierte automáticamente ese conjunto en una lectura core actualizada.
- Core `jobhunt_core/search_execution.py:22,53` sólo admite el contrato `swissjob-v1`, no cualquier formato de búsqueda Portfolio.

**Qué comprobar antes de programar.** Routing y búsquedas activas reales de Portfolio, campos de sus filtros, destinatarios/canales, umbral y semántica de novedades del digest. No asumir que el estado de las diez búsquedas SwissJob cubre al otro consumer.

**Por qué importa.** Tras dejar caducar la caché, el frontend podría seguir leyendo core pero el correo diario quedarse vacío, obsoleto o repetir ofertas antiguas. Habilitar el contrato SwissJob con filtros Portfolio sin traducirlos puede producir silenciosamente otro conjunto.

**Solución mínima a comparar:**

1. Consumir catálogo/estado core desde el lector del aviso existente, conservando su semántica y ledger, si eso cumple la retirada del motor local; o
2. Añadir el contrato Portfolio al ejecutor existente con el mínimo mapeo de filtros/observaciones y su receptor, si permite retirar de verdad el motor duplicado.

No decidir un segundo framework de notificaciones por anticipado. Tampoco sustituir silenciosamente el digest actual por el top-10 del feed: son funciones distintas. Mantener las preferencias, historial de entregas y los casos escolares existentes.

**Pruebas de cierre.** Oferta sólo core aparece cuando corresponde; filtro editado/búsqueda eliminada deja de notificar lo anterior; doble ejecución no duplica observaciones; fallo de receptor conserva pendientes; reinicio conserva ledger; vacío y error son distinguibles; separación de consumers. Ensayar con receptor aislado, sin correo al propietario para validar un fixture.

**Despliegue.** Incluir la guarda `0f7ac3f` sólo después de cerrar esos lectores. Revisar el commit previo `26b752a`, que cambia el default del modelo CV; preservar configuración efectiva para no introducir un cambio ajeno al corte.

### R5. Colegios: retirar la dependencia del productor, no repetir E.15

**Qué ya existe.** El estado escolar vive en core. Portfolio ya lee monitores de core cuando procede y publica observaciones por su frontera existente.

**Dependencia que sigue siendo concreta.** SwissJob `backend/services/schools/producer.py:101` lee `Job` local para reconciliar observaciones; `backend/tasks/scraping_tasks.py:529` condiciona el reconocimiento del cursor a esa publicación. En Portfolio, `services/school_scraper.py:146,319` todavía realiza extracción y coordinación desde el BFF, aunque `school_store` sea core.

**Por qué.** Desactivar esos procesos no sólo retira un scraper: puede detener nuevas observaciones/avisos escolares. Tener la tabla escolar en core no prueba que el productor sea autónomo.

**Mínimo.** Enumerar estrategias realmente activas; preservar extracción existente, identidad por monitor, contexto de consumer, dedup/actualización y alertas. Hacer que la publicación no necesite un `Job` legacy como paso intermedio. Reutilizar `school_ingest`/API escolar y las costuras actuales. No reimportar documentos ni repetir el corte de estado E.15.

**Pruebas.** Alta/actualización/retry de observación, ausencia legítima vs fallo de portal, dos monitores del mismo portal, aislamiento entre consumers, reanudación tras publicación parcial, avisos pendientes y enlaces servidos. Una baja no puede inferirse de una descarga incompleta.

**Decisión a contrastar.** Extraer el trabajo a core o conservar un colector especializado sin estado legacy. Si conservarlo satisface el contrato, no convertir la ubicación física del fichero en un bloqueo; si no, dejar explícito el portado necesario. No prometer que portar todas las estrategias requiere sólo unas horas sin inventariarlas.

### R6. Garantizar postprocesado sin captura CDC

**Precisión respecto a indicaciones anteriores.** No está demostrado que sea necesaria una reescritura completa de embeddings/matching. El proyector actual llama a recuperación aunque no haya lotes: `shadow/projector.py:370` y `_replay_after_batch` en la línea 1605. Cuando no hubo trabajo agregado, drena embeddings; después evalúa perfiles con señal. El beat programa `jobhunt.shadow.project` (`celery_app.py:153`).

**Qué falta demostrar.** Con captura CDC parada y sin nuevas filas legacy, una oferta nativa y una edición de perfil completan canónica → embedding → evaluación → feed, con reintento tras fallo. Verificar también dedup/mantenimiento/avisos y las sondas que dejarán de tener sentido al retirar el slot.

**Ruta mínima.** Primero probar ese recorrido con lo que existe. Si pasa, puede bastar mantener el trabajo core reutilizado y separar únicamente la captura/sondas obsoletas. Si exige todavía datos legacy o el contrato requiere retirar también el drenador, extraer la coordinación postcosecha a una tarea core acotada reutilizando las funciones, sin copiar SQL ni reglas de recuperación.

**No hacer.** Deshabilitar todas las tareas `shadow.*` por su nombre; algunas sostienen trabajo útil. Tampoco conservar dependencias legacy ocultas y afirmar retirada completa.

**Coste/contención.** El drenaje observado de ~24 minutos justifica comprobar límites y reanudación, no por sí solo inventar un nuevo sistema de colas. La optimización global queda en punto 5 salvo que impida completar o recuperar el corte.

**Cierre.** Cadena autónoma con captura detenida, cambios nuevos visibles, reinicio recuperable, sin lector imprescindible de tablas legacy. Si queda una dependencia deliberada, el acta debe nombrarla; no cumple una retirada total por ocultarla.

### R7. Retirada final, recuperación y aceptación específica del corte

**Condición previa.** Todos los productores activos y sus lectores dependientes tienen sustituto o excepción aprobada. Perfiles/feedback/búsquedas ya transferidos permanecen bajo su autoridad nueva.

**Trabajo:**

1. Revisar despachos periódicos, manuales, arranque y peticiones que provocan fetch. La cadena legacy aún incluye `fetch_providers → fetch_scrapers → embed_all_pending → dedup_semantic_batch` (`backend/tasks/pipeline_tasks.py:70`). Una lista de providers vacía no elimina todas sus tareas.
2. Retirar sólo tareas y procesos sin responsabilidad útil. Mantener BFF, autenticación, generación/análisis de CV, entrega documental y cualquier tarea aún necesaria. «Retirar legacy» no significa borrar ambos backends ni apagar un worker que todavía sirve esas funciones.
3. Drenar colas y CDC; comprobar pendientes, errores y último punto confirmado. Gestionar la retirada del slot/sondas según el procedimiento operativo: ni borrar un slot prematuramente ni dejarlo abandonado reteniendo WAL indefinidamente.
4. Conservar histórico y evidencias dentro de su política de retención. No borrar tablas/volúmenes productivos para obtener una foto sin legacy.
5. Comprobar las rutas afectadas en ambos BFF y los flujos de acciones con ofertas exclusivamente nativas; verificar también ejecución programada, no sólo llamadas directas.
6. Ensayar recuperación **posterior** al corte con nuevas decisiones de usuario. Reusar los ensayos existentes para contratos sin cambios; añadir sólo el caso que cambie.

**Recuperación no equivale a volver al pasado.** Deshabilitar/drenar el nuevo escritor antes de rearmar otro. Conservar nuevos estados, observaciones y outbox. Restaurar un snapshot anterior sobre producción activa puede perder datos; si no hay reversión segura, mantener autoridad nueva y reparar hacia delante. El `revert` preactivación de búsquedas no es válido tras actividad nueva.

**Cierre.** Ningún camino retirado sigue produciendo; los que se conservan tienen responsabilidad explícita y no son segundo motor; ofertas nuevas, acciones y avisos mantienen contrato; recuperación acreditada. No se exige que no quede una sola línea antigua en Git.

## 5. Qué no debe añadirse al punto 4

- Otro holdout, RankNet, promoción de política o racha de siete días. Calidad del ranking es un hito separado.
- Rendimiento integral y optimización general: punto 5. Aquí sólo medir lo necesario para que el cambio no bloquee, pierda trabajo o sobrecargue el NAS.
- Nuevas fuentes, exportación integral, interfaces nuevas o cambio funcional del digest.
- Migrar o reactivar APIs sin credenciales o fuentes deliberadamente retiradas.
- Reabrir documentos/colegios/feedback cerrados salvo regresión reproducible del cambio actual.
- Reescribir todos los scrapers, introducir dependencias nuevas o rehacer el orquestador por uniformidad estética.
- Fusionar ramas/publicar código, eliminar historia o alterar `:prod` como efecto tácito de este informe. La versión de entrega y sus operaciones se gestionan explícitamente.
- Programación general de retención/alarma diferida por el propietario; distinta de cumplir la caducidad de las copias temporales que ya se crearon.

## 6. Por qué se consumió más tiempo

| Causa | Evidencia | Qué debí haber hecho antes / qué cambia ahora |
|---|---|---|
| El alcance real no era sólo sustituir descargas | Había dependencias de perfiles, feedback y búsquedas del corpus anterior. Los commits `a93dcbc`, `bac5e78`, `038b099`, `fc7dc70`, `18dbb8c`, `725be1a`, `a0fb403` muestran trabajo entregado para cerrarlas. | Inventario extremo a extremo antes de estimar. No presentarlo como sorpresa inevitable ni como ampliación pedida por el usuario. |
| Búsquedas requerían conservar historial y pendientes, no sólo filas | Captura real, 277 pendientes y 92 referencias que hubo que recuperar. | Identificar desde el inicio semántica de novedad y marcadores Redis; no igualar `10 filas` a migración terminada. Ese trabajo ahora está hecho. |
| Había más de un productor por fuente | Remotive: público, R5 y Portfolio con categorías distintas. | Matriz fuente × productor × lector. No trasladar todo el esfuerzo WorkingNomads multiplicándolo por número de portales. |
| El adaptador podía perder metadatos funcionales | WorkingNomads: fix `3d5d67a`, paridad de 12 campos, reparación de histórico. | Comparar contratos completos de filtros antes del corte, no sólo título/URL o conteos. Convertir diferencias reales en pruebas, sin auditar todo desde cero. |
| Hubo incidencias reales del proceso de migración | Plan de feedback lento/bloqueante; `FEEDBACK_PLAN_LOCKS_2026-09-20.md`. Captura de búsquedas abortó por timeout antes de reintentar. | Consultas por lote, locks cortos y dry-run. No intentar resolver con timeouts crecientes o declarar que todo tiempo adicional era inevitable. |
| La operación tiene tiempos no instantáneos | Suite core ~15,1 min; BFF ~5,5 min; un drenaje core ~24 min. | Presupuestar pruebas/build/drenaje. Agrupar releases compatibles; no repetirlos para cada cambio documental. |
| Documentación fragmentada/histórica | Algunos encabezados dicen «no desplegado» después de existir un acta posterior de corte. | Un checkpoint vigente con enlaces. Separar implementación, ensayo, despliegue y confirmación para no rehacer pendientes ya cerrados. |

No existe en esta revisión una contabilidad fiable minuto a minuto de las más de 15–16 horas indicadas por el propietario. **No atribuyo todas esas horas a causas demostradas ni afirmo que no se pudiera haber trabajado más eficientemente.** El historial acredita entregables, no productividad ni necesidad de cada repetición.

Las correcciones posteriores no tienen todas el mismo origen: algunas eran dependencias antiguas descubiertas tarde; otras, defectos del código o del operador recién preparado. Las regresiones y los commits permiten clasificarlas, pero no sería honesto etiquetar todo como «errores nuevos inevitables».

## 7. Estimación revisable, no otra promesa

### 7.1 Qué se puede estimar y qué no

No hay una espera intrínseca de varios días para cerrar punto 4. Los días pueden resultar de horas de trabajo, disponibilidad operativa, límites de portales o amplitud real del inventario. La racha 7×24 h no pertenece a este cierre.

El siguiente desglose es una **hipótesis de planificación de confianza baja/media**, no un benchmark ni compromiso. Supone reutilización elevada, acceso operativo disponible, ningún defecto grave nuevo y pocas estrategias activas adicionales. Incluye pruebas dirigidas y preparación de la evidencia; no es tiempo continuo con producción parada.

| Trabajo restante | Horas efectivas propuestas | Supuesto / fuente principal de incertidumbre |
|---|---:|---|
| Censo final y decisiones de inclusión | 1–2 | Acceso a configuración y actividad, sin incidentes. |
| Verificación de avisos SwissJob | 0,5–1 | Recorrido existente sano; espera natural aparte. |
| Fuentes ya portadas: brechas menores, conciliación y cortes por grupos | 4–7 | Alta reutilización de paridad/ensayos; no incluye portar productores ausentes. |
| Lectores/avisos/digest Portfolio | 4–7 | Reutilizar una frontera existente; no diseñar otra plataforma. |
| Productores escolares | 3–6 | Pocas estrategias efectivas y extracción reutilizable; si el censo lo refuta, este rango no vale. |
| Postprocesado sin CDC | 1–3 | Recorrido actual reutilizable; separación local si hace falta. |
| Retirada final y comprobaciones cruzadas | 2–4 | Sin pérdida de estado ni divergencias pendientes. |
| Acta y actualización documental | 0,5–1 | Una fuente de estado vigente, sin reescribir todo el historial. |
| **Subtotal condicionado** | **16–31** | **No es una cota del punto 4 completo.** |

**Falta sumar el código realmente ausente de R3**, si el censo confirma productores activos sin sustituto. No se asigna un número ficticio a ese subconjunto: debe estimarse por familias/parser/estado una vez identificado, descontando las estrategias escolares ya presupuestadas. Tampoco se suma una contingencia silenciosa que luego se presente como trabajo obligatorio.

Si el revisor demuestra que un lector ya está cubierto o que un colector existente cumple el contrato sin portado, se elimina ese trabajo y se reduce la estimación. Si aparecen varias familias activas no portadas, hay que reconocer que 16–32 horas no era suficiente, no desplazar otra vez la fecha sin explicar el cambio.

### 7.2 Tiempo de reloj que no es programación

| Concepto | Evidencia o límite | Cómo abreviarlo sin falsear el cierre |
|---|---|---|
| Suite core | Última acta: 907,32 s (~15 min). | Una suite por candidata estable; dirigidas por fix. |
| Suite BFF SwissJob | Acta búsquedas: 328,52 s (~5,5 min). | Ejecutarla si cambia esa capa. Suites siempre en serie por su BD compartida. |
| Suite Portfolio | Acta guarda: 198,14 s (~3,3 min), con una comprobación posterior adicional dirigida. | No atribuir pruebas posteriores a aquella pasada; ejecutar candidata final si cambia. |
| Build/carga/recreación de imágenes | No hay medición actual suficiente en este informe. | Construir por release; conservar SHA/digest; no multiplicar reinicios por portal innecesariamente. |
| Drenaje | Un caso real ~24 min. No es cota general. | Preparar cortes antes de drenar y agrupar los compatibles. No matar persistencias ni purgar colas para cumplir el reloj. |
| Cadencia native | Dispatcher a 00:10/06:10/12:10/18:10 según timezone de Celery (`celery_app.py:113`). | Prueba acotada del dispatcher/broker más observación programada; no esperar seis horas después de cada fuente en serie. |
| Aviso natural | Acta estimaba el siguiente vencimiento para 22-09 ~07:10 UTC. | Observar en paralelo al resto; no alterar la frecuencia del usuario para la prueba. |
| Portal bloqueado o limitado | NAV/Jobgether tienen evidencia histórica de 429/403. | Resolver vía autorizada o decidir excepción explícita. No hay plazo garantizable de un servicio ajeno. |

No sumar dos veces los mismos minutos si la tabla de trabajo ya los incluye. La unidad principal debe ser horas efectivas por paquete y, separadamente, espera no solapable. «Dos a cuatro días» sólo resulta de una jornada supuesta: no es una propiedad técnica de la migración.

## 8. Secuencia más corta que considero defendible

1. **Censo acotado y revisión de alcance** (§3). Salida: lista finita de activos, consumidores y lectores; fuentes excluidas justificadas; propuesta de horas ajustada. No iniciar otra auditoría global.
2. **Cerrar las dependencias compartidas primero**: Portfolio (R4), colegios (R5) y prueba del postprocesado (R6). Priorizar la que bloquee más fuentes. Preparar otras fuentes en el mismo lote de código cuando sean independientes, sin poner suites concurrentes sobre la misma BD.
3. **Preparar grupos de fuentes** con paridad y mapa histórico. Portar únicamente los huecos activos de R3. No esperar a una fuente con bloqueo externo para validar las demás, pero tampoco ocultarla del cierre.
4. **Candidata estable**: pruebas dirigidas, una pasada completa por repositorio afectado, revisión focalizada de locks/errores/identidades y `git diff --check`. Corregir un fallo y repetir las pruebas afectadas; decidir repetición integral por impacto, no por ritual.
5. **Ensayo incremental y corte**: usar copia válida, constraints equivalentes, receptor aislado y recibos. Reutilizar el restore si sigue cumpliendo esquema/retención y objetivo; refrescarlo si no. Sin suprimir errores de restauración.
6. **Confirmar y retirar** (R7); incorporar la evidencia de avisos R1 cuando esté disponible. Dejar rollback/recuperación actual, no el plan preactivación.
7. **Una sola acta de cierre** con el checklist siguiente. Si queda un rojo, describir el caso preciso y no proclamar cerrado el punto.

No se necesita autorización adicional para cada paso técnico ya autorizado al reanudar, pero sí se debe elevar una decisión que elimine cobertura, cambie comportamiento, introduzca coste o amplíe el alcance. Esta petición documental **no reanuda por sí misma los cortes**.

## 9. Pruebas y límites que previenen más rondas, sin prometer cero bugs

Para cada cambio, escribir primero el contrato observable y el caso de fallo. Probar el fix en su consumidor real, no sólo el helper. Mantener estas fronteras:

- Identidad estable y alias histórico; nunca enlazar por título/URL ambigua.
- Un solo escritor antes, durante y después del corte; flags en arranque requieren drenaje/recreación.
- Error/descarga parcial no equivalen a conjunto vacío completo; no archivar por un fallo de fetch.
- Filtros y metadatos equivalentes; no ganar «paridad» quitando el filtro que falla.
- Estado, contadores, ya enviados y pendientes conservados; replay no recrea novedades.
- Inferencia/red fuera de locks largos; revalidación de autoridad/entradas antes de publicar.
- Fallo entre commit y ACK, doble entrega/retry y reinicio probados donde cambia la cadena.
- Modelo/política/corpus y parámetros de calidad intactos salvo cambio expresamente aprobado fuera de este punto.
- Cada prueba distingue resultado no vacuo de «no había nada que hacer».

No se puede garantizar que no habrá más bugs. Sí se puede evitar una sucesión ilimitada de revisiones imponiendo un DoD finito y exigiendo que cualquier nuevo bloqueo tenga reproducción, impacto en ese DoD y corrección mínima. Estilo, refactorización opcional o una hipótesis sin evidencia no reabren el punto.

### Checklist de cierre del punto 4

- [ ] Inventario efectivo de ambos proyectos conciliado; cada fuente activa tiene responsable y sustituto o excepción ratificada.
- [ ] Productores transferidos con cobertura/identidad/filtros preservados; sin escritor viejo efectivo ni bypass manual/on-demand.
- [ ] Nuevas ofertas exclusivamente core son visibles y accionables por las rutas servidas; prueba de cambios de usuario en entorno controlado.
- [ ] Las diez búsquedas conservan su contrato; entrega no vacua acreditada con límites claros.
- [ ] Búsquedas/digest Portfolio no dependen de una caché que deja de actualizarse; consumidores y destinatarios aislados.
- [ ] Colegios siguen generando observaciones/avisos sin depender del corpus legacy que se retira.
- [ ] Embeddings, dedup, matching y notificaciones continúan sin captura CDC de legacy.
- [ ] Schedulers/colas/slots retirados o conservados con responsabilidad explícita; no se apagan tareas CV/documentales necesarias.
- [ ] Recuperación posterior al corte preserva decisiones nuevas y no crea dos escritores.
- [ ] Código, esquema, imágenes y configuración desplegados identificados; acta distingue probado/desplegado/confirmado y excepciones.

Cerrar esta lista no cierra automáticamente punto 5, cron de retención, aceptación integral final ni GO de calidad. Tampoco deben esos hitos añadirse silenciosamente a esta lista.

## 10. Deuda y documentación que deben quedar reconciliadas

### Estado documental observado

Los desfases enumerados a continuación corresponden a la lectura inicial del
informe. La actualización documental posterior, autorizada por el propietario,
añade estado prevalente a ESTADO §43, PLAN, BACKLOG y DEUDA, actualiza el runbook
y los encabezados de cierre/perfiles, sin reescribir las actas históricas.
No constituye nueva evidencia operativa ni cierre de R1–R7.

- El runbook tiene el checkpoint actualizado de WorkingNomads, seguido de estados históricos.
- `docs/CIERRE_PUNTOS_4_5_2026-09-19.md:3` aún encabeza un estado de preparación anterior al corte de búsquedas.
- `docs/PROFILE_DELIVERY_PUNTO4.md:3` dice «no desplegada»; el acta de despliegue posterior acredita lo contrario.
- `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md:2625` conserva como pendientes perfiles/feedback que después se cerraron.
- `/home/lothar/Public/DEUDA_TECNICA.md` mezcla foto E.15, auditoría 18-09 e histórico. No debe usarse cada marca «ABIERTO» como prueba automática del estado actual.

**Actualización mínima aplicada:** estado prevalente con fecha y referencias y encabezados anteriores identificados como históricos. Sólo se reclasifican cierres respaldados por actas; A18 conserva sus límites de evidencia. No se borran actas ni se hace una reescritura global. Las modificaciones previas ajenas se preservan y quedan fuera de los commits de esta actualización.

### Clasificación de deuda para este cierre

| Deuda | Tratamiento |
|---|---|
| Productor/lector activo sin sustituto; aviso que dejaría de funcionar | Bloqueante del corte correspondiente, R2–R5. |
| Postprocesado que necesita una tabla/servicio que se va a retirar | Bloqueante de la retirada final, R6. Probar antes de refactorizar. |
| Coste que impide drenar o bloquea usuarios durante la migración | Corregir en este punto con medición acotada. |
| Paginación/latencia global y recorridos completos del feed (A18-05) | Punto 5; no convertir toda optimización en precondición de cada portal. |
| Hallazgos A18-01/02/03/04 de filtros/watchlist/avisos | Conciliar con commits y regresiones actuales. Si afectan el recorrido tocado y siguen reproducibles, incluir el fix; no declararlos cerrados ni rehacerlos sólo por la fecha del documento. |
| Deriva de configuración de proveedores/modelos al construir imágenes | Comprobar configuración efectiva antes del despliegue; evitar introducir defaults ajenos. |
| Calidad del ranking / holdout nuevo | Separado, no condición añadida al punto 4. |
| APIs inaccesibles/inactivas | Estado explícito y decisión por fuente; no una falsa promesa de recuperación universal. |

### Copia temporal: obligación ya existente

El acta WorkingNomads registra una copia aislada NAS `swissjob-f-rehearsal.goIBte`, contenedor `swissjob-f-rehearsal-20260919`, con caducidad **21-09 a las 20:00 UTC / 22:00 Madrid**, sin borrado automático configurado. Este informe no ha comprobado su existencia ni borrado actual. Al operar, comprobar el registro y cumplir su retención; no reutilizarla fuera de plazo sin decisión expresa ni confundirla con la base viva. La pausa del agente no prorroga la conservación.

## 11. Encargo al revisor externo: validar o rebatir este plan

No se pide ratificar mi diagnóstico por autoridad. Se pide reducir el trabajo al mínimo seguro y detectar omisiones con evidencia.

### Material a revisar

1. Este documento y el runbook de punto 4.
2. Las actas de WorkingNomads, búsquedas, feedback/perfiles y censo Remotive enlazadas.
3. Código en las referencias de R1–R7, ambos repositorios y su configuración efectiva cuando exista acceso autorizado.
4. Inventario de actividad, no sólo imports o listas de registro.
5. Estado de pruebas/cambios sin commit. No revertir las eliminaciones documentales o cambios del propietario para «limpiar» el árbol.

### Preguntas que debe responder

- ¿El alcance incluye realmente cada productor listado? ¿Cuál es inactivo o sólo histórico y debe salir del presupuesto?
- ¿Qué parte de Portfolio ya puede leer core con un helper existente? ¿Es necesario mover su motor de digest o basta cambiar el lector preservando su contrato?
- ¿Qué lectores exactos de `jobs`, cachés o marcadores quedan tras el corte? Dar llamada, dato y consecuencia, no una sospecha genérica.
- ¿El proyector progresa sin CDC con los datos y configuración efectivos? ¿Podemos evitar una tarea nueva o una extracción grande?
- ¿Puede reutilizarse la extracción escolar sin mantener un segundo corpus? ¿Qué exige literalmente el contrato de retirada?
- ¿Qué pruebas de paridad/corte ya existentes se pueden reutilizar? ¿Cuáles se repiten sin razón?
- ¿Alguna propuesta cambia la funcionalidad o impone una garantía inexistente, como exactamente-una-entrega SMTP?
- ¿Qué resta realmente de A18? Distinguir fallo vigente, arreglo ya entregado, evidencia operativa pendiente y deuda fuera del punto.
- ¿Es defendible el subtotal 16–31 horas bajo sus supuestos? ¿Qué partidas sobran y qué código activo sin sustituto falta añadir? Dar un rango alternativo desglosado, no una fecha intuitiva.

### Comprobaciones iniciales reproducibles, sólo lectura

```sh
git -C /home/lothar/Public/SwissJob status --short
git -C /home/lothar/Public/SwissJob log -35 --oneline
git -C /home/lothar/Public/ReactPortfolio/backend log -5 --oneline
rg -n 'get_all_providers|_PROVIDER_CLASSES|_SCRAPER_CLASSES' \
  /home/lothar/Public/SwissJob/backend/providers/__init__.py \
  /home/lothar/Public/SwissJob/backend/scrapers/__init__.py \
  /home/lothar/Public/ReactPortfolio/backend/services/job_provider.py
rg -n 'collect_normalized_jobs|_local_table_is_authoritative' \
  /home/lothar/Public/ReactPortfolio/backend/services/saved_search_runner.py \
  /home/lothar/Public/ReactPortfolio/backend/services/daily_match_report.py
rg -n '_after_batch|_replay_after_batch|_drain_embeddings' \
  /home/lothar/Public/SwissJob/jobhunt_core/shadow/projector.py
```

La revisión no exige volver a ejecutar suites completas para leer este plan. Si se necesita una reproducción, usar entorno aislado y las pruebas dirigidas existentes; suites en serie. No abrir holdouts, enviar correos/aplicaciones reales, copiar datos privados, editar configuración productiva o retirar fuentes como parte de una auditoría documental.

### Formato esperado del dictamen externo

Para cada pendiente R1–R7: **NECESARIO / YA RESUELTO / SIMPLIFICABLE / FUERA DE ALCANCE / EVIDENCIA INSUFICIENTE**, con fichero:línea o recibo, consecuencia, solución mínima y criterio verificable de cierre. Para un defecto nuevo: reproducción, severidad e impacto en el DoD; sin preferencias de estilo como bloqueos.

Terminar con:

1. Lista finita corregida de trabajo restante.
2. Camino crítico y acciones que pueden solaparse sin competir por BD/autoridad.
3. Estimación de horas efectivas, esperas y condiciones que invalidan el rango.
4. Aprobación o cambios al **plan de cierre**, no un GO del proyecto basado sólo en este documento.

## 12. Conclusión y punto exacto de reanudación

El primer traspaso no está pendiente de hacerse otra vez: WorkingNomads y las diez búsquedas ya pasaron a core. Lo pendiente principal está en completar el inventario activo de ambos proyectos, cerrar sus lectores/avisos, transferir las fuentes restantes y demostrar autonomía antes de retirar procesos.

La siguiente acción al reanudar debe ser el **censo acotado de R2/R3 y lectores Portfolio**, con lista cerrada y estimación corregida, no otro ciclo de auditoría global. Mientras tanto, el trabajo funcional permanece pausado. Este documento no constituye un despliegue, un cierre del punto 4 ni una garantía de ausencia de errores.
