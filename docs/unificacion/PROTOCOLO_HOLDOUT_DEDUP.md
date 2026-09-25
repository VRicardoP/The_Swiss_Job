# Protocolo del holdout de dedup (cierre B-1, auditoría externa 2026-08-23)

> Fijado el 2026-08-23 ANTES de ejecutar el muestreo (requisito B-1.2).
> Ejecutar contra el corpus del NAS (BD core, schema `jobhunt`).
> El conjunto congelado actual (sets D del 2026-08-23 08:51 UTC) queda
> reclasificado como **development**: sirve para ajustar reglas/umbrales.
> El **holdout** que define este protocolo NO se usa jamás para ajustar,
> resolver candidatos ni depurar — solo para medir.

## Restricción declarada (honestidad)

El proyecto tiene UNA persona (el propietario). "Persona distinta" (B-1.1)
no es alcanzable literalmente; la independencia se aproxima con:
- etiquetado CIEGO: la hoja de juicios NO muestra estado en
  `dedup_candidates`, ni similitud, ni si el detector los encontró;
- muestreo por BLOQUEO LÉXICO (empresa + trigram de título), un mecanismo
  DISTINTO del detector (embeddings ANN + hash exacto), definido aquí antes
  de mirar resultado alguno;
- semilla fija y SQL literal: cualquiera puede reproducir la extracción.
Esta aproximación queda registrada como limitación en el informe del gate.

## Estratos y tamaños (definidos 2026-08-23, antes del muestreo)

Marco muestral: vacantes vigentes con revisión canónica (mismo join del
corpus del detector, SIN tocar `dedup_candidates`).

| Estrato | Mecanismo de formación del par | n |
|---|---|---|
| H1 cross-source, bloqueo léxico | misma empresa normalizada + similitud trigram de título ≥ 0.55, fuentes distintas | 15 |
| H2 intra-source, bloqueo léxico | ídem, misma fuente, external_id distinto | 15 |
| H3 texto idéntico, ciudad DISTINTA | mismo `text_hash`, `location` distinta (cualquier fuente) | 10 |
| H4 texto idéntico, misma ciudad | mismo `text_hash`, misma `location`, misma fuente | 8 |
| H5 control aleatorio | dos vacantes al azar del corpus | 12 |

Total: 60 pares. Si un estrato no alcanza su n, se registra el déficit y NO
se rellena desde otro estrato.

## Reglas de juicio (las mismas que el development, registradas)

- `duplicate` = la MISMA vacante de empleo (mismo puesto, misma empresa,
  mismo proceso de selección), aunque el texto difiera.
- Publicación multi-ciudad del mismo puesto (Flix Berlín vs Múnich) =
  `distinct` — regla ratificada 2026-08-23. H3 la PONE A PRUEBA: el juicio
  humano manda; si el propietario juzga `duplicate` donde la regla dice
  `distinct`, eso es un desacuerdo A REGISTRAR, no a corregir.
- `unsure` permitido; los `unsure` se excluyen del numerador y denominador y
  se reportan aparte.

## Procedimiento

1. `setseed(0.230823)` y ejecutar el SQL de muestreo (abajo) — una sola vez.
2. Generar hoja ciega (id_par, título/empresa/location/descripcion/URL de
   cada lado; SIN fuente… la fuente SÍ se muestra: ocultarla impediría
   juzgar; lo vetado es estado/similitud/procedencia del detector).
3. El propietario etiqueta los 60 pares de una sentada; los juicios se
   INSERTan en un set `holdout-dedup-2026-08-23` y se CONGELA.
4. Solo entonces: evaluar el detector contra el holdout (precision/recall
   por estrato y global). El resultado se publica tal cual salga.
5. Los **22** positivos del development incompatibles con la regla
   multi-ciudad (superconjunto de los 17 que citó la auditoría Nº1; auditoría
   Nº2 MENOR 1) se re-adjudican en la misma sesión con el criterio de arriba;
   criterio y desacuerdos quedan en el acta (§ del informe del ciclo).

## SQL de muestreo (literal, reproducible)

```sql
-- Marco: corpus elegible (mismo join del detector, sin dedup_candidates)
CREATE TEMP TABLE marco AS
SELECT v.id, orv.text_hash, sl.source_id,
       orv.content->>'title'    AS title,
       lower(coalesce(orv.content->>'company',''))  AS comp,
       coalesce(orv.content->>'location','')        AS loc
FROM vacancies v
JOIN offer_revisions orv ON orv.id = v.current_offer_revision_id
JOIN source_listing_incarnations pi ON pi.id = v.primary_incarnation_id
JOIN source_listings sl ON sl.id = pi.source_listing_id
WHERE v.archived_at IS NULL AND v.merged_into IS NULL;

SELECT setseed(0.230823);

-- H1/H2: bloqueo léxico (pg_trgm)
CREATE TEMP TABLE bloqueo AS
SELECT a.id AS va, b.id AS vb, (a.source_id <> b.source_id) AS cross_src
FROM marco a JOIN marco b
  ON a.comp = b.comp AND a.comp <> '' AND a.id < b.id
  AND similarity(a.title, b.title) >= 0.55
  AND a.text_hash <> b.text_hash;
(SELECT 'H1' AS estrato, va, vb FROM bloqueo WHERE cross_src ORDER BY random() LIMIT 15)
UNION ALL
(SELECT 'H2', va, vb FROM bloqueo WHERE NOT cross_src ORDER BY random() LIMIT 15)
UNION ALL
(SELECT 'H3', a.id, b.id FROM marco a JOIN marco b
   ON a.text_hash = b.text_hash AND a.loc <> b.loc AND a.id < b.id
 ORDER BY random() LIMIT 10)
UNION ALL
(SELECT 'H4', a.id, b.id FROM marco a JOIN marco b
   ON a.text_hash = b.text_hash AND a.loc = b.loc
  AND a.source_id = b.source_id AND a.id < b.id
 ORDER BY random() LIMIT 8)
UNION ALL
(SELECT 'H5', a.id, b.id FROM
   (SELECT id, row_number() OVER (ORDER BY random()) rn FROM marco) a
   JOIN (SELECT id, row_number() OVER (ORDER BY random()) rn FROM marco) b
   ON a.rn = b.rn + 1 AND a.id <> b.id
 LIMIT 12);
```

> Nota: `random()` tras `setseed` es reproducible dentro de la MISMA sesión
> psql; ejecutar todo el bloque en una sola sesión y guardar el resultado.

## Enmienda de ejecución (2026-08-23, al muestrear)

Verificación de independencia añadida ANTES de entregar la hoja: todo par
muestreado que ya exista en `labeled_dedup_pairs` (development, cualquier
sentido A/B) se EXCLUYE del holdout — la regla multi-ciudad se ratificó con
esos pares y medirían memoria, no generalización. Resultado del muestreo
real: excluidos H3-02 y H4-01; estratos efectivos H1=15, H2=15, H3=9, H4=7,
H5=12 (58 pares). El déficit queda registrado y no se rellena.

## Enmienda 2ª (2026-08-23, cierre de la auditoría Nº2)

- **Congelado REAL** (BLOQUEANTE 2): "se congela" = `freeze_dedup_cohort()`
  sobre `labeled_dedup_cohorts` (migración core0025) — el trigger-guard hace
  inmutables los pares de la cohorte en la BD (INSERT/UPDATE/DELETE ⇒
  excepción). El manifest del freeze lleva los SHA-256 del pre-registro.
- **Aislamiento del gate** (BLOQUEANTE 1): `_dedup_rows` y `_labels_ready_row`
  puntúan SOLO `source='holdout-dedup-2026-08-23'` (`DEDUP_EVAL_COHORT`).
  seed + curado quedan como development: diagnóstico, jamás gate.
- **Elegibilidad de ciclos** (BLOQUEANTE 3): `gate_status()` solo cuenta
  ciclos cuya ventana empieza DESPUÉS de `frozen_at` de la cohorte. El ciclo
  mixto del 2026-08-23 es inelegible por construcción.
- **Resultado por estrato** (IMPORTANTE 3): la evaluación del holdout se
  publica por estrato H1–H5 y global; el global se declara score del
  challenge-set (muestreo estratificado con clústeres — taxtalente/clera/
  enpal —, NO estimación poblacional sin ponderar). La hoja se enriquece con
  URL y texto completo SIN re-muestrear (misma muestra fija).

## Aviso operativo al etiquetador (revisión solo-código, área limpia con aviso)

Con 58 pares efectivos y `LABELS_MIN_DEDUP_PAIRS=50`, hay margen para **como
máximo 8 `unsure`** (los unsure no se insertan): al noveno, `labels_ready`
queda en rojo y la racha no puede arrancar hasta ampliar el holdout con un
segundo muestreo pre-registrado. Es fail-closed deliberado, no un error —
pero conviene saberlo ANTES de etiquetar.
