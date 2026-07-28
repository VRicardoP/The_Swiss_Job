
### Revisión EXTERNA de la Fase B + paso de fase (2026-07-27/28)
Primera revisión externa desde A-09 (tramo `d8fc653..HEAD`): **REQUEST CHANGES con 8
hallazgos (4 P1 + 4 P2), TODOS confirmados y corregidos** con regresión del escenario del
revisor (commits `06ea66c` + `fc98f0d` + infra `21252ef`): suite aislada en BD desechable de
sesión, gate `labels_ready` (DoD del oráculo como precondición), run_cycle sin medir en
caliente, ciclos sellados inmutables, cadencias de proyector/entrega cada 5 min, entrega
sombra REAL (`core0009`: `shadow_inbox` — 100 eventos delivered verificados), lag del outbox
por edad de evento + gate `outbox_dead`, intención de lote durable, heartbeat de liveness.
**Revisión de paso de fase: NO-GO a Fase C** (correcto — evidencia operativa): workers legacy
recuperados con restart (7 días caídos por DNS sin política), cosecha reparada (2 migraciones
legacy pendientes), oráculo congelado (2 sets contables + persona de evaluación vía API
legacy, `b513c55`+`d6cb4d1`), exclusión de inactivos compartida proyector↔métricas.
**Estado real del gate (§6)**: infra en verde (perdida=0, outbox, latencia); PENDIENTE de
acumulación (pares dedup mapeables) y de **CALIDAD del matching** — ndcg honesto 0.29/0.0 vs
umbral 0.60: el siguiente paquete de trabajo es el diagnóstico/mejora del ranking, no el
harness. Sin fecha fiable de paso; contador 0/7.
