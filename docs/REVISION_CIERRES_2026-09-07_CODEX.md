# Revisión de los cierres del 2026-09-07

**Veredicto: REQUEST CHANGES. Confianza alta en las reproducciones descritas.**

Árbol revisado: `7871747..c8c15f9`, rama `feat/fase-a-core`. Se contrastó el
informe adjunto con el código y sus consumidores. No se accedió al NAS, no se
desplegó y no se modificó código productivo. Se preservó el cambio preexistente
en `school_job_monitor_architecture.md`.

Los cierres son reales pero algunos son **parciales**: las pruebas nuevas cubren
el ejemplo inicial, no todo el recorrido que debe preservar el invariante. Agrupo
los residuales por contrato para corregirlos conjuntamente, evitando otra ronda
de parches independientes.

## Verificación ejecutada

| Comprobación | Resultado propio |
|---|---|
| Cross-encoder, migración SwissJob, dev_eval y matching | **70 passed**, 47,89 s |
| Suite oficial completa, sin las pruebas de esta revisión | **1050 passed / 3 failed**, 1492,46 s |
| Repetición aislada de esos tres fallos | **3 failed**, 7,91 s |
| Control diagnóstico de esos mismos cuerpos con margen de BD holgado | **3 passed**, 5,41 s; no modifica ni sustituye la suite oficial |
| Cinco reproducciones adversariales nuevas | **5 failed**, 10,91 s, cada una en la aserción del defecto que se indica abajo |

Todas las invocaciones pytest se ejecutaron en serie. Usan el `conftest.py` del
proyecto: base desechable, migración a head y constraints reales. La reproducción
del presupuesto también se ejecutó directamente, sin BD, con el mismo resultado.
No se reejecutaron todas las mordidas históricas contra sus respectivos padres.

Las cinco pruebas están en [review_evidence_20260907.py](review_evidence_20260907.py).
No se incorporan automáticamente a la suite oficial: se montan expresamente bajo
su directorio de tests para reutilizar sus fixtures y aislamiento.

```bash
cd /home/lothar/Public/SwissJob
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
  -v /home/lothar/Public/SwissJob/docs/review_evidence_20260907.py:/app/jobhunt_core/tests/test_review_evidence_20260907.py:ro \
  core-migrate python -m pytest \
  jobhunt_core/tests/test_review_evidence_20260907.py -q --tb=short
```

## Hallazgos

| ID | Severidad | Dimensión | Contrato incompleto | Localización |
|---|---|---|---|---|
| A | P1 | Bugs / eficiencia | Presupuesto hasta el final de la publicación | `jobhunt_core/matching.py:1517`, `jobhunt_core/tasks/materialize.py:88` |
| B | P1 | Diseño / corrección | Exclusiones desde su modificación hasta el feed servido | `jobhunt_core/matching.py:1715`, `jobhunt_core/import_swissjob_durables.py:172` |
| C | P1 | Diseño / integridad de la evaluación | El sello no identifica la restricción efectiva | `jobhunt_core/dev_eval.py:226`, `jobhunt_core/dev_eval.py:422` |
| D | P2 | Eficiencia / regresión | La recuperación sigue reclamando el CE que ahora omite | `jobhunt_core/tasks/matching.py:100`, `jobhunt_core/shadow/projector.py:1471` |

### A — El presupuesto sigue siendo excedible por dos caminos

**Reproducción 1:** `test_fragment_cannot_overrun_soft_limit_in_second_batch`.
El bucle real recibe 128 documentos pendientes. Un reloj controlado representa
135 s de recuperación y 16 s por documento; no se espera ese tiempo real.
El lote de materialización predeterminado es de **64 documentos**:

- Primera tanda: `135 + 64×16 = 1159 s`.
- Como `1159 < 1200`, empieza otra tanda completa.
- Termina la inferencia a `2183 s`; la siguiente preparación eleva el total a
  **2318 s**, y el materializador devuelve `status=ok, agotado=False`.

Esto supera tanto el soft limit de 1800 s como el hard limit de 2100 s. No es un
benchmark nuevo del NAS: es una reproducción determinista de que el control de
flujo admite ese exceso. Con esos costes reales, Celery mataría la tarea antes
del retorno observado en la simulación. El test oficial compara constantes,
pero no comprueba cuánto trabajo puede comenzar antes del límite.

**Reproducción 2:** `test_real_cv_edit_reopens_unbudgeted_inference`.
Se materializan realmente los scores de dos ofertas. Después de obtener
`remaining=0`, y antes de la evaluación final, se guarda una revisión nueva del
CV con otro rol objetivo y se drena su embedding. `_con_factory` llama entonces
a `evaluate_profile`, que prepara la revisión nueva y vuelve a inferir **dos
documentos fuera del materializador presupuestado**. La prueba no simula un
éxito inexistente del materializador: cambia el CV después de su éxito real.
La valla de F3 valida la revisión nueva; por tanto no evita esta inferencia.

**Corrección mínima conjunta:**

1. Acotar también la unidad de inferencia/persistencia. No confundir el batch
   interno del motor (8) con la tanda de materialización (64). Antes de empezar
   una tanda debe caber su coste conservador medido, más SQL, commit y cierre,
   en el tiempo restante. Si no cabe, devolver backlog reanudable.
2. La evaluación invocada para publicar debe ser **cache-only**: si la nueva
   preparación encuentra misses, volver a materialización/backlog, sin inferir.
   La valla transaccional existente debe conservarse.
3. Probar conjuntamente tanda lenta, final de presupuesto, varios roles del
   perfil y cambio de revisión/corpus entre materialización y publicación.
   No basta envolver `to_thread` en un timeout: cancelar la espera no garantiza
   detener la inferencia que ya corre en el hilo.

El coordinador que encola trabajos separados sí corrige la acumulación anterior
de presupuestos dentro de una sola tarea. Ese subcierre se confirma.

### B — El filtro existe, pero no se conserva su comportamiento completo

**Reproducción ejecutada:**
`test_new_exclusions_empty_feed_must_remove_previous_results` publica dos
ofertas tituladas `python developer` y `python engineer`. Después inserta la
exclusión `title_contains=python` y fuerza otra evaluación. La recuperación
devuelve correctamente cero candidatos y la evaluación responde `ok`, pero
`matching.feed` sigue sirviendo **las dos ofertas excluidas**.

La causa es el retorno de `matching.py:1715`: sale antes de la fase de publicación
y no limpia los punteros anteriores. Además, el feed solo comprueba dismissed y
elegibilidad de la vacante; no aplica `profile_exclusions`.

**Integración adicional comprobada por lectura de los consumidores:**
`backend/routers/analytics.py:192` y `:214` siguen creando/desactivando
`JobFilter` en legacy. La aprobación de sugerencias también escribe allí.
El CDC captura `jobs`, `user_profiles` y `users`, no `job_filters`; no existe
un escritor API del core para la tabla nueva. El importador solo añade reglas.

Caso reproducible en un entorno desechable del BFF: migrar un filtro activo,
eliminarlo mediante `DELETE /api/v1/analytics/filters/{id}` y recalcular en el
core. El DELETE puede devolver 204 mientras la regla migrada sigue activa en
`profile_exclusions`. Una nueva regla creada por el POST tampoco llega al core.
Este recorrido HTTP no se ejecutó contra el NAS; la desconexión se establece
por los escritores y la lista de tablas capturadas, no por una prueba operativa.

**Corrección mínima conjunta:**

- Definir un único escritor efectivo de esta configuración y conectar a él
  altas, bajas, lectura y aprobación de sugerencias del BFF. Si se conserva un
  escritor legacy, su proyección debe cubrir también las bajas y ser verificable;
  no basta volver a ejecutar un importador que solo inserta.
- Invalidar la selección pendiente cuando cambien las reglas, y revalidar esa
  configuración antes de publicar. Reutilizar los mecanismos existentes cuando
  sea seguro; no invalidar innecesariamente los scores absolutos de parejas.
- Tratar un conjunto elegible legítimamente vacío como una fotografía publicable:
  tras pasar la valla de F3, limpiar los punteros del feed y registrar el intento.
  Preservar feedback, notas y guardados. No vaciar por errores de BD, ausencia de
  vector ni lecturas que hayan perdido la autoridad.

La comparación literal de títulos, el escape de comodines y la igualdad de tags
sí funcionan en la recuperación. El defecto residual está en el recorrido completo.

### C — Un mismo sello admite dos universos y dos métricas

**Reproducción ejecutada:**
`test_same_seal_cannot_certify_two_different_exclusions` sella un corpus de dos
ofertas, ambas juzgadas. Evalúa dos veces con el **mismo sello**, release, modelo,
política, revisiones y archivo de juicios: primero sin exclusiones y luego
excluyendo una de las ofertas.

Resultado: ambas evaluaciones se declaran `elegible=True`, con `feed_n=2/1` y
`ndcg10=1.0/0.613147`. La exclusión ha cambiado la población efectiva sin invalidar
el examen. No hay cambios de BD ni manipulación del SHA.

`build_universe_manifest` no incluye `exclude_vacancy_ids`, su validador no la
contrasta y el resultado tampoco registra esa lista. Los comandos CLI
`seal-universe`, `build-pool` y `evaluate` no la exponen ni la derivan del sello.
El parámetro Python repara la recuperación, pero aún no cierra la trazabilidad
del examen restringido.

**Corrección mínima:** guardar en el sello la lista canónica de exclusiones —o
el conjunto efectivo equivalente— y la configuración de filtros que determina
la elegibilidad por perfil. Pool y evaluación deben derivarla de ese mismo
artefacto; cualquier argumento contradictorio debe fallar cerrado. Conectar el
CLI al contrato y registrar su identidad en el informe. Mantener la exclusión
en SQL antes de los LIMIT, que ya está correctamente situada.

### D — La delegación deja encendida la recuperación

**Reproducción ejecutada:**
`test_delegated_ce_does_not_keep_projector_recovery_on_forever` activa cosine y
CE para un perfil, ejecuta dos veces `_evaluate_and_record` y consulta el SQL
real de recuperación. El perfil sigue pendiente.

La política CE continúa en `_ACTIVE_COMBOS_SQL`, pero el nuevo `continue` impide
registrar su intento. El materializador tampoco utiliza ese callback. Mientras
permanezca activa, esa condición no desaparece y el proyector puede repetir
innecesariamente el trabajo barato. No se afirma aquí inanición medida en el NAS.

**Corrección mínima:** la señal del proyector debe enumerar solo el trabajo del
que es responsable; la materialización CE debe tener su señal real de pendiente.
No fabricar un intento CE exitoso para apagarla. Reutilizar la misma clasificación
de políticas y probar que, sin cambios, una segunda pasada del proyector no vuelve
a evaluar por culpa de una política delegada.

## Cierres confirmados y límites

- La exclusión antes del LIMIT y su aplicación a `ce_inference=False` pasan.
- La elección de modelo canónico utiliza ahora corpus realmente elegible; pasa
  la regresión de vacantes archivadas.
- Pasa la reproducción del rollback de dos entradas convergentes sobre una fila
  previa. Esto no acredita el ensayo operativo sobre la copia del NAS.
- La regresión modificada del fallback HNSW pasa en esta suite. El cambio de
  objetivo fuerza la rama: no equivale a medir un underfill físico del índice.
- `git check-ignore` confirma el ignorado de `Public/trabajo_v8/`. La rotación,
  revocación y salud del frontend son actuaciones declaradas por el informe;
  no se volvieron a verificar en el NAS en esta revisión.

### Los tres fallos de la suite oficial

Son `test_scope_heartbeat_beats_while_the_block_runs`,
`test_heartbeat_survives_a_transient_failure` y
`test_heartbeat_aborts_the_fetch_when_evicted`, en `test_integration_runs.py`.
También fallan aislados. Esos tests y `runs.py` no cambian en el delta revisado.

Los tests reducen a **50 ms** tanto la cadencia como el timeout que incluye abrir
una sesión nueva, escribir y hacer commit. Como control, se ejecutaron sus mismos
cuerpos en una copia temporal con 500 ms y ventanas de observación de 2 s: **3/3
verdes**, sin modificar el motor. El control respalda un problema de sensibilidad
temporal del test en este entorno, no una regresión atribuible a estos commits.
No convierte el resultado oficial en 1053/1053: hay que estabilizar esas pruebas
con sincronización observable y plazos de BD realistas, conservando sus aserciones.

## Prioridades de cierre para el agente corrector

1. **Comportamiento operativo primero:** cerrar B de extremo a extremo, incluido
   el resultado vacío, alta/baja y cambio concurrente. La baseline puede seguir
   siendo la política canónica; esto no exige entrenar ni examinar otro modelo.
2. **Materialización como un solo paquete:** cerrar A y D juntos. Deben concordar
   coordinador, reloj, lote, caché, publicador y señal de recuperación. No declarar
   resuelto el presupuesto con una comparación de constantes.
3. **Instrumento antes de nuevas etiquetas:** cerrar C mediante el sello y CLI
   compartidos. No reutilizar el holdout consumido ni ajustar umbrales tras medir.
4. Incorporar las reproducciones a la suite apropiada antes de corregir; deben
   fallar por estas aserciones en `c8c15f9` y pasar con el fix. Añadir controles
   vecinos: vacío/error/deriva, crear/borrar/reintentar, cache-hit/cache-miss y
   reanudación. Ejecutar pruebas y suite en serie sobre un árbol identificado.
5. Usar el restaurador ya documentado, con `pg_restore --exit-on-error` y atomicidad
   cuando el procedimiento la permita. Verificar definiciones y validación de
   constraints, índices y triggers contra el origen; después, ensayo de migración
   y rollback comparando estado previo/posterior. No aceptar solo conteos ni ocultar
   errores. Las migraciones ya publicadas/aplicadas no se reescriben.
6. Con el ensayo válido: release inmutable, migraciones, remigración verificable de
   las siete exclusiones y canary de ambos perfiles. Confirmar **el feed servido**,
   no solo la existencia de filas. Mantener identificados el rollback y la versión
   realmente ejecutada por API y workers.
7. Actualizar ESTADO/DEUDA/PLAN/runbooks separando: corregido en código, ensayado,
   desplegado y confirmado en operación. E/F y la campaña de calidad son hitos
   distintos; no bloquear todo el avance de la baseline por el nuevo ranker.

El valor `0.5856/0.7438` no se certifica. Tampoco puede deducirse que reparar el
instrumento no pueda elevarlo: recuperar candidatos antes perdidos puede cambiar
el ranking. El NO-GO de calidad se sostiene por **ausencia de un examen válido**,
no por convertir una cifra invalidada en evidencia. Reabrir requiere un holdout
independiente nuevo después de cerrar instrumento y operación.

Esta secuencia permite un cierre acotado y verificable. No promete ausencia
absoluta de errores futuros ni autoriza un despliegue con los residuales abiertos.
