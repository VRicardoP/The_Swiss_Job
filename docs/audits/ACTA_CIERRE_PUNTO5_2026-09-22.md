# Acta del punto 5 — rendimiento del sistema servido

Fecha: **2026-09-22**, 08:29 → 10:10 UTC. Presupuestos y baseline sellados antes
de comparar variantes en [`PREDECLARACION_PUNTO5_2026-09-22.md`](../PREDECLARACION_PUNTO5_2026-09-22.md).

## Veredicto

**PUNTO 5 ABIERTO.** La optimización del recorrido del feed está implementada,
desplegada y verificada; la **aceptación integral de rendimiento queda
pendiente**.

Esta acta se emitió primero como «PUNTO 5 CERRADO» y **era incorrecto**. Una
revalidación independiente
([REVALIDACION_INFORME_DESPLIEGUE_2026-09-22.md](REVALIDACION_INFORME_DESPLIEGUE_2026-09-22.md))
lo señaló y las mediciones posteriores le dieron la razón en los cuatro puntos.
Qué estaba mal, sin rodeos:

1. **Declaré cerrado un contrato que no se cumple.** La predeclaración fija
   **p95 ≤ 2 s**, no mediana; varios escenarios dan 2,1–3,0 s. Apoyarme en el p50
   era convertir el criterio en otro después de ver el resultado.
2. **Medí el método intermedio, no el endpoint servido.** `CoreMatching.results`
   da p50 0,648 s; `GET /api/v1/match/results` da **p50 1,924 s sin traducción y
   2,586 s con ella**, con un mínimo de 1,177 s. El usuario ve lo segundo.
3. **Atribuí al host toda la latencia residual sin demostrarlo.** El mínimo del
   endpoint (1,177 s) es trabajo propio, no espera de CPU ajena.
4. **La tabla de versiones era falsa**: declaraba los tres procesos del core en
   `point5-cf260b1` cuando worker y captura siguen en `point4-51be757`.

Lo que sí sostiene la evidencia se mantiene y está en §1–§4. Lo que falta para
poder cerrar, en §10.

## 1. Qué se midió y con qué

**Dos niveles distintos, que no deben mezclarse.** El método `CoreMatching.results`
es el recorrido que se optimizó; el endpoint `GET /api/v1/match/results` es lo
que ve el usuario y añade autenticación, overlay escolar, serialización y —por
defecto— traducción de títulos con un LLM externo.

### 1a. El método optimizado (n=50, conc. 1 y 2, warmup excluido)

| Recorrido | p50 | p95 |
|---|---:|---:|
| Feed, perfil 1, conc. 1 | 0,648 s | 2,131 s |
| Feed, perfil 1, conc. 2 | 1,032 s | 2,366 s |
| Feed, perfil 2, conc. 1 | 0,558 s | 1,413 s |
| Feed, perfil 2, conc. 2 | 1,186 s | 2,552 s |
| Guardados | 0,120 s | 0,320 s |
| Catálogo por texto | 0,147 s | 0,219 s |
| Catálogo, página profunda (offset 200) | 0,669 s | 1,850 s |
| Catálogo remoto | 0,416 s | 2,753 s |
| Catálogo general | 1,510 s | 2,985 s |

### 1b. El endpoint servido (n=20, sonda con aserciones que fallan)

| Endpoint | p50 | p95 | mín | ¿Cumple p95 ≤ 2 s? |
|---|---:|---:|---:|---|
| `/api/v1/jobs/search` | 1,156 s | 2,838 s | 0,513 s | **no** |
| `/api/v1/match/results?translate=false` | 1,924 s | 5,357 s | 1,177 s | **no** |
| `/api/v1/match/results` (con traducción) | 2,586 s | 4,063 s | 2,024 s | **no** |

**Ningún endpoint cumple el p95 declarado.** La traducción añade ~0,66 s de
mediana —menos de lo esperado, porque su caché Redis funciona— pero es una
llamada a un LLM externo y la predeclaración la excluye del presupuesto de 2 s:
necesita el suyo propio.

### 1c. Desglose del tiempo propio del router (n=8)

| Fase | p50 | máx |
|---|---:|---:|
| `resolve_matching` | 0,000 s | 0,018 s |
| `CoreMatching.results` | 0,603 s | 1,705 s |
| `db.refresh(profile)` | 0,011 s | 0,087 s |
| `overlay_school_results` | **0,208 s** | 0,768 s |

Suman **~0,82 s** de los 1,924 s del endpoint sin traducción. **~1,1 s quedan sin
atribuir**: pueden ser autenticación, serialización Pydantic, el servidor
(gunicorn con dos workers) o contención. **No se ha separado**, y esta acta no
afirma cuál es.

### 1d. Las sondas, endurecidas tras la revalidación

La sonda anterior leía la clave `jobs` cuando la respuesta trae `data`: habría
dado por bueno un 200 indebidamente vacío. La actual comprueba estructura,
cardinalidad, identidad, título y coherencia del total, **falla con excepción**
ante cualquier discrepancia, y arranca ejecutando **cinco controles negativos**
(200 vacío, clave equivocada, total incoherente, cardinalidad distinta y total
distinto del esperado) que deben detectarse antes de usarla como evidencia:
`{"controles_negativos": 5, "todos_detectados": true}`.

## 2. La causa dominante, y por qué era ésa

Para servir **20 ofertas**, el BFF recorría el feed **completo**: 18 peticiones
al core, 1.800 items resueltos y transformados, y sólo entonces el corte. Pedir
100 costaba lo mismo que pedir 20 — el coste nunca estuvo en lo devuelto.

El recorrido existía por una razón concreta: el BFF necesita el `total` y
`MatchesPageDTO` no lo traía. Las exclusiones locales que en su día justificaron
recorrerlo todo (accionabilidad y feedback negativo) viven en la rama de
`CORE_FEEDBACK_ENABLED = false`; en producción el flag está **activo** y esa rama
no excluye nada, así que el recorrido sólo servía para contar lo que el core
puede contar en una consulta.

Desglose medido (n=3): `_fetch_full_feed` **8,7–11,4 s de ~12 s totales**
(85–90 %); overlay y resto 0,9–2,1 s; resolución de identidad 0,01–0,61 s.
Una página del core cuesta **29 ms**: el recorrido era la petición entera.

## 3. Los dos cambios, medidos por separado

**Experimento 1 — el core informa del total y el consumidor deja de recorrer**
(`b7df2a9`). El total se calcula con el feedback efectivo **por lotes** ya
presente en `feedback.py`: la forma correlacionada costaba 1,1–9,3 s y la
agrupada 0,4–0,9 s **para el mismo número**. Sólo se emite en la primera página.
El consumidor corta al cubrir `offset + limit`, y **únicamente si el core informó
del total**: sin ese dato el recorrido ES lo que produce el número, y cortar lo
falsearía. La rama de feedback local conserva el recorrido completo.

Resultado aislado: 18 peticiones → **1**; p50 de 9,3–12,9 s a **0,81–1,72 s**.
Control previo: con el core nuevo pero el BFF antiguo **no cambia nada** (sigue
en 18 peticiones), lo que confirma que la mejora viene de ese par y no de otra cosa.

**Experimento 2 — índice parcial para el recuento** (`cf260b1`, migración
`core0051`). El `EXPLAIN ANALYZE` del COUNT mostraba 604 ms dominados por un
`Seq Scan` de `profile_vacancy_state` con `Rows Removed by Filter: 24712`. Sólo
el **15,4 %** de esa tabla puede pertenecer a un feed (5.400 filas de 35.092), así
que se indexan exactamente ésas, con la misma forma parcial que el
`ix_pvs_saved_feed_keyset` ya existente. Coste en disco: **280 kB**.

Resultado aislado: COUNT de 404–930 ms a **241–474 ms**; p50 del feed de
0,81–1,72 s a **0,56–1,19 s**, y p95 mejor en los cuatro casos (el peor, 3,797 s,
pasó a 2,366 s).

## 4. Equivalencia funcional verificada sobre datos reales

Camino corto contra recorrido completo, en producción, **8 de 8 casos idénticos**
— incluidas la primera página, una intermedia (offset 40), una de 100 y la
última (offset 1795):

| Comprobación | Resultado |
|---|---|
| Mismos IDs y mismo orden | **sí**, 8/8 |
| Mismos `score_final` | **sí**, 8/8 |
| Mismo total (1.800 / 1.799) | **sí**, 8/8 |
| Número de items servidos | idéntico, incluida la página final parcial (4 de 5 pedidos) |

Regresiones añadidas, deterministas por **número de peticiones y forma del plan**,
no por reloj —que en un NAS de dos núcleos cargado no sería fiable—:

- el consumidor deja de paginar al cubrir la ventana (cuenta de peticiones);
- una página profunda alcanzable se sigue sirviendo entera;
- **sin total del core, vuelve al recorrido completo**: perder la optimización es
  aceptable, dar un total equivocado no;
- el recuento no reintroduce una subconsulta correlacionada (ausencia de `SubPlan`);
- el índice parcial existe con su condición.

Una de las regresiones que escribí **se descartó por inútil**: afirmaba que el
plan del recuento no usara `Seq Scan`, y pasaba igual sin el fix, porque con una
tabla de pruebas diminuta PostgreSQL prefiere correctamente el barrido
secuencial. Un test que no falla contra el estado anterior no es una regresión;
se sustituyó por la afirmación del esquema, y la evidencia del plan vive aquí.

## 5. La cola de latencia: hipótesis, no conclusión

La primera versión de esta acta afirmaba que el residual **no procedía del
código**. **Esa atribución no estaba demostrada y en parte es falsa**: el mínimo
del endpoint sin traducción es 1,177 s, y eso es trabajo propio, no espera de
CPU ajena.

Lo que sí está medido, y que sigue siendo relevante:

- loadavg del host entre **5,65 y 7,38 con dos núcleos** — sobresuscripción de
  2,8× a 3,7×, sin carga de prueba propia;
- ningún contenedor del proyecto pasa del **0,4 % de CPU** en el muestreo
  instantáneo; los mayores eran `redis` 5,69 % y 4,78 %, y `portfolio_db` 4,78 %.

Lo que **no** demuestra eso: un loadavg alto y porcentajes instantáneos de CPU no
separan espera de CPU, de disco, de locks, de conexiones o de consultas, ni
descartan que el propio proyecto contribuya durante los picos.

**Hipótesis vigente**, pendiente de comprobar: la cola combina (a) trabajo propio
identificado —0,82 s de fases medidas— más (b) ~1,1 s sin atribuir y (c) espera
por contención del host. Para separarlos hace falta correlacionar una ventana de
peticiones con tiempos SQL, esperas de PostgreSQL y CPU/IO de host y contenedores.
**No se ha hecho.** No se impusieron límites de recursos ni se cambió PostgreSQL.

## 6. Ausencia de regresión, verificada tras desplegar

| Comprobación | Resultado |
|---|---|
| Reinicios de contenedor | **0** en backend, worker, core-api y core-worker |
| Salud de cosecha | `alertas: []`, 17 scopes, censo completo |
| Outbox sin entregar | **0** |
| CDC pendiente | **0** |
| Proyector | 6 ciclos en 30 min, con normalidad |
| Errores en el BFF | ninguno real: dos `profile erasure drain deferred (ConnectError)` a las 09:29:30, el diferido previsto mientras se recreaba el core-api, y una línea de arranque que contiene `--error-logfile` |
| Canario HTTP servido | `/api/v1/jobs/search` 200 con datos reales y `total` 45.859; `/api/v1/health` 200 |
| Suite del core | **1.749 passed** |
| Suite del BFF | ver §8 |

Suites **en serie**. Una de ellas se lanzó por error en paralelo con la del core
y se detuvo de inmediato, antes de que compitieran por la base.

## 7. Decisión que corresponde al propietario

Ningún endpoint cumple hoy el p95 de 2 s que fija la predeclaración. Hay dos
caminos, y la elección no es técnica:

1. **Completar la aceptación contra el presupuesto vigente**: medir lo que falta
   (§10) y corregir lo que la evidencia señale.
2. **Aprobar expresamente otro presupuesto** —por ejemplo p95 ≤ 3 s en LAN para
   un portfolio de pocos usuarios, o un presupuesto propio para la traducción— y
   medir contra él. Sería una decisión nueva y registrada, **no** una aprobación
   retroactiva del criterio anterior.

Observación registrada, no corregida: PostgreSQL corre con `shared_buffers=128 MB`
y `work_mem=4 MB`, valores de fábrica, sobre una base de 2.753 MB; y los seis
contenedores del proyecto corren **sin límite** de CPU ni memoria. No se tocó
ninguna de las dos cosas: no hay evidencia de que sean el limitante actual.

## 8. Versiones realmente desplegadas

La primera versión de esta acta declaraba los tres procesos del core en
`point5-cf260b1`. **Era falso**, y lo detectó la revalidación. El despliegue fue
**selectivo** y así queda registrado:

| Servicio | Imagen REAL | Notas |
|---|---|---|
| `swissjob-core-api-r5` | `swissjob-core:point5-cf260b1` | `core0051`, `authoritative: true`. Es quien sirve `/v1/profiles/{id}/matches` y, por tanto, el único que necesita el cambio |
| `swissjob-core-worker-r5` | **`swissjob-core:point4-51be757`** | no recreado |
| `swissjob-core-capture-r5` | **`swissjob-core:point4-51be757`** | no recreado |
| `swissjob-backend` | `swissjob-backend:point5-b7df2a9` | |
| `swissjob-worker` | `swissjob-worker:point5-b7df2a9` | misma imagen, re-etiquetada |

**Por qué no se recrean ahora**: la migración `core0051` es aditiva y ya está
aplicada; el cambio del feed lo sirve `core-api`; y recrear dos procesos sólo
para que coincidan con una tabla documental sería mover producción por una razón
cosmética. Queda como divergencia **conocida y declarada**, no como descuido.

**Consecuencia que hay que recordar**: el worker en ejecución **no contiene**
`CORE_CAPTURE_ENABLED`. Antes de apoyarse en ese interruptor para retirar el
slot CDC hay que verificar que el proceso que lo ejecuta lleva el código —no
basta con que esté en HEAD—. Anotado también en
[`RETIRADA_SLOT_CDC_PUNTO4.md`](../RETIRADA_SLOT_CDC_PUNTO4.md).

Copias `.before` y sondas en el directorio privado del NAS
`unification-e15-20260914/point5-20260922/`. La sonda de aceptación vigente es
`sonda_endpoint.py` (con sus controles negativos); `canario_http.py` y `equiv.py`
quedan **obsoletas por comprobación insuficiente** y no deben citarse como
evidencia.

## 9. Deuda: qué se cierra y qué no

- **A18-05 (coste del feed de matching) — PARCIALMENTE ATENDIDO, no cerrado.**
  El recorrido de 18 peticiones está corregido y verificado, pero el contrato de
  rendimiento del endpoint servido no se cumple todavía. Se cierra cuando §10
  esté completo.
- **A18-01/02/03/04/06 — NO se cierran por asociación.** No se revalidaron en
  este trabajo y no se tocaron sus recorridos.
- **Sin deuda nueva de rendimiento** en los recorridos medidos. Queda anotada la
  configuración de fábrica de PostgreSQL (§7) como observación, no como defecto.
- Fuera de alcance y sin tocar: retirada del slot, cron de retención, aceptación
  final del proyecto y GO de calidad del ranking.

## 10. Qué falta para poder cerrar el punto 5

Lista finita, derivada del contrato que yo mismo sellé:

1. **Medir el endpoint servido en los escenarios que faltan**: frío y caliente
   por separado, escrituras en copia, y navegación representativa de ambos
   frontends. Nada de eso se midió.
2. **Muestra suficiente en copia autorizada** (≥100 observaciones por recorrido
   prioritario), en lugar de series de producción. La serie de 50 que usé
   contradice el máximo de 20 que fija mi propia predeclaración para producción.
3. **Separar la cola de latencia**: correlacionar una ventana de peticiones con
   tiempos SQL, esperas de PostgreSQL y CPU/IO de host y contenedores, para
   atribuir los ~1,1 s no explicados del router.
4. **Presupuesto propio para la traducción de títulos**, o constancia de que el
   frontend la pide con `translate=false`.
5. **Decidir el presupuesto** (§7) y emitir aceptación contra el vigente o contra
   uno nuevo aprobado expresamente.

Mientras tanto: la optimización desplegada es **útil y está verificada** —18
peticiones a 1, equivalencia 8/8, sin regresión funcional— y no hay motivo para
revertirla. Simplemente **no basta para declarar el punto cerrado**.
