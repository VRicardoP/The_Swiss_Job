# CIERRE CORREGIDO — v5 hasta GO (2026-09-03)

Ejecución del PROMPT_CORRECCION_CIERRE_V5. Punto de partida `b3dd8f5`; los
nDCG del cierre anterior quedan INVÁLIDOS para decisiones (feed de unión
histórica). Entorno: SOLO R5; `:prod` intacta (`ba69dbb887d0…` verificada en
cada carga). **El holdout congelado permaneció cerrado**: ninguna consulta a
sets congelados en toda la sesión (las únicas lecturas de juicios fueron los
113 de DESARROLLO, sha256 `41f81bcb…c09d79`).

## Veredicto

**`NO-GO`** — concreto, reproducible y **no** causado por infraestructura de
evaluación: con el instrumento corregido (feed coherente de una sola
ejecución, fórmula del gate, señales arregladas), ninguna familia determinista
predeclarada alcanza nDCG@10 ≥ 0.60 en ambos perfiles.

**Primer gate real bloqueante**: calidad de ORDEN del desarrollo. La
descomposición con oráculo lo acota con precisión:

- **Recuperación: RESUELTA.** Oráculo 1.0/1.0 — todos los juzgados necesarios
  para un top-10 perfecto (ambos rel-2 incluidos) ESTÁN en el conjunto
  candidato de 1800. `rel2_fuera_de_candidatos = []` en ambos perfiles.
  ⇒ **El «techo estructural de P1» del cierre anterior queda RETIRADO**: era
  un artefacto de la medición contaminada. La revisión externa acertó.
- **Orden: el bloqueo.** Mejor configuración A–E coherente: 0.451/0.505
  (E a=1.0 sin pen); familia G (score absoluto, predeclarada en `72c4428`):
  mejor P1 0.494 con P2 0.241 — el ALTO predeclarado dispara.
- **Cobertura: decae con la deriva.** 17/55 (P1) y 16/58 (P2) juicios ya
  fuera de candidatos; configs con penalizaciones meten 3-7 no-juzgados en el
  top-10 (un verde así no sería creíble y se marcó como no-cumple).
- **Compatibilidad: corregida y acotada** (multi-país por intersección,
  idiomas AND/OR/ambiguo) — ya no puede ser la explicación del rojo.

**Decisiones elevadas al propietario** (ninguna se toma sola):
1. **Rerank cross-encoder/LLM acotado** — ahora LEGITIMADO por protocolo: las
   familias deterministas (fusión B–E y absoluta G) están medidas
   coherentemente y suspenden; el oráculo demuestra que el orden perfecto es
   alcanzable. Requisitos ya fijados: modelo/versión clavados, entrada y
   truncado versionados, score calibrado con fallback determinista, batching
   sin N+1, presupuesto de coste/latencia, y NINGUNA llamada externa con
   CV/PII sin autorización y contrato de privacidad.
2. **Contrato de desarrollo/examen con corpus VIVO** — la cobertura de
   juicios decae con cada cosecha y en `shadow.metrics` un no-juzgado vale 0
   (ampliar corpus NO puede aprobar el examen congelado; puede bajarlo).
   Opciones a decidir: congelar el corpus del examen, o protocolo de
   pooling/cobertura explícito. No se cambia la métrica en silencio.

## Tabla de hallazgos de REVISION_CIERRE_V5_2026-09-03

| Hallazgo | Estado | Cierre y mordida |
|---|---|---|
| P1 — feed mezcla generaciones | **CERRADO** | `compute_policy_feed`: cálculo ÚNICO extraído de `evaluate_profile` (sin persistir, orden de feed); `dev_eval` v2 lo mide bajo REPEATABLE READ READ ONLY con `corpus_generation` de la misma fotografía; `shadow_feed` ELIMINADO. Reproducción roja: limit+1 filas en el almacén y rango de G1 conservado tras el cambio de corpus, mientras el cálculo directo da el rango actual y tamaño objetivo. Mordida: `AttributeError compute_policy_feed` en el padre. Commit `0e105d2`. |
| P1 — sombra sin semántica canónica (dismissed) | **CERRADO** | `exclude_dismissed` con la MISMA regla que `feed()` (dismissed_at excluye; sin fila de estado, visible); test simétrico + equivalencia fila a fila con `feed()` en canónico; promoción sirve EXACTAMENTE lo medido y el rollback restaura el feed anterior (test transaccional con `declare_active_policies`). |
| P1 — corpus no es palanca del holdout | **CERRADO** (estrategia) | Afirmación retirada del cierre anterior; sourcing (Jobicy etc.) queda SEPARADO del gate como mejora de producto; la mejora exigible es el orden de las vacantes juzgadas; decisión corpus-vivo elevada. |
| P2 — multi-país primer-país | **CERRADO** | `_offer_countries` (todos los países, frontera de palabra — «india» no dispara en «Indiana»); incompatible SOLO con conjuntos conocidos y disjuntos. Matriz: compatible al principio/al final/entre dos incompatibles, multi-palabra, alias USA, neutrales. Mordida real: «Germany / Switzerland» incompatible en el padre. |
| P2 — idiomas disyuntivos | **CERRADO** | `_title_language_requirement`: «or»⇒cualquiera, «&/and»⇒todas, barra o mezcla⇒neutral; positivo, negativo e inverso por regla. Mordida real: el padre penalizaba «English or Spanish» a un perfil con inglés. |
| P2 — API acepta preferencias inválidas | **CERRADO** | `remote_pref` limitado al enum autoritativo (Literal); rango salarial validado sobre el contenido FINAL COMBINADO del PUT parcial (mínimo preservado + máximo nuevo ⇒ 400 `invalid_salary_range`; inverso legítimo 200). Mordida: el padre respondía 200. |
| P2 — Jobicy forma externa | **CERRADO** | Raíz dict, `jobs` lista, elementos no-dict descartados con log; la fila válida del lote mixto sobrevive. Mordida: `AttributeError` en el padre con raíz lista. |
| P3 — manifiesto volátil | **CERRADO** | Payload reproducible sellado con `payload_sha256`; `generated_at` FUERA (el test compara payloads completos, sin quitar campos); `RELEASE_SHA=unknown` es error en operación; `unsure` validado como dict[str, list[UUID]] con rechazo de extras/escalares. |

## Prioridad 0 — preservación

HEAD inicial `b3dd8f5`; árbol de trabajo sin ficheros ajenos en los commits
(school_job_monitor/INSTALAR_CODEX/web_scraping intactos y fuera); builds
desde worktree limpio. Confirmado por consulta: SOLO `cosine-baseline:v1`
canónica (5400 `current_eval_id`, todos cosine); ninguna v5 existe como fila;
v4 sigue en sombra sin autoridad sobre el feed. Juicios sellados
(41f81bcb…). Migración: única cabeza `core0040`; ninguna migración publicada
tocada; ninguna nueva necesaria (todo cerró con extracción local del cálculo).

## Remedición coherente (gen 264525, release 28a17fe)

Todo bajo fotografía transaccional, señales de producción, fórmula del gate,
113 juicios dev v3 (evidencia y tablas completas en
`/home/lothar/Public/DEV_MEDICION_COHERENTE_2026-09-03/`, sha
`7f1b090e…`):

| Medición | P1 | P2 |
|---|---|---|
| Oráculo (máximo alcanzable con candidatos presentes) | **1.0** | **1.0** |
| v4 base coherente | 0.4388 | 0.3657 |
| Mejor A–E (E a=1.0 sin pen) | 0.4513 | 0.5052 |
| Mejor G (absoluta; g_l=.1 g_r=.3) | 0.4940 | 0.2410 |

Los JSON del método anterior quedan RETIRADOS con nota (no borrados):
`DEV_METRICAS_V4_2026-09-02/RETIRADO.md`, `DEV_ABLACION_V5_2026-09-02/RETIRADO.md`.
Hallazgo colateral honesto: el score absoluto ordena PEOR que la fusión en P2
— la similitud coseno comprime el rango alto; la distancia restante es señal
semántica fina, el caso reservado al cross-encoder.

## Suites, mordidas e identidades

- Suite completa en serie: **1000 passed** (11m58s) sobre `28a17fe`; focales
  por fase verdes. Todas las mordidas listadas arriba EJECUTADAS contra el
  padre con la causa exacta (no solo ImportError: primer-país, disyuntivo,
  200-en-banana y AttributeError de Jobicy son fallos semánticos reales).
- Commits: `0e105d2` (P1 instrumento) → `28a17fe` (P2 fronteras). Public:
  `e9f2b42` (retirados) → `72c4428` (predeclaración G) → `7f6d431`
  (remedición) → `202d10f` (resultado G).
- Imagen R5: build limpio de `28a17fe`, tar sha256 `da0bb8bc…` idéntico en
  ambos extremos, verificada EJECUTANDO (`RELEASE=28a17fe`); deploy rc=0; el
  bootstrap preservó `{cosine-baseline:v1 canónica, hybrid-rrf:v4 sombra}`;
  `:prod` intacta. Rollback de activación disponible y probado en tests
  (`policy_ctl declare` + regresión de feed restaurado).

## Deuda residual (propietario y condición de cierre)

1. **Decisión LLM/cross-encoder** — propietario: dueño del producto.
   Condición: autorización expresa (incluida privacidad de CV/PII si el
   modelo es externo); entonces se diseña como política nueva versionada con
   predeclaración, presupuesto (≤ X s/perfil a fijar) y validación ciega.
2. **Contrato corpus-vivo del desarrollo/examen** — propietario: dueño del
   protocolo. Condición: elegir congelación de corpus o pooling ANTES de la
   próxima ronda de etiquetado (la cobertura decae: 17/55 y 16/58 ya fuera).
3. **Coste del rerank determinista** (15.7 s/perfil, EXPLAIN archivado) —
   propietario: core. Condición: presupuesto fijado y optimización medida
   ANTES de cualquier promoción que use señales.
4. **Port del conector Careerjet a API v4** — propietario: sourcing.
   Condición: clave de publisher del usuario; cambio de ~20 líneas + sonda.

No hubo examen (desarrollo no pasó) ni promoción (sin racha que abrir). Un
`NO-GO` así es válido por el propio contrato del encargo: la calidad real
falla DESPUÉS de corregir el instrumento, con la causa acotada a orden, la
vía determinista agotada con predeclaraciones y la siguiente acción elevada
en vez de improvisada.
