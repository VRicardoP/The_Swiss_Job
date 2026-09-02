# CIERRE — post-etiquetado hasta GO (2026-09-02)

> Todo lo de aquí está medido y con su comando; donde algo se predijo, la
> predicción quedó escrita ANTES de la medición. Los commits nombrados llevan la
> mordida contra su padre.

## Veredicto

**Dedup: EXAMEN FINAL VERDE** — TP=7 · FP=0 · FN=0, precisión 1.0, recall 1.0.

**Matching: NO-GO de promoción — pendiente de etiquetado incremental de 3
ofertas.** hybrid-rrf/v3 en sombra: P2 pasa desarrollo (0.617 ≥ 0.60), P1 se
queda en 0.557 con UN solo hueco sin juzgar en su top-10. El umbral no se movió
tras ver los números y el examen contra los sets congelados NO se ejecutó
(desarrollo no superado ⇒ no hay examen). La sesión NO declara «apto para
iniciar racha».

## 1. Trazabilidad (Fase 0)

- Commit documental de los juicios: `0d2284a` — verificado.
- Los 6 sha256 (hojas, md, juicios × 2 paquetes) coinciden con el relevo.
- Seniority: la clave Dxx→UUID/estrato **existía** (`dev_clave.txt`, commit
  `8167b9c`) — no hubo que reconstruirla. **60 pares únicos, 0
  repetidos/invertidos** (el muestreador deduplicaba por construcción: lo que el
  etiquetador percibió como repetición son pares *parecidos*, no el mismo par).
- Ranking: clave validada R001..R079, 0 solapes con sets/cohortes congelados.
- Baseline R5 capturado antes de tocar nada: release `2c19837-0e83e37a6f1b`,
  `core0040`, cohorte v3 congelada, TP=6/FP=1/FN=1.

## 2. Dedup — examen final VERDE (Fase 1)

**El desarrollo decidió; el holdout examinó una vez.**

- Estrato S1 (misma empresa, seniority asimétrico): **20/20 `distinct`**.
- Regla congelada por escrito ANTES de mirar el holdout: *niveles canónicos
  distintos + título base contenido (tokens por frontera, sin género; dígitos
  conservados — IPOS-03) ⇒ no candidato*. `leader→lead` canonizado a propósito.
- En desarrollo: dispara 3/60, los 3 `distinct`, **cero** de los 5 `duplicate`
  de control tocados.
- UNA expresión (`_nivel_incompatible_sql`) en los 4 consumidores: kNN antes del
  LIMIT, su conteo espejo, léxico antes del INSERT, revalidación con bump
  `rule:track-r-location-v2+nivel-v1`. Mordidas contra el padre por la causa
  exacta. Commit `353fab6`.
- Revalidación en R5: **259** pendientes retirados, preview=apply, segunda=0.
- **Examen final (una medición, 12:36 CEST): TP=7 · FP=0 · FN=0 —
  precisión 1.0 · recall 1.0. VERDE.** La predicción escrita antes de medir.

**Honestidad de alcance**: la matriz de desarrollo de la regla vigente era
TP=0/FP=0/**FN=5** — los generadores nunca propusieron los 5 duplicados del
desarrollo, con o sin veto. Hueco preexistente, registrado, fuera del alcance de
un supresor y de esta sesión (tocar umbrales está prohibido).

## 3. Matching — diagnóstico, v2 y la ablación única (Fases 2.1–2.3)

Baselines de desarrollo (75 juicios, nDCG@10 con no-juzgado=0):

| Política | P1 | P2 | rel-2 fuera del feed |
|---|---|---|---|
| cosine-baseline | 0.279 | 0.387 | **R003 en 3933** |
| hybrid-rrf/v1 | 0.098 | 0.000 | ninguno |
| hybrid-rrf/v2 (peso 1.15) | 0.000 | 0.000 | ninguno |
| **hybrid-rrf/v3 (peso 0.25)** | **0.557** | **0.617** | ninguno |

**El hallazgo que reordena el problema**: el brazo semántico de v2 coloca los
relevantes en los rangos semánticos 1,1,2,2,3,4,5,6 — casi perfecto — y era la
FUSIÓN quien los enterraba: con peso 1.15 y consulta ancha, una oferta mediocre
en ambos brazos suma más RRF que un nº1 semántico sin señal léxica (y las
ofertas sin descripción no tienen señal léxica que sumar: justo el caso que v2
existe para rescatar).

**El ajuste fue UNA comparación predeclarada** (A: peso 0.25 · B: semántico
primero), simulada con matemática determinista sobre las evaluaciones
persistidas — cero reevaluaciones, cero margen de sobreajuste. Ganó A:
0.557/0.617 simulado. Commits `77d1395` (v2) y `54564bf` (peso propio).

Diseño que evitó dos minas: `_LEXICAL_WEIGHT` está compartida con v1 — ajustarla
la habría MUTADO; v2 lleva peso y SQL propios con guarda de derivación al
importar. Y la suficiencia del ANN se comprueba POR BRAZO
(`_semantic_arm_filled`): el control combinado dejaba que un FTS lleno ocultara
el underfill semántico (auditoría R8 §2.2.4) — muerde.

**Refutación registrada (§2.2 del encargo)**: la hipótesis «HNSW trunca el
evaluador canónico» quedó matizada — `evaluate_profile` ya configura
`iterative_scan strict_order` + fallback exacto; el hueco real era el control
COMBINADO de suficiencia, no el índice.

## 4. Cola histórica (Fase 3) — decisión pendiente, acotada

~190k `dedup_candidates` pending (86k vacantes archivadas). No afecta al gate
(las métricas se computan sobre los 51 pares de la cohorte) ni se midió
degradación. **Sin DELETE**: los candidatos históricos sostienen la evaluabilidad
del holdout. Queda como deuda acotada con la opción mínima recomendada: excluir
archivados del flujo operativo conservando la evidencia.

## 5. Verificación integral (Fase 4)

- Suites en serie: **937 passed** (929 tras el veto; 917 al relevo).
- `git diff --check` limpio; cabeza única `core0040`.
- Builds desde worktree del commit candidato (árbol limpio), etiqueta por
  commit; **nunca `:prod`** para R5 — el `:prod` del NAS sigue en `2c19837`.
- sha256 origen=destino en cada transferencia; release verificada EJECUTANDO la
  imagen; los 3 servicios R5 con el mismo image ID.
- Deploy de esta sesión dejó `{cosine-baseline, hybrid-rrf/v3}` activas (v1 y
  v2 inactivas); canónica = cosine por orden de selección verificado, v3 en
  SOMBRA. [Corregido 2026-09-02: la redacción original decía «v2» — v2 quedó
  inactiva al detectarse el régimen mezclado y el peso 0.25 es la fila v3.
  Desde P1-D el deploy ya NO declara activaciones: solo policy_ctl.]

## 6. Medición final de desarrollo — v3 (peso 0.25)

**La medición real reprodujo la simulación al tercer decimal**: P1 0.557 ·
P2 0.617, relevantes-2 dentro del feed, feed limpio (1801/1800). En P2 hay un
relevante-2 en el rango 5.

**Por qué el peso 0.25 es la política v3 y no «v2 ajustada»**: al cambiar el
peso, las 1800 evaluaciones ya persistidas de v2 NI se recalculan NI se
distinguen — `eval_key` no lleva los parámetros del algoritmo — y la primera
medición leyó una mezcla de dos regímenes (feed_n=2380, nDCG 0.0). El almacén
append-only hizo cumplir el contrato del proyecto: *una política versionada no
muta; otra versión, otra fila*. v1 y v2 quedan inactivas como histórico intacto.

**El bloqueo, reducido a su mínima expresión medible**: el top-10 de P1 tiene
9/10 puestos ya juzgados; solo el rango 4 está sin juzgar (P2: rangos 4 y 6).
Paquete incremental de **3 ofertas** listo en `DEV_RANKING_TOP10_2026-09-02/`.
La aritmética es decisiva y quedó escrita ANTES del etiquetado: si el rango 4 de
P1 es relevante (rel ≥ 1), P1 sube a **0.624 ≥ 0.60** y procede la promoción
transaccional + examen único contra los sets congelados; si es 0, v3 suspende
desarrollo y se queda en sombra.

Nota: el beat evaluó también un TERCER perfil (`075f…`) bajo v3; no tiene set
congelado y no puntúa en el gate — se registra para que nadie lo confunda con
los perfiles medidos.

## 7. Fallos propios de la sesión — para que el siguiente no los repita

1. Espera por FICHERO de estado (`.rc`) que sobrevivía de un deploy anterior ⇒
   «terminado» falso. Corrección: esperar el SELLO (la release respondiendo).
2. `print` con variable renombrada a medias en el deploy script ⇒ NameError
   post-commit. El rc quedó, los contenedores no se recrearon.
3. `sys.path` ausente al ejecutar el medidor desde /tmp del contenedor.
4. Helpers de test inventados dos veces; y el acceso por atributo a Rows que
   vació consultas en silencio (indexación posicional + aserciones que nombran).

## 8. Rollback

- Política: `cosine-baseline` sigue siendo la canónica (la sombra es v3, no
  v2 como decía la redacción original; corregido 2026-09-02). Rollback de
  activación: `python -m jobhunt_core.policy_ctl declare cosine-baseline:v1`.
- Imagen R5: `docker tag swissjob-core:353fab6 swissjob-core:r5-cycle` +
  Recreate (el examen de dedup verde corresponde a `353fab6`; `54564bf` añade
  el peso 0.25 como fila v3 — no un peso «de v2»).
- Cohortes/labels/identidades: **cero** tocadas en toda la sesión.
