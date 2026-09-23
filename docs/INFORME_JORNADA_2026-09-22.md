# Informe de jornada — SwissJobHunter, 21–23 de septiembre de 2026

> El fichero conserva la fecha `2026-09-22` de su nombre **a propósito**: la
> revalidación externa `docs/audits/REVALIDACION_INFORME_DESPLIEGUE_2026-09-22.md`
> lo cita por ruta y línea, y renombrarlo dejaría esas citas colgando. El
> contenido sí llega al **23-09** (§6-bis).

Prompt para pegar a quien deba conocer lo hecho: propietario, revisor externo o
un agente que retome el proyecto. Dice qué se hizo, qué se rompió por el camino,
qué queda y dónde verificarlo **ejecutando**, no leyendo.

---

En **SwissJobHunter** (`/home/lothar/Public/SwissJob`, rama `feat/fase-a-core`)
se cerró el **punto 4** (traspaso de productores y retirada del motor legacy) y
se avanzó el **punto 5** (rendimiento), que **sigue abierto**: la optimización
está desplegada y verificada, pero la aceptación de rendimiento no. Una
revalidación externa corrigió un cierre que yo había declarado sin evidencia
suficiente; el detalle está en §3 y §4.

## 1. Punto 4 — la cosecha es nativa

Las **16 fuentes** que alimentan el corpus las cosecha ahora el core, en cuatro
ventanas diarias. Verificado, no supuesto:

- **17 scopes** habilitados, todos con cosecha completa y **0 fallos**.
- **Replay 16/16 `skipped`**: ninguna segunda descarga.
- **45 vacantes nativas servidas** por `CoreCatalog` a través del BFF desplegado,
  0 fallos, sólo lectura.
- **Ciclo autónomo**: a las 04:10 UTC el beat despachó los 17 scopes por sí solo,
  con el worker R5 y su disparador ya parados; los 17 completaron en 7 minutos.
- **Primera entrega natural de avisos acreditada de extremo a extremo**: evento
  07:15:45 → `run_due` 6 búsquedas y 1.135 coincidencias → notificación 07:19:17
  → ACK 07:19:18. Nada fabricado para provocarla.
- Corpus: **45.857 vacantes vivas** frente a 40.406 al empezar.

Se portaron desde cero `irishjobs` (193 ofertas/7 d) y `financejobs` (144), que
llevaban **sin llegar al corpus desde el 1 de septiembre** — un hueco que nadie
había detectado. Paridad pública sobre descarga real: **25/25 sin diferencias** y
**10/10**.

Retirados: disparador R5, worker R5 (exit 0, CDC a cero), frontend R5 huérfano y
una copia temporal caducada (**2,6 GB**). Recuperación acreditada con una ida y
vuelta completa sobre una fuente, sin pérdida de datos.

### Lo que más valor tiene de este punto

**Los cuatro bloqueos que frenaban el traspaso eran nuestros, no de los
portales** — y un informe externo los daba por «dependencias externas»:

| Síntoma | Causa real |
|---|---|
| NAV devolvía 429 | el adaptador pedía 100 páginas donde su productor pide 3 |
| Jobgether devolvía 403 | omitía las cabeceras que su propio código documenta como obligatorias |
| irishjobs daba ReadTimeout | pedía una 9.ª página a un portal que se ralentiza (1,3 s una suelta, 105 s ocho seguidas) |
| Jobgether perdía el 4 % del feed | el patrón del slug rechazaba el punto de `next.js`, `.net`, `psy.d` |

## 2. Un error propio, corregido por evidencia

Decidí cerrar las encarnaciones legacy durante el corte para que el archivado las
retirase en 3 días en lugar de 120. Se aplicó a **28.767 filas**. El primer
barrido nativo lo refutó: con ventana de admisión de 7 días el productor nativo
**no readmite** lo que el portal sigue listando pero publicó antes, así que
cerrar restaba cobertura viva en vez de sólo evitar duplicados.

Se revirtieron **las 28.767 por id exacto** desde el propio recibo, dentro de la
ventana de gracia. El operador lleva escrito el motivo para que no se repita.

## 3. Punto 5 — optimización desplegada, punto ABIERTO

El recorrido del feed está corregido y verificado:

| Medida | Antes | Después |
|---|---:|---:|
| Peticiones internas para servir 20 ofertas | 18 | **1** |
| Método `CoreMatching.results`, p50 | 9,33–12,87 s | **0,558–1,186 s** |
| Equivalencia (ids, orden, scores, total) | — | **8/8 idénticos** |

La causa no estaba donde parecía: el consumidor recorría el feed entero no por
las exclusiones locales —que en producción no se aplican— sino porque necesitaba
el `total` y el DTO del core no lo traía. Una página del core cuesta 29 ms. La
señal que lo delató: **pedir 100 costaba lo mismo que pedir 20**.

**Pero el punto NO está cerrado**, y mi primera acta decía lo contrario. Medido
después sobre el **endpoint servido**, que es lo que ve el usuario —cifras
**del 22-09**, superadas por las de §6-bis:

| Endpoint | p50 | p95 | mín |
|---|---:|---:|---:|
| `/api/v1/match/results?translate=false` | 1,924 s | 5,357 s | 1,177 s |
| `/api/v1/match/results` (con traducción) | 2,586 s | 4,063 s | 2,024 s |
| `/api/v1/jobs/search` | 1,156 s | 2,838 s | 0,513 s |

Ninguno cumple el **p95 ≤ 2 s** que fija mi propia predeclaración.

## 4. Cuatro errores míos que encontró una revalidación externa

Los cuatro son correctos y están corregidos en el acta:

1. **Declaré cerrado un contrato que no se cumple**, apoyándome en el p50 cuando
   el criterio sellado era el p95. Eso es cambiar el criterio después de ver el
   resultado.
2. **Medí el método intermedio, no el endpoint servido.** El router añade
   autenticación, overlay escolar, serialización y traducción con un LLM. Su
   desglose: `results` 0,603 s + overlay escolar 0,208 s + refresh 0,011 s ≈
   0,82 s, de 1,924 s. **~1,1 s siguen sin atribuir.**
3. **Atribuí al host toda la latencia residual sin demostrarlo.** El mínimo del
   endpoint, 1,177 s, es trabajo propio. Un loadavg alto no separa CPU de I/O,
   locks o consultas. Ahora figura como hipótesis pendiente de comprobar.
4. **La tabla de versiones era falsa.** Declaraba los tres procesos del core en
   `point5-cf260b1` cuando worker y captura seguían en `point4-51be757`, porque
   sólo recreé `core-api`. (Resuelto el 23-09: los cinco servicios en
   `point5-9d6b46e`.) El despliegue selectivo es defendible —la migración es
   aditiva y el cambio lo sirve la API— pero había que decirlo, no ocultarlo.
   **Consecuencia práctica: el worker vivo no contiene `CORE_CAPTURE_ENABLED`**;
   verificarlo antes de apoyarse en ese interruptor para retirar el slot.

Además, la sonda de aceptación leía la clave `jobs` cuando la respuesta trae
`data`: habría aprobado un 200 indebidamente vacío. Le puse cinco controles
negativos… y **una segunda revalidación demostró que también mentían**: tres
usaban la clave `results` y fallaban en la primera guarda, antes de llegar a la
que decían probar. Ahora cada rechazo lleva un **motivo nombrado** y cada
control exige ESE motivo, así que borrar una guarda rompe su propio control.
Prueba de mutación: **12 guardas, 12 mutantes detectados, 0 supervivientes**.

## 5. Dos fallos de método propios, dichos en voz alta

- Escribí una regresión **inútil**: afirmaba que el plan del recuento no usara
  `Seq Scan` y pasaba igual sin el fix, porque con una tabla de pruebas diminuta
  PostgreSQL prefiere correctamente el barrido secuencial. Un test que no falla
  contra el estado anterior no es una regresión: se descartó y se sustituyó por
  una afirmación del esquema.
- **Lancé la suite del BFF en paralelo** con la del core, violando la regla de
  suites en serie que este proyecto tiene por norma. Se detuvo de inmediato y se
  repitió después.

## 6. Estado verificable

```sh
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
ssh nas "$D exec swissjob-core-api-r5 python -c \"import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/ready')))\""
ssh nas "$D exec -i swissjob-postgres psql -U jobhunt_core -d swissjobhunter_r5_rehearsal -Ac \"SET search_path=jobhunt; SELECT s.name, st.last_complete_at, st.consecutive_failures FROM harvest_scopes hs JOIN sources s ON s.id=hs.source_id LEFT JOIN source_scope_state st ON st.scope_id=hs.id WHERE hs.enabled ORDER BY 2 DESC;\""
ssh nas "$D logs swissjob-core-worker-r5 --since 2h | grep check_health | tail -1"
```

Versiones REALES (2026-09-23): los **cinco** servicios en `point5-9d6b46e`
—`core-api`, `core-worker`, `core-capture`, `backend`, `worker`—, sin
divergencia entre lo declarado y lo que corre. `core0051` en el core y
`d3a7c1f60b84` en el BFF. Suites: **core 1.765 passed**, **BFF 2.562 passed +
4 xfailed**. 0 reinicios, 0 OOM, 0 respuestas 5xx, outbox y CDC a cero,
`alertas: []`, 17/17 scopes sin fallos.

## 6-bis. Tercer día (23-09): el cuello real, y el contrato que sigue sin cumplirse

Una segunda revalidación externa tumbó dos afirmaciones mías más. Las dos eran
correctas y las dos tenían la misma forma: **dar por bueno un arreglo sin
comprobar que muerde**.

1. **Los cinco controles negativos de la sonda mentían.** Tres usaban la clave
   `results` cuando la respuesta trae `data`, así que fallaban en la primera
   guarda, mucho antes de la que decían probar. Desactivando las guardas de
   cardinalidad y total, la autocomprobación seguía verde. Hoy **cada rechazo
   lleva un motivo nombrado y cada control exige ESE motivo**, y una prueba de
   mutación lo verifica: `scripts/mutation_test_probe.py`, **12 guardas, 12
   mutantes detectados, 0 supervivientes** (la primera pasada dejó 4).
2. **Reparar el transporte del idioma no habría bastado**, y además lo que yo
   creía la causa era falso. El campo se perdía en **tres** capas (`VacancyDTO`,
   `_vacancy_dtos`, `_job_view`), no sólo en los normalizadores nativos. Y
   medido: sólo el **3,1 %** del feed trae el dato. El escritor legacy tampoco
   lo rellenaba — `legacy:arbeitnow` está al 1,5 %. **El corpus nunca tuvo
   cobertura de idioma.**

### Los dos cuellos, atacados donde estaban

**El idioma sale del camino de respuesta.** Se deriva una vez por título y se
persiste (`job_title_languages`); servir sólo lee. Tres estados explícitos:
ausente / encolado / **resuelto-como-desconocido** — sin el tercero, un título
indecidible sería trabajo repetido para siempre. En producción: 1.547 títulos,
0 pendientes, 0 desconocidos.

**El recorrido del feed se cachea por VERSIÓN.** Trazado por fases en el NAS:
recorrer el feed era el **87-89 %** del coste (47,5 s frío, 11,5 s caliente, 18
páginas). El hallazgo que no era obvio: **la caché por ETag no ahorraba
cómputo**, porque el ETag se deriva del payload y el core construye la página
igual para contestar 304. Ahora el core publica
`GET /v1/profiles/{id}/matches/version` y el consumidor reutiliza su recorrido
sólo si coincide exactamente. **El frontend no cambia una línea.**

### La matriz de aceptación, y su veredicto

| Escenario | Presupuesto | Medido | |
|---|---|---|---|
| Readiness / lectura ligera | p95 ≤ 1 s | 0,323 s / 0,039 s | **cumple** |
| Catálogo 20 | p95 ≤ 2 s | p95 2,420 s | no |
| Feed 20 | p95 ≤ 2 s | p95 3,040 s | no |
| Pantalla principal (3.000) | p95 ≤ 2 s | p50 **2,147**, p95 2,554 s | no |
| Primera carga — catálogo / feed 20 | ≤ 5 s | 2,920 / 1,002 s | **cumple** |
| Primera carga — 3.000 | ≤ 5 s | **10,196 s** | no |
| ≥100 en copia · escrituras · frontend · traducción | — | — | **pendientes** |

La pantalla principal pasó de **79,265 s a 2,147 s** de mediana, 37 veces. **Y
el contrato sigue sin cumplirse**: una mejora grande no convierte un
incumplimiento en cumplimiento. Detalle y qué falta, en §9-bis y §10 del acta.

## 6-ter. Tarde del 23-09: auditoría, regresión retirada y el panel del Portfolio

**Auditoría profunda** (`docs/audits/AUDITORIA_PROYECTO_2026-09-23.md`): seis
revisiones de área más verificación ejecutando lo que ellas no podían tocar.
7 críticos, 14 altos, 26 medios, 6 bajos; Parte I dice qué falta para terminar
y Parte IV cómo hacerlo, paquete a paquete, con prueba roja→verde. Dos que no
esperan: un slot de replicación huérfano con **40 GB de WAL retenido** (decisión
del propietario) y una **regresión desplegada esa misma mañana**: la caché del
feed por versión servía feedback rancio con `CORE_FEEDBACK_ENABLED=True`.

**T2, retirada el mismo día** (`17e2b9e`): el digest cubre ahora estado de
usuario y listing primario; las escrituras invalidan; el recorrido se
reverifica. Un tropiezo con lección: la cláusula de canónica puesta en `feed()`
bajó dos nDCG del gate a 0,0 — se dejó sólo en recuento y versión.

**Panel de ofertas.** El propietario veía títulos sin traducir y ninguna
descripción. Medido: el 28,5 % del feed no tiene descripción en ninguna fuente;
el 5,7 % lleva HTML crudo (`arbeitnow` nativo); la traducción era un botón con
7 títulos en caché. Y la ventana era la del **Portfolio**, cuyo backend tragaba
la causa de un fallo transitorio del feed. Implementado allí: `job_enrichments`
+ bucle de fondo acotado + decoración de sólo lectura; 38/38 resúmenes y 35
títulos resueltos en < 5 min en producción. Dos tropiezos: un id de Alembic
reutilizado y una imagen que no arrancó por `working_dir: /release` (~1 min sin
servicio, restaurada). **El frontend del Portfolio sigue sin publicar** (sin
push).

## 7. Qué queda abierto

1. **Retirar el slot `jobhunt_shadow_r5_rehearsal`** — única acción sin vuelta
   atrás barata; pide 48 h de dispatcher limpio (no antes del 23-09 ~22:40 UTC).
   Código hecho, probado **y ya desplegado**: `CORE_CAPTURE_ENABLED` existe en
   el proceso vivo del worker desde el 23-09. Procedimiento en
   `docs/RETIRADA_SLOT_CDC_PUNTO4.md`.
2. **Aceptación final** del proyecto y **cron de retención**, separados.
3. **GO de calidad del ranking**: sigue en NO-GO **por ausencia de un examen
   válido**, no por una métrica mala. No se tocó ni se debe mezclar con esto.
4. **A18-05 sigue ABIERTO**: el recorrido está corregido y desplegado, la
   aceptación de rendimiento no. Ninguna lectura habitual baja del p95 de 2 s.
   Lo que falta, en §10 del acta. A18-01/02/03/04/06, también abiertos y sin
   tocar.

## 8. Dónde está cada cosa

| Necesitas | Documento |
|---|---|
| El traspaso, con evidencia | `docs/audits/POINT4_CUTOVER_2026-09-22.md` |
| Por qué se planificó así | `docs/ANALISIS_PENDIENTES_PUNTO4_2026-09-21.md` |
| El rendimiento, con evidencia | `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md` |
| Presupuestos sellados antes de medir | `docs/PREDECLARACION_PUNTO5_2026-09-22.md` |
| Qué NO tocar y por qué | `docs/COTAS_Y_DECISIONES.md` (cotas del traspaso y del punto 5) |
| Lo único que queda del punto 4 | `docs/RETIRADA_SLOT_CDC_PUNTO4.md` |
| Contexto para retomar | `docs/CONTEXTO_TRAS_TRASPASO_2026-09-22.md` |
| Estado y deuda | `ESTADO_Y_HOJA_DE_RUTA.md` §44 y §45 · `DEUDA_TECNICA.md` |
| Convenciones e invariantes | `CLAUDE.md` |

**25 commits** en SwissJob desde el inicio de la jornada, más los de Public y los tres del Portfolio. Sin push, como corresponde.
