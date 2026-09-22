# Prompt de contexto — SwissJobHunter tras el traspaso y el cierre de rendimiento

Pégalo como primer mensaje a cualquier agente que retome el proyecto. Da la
perspectiva de lo hecho y, sobre todo, dice **dónde está cada cosa** para que no
tenga que reconstruirla ni preguntar.

---

Retomas **SwissJobHunter** (`/home/lothar/Public/SwissJob`, rama
`feat/fase-a-core`): agregador de empleo para Suiza con un backend legacy (BFF
FastAPI + Celery) y un core propio (`jobhunt_core/`) al que se ha ido migrando
capacidad por capacidad.

## Dónde está el proyecto

**El 2026-09-22 pasaron dos cosas.** Por la mañana la cosecha pasó a ser
NATIVA: las 16 fuentes que alimentan el corpus las cosecha el core en cuatro
ventanas diarias y los productores legacy están retirados por lista de arranque
(punto 4, sustancialmente completo). Por la tarde se optimizó el feed —de **18 peticiones internas a 1** y de
**9,3-12,9 s a 0,56-1,19 s** en el método, con contrato idéntico verificado—,
pero el **punto 5 sigue ABIERTO**: el endpoint servido da p50 1,924 s y no
cumple el p95 declarado. Una revalidación externa corrigió un cierre que se
había declarado sin evidencia suficiente.

Antes de creerte nada de lo anterior, **compruébalo ejecutando** (regla de oro
del proyecto: un documento puede afirmar por escrito una garantía que el código
no da):

```sh
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
# release desplegada (la imagen del core no trae curl)
ssh nas "$D exec swissjob-core-api-r5 python -c \"import urllib.request,json;print(json.load(urllib.request.urlopen('http://localhost:8000/v1/ready')))\""
# los scopes nativos y su última cosecha completa
ssh nas "$D exec -i swissjob-postgres psql -U jobhunt_core -d swissjobhunter_r5_rehearsal -Ac \"SET search_path=jobhunt; SELECT s.name, st.last_complete_at, st.consecutive_failures FROM harvest_scopes hs JOIN sources s ON s.id=hs.source_id LEFT JOIN source_scope_state st ON st.scope_id=hs.id WHERE hs.enabled ORDER BY 2 DESC;\""
# salud de cosecha: alertas vacías es lo sano
ssh nas "$D logs swissjob-core-worker-r5 --since 2h | grep check_health | tail -1"
```

## Qué leer, según lo que necesites

| Necesitas | Documento |
|---|---|
| **Qué se hizo, con evidencia** | `docs/audits/POINT4_CUTOVER_2026-09-22.md` — el acta: versiones, las 16 fuentes, canario servido, los cuatro defectos destapados, la decisión revertida, procesos retirados y lo que queda abierto |
| **Por qué se hizo así** | `docs/ANALISIS_PENDIENTES_PUNTO4_2026-09-21.md` — análisis del estado real contra el informe externo, con el censo de fuentes medido en producción y el plan paso a paso |
| **Qué NO tocar y por qué** | `docs/COTAS_Y_DECISIONES.md` §N (cotas del traspaso) — siete límites aceptados con la medición detrás de cada uno |
| **Lo único que queda del punto 4** | `docs/RETIRADA_SLOT_CDC_PUNTO4.md` — retirada del slot CDC: precondiciones, orden reversible hasta el último paso y qué no se borra con él |
| **Cómo se opera el NAS de verdad** | `docs/DEPLOY_NAS.md` (aviso de cabecera) y la memoria `qnap_container_station.md` |
| **Rendimiento: qué se midió y qué se corrigió** | `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md`, con los presupuestos sellados antes de medir en `docs/PREDECLARACION_PUNTO5_2026-09-22.md` |
| **Estado global del proyecto** | `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md` §44 y §45 |
| **Deuda viva** | `/home/lothar/Public/DEUDA_TECNICA.md` |
| **Convenciones y arquitectura** | `CLAUDE.md` — se carga solo en cada sesión |

## Lo que queda abierto

1. **Retirar el slot `jobhunt_shadow_r5_rehearsal`.** Única acción sin vuelta
   atrás barata: después, reanudar el legacy exigiría un snapshot CDC nuevo.
   Pide 48 h de dispatcher limpio (no antes del 23-09 ~22:40 UTC). El código ya
   está hecho y probado (`CORE_CAPTURE_ENABLED`); el procedimiento, escrito.
2. **Punto 5**: optimización desplegada, aceptación de rendimiento pendiente
   (§10 del acta: frío, escrituras en copia, frontend, muestra ≥100, separar la
   cola de latencia y decidir el presupuesto).
3. **Aceptación final** del punto 4 y, separados, el cron de retención y el GO
   de calidad — que sigue en NO-GO por ausencia de un
   examen válido, no por una métrica mala. No los mezcles con este cierre.

## Siete trampas que ya costaron caro

1. **`jobhunt.shadow.project` NO es una tarea de sombra.** Pese al nombre, es el
   postprocesado del ciclo nativo: drena embeddings y reevalúa perfiles.
   Apagar la familia `shadow.*` por prefijo dejaría el matching muerto con todos
   los indicadores en verde.
2. **El worker legacy sigue vivo a propósito.** `zebis` y `publicjobs` alimentan
   la alerta de profesor de primaria desde la base pública; los ocho
   `swiss_schools_*` son el colector escolar. No son un olvido.
3. **Antes de culpar a un portal, compara tu patrón de petición con el del
   productor que se retira.** Los cuatro bloqueos que frenaban el traspaso —429
   de NAV, 403 de Jobgether, ReadTimeout de irishjobs, 4% de feed perdido— eran
   nuestros. Un adaptador que pasa sus tests puede pedir cosas que el original
   nunca pidió: la paridad también es de ritmo, tope de páginas y cabeceras.
4. **Un decomiso de Groq falla MUDO.** `qwen3.6-27b` desapareció del catálogo y
   la traducción de títulos y el Stage 3 llevaban tiempo caídos sin alarma.
   Comprueba `GET /openai/v1/models` antes de dar por bueno un modelo.
5. **Una cosecha «parcial» permanente destruye la señal de salud.** Si declaras
   un presupuesto de páginas, agotarlo cuenta como cosecha completa; si no,
   `last_complete_at` no avanza nunca y `harvest.check_health` grita todos los
   días hasta que nadie lo mire.
6. **El feed no se recorre entero para servir una página.** `MatchesPageDTO.total`
   es aditivo: si falta, el consumidor vuelve al recorrido completo a propósito,
   porque sin ese dato el recorrido ES lo que produce el número. Ver el invariante
   en `CLAUDE.md`.
7. **Antes de optimizar, comprueba de quién es el tiempo.** El p95 que queda lo
   domina un host con loadavg 5,65-7,38 sobre dos núcleos donde ningún contenedor
   del proyecto pasa del 0,4 % de CPU. Optimizar más código no lo arreglaría.

## Cómo se trabaja aquí

- **Verifica ejecutando, no leyendo.** Vale para la documentación, para los
  comentarios y para los mensajes de commit, incluidos los míos.
- **Reproducción roja antes de cada fix**, verde después.
- Suites **en serie**, nunca dos `pytest` a la vez, nunca contra producción.
  Core: `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm
  core-migrate python -m pytest jobhunt_core/tests` (1.749 passed, ~18 min).
  BFF: `docker compose exec -T backend python -m pytest tests/ -q` (2.511 passed, 4 xfailed, ~7 min).
- En el NAS, **solo lectura por defecto**; cada escritura con copia `.before` y
  recibo. Nunca `compose down`, `--remove-orphans`, `celery purge` ni un `up`
  global.
- **Nada de avisos, candidaturas ni ofertas fabricadas** para dar por verde una
  prueba. Un resultado vacío correcto se registra como tal.
- Commits solo con aprobación explícita; nunca `push`.
- Si algo no cuadra con lo que dicen estos documentos, **gana lo que observas**:
  para, anótalo y dilo.
