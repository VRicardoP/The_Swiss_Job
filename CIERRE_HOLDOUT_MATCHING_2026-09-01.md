# CIERRE — holdout dedup + calidad de matching (2026-09-01)

## Veredicto

**NO-GO: pendiente de etiquetado independiente.**

No se abre la racha de siete ciclos. El bloqueo exacto es **la precisión de dedup**,
y **no puede resolverse con la evidencia disponible sin contaminar el examen**.

---

## 1. El bloqueo, en una tabla

`DEDUP_PRECISION_MIN = 0.95` — verificado en `jobhunt_core/shadow/metrics.py:178`,
no en la documentación. La cohorte vigente `holdout-dedup-20260830-v3` tiene **7
duplicados**, así que:

| TP | FP | precisión | ¿pasa 0,95? |
|---:|---:|---:|---|
| 6 | 1 | 0,857 | no *(estado inicial)* |
| **7** | **1** | **0,875** | **no** — *estado MEDIDO tras el despliegue* |
| **7** | **0** | **1,000** | **sí** |

**Cerrar el falso negativo no puede poner el dedup en verde.** Con siete positivos
no hay punto intermedio: un solo falso positivo deja la precisión en 0,875. Hay que
eliminar el FP. El prompt de relevo lo situaba «por debajo de 0.90»; el umbral real
es 0,95, que es un problema distinto.

> **Fragilidad del denominador**: con 7 positivos, cada FP cuesta 12,5 puntos de
> precisión y cada FN 14,3 de recall. La métrica es muy sensible al tamaño de la
> cohorte, y v3 excluyó 2 de los 9 positivos del padre (56→51 pares, 9→7 dup).

## 2. Por qué NO se tocó el detector — el hallazgo que decide la sesión

El FP es AuroraSolar: `Senior Account Manager` vs `Account Manager`, **misma
empresa**. La hipótesis natural es una regla de calificadores de seniority. Se buscó
evidencia independiente en dev-2/dev-3 **sin mirar el verdict de v3**, como manda el
protocolo.

**Resultado: 13 pares con calificador de seniority asimétrico, y los 13 etiquetados
`distinct`.** A primera vista, apoyo unánime para la regla.

**Pero los 13 tienen EMPRESAS DISTINTAS.** Su `distinct` queda explicado por la
empresa; el seniority es incidental. **Ninguno reproduce la configuración del FP**
—misma empresa, mismo título base, seniority distinto—, que es justo la que la regla
tendría que decidir.

| Configuración | Pares en dev-2 |
|---|---:|
| seniority asimétrico, **misma** empresa | **0** |
| seniority asimétrico, empresa distinta | 13 |

Usar esos 13 como justificación sería apoyar una regla en datos que no la prueban.
Se aplica la Fase 2.3: **no se modifica el detector** y se prepara muestra ciega
nueva con etiquetado independiente.

## 3. Cambios realizados

### 3.1 Guarda de derivación en `dedup.py` (causa raíz: un fallo silencioso)

`_EXACT_INTRA_HISTORY_SQL` se deriva de `_EXACT_INTRA_SQL` con `str.replace`, que
**no falla cuando no encuentra su patrón**: devuelve la cadena intacta. Si alguien
reescribiera el `WHERE` de la consulta diaria, la variante histórica quedaría
idéntica a la diaria —active-only— y **el backfill dejaría de recuperar archivadas
en silencio, con todos los tests en verde**.

Añadida una comprobación al importar y su regresión
(`test_la_variante_historica_no_puede_quedar_igual_que_la_diaria`), verificada **en
rojo** reescribiendo el patrón: la guarda muerde.

Los cinco invariantes exigidos por la Fase 1 se comprobaron **leyendo el SQL
generado**: incluye archivadas · excluye `merged_into` · conserva identidad
(`text_hash` + fuente + ubicación) · no altera el barrido diario · es idempotente y
no reabre resueltos (por `_ON_CONFLICT`, que solo actualiza `pending` y solo si la
similitud crece).

### 3.2 Política experimental fuera del camino canónico

`hybrid-rrf/v1` era la **única** política activa en R5: la experimental **no
validada gobernaba el feed canónico**.

Antes de tocar nada se verificó la semántica real de selección
(`jobhunt_core/tasks/matching.py:71`): `WHERE active ORDER BY name, prompt_version`,
y **la primera es la canónica**; el resto corren en sombra. Alfabéticamente
`cosine-baseline` precede a `hybrid-rrf`, luego reactivarla la devuelve al camino
canónico sin borrar nada.

```
ANTES     cosine-baseline v1  active=f | hybrid-rrf v1  active=t
DESPUÉS   CANONICA cosine-baseline v1  | sombra hybrid-rrf v1
```

Verificado ejecutando la misma consulta que usa la tarea.

## 4. Verificaciones ejecutadas

| Qué | Resultado |
|---|---|
| Suite completa del core | **917 passed**, exit 0, 11 min 39 s |
| Suite focal de dedup | **24 passed** (23 previos + la regresión nueva) |
| Cabeza de Alembic | única, `core0040` |
| Diff del árbol | 36 ficheros, 1718/460 — coincide con el relevo |
| R5: release e imagen | los tres servicios, **el mismo artefacto** `sha256:f8fe323b…`, `RELEASE_SHA=2c19837-c940d43844be` |
| R5: `/v1/health` y `/v1/ready` | `alembic core0040`, `authoritative: true` |
| R5: base de datos | `swissjobhunter_r5_rehearsal` — **comparte servidor con producción**, base distinta |
| Cohortes congeladas | `holdout-dedup-2026-08-30` 56 pares (9 dup/47 distinct) · `holdout-dedup-20260830-v3` 51 pares (7/44) — íntegras |
| Matriz dedup v3 | **TP=6 · FP=1 · FN=1** · precisión 0,857 · recall 0,857 |
| `labels_ready` | 1 · 51/51 mapeables · 2 perfiles |

**Producción NO fue modificada**: todas las escrituras fueron a
`swissjobhunter_r5_rehearsal`. La única fue el `UPDATE` de reactivación de
`cosine-baseline` (§3.2).

## 4bis. Fase 1 completada — desplegada y MEDIDA

| Paso | Resultado |
|---|---|
| Imagen construida | `RELEASE_SHA=2c19837-0e83e37a6f1b` (commit + huella del diff sucio) |
| Verificación de la imagen | **ejecutándola**: publica esa release y contiene la guarda nueva |
| Transferencia | sha256 `e73c0a77…` idéntico en origen y destino |
| Etiquetas | build sucia SOLO en `:r5-cycle` y `:2c19837-0e83e37a6f1b`; **`:prod` restaurada** a la build limpia `2c19837` |
| Deploy | modo `up` (nunca `bootstrap`), `rc=0`; los TRES servicios con el mismo artefacto `sha256:b759b562…` |
| Backfill histórico | **178 343** candidatos; segunda ejecución **0** (idempotente, verificado) |
| Revalidación | `apply` n=13, preview == apply, tercera pasada 0 |
| **Matriz v3** | **TP=7 · FP=1 · FN=0 · recall 1,000 · precisión 0,875** |
| Par Klickpiloten | existe con similitud 1.000, `pending`, **ambos lados archivados** — el caso que el barrido diario no puede ver y el histórico sí |

**Recall: VERDE** (1,000 ≥ 0,40). **Precisión: ROJA** (0,875 < 0,95), como predecía la
aritmética de §1: cerrar el FN no podía bastar.

> **Efecto colateral que hay que vigilar**: el backfill dejó **190 172 candidatos
> `pending`** sobre un corpus con 86 250 vacantes archivadas. No afecta a las
> métricas del holdout —que se calculan solo sobre sus 51 pares— pero es una cola
> de revisión enorme que nadie ha dimensionado. Conviene decidir si esos candidatos
> históricos deben resolverse, archivarse o quedar fuera del flujo de trabajo.

## 4ter. Paquete de desarrollo listo para etiquetar

`/home/lothar/Public/DEV_SENIORITY_2026-09-02/` — **60 pares**, muestreo con semilla
fija `setseed(0.010926)` sobre 104 582 vacantes libres:

| Estrato | Qué prueba | n |
|---|---|---:|
| S1 | **misma empresa**, seniority asimétrico, título parecido — *la configuración del FP* | 20 |
| S2 | misma empresa, mismo seniority, títulos parecidos (control) | 14 |
| S3 | misma empresa, títulos cercanos pero funciones distintas | 12 |
| S4 | misma empresa, **cross-portal** | 14 |

- **Independencia verificada contra la base**, no confiada al `NOT EXISTS` del
  muestreo: **0** vacantes en cohortes de dedup y **0** en sets de ranking.
- **Ceguera verificada por estructura**: solo `titulo, empresa, lugar, portal,
  salario, desc`; sin estrato, sin similitud, sin estado del detector. Barajado
  estable por hash del par.
- sha256: hoja `f0bb731e76aaf7d7` · md `642e156a07c3da8c`.

## 4quater. El script de deploy reponía la política sin validar

`nas_deploy_rehearsal_r5.sh` dejaba `cosine-baseline` inactiva y **exigía con un
`assert` que `hybrid-rrf` fuera la ÚNICA activa**. Es decir: cada despliegue
devolvía la política no validada al camino canónico, y el arreglo de §3.2 habría
durado hasta el siguiente deploy.

Es el mismo patrón que tumbó producción el 2026-08-29 con `wal_level`: **una
decisión de seguridad que vive solo en un artefacto que otro paso reescribe.**
Corregido para dejar las dos activas, con la validada primera por nombre —y por
tanto canónica— y la experimental en sombra. Verificado tras el deploy:
`cosine-baseline|t · hybrid-rrf|t`, canónica la primera.

## 4quinquies. Preview de gates (Fase 4.5) — desglosado y explicado

Ejecutado con `preview_cycle_task` (savepoint + rollback: no sella ni toca la racha).
Ciclo 2026-09-01, `cycle_eligible: false` — correcto: la ventana empezó antes del
congelado de v3; **el primer ciclo elegible es el que abre el 2026-09-02 a las 11:30**.

| Gate en rojo | Causa, verificada |
|---|---|
| `dedup_precision` (0,875) | el FP de AuroraSolar; pendiente del etiquetado independiente (§4ter) |
| `ndcg@10` ambos perfiles · `falsos_negativos` p1 | **es la calidad real de `cosine-baseline`**, no un residuo de hybrid: el feed vigente de ambos perfiles está firmado por `cosine-baseline/v1` (reevaluado por el beat a las 03:57 UTC tras el revert). Es el problema conocido que motivó hybrid; su arreglo es la Fase 3 con desarrollo independiente |
| `outbox_lag_p99` | **transitorio post-deploy y autocorregible**: 2.478 entregas quedaron con backoff durante la ventana del deploy y drenan a ~1.206/h con llegadas **cero** (medido). ETA ~2 h. El despachador está sano: `claimed 100, delivered 100, failed 0` en cada pasada |

> **Hipótesis descartada, y consta a propósito**: el crecimiento 1:1 del
> `oldest_pending_s` sugería inanición por el `ORDER BY next_attempt_at NULLS
> FIRST` (los eventos nuevos nacen NULL y se colarían por delante). La medida la
> refutó: llegadas 0/h, entregas 1.206/h, pendientes bajando. El más viejo crece
> solo porque su turno de backoff es de los últimos. No se «arregló» un orden que
> no estaba roto — pero la inanición ES posible si las llegadas saturan el lote
> (100/5min) con backlog fechado: queda anotada como riesgo a vigilar, no como bug.

## 5. Bloqueos restantes, por orden

1. **Falso positivo de dedup (AuroraSolar).** Requiere etiquetado independiente de
   la muestra ciega de seniority. **Sin él no hay verde posible en precisión.**
2. **Matching**: `nDCG@10 = 0` en ambos perfiles. La causa confirmada por el relevo
   —`_lexical_query` toma los primeros 32 tokens de title+skills y deja fuera
   `teacher`— **no se ha corregido en esta sesión**: hacerlo exige un conjunto de
   desarrollo independiente que tampoco existe. Los dos sets congelados **ya fueron
   observados** y no pueden usarse para afinar.
3. ~~Falso negativo histórico~~ — **CERRADO**: desplegado, backfill ejecutado e
   idempotente, `FN=0` medido (§4bis).
4. **Cola de 190 172 candidatos pendientes** creada por el backfill histórico: sin
   dimensionar y sin decisión sobre qué hacer con ella.

## 6. Rollback de R5

- **Política**: `UPDATE jobhunt.scoring_policies SET active = false WHERE name =
  'cosine-baseline';` devuelve el estado anterior exacto.
- **Imagen**: los servicios R5 siguen en `sha256:f8fe323b…`
  (`2c19837-c940d43844be`). Mientras no se recreen, no hay nada que revertir.
- **Datos**: no se alteró ninguna cohorte, etiqueta ni identidad.

## 7. Lo que NO se hizo, y por qué

- **No se tocó el detector** para el FP: la evidencia independiente disponible no
  cubre su configuración (§2).
- **No se autoetiquetó** la muestra ciega: sería la misma contaminación por otra vía.
- **No se ajustó ningún umbral** ni se miró el verdict del holdout para formular
  reglas.
- **No se ejecutó bootstrap de R5** ni se tocó producción.
