# Revalidación independiente del informe y del despliegue

Fecha: 2026-09-22, sondas entre 11:42 y 11:47 UTC.
Repositorio: `feat/fase-a-core`, HEAD `dcdc321`.
Alcance: informe de jornada, acta del punto 5, delta `ac81a59..HEAD`,
pruebas dirigidas locales y comprobaciones de sólo lectura en el NAS.

## Veredicto

**REQUEST CHANGES sobre la certificación del informe.**
**Despliegue comprobado operativo; mejora del feed desplegada.**

No se ha demostrado una regresión funcional que exija revertir este despliegue.
Sí hay un cierre de rendimiento que excede la evidencia, una atribución causal
no demostrada y un inventario de versiones incorrecto. Corregir esas afirmaciones
no exige deshacer la cosecha nativa ni volver a abrir toda la migración.

La revisión aplica `code-review` y `yagni`: correcciones acotadas, sin nuevas
funcionalidades, refactorización general ni cambios operativos especulativos.

## Estado observado, no inferido de los documentos

| Comprobación | Resultado actual |
|---|---|
| `/v1/ready` | `ready`, `core0051`, release `cf260b1cf52a6a45696fe64e0d2977075ede6255`, `authoritative=true` |
| API core | `swissjob-core:point5-cf260b1` |
| Worker y captura core | **`swissjob-core:point4-51be757`**, no la imagen del punto 5 |
| Backend / worker públicos | `point5-b7df2a9` |
| Portfolio backend | `e15-9f8c85a` |
| Reinicios Docker | 0 en los seis contenedores comprobados |
| Cosecha | 16 fuentes, 17 scopes habilitados; todos completados entre 10:10:12 y 10:14:31 UTC; 0 fallos consecutivos |
| Último `check_health` observado | 11:14:13 UTC, `alertas=[]`, 17 habilitados y con estado |
| CDC pendiente | 0 filas con `applied_at IS NULL` |
| Entregas outbox | 73.930 `delivered`; ningún otro estado en la consulta de 11:46 UTC |
| Índice nuevo | `ix_pvs_feed_current_eval` presente, condición `current_eval_id IS NOT NULL` |
| Código realmente servido | SHA-256 idénticos a los locales en `matching.py`, `api/v1.py`, `api/schemas.py` y el cliente matching del BFF |

El proyector sigue ejecutándose: se observaron terminaciones `status=ok`, incluida
una recuperación de dos perfiles. No se retiró el slot ni se modificó ningún flag.
El worker antiguo todavía no contiene `CORE_CAPTURE_ENABLED`; la retirada futura
debe comprobar su despliegue efectivo antes de depender de ese interruptor.

### Canarios nuevos

Lecturas secuenciales con pausa, sin escribir feedback, lanzar cosechas ni enviar
avisos. Son canarios pequeños, **no una certificación estadística de capacidad**.

| Recorrido | n | Mediana | Máximo | Resultado funcional |
|---|---:|---:|---:|---|
| Catálogo HTTP, 20 ofertas | 10 | 0,641 s | 1,493 s | 200; comprobación adicional: `data` contiene 20 ofertas, total 46.489 |
| `CoreMatching.results`, perfil 1 | 10 | 0,928 s | 9,370 s | 20 ofertas; total 1.800 |
| `CoreMatching.results`, perfil 2 | 10 | 1,892 s | 4,456 s | 20 ofertas; total 1.799 |

Para cada perfil se hizo además un recorrido completo: **longitud real** 1.800
y 1.799, respectivamente. Coinciden con el total informado y con los IDs y orden
de la primera página corta. Esto comprueba el total de forma más fuerte que
comparar dos respuestas que reutilizan el mismo contador.

## Hallazgos y corrección mínima

### P1 — Cierre del punto 5 sin satisfacer el contrato declarado

Ubicación: `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md:8`, `:21`, `:27`;
`docs/INFORME_JORNADA_2026-09-22.md:10`.

Reproducción: comparar la tabla del acta con
`docs/PREDECLARACION_PUNTO5_2026-09-22.md:35`: el requisito es **p95 extremo a
extremo <=2 s**, no mediana. El acta contiene p95 de 2,131, 2,366, 2,552,
2,753 y 2,985 s y aun así declara cerrado el punto. Además, mide el método
`CoreMatching.results`, no el endpoint completo: `backend/routers/match.py:256`
añade lectura del perfil, overlay escolar y construcción de respuesta después
de ese método. La traducción, si se usa, debe presupuestarse aparte.

La predeclaración exige también frío, escrituras en copia y navegación de
frontend. El acta no aporta esas medidas. Su serie de 50 lecturas en producción
contradice el máximo de 20 de la propia predeclaración (`:63`); no sustituye la
serie >=100 sobre copia autorizada que allí se exige.

**Fix mínimo:** reconocer «optimización del recorrido de feed implementada y
desplegada; aceptación integral de rendimiento pendiente». Completar sólo la
matriz faltante del contrato con medidas extremo a extremo, frío/caliente y
carga acotada en copia. Si el propietario acepta otro presupuesto, registrarlo
como una decisión nueva y medir contra él, sin convertir p50 en p95 ni aprobar
retroactivamente el criterio anterior.

### P2 — Atribución exclusiva al host no demostrada

Ubicación: `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md:112` y
`docs/INFORME_JORNADA_2026-09-22.md:91`.

El acta afirma que el residual no procede del código y es espera de CPU ajena.
Un loadavg alto, porcentajes de CPU instantáneos y el mínimo de latencia no
separan espera de CPU, I/O, locks, conexiones y consultas. Tampoco demuestran
que el trabajo del propio proyecto no contribuya durante los picos.
Los canarios actuales vuelven a mostrar picos, pero no identifican su causa.

**Fix mínimo:** cambiar la atribución a hipótesis y correlacionar una ventana
de peticiones con tiempos SQL/HTTP, esperas de PostgreSQL y CPU/I/O del host
y contenedores. No imponer límites de recursos ni cambiar PostgreSQL a ciegas.

### P2 — La imagen certificada no coincide con dos procesos vivos

Ubicación: `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md:168`.

Reproducción de sólo lectura:

```sh
ssh nas '/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker inspect \
  --format "{{.Name}} {{.Config.Image}}" \
  swissjob-core-api-r5 swissjob-core-worker-r5 swissjob-core-capture-r5'
```

La API devuelve `point5-cf260b1`; worker y captura devuelven `point4-51be757`.
El acta atribuye `point5-cf260b1` a los tres. La migración aditiva y el cambio
del feed no demuestran por sí solos incompatibilidad del worker antiguo.

**Fix mínimo:** inventario por servicio e indicación explícita del despliegue
selectivo. No recrear los workers sólo para hacer coincidir una tabla documental.
Antes de retirar CDC, verificar el código del interruptor en el proceso que
realmente lo ejecuta.

### P2 — Sondas de aceptación con comprobaciones vacuas/incompletas

Ubicación: scripts NAS `point5-20260922/canario_http.py` y `equiv.py`,
referenciados en el acta `:78` y `:172`.

Reproducción: el canario publicado obtiene `body.get('jobs', [])`, aunque el
endpoint devuelve `data`. Al ejecutarlo imprimió `items=0`, total 46.489 y
terminó correctamente. Una sonda adicional confirmó `len(data)=20`.
Por tanto, ese canario pasaría igualmente ante un 200 indebidamente vacío.

En `equiv.py`, tanto el recorrido corto como el completo usan el mismo total
del core. Compararlos no prueba que el total coincida con `len(todo)`; además
las condiciones se imprimen, sin hacer fallar el proceso cuando son falsas.

**Fix mínimo:** comprobar explícitamente estructura `data`, cardinalidad esperada
del caso controlado, IDs/orden/scores y `total == len(todo)`; terminar con error
ante discrepancia. Añadir controles negativos locales (200 vacío inesperado,
total incorrecto y un ID distinto) que demuestren que las sondas fallan.
No fabricar datos en producción para probarlos.

## Pruebas ejecutadas en esta revisión

En serie, en el ordenador local. Core sobre la BD desechable creada por su
`conftest`; BFF sobre `swissjobhunter_test`. Nunca contra el NAS:

1. `test_matches_total.py` + `test_integration_matching.py`: **35 passed**.
2. BFF `test_matching_contract.py`, `test_matching_native_identity.py`,
   `test_feedback_cutover.py`, `test_schools_core.py`: **73 passed**.
3. Core `test_integration_api.py`, `test_integration_api_feedback.py`,
   `test_integration_school_feedback_order.py`, `test_feedback_plan_batch.py`:
   **57 passed**.

**165 pruebas dirigidas verdes**. Las ejecuciones core avisaron únicamente de
que no podían escribir la caché de pytest. `git diff --check` sin incidencias.
No se reejecutaron las suites completas de 1.749/2.511; sus cifras permanecen
como evidencia declarada del autor, no recertificada aquí.

Se revisaron contador/feedback efectivo, ownership, paginación, fallback sin
`total` e índice aditivo. No se encontró una regresión funcional demostrable en
ese delta. Esto no garantiza ausencia de todo bug ni sustituye la aceptación
de ambos frontends. Tampoco se repitió el replay de proveedores ni se generó
otro aviso para recertificar retrospectivamente la entrega natural del informe.

## Secuencia mínima para cerrar la discrepancia

1. Rectificar alcance del cierre, tabla de imágenes y atribución causal en acta,
   informe y documentos que los replican. Mantener el despliegue útil actual.
2. Endurecer las dos sondas con controles negativos locales antes de medir más.
3. Medir el endpoint servido, no sólo el método intermedio; completar en copia
   los escenarios pendientes de la predeclaración. No repetir la cosecha entera.
4. Diagnosticar la cola de latencia con observación correlacionada; aplicar una
   corrección sólo si la evidencia la justifica y probar equivalencia después.
5. Emitir aceptación contra el presupuesto vigente o contra otro nuevo aprobado
   expresamente. Mantener separados slot/48 h, cron, aceptación funcional y
   GO de calidad: esta revisión no los da por cerrados.

No se cambió código funcional, datos ni servicios del NAS. No se hizo commit ni
push. Los cambios preexistentes del árbol se conservaron.
