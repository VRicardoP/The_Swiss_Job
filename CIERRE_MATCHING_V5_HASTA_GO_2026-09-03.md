# CIERRE — v5 hasta GO (2026-09-02/03)

Ejecución del PROMPT_V5_MATCHING_HASTA_GO_2026-09-02. Entorno: SOLO R5;
`:prod` intacta (`ba69dbb887d0…` = `2c19837`, verificada por inspect en cada
carga). Sets congelados: **sin abrir** en toda la sesión.

## Veredicto (vocabulario exacto)

**`NO-GO DE PROMOCIÓN`.**

Ninguna configuración v5 alcanza el criterio inmutable nDCG@10 ≥ 0.60 en ambos
perfiles sobre el desarrollo honesto (113 juicios, fórmula del gate). La mejor
—familia F: bono de compatibilidad explícita sobre la aditiva— da
**P1 = 0.5630 / P2 = 0.6202**. No se congela receta v5, no hay paquete de
validación, no se abre el examen, no hay racha. La política v5 NO existe como
fila: el MECANISMO (rerank por receta) está implementado, probado y en la
imagen, sin nada que lo active.

**Primer gate real que bloquea**: calidad de desarrollo de P1. Su techo es
estructural: con sus juicios (2×rel-2, 28×rel-1, 25×rel-0) un top-10 PERFECTO
de rel-1 da 0.582 < 0.60 — P1 solo pasa con ≥1 rel-2 en el top-5, y sus dos
rel-2 no son separables de los rel-1 con señales deterministas (medido en
3 rondas predeclaradas de ablaciones; familia de fallo registrada en
`DEV_ABLACION_V5_2026-09-02/RESULTADOS.md`).

**Única siguiente acción que no contamina el examen**: engordar el corpus del
nicho de P1 (censo: solo 12 ofertas vivas remotas global/compatibles de su
nicho entre 27 800; 5 de ellas fuera de su feed). Ya en marcha: jobicy
reactivado (104 ofertas/run, 29 del nicho, verificado en vivo); pendientes de
credenciales del usuario: Adzuna (`app_id/app_key`, captcha impide
automatizar), JSearch (RapidAPI key), Careerjet (alta de publisher + port del
conector a la API v4 — la legacy responde 401 a usuarios nuevos). Con corpus
nuevo: re-muestrear desarrollo, y solo si el dev pasa, validación ciega
NUEVA → examen. Un rerank LLM/cross-encoder queda ahora LEGITIMADO por
protocolo (las señales deterministas están formalmente agotadas y medidas),
condicionado a coste/latencia/reproducibilidad/fallback.

## Fase 0 — línea base

HEADs e3524bf/373415a ✓ · Alembic core0040 ✓ · R5 release 9c6dbab, activas
exactamente {cosine-baseline:v1 canónica, hybrid-rrf:v4 sombra} ✓ · suite
baseline 949 passed ✓ · v4 reprodujo 0.440331/0.537710 (recompute archivado).

## Fase 1 — evaluador canónico (CERRADO)

`jobhunt_core/dev_eval.py` + `matching.shadow_feed` (definición ÚNICA del feed
sombra: revisión vigente del perfil, revisión canónica vigente de la vacante,
vacante activa, modelo indicado; duplicado ⇒ error). Importa `_dcg`/`NDCG_K`
de shadow.metrics — no copia la fórmula. Falla cerrado (juicios ambiguos,
vacante inexistente, política inexistente, varios modelos activos);
`unsure` explícito; IDCG=0 ⇒ `no_medible`; manifiesto con release, receta,
revisiones, corpus_generation y sha256 de insumos; salida determinista
(probado: dos corridas idénticas). Regresión clave: la fórmula lineal da OTRO
número y el test exige el del gate. **Mordida**: en el padre el módulo no
existe (ImportError). Commit `4f511aa`.

Reproducción en R5 post-deploy: el manifiesto delató deriva de corpus
(generación 263361→264013 durante la noche; v4 activa-sombra acumuló evals
nuevas por el beat: feed 1800→1814, P2 0.538→0.413 por no-juzgados nuevos en
su top). Los números sellados del 2026-09-02 son los JSON archivados con SU
generación — exactamente el trabajo del manifiesto. Evidencia:
`DEV_METRICAS_V4_2026-09-02/dev_eval_v4_post_deriva_2026-09-03.json`.
Lección operativa registrada: el feed de una política sombra ACTIVA acumula
con la deriva del corpus; toda medición debe citar su generación.

## Fase 2 — preferencias restauradas (CERRADO)

**Causa raíz**: la captura solo traía user_id/title/cv_text/skills y el
proyector solo proyectaba title/cv_text/skills; `languages/locations/
experience_years/salary_min/max/remote_pref` se perdían en la costura CDC.
**Diff legacy→core medido en R5** (antes → después del resync):

- P1: languages []→["English","Spanish","Japanese","French"]; locations
  []→["Remote","Switzerland","Geneva","Zurich","Spain"]; remote_pref
  null→remote_only; salary 45000..85000.
- P2: languages []→["English","Spanish","Japanese"]; locations []→[Remote,
  Anywhere, Worldwide, Europe, Switzerland, Spain, Valencia]; remote_pref
  null→remote_only; experience_years 10.

Cierre (commit `97b825f` + fixture gate `daa0c61`): whitelist de captura
ampliada (REQUIRED para slots nuevos — sin ellas el snapshot confirmaría
preferencias vacías = verde falso); PROFILE_FIELDS ampliado con fail-safe
refinado (crítico ausente ⇒ alerta y salto; preferencia ausente ⇒ preservar de
la vigente o default en perfil nuevo — un UPDATE parcial jamás vacía);
ProfileWriteDTO con preservar-si-omitido bajo el mismo FOR UPDATE (C-3 de
solo-CV sigue válido); resync one-shot por el flujo normal (staging op='U' +
proyector; sin UPDATE manual ni WAL artificial).

Verificado en R5: valores autoritativos exactos en las revisiones vigentes;
**un solo text_hash distinto** por perfil (el cambio de preferencias NO
re-embebe: vector COPIADO a la revisión nueva — en tests, con backend
envenenado que muerde si hay forward pass); segunda pasada del resync =
**cero revisiones nuevas** (4 totales, sin cambio). Adversariales: TOAST/
omitido, null/lista rara/coerción, A→B→A, tenants, C-3, idempotencia.
Incidente encontrado y arreglado: el fixture legacy del GATE no tenía las
columnas nuevas y el readiness de la captura (backoff) colgó la suite 2 h —
mismo arreglo que el fixture de captura; los fixtures deliberadamente
incompletos (partialcv, leg2) se conservan porque SON el caso de fallo.

## Fase 3 — mecanismo v5 y ablaciones (mecanismo CERRADO; calidad NO-GO)

Mecanismo (commit `e5385c9`): algoritmo `hybrid_rrf_rerank` — candidatos de v4
+ rerank determinista sobre el conjunto ya recuperado, UNA consulta de señales
por lote (sin N+1): rol (trgm título↔título+skills), compatibilidad
ubicación/modalidad (léxicos acotados versionados; ausente=neutral;
remote=true con país concreto NO es global; Remote/Anywhere/Worldwide son
modalidad, no permiso), idiomas del TÍTULO, término aditivo para rangos
profundos y bono de compatibilidad explícita. Receta completa en la fila
(validada antes de evaluar; componentes persistidos por eval; escala
anti-saturación del clamp). 30 regresiones de las familias 1-5 y 8 (mordida:
símbolos ausentes en el padre, verificado por inspección de `97b825f`).

Ablaciones (3 predeclaraciones selladas ANTES de simular: 567daf7, 6992d40,
4e028f5; regla de alto autoimpuesta en la v3 y respetada):

| Ronda | Mejor configuración | P1 | P2 |
|---|---|---|---|
| base v4 | — | 0.4403 | 0.4766¹ |
| v1 (B/C/D, 67 configs) | B w=4 θ=0.35 | 0.4921 | 0.4919 |
| v2 (familia E aditiva) | E a=1.0 pl=0.4 | 0.4297 | **0.6918** |
| v3 (familia F: bono compat + jaccard) | F3 b=0.3 a=1.0 pl=0.4 | **0.5630** | 0.6202 |

¹ Con dev v3 (113 juicios): los 35 ciegos nuevos enriquecen el IDCG — la
medición anterior estaba favorecida por un ideal más pobre.

Trampa de etiquetas dispersas detectada y tratada: la rejilla v1 llenaba el
top-10 de no-juzgados (nj 6-9/10, nDCG sin sentido) ⇒ ronda DEV-3 de 35
etiquetas ciegas (paquete sellado `0795059`, juicios `61f2f67`: 1×2/17×1/17×0)
hasta nj≈0. Diagnóstico positivo del mecanismo: los rel-2 SUBEN (P1 QA Rater
52→14; P2 Customer Support Global → rank 1) — la calidad que falta no es del
reordenador sino del corpus de P1.

Coste medido (EXPLAIN ANALYZE en R5): la consulta de señales con 49 objetivos
× 1800 candidatos tarda **15.7 s** por perfil (seq scan de offer_revisions +
267k buffers). Registrado como deuda: si alguna vX llegara a promoción, exige
optimización (columna lower(title) precalculada o restricción del lote) ANTES
del flip. Sin política que lo ejecute, no bloquea nada hoy.

## Fase 4-7 — no alcanzadas, por diseño

Sin configuración que pase desarrollo NO se crea paquete de validación (nada
que validar sin sobreajustar), NO hay promoción, NO hay examen único (holdout
virgen), NO hay racha. El conjunto activo de R5 permanece {cosine-baseline:v1
canónica, hybrid-rrf:v4 sombra} — verificado tras el deploy (el bootstrap
P1-D lo preservó sin declarar nada). Rollback: no hubo flip que revertir;
`policy_ctl declare cosine-baseline:v1` sigue siendo la vía probada.

## Identidades y suite

- Commits SwissJob: `4f511aa` (evaluador) → `97b825f` (costura+resync) →
  `e5385c9` (mecanismo v5+regresiones) → `daa0c61` (fixture gate). Además
  jobicy reactivado con tags dirigidos (pendiente de commit junto al sourcing).
- Public: `567daf7`→`6992d40`→`4e028f5` (predeclaraciones), `0795059`/`61f2f67`
  (DEV-3), `fe3c961` (resultados), `9e41eac` (manifiesto post-deriva).
- Suite completa FINAL: **988 passed** (949 + 39 nuevos), 11m52s en serie.
- Imagen R5: build limpio de `daa0c61` (RELEASE_SHA horneado), tar sha256
  `6ecf49d0…5376` idéntico en ambos extremos, cargada y verificada EJECUTANDO;
  los 3 servicios R5 con `RELEASE=daa0c61`. Deploy rc=0; dedup backfills
  menores (68/12/1) y revalidación idempotente.

## Deudas residuales (medidas)

1. Corpus del nicho de P1: 12 ofertas vivas global/compatibles de 27 800;
   5 fuera del feed (brecha de recuperación pequeña y real). Palancas en
   marcha: jobicy activo; Adzuna/JSearch/Careerjet esperando credenciales;
   Active Jobs DB como candidato con conector nuevo.
2. Coste del rerank: 15.7 s/perfil (EXPLAIN archivado) — optimizar antes de
   cualquier promoción futura.
3. Conector Careerjet apunta a la API legacy (401 para usuarios nuevos);
   port a v4 (`search.api.careerjet.net/v4/query`, Basic auth + Referer)
   pendiente de la clave de publisher.
4. Deriva de feeds sombra activos: acumulan evals con el corpus (v4 igual que
   v3); toda medición cita su corpus_generation — el evaluador lo impone.
