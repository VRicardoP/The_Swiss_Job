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
3. **Atribuí al host toda la latencia residual sin demostrarlo.** Y la
   rectificación tampoco servía: un **mínimo no separa trabajo de espera**
   —puede incluir I/O, conexión o lock—, así que 1,177 s no demuestra trabajo
   propio. La cola sigue **sin atribuir** (§5).
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

### 1b. El recorrido que el frontend pide de verdad

La revalidación exigió comprobar qué solicita el frontend. `MatchPage` llama
`useMatchResults(3000, 0)` con **`translate=false`** y no usa la variante
traducida: la pantalla principal pide **3.000 ofertas de una vez**.

Eso tiene una consecuencia que conviene decir claro: **el corte temprano del
consumidor no aplica a ese caso**. Con `needed = 3000 > 1800` recorre el feed
entero igual que antes. La primera optimización mejoró un tamaño de página que
la pantalla principal no usa.

### 1c. Traza de UNA petición completa, por fases

Sin restar medianas entre muestras distintas: cada fila viene de la misma
invocación, así que las partes suman el total.

| Fase | limit=3000 antes | limit=3000 ahora | limit=20 antes | limit=20 ahora |
|---|---:|---:|---:|---:|
| cargar usuario + routing + perfil | 0,021 s | 0,023 s | 0,032 s | 0,022 s |
| `matching.results` | 11,132 s | 9,741 s | 0,873 s | 0,990 s |
| overlay escolar | 0,478 s | 0,212 s | 0,434 s | 0,367 s |
| **serializar respuesta** | **65,747 s** | **0,322 s** | **1,888 s** | **0,007 s** |
| **TOTAL** | **79,265 s** | **10,298 s** | 3,340 s | **1,529 s** |

**El cuello dominante era la serialización, y nunca la había medido.**
`_to_match_response` detecta el idioma de **cada oferta servida**, también con
`translate=false`, porque el indicador de la UI lo necesita. Las 1.800 ofertas
del feed llegan **sin `language`** —el 96,9 %, medido después (§2b)— y cada
detección cuesta **50,1 ms**:
unos 90 s de detección pura por petición.

Conexión incómoda con el punto 4: se decidió **no exponer `language`** en los
normalizadores nativos porque «ninguna búsqueda lo filtra». Tenía un lector que
no se buscó: el router del BFF. El escritor legacy sí rellenaba ese campo.

Corrección aplicada (`f331c0d`): la detección es función pura del título, así que
se memoiza por proceso. No cambia ninguna respuesta y elimina el coste repetido;
1.544 de los 1.800 títulos son distintos, así que la primera carga sigue pagando
los nuevos. **La causa de fondo —que el dato viaje en la canónica— queda abierta
en §10.**

Hallazgo colateral: **`langdetect` no es determinista en títulos cortos**.
«Sviluppatore software» devolvió `sv` en una pasada y `en` en la siguiente, de
modo que el indicador de idioma **puede cambiar entre cargas**. La memoización
no lo causa: lo estabiliza dentro del proceso.

### 1c-bis. El endpoint servido, medido por HTTP

| Endpoint | antes | p50 | p95 | mín | ¿p95 ≤ 2 s? |
|---|---:|---:|---:|---:|---|
| `/api/v1/jobs/search` | 1,156 s | **0,739 s** | **1,810 s** | 0,517 s | **sí** |
| `/match/results` 20, `translate=false` | 1,924 s | **0,823 s** | 2,967 s | 0,542 s | no |
| `/match/results` 20, con traducción | 2,586 s | **1,766 s** | 4,331 s | 1,192 s | no (fuera del presupuesto) |
| `/match/results` **3000** (el real) | ~79 s | **8,617 s** | 11,387 s | 7,230 s | no (necesita presupuesto propio) |

### 1d. Las sondas, endurecidas tras la revalidación

La sonda original leía la clave `jobs` cuando la respuesta trae `data`: habría
dado por bueno un 200 indebidamente vacío. Su sustituta parecía arreglarlo con
cinco controles negativos… **y los cinco mentían**. Lo demostró la revalidación
externa: tres de ellos usaban la clave `results`, así que fallaban en la primera
guarda, mucho antes de llegar a la que decían probar. Desactivando las guardas de
cardinalidad y de total, `self_test()` **seguía verde**. Y el cuerpo
`data=[{}]*20, total=1800` pasaba: no comprobaba las identidades que su propia
docstring prometía.

Dos reglas nuevas, que es lo que hace refutable todo lo demás:

1. **Cada rechazo lleva un motivo nombrado** (`ProbeFailure.reason`) y cada
   control negativo exige **ese** motivo. Borrar una guarda deja de producir su
   motivo ⇒ rompe su propio control. Es la propiedad que se le pedía.
2. **Cada control negativo parte de un cuerpo válido y rompe UNA sola
   propiedad**, y hay además **controles positivos**: el cuerpo válido debe
   pasar, o una guarda de más convertiría la sonda en un rechazo universal que
   «detecta» todos los negativos sin probar nada.

Se añadió una comprobación de **equivalencia** entre repeticiones (identidad,
orden y score), porque devolver la misma *cantidad* con otro contenido pasaba
todos los controles anteriores.

Prueba de mutación ejecutada sobre el fichero, sin red ni base de datos
(`scripts/mutation_test_probe.py`, en el repo para que se pueda repetir): se
desactiva cada guarda una a una y se exige que `self_test()` deje de pasar.

| | Antes | Ahora |
|---|---:|---:|
| Guardas | 12 | 12 |
| Mutantes detectados | — | **12** |
| Supervivientes | 4 (y 3 controles por causa equivocada) | **0** |
| Controles | 5 negativos | 14 negativos + 2 positivos |

La primera pasada dejó **4 supervivientes** —cuerpo no-objeto, `data` no-lista,
oferta sin título y total no positivo, guardas sin ningún control— y se
añadieron los cuatro controles que faltaban hasta llegar a 12/12.

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

## 2b. El idioma: el transporte estaba roto Y el dato no existe

Al encontrar que `_to_match_response` detectaba el idioma de cada oferta servida,
escribí que la causa era mi decisión del punto 4 de no exponer `language` en los
normalizadores nativos, y que «el escritor legacy sí rellenaba ese campo».
**Las dos mitades eran falsas**, y hicieron falta dos correcciones ajenas y una
medición para verlo.

**Primera: rellenarlo en el origen no habría servido de nada.** Lo demostró la
revalidación externa. El campo se perdía aguas abajo, en tres sitios a la vez:

| Capa | Estado antes | Ahora |
|---|---|---|
| Contenido canónico (`offer_revisions.content`) | admite `language` | igual |
| `VacancyDTO` (`api/schemas.py`) | **no lo declaraba** | campo aditivo y opcional |
| `_vacancy_dtos` (`api/v1.py`) | **no lo leía** | `_canonical_language()` |
| `_job_view` (BFF `core_client.py`) | **no lo asignaba** | `_language_of()` |

`CoreJobView.language` existía, con el comentario «el router lo detecta por
título si falta»… y era **siempre** None, porque nadie lo llenaba nunca. Un dato
perfecto en la canónica se perdía igual. Reproducción del revisor:
`_job_view({'title':'Software Engineer','language':'de'}, 'core').language`
devolvía `None` recibiendo el dato explícitamente.

**Segunda: con el transporte arreglado, el dato sigue sin estar.** Es la
medición que faltaba —la revalidación no pudo hacerla porque la consulta al
corpus entero agotaba su límite de sentencia—, acotada aquí a las 2.000 filas
del feed en vez de a las 46.000 del corpus:

| | Ofertas | Con `language` utilizable |
|---|---:|---:|
| **Feed servido (muestra de 2.000)** | 2.000 | **62 — 3,1 %** |

Por fuente, lo que explica el 3,1 %:

| Fuente | Ofertas | Con idioma |
|---|---:|---:|
| `legacy:arbeitnow` | 1.133 | 17 (1,5 %) |
| `legacy:jobgether` | 430 | 2 |
| `arbeitnow` (nativa) | 102 | **0** |
| `legacy:nav_arbeidsplassen` | 80 | 10 |
| `legacy:workingnomads` | 15 | **15 (100 %)** |
| `legacy:thehub` / `legacy:euremotejobs` | 3 / 2 | 3 / 2 (100 %) |
| resto de fuentes nativas | 132 | **0** |

Lo traen **sólo** las fuentes cuyo metadato lo declara literalmente
(`search_metadata.py:82` pone `language: "en"`). El escritor legacy no lo
rellenaba: `legacy:arbeitnow` está al 1,5 %. **El corpus nunca tuvo cobertura de
idioma**; lo del punto 4 empeoró un campo que ya estaba casi vacío, no rompió
uno que funcionaba.

**Consecuencia para el cierre, que es lo que importa:** el transporte había que
arreglarlo —un dato correcto no puede perderse por el camino— pero **recupera el
3,1 % del coste, no el 100 %**. La memoización sigue siendo la que sostiene el
recorrido, con la cota que el revisor señaló y que esta acta hace suya: **no
cubre la primera carga**. Con 1.544 títulos únicos a 50,1 ms, la primera
petición tras cada arranque o expulsión vuelve a pagar del orden de 77 s. Por
eso la matriz de aceptación **debe medir frío y caliente por separado**, y por
eso el arreglo de fondo no es una caché sino **deducir el idioma UNA vez, al
ingerir**, y escribirlo en la canónica (§10).

### La detección no es una función pura, y la docstring lo decía

La docstring de `_detect_language` afirmaba «función pura», «no cambia ni una
respuesta» y «el resultado de un mismo título no cambia». El revisor lo midió:
30 vaciados de caché de «Sviluppatore software» dan **en=21, sv=6, it=3**. La
caché **estabiliza** una respuesta; no la hace correcta ni igual entre procesos,
workers o reinicios. Corregida la docstring para prometer exactamente eso y
nada más.

## 2c. Los 9,7 s restantes: el 87-89 % son las 18 páginas del core

Con el idioma fuera del camino, el coste de la pantalla principal quedaba en
`matching.results`. En vez de suponer dónde, se trazó por fases en el NAS
(`scratchpad/traza_feed.py`, sólo lectura, dos vueltas seguidas):

| Vuelta | Total | Páginas al core | Tiempo en páginas | % | p50/página | Resto BFF |
|---|---:|---:|---:|---:|---:|---:|
| 1ª (fría) | 54,347 s | 18 | 47,484 s | **87,4 %** | 1,594 s | 6,863 s |
| 2ª (caliente) | 12,910 s | 18 | 11,469 s | **88,8 %** | 0,348 s | 1,441 s |

**Por qué caliente sigue costando, que es lo que no era obvio:** hay caché de
páginas por ETag, pero un `If-None-Match` **no ahorra trabajo en el core**. El
ETag se deriva del payload (`v1._etag_of`), así que para contestar 304 el core
tiene que construir la página igual. La caché ahorra transferencia, no cómputo.

De aquí sale la conclusión operativa: **no hay forma de servir 1.800 ofertas
bajo 2 s**. Ni con páginas mayores —el coste va con los items, 3,5 ms cada uno—
ni afinando SQL. La pantalla no debe pedirlas en cada carga.

### Qué se hizo: preguntar si cambió en vez de descargarlo

`GET /v1/profiles/{id}/matches/version` devuelve `{version, total}`. La versión
es un `md5` sobre `vacancy_id : current_eval_id : current_offer_revision_id` de
todo el feed, con su misma cláusula (excluye no-activas y feedback negativo, y
alcanza el índice parcial `ix_pvs_feed_current_eval`).

Los tres componentes no son decorativos: cubren **pertenencia** (entra o sale
una oferta), **evaluación** (re-scoring) y **contenido** (título nuevo ⇒
revisión canónica nueva). Un contador o un `max(updated_at)` no bastarían: un
alta y una baja simultáneas dejan el contador igual.

El consumidor cachea el recorrido **completo** por perfil y sólo lo reutiliza
si la versión coincide **exactamente**. Cuatro cotas deliberadas:

1. **Sólo el recorrido completo se cachea.** Uno cortado por `needed` no puede
   responder a quien pida más.
2. **Sólo se pregunta la versión si puede pagar** (`needed > 100`). La consulta
   recorre las mismas filas que el recuento (0,4-0,9 s): pedirla para servir
   20 ofertas convertiría una petición barata en dos.
3. **Sólo se cachea la parte inmutable.** El estado local del usuario
   —feedback, candidatura, urgencia, borrador— se relee en CADA petición. Por
   eso esta caché no puede servir rancio lo que el usuario acaba de tocar.
4. **Sin versión fiable, se recorre.** Core antiguo, fallo de red o payload con
   otra forma ⇒ comportamiento de siempre. Degradar el rendimiento es
   aceptable; servir datos viejos, no.

### Lo que cuesta la consulta de versión, medido antes de prometer nada

Sobre la base de producción, mismo SQL que ejecuta el endpoint, tres pasadas
seguidas (`\timing`, sólo lectura):

| Pasada | Tiempo | Resultado |
|---|---:|---|
| 1ª (fría) | **1,224 s** | `total=1800`, digest `b16bfe3a…` |
| 2ª | **0,292 s** | idéntico |
| 3ª | **0,606 s** | idéntico |

El digest es estable entre pasadas y el total coincide con el feed servido.

**Aritmética honesta de lo que esto deja, que NO es una medición del endpoint.**
Una carga con caché válida costaría: versión 0,3-0,6 s + resto del BFF 1,4 s
(resolución de identidad y overlay local de 1.800 items, del trazado) +
serialización 0,3 s ≈ **2,0-2,3 s**, frente a los 12,9 s de hoy. Es una mejora
de unas 6 veces, pero **queda en el filo del p95 ≤ 2 s y puede no cumplirlo**.
Si no cumple, lo que queda por atacar está identificado y es el resto del BFF:
1,4 s para resolver identidad y superponer estado local de 1.800 ofertas.

Esto es una estimación compuesta, no un p95 del endpoint servido. Medirlo de
verdad exige desplegar; mientras no se despliegue, el escenario queda
**expresamente pendiente**, no aprobado por aritmética.

**El frontend no cambia**: categorías, contadores, Watchlist, orden y tarjetas
siguen saliendo del mismo payload. Es optimización, no cambio de
funcionalidad, que es la condición que el propietario puso.

Un defecto propio que salió en la suite y conviene dejar escrito: `_feed_version`
hacía `resp.json().get(...)` y un 200 con un cuerpo **no-objeto** lanzaba
`AttributeError` fuera del fallback. Corregido y fijado con casos de forma rara.

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

## 5. La cola de latencia: qué se sabe y qué no

La primera versión de esta acta afirmaba que el residual **no procedía del
código**. Era falso y la traza lo demostró: el cuello dominante del recorrido
real era **nuestro** —90 s de detección de idioma por petición—, no del host.

Una segunda afirmación tampoco tenía el rigor que aparentaba: dije que «el
mínimo de 1,177 s es trabajo propio». **Un mínimo tampoco separa trabajo de
espera**: puede incluir esperas de I/O, de conexión o de lock. Y restar medianas
de experimentos distintos —0,82 s de un desglose frente a 1,924 s de otro— no
atribuye nada con rigor. Ambas cosas se corrigieron trazando la petición
completa, que es de donde salen las cifras de §1c.

Lo que sí está medido del entorno, y sigue siendo contexto, no conclusión:
loadavg 5,65–7,38 sobre dos núcleos sin carga de prueba propia, y ningún
contenedor del proyecto por encima del 0,4 % de CPU en el muestreo instantáneo.
Eso **no** separa espera de CPU, disco, locks o consultas.

Lo que queda por explicar, ya con el cuello mayor corregido: el p95 de
`/match/results` con 20 ofertas (2,967 s) frente a su mediana de 0,823 s. No
está atribuido. Separarlo exige correlacionar una ventana de peticiones con
tiempos SQL, esperas de PostgreSQL y CPU/IO de host y contenedores. **No se ha
hecho**, y esta acta no afirma la causa.

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
| Suite del core | **1.760 passed** (+11: transporte de idioma) |
| Suite del BFF | **2.527 passed, 4 xfailed** (+12: frontera de idioma) |

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
| `swissjob-backend` | `swissjob-backend:point5-f331c0d` | memoización de la detección de idioma |
| `swissjob-worker` | `swissjob-worker:point5-b7df2a9` | **no recreado tras `f331c0d`**: no sirve ese recorrido |

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

## 9-bis. Matriz de aceptación tras el despliegue conjunto (2026-09-23)

Desplegado `point5-9d6b46e` en los **cinco** servicios —`core-api`,
`core-worker`, `core-capture`, `backend`, `worker`—, sin desfase entre lo
declarado y lo que corre. Migración `d3a7c1f60b84` aplicada. Copias `.before`
en `point5-close-20260923`.

Antes de medir se drenó la cola del almacén de idioma: **1.547 títulos
resueltos, 0 desconocidos, 0 pendientes**. Es el régimen permanente, no un
estado de estreno. El `backend` se reinició después para que la primera muestra
de cada ruta fuese **fría de verdad**.

Reglas de la predeclaración respetadas: secuencial, ≤ 20 muestras por ruta, 1
sesión, sólo lectura, sin fabricar nada y **sin llamar a proveedores
facturables**.

### Resultado por escenario obligatorio

| Escenario | Presupuesto | Medido | Veredicto |
|---|---|---|---|
| Readiness y lectura ligera | p95 ≤ 1 s | `/api/v1/health` p95 **0,323 s**; `/v1/ready` p95 **0,039 s** | **CUMPLE** |
| Lectura habitual — catálogo | p95 ≤ 2 s | `/jobs/search?limit=20` p50 0,718 s, p95 **2,420 s** | **NO CUMPLE** |
| Lectura habitual — feed 20 | p95 ≤ 2 s | p50 1,284 s, p95 **3,040 s** | **NO CUMPLE** |
| Lectura habitual — pantalla principal (3.000) | p95 ≤ 2 s | p50 **2,147 s**, p95 **2,554 s** | **NO CUMPLE** |
| Primera lectura tras arranque — catálogo | ≤ 5 s | **2,920 s** | **CUMPLE** |
| Primera lectura tras arranque — feed 20 | ≤ 5 s | **1,002 s** | **CUMPLE** |
| Primera lectura tras arranque — 3.000 | ≤ 5 s | **10,196 s** | **NO CUMPLE** |
| ≥ 100 muestras en copia | obligatorio | — | **PENDIENTE**: no existe copia autorizada |
| Escrituras locales/core | p95 ≤ 2 s | — | **PENDIENTE**: medirlo en producción exigiría fabricar datos, prohibido |
| Frontend, contenido útil | ≤ 3 s | — | **PENDIENTE**: sin sesión de navegador. La llamada que lo alimenta da p50 2,147 s / p95 2,554 s |
| Traducción de títulos | sin presupuesto | — | **PENDIENTE**: no medida (LLM facturable); la pantalla principal no la usa |
| Fondo | pendiente no crece | `alertas: []`, 17/17 scopes sin fallos | **CUMPLE** |

Sin regresión: **0 reinicios**, 0 OOM, **0 respuestas 5xx** en 30 min, cosecha
sana.

### Lo que sí cambió, que es mucho

| Recorrido | Antes del punto 5 | Ayer | Hoy |
|---|---:|---:|---:|
| Pantalla principal (3.000), p50 | **79,265 s** | 8,617 s | **2,147 s** |
| Pantalla principal, primera carga | ~54 s + ~77 s de idioma | ~54 s | **10,196 s** |
| `matching.results` 3.000, caliente | 12,910 s | 12,910 s | ~2,4 s |

### El veredicto, sin suavizarlo

**El contrato vigente NO se cumple.** Ninguna de las tres lecturas habituales
baja del p95 de 2 s, y la primera carga de la pantalla principal se pasa del
presupuesto de 5 s por el doble. Que la mejora sea de **37 veces** en la
mediana del recorrido principal no convierte un incumplimiento en cumplimiento.

Lo que queda está identificado y es medible, no es una incógnita:

1. **~1,4 s por petición de trabajo del BFF** sobre 1.800 items (resolver
   identidad legacy y superponer estado local), del trazado por fases. Es hoy
   el mayor sumando de la pantalla principal en caliente.
2. **La primera carga sigue recorriendo el feed entero** (10,2 s). La caché por
   versión no puede evitarlo: alguien tiene que recorrerlo una vez. Lo que sí
   puede es que ese alguien **no sea el usuario** — calentar tras cada cambio
   de versión, en segundo plano.
3. **La cola de latencia de las rutas de 20 sigue SIN atribuir**. Con n=20 el
   p95 ES el máximo, así que un único pico decide el veredicto. Separar código
   de entorno exige observación correlacionada, que sigue sin hacerse.

## 10. Qué falta para poder cerrar el punto 5

El criterio debe fijarse **antes** de la ejecución final, no después de ver el
resultado. Lista finita:

1. **Decidir el presupuesto** (§7). Una corrección sobre lo que esta acta
   decía aquí: escribí que la carga de 3.000 ofertas «no es una lectura habitual
   de 20» y por tanto carecía de presupuesto. **Es al revés.** Esa carga es lo
   que `MatchPage` pide en CADA entrada a la pantalla principal: es la lectura
   habitual, y la predeclaración ya la cubre con sus objetivos de lectura y de
   pantalla útil. Descubrir tarde su tamaño no la convierte en una exportación
   excepcional; excluirla después de medirla sería cambiar el criterio al ver el
   resultado, que es justo el error que abrió esta ronda. Se puede rediseñar la
   carga (punto 5 de esta lista) o acordar **expresamente** otro presupuesto
   como cambio de requisito, nunca como ausencia previa de requisito.
   Lo que sí carece de presupuesto propio y hay que fijarlo es la **traducción
   de títulos**, por depender de un LLM externo. Hoy no afecta a esta pantalla:
   `useMatchResultsPage` —la única consulta con `translate=true`— **no la usa
   nadie**, es código muerto.
2. **Matriz única de aceptación** contra el presupuesto vigente: ambos
   frontends, **≥100 muestras por recorrido prioritario en copia**, concurrencia
   prevista, frío y caliente separados, y escrituras. Después, canario acotado
   en el NAS. Nada de eso se ha hecho: las series usadas aquí son de producción
   y una de ellas (n=50) contradice el máximo de 20 que fija la predeclaración.
   **El frío no es un adorno**: con el 96,9 % del feed sin idioma, la primera
   petición tras un arranque o una expulsión de caché vuelve a pagar del orden
   de 77 s de detección. Medir sólo en caliente ocultaría exactamente eso.
3. **Atribuir el p95 que queda**: `/match/results` con 20 ofertas da p50
   1,284 s y p95 3,040 s; el catálogo, p95 2,420 s. Con n=20 el p95 ES el
   máximo, así que un solo pico decide. Exige observación correlacionada de
   SQL, esperas de PostgreSQL y CPU/IO, no deducción entre muestras. **Sigue
   sin hacerse.**

3-bis. **Bajar el trabajo del BFF por petición**: ~1,4 s para resolver
   identidad legacy y superponer estado local de 1.800 items. Es el mayor
   sumando que queda en caliente de la pantalla principal.

3-ter. **Que la primera carga no la pague el usuario**: recorrer el feed una
   vez por cambio de versión es inevitable; hacerlo en segundo plano al
   detectar la versión nueva, no. Hoy son 10,196 s en la cara del usuario.
4. ~~Que el idioma EXISTA en la canónica.~~ **HECHO de otra forma**, y
   conviene decir cuál. El transporte se reparó (§2b) pero sólo cubre el 3,1 %.
   Para el resto, el idioma se deriva **una vez por título** y se persiste en
   `job_title_languages`, fuera del camino de respuesta. En producción:
   **1.547 títulos, 0 pendientes, 0 desconocidos**. No se reingirió ni se
   reembebió nada, y la canónica no se toca: lo derivado vive donde se ve que
   es derivado. Los tres estados (ausente / encolado / resuelto-desconocido)
   son explícitos, que era la condición.
5. **Revisar si `matching.results` puede servir 3.000 ofertas mejor**: son
   9,7 s de los 10,3 s del caso real, y proceden de 18 peticiones al core en
   páginas de 100. No se ha tocado.

   Lo que un rediseño tiene que **conservar**, leído en el frontend y no
   supuesto: `MatchPage` usa el lote completo para agrupar por categorías y
   contar cada pestaña (`groupByCategory`), para la pestaña **Watchlist**
   (filtro por `school_id` y orden por `score_final + urgency_score`), para el
   **top score** (máximo) y para los **matches ≥ 70** (recuento). Y renderiza
   las tarjetas visibles desde ese mismo lote. Bajar el límite a 20 sin más
   **rompería las cinco cosas**. La vía que preserva la semántica es que el
   servidor calcule esos agregados y la pantalla pagine las tarjetas; es un
   cambio de funcionalidad y **corresponde decidirlo al propietario**, no
   colarlo dentro de una corrección de rendimiento.

Cada escenario obligatorio **cumple o queda expresamente pendiente**. Una mejora
porcentual, una mediana buena o una atribución al hardware no sustituyen ese
resultado.

Mientras tanto, lo desplegado es útil y está verificado —de **79,3 s a 2,147 s**
de mediana en el recorrido que usa la pantalla principal, sin regresión
funcional, con **2.562** pruebas del BFF y **1.765** del core en verde, 0
reinicios y 0 respuestas 5xx— y no hay motivo para revertirlo. Pero **el
contrato sigue sin cumplirse** y el punto 5 sigue ABIERTO.
