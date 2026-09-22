# Encargo externo — cerrar el punto 5: rendimiento y optimización

Fecha: 22-09-2026. Proyecto SwissJobHunter + integración con ReactPortfolio.
Repositorio principal: `/home/lothar/Public/SwissJob`, rama `feat/fase-a-core`.

## 1. Misión y definición de terminado

Medir el rendimiento del sistema realmente servido, localizar sus costes dominantes y corregir los incumplimientos con cambios mínimos. Verificar en el hardware de destino que la solución conserva resultados, aislamiento, recuperación y tareas de fondo. Dejar evidencia reproducible y documentación actualizada.

**No confundir «optimizado» con «reescrito» ni con «sin ningún bug posible».** Un recorrido que cumple el presupuesto y no presenta un coste material demostrado no necesita modificaciones. El cierre exige un presupuesto fijado antes de comparar variantes, evidencia representativa y ausencia de regresiones conocidas bloqueantes en los recorridos afectados.

El contrato de partida está en `DEUDA_TECNICA.md`, A18-05: medición NAS del recorrido completo, concurrencia, reinicio y backlog, sin variar resultados ni aislamiento. Los canarios E.12–E.15 no certifican por sí solos esa carga global.

Entregar un veredicto **PUNTO 5 CERRADO** o **PUNTO 5 ABIERTO**, no un GO de calidad del ranking ni un cierre automático del proyecto completo. No declarar cierre productivo si el fix sólo está probado localmente o si una autorización pendiente impide verificarlo desplegado.

## 2. Foto verificada por quien redacta este encargo

### Verificación operativa de sólo lectura, 22-09-2026 08:29 UTC

Se ejecutaron las tres comprobaciones solicitadas, no sólo se leyó el acta:

| Sonda | Resultado |
|---|---|
| `GET /v1/ready`, desde `swissjob-core-api-r5` | `status=ready`, `alembic=core0050`, `release=51be7576b3d749eb302c4aae1bab699749e3ffaf`, `authoritative=true`. |
| Scopes habilitados, con LEFT JOIN a su estado | 17 filas, correspondientes a 16 fuentes; todas con `last_complete_at` del 22-09 entre 04:10:13 y 04:14:20 UTC; todas con `consecutive_failures=0`. NAV tiene dos scopes. |
| Último `jobhunt.harvest.check_health` | 08:14:11 UTC: `alertas=[]`, `scopes=17`; censo total 46, habilitados 17, con estado 17. |

No hubo cambios NAS, carga artificial, cosecha forzada ni avisos de prueba. Estas sondas corroboran el estado actual de cosecha; **no demuestran 48 horas continuas de salud ni rendimiento bajo carga**.

### Una fotografía adicional de recursos, 08:30 UTC

- Host: loadavg `4.89 / 4.11 / 4.13`.
- `MemTotal=8035456 kB`, `MemAvailable=3486296 kB`; swap total `24009972 kB`, libre `21168852 kB`.
- `docker stats --no-stream`: worker core ~1,034 GiB; PostgreSQL ~840,5 MiB; Portfolio ~665,3 MiB; BFF SwissJob ~264,3 MiB; worker público ~247,2 MiB; API core ~86 MiB. CPU instantánea de esos contenedores entre 0,02% y 0,38%.
- La memoria operativa identifica el NAS como Celeron J1800, dos núcleos y ~7,8 GB RAM. Revalidar límites y servicios al medir.

**Interpretación limitada:** carga elevada no demuestra saturación de CPU causada por SwissJob; puede incluir otros procesos y esperas de disco. Swap ocupada no demuestra intercambio activo. Medir tendencias de CPU, iowait, paginación, colas y latencia antes de modificar recursos. No parar otros servicios del NAS para fabricar un benchmark favorable.

### Identidad local y discrepancias documentales

- HEAD local al preparar este documento: `ac81a59`; la release productiva comprobada es `51be757`. Hay código posterior, incluido el flag de retirada CDC `281cf54`, que no debe darse por desplegado sólo por estar en HEAD.
- ESTADO §44 aún enumera como pendiente el primer aviso; el acta `POINT4_CUTOVER_2026-09-22.md` §7ter acredita la entrega natural posterior. Priorizar evidencia fechada, no reabrir esa migración.
- El procedimiento del slot habla de 48 h y de cuatro rondas; cuatro ventanas diarias son aproximadamente ocho rondas en 48 h, según límites exactos. Un conteo de líneas `alertas=[]` tampoco prueba ausencia de líneas rojas. Antes de ejecutar **ese procedimiento separado**, conciliar ventanas completas, timestamps y todas las alertas. No retirarlo por cuatro éxitos aislados.

Estas diferencias se registran, no se corrigen mediante operaciones productivas dentro de este encargo preparatorio. Este documento no afirma que se haya certificado la primera entrega mediante una sonda nueva ni que el slot se haya retirado.

## 3. Lecturas obligatorias y límites que no se renegocian

Leer al comenzar:

1. `docs/audits/POINT4_CUTOVER_2026-09-22.md`: corte, recuperación, ciclo autónomo y entrega natural.
2. `docs/ANALISIS_PENDIENTES_PUNTO4_2026-09-21.md`: decisiones D1–D5 y explicación de lectores/consumers. Sus listas de tareas previas no prevalecen sobre el acta posterior.
3. `docs/COTAS_Y_DECISIONES.md`, especialmente §N y las cotas del componente a tocar.
4. `docs/RETIRADA_SLOT_CDC_PUNTO4.md`: residual separado, irreversible sin snapshot nuevo.
5. Cabecera vigente de `docs/DEPLOY_NAS.md`; memoria `/home/lothar/.claude/projects/-home-lothar-Public/memory/nas-qnap-docker-operativa.md` como orientación, verificando en ejecución lo que importe.
6. `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md` §44 y `/home/lothar/Public/DEUDA_TECNICA.md`, especialmente A18-05 y contratos de las rutas medidas.
7. Instrucciones AGENTS/skills aplicables. Mantener YAGNI, responsabilidad única, cohesión, bajo acoplamiento y consistencia.

**No tocar para mejorar una cifra:**

- `jobhunt.shadow.project`: pese al nombre, sostiene embeddings y matching nativos. No apagar familias de tareas por prefijo.
- Worker legacy público: sigue siendo necesario para CV/documentos, alerta de profesor y colegios. Portfolio conserva su cosecha para su tablero/digest por decisión acordada. No reintroducir el antiguo plan de portarlo todo.
- Histórico legacy coexistente, ventana de admisión de siete días, gracia/retención, cotas de NAV/IrishJobs, identidad CH Media y exclusiones de §N.
- Calidad/ranker/holdout, umbrales y política canónica. El NO-GO es por ausencia de examen válido, no por una cifra reutilizable.
- Fuentes nuevas o deliberadamente excluidas; reparación masiva del `canton` histórico; reactivación de Jobicy.
- Contenido o semántica de los avisos, acciones de usuario, filtros, totals o aislamiento para reducir coste.
- Cron de retención y retirada del slot: entregables separados. Su observación puede solaparse con este trabajo, pero no se acorta por obtener mejores tiempos.

NAS sólo lectura por defecto. Cualquier despliegue, reinicio, migración, configuración, copia de datos o escritura de ensayo en él requiere autorización para el alcance exacto, configuración `.before` y recibo. No asumir que este documento concede permisos nuevos. Commits sólo con aprobación; nunca `push`.

## 4. Paso 0 — fijar el entorno y la evidencia antes de medir

1. Registrar HEAD y árbol sucio de cada repo que participe. No incluir ni revertir cambios ajenos. Registrar por servicio release, digest, esquema, routing y flags relevantes, **no volcar el entorno completo**: contiene credenciales.
2. Repetir readiness, scopes y salud. Si una garantía contradice lo observado, detener el camino afectado, anotar la discrepancia y comunicarla; no optimizar encima de una pérdida de funcionalidad no entendida.
3. Confirmar topología efectiva: core en `swissjobhunter_r5_rehearsal`, esquema `jobhunt`; BFF SwissJob en su base; Portfolio en `proyecto`. El nombre `rehearsal` NO hace desechable esa base: es el core vivo.
4. Inventariar CPU/memoria/límites, discos, versión PostgreSQL/extensiones, conexiones/pools, colas, concurrencia y horarios reales. Medir a baja frecuencia durante una franja tranquila y durante una ventana natural; no sólo `stats` instantáneo.
5. Registrar volúmenes agregados: vacantes presentables, revisiones, embeddings, candidatos, tamaño real de feed por perfil, documentos, candidaturas, colegios, observaciones de búsquedas y outbox. No sacar CV, hashes de autenticación, títulos personales ni tokens a informes.
6. Identificar URLs realmente servidas por ambos frontends/proxy y rutas BFF. Un localhost rápido sólo mide una capa. Separar LAN y acceso remoto/Tailscale; no mezclar sus distribuciones.
7. Definir la versión de referencia: binario/config productivos. No comparar un HEAD distinto contra producción atribuyendo todos los cambios a una optimización.

Guardar `BASELINE_ENTORNO` con timestamps, versiones y límites. La carga del resto del NAS forma parte del entorno; anotar sus variaciones. No silenciar tareas para «estabilizarlo» sin declarar el escenario alternativo.

### Sondas reproducibles de sólo lectura

```sh
ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes nas 'sh -s' <<'REMOTE'
set -eu
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
date -u '+%Y-%m-%dT%H:%M:%SZ'
$D exec swissjob-core-api-r5 python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/v1/ready", timeout=10).read().decode())'
$D exec -i swissjob-postgres psql -X -v ON_ERROR_STOP=1 -U jobhunt_core -d swissjobhunter_r5_rehearsal -A <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL search_path=jobhunt;
SELECT s.name, hs.id, st.last_complete_at, st.consecutive_failures
FROM harvest_scopes hs JOIN sources s ON s.id=hs.source_id
LEFT JOIN source_scope_state st ON st.scope_id=hs.id
WHERE hs.enabled ORDER BY s.name, hs.id;
COMMIT;
SQL
$D logs swissjob-core-worker-r5 --since 24h 2>&1 | grep 'harvest.check_health' | tail -1
REMOTE
```

La ausencia de resultado del filtro de logs es evidencia ausente, no salud verde. Revisar todas las líneas y periodos si se pretende certificar una ventana, no sólo la última. El warning conocido de lectura de `.docker/config.json` no invalida automáticamente una operación cuya salida y código se han comprobado.

## 5. Paso 1 — predeclarar un presupuesto finito

No se ha encontrado en lo inspeccionado una tabla completa de SLO vigente para todos los recorridos. **No inventar que los números siguientes son contratos ya aprobados.** Son una propuesta inicial para acordar y sellar antes de evaluar variantes. Si existe un contrato más específico ratificado, citarlo y usarlo. Resolver las decisiones de presupuesto juntas, no por cada prueba.

### Propuesta para este portfolio de pocos usuarios, no una plataforma masiva

| Recorrido / escenario | Presupuesto candidato, sujeto a ratificación previa |
|---|---|
| Lecturas autenticadas habituales en LAN: catálogo/feed/colegios/biblioteca/candidaturas/búsquedas, caché caliente, 1 y 2 sesiones concurrentes | p95 extremo a extremo ≤2 s, sin respuestas inesperadas ni pérdida de resultados. |
| Readiness/lectura ligera | p95 ≤1 s; error/indisponibilidad nunca disfrazados de vacío. |
| Primera lectura tras arranque controlado en copia | ≤5 s una vez listo el servicio; arranque/modelo se mide por separado. |
| Escrituras locales/core sin LLM ni SMTP, en copia | p95 ≤2 s y persistencia/idempotencia correctas. |
| Frontend, navegación representativa | Contenido útil visible ≤3 s en LAN bajo dispositivo/red declarados; no sólo un spinner rápido. |
| Fondo | No crece la edad del trabajo pendiente de forma sostenida a la tasa real; el siguiente ciclo puede progresar, sin hambre de notificaciones ni del feed. |

Para generación documental, inferencia y recuperación de backlog, fijar presupuestos específicos a partir del contrato existente, límites de proveedor y tamaño real. Separar encolado, espera, cómputo y disponibilidad del resultado. **No aplicar 2 s a un PDF+LLM ni aprobarlo midiendo sólo su ACK**. No aumentar timeouts para convertir un incumplimiento en aprobado.

Si el propietario no ratifica un SLO faltante, se puede entregar diagnóstico y comparación antes/después, pero no «cumple contrato» ni cierre incondicional. Un presupuesto no cambia después de ver qué variante gana; cualquier revisión se explica y exige nueva medición completa bajo el criterio nuevo.

### Plan de muestras y protección

- Carga representativa de pocos usuarios: concurrencia 1 y 2; escenario de 4 en copia para margen. No aumentar concurrencia en producción como prueba de estrés.
- En copia: al menos 100 observaciones por recorrido prioritario y escenario para p95 empírico; publicar n, errores, máximo y dispersión. Un p95 de 12 solicitudes es canario, no capacidad certificada. No presentar p99 robusto con muestra insuficiente.
- Excluir warmup sólo si se declaró antes; informar warmup/frío por separado. «Caché fría» debe especificar capa: app, conexión, PostgreSQL u OS. No vaciar Redis ni page cache del NAS vivo.
- Producción: sólo canario acotado de lectura, preferiblemente 10–20 solicitudes por ruta, secuencial con pausa y autorización de carga. El percentil de esa muestra no reemplaza el ensayo representativo en copia.
- En una copia NAS autorizada, carga acotada con límites explícitos; si no puede coexistir sin riesgo con otros servicios, acordar ventana o usar medición pasiva. Un benchmark en un PC potente no certifica el J1800.
- Detener la carga de prueba ante 5xx inesperado, timeout, OOM/reinicio, bloqueo de usuarios o degradación sostenida frente a la referencia. Predeclarar límites de seguridad de memoria, colas y tiempo; no improvisarlos en pleno ensayo. Guardar evidencia y retirar sólo la carga propia, no matar tareas productivas.
- No generar búsquedas/ofertas/avisos falsos en producción. Fixtures sintéticos sólo aislados y claramente identificados; nunca mezclarlos con corpus, juicios ni métricas reales.

## 6. Paso 2 — matriz de recorridos que deben medirse

Medir **tiempo servido, cantidad de peticiones internas, filas/bytes transferidos, SQL, CPU/memoria y espera**. La duración HTTP sola no identifica la causa.

| Grupo | Casos mínimos | Invariantes que acompañan al tiempo |
|---|---|---|
| Catálogo | General, remoto, texto literal, filtros usados realmente, sin resultados, primera/página profunda, detalle nativo e histórico. | Mismo orden/total/filtros, alias e identidad, enlace accionable, sin truncamiento silencioso. |
| Matching y guardados | Ambos perfiles SwissJob y el consumer Portfolio; primera página, posteriores, guardados/rechazados, overlays escolares; frío/caliente. | Exclusiones, orden/score, total y estados iguales; no servir la caché de otro perfil/consumer. |
| Colegios/watchlist | Listado, detalle, ofertas por monitor, limit pequeño, paginación, monitor con oferta más allá del antiguo límite global. | No confundir error con vacío ni recortar antes de aplicar filtro. |
| Candidaturas | Listado/detalle y, en copia, alta/cambio/retry sobre vacante sólo core. | Conserva IDs, historia, idempotencia y permisos. |
| Documentos | Biblioteca, descarga existente; en copia creación de pareja CV/carta y fallo parcial. | Contenido/propietario íntegros; atomicidad; no contar token/ACK como documento completo. |
| Búsquedas/avisos | Listado, ejecución con pendientes, replay, receptor lento/fallido en copia; actividad natural en NAS. | Contadores y ya observados intactos; no perder novedades ni duplicar eventos. |
| Fondo | Cosecha programada, embeddings/matching, dedup, archivado, entrega, tareas escolares y alerta retenida. | Progreso, reanudación y salud; descargas parciales no se maquillan como completas. |
| Frontends | Navegación y acciones visibles en ambos; LAN y remoto separados, vista móvil si afectada. | La ruta usada por UI coincide con lo medido, sin nuevas peticiones en bucle ni errores ocultos. |

No se exige ejecutar todas las combinaciones cartesianas. Elegir clases equivalentes y fronteras: vacío, un elemento, varias páginas, tamaño actual y crecimiento razonable en copia. El crecimiento se deriva de la cosecha/retención observadas, no de inventar miles de usuarios.

Los endpoints GET no son automáticamente inocuos: algunos actualizan caché o disparan fetch. Trazar su camino antes de incluirlos en el canario. No probar un refresco de fuente cien veces contra su portal.

## 7. Paso 3 — medir y priorizar los costes concretos

### 7.1 Matching y overlays: primera hipótesis, no fix obligatorio

Referencias actuales:

- `backend/services/matching/core_client.py:372`: `results()` llama `_fetch_full_feed`; el corte de página llega después de resolver identidad y estados.
- `backend/services/matching/core_client.py:570`: recorre páginas core y valida cursores/DTO; ETag no evita necesariamente recorrer el conjunto.
- `backend/services/schools/presentation.py:13`: `school_job_refs` recorre `/school-jobs` para resolver un subconjunto; otros overlays enumeran colegios/estados.
- Portfolio `services/schools_core.py:133,222`: `_pages()` y `jobs()` recorren colección y catálogo; `limit` de página no equivale necesariamente a límite total del consumidor.

Instrumentar esos recorridos antes de cambiar: ¿cuántas solicitudes y filas para entregar 20 ofertas o 1 oferta escolar? ¿Qué porcentaje del tiempo es red, SQL, validación, alias y overlay? Comparar caché fría/caliente y tamaño creciente.

Si incumple: preferir consulta acotada por IDs/página y procesamiento por lote en la frontera existente. **No mover `LIMIT` antes de las exclusiones/accionabilidad si cambia orden, total o contenido**. No sustituir el feed completo por sus primeras 100 filas. Mantener APIs de barrido para los productores que sí necesitan la colección completa; distinguirlas de lectura paginada, sin romper su contrato por optimizar UI.

Si los overlays locales impiden paginar correctamente, diseñar el mínimo cambio de frontera que conserve la semántica; probarlo antes de desplegar. Una caché compartida sin clave de consumer/perfil/revisión/filtros no es una solución aceptable.

### 7.2 PostgreSQL y búsquedas

- Consultas observadas, no todas las tablas: catálogo, feed, identidad, escolares, `saved_search_observations`, `integration_outbox`, generaciones y mantenimiento.
- Usar estadísticas ya disponibles sin resets globales. Si `pg_stat_statements` no está instalado, no reiniciar PostgreSQL sólo por instrumentar: primero tiempos de aplicación y planes acotados.
- `EXPLAIN (ANALYZE, BUFFERS, ...)` **sólo en copia para consultas costosas o con efectos**. Ejecuta la consulta; incluso un SELECT puede llamar funciones. Producción: diagnóstico acotado/read-only y `statement_timeout`, sin barridos masivos.
- Comparar planes reales con parámetros representativos y el comportamiento de consultas preparadas. Un índice útil en un SQL literal puede no usarse en el plan efectivo.
- Añadir índice sólo si el plan y el coste lo justifican; medir también tamaño, escrituras/WAL y mantenimiento. No indexar cada filtro por costumbre ni forzar `enable_seqscan=off` para mostrar mejora.
- `search_execution.py` observa también no coincidencias para que editar un filtro no fabrique novedades. No eliminar ese trabajo semántico sin preservar el invariante. El coste de la primera ejecución no equivale al coste estable incremental.
- Cualquier migración: nueva revisión posterior a la cabeza real; no reescribir publicadas. Evaluar locks y tamaño de tablas; ensayar upgrade y recuperación en copia.

### 7.3 Fondo, recursos y servicios externos

- Medir espera en cola separada de ejecución. `jobhunt_core/celery_app.py` configura límites de tarea y concurrencia; no asumir más workers = más rendimiento en dos núcleos.
- Medir durante una ventana nativa real: API no debe quedar sin servicio por embeddings/dedup. Correlacionar timestamps, backlog y capacidad de drenaje. Un worker sin CPU puede estar esperando disco/red/lock.
- No cambiar el presupuesto NAV/IrishJobs, cadencias ni clasificación de completo/parcial para bajar duración. Las cotas de §N protegen cobertura y salud.
- Si hay contención real, evaluar primero lotes/cadencia/acotación existentes. Separar colas o procesos sólo con evidencia y memoria disponible; conservar locks, fencing, retry e idempotencia.
- Groq/otros proveedores: error permanente/modelo retirado debe quedar visible; un fallback rápido o respuesta vacía no acredita generación funcional. No cambiar modelo como optimización incidental ni hacer llamadas facturables sin autorización.
- No atribuir toda la carga del NAS al proyecto. Si el recurso limitante es externo al alcance, medir su contribución y proponer una decisión; no modificar otros contenedores.

## 8. Paso 4 — reproducir, corregir y comparar sin introducir regresiones

Para cada cuello de botella seleccionado:

1. Anotar contrato, evidencia base, coste dominante y cambio mínimo propuesto. Prioridad: fallo de corrección/aislamiento → bloqueo de usuario → coste dominante medido → resto.
2. Añadir reproducción roja. Para rendimiento, preferir regresión determinista de número de consultas/páginas/filas frente a un test con reloj frágil. Verificar que falla contra la base anterior, sin restaurar ficheros encima de cambios ajenos.
3. Corregir en la frontera que posee la regla. Reutilizar helpers/dependencias. Una variable por experimento; no combinar reescritura, índice, caché y cambio de modelo y atribuirles una sola mejora.
4. Comparar respuestas completas o campos canónicos estables: IDs, orden, total, exclusiones, cursores, permisos y errores. Variación de datos vivos no sirve de prueba de equivalencia: usar snapshot/fixture estable en copia.
5. Probar fallos/retry y dos sesiones competidoras si se cambian locks, cachés, colas o escrituras. Mantener inferencia/red fuera de transacciones largas y revalidación antes de publicar.
6. Medir antes/después en el mismo hardware/dataset/config y condiciones descritas. Alternar o repetir bloques para detectar deriva de carga; conservar todos los resultados, no sólo la corrida más rápida.
7. Si cumple sin el cambio o la mejora es irrelevante frente a su complejidad, descartarlo y registrar el motivo. Si no mejora, no encadenar optimizaciones especulativas.

**Herramienta reutilizable:** `scripts/nas_portfolio_read_benchmark.py` cubre lecturas acotadas dentro de Portfolio. Sus 12 muestras, concurrencia 2 y localhost son un canario, no el benchmark integral. Revisar autorización de su token efímero, no imprimirlo y adaptar únicamente lo necesario. No crear una plataforma nueva de benchmarking.

## 9. Paso 5 — ensayo representativo y recuperación aislada

Antes de ejecutar tests, demostrar que los DSN apuntan a bases de pruebas. Las suites no van contra `swissjobhunter_r5_rehearsal` productiva ni contra la base Portfolio viva. Desactivar schedulers/red externa en las copias y evitar alias Docker que puedan redirigir producción a un ensayo.

Si hace falta copia real: obtener autorización precisa, destino privado, propósito y caducidad. Restauración con errores fatales (`--exit-on-error`/transacción según procedimiento), sin `2>/dev/null`; verificar extensiones, constraints, índices, triggers y filas no validadas. Una copia sin constraints no prueba recuperación. No exportar datos personales por comodidad.

Ensayar:

- Lecturas y escrituras con tamaños no vacíos; carga simultánea representativa de dos consumers y trabajo de fondo en copia.
- Caché caducada, restart de API/worker en copia, conexiones recuperadas, y cola que conserva pendientes.
- Caída antes/después de commit y de ACK donde el fix afecta entrega; sin SMTP/HTTP real, usar receptor de prueba aislado.
- Backlog sellado y finito: medir tiempo de drenaje y edad del pendiente; no sólo número de tareas completadas. Se permite fixture sintético en copia, nunca fingir que fue una ronda productiva.
- Recuperación de versión/config antes y después de nuevas escrituras. No restaurar un dump antiguo sobre un sistema activo como rollback genérico.
- Filtros/estados concurrentes, total paginado, cursores repetidos, errores core, aislamiento y no resurrección de datos borrados si se tocan sus tablas.

Suites **en serie**, con árbol estable. Core, BFF y Portfolio sólo cuando los cambios afecten esa capa; conservar regresiones compartidas relevantes. No repetir la suite completa tras editar una frase de documentación.

Comandos existentes a verificar antes de usarlos en entorno LOCAL aislado:

```sh
# Desde SwissJob; comprobar primero compose, DSN y base de tests.
docker compose run --rm core-migrate python -m pytest jobhunt_core/tests -q
docker compose exec -T backend python -m pytest tests/ -q

# Desde cada frontend afectado, con dependencias instaladas y tests aislados.
# SwissJob tiene Vitest instalado, pero no script npm "test": no inventarlo.
# Usar su ejecutable local y los tests existentes; npm run build / npm run lint.
# Portfolio: npm run test:run; E2E ya existente sólo contra el entorno de ensayo.
```

No instalar `pytest-timeout` ni otra dependencia para copiar un comando. Validar nombres actuales de servicios y utilidades. El script no concede permisos para arrancar nuevas colas en NAS.

## 10. Paso 6 — despliegue autorizado y canario servido

Preparar una candidata con tests completos necesarios, receta de medición y recibos. **Pedir aprobación antes del commit y de las escrituras NAS** que no estén ya autorizadas para esa maniobra. Si falta permiso, dejar artefactos listos y especificar la operación exacta pendiente; no declarar cerrado producción.

1. Registrar SHA/digest/esquema/config efectivos y copia privada `.before`. Construir de árbol identificado, sin incluir secretos ni cambios ajenos.
2. Revisar diff de candidata frente a la release viva, no sólo frente al HEAD anterior. El flag CDC nuevo no debe activarse incidentalmente con una optimización.
3. Coordinar con las 48 h del punto 4: preparación/ensayos pueden correr antes; cambios que alteren la observación se registran y obligan a justificar su continuidad o reinicio. No retrasar pruebas locales por una espera que sólo afecta al slot.
4. Compose efectivo core: `/share/CACHEDEV1_DATA/Public/unification-e15-20260914/core.configured.yml`, proyecto `swissjob-r5`. Público: `swissjob.configured.yml`. Verificar los de Portfolio antes de tocarlo. CLI real `/share/Public/swissjob/bin-docker-compose` o binario confirmado, no recetas históricas de instalación completa.
5. Drenar y recrear sólo servicios afectados, con `--no-deps`; nunca `up` global, `--remove-orphans`, purga general de colas o borrado de volúmenes. Mantener el motor `shadow.project` y el worker público necesario.
6. Repetir readiness y verificar release, migración, autoridad, contador de reinicios y endpoints a través del proxy que usa el frontend.
7. Canario de lectura acotado en ambos consumers; comparar contrato y latencia. No generar ofertas, avisos o candidaturas reales para acreditar carga o recuperación. Generación facturable real sólo con permiso y necesidad explícitos.
8. Observar una franja relevante con trabajo natural: no basta probar cuando las colas están vacías. Si el cambio se ensayó durante cosecha sólo en copia, declararlo y recoger evidencia pasiva al siguiente ciclo.
9. Ante regresión, detener ampliación del despliegue, conservar evidencia y aplicar recuperación ensayada. No «arreglar» el rendimiento apagando avisos ni volviendo al segundo escritor.

La retirada del slot se ejecuta por su procedimiento, no por este plan. Punto 5 puede demostrar rendimiento mientras el slot siga pendiente; identificar en el acta la configuración certificada y la revalidación ligera necesaria si después cambia. No adjudicar cierre conjunto automáticamente.

## 11. Paso 7 — documentación, deuda y versión de evidencia

Guardar un conjunto pequeño de artefactos, usando directorio privado para datos sensibles y sólo agregados/versiones en Git:

1. `PREDECLARACION_PUNTO5_<fecha>.md`: presupuestos ratificados, escenarios, muestras, seguridad, carga, criterios de parada y exclusiones.
2. Resultados máquina-legibles antes/después: timestamps, release/config/hardware, dataset agregado, escenario, n, p50/p95/max, errores/timeouts, QPS de la prueba, peticiones internas, filas/bytes, CPU/RSS/I/O y colas. No etiquetar throughput incluyendo pausas como capacidad máxima.
3. Planes y evidencia de regresiones rojas/verdes, comandos y salida de suites; explicar qué prueba realmente cada ensayo.
4. `ACTA_CIERRE_PUNTO5_<fecha>.md`: tabla por recorrido con presupuesto, referencia, candidata, igualdad funcional, evidencia NAS y veredicto; rollback/canario y límites.

Actualizar de forma coherente ESTADO (nueva sección posterior a §44), DEUDA A18-05 y los hallazgos realmente corregidos, PLAN/BACKLOG y runbook de operación si cambia. No cerrar A18-01/02/03/04/06 por asociación: revalidar sólo los que afecten esta entrega y enlazar prueba. No copiar de nuevo cifras de calidad no certificables.

La aceptación funcional final de todo el proyecto y el cron quedan en su lista separada. Sus recorridos afectados se prueban para prevenir regresiones, pero no se incorporan funcionalidades nuevas ni se declara completado todo el proyecto por esta acta.

## 12. Checklist finito de cierre

- [ ] Entorno y release efectivos identificados; no se midió otra imagen o una copia con constraints incompletas.
- [ ] Presupuestos/escenarios predeclarados y ratificados; ninguna relajación retroactiva para aprobar.
- [ ] Recorridos prioritarios de ambos proyectos medidos con datos no vacíos, n suficiente y frío/caliente separados.
- [ ] Concurrencia representativa y coexistencia con trabajo de fondo, backlog y reinicio acreditados en entorno seguro.
- [ ] Cada optimización tiene causa medida, reproducción roja, mejora demostrada y contrato preservado.
- [ ] Sin pérdida de resultados, cambio de total/filtros, mezcla de consumers, avisos perdidos, errores ocultos ni duplicación por retry atribuibles al cambio.
- [ ] Sin crecimiento sostenido de colas/memoria en la ventana declarada ni degradación bloqueante de otra función; no se confundió capacidad de laboratorio con canario.
- [ ] Suites de las capas afectadas completas en serie, con regresiones relevantes; no contra producción.
- [ ] Si hubo cambios productivos, autorización, `.before`, recibo, versión identificada, recuperación y canario servido acreditados.
- [ ] Estado/deuda/documentación actualizados; exclusiones y límites explícitos, no bloqueos difusos.

Un fallo de presupuesto ratificado o una regresión de contrato mantiene abierto el recorrido. Una mejora opcional no impide cerrar si el presupuesto ya se cumple y su coste residual está justificado. No exigir optimización infinita ni ausencia matemática de todo defecto futuro.

## 13. Secuencia eficiente y tiempos que no deben confundirse

Orden: **identidad + presupuesto → baseline → cuello dominante → fix mínimo + regresión → comparación → ensayo → permiso/despliegue → canario + acta**.

- Acotar el diagnóstico inicial a una sesión y entregar una tabla priorizada antes de programar; si no se encuentra cuello material, no inventarlo.
- El número de fixes y el permiso de escritura no se conocen todavía: no prometer cierre total en una cifra cerrada. Estimar cada paquete después de la baseline, separando horas activas de espera.
- La suite core registrada sobre `281cf54` tardó 1314,63 s (~22 min); BFF/Portfolio y builds añaden su coste real. Agrupar correcciones compatibles en una candidata; no repetir releases por cada índice.
- Una pasada estable representativa y otra de contraste son más útiles que numerosas micropruebas sin contexto. Si la variabilidad impide concluir, informar incertidumbre y ampliar sólo esa muestra.
- Cuatro ventanas de cosecha al día: la observación de coexistencia puede esperar a la siguiente ventana mientras se completa documentación o ensayos. No son siete días obligatorios de rendimiento.
- Las 48 h y ronda adicional de captura parada pertenecen al slot/punto 4. No saltarlas ni convertirlas en una nueva racha de punto 5.
- No ejecutar entrenamientos ni abrir el holdout para este trabajo.

## 14. Forma de comunicar el resultado

Terminar con:

1. Veredicto del punto 5 y lista de recorridos aprobados/pendientes.
2. Comparativa antes/después, unidades, n, condiciones y coste reducido — o evidencia de que no hacía falta cambiar.
3. Contratos/regresiones comprobados y evidencia de despliegue, si ocurrió.
4. Deuda restante concreta, sin mezclar slot, aceptación final, cron ni calidad con el rendimiento aprobado.
5. Commits únicamente si fueron autorizados; nunca push.

Si no puedes demostrar un cierre, decir exactamente qué prueba, permiso o corrección falta. No usar «se necesita otra auditoría» como sustituto de un caso reproducible. La misión es cerrar un presupuesto de funcionamiento real, no añadir una nueva ronda indefinida de mejoras.
