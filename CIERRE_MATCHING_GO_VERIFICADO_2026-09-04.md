# CIERRE — matching GO verificado (2026-09-04)

Ejecución del PROMPT_CORRECCION_CIERRE_DEFINITIVO (revisión 2026-09-03).
Entorno: SOLO R5; `:prod` intacta. Partida `2619618`; HEAD `be6634d`.

## Veredicto exacto

**NO-GO DE CALIDAD** — con la curva de aprendizaje ENTREGADA y medida, según
la regla de alto predeclarada (Public `347edb6`, sellada ANTES de etiquetar y
entrenar): ninguna variante alcanza 0.60/0.60 en desarrollo ⇒ alto sin
tercera vuelta y con el holdout VIRGEN. No es «tests verdes»: es el gate de
calidad el que está rojo, y ahora con una pendiente MEDIDA en vez de un
«hacen falta miles» sin curva:

| Punto de la curva | dev P1 | dev P2 | cobertura top-10 |
|---|---|---|---|
| CE preentrenado (v2, sin fine-tune) | 0.2805 | 0.1744 | 100 % |
| BCE n=163 (cierre anterior) | 0.1598 | 0.1850 | 100 % |
| tier n/a (candidata barata P2-1) | 0.1356 | 0.1197 | 100 % |
| **RankNet n=279 (ganadora P6)** | **0.5097** | **0.5063** | 100 % (v9 = 287 juicios) |

La pendiente 0.16/0.19 → 0.51/0.51 con +116 juicios ciegos y una pérdida de
ranking demuestra que la vía es el etiquetado incremental por rondas de ESTE
circuito (cada ronda: sellado→ciego→re-entrenar→medir), no un volumen a
ciegas. Reapertura: repetir el punto siguiente de la curva (n≈400-450) con la
misma receta; promoción solo si mejora en AMBOS perfiles y en el ciego
incremental, como ya estaba predeclarado.

## Hallazgos de la revisión — estado

### P1-1 — identidad efectiva del modelo: CERRADO (commit `4c0bad7`)

Huella agregada `model_fingerprint` = sha256 del manifiesto canónico (líneas
"sha256  nombre" ordenadas de la allowlist RUNTIME_FILES);
`verify_model_identity` ANTES de construir el motor real; la clave del motor
incluye (modelo, revisión, huella, backend); CLI `python -m
jobhunt_core.cross_encoder fingerprint`. **Mordida roja en el padre**:
sustituir el safetensors del artefacto puntuaba EN SILENCIO; con el fix,
ValueError antes de puntuar (también caza un vocab.txt añadido). La
verificación solo aplica a cargas reales (la inyección de factory para tests
no la paga).

### P1-2 — sello real del universo: CERRADO (commit `11b3e55`)

`_validate_universe_seal` único: esquema, sha del cuerpo, release, model_id,
TODAS las profile_revisions y el CONJUNTO EXACTO de parejas
(vacancy, offer_revision, text_hash) contra las elegibles actuales; cualquier
deriva ⇒ ValueError = examen COMPLETO inelegible (jamás nDCG parcial). Pool
ligado al mismo sello. CLI estricto `seal-universe`/`build-pool`/`evaluate`
con escritura atómica y sha impreso; sin flags de permiso en operación.
**Mordidas rojas en el padre**: universo con vacante retirada, con revisión
derivada y con cuerpo manipulado devolvían `elegible=True`.

### P1-3 — inferencia fuera de la transacción: CERRADO (commit `f1a8c90`)

`evaluate_profile` TRIFÁSICO: F1 transacción corta de lectura (vallas +
snapshot + preparación de misses), F2 inferencia en `asyncio.to_thread` SIN
sesión de BD, F3 transacción corta con FOR UPDATE + revalidación (revisión de
perfil y pesos de política contra el snapshot; deriva ⇒
`descartado_por_deriva`, nada se publica) + persistencia idempotente + outbox
+ movimiento de estado + `on_evaluated` atómico. **Mordida roja en el padre**:
con inferencia bloqueada en una barrera, un `save_profile_revision` con
`lock_timeout=2s` moría por lock; ahora progresa y el resultado rancio se
descarta con el feed intacto (0 filas).

### P1-4 — entrenamiento reproducible versionado: CERRADO (commit `98d3d01`)

CLI `python -m jobhunt_core.train_cross_encoder`: dataset canónico sellado
(sha256), split por grupos sha1(vac)%5, semilla/batch/lr/warmup/max_length
fijos, épocas {1,2,3} por validación de grupos, artefacto content-addressed
`<out>/<huella>/` + TRAIN_MANIFEST.json (hashes de dataset/juicios/perfiles,
versiones de librerías); los pesos NO van a Git; e2e con entrenador stub y
verificación P1-1 del artefacto resultante.

### P2-1 — candidata barata cross_encoder_tier: CERRADO y MEDIDA (commit `55ef4a8`)

`_pair_compatibility` única y compartida (remote_only vs onsite; países
conocidos disjuntos; idioma del título AND/OR; desconocido/ambigua = neutral);
política `xtier-mmarco:v1` predeclarada sin grid:
`score = 50*tier + 49.99*ce_prob`, tier 0 SOLO ante incompatibilidad
demostrada; pair_absolute. **Mordida roja en el padre**: el CE puro ordenaba
primero una oferta anclada a EE. UU.; el tier pone la viable delante.
**Medición dev con cobertura completa (v7, 196 juicios; ronda de cobertura
sellada a6f5b1f/juzgada a8d3ecb): P1 = 0.1356, P2 = 0.1197 — SUSPENDE.**
Taxonomía del fallo (por qué el tier no basta): (1) dominante — restricciones
visibles SOLO en la descripción (campo location dice «Remote», el texto exige
residencia US); (2) desajuste de dominio temáticamente afín (clínico,
compliance, SEO); (3) requisitos de idioma solo en descripción; (4) datos
degradados (locations incoherentes, descripciones vacías); (5) el CE
encuentra afines adyacentes (rel-1 en puestos 5-9) pero no separa el exacto.

### P3-1 — batch de runtime = batch medido: CERRADO (commit `4c0bad7`)

`CE_BATCH_SIZE = 8` (el medido en el benchmark) usado por el camino de
runtime; invariancia de scores a batch/lote probada.

## P6 — supervisión eficiente (predeclarada en Public `347edb6` ANTES de ejecutar)

- Lote ciego estratificado sellado ANTES de etiquetar (Public `004866a`):
  83 ítems (las franjas de 50/perfil no se llenaban al descontar lo ya
  juzgado). Etiquetado por agente aislado: 1×«2», 23×«1», 59×«0»
  (juicios sellados `f6d9c15`). Total dev v8 = 279 juicios.
- RankNet por parejas implementado y versionado (commit `830c976`), tests
  rojos primero; tope de 800 parejas/época fijado desde presupuesto MEDIDO
  (sonda: 21 s/paso en 2 CPUs) ANTES de entrenar, en el manifiesto.
- Curva de aprendizaje (misma receta P1-4; artefactos content-addressed):

| Punto | loss | selección (val grupos) | dev P1 | dev P2 |
|---|---|---|---|---|
| n=163 | BCE | val-MSE 0.077 | 0.1598 | 0.1850 |
| n=279 | BCE | val-MSE 0.068119 · pair_acc 0.615385 | perdedora — sin medir (regla 4 predeclarada) | |
| n=279 | RankNet cap800 | pair_acc **0.667692** (época 1) | **0.5097** | **0.5063** |

- Selección por validación de grupos con **concordancia de parejas** (libre de
  calibración, comparable entre pérdidas): RankNet 0.6677 > BCE 0.6154 ⇒ SOLO
  RankNet recibió ronda de cobertura (P6COB sellada `a7eb096`, juzgada
  `5fc8bb7`: 1×2, 4×1, 3×0) y medición dev formal con cobertura 100 %
  (fórmula del gate, `shadow.metrics._dcg` graduada; juicios v9 = 287).
- Artefactos: BCE `84922bdc…` (épocas 3) · RankNet `3bd81247…` (época 1),
  ambos con TRAIN_MANIFEST completo (dataset/juicios/perfiles sha256,
  versiones de librerías, tope de parejas). Evidencia sellada en Public
  `2863628` (`DEV_P6_RESULTADO_2026-09-04/`).
- Regla de alto aplicada: 0.51/0.51 < 0.60/0.60 ⇒ NO-GO DE CALIDAD, sin
  tercera vuelta, holdout intacto.

## Incidencia operativa: reinicio del equipo a mitad de P6

El equipo se bloqueó y el reinicio vació `/tmp` (scratchpad): se perdieron el
artefacto BCE@n279 recién entrenado, las mediciones locales y los CSVs de
juicios. **Recuperación completa desde la evidencia sellada**, sin re-etiquetar
nada: `juicios_merged_v8.csv` reconstruido BYTE-EXACTO (sha `cda56f8a…`
idéntico al registrado antes del bloqueo) desde los 8 paquetes ciegos + claves;
el corpus dev congelado (1800×2) recuperado de `medicion_ce_v2_full.json`
(sellado) y re-extraído de R5 por id explícito con **deriva 0** (166 juzgados
cotejados contra las hojas selladas); modelo base re-descargado a revisión
clavada con huella idéntica (`0ef69f89…`); dataset reconstruido con contadores
idénticos (279/227/52; parejas 6480/325). Trabajo posterior en ruta
persistente (`trabajo_v8/`), no en `/tmp`.

## Suite, mordidas y correcciones colaterales

- Suite completa en serie tras P2-1: 1030/1031 → el único rojo era
  NO-DETERMINISMO DEL PROPIO TEST (`test_pool_ciego_...`): `vacs` venía de un
  SELECT sin ORDER BY sobre tablas compartidas y `list(vacs.values())[0]`
  cambiaba según qué test corriera antes (verde focal, rojo en suite).
  Diagnóstico por bisección hasta la pareja mínima; fix en la fuente
  (`_setup` ordena por external_id) + el test nombra la vacante que exige en
  el top-3 (commit `be6634d`). No se corrigió el test para aceptar un
  defecto: el defecto ERA del test.
- Suite completa final (HEAD `be6634d`, perfil dev, en serie):
  **1033 passed, 0 failed** (12 m 26 s). Crecimiento neto de la misión:
  1021 → 1033 (+12 tests: P1-1..P1-4, P2-1, P3-1, RankNet).
- `git diff --check` limpio; árbol limpio (solo ficheros del usuario ajenos a
  la misión); pasada adversarial: identidad mezclada (P1-1, motor con clave
  por huella), lock sostenido (P1-3 barrera + lock_timeout), fallback
  silencioso (fallo de modelo ⇒ feed intacto), camino no cableado (xtier en
  POLICY_CATALOG; `--loss ranknet` en CLI; `CE_BATCH_SIZE` usado por
  matching:613), afirmación documental (números del cierre = JSON de
  artefactos sellados, no prosa).

## Invariantes

| Invariante | Estado |
|---|---|
| cosine-baseline:v1 canónica; current_eval_id sin mover en desarrollo | ✓ (feed 5400 punteros cosine, intacto) |
| Holdout cerrado | ✓ (cero consultas; todos los juicios = desarrollo) |
| Receta/versión/policy_id inmutables; cambio ⇒ versión nueva | ✓ (v8: pérdida y tope en el manifiesto; xtier = política nueva) |
| Sin degradación silenciosa | ✓ (P1-3: deriva ⇒ descartado; fallo modelo ⇒ feed intacto) |
| Suites en serie contra BD compartida | ✓ |
| Paquetes sellados con sha256 ANTES de etiquetar; claves fuera del alcance del etiquetador | ✓ |

## Fases condicionales que NO proceden (por diseño)

Desarrollo rojo ⇒ P7 (benchmark NAS ONNX/OpenVINO con paridad declarada) y P8
(examen único → promoción → racha 7/7) no se abren: siguen condicionados a que
una variante pase 0.60/0.60 en desarrollo. El holdout sigue VIRGEN; el
contador de racha sigue en 0/7 sin abrir; `current_eval_id` no se movió
(feed 5400 punteros cosine, intacto).

## Deuda residual (propietario y condición)

1. **Etiquetado incremental** (producto) — siguiente punto de la curva
   (n≈400-450) con este mismo circuito sellado; la pendiente medida
   (0.16/0.19 → 0.51/0.51 con +116) justifica la ronda. Condición de
   reapertura del gate.
2. **Backend NAS** (core) — torch-cpu descartado con evidencia (J1800 sin
   AVX, >4 h/perfil frío vs 3600 s); ONNX/OpenVINO del snapshot clavado, SOLO
   si algo pasa desarrollo (P7).
3. **Claves de sourcing** (usuario) — Adzuna/JSearch/Careerjet-publisher
   pendientes; Jobicy cosechando. Mejora de producto, no palanca del gate.
4. **Careerjet v4 port** (sourcing) — ~20 líneas al llegar la clave.
5. **Cadencia de scoring CE en R5** — si un día se promueve una política CE,
   materializar por lotes con la caché absoluta (misses-only) y el batch
   medido; el coste frío completo del corpus es de horas, no de minutos.
