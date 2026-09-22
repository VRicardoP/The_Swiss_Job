# Informe de la jornada — SwissJobHunter, 21–22 de septiembre de 2026

Prompt para pegar a quien deba conocer lo hecho: propietario, revisor externo o
un agente que retome el proyecto. Dice qué se hizo, qué se rompió por el camino,
qué queda y dónde verificarlo **ejecutando**, no leyendo.

---

En **SwissJobHunter** (`/home/lothar/Public/SwissJob`, rama `feat/fase-a-core`)
se cerraron dos hitos en una jornada: el **punto 4** (traspaso de productores y
retirada del motor legacy) y el **punto 5** (rendimiento del sistema servido).

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

## 3. Punto 5 — rendimiento del feed servido

| Recorrido | Antes p50 | Después p50 | p95 | Peticiones internas |
|---|---:|---:|---:|---|
| Feed, perfil 1 | 9,33 s | **0,648 s** | 2,131 s | 18 → **1** |
| Feed, perfil 2 | 12,87 s | **0,558 s** | **1,413 s** | 18 → **1** |
| Guardados | — | **0,120 s** | **0,320 s** | — |
| Catálogo por texto | — | **0,147 s** | **0,219 s** | — |
| `/api/v1/jobs/search` (HTTP) | — | **0,699 s** | máx. 1,783 s | — |

**La causa no estaba donde parecía.** El consumidor recorría el feed entero —18
peticiones, 1.800 items— no por las exclusiones locales, que en producción no se
aplican porque el feedback vive en el core, sino porque necesitaba el `total` y
`MatchesPageDTO` no lo traía. Una página del core cuesta **29 ms**: el recorrido
era la petición entera. La señal que lo delató: **pedir 100 costaba lo mismo que
pedir 20**.

Dos cambios, medidos por separado, con un control intermedio que confirmó la
atribución (core nuevo + BFF antiguo = **sin cambio**):

1. El core informa del total en su primera página, contado en una pasada con el
   feedback por lotes que ya existía (0,4-0,9 s frente a 1,1-9,3 s de la forma
   correlacionada, **mismo número**).
2. Migración `core0051`: índice parcial de **280 kB** sobre el 15,4 % de filas
   que pueden pertenecer a un feed.

**Contrato preservado**, verificado sobre datos reales: 8 de 8 casos con los
mismos IDs, orden, scores y total —incluida la última página—, y el camino
escolar conservando sus 7 y 13 ofertas con identidad propia.

## 4. Lo que queda por encima del presupuesto no es del código

El p95 residual (1,4-2,6 s frente a un presupuesto de 2 s) lo domina el host:
**loadavg 5,65-7,38 sobre dos núcleos** sin carga de prueba propia, y ningún
contenedor del proyecto pasa del **0,4 % de CPU**. El trabajo propio son ~0,4 s,
que es exactamente el **mínimo observado**.

Tres opciones, sin preferencia impuesta: aceptarlo (la mediana está en
0,12-1,51 s, holgado para pocos usuarios), poner límites de CPU/memoria a los
contenedores —hoy los seis del proyecto corren **sin ninguno**— o revisar qué más
corre en el NAS, diagnóstico que excede este encargo.

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

Versión certificada: core `point5-cf260b1` / `core0051` / `authoritative`, BFF y
worker `point5-b7df2a9`. Suites: **core 1.749 passed**, **BFF 2.511 passed +
4 xfailed**. 0 reinicios, outbox y CDC a cero, `alertas: []`.

## 7. Qué queda abierto

1. **Retirar el slot `jobhunt_shadow_r5_rehearsal`** — única acción sin vuelta
   atrás barata; pide 48 h de dispatcher limpio (no antes del 23-09 ~22:40 UTC).
   Código hecho y probado (`CORE_CAPTURE_ENABLED`), procedimiento escrito en
   `docs/RETIRADA_SLOT_CDC_PUNTO4.md`.
2. **Aceptación final** del proyecto y **cron de retención**, separados.
3. **GO de calidad del ranking**: sigue en NO-GO **por ausencia de un examen
   válido**, no por una métrica mala. No se tocó ni se debe mezclar con esto.
4. **A18-01/02/03/04/06**: abiertos. Sólo se cerró **A18-05**, con su medición.

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

**17 commits** en SwissJob y **3** en Public. Sin push, como corresponde.
