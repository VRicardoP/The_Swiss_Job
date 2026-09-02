# CIERRE — matching hasta GO (2026-09-02, sesión de cierre verificable)

Ejecución del PROMPT_CIERRE_MATCHING_HASTA_GO_2026-09-02: cierre de los cuatro
hallazgos de la revisión externa (P1-A..P1-D), etiquetado ciego de M01..M03,
recomputación completa de desarrollo bajo v4 con la implementación de nDCG del
gate, y veredicto. Entorno: SOLO R5. Producción intacta (`:prod` = `2c19837`,
imagen `ba69dbb887d0`, verificada por inspect).

## Veredicto (vocabulario exacto del protocolo)

**`NO-GO DE PROMOCIÓN`.**

El desarrollo bajo v4 —con la implementación de nDCG de `shadow.metrics`
(ganancia graduada `(2^rel−1)/log2(pos+2)`, IDCG sobre el conjunto juzgado,
no-medible explícito)— da **nDCG@10 P1 = 0.440331 y P2 = 0.537710**, ambos por
debajo del criterio inmutable ≥ 0.60. Los 0.557/0.617 publicados en el cierre
anterior salían de una fórmula lineal (`rel/log2(rank+1)`) que NO es la del
gate; con la implementación mandada, el desarrollo no pasa.

En consecuencia, y por los criterios inmutables del protocolo:
- v4 queda **en sombra** (activa, append-only, sin gobernar el feed).
- El examen de sets congelados **NO se ejecuta** (no hay promoción que examinar).
- **No** hay preview de gates ni comienzo de racha: NO se emite
  `APTO PARA INICIAR RACHA`, NO se emite `GATE-SOMBRA SUPERADO`, NO se emite `GO`.
- No se leyó ni tocó ninguna cohorte congelada; el holdout de matching sigue
  virgen para la política que algún día llegue a examen.

Sí se sostiene, de la sesión anterior: dedup en su examen único final
TP=7·FP=0·FN=0 (precisión 1.0 / recall 1.0), sin cambios de comportamiento en
esta sesión (el único toque a `dedup.py` fue corregir un comentario).

## Estado inicial verificado (antes de tocar nada)

- HEADs: SwissJob `5544523`, Public `c748769` ✓. `git diff --check` limpio ✓.
- Alembic core: única cabeza `core0040` ✓.
- R5: release `54564bf` en los 3 servicios; activas exactamente
  {cosine-baseline/v1 canónica, hybrid-rrf/v3 sombra} ✓.
- Suite completa AL FINAL de los cambios: **949 passed** en serie (11m40s).

## P1-A — receta persistida (CERRADO)

**Confirmación del defecto en datos**: `SELECT weights` en R5 mostró v2 y v3
con el MISMO JSON `{"algorithm": "hybrid_rrf_v2"}` — peso 0.25, rrf_k=60 y
versión de consulta léxica solo en el binario; v2 (nacida con 1.15) y v3
indistinguibles por sus filas.

**Cierre** (commit `9c6dbab`):
- `hybrid-rrf/v4` con receta canónica completa persistida en `weights`:
  `{"algorithm": "hybrid_rrf", "lexical_query": "v2", "lexical_weight": 0.25,
  "rrf_k": 60}`.
- `_validated_recipe()`: el evaluador VALIDA la receta contra la implementación
  (claves exactas, versión de consulta implementada, rrf_k soportado, peso
  finito y positivo) y DERIVA de ella el comportamiento; receta inválida ⇒
  `ValueError` antes de evaluar nada (test: cero filas insertadas).
- `_hybrid_candidates_sql(peso)`: constructor ÚNICO del SQL — v3 (legacy) y v4
  (receta) lo comparten ⇒ equivalencia byte a byte POR CONSTRUCCIÓN; guard de
  derivación contando patrones (lección `_EXACT_INTRA_HISTORY_SQL`).
- `ensure_policy` ya rechazaba redeclarar una versión con weights distintos
  (test existente `test_policy_version_rejects_different_weights`); golden
  nuevo fija v1/v2/v3 intactas (`test_la_receta_v4_reconstruye_...`).
- Cada eval de v4 lleva `scores.recipe` — auditable por fila.
- **Mordida contra el padre**: los tests nuevos fallan en `5544523` con
  `AttributeError: HYBRID4_POLICY_WEIGHTS` / `_validated_recipe` — la receta
  no era reconstruible desde datos, que es la causa exacta documentada.

**Equivalencia v4↔v3** (exigida ANTES de usar juicios nuevos):
- Fixtures: `test_v4_reproduce_exactamente_a_v3_...` — mismas filas, mismo
  orden, mismos scores; v4 con `recipe`, v3 sin ella.
- R5 contemporánea (`DEV_METRICAS_V4_2026-09-02/equivalencia_contemporanea.json`):
  en la imagen desplegada, el SQL derivado de la receta de la fila v4 es
  byte-idéntico al de v3 y la función léxica es la misma; ambos SQL ejecutados
  en el mismo instante devuelven **las mismas 1800 filas con los mismos scores
  crudos** para P1 y P2 (`identico: true`).
- Filas persistidas: v4 (hoy) vs v3 (ayer) difieren en feed_n (1800 vs
  1814/1811) y ±0.01 en algunos scores. Es DERIVA DE CORPUS entre instantes de
  materialización (el corpus creció ~66 ofertas; `eval_key` no incluye el
  estado del corpus y las filas viejas de v3 son append-only e inamovibles),
  no diferencia de algoritmo — los top-10 coinciden en UUIDs y orden en ambos
  perfiles y M01..M03 conservan sus rangos 4/4/6.

## P1-B — clave auditable y paquete autocontenido (CERRADO)

**Confirmación**: M01 = vacante juzgada `unsure` DOS veces en DEV-RANKING v1
(R021 para P1 y R050 para P2 — la misma vacante muestreada para ambos
perfiles); M03 = `unsure` R051 (P2). Mi afirmación previa «9/10 juzgados, el
rango 4 sin juzgar» era FALSA: el rango 4 de P1 estaba juzgado (`unsure`) y mi
consulta lo confundía con no-juzgado porque el CSV excluía los unsure. Solo
M02 era genuinamente nueva.

**Cierre** (Public `326de2c` sellado + `385474c` juicios):
- Clave privada versionada `holdout_artefactos_2026-08-23/clave_top10_v1_2026-09-02.json`
  (sha256 `009c96f7d310…2f8a64`), reconstruida de R5 con, por Mxx: perfil y
  revisión (UUID), vacante y offer_revision (UUID), eval_id/eval_key,
  source_listing (fuente+external_id), política (UUID/nombre/versión/weights),
  receta efectiva, release `54564bf` + image id, generación de corpus (263361),
  rango y score. **Cada join resolvió exactamente una fila**
  (`n_evals_hist=1`, `n_listings=1` en la propia consulta); rangos
  verificados contra el feed real (4/4/6).
- Paquete autocontenido: `RUBRICA.md` = copia LITERAL de la rúbrica original;
  `INSTRUCCIONES.md` ya no remite fuera del directorio; `MANIFIESTO.md` con
  sha256 de hoja/instrucciones/plantilla/rúbrica Y de la clave, **sellado y
  comiteado ANTES del etiquetado** (`326de2c`).
- Historia append-only: los juicios de DEV-RANKING v1 no se tocaron; los
  nuevos forman DEV-RANKING-TOP10 v1 con su manifiesto. El `unsure` de R050
  (P2 sobre la vacante de M01) **permanece excluido** — M01 solo re-juzga el
  par (P1, vacante).

**Etiquetado ciego**: agente NUEVO de contexto limpio, con acceso únicamente
al directorio del paquete (sin prompt, clave, repo, métricas, rangos ni
juicios previos). Resultado: **M01=0, M02=0, M03=0** (criterio: restricción
geográfica/visado EE. UU. y bilingüe EN/FR no declarado). sha256 de
`juicios.txt`: `0dd890d9ba89…31dfa0d0` → commit `385474c` antes de desenmascarar.
Fusión (dev v2): 78 juicios (38×0/36×1/4×2); permanecen `unsure` y excluidos:
R019 (P2, b3a2b6a9…) y R050 (P2, b16da054…).

## P1-C — valla de canonicidad (CERRADO)

**Cierre** (commit `9c6dbab`): en `evaluate_profile`, mover `current_eval_id`
exige que la política siga siendo la canónica, verificado EN LA MISMA
transacción que la escritura con `FOR SHARE` sobre la fila canónica; el flip
(`declare_active_policies`) es UN solo UPDATE sobre todas las filas de
`scoring_policies`, con lo que toma locks de fila que se serializan con la
valla: o el movimiento en vuelo termina antes del flip, o ve el canónico nuevo
y se aborta (la evaluación queda registrada; solo el feed no se mueve).

- **Regresión de intercalación** `test_un_worker_pre_flip_no_puede_restaurar_el_feed_antiguo`:
  worker A lee «cosine canónica» (transacción cerrada) → promoción atómica a
  {v4} → worker B materializa v4 → A retoma con su decisión caducada.
- **Mordida contra el padre** (variante temporal con flip por SQL crudo, para
  no depender del seam nuevo): en `5544523` falla con
  `assert True is False` en `moved_current` — el worker caducado SÍ restauraba
  el feed antiguo. Con el fix, la misma variante pasa.
- Operativa documentada: pausar encolado → drenar → flip atómico → reevaluar →
  verificar `policy_id` de todos los `current_eval_id` → rearmar. La quiescencia
  complementa la valla; la regresión existe por si un worker se salta el
  procedimiento.

## P1-D — autoridad única sobre la canonicidad (CERRADO)

**Cierre** (commits `9c6dbab` + `7b5c3b8`):
- `bootstrap_policy_catalog(session)`: asegura las FILAS del catálogo (cosine
  v1, hybrid v1/v2/v3/v4 con sus recetas) SIN tocar activación
  (`ensure_policy(active=None)`: crea inactiva, jamás actualiza el flag).
- `declare_active_policies(session, ids)`: la ÚNICA función que cambia el
  conjunto activo — atómica (un UPDATE), validada (conjunto exacto o aborta) y
  serializada con la valla P1-C. CLI operador: `python -m jobhunt_core.policy_ctl
  status|declare name:version …`.
- `scripts/nas_deploy_rehearsal_r5.sh`: el one-shot ya NO declara activaciones;
  bootstrap + trampa (v1/v2 activas ⇒ RuntimeError; v3 activa ⇒ AVISO con la
  vía de salida) + primera-vez de entorno vacío ⇒ línea base cosine.
- `jobhunt_core/shadow/DEPLOY_NAS.md` §7: eliminado el bloque que desactivaba
  cosine y REACTIVABA hybrid-rrf/v1 (0.098/0.000) en cada despliegue; nuevo
  §7.1 con el comando único.
- **Regresiones**: `test_el_redeploy_no_cambia_la_canonicidad` (redeploy
  preserva {cosine, v4-sombra}, {v4} y {cosine} post-rollback; jamás reactiva
  v1/v2/v3) y `test_declarar_un_conjunto_activo_invalido_aborta`.
- Documentos desfasados corregidos: `dedup.py` comentario 4/60 → 3/60 (los
  tests prueban 3/60); `CIERRE_POST_ETIQUETADO…` líneas de deploy/rollback
  («v2 activa» → v3; rollback vía policy_ctl).
- Verificado EN VIVO: el deploy de `9c6dbab` preservó {cosine, v3} e imprimió
  el AVISO; la transición v3→v4 sombra se hizo después, explícita:
  `policy_ctl declare cosine-baseline:v1 hybrid-rrf:v4` → conjunto exacto
  confirmado y las 5400 filas de `current_eval_id` (3 perfiles) siguen
  referenciando cosine-baseline.
- Incidente menor del primer deploy: backticks en dos líneas de comentario del
  one-shot eran sustitución de comandos para el shell del NAS (error espurio
  «python: command not found»; solo comentarios afectados, python resultante
  válido). Corregido en `7b5c3b8` y verificado por checksum en el NAS.

## Recomputación completa de desarrollo (fórmula del gate)

Insumos con hash en `/home/lothar/Public/DEV_METRICAS_V4_2026-09-02/`
(`juicios_merged.csv` 97b6bec6…, `excluidos.json` 04212d95…,
`recompute_gate_v3.json` bbbbbbb3…, `recompute_gate_v4.json` 08e0a26c…,
`equivalencia_contemporanea.json` 536f289d…).
Implementación: `jobhunt_core.shadow.metrics._dcg` importada, no reimplementada.

| Perfil | feed_n (v4) | DCG | IDCG | **nDCG@10** | rel-2 fuera del feed | unsure excluidos |
|---|---|---|---|---|---|---|
| P1 | 1800 | 3.436965 | 7.805419 | **0.440331** | 0 | 0 |
| P2 | 1800 | 4.197048 | 7.805419 | **0.537710** | 0 | 2 (R019, R050) |

- Los valores bajo v3 son idénticos (mismos top-10 y relevancias) — v4 no
  cambió el ranking, cambió su auditabilidad.
- Los tres juicios nuevos (todos 0) no alteran DCG ni IDCG: el descenso desde
  0.557/0.617 es ÍNTEGRAMENTE el cambio a la fórmula del gate. M02/M03 sí
  entran en el DCG de P2 (rangos 4 y 6 con rel=0 confirmado, ya no
  «sin juzgar»).
- Top-10 con relevancias, feeds completos y primera divergencia v3/v4
  persistida: en los JSON archivados.

## Deudas residuales (medidas, no escondidas)

1. **Dev-dedup TP=0/FP=0/FN=5** — diagnóstico ejecutado (solo lectura, R5):
   los 5 pares `duplicate` del dev tocan vacantes ARCHIVADAS (4/5 por ambos
   lados) y TODOS los generadores (exact/KNN/léxico) excluyen archivadas por
   diseño (`archived_at IS NULL` en el corpus de generación) ⇒ los FN son
   estructurales del muestreo del dev sobre histórico, no fallo del detector
   en flujo vivo: 4/5 pares serían triviales si vivieran (dist coseno ≤0.0032,
   trgm 1.000); el difícil real es D14 (variante de título truncado, dist
   0.31/trgm 0.49). Sin cambio de código: no hay regla acotada con precisión
   ≥0.95 demostrada y la exclusión de archivadas es cota deliberada. Riesgo
   residual: un duplicado vivo↔archivado es invisible — impacto acotado porque
   el feed también excluye archivadas.
2. **Cola de candidatos pendientes** — 190.091 `pending` + 1.514 `rejected`;
   40 MB totales; scan completo 137 ms (2.565 buffers, EXPLAIN ANALYZE
   archivado en sesión) ⇒ **el índice parcial NO se justifica por EXPLAIN**.
   El 97,8 % de pendientes toca alguna vacante archivada; el crecimiento es un
   pico único de la ola del backfill léxico (186.978 en la semana del
   2026-08-31; <4k/semana antes), no orgánico. Sin efecto medible sobre gates,
   racha, backup u operación ⇒ deuda tolerada con medición; la vía preferida
   (excluir archivadas del flujo operativo preservando evidencia) queda
   propuesta, sin DELETE masivo.

## Identidades

- SwissJob: `5544523` → `9c6dbab` (P1-A/C/D + tests; suite 949 passed) →
  `7b5c3b8` (fix backticks del deploy). Public: `c748769` → `326de2c` (sellado
  del paquete y la clave ANTES de etiquetar) → `385474c` (juicios M01..M03).
- Imagen R5: build limpio en worktree de `9c6dbab` (`RELEASE_SHA` horneado),
  tar sha256 `529d53f8…99e6` igual en origen y destino; cargada como
  `swissjob-core:9c6dbab` = `:r5-cycle` (id NAS `2f186de728f5…`); los 3
  servicios R5 con la MISMA imagen y `RELEASE=9c6dbab` verificado ejecutando.
  `:prod` intacta (`ba69dbb887d0…` = `2c19837`).
- Nota: el id local del build (`3de70f86…`) difiere del id tras `docker load`
  en el NAS (`2f186de7…`) por el snapshotter (digest OCI vs config legacy);
  la identidad de bytes la da el sha256 del tar, idéntico en ambos extremos,
  más la verificación ejecutando.
- Políticas R5: activas exactamente {cosine-baseline:v1 (canónica),
  hybrid-rrf:v4 (sombra, receta completa persistida)}; v1/v2/v3 historia
  inactiva e inmutable.

## Qué haría falta para reabrir el camino a promoción

El bloqueo ya no es de auditabilidad (P1-A..D cerrados) sino de CALIDAD medida:
0.44/0.54 contra 0.60. Cualquier cambio de fórmula/consulta/pesos es una
**v5** con receta nueva y policy_id nuevo, medida contra el dev v2 (y con
etiquetas frescas si se re-muestrea), sin tocar sets congelados. El examen
único de holdout sigue reservado para la primera política que pase desarrollo.
