# ACTA — Carga y congelado de la cohorte de dedup `positive-stratum-v1` (2026-08-27)

> Ejecutado sobre la base REAL `swissjobhunter`, esquema `jobhunt`, con la release
> inmutable `ae7fbf2` (imagen `swissjob-core:dev`; el SHA-256 de `stratum.py` y
> `labels.py` de la imagen coincide con el del árbol de trabajo — verificado antes
> de escribir nada). Todas las cifras de este acta salen de una ejecución, no de una
> lectura de código.

## 1. Insumos extraídos (sin alterar una sola etiqueta)

| Payload | Origen | Tamaño | SHA-256 del payload extraído |
|---|---|---:|---|
| Candidatos | `ESTRATO_POSITIVO_CANDIDATOS_DEV_2026-08-25.md`, bloque `<!-- JSON_CANDIDATOS … -->` | 107 653 B | `9bb894611d1978cb68637341e17150b53a6d79594ffeab39dfb2abcd65987c89` |
| Etiquetas | `ESTRATO_POSITIVO_ETIQUETADO_DEV_2026-08-25.md`, §5 (bloque ```json) | 4 777 B | `9d3f875e8255f65f14e306f50a784c81f7d423bb3d26e1ad4d688db33b72f7bb` |

> **Corrección (auditoría G9 P3-B, 2026-08-27).** La fila de candidatos declaraba
> **106 812 B**. El payload cuyo SHA-256 es el declarado —el que de verdad se cargó—
> mide **107 653 B**; re-extraído del bloque y medido dos veces por dos agentes
> distintos, el hash se reproduce exacto y el tamaño no era el suyo. Se corrige el
> tamaño; el hash, que es lo que ata el acta al dato, estaba bien. La fila de
> etiquetas se reverificó igual y es correcta (4 777 B con `strip()`, hash exacto).

SHA-256 de las actas de origen: candidatos
`26a9b93cf3d031d69e7ef58bacdec5673899de374dfbf3e85fa2f1aeb2369801`, etiquetado
`a13b135d5d5091b2022ce3de906215bc7f7d8df3950b967de750c0e6d6510bac`.

Validación de la extracción (ejecutada, no asumida):

- Ambos payloads parsean como JSON. 222 candidatos (A 0 · B 60 · C 26 · D 60 ·
  E 27 · F 42 · M 7) y 222 etiquetas: **86 `duplicate`, 117 `distinct`,
  19 `ambiguous-owner`** — idéntico a la tabla §1 del acta de etiquetado.
- Se reprodujo la numeración de la hoja (`pair_ids_from_candidates`) y se cotejó
  **par a par contra las 222 filas de la tabla §2 del acta**: 0 desajustes de
  identificadores (`id_a`/`id_b` frente a los prefijos de 8 hex) y 0 desajustes de
  veredicto entre la tabla y el JSON de §5. Ni un `labels sin candidato` ni un
  `candidato sin label`.

## 2. Ensayo previo (transacción revertida)

`load_positive_stratum` se ejecutó dos veces con `ROLLBACK` explícito antes de
tocar nada en firme (el cargador no commitea; el CLI sí, así que el ensayo se hizo
con un script propio que revierte).

1. **Sin `--excluir`**: la guarda de round-trip (G7-N-6) falló fuerte nombrando
   **13 pares** sobre slots legacy RECICLADOS:
   `B-17, B-31, B-57, C-05, C-10, C-17, C-19, E-09, E-14, E-21, E-23, F-26, M-02`.
   La guarda de colisión canónica no disparó: **0 colisiones**.
2. **Con esos 13 en `--excluir`**: 187 cargables, 187 insertados, 0 ya presentes.

Coincide exactamente con el análisis previo del proyecto (187 cargables tras la
exclusión, 13 rechazados por las guardas, 0 colisiones). Cuadre aritmético:
`222 = 187 + 19 (ambiguous-owner) + 3 (sintéticos B-26/27/28) + 13 (reciclado)`.
Tras el rollback la base seguía con 0 cohortes y solo los 779 pares
`seed_duplicate_of` (comprobado con `SELECT`).

## 3. Carga en firme

Ejecutada con la **imagen de release** (sin bind mount de código):

```
docker compose run --rm --no-deps -v <payloads>:/data:ro core-migrate \
  python -m jobhunt_core.shadow.stratum /data/candidatos.json /data/etiquetas.json \
  --excluir "B-17,B-31,B-57,C-05,C-10,C-17,C-19,E-09,E-14,E-21,E-23,F-26,M-02"
```

```json
{"cohorte": "positive-stratum-v1", "total_hoja": 222, "etiquetados": 222,
 "cargables": 187, "insertados": 187, "ya_presentes": 0,
 "excluidos_ambiguous_owner": 19, "excluidos_sinteticos": 3,
 "excluidos_reciclado": 13, "congelada": false}
```

Verificación contra la base:

| Comprobación | Resultado |
|---|---|
| Pares en `labeled_dedup_pairs` con `source='positive-stratum-v1'` | **187** (79 `duplicate` + 108 `distinct`) |
| Refs distintos en la cohorte | 229 |
| Refs que `labels.map_job_refs_to_vacancies` resuelve | **229 / 229** (0 sin slot) |
| Pares mapeables por AMBOS lados | **187 / 187** |
| Pares que el core YA colapsa en la misma vacante | 0 |

El mapeo se comprobó con la **función real** del módulo, no con una consulta
equivalente: su contrato deja fuera del dict los refs sin slot, así que «no falla»
no bastaba como prueba — se contó el dict devuelto.

### Exclusiones y su motivo

- **13 `excluidos_reciclado`** (`--excluir`): sus `job_ref` legacy ya no vuelven a
  la vacante juzgada (`external_id` identifica al SLOT, y el slot fue reciclado con
  `seq+1`), de modo que el par se puntuaría contra otra oferta. **No se editó el
  acta ratificada**; salen contabilizados aparte.
- **3 `excluidos_sinteticos`** (B-26/27/28): registros de test del corpus
  («SkipDedup Collapse Test», Clera), excluidos por §4 del acta de etiquetado.
- **19 `ambiguous-owner`**: reservados a la adjudicación del propietario (§3).

Los 32 pares no cargados **no se re-decidieron**: si algún día se adjudican, van a
una cohorte NUEVA (`positive-stratum-v2`), nunca a esta.

## 4. Congelado

Con `labels.freeze_dedup_cohort` (no un UPDATE a mano):

- `frozen_at = 2026-08-27T15:52:09.217019+00:00`.
- `manifest` jsonb objeto NO vacío con los SHA-256 del pre-registro (actas,
  payloads, `stratum.py`, `labels.py`), la release `ae7fbf2`, los conteos y los
  ids excluidos. Releído desde la base: `jsonb_typeof = 'object'`,
  `manifest->'conteos'->>'cargados' = 187`.
- `labels.dedup_cohort_frozen_at` (lector fail-closed) devuelve ese mismo instante:
  el sello cuenta como real, con acta.
- **Inmutabilidad FILA A FILA verificada en la base real**: un `UPDATE` sobre los pares
  de la cohorte (dentro de un savepoint, revertido) muere con
  `cohorte dedup CONGELADA: positive-stratum-v1 — labeled_dedup_pairs es inmutable
  tras el freeze (core0025)`.

  > **Corrección (auditoría G9 P3-A).** Este acta decía «inmutabilidad **física**» y no
  > lo era: el trigger de `core0025` es `FOR EACH ROW`, y `TRUNCATE` no dispara triggers
  > de fila. El rol dueño (`jobhunt_core`) tenía el privilegio, así que el oráculo
  > sellado se podía vaciar de un golpe saltándose la guarda. Cerrado con la migración
  > **`core0033`**, que añade `BEFORE TRUNCATE … FOR EACH STATEMENT` sobre
  > `labeled_dedup_pairs` **y** sobre `labeled_dedup_cohorts` (truncar la tabla de
  > sellos habría descongelado todo por la puerta de atrás). Regresión:
  > `test_cohorte_congelada_tampoco_se_puede_truncar`.

## 5. Qué mide esta cohorte (informativo, NO vinculante)

Confusión de los 187 pares contra el veredicto actual del core
(`_dedup_cohort_confusion`, la misma consulta que usa el gate):

| tp | fp | fn | tn | sin mapeo |
|---:|---:|---:|---:|---:|
| 20 | 2 | 59 | 106 | 0 |

⇒ precision 0.909, **recall 0.253**. La misma historia que el holdout del
2026-08-24: el detector domina el duplicado exacto intra-fuente y se le escapa el
resto. Esta fila se publica como `dedup_recall::cohort:positive-stratum-v1`,
marcada `[alerta]` con `ok=true` **siempre**: no aprueba, no resetea la racha.

## 6. Diagnóstico del gate — el reloj de los 7 ciclos SIGUE sin arrancar

Ejecutado `gate.gate_status()` tras el congelado:

```json
{"consecutive_ok": 0, "required": 7, "gate_passed": false,
 "last_cycle": "2026-08-26", "holdout_frozen_at": null}
```

La razón, verificada en ejecución:

- `metrics._dedup_rows` y `metrics._labels_ready_row` puntúan **solo**
  `DEDUP_EVAL_COHORT`, que es la constante **`holdout-dedup-2026-08-23`** de
  `labels.py:39` — hardcodeada, sin override por entorno.
- Esa cohorte tiene hoy **0 pares** y **ninguna fila** en `labeled_dedup_cohorts`.
  No es una regresión de esta carga: los ciclos del 2026-08-25 y 2026-08-26 ya
  registraron `labels_ready.details.pares_dedup = 0` y `dedup_recall = -1`
  (centinela `no_data`) con `"cohorte": "holdout-dedup-2026-08-23"`. El holdout que
  narra `ACTA_HOLDOUT_DEDUP_2026-08-24.md` **no está en esta base**.
- La elegibilidad de un ciclo es
  `frozen_at(DEDUP_EVAL_COHORT) is not None and cycle_bounds(cid)[0] >= frozen_at`.
  Con `frozen_at = NULL` para el holdout, **ningún** ciclo es elegible, por verdes
  que salgan sus gates. Congelar `positive-stratum-v1` no cambia ese corte: es otra
  `source`.
- El otro brazo de `labels_ready` **ya está satisfecho**: `perfiles_ok = 2`
  (≥ `LABELS_MIN_FROZEN_SETS = 2`), con tres sets congelados de 31/30/30 juicios y
  uno excluido por usuario inactivo. Lo único que mantiene `labels_ready` en rojo
  son los umbrales de dedup: `pares_dedup 0 < 50` y `pares_mapeables 0 < 20`.

**Conclusión:** la cohorte del estrato positivo queda cargada, congelada y
auditable, y cierra el requisito de «freeze real con manifest no vacío» como
artefacto de development. **No arranca el contador de 7 ciclos.** Para eso falta
cargar y congelar la cohorte que el gate puntúa —`holdout-dedup-2026-08-23`— con
pares realmente ciegos: por construcción no puede ser este estrato, cuyo propio
acta declara que el etiquetador vio el holdout y que alimenta EXCLUSIVAMENTE
development. Con ≥50 pares (≥20 mapeables) en esa cohorte y su freeze,
`labels_ready` pasa a verde y el primer ciclo elegible será el primero cuya ventana
empiece después de ese `frozen_at`.

## 7. Rastro reproducible

- Payloads, manifiesto y scripts de ensayo/verificación:
  `scratchpad/cohorte/{candidatos,etiquetas,manifest}.json`,
  `{dryrun,verify,freeze,gate_check}.py` (efímeros; el manifiesto persiste dentro
  de `labeled_dedup_cohorts.manifest`).
- Escrituras realizadas: **solo** el `INSERT` del cargador y el `UPDATE` del
  congelado. Todo lo demás fueron `SELECT` o transacciones revertidas. No se
  reinició, recreó ni reconstruyó ningún servicio; no se tocaron `.env`, compose ni
  el NAS. Los contenedores efímeros se ejecutaron con `--rm --no-deps`.
