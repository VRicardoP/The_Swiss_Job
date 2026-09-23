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

**El 2026-09-22 la cosecha pasó a ser NATIVA**: las 16 fuentes que alimentan el
corpus las cosecha el core en cuatro ventanas diarias y los productores legacy
están retirados por lista de arranque (punto 4, sustancialmente completo).

**El 2026-09-22/23 se trabajó el rendimiento (punto 5), y SIGUE ABIERTO.** Lo
desplegado hoy (`point5-9d6b46e` en los **cinco** servicios) es grande: la
pantalla principal pasó de **79,3 s a p50 2,147 s** y la primera carga de ~54 s
a 10,196 s. Pero **ninguna lectura habitual baja del p95 de 2 s** (catálogo
2,420 s, feed 20 3,040 s, pantalla principal 2,554 s) y la primera carga de
3.000 dobla su presupuesto de 5 s.

**Dos revalidaciones externas corrigieron cierres declarados sin evidencia
suficiente.** Si retomas esto, cuenta con que lo que aquí se afirma puede estar
igual de equivocado: comprueba antes de creer.

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
| **Estado global del proyecto** | `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md` §44, §45 y **§46** (la foto vigente) |
| **Deuda viva** | `/home/lothar/Public/DEUDA_TECNICA.md` |
| **Convenciones y arquitectura** | `CLAUDE.md` — se carga solo en cada sesión |

## Lo que queda abierto

1. **Retirar el slot `jobhunt_shadow_r5_rehearsal`.** Única acción sin vuelta
   atrás barata: después, reanudar el legacy exigiría un snapshot CDC nuevo.
   Pide 48 h de dispatcher limpio (no antes del 23-09 ~22:40 UTC). El código
   está hecho y **ya desplegado**: `CORE_CAPTURE_ENABLED` existe en el proceso
   vivo del worker desde el 23-09 (antes no, y el procedimiento avisaba).
2. **Punto 5**: desplegado y medido, **contrato incumplido**. Matriz por
   escenario en §9-bis del acta; lo que falta, en §10. En corto: ~1,4 s de
   trabajo del BFF por petición sobre 1.800 items; la primera carga, que sigue
   recorriendo el feed en la cara del usuario en vez de calentarse en segundo
   plano; la cola de las rutas de 20, sin atribuir; y cuatro escenarios
   (≥100 muestras en copia, escrituras, frontend y traducción) que siguen
   **expresamente pendientes** por falta de copia autorizada.
3. **Aceptación final** del punto 4 y, separados, el cron de retención y el GO
   de calidad — que sigue en NO-GO por ausencia de un
   examen válido, no por una métrica mala. No los mezcles con este cierre.

## Diez trampas que ya costaron caro

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
7. **Antes de optimizar, mira qué pide el CLIENTE.** La pantalla principal pide
   `limit=3000` (`MatchPage.jsx:40`), no una página de 20. Se optimizó durante
   un día el tamaño de página equivocado.
8. **No atribuyas al entorno lo que no has separado.** Aquí llegó a estar
   escrito que el p95 residual «lo domina el host». Era una hipótesis: ni el
   loadavg ni el mínimo observado separan trabajo de espera. Al trazar una
   petición completa por fases, el cuello dominante resultó **nuestro**.
9. **Un `If-None-Match` no ahorra cómputo si el ETag se deriva del payload.**
   El core reconstruye la página igual para contestar 304. Para ahorrar
   cómputo hace falta un validador que no dependa del cómputo: por eso existe
   `GET /v1/profiles/{id}/matches/version`. **Vía muerta**, no reintentar.
10. **La detección de idioma NO puede vivir en el camino de respuesta.** Costaba
   50,1 ms por oferta servida, ~90 s por petición. Se deriva una vez por título
   y se persiste (`job_title_languages`). Una caché en proceso NO basta: cada
   arranque la vacía.

## Cómo se trabaja aquí

- **Verifica ejecutando, no leyendo.** Vale para la documentación, para los
  comentarios y para los mensajes de commit, incluidos los míos.
- **Reproducción roja antes de cada fix**, verde después.
- Suites **en serie**, nunca dos `pytest` a la vez, nunca contra producción.
  Core: `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm
  core-migrate python -m pytest jobhunt_core/tests` (1.765 passed, ~16 min).
  BFF: `docker compose exec -T backend python -m pytest tests/ -q` (2.562 passed, 4 xfailed, ~6 min).
- En el NAS, **solo lectura por defecto**; cada escritura con copia `.before` y
  recibo. Nunca `compose down`, `--remove-orphans`, `celery purge` ni un `up`
  global.
- **Nada de avisos, candidaturas ni ofertas fabricadas** para dar por verde una
  prueba. Un resultado vacío correcto se registra como tal.
- Commits solo con aprobación explícita; nunca `push`.
- Si algo no cuadra con lo que dicen estos documentos, **gana lo que observas**:
  para, anótalo y dilo.
