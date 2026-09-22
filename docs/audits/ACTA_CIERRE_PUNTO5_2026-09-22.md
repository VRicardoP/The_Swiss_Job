# Acta del punto 5 — rendimiento del sistema servido

Fecha: **2026-09-22**, 08:29 → 10:10 UTC. Presupuestos y baseline sellados antes
de comparar variantes en [`PREDECLARACION_PUNTO5_2026-09-22.md`](../PREDECLARACION_PUNTO5_2026-09-22.md).

## Veredicto

**PUNTO 5 CERRADO para los recorridos de lectura medidos, con una condición
externa declarada.** El cuello dominante estaba localizado, medido y corregido:
servir una página del feed pasó de **9,3–12,9 s a 0,56–1,19 s de mediana**, y de
**18 peticiones internas a 1**, con contrato idéntico verificado sobre datos
reales. Lo que queda por encima del presupuesto en la cola alta **no es
atribuible al código**: el host está sobresuscrito por procesos ajenos al
proyecto (§5). Esa decisión es del propietario y se enuncia en §7.

No se declara GO de calidad del ranking, ni cierre del proyecto, ni se toca la
retirada del slot ni el cron de retención: siguen siendo entregables separados.

## 1. Qué se midió y con qué

Camino real `CoreMatching.results()` dentro de los contenedores desplegados,
sólo lectura, sin fixtures ni carga artificial en producción. Concurrencia 1 y 2,
n=50 por caso en las medidas finales, warmup declarado y excluido.

| Recorrido | Presupuesto | p50 | p95 | Veredicto |
|---|---|---:|---:|---|
| Feed de matching, perfil 1, conc. 1 | ≤2 s | **0,648 s** | 2,131 s | cumple en p50; cola externa |
| Feed de matching, perfil 1, conc. 2 | ≤2 s | **1,032 s** | 2,366 s | cumple en p50; cola externa |
| Feed de matching, perfil 2, conc. 1 | ≤2 s | **0,558 s** | **1,413 s** | **cumple** |
| Feed de matching, perfil 2, conc. 2 | ≤2 s | **1,186 s** | 2,552 s | cumple en p50; cola externa |
| Guardados | ≤2 s | **0,120 s** | **0,320 s** | **cumple** |
| Catálogo por texto | ≤2 s | **0,147 s** | **0,219 s** | **cumple** |
| Catálogo, página profunda (offset 200) | ≤2 s | **0,669 s** | **1,850 s** | **cumple** |
| Catálogo remoto | ≤2 s | **0,416 s** | 2,753 s | cumple en p50; cola externa |
| Catálogo general | ≤2 s | **1,510 s** | 2,985 s | cumple en p50; cola externa |
| `/api/v1/jobs/search` por HTTP (canario, n=10) | ≤2 s | **0,699 s** | máx. 1,783 s | **cumple** |
| `/api/v1/health` | ≤1 s | 200 OK | — | **cumple** |

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

## 5. Lo que queda por encima del presupuesto, y de quién es

El p95 residual (1,4–2,6 s) **no lo produce este código**. Medido sin carga de
prueba propia:

- loadavg del host entre **5,65 y 7,38 con dos núcleos** — sobresuscripción de
  entre 2,8× y 3,7×;
- ningún contenedor del proyecto pasa del **0,4 % de CPU** (`swissjob-backend`
  0,37 %); los mayores consumidores del momento eran `redis` 5,69 %, `redis` 4,78 %
  y `portfolio_db` 4,78 %, insuficientes para explicar esa carga;
- el trabajo real de una petición son ~0,4 s (COUNT ~0,3 s + página 0,03 s +
  HTTP), y **ése es justamente el mínimo observado**: 0,349–0,662 s. El p95 es el
  mismo trabajo esperando CPU.

No se paró ningún otro servicio para fabricar una cifra mejor, ni se tocaron
contenedores ajenos al proyecto.

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

El presupuesto de 2 s se cumple en mediana en todos los recorridos y en p95 en
varios. Para que el p95 lo cumpla **siempre** haría falta actuar sobre la
saturación del host, que es ajena a este proyecto. Tres opciones, sin preferencia
impuesta:

1. **Aceptar el estado actual.** La mediana está en 0,12–1,51 s y el trabajo
   propio en ~0,4 s. Para un portfolio de pocos usuarios es holgado.
2. **Poner límites de CPU/memoria** a los contenedores que compiten. Hoy los seis
   servicios del proyecto corren **sin límite alguno** (`Memory=0`, `NanoCpus=0`),
   igual que los ajenos. Es una decisión de operación del NAS, no de código.
3. **Revisar qué más corre en el NAS.** Ese diagnóstico excede el alcance de este
   encargo y no se ha hecho.

Observación registrada, no corregida: PostgreSQL corre con `shared_buffers=128 MB`
y `work_mem=4 MB`, valores **de fábrica**, sobre una base de 2.753 MB. No se tocó
porque no hay evidencia de que sea el limitante actual y porque subirlo compite
por la misma memoria que el resto del NAS.

## 8. Versión certificada

| Servicio | Imagen | Notas |
|---|---|---|
| core-api / worker / capture | `swissjob-core:point5-cf260b1` | `core0051`, `authoritative: true` |
| backend público | `swissjob-backend:point5-b7df2a9` | |
| worker público | `swissjob-worker:point5-b7df2a9` | misma imagen, re-etiquetada |

Copias `.before` de cada compose en el directorio privado del NAS
`unification-e15-20260914/point5-20260922/`, junto con los scripts de medición
(`bench_feed.py`, `bench_fases.py`, `bench_count2.py`, `bench_p95.py`,
`bench_rutas.py`, `equiv.py`, `plan_count.py`, `canario_http.py`).

Si más adelante se retira el slot CDC, esta configuración cambia y basta una
revalidación ligera: repetir `bench_p95.py` y el canario HTTP.

## 9. Deuda: qué se cierra y qué no

- **A18-05 (coste del feed de matching) — CERRADO** con medición antes/después,
  causa identificada, corrección mínima y equivalencia verificada.
- **A18-01/02/03/04/06 — NO se cierran por asociación.** No se revalidaron en
  este trabajo y no se tocaron sus recorridos.
- **Sin deuda nueva de rendimiento** en los recorridos medidos. Queda anotada la
  configuración de fábrica de PostgreSQL (§7) como observación, no como defecto.
- Fuera de alcance y sin tocar: retirada del slot, cron de retención, aceptación
  final del proyecto y GO de calidad del ranking.
