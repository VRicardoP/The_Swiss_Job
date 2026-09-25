# FREEZE — congelación previa al examen ÚNICO de holdout (2026-09-06)

> Declarado ANTES de consultar un solo juicio del holdout. A partir de esta
> línea NADA de lo listado se toca: si algo cambia, el examen se anula y
> exige una campaña nueva. El holdout se consume UNA vez.

## Precondiciones cumplidas (verificadas hoy, no presupuestas)

| Precondición | Evidencia |
|---|---|
| Fase D cerrada y ESTABILIZADA | `SWISSJOB SOBRE CORE` (ESTADO §23); ciclo diario completo 06-09 en `core_primary` (1.422 fetched, 617 embebidas, dedup ok) con etapa de matching legacy OMITIDA; **0 errores de core en el BFF en 24 h**; core evaluando por su cuenta (202 evals cosine, feeds refrescados) |
| P7-incremental APTO | `DEV_P7B_INCREMENTAL_2026-09-04/` (paridad 6e-06, pico 100=30 min, corte+reanudación sin duplicados) |
| Release limpia desplegada | `099cf6d` en los 4 servicios core del NAS, `/v1/health` y `/v1/ready` `authoritative: true`; paridad de contenido 177/177 |
| Caché de la candidata al día | bootstrap 3.600 pares + materialización incremental de los 213 misses de deriva (pre-examen, vía NAS predeclarada) |

## Lo que queda CONGELADO

- **Código**: SwissJob `099cf6d` (suite 1045/1045), desplegado y autoritativo.
- **Modelo/artefacto**: RankNet n=436 exportado a ONNX, huella
  `58483c94c6f579a6219c3b5fbeef992ed85542f2d8c457bf1e5b7fc83e9eae01`
  (`/models/xenc-ranknet-n436`), procedencia `train_data_sha256
  25cdd3a9037c13014188ec23ad19a1d7f6929920522875c54a3b3c57e526e01b`.
- **Receta/política candidata**: `xenc-ranknet:v1` — algorithm
  `cross_encoder`, input v1, activation `sigmoid_t4`, backend `onnx-cpu`,
  recuperación lexical_query v2 / lexical_weight 0.25 / rrf_k 60.
- **Baseline**: `cosine-baseline:v1` (canónica en producción, intacta).
- **Cohorte del examen**: P1 `0b69cfae-41e3-4dd2-971b-1ded69426060` y
  P2 `680b2f12-9e45-478e-ad60-6762f4b8df28` (los dos perfiles con set
  congelado). El perfil del portfolio no entra: no tiene juicios de esta
  campaña.
- **Universo**: se sella con el CLI estricto ANTES de leer juicios; su sha
  queda en la evidencia y liga pool y evaluación.
- **Juicios**: `labeled_sets 'nas-ronda-1'` (frozen 2026-08-23, VIRGEN):
  P1=34 (`8578eca8-6656-4c76-b9fb-8e3ae220e871`), P2=32
  (`237d36be-1f97-4c5d-bd3c-5157c7daf170`), más el etiquetado ciego
  incremental de los pares del top-10 que no cubran (mismo protocolo
  sellado: paquete con sha256, clave privada fuera, etiquetador de contexto
  fresco).
- **Métrica y umbral**: `shadow.metrics._dcg` graduada, nDCG@10, **0.60/0.60
  en AMBOS perfiles** y no empeorar respecto de la baseline. Inmutables.

## Reglas del examen

1. Cobertura del top-10 al 100 % o **INELEGIBLE** (jamás nDCG parcial).
2. Una sola evaluación por política sobre el universo sellado.
3. Si SUSPENDE: el holdout queda CONSUMIDO. No se convierte en desarrollo,
   no se reabre, no se baja el umbral; otra promoción exigiría un holdout
   independiente nuevo en una campaña futura.
4. Si PASA: promoción transaccional por la autoridad única
   (`declare_active_policies`), rematerialización, verificación de punteros,
   rollback probado, y arranque de la racha 7×24 h. La promoción exige
   además desplegar en los servicios core `CE_BACKEND=onnx-cpu` y el
   artefacto en `/models/xenc-ranknet-n436` (hoy NO están: la valla de
   receta falla cerrado, comprobado).

## Adenda sellada ANTES de diagnosticar y ANTES de consultar juicio alguno

Hallazgo estructural (sin leer etiquetas): el set `nas-ronda-1` es de
`source=seed_feedback` — derivado del feedback del propio usuario en agosto,
con clave de hash legacy (64/66 resuelven vía `external_id`, 54 a vacante
viva). Es INDEPENDIENTE del modelo y anterior a él, pero cubre lo que el
motor LEGACY mostró, no lo que la candidata pone arriba: por sí solo no
cubrirá su top-10.

Además, el entrenamiento del RankNet consumió **392 vacantes** (juicios dev
v11). Medir nDCG sobre pares que el modelo vio etiquetados sería medir
MEMORIZACIÓN, no generalización — la misma cautela que ya se declaró sobre
el 0.9872/0.8604 de desarrollo.

**REGLA DE DISEÑO (predeclarada, se aplica sea cual sea el resultado):**

1. Se mide el solape: cuántos de los 10 primeros de la CANDIDATA proceden de
   esas 392 vacantes vistas en entrenamiento.
2. **Si el solape es ≥3 de 10** en cualquiera de los dos perfiles, el examen
   se ejecuta sobre el universo restringido a pares **NO VISTOS** en
   entrenamiento (para AMBOS sistemas por igual, baseline y candidata). Es
   un test de generalización limpio.
3. **Si el solape es ≤2 de 10** en ambos, el examen corre sobre el universo
   elegible completo.
4. En cualquiera de los dos casos: pool = unión de los top-10 de ambos
   sistemas, barajado, etiquetado A CIEGAS por un agente de contexto fresco
   con la rúbrica de siempre, sellado con sha256 y clave privada fuera; UNA
   sola evaluación; umbral **0.60/0.60 inmutable**; `nas-ronda-1` se usa
   como corroboración independiente donde cubra, nunca para sustituir la
   cobertura del top-10.
5. Se reporta SIEMPRE el solape medido, pase lo que pase.
