# ACTA — Holdout de dedup

> **ACTUALIZACIÓN 2026-08-30 — la cohorte vinculante es otra.** Un agente
> **independiente**, que no construyó el detector, etiquetó los mismos 56 pares a
> ciegas. Sus juicios son ahora el oráculo: `holdout-dedup-2026-08-30`. La cohorte
> del 29, etiquetada por el asistente, queda congelada **solo como término de
> comparación** — su limitación de independencia (§1) hacía que no sirviera para
> puntuar el gate.
>
> **Los dos etiquetados coinciden en los 56 pares (100%, cero desacuerdos)**; los
> ficheros de juicios son incluso byte a byte idénticos (`sha256 5977bd56…`). Eso
> sugiere que el rúbrico es objetivo —dos jueces independientes leen lo mismo—,
> **no** que el detector funcione. Cabe además que la tarea sea casi determinista
> dado el rúbrico: H4 son textos idénticos, H5 es ruido aleatorio y la regla
> multi-ciudad resuelve H3 sin margen. El acuerdo mide la pregunta, no la respuesta.
>
> **Anomalía que encontró el etiquetador independiente y yo no**: P33 y P50
> comparten un lado, y sus otros dos lados son vacantes DISTINTAS con idéntico
> título, empresa, portal y ciudad (Clera / Founding Engineer / New York), que solo
> difieren en el salario publicado. Comprobado: el detector **sí** las propuso, con
> similitud 1.000, y el candidato sigue `pending` desde el 2026-08-26. No es un
> fallo de detección sino un candidato sin resolver — pero significa que los 56
> pares contienen **55 juicios independientes**, no 56.
>
> Lo de abajo describe el muestreo y el primer etiquetado, y sigue siendo válido
> salvo por quién etiqueta: eso lo dice este encabezado.

## Acta original (etiquetado del asistente, `holdout-dedup-2026-08-29`)

> Muestreado, etiquetado y congelado el 2026-08-29 sobre la base real
> `swissjobhunter`, esquema `jobhunt`. Todas las cifras salen de una ejecución.

## 0. Por qué hubo que rehacerlo

El holdout del 2026-08-23 se **perdió**: ninguno de sus refs sobrevivió a la
canonización de identidad. `DEDUP_EVAL_COHORT` siguió apuntándolo, de modo que el
gate corregía un examen **inexistente**: `labels_ready` publicaba `pares_dedup: 0`
—que se lee como «aún llenándose»— y el reloj de los siete ciclos no podía
arrancar. Ninguna métrica lo decía. Eso se corrigió aparte (`8dc212a`): ahora la
métrica **nombra su cohorte** y avisa si no existe.

## 1. LIMITACIÓN DECLARADA — quién etiquetó

**Etiquetó el asistente, no el propietario.** El asistente trabajó el detector de
dedup, sus umbrales y la canonización: **no es independiente del sistema que este
holdout juzga**. Se rechazó antes usar `positive-stratum-v1` por falta de
independencia, y esta limitación es **mayor**, no menor.

Mitigaciones, todas mecánicas y verificables:

| Defensa | Cómo se garantiza |
|---|---|
| Hoja **ciega** | solo `titulo, empresa, lugar, remoto, salario, fuente, desc`. Verificado **por estructura** sobre las claves del JSON, no por búsqueda de texto |
| Sin pistas del detector | ni similitud, ni `text_hash`, ni estado en `dedup_candidates` |
| Estrato **oculto** | los pares van barajados por `md5(va||vb)`; saber que un par es H3 delataría que los textos son idénticos |
| Muestreo reproducible | SQL literal del protocolo con `setseed(0.230823)` — `backend/scripts/holdout_dedup_muestreo_2026-08-29.sql` |
| Clave aparte | `estrato → par` se emitió a otro fichero y **no se abrió hasta después** de emitir los 56 juicios |

**Cualquier informe que cite este gate debe citar también esta limitación.**
Queda además dentro del `manifest` de la cohorte congelada, en la BD.

## 2. Muestreo

Marco: **11.113** vacantes vigentes. Bloqueo léxico: **8.515** pares, de ellos
**31** cross-source. Enmienda de independencia del protocolo: se excluyeron **4**
pares ya presentes en `labeled_dedup_pairs` (medirían memoria, no
generalización). Déficit registrado y **no rellenado**, como manda el protocolo.

| Estrato | Mecanismo | n |
|---|---|---:|
| H1 | cross-source, bloqueo léxico | 11 (−4 por independencia) |
| H2 | intra-source, bloqueo léxico | 15 |
| H3 | texto idéntico, ciudad distinta | 10 |
| H4 | texto idéntico, misma ciudad y fuente | 8 |
| H5 | control aleatorio | 12 |
| **Total** | | **56** |

## 3. Juicios

**9 `duplicate` · 47 `distinct` · 0 `unsure`**, con el rúbrico del protocolo:
`duplicate` = misma vacante (mismo puesto, empresa y proceso) aunque el texto
difiera; publicación multi-ciudad del mismo puesto = `distinct` (regla ratificada).

| Estrato | duplicate | distinct |
|---|---:|---:|
| H1 | 1 | 10 |
| H2 | 0 | 15 |
| H3 | 0 | 10 |
| H4 | 8 | 0 |
| H5 | 0 | 12 |

Coherencia interna: H4 (texto idéntico, misma ciudad y fuente) sale **8/8
duplicate**; H5 (control aleatorio) **12/12 distinct**. H3 sale **10/10 distinct**
porque la regla multi-ciudad así lo manda — sin desacuerdos que registrar.

## 4. OBSERVACIÓN SOBRE EL PODER DE MEDIDA — leer antes de citar el recall

Medición contra este holdout: **recall 1.0 · precision 0.818** (tp 9, fp 2, fn 0).

**Ese 1.0 dice mucho menos de lo que parece.** Ocho de los nueve positivos son H4
—texto idéntico, misma ciudad, misma fuente—, que el detector resuelve por hash
exacto: el caso trivial. Solo hay **un** positivo cross-source. Es decir, el
holdout mide bien lo fácil y casi no mide el dedup cross-source, que es el que
importa. **No debe presentarse como evidencia de que el dedup cross-source
funciona.**

Causa estructural, no accidente: el marco solo tiene **31** pares cross-source con
bloqueo léxico, y la regla multi-ciudad convierte en `distinct` casi todo lo que
H3 aporta. Un holdout con poder de medida real sobre cross-source necesitaría un
estrato distinto y más corpus multi-portal.

## 5. Congelado

`freeze_dedup_cohort()` sobre `labeled_dedup_cohorts` — el trigger-guard hace
**inmutables** los pares de la cohorte en la BD. Sellada
**2026-08-29 16:22:13 UTC**.

SHA-256: hoja ciega `7b85acaa…592b98` · SQL de muestreo `7011324f…4d6ae` ·
juicios `5977bd56…0cbed7`. Los tres van dentro del manifest.

## 6. Estado del gate tras la carga

```
labels_ready = 1
  cohorte: holdout-dedup-2026-08-29   cohorte_existe: true
  pares_dedup: 56   pares_mapeables: 56
  perfiles_ok: 2    (sets congelados: 3, uno excluido por usuario inactivo)
```

**El reloj de los siete ciclos verdes consecutivos puede empezar.** Solo cuentan
los ciclos cuya ventana empieza DESPUÉS del sellado, así que el primero elegible
es el del 2026-08-30.

## 7. Pendiente — ratificación del propietario

Muestra de 15 pares para revisar (no los 56). Si el propietario discrepa en
alguno, **el desacuerdo se registra, no se corrige** (regla del protocolo), y si
los desacuerdos cambian el signo de la medición, la cohorte se retira y se rehace
con etiquetado del propietario.
