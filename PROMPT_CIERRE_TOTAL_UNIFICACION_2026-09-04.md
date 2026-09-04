# PROMPT — cierre total y verificable de la unificación SwissJob + ReactPortfolio

> Fecha de partida: 2026-09-04. Este encargo sustituye los prompts parciales de matching como
> guía operativa. Conserva los contratos y runbooks existentes; no autoriza a reescribir la
> historia ni a relajar gates después de observar resultados.

## Rol y resultado exigido

Actúa como responsable senior de migración, datos y operación. Lleva el proyecto desde el estado
real descrito abajo hasta una unificación productiva sólida:

1. ReactPortfolio y SwissJob usan `jobhunt_core` como núcleo autoritativo para todas las
   capacidades incluidas en el plan.
2. No quedan dos escritores para el mismo estado ni dos motores de matching/notificaciones para
   el mismo perfil.
3. El ranker promovido supera los criterios predeclarados con evidencia independiente; si todavía
   no los supera, el baseline productivo continúa funcionando y se itera exclusivamente sobre
   desarrollo, sin contaminar el holdout.
4. Colegios y documentos quedan migrados en Fase E.
5. El harvest y las responsabilidades legacy quedan portados o retirados en Fase F, tras backup,
   restauración probada, retención y observación.
6. El cierre final incluye evidencia ejecutable, estado de producción y deuda residual honesta.

No confundas tres hitos distintos:

- **Fase C ya consumada:** ReactPortfolio ya consume el core.
- **GO de calidad:** autoriza una política de matching nueva y la racha certificada.
- **Unificación total:** Fase D + E + F, incluida la retirada del motor legacy.

## Repositorios y documentos fuente

- Core y legacy SwissJob: `/home/lothar/Public/SwissJob`, rama `feat/fase-a-core`.
- ReactPortfolio backend: `/home/lothar/Public/ReactPortfolio/backend`.
- ReactPortfolio frontend: `/home/lothar/Public/ReactPortfolio/frontend`.
- Documentación transversal: `/home/lothar/Public/`.
- Estado: `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md`.
- Plan: `/home/lothar/Public/PLAN_UNIFICACION_JOBHUNTING.md`.
- Backlog: `/home/lothar/Public/BACKLOG_UNIFICACION_JOBHUNTING.md`.
- Deuda: `/home/lothar/Public/DEUDA_TECNICA.md`.
- Decisiones: `SwissJob/docs/COTAS_Y_DECISIONES.md`.
- Operación core: `SwissJob/docs/DEPLOY_NAS.md`,
  `SwissJob/jobhunt_core/shadow/{RUNBOOK,DEPLOY_NAS}.md`.
- Cutover del piloto: `/home/lothar/Public/RUNBOOK_CUTOVER_PILOTO.md`.
- Datos de SwissJob: `/home/lothar/Public/MANIFIESTO_DATOS_SWISSJOB.md`.

Si dos documentos discrepan, manda primero el estado observado en producción, después el contrato
ratificado y finalmente la entrada documental más reciente. Corrige la divergencia en la misma
sesión; no dejes dos “fuentes de verdad”.

## Fotografía de partida que debes verificar, no presuponer

- HEAD SwissJob comunicado: `809e24a`.
- `809e24a` añade tres regresiones que pasan focalmente: sello de generación, worker G1 que termina
  después de G2 y desactivación del modelo durante inferencia.
- Suite completa declarada: 1036/1036. Reejecútala con el override de desarrollo; el compose base
  usa una imagen inmutable anterior y puede probar código viejo.
- ReactPortfolio producción: catálogo/matching en `core_read` y applications/saved_searches en
  `core_primary`; Fase C consumada.
- Producción continúa segura con `cosine-baseline:v1`; no la retires durante el desarrollo.
- Holdout de matching virgen; último desarrollo formal RankNet: P1=0.5097, P2=0.5063, umbral
  inmutable 0.60/0.60.
- Entrenamiento v10 con unos 436 juicios y benchmark OpenVINO en el NAS fueron comunicados como
  procesos en curso. Localízalos, identifica PID/contenedor, release, logs, artefactos parciales y
  ETA. No los dupliques ni reinicies si están sanos.
- Hay cambios del propietario sin commit en los repos. Inventaría `git status` en los tres y no
  sobrescribas, limpies, incluyas ni reformatees cambios ajenos.

## Reglas que evitan otra cadena de regresiones

1. **Reproducción antes del fix.** Todo defecto nuevo empieza por un test o ensayo determinista que
   falle en el padre y reproduzca exactamente el interleaving o dato real.
2. **Una causa raíz, una frontera.** Corrige en la fuente compartida del invariante. No copies guards
   en callers ni crees autoridades paralelas.
3. **YAGNI arquitectónico.** Solución mínima completa; responsabilidad única, cohesión alta,
   acoplamiento bajo, legibilidad y consistencia. Sin dependencias, flags o capas especulativas.
4. **Matriz adversarial obligatoria.** Después de cada fix prueba: antes/durante/después del commit,
   éxito/fallo/cancelación, primer run/reintento, alta/desactivación/reactivación y dos workers con
   orden invertido.
5. **Mordida real.** La regresión debe fallar contra el padre por el defecto afirmado y pasar en
   HEAD. Un test que falla por fixture, timeout o mock equivocado no vale.
6. **Suites en serie.** Nunca ejecutes en paralelo suites que compartan PostgreSQL. Usa:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml \
     run --rm core-migrate python -m pytest jobhunt_core/tests -q
   ```

   Ejecuta además las suites backend/frontend propias de cada repo según sus README.
7. **Release reproducible.** Construye desde árbol limpio, hornea `RELEASE_SHA`, compara digest
   local/NAS y exige `/v1/health` y `/v1/ready` autoritativos. Nunca etiquetes `:prod` desde un árbol
   sucio ni montes código mutable en producción.
8. **Datos primero.** Antes de una mutación productiva: objetivo exacto, backup, restore probado o
   ensayo sobre copia, manifiesto, checksums y rollback. No borres por scope amplio si existe
   procedencia por PK.
9. **Un escritor.** Cualquier flip debe demostrar quién escribe antes, durante y después. No basta
   con que las lecturas funcionen.
10. **Métricas inmutables.** No cambies umbrales, fórmula nDCG, rúbrica ni conjunto después de ver
    un resultado. No uses el holdout para entrenar, seleccionar features o explicar errores.
11. **Políticas/modelos append-only.** Una receta, artefacto, backend o peso distinto implica nueva
    versión y nueva fila. Bootstrap crea catálogo; una única autoridad cambia activación.
12. **Despliegue monotónico.** Canary pequeño, observación, ampliación; rollback específico por
    capacidad. No hagas un big-bang de D/E/F.
13. **Documentación en el mismo commit.** Cada cambio de estado actualiza estado, deuda, runbook y
    decisión afectada. Las cifras siempre llevan fecha, entorno y consulta/comando.
14. **Nada de NO-GO difuso.** Si algo bloquea, nombra condición, evidencia, responsable, acción
    exacta y duración mínima. Mantén el servicio útil con el baseline mientras se resuelve.

## Fase 0 — inventario y preservación

Antes de tocar código:

1. Captura SHA, rama, `git status` y remotos de los tres repos.
2. Comprueba procesos locales y del NAS; distingue entrenamiento, benchmark y servicios
   productivos.
3. Registra release y head Alembic de API, worker, capture y migrate.
4. Consulta políticas/modelos activos, política realmente referenciada por `current_eval_id`,
   tamaño del feed, routing por consumer/perfil/capacidad, `jobhunt_profile_map`, outbox, CDC,
   schedulers y estado de la cohorte/racha.
5. Verifica las cuatro capacidades de ReactPortfolio desde fuera y dentro del contenedor.
6. Guarda este inventario como anexo fechado del estado; no copies cifras antiguas.

## Fase 1 — cerrar correctamente la publicación concurrente

`809e24a` cierra las reproducciones originales, pero quedan dos propiedades por demostrar.

### 1A. Generación protegida hasta el commit

Defecto a reproducir: A entra en fase 3, bloquea el perfil y lee G1; se pausa justo después;
B modifica `offer_embeddings` o una transición de elegibilidad, el trigger confirma G2; A continúa
y publica candidatos de G1. Un `SELECT` ordinario de `corpus_generation` no impide ese orden.

Corrección mínima esperada:

- La lectura final de generación debe mantener un lock compatible de lectura sobre la fila única
  hasta el commit (`FOR SHARE`, o un mecanismo existente estrictamente equivalente).
- Todos los triggers/caminos que cambian elegibilidad deben incrementar esa misma fila mediante
  `UPDATE`, de modo que se serialicen con la publicación.
- Audita el orden completo de locks: perfil → generación → modelo/política → estado. Demuestra que
  no hay un camino inverso capaz de producir deadlock.
- Test con barrera colocada **después de leer la generación final y antes de escribir**: B debe
  esperar o A debe descartarse; nunca puede quedar corpus G2 + feed G1 sin señal pendiente.

### 1B. Modelo canónico exacto, no solo activo

Defecto a reproducir: A infiere con modelo Z; un modelo A anterior en el orden, ya embebido pero
inactivo, se activa mientras A está fuera de transacción; Z sigue activo y por ello la comprobación
booleana deja a A publicar aunque ya no sea el modelo canónico.

Corrección mínima esperada:

- Define en una sola función la selección efectiva del modelo canónico que hoy implementa la
  tarea: activo, dimensión compatible, revisión vigente embebida y corpus elegible, con el orden
  productivo determinista.
- La tarea y la valla final deben reutilizar esa definición y comparar el `model_id` exacto.
- La activación/desactivación debe tener una única autoridad transaccional, análoga a
  `declare_active_policies`; un bootstrap no puede cambiar canonicidad.
- La valla de publicación y esa autoridad deben compartir un lock/protocolo que cubra también alta
  o reactivación de otra fila, no solamente `FOR SHARE` sobre el modelo antiguo.
- Añade regresiones para desactivar el actual, activar uno anterior, insertar/activar concurrente,
  modelo sin embeddings y segundo intento. El feed previo debe permanecer byte-equivalente ante
  descarte.

### 1C. Cierre de Fase 1

- Ejecuta mordidas contra `809e24a` o el padre correcto.
- Ejecuta tests focales y suite completa 1036+ en serie.
- Revisa que ningún callback `on_evaluated` registre un intento descartado.
- No promociones todavía: este cierre protege la escritura, no acredita calidad.

## Fase 2 — cerrar matching hasta un examen legítimo

1. Espera los procesos v10/OpenVINO ya iniciados sin bloquear otras verificaciones seguras.
2. Para v10 valida: dataset/juicios/perfiles sellados, split por grupos, semilla, receta, pérdida,
   número de juicios, huellas de entrada/salida y reproducibilidad.
3. Evalúa desarrollo con `shadow.metrics._dcg`, universo sellado, cobertura 100 % del top-10 y
   ambos perfiles. Publica resultados aunque sean rojos.
4. Candidato a examen solo si P1 y P2 alcanzan **cada uno** 0.60 y no empeoran respecto del punto
   anterior bajo el protocolo predeclarado.
5. Si falla, continúa únicamente con desarrollo:
   - clasifica FN/orden incorrecto por causa verificable;
   - selecciona una sola hipótesis con señal suficiente;
   - crea nueva política/modelo versionado;
   - obtiene etiquetas ciegas independientes adicionales;
   - añade el siguiente punto de la curva y repite.
   No abras ni consultes el holdout y no bajes el umbral.
6. Benchmark OpenVINO sobre el hardware real del NAS: misma entrada y artefacto, paridad de orden y
   scores frente al backend de referencia, tiempo frío/caliente, RSS pico, batch, cancelación,
   reinicio y caché. El backend pasa a formar parte de la receta versionada.
7. Si desarrollo y hardware pasan, congela release, receta, modelo, código, universo y juicios.
   Ejecuta el holdout **una sola vez**. Si suspende, no lo conviertas en desarrollo: crea en una
   futura campaña un holdout independiente antes de otra promoción.
8. Si pasa, promoción transaccional por la autoridad única, rematerialización completa, verificación
   de punteros y rollback probado a `cosine-baseline:v1`.

## Fase 3 — racha GATE-SOMBRA

La racha son siete ventanas diarias independientes: **mínimo inevitable 7 × 24 h = 168 h** desde
el primer cierre elegible posterior al freeze. No comprimas ciclos ni muevas sus límites.

1. Congela código, receta, modelo, política y cohortes durante la racha.
2. Antes del primer ciclo confirma: labels_ready, dedup precision/recall, nDCG/falsos negativos,
   pérdida, latencia, outbox lag/dead, CDC/slot y release homogénea.
3. Observa cada ciclo con consulta y artefacto sellado. Un rojo reinicia la racha según el contrato;
   diagnostica la causa concreta antes de cambiar nada.
4. Cambiar código o etiquetas invalida el freeze y reinicia el conteo. Una incidencia externa que
   el contrato marque inelegible no se maquilla como verde.
5. Al 7/7 emite acta de GO del gate, con siete IDs, ventanas, métricas, cohortes, release y hashes.

## Fase 4 — Fase D: SwissJob como segundo consumer del core

No uses las cifras históricas como inventario final; vuelve a contarlas justo antes del corte. El
manifiesto del 23 de agosto orientaba: 2 perfiles, 18 feedbacks, 10 búsquedas, 7 filtros, 0
applications y 2 documentos.

1. Completa/actualiza un runbook específico de Fase D reutilizando el del piloto y documentando la
   diferencia esencial: el harvest legacy y CDC continúan durante D; solo se apagan matching,
   alertas y notificaciones por cada perfil migrado.
2. Preflight: backup y restore, migraciones, credenciales mínimas, `profiles:read/write`, routing,
   maps, outbox, estado de schedulers y rollback por capacidad.
3. Provisiona/mapea los perfiles mediante el mecanismo legítimo; no escribas IDs manualmente si
   existe enrollment. Push del perfil/CV con ETag e idempotencia.
4. Ensaya sobre copia y luego migra en freeze breve:
   - feedback → estado/eventos estables;
   - saved searches + filtros → tuplas canónicas;
   - applications que existan al corte → migración §4;
   - scores legacy → no migrar, recomputar;
   - documentos → conservar local hasta E;
   - notificaciones → aplicar la decisión ratificada y conservar el archivo requerido.
5. Reconciliación independiente por conteos, valores, identidades y checksums; manifiesto de
   procedencia y rollback probado.
6. Canary monotónico:
   - catálogo global `core_read`;
   - matching `core_read` para un perfil, comprobar feed y desactivar su motor/alertas legacy;
   - segundo perfil;
   - escrituras durables a `core_primary` solo tras confirmar un único escritor;
   - ampliar únicamente después de la ventana observada definida en el runbook.
7. Verifica rutas interactivas, tareas periódicas, digest, SSE, UI y botón de análisis. Ninguna vía
   debe leer resultados legacy congelados ni disparar doble evaluación.
8. DoD D: todos los perfiles destinados al core, routing sin `rollback_pending`, durables
   reconciliados, outbox sana, rollback ensayado y cero motor duplicado por perfil.

## Fase 5 — Fase E: colegios y documentos

1. Recuenta tablas y escritores reales; define autoridad por cada durable.
2. Añade solo el esquema/API imprescindible del core para documentos, colegios, ofertas y
   candidaturas escolares, siguiendo los contratos existentes de idempotencia, ownership y outbox.
3. Porta la generación WeasyPrint sin cambiar la salida visible; compara fixtures/goldens.
4. Migra datos con manifest, checksums y rollback. Mantén local hasta que cada vertical pase su
   canary; después cambia routing y apaga el escritor local correspondiente.
5. Verifica permisos, descarga, nombres/content-type, PII, retención y eliminación GDPR.
6. DoD E: ninguna capacidad de documentos/colegios depende ya de tablas o servicios legacy.

## Fase 6 — Fase F: retirada del motor legacy

1. Inventaría todas las fuentes y clasifícalas: portada nativa en core, sustituida, retirada por
   decisión o todavía legacy. No apagues una fuente sin paridad de corpus y salud durante su
   ventana de observación.
2. Porta fuente por fuente: raw primero, identidad estable, cursor por scope, error distinto de
   vacío, compliance, health y pruebas contractuales. Canary y apagado del scheduler viejo por
   fuente.
3. Drena CDC y outboxes solo cuando ningún dato autoritativo dependa de ellos. Demuestra lag cero y
   capacidad de replay antes de retirar el slot.
4. Comprueba que no queden routing `local`/`shadow`/`rollback_pending`, writers legacy, tareas beat,
   consumers de cola, endpoints o UI que dependan de tablas antiguas.
5. Ejecuta backup final y restauración completa en entorno desechable. Conserva el legacy en modo
   lectura durante la retención ratificada; no inventes su duración si falta decisión del
   propietario.
6. Tras N ciclos de operación estable definidos en el runbook, retira procesos, redes, secretos,
   tablas y código legacy mediante expand/contract. Cada borrado debe tener inventario y copia
   recuperable.

## Tiempos inevitables y estimación

- Entrenamiento v10 comunicado: alrededor de 4 h totales; usa su ETA real restante.
- Benchmark NAS: varias horas según conversión y medición; no aceptar extrapolación desde otro CPU.
- Holdout + promoción, si procede: 0.5–1 día.
- Racha: **168 h mínimas**, más el tiempo hasta el primer cierre diario elegible.
- Fase D, con código ya construido y pocos durables: 1–3 días de preparación/cutover después del
  gate, salvo divergencias reales.
- Fase E: estima tras el inventario; referencia inicial 3–7 días, no compromiso.
- Fase F: depende del número real de fuentes aún legacy y de la retención; referencia inicial
  1–3 semanas de portado y observación, no compromiso.

Camino feliz hasta SwissJob sobre el core: aproximadamente **9–12 días naturales** desde que una
política pasa desarrollo. La unificación total con retirada del legacy será posterior y no debe
declararse terminada antes de E/F.

## Documentación obligatoria durante la ejecución

Actualiza, sin borrar el historial:

1. `ESTADO_Y_HOJA_DE_RUTA.md`: nueva sección fechada que prevalezca sobre §20, con producción,
   release, routing, gates, procesos en curso y siguiente acción.
2. `DEUDA_TECNICA.md`: cabecera vigente que marque entradas antiguas como cerradas/superadas y
   liste solo deuda actual con prioridad, coste, condición y responsable.
3. `PLAN_UNIFICACION_JOBHUNTING.md`: estados C/D/E/F y gates reales.
4. `MANIFIESTO_DATOS_SWISSJOB.md`: conteos del instante de corte y decisión final por tabla.
5. Runbook de Fase D; créalo solo si no existe uno ejecutable. No dupliques comandos ya correctos.
6. Runbooks de NAS: SHA, imagen, migración, backup/restore, canary y rollback realmente usados.
7. `COTAS_Y_DECISIONES.md`: solo decisiones aceptadas y límites vigentes; elimina como “abiertos”
   los asuntos ya cerrados, conservando su historia.
8. Un acta final con hashes, tests, migraciones, manifests, siete ciclos, comprobaciones de ambos
   clientes y deuda residual.

## Deuda que debes reclasificar al empezar

- **Bloqueante inmediata:** atomicidad generación→commit y canonicidad exacta del modelo.
- **Condicionada por evidencia:** calidad v10 y backend OpenVINO.
- **Operativa inevitable:** siete ciclos diarios.
- **Fase D:** cutover de SwissJob y migración/recomputación de sus durables.
- **Fase E:** documentos y colegios.
- **Fase F:** fuentes legacy, CDC, health/compliance y retirada.
- **No bloqueantes conocidas:** claves opcionales de sourcing, mejoras de cobertura sin API y
  optimizaciones que EXPLAIN/medición no justifiquen.
- **Cotas aceptadas:** no las reabras como hallazgos salvo evidencia nueva que contradiga su
  premisa documentada.

## Entregables y veredicto final

Entrega commits pequeños por frontera, sin mezclar cambios ajenos, y un informe final con:

- SHA/digest desplegados en cada servicio y repos limpios respecto del alcance.
- Suites completas y mordidas.
- Métricas de desarrollo, holdout y siete ciclos con artefactos sellados.
- Routing efectivo de ambos consumers por perfil/capacidad.
- Conteos/checksums/manifiestos y pruebas de rollback/restore.
- Inventario de writers/schedulers: exactamente uno o cero según capacidad.
- Estado de D, E y F.
- Deuda residual no bloqueante, con trigger explícito para abordarla.

Solo declara **APROBADO SÓLIDO — UNIFICACIÓN COMPLETA** cuando D, E y F estén cerradas y el legacy
pueda retirarse sin pérdida ni doble escritor. Si solo termina matching/racha, declara
**GO DE CALIDAD**; si solo termina D, declara **SWISSJOB SOBRE CORE**. No uses “GO” sin apellido.

Trabaja de forma persistente hasta ese resultado. Detente únicamente ante una contraseña/decisión
de propietario imprescindible o una condición externa demostrada; en ese caso deja el servicio
funcionando, un comando siguiente exacto y evidencia suficiente para reanudar sin repetir análisis.
