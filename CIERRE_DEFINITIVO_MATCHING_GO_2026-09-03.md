# CIERRE DEFINITIVO — matching hasta GO (2026-09-03)

Ejecución del PROMPT_CIERRE_DEFINITIVO. Entorno: SOLO R5; `:prod` intacta
(`ba69dbb887d0…` verificada en cada carga). Partida `6575dc8`.

## Veredicto exacto

**Un único bloqueo externo probado: FALTA DE ETIQUETADO INDEPENDIENTE en el
volumen que exige un ranker aprendido.** No se emite `APTO PARA INICIAR
RACHA` ni `GO`: el desarrollo está rojo tras agotar la vía sancionada, y el
rojo ya NO es atribuible al instrumento (coherente), a la producción (vallas
pair_absolute), al contrato de evaluación (universo/INELEGIBLE) ni a una
hipótesis sin probar — está medido con cobertura completa del top-10:

| Candidata | P1 | P2 | Criterio ≥0.60/0.60 |
|---|---|---|---|
| xenc-mmarco:v2 (preentrenado, sigmoid_t4) | 0.2805 | 0.1744 | ✗ |
| xenc-mmarco:v3 (fine-tuned — la ÚNICA repetición sancionada) | 0.1598 | 0.1850 | ✗ |
| referencia v4 híbrida (coherente; no promovible: relativa) | 0.4388 | 0.3657 | ✗ |

Se entrega el paquete completo, no una hipótesis: pipeline de entrenamiento
determinista y reproducible, artefacto sellado, 4 rondas de etiquetado ciego
selladas (184 juicios dev v6), benchmarks local/NAS y contrato de examen
implementado y probado. Bloqueo secundario documentado: el NAS (Celeron J1800
sin AVX) tiene **incapacidad física demostrada** para el backend torch-cpu
(>4 h sin completar un perfil frío frente a un presupuesto de 3600 s); la vía
ONNX/OpenVINO (el snapshot clavado la incluye) queda condicionada a que algún
modelo pase desarrollo.

**Qué necesita el propietario aportar para reabrir**: volumen de etiquetado
independiente de otro orden (miles de pares, no cientos) o una fuente de
señal supervisada equivalente (p. ej. feedback real de uso acumulado); con
eso, re-entrenar con la MISMA receta predeclarada y repetir el circuito
(desarrollo → validación ciega nueva → examen único → promoción → 7/7).

## Invariantes — tabla y evidencia

| Invariante | Estado | Evidencia |
|---|---|---|
| cosine-baseline:v1 canónica; R5/:prod/BD sin alterar en desarrollo | ✓ | Consulta inicial y final: 5400 `current_eval_id` todos cosine; candidatas evaluadas SOLO en sombra/local; deploys solo de código; `:prod` id intacto |
| Holdout cerrado | ✓ | Cero consultas a sets congelados en toda la sesión; juicios usados = SOLO desarrollo (sha v5 `50bb3ed9…`, v6 en el paquete) |
| Receta inmutable; cambio ⇒ versión | ✓ | v1 (sigmoid) → v2 (sigmoid_t4) → v3 (fine-tuned, `train_data_sha256`); `ensure_policy` rechaza redeclarar; huellas de artefactos selladas |
| Sin PII a servicios externos; reranker local | ✓ | Modelo local (NAS/imagen); la consulta usa SOLO intención declarada (roles/skills/idiomas/ubicaciones/remote), jamás el CV completo |
| Sin fallback silencioso; fallo ⇒ feed intacto + error | ✓ | `test_fallo_del_modelo_deja_el_feed_intacto` (OOM simulado: rollback, 0 evals, feed previo) |
| Rojas antes del fix; suite en serie; build limpio; rollback probado | ✓ | Mordidas por fase (abajo); suites 1015→1020→final; builds en worktree con sha verificado en ambos extremos; `test_promover_y_rollback_conservan_el_feed` |
| Migraciones publicadas intactas | ✓ | Ninguna migración nueva necesaria (cabeza `core0040` sin cambios) |

## Fase 1 — P1 de producción CERRADO (commit `272c85c`)

`_ALGORITHM_PAIR_ABSOLUTE` en código (no booleano de receta): cosine y
cross_encoder absolutos; híbridas/rerank relativas; desconocido = no
promovible. Vallas: `declare_active_policies` rechaza que la canónica
RESULTANTE por orden productivo sea relativa (G1/G2 + canonicidad = la
mezcla histórica jamás se sirve); `evaluate_profile(move_current=True)` falla
cerrado ANTE un bypass, antes de tocar `profile_vacancy_state` (feed bueno
intacto, error observable). Sombra relativa permitida; una ABSOLUTA tras
G1→corpus nuevo→G2 sirve exactamente el cálculo G2 y la pareja vieja conserva
su score. Las 4 regresiones nacieron ROJAS; 5 tests que promovían relativas
se alinearon al contrato. Familia G reclasificada como relativa
(`lexical_score/max(lote)`) en los artefactos.

## Fase 2 — cross_encoder local CERRADO (commits `ee10266`, `7db580e`, `fffcdf9`)

- Modelo `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` clavado a
  `1427fd652930e4ba29e8149678df786c240d8825`, checksums de los 6 artefactos
  del runtime y huella agregada `0ef69f89…` en la receta; preloadado y
  verificado sha-a-sha en el volumen `core-hf-cache` de R5 (sin dependencia
  de red: HF_HUB_OFFLINE probado).
- `target_roles` en el contenido versionado (cota central 10×120), DTO con
  preservar-si-omitido (C-3 intacto), proyector lo preserva SIEMPRE (el CDC
  nunca lo trae), round-trip probado y mismo `text_hash` (sin re-embeber).
- Consulta v1 por rol (intención declarada, truncados fijos); documento v1
  título+ubicación+descripción. Score absoluto = activación fija del máximo
  logit por roles. **Defecto real cazado**: la sigmoide plana empataba el
  top de consultas anchas en 99.99/100.00 bajo NUMERIC(6,2) (el feed habría
  ordenado por vacancy_id — P2 con 10/10 empatados) ⇒ regresión roja +
  `sigmoid_t4` (monótona) como v2.
- Caché por identidad absoluta: cache-hit sin invocar el modelo, lote mixto
  puntúa SOLO misses (G2 = 1 inferencia), identidad nueva re-puntúa y A→B→A
  reutiliza; invariancia a batch/lote; motor una vez por proceso e
  inyectable. Todo con tests (9 de integración + 8 de unidad + matrices).

## Fase 3 — benchmark y desarrollo (paquete en `DEV_XENC_2026-09-03/`)

- Local (2 CPUs): batch 8 = 240 s/1800 docs, RSS 1.19 GB. NAS: humo OK (83 s
  carga), materialización fría **abortada a las >4 h** — presupuesto
  incumplido con evidencia (J1800 sin AVX).
- Preentrenado v2: dev con cobertura COMPLETA (2 rondas ciegas selladas,
  18+32 juicios) = 0.28/0.17 — suspende (diagnóstico del etiquetador: tops
  temáticamente afines pero EE. UU./Canadá o idiomas no acreditados).
- Segunda etapa sancionada (predeclarada y sellada ANTES de entrenar,
  `ee7e754`): pool ciego adicional de franjas 1-20/21-90/91-390; 163 pares;
  split por GRUPOS de vacante (134/29); seed fija; épocas {1,2,3} por MSE de
  validación (eligió 3: 0.125→0.096→0.077); artefacto sellado
  (`d96d5de3…`, `train_data_sha256 50bb3ed9…`). Medición única con cobertura
  completa (ronda CE3, 21 juicios): **0.16/0.19 — suspende**. El MSE mejoraba
  aprendiendo la tasa base (scores comprimidos 0.46-0.52): 163 ejemplos son
  un orden de magnitud menos de lo que un cross-encoder necesita.

## Fase 4 — contrato de examen CERRADO (commit `299c067`)

`build_universe_manifest` (parejas elegibles + modelo + revisiones +
corpus_generation + release, sellado sha256); examen LIGADO al universo (la
oferta intrusa de score alto ⇒ `fuera_de_universo` ⇒ INELEGIBLE — test
mandado en verde); cobertura 100 % del top-10 o INELEGIBLE con lista exacta
(un no-juzgado YA NO es relevancia 0); `build_blind_pool` determinista
(unión top-K baseline+candidata, juicios aplicables separados). Mordida:
TypeError en el padre.

## Fase 5-7 — no procedieron, por diseño

Desarrollo rojo ⇒ sin congelación de candidata, sin clave al evaluador, sin
holdout (sigue VIRGEN), sin promoción, sin racha (contador 0/7 no abierto).
El ensayo mecánico de promoción/rollback con cambio de corpus está PROBADO en
tests transaccionales para políticas absolutas (feed == cálculo, restitución
exacta).

## Identidades, suites y mordidas

- Commits: `272c85c` (F1) → `ee10266` (F2) → `1523dc2` (golden target_roles)
  → `693fefc` (test API) → `299c067` (F4) → `7db580e` (sigmoid_t4/v2) →
  `fffcdf9` (receta v3). Public: predeclaración FT `ee7e754`; 3 paquetes de
  etiquetado sellados ANTES de etiquetar (`d0b154e`, `3a474f0`, `a9a20b5`) y
  sus juicios (`f9a7134`, `719504c`, `20b0759`); evidencia `02cab5b`.
- Suites completas en serie: 1015 (F1+F2) → 1020 (F4+v2) → **1021 passed**
  final (12m20s, HEAD fffcdf9); focales por fase verdes. Un flake no
  reproducido quedó anotado.
- Mordidas ejecutadas contra el padre por fase: declare/valla relativa
  (rojas antes del fix), TypeError contrato F4, empate 99.99 de la sigmoide
  plana, procedencia v3; más las semánticas de F-anteriores.
- R5: release `693fefc` en los 3 servicios (build limpio, tar sha
  `5c358a17…` idéntico); imagen `299c067` transferida y verificada
  (`2dbf81e3…`), desplegable; activas {cosine-baseline:v1 canónica,
  hybrid-rrf:v4 sombra}; feed 5400 punteros cosine, intacto de punta a punta.

## Deuda residual (propietario y condición)

1. **Volumen de etiquetado** (propietario: producto) — miles de pares o señal
   de feedback real; condición de reapertura del circuito completo.
2. **Backend NAS** (core) — benchmark ONNX/OpenVINO como receta nueva SOLO si
   un modelo pasa desarrollo; torch-cpu descartado con evidencia.
3. **Claves de sourcing** (usuario) — Adzuna/JSearch/Careerjet-publisher
   pendientes; Jobicy ya cosechando (mejora de producto, NO palanca del gate).
4. **Careerjet v4 port** (sourcing) — ~20 líneas al llegar la clave.
