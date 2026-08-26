-- =====================================================================
-- G6/P2-3 · Canonización de la identidad (`hash`) de irishjobs
-- =====================================================================
--
-- QUÉ HACE
-- --------
-- Reescribe el `hash` de las filas YA persistidas de `irishjobs` al hash
-- CANÓNICO que el scraper emite desde el fix G6/P2-3 (identidad calculada
-- sobre el id de plataforma StepStone, sin el slug y sin el host):
--
--   ".../job/lead-ms-fabric-architect/ntt-data-services-inc-job107803777"
--   ".../job/lead-architect/ntt-data-services-inc-job107803777"
--        ->  "https://www.irishjobs.ie/job/job107803777"
--
-- POR QUÉ HACE FALTA
-- ------------------
-- El portal REEDITA el slug del anuncio (le cambia el título) conservando el
-- `-job<id>` final. Con la URL completa como identidad, cada reedición entraba
-- como fila CLON y la histórica dejaba de refrescar `last_seen_at` para
-- siempre: la MISMA pérdida silenciosa que G3/P3-13 documentó en arbeitnow, en
-- una fuente que hasta G6 nadie había medido porque el diagnóstico se acotó por
-- inspección a dos providers.
--
-- ESTADO MEDIDO (producción `swissjobhunter`, 2026-08-26, SOLO LECTURA):
--   · 919 filas de irishjobs, TODAS con `-job<id>` reconocible y TODAS
--     reproduciendo su propio `hash` (0 quedan fuera del mapa);
--   · 879 identidades canónicas -> **40 filas clon en 40 grupos**;
--   · ritmo de aparición: 8 (semana del 08-03), 10 (08-10), **22** (08-17);
--   · 61 `match_results` apuntan a filas de irishjobs.
--
-- LO QUE ESTE SCRIPT **NO** ARREGLA (medido, no supuesto). De los 89 grupos de
-- irishjobs con descripción idéntica byte a byte:
--   · 38 son deriva de slug con el MISMO id  -> los arregla este script;
--   · 35 son el mismo anuncio en los DOS hosts con ids DISTINTOS (cada host
--     acuña el suyo: 0 ids compartidos sobre 919 filas) -> NO son resolubles
--     por identidad de URL, y fusionarlos por título+empresa destruiría
--     vacantes reales;
--   · 16 son re-emisiones con id nuevo en el mismo host -> tampoco.
-- La alarma `Deduplicator.find_same_source_clone` los sigue vigilando.
--
-- ORDEN OBLIGATORIO (no es una recomendación): **parar el worker/beat →
-- `pg_dump` → ejecutar este script → rearrancar el worker**. El worker NO debe
-- correr una cosecha con el código nuevo sobre datos sin migrar: el `hash`
-- nuevo no reconocería ninguna de las 919 filas y el `ON CONFLICT (hash)`
-- fallaría contra `ix_jobs_url` (rama 1: la oferta se descarta sin refrescar)
-- o crearía un clon (rama 2). Es el mismo par de ramas de
-- `g3_canonizacion_identidad_arbeitnow_jobgether.sql`; si ese está pendiente,
-- se aplican LOS DOS en la misma parada.
--
-- ESTADO: **NO EJECUTADO contra ninguna base real**. Pendiente de aplicar.
--
-- VALIDACIÓN (G6, ejecutada): ensayado contra `swissjobhunter_test` con la
-- fixture `g6_canonizacion_ensayo_irishjobs.sql`, que incluye deriva de slug,
-- el par cross-host con ids distintos (que NO debe fusionarse), la colisión
-- clon↔clon de `uq_match_user_job` que abortaba el script hermano, y una
-- oferta intacta. Todo dentro de una transacción revertida; residuo: 0 filas.
--
-- USO
-- ---
--   psql -U swissjob -d swissjobhunter -v ON_ERROR_STOP=1 \
--        -f g6_canonizacion_identidad_irishjobs.sql
--
-- Todo ocurre dentro de UNA transacción y termina en ROLLBACK: es un ENSAYO
-- que imprime el informe sin tocar nada. Para aplicarlo de verdad, cambiar la
-- última línea `ROLLBACK;` por `COMMIT;` (está marcada).
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Mapa old_hash -> new_hash
--
-- El hash canónico se recalcula con la MISMA fórmula que
-- BaseJobProvider.compute_hash: md5("titulo|empresa|url"), título y empresa en
-- minúsculas y sin espacios en los extremos.
--
-- `canonical_url` reproduce `scrapers/irishjobs.canonical_identity_url`: host
-- canónico `_HOSTS[0]` + "/job/job" + el id de plataforma. Las filas sin
-- `-job<id>` reconocible se quedan fuera (el scraper también las deja con su
-- URL: identidad volátil antes que identidad ambigua).
--
-- Salvaguarda: solo se tocan las filas cuyo `hash` actual se REPRODUCE con los
-- campos almacenados y la URL real.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE g3_map ON COMMIT DROP AS
WITH candidates AS (
    SELECT
        j.hash AS old_hash,
        j.source,
        j.first_seen_at,
        md5(
            lower(btrim(j.title)) || '|' || lower(btrim(j.company)) || '|' || j.url
        ) AS check_hash,
        CASE
            WHEN j.url ~ '-job\d+/?$' THEN
                'https://www.irishjobs.ie/job/job'
                || substring(j.url from '-job(\d+)/?$')
        END AS canonical_url
    FROM jobs j
    WHERE j.source = 'irishjobs'
)
SELECT
    c.old_hash,
    c.source,
    c.first_seen_at,
    md5(
        lower(btrim(j.title)) || '|' || lower(btrim(j.company)) || '|' || c.canonical_url
    ) AS new_hash
FROM candidates c
JOIN jobs j ON j.hash = c.old_hash
WHERE c.check_hash = c.old_hash        -- la fila reproduce su propio hash
  AND c.canonical_url IS NOT NULL      -- y tiene id de plataforma reconocible
  AND c.canonical_url <> j.url;        -- y la canonización la cambia de verdad

CREATE UNIQUE INDEX ON g3_map (old_hash);
CREATE INDEX ON g3_map (new_hash);

-- ---------------------------------------------------------------------
-- 2. Superviviente por identidad canónica
--
-- Ante colisión (N filas que colapsan al mismo hash canónico) se conserva la
-- MÁS ANTIGUA por `first_seen_at` — así el `first_seen_at` de la vacante es el
-- de su primera aparición real y no el del último clon. Desempate estable por
-- `old_hash` para que el script sea determinista.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE g3_survivors ON COMMIT DROP AS
SELECT DISTINCT ON (new_hash)
    new_hash,
    old_hash AS survivor_hash
FROM g3_map
ORDER BY new_hash, first_seen_at ASC, old_hash ASC;

CREATE UNIQUE INDEX ON g3_survivors (new_hash);
CREATE UNIQUE INDEX ON g3_survivors (survivor_hash);

CREATE TEMP TABLE g3_losers ON COMMIT DROP AS
SELECT m.old_hash AS loser_hash, s.survivor_hash, m.new_hash
FROM g3_map m
JOIN g3_survivors s ON s.new_hash = m.new_hash
WHERE m.old_hash <> s.survivor_hash;

CREATE UNIQUE INDEX ON g3_losers (loser_hash);

-- ---------------------------------------------------------------------
-- 3. Guarda: el hash canónico no puede chocar con una fila AJENA al mapa
--    (otra fuente, u otra fila de la misma fuente que no entró en el mapa).
--    Si ocurre, se aborta: hay que revisarlo a mano, no adivinar.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    n_conflict int;
BEGIN
    SELECT count(*) INTO n_conflict
    FROM g3_survivors s
    JOIN jobs j ON j.hash = s.new_hash
    WHERE j.hash <> s.survivor_hash;

    IF n_conflict > 0 THEN
        RAISE EXCEPTION
            'ABORTADO: % hash canónicos ya existen en filas ajenas al mapa', n_conflict;
    END IF;
END
$$;

-- ---------------------------------------------------------------------
-- 4. El superviviente hereda la última vista y la actividad del grupo
--    (first_seen_at NO se toca: ya es el mínimo del grupo por construcción).
-- ---------------------------------------------------------------------
UPDATE jobs j
SET last_seen_at = GREATEST(j.last_seen_at, g.max_last_seen),
    is_active    = j.is_active OR g.any_active
FROM (
    SELECT l.survivor_hash,
           max(jl.last_seen_at) AS max_last_seen,
           bool_or(jl.is_active) AS any_active
    FROM g3_losers l
    JOIN jobs jl ON jl.hash = l.loser_hash
    GROUP BY l.survivor_hash
) g
WHERE j.hash = g.survivor_hash;

-- ---------------------------------------------------------------------
-- 5. Reapuntar las tablas hijas de los clones al superviviente, respetando
--    sus claves únicas (user_id, job_hash).
--
-- ⚠ RECTIFICACIÓN (G6/P1-1, 2026-08-26). La versión anterior de este paso
-- ABORTABA sobre los datos de producción de hoy. Su guarda solo miraba la
-- colisión *clon ↔ superviviente* (`EXISTS` de una fila del superviviente) y
-- un superviviente puede tener VARIOS clones: si el mismo usuario tiene fila
-- en DOS clones y ninguna en el superviviente, el `EXISTS` no encontraba nada,
-- el `DELETE` no borraba ni una, y el `UPDATE` apuntaba las DOS al mismo
-- `survivor_hash` → `duplicate key value violates unique constraint
-- "uq_match_user_job"`. Medido en producción 2026-08-26: 60 grupos canónicos
-- con >= 2 clones y **1** grupo (user 46f0b4c2…, superviviente 140b0440…) con
-- la colisión clon↔clon exacta. Fallaba cerrado (todo en una transacción), pero
-- dejaba al operador con el worker parado y la migración a medias.
--
-- Ahora el grupo se fusiona ENTERO —la fila del superviviente más las de TODOS
-- sus clones— conservando UNA sola por (user_id, superviviente), con este
-- orden de preferencia:
--   1) la que tiene SEÑAL del usuario (`feedback`, `feedback_implicit`,
--      `draft_letter`, o `application_status` distinto del inicial
--      'detected') — la misma regla que `maintenance_tasks.py:283-294`;
--   2) a igualdad de señal, la del SUPERVIVIENTE (es la fila más antigua);
--   3) desempate estable por `id`, para que el script sea determinista.
--
-- Esto cierra además la COTA que el encabezado declaraba en G4 ("el DELETE se
-- queda SIEMPRE con la del superviviente sin mirar la señal"): ya no es cierta.
-- Las filas descartadas se CUENTAN en el informe del PASO 8 (con cuántas
-- llevaban señal), para que la fusión no ocurra en silencio.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE g3_mr_merge ON COMMIT DROP AS
SELECT
    m.id,
    (m.feedback IS NOT NULL
     OR m.feedback_implicit IS NOT NULL
     OR m.draft_letter IS NOT NULL
     OR m.application_status <> 'detected') AS has_signal,
    row_number() OVER (
        PARTITION BY m.user_id, COALESCE(l.survivor_hash, m.job_hash)
        ORDER BY (m.feedback IS NOT NULL
                  OR m.feedback_implicit IS NOT NULL
                  OR m.draft_letter IS NOT NULL
                  OR m.application_status <> 'detected') DESC,
                 (l.loser_hash IS NULL) DESC,
                 m.id ASC
    ) AS rn
FROM match_results m
LEFT JOIN g3_losers l ON l.loser_hash = m.job_hash
WHERE m.job_hash IN (SELECT loser_hash FROM g3_losers)
   OR m.job_hash IN (SELECT survivor_hash FROM g3_losers);

CREATE UNIQUE INDEX ON g3_mr_merge (id);

DELETE FROM match_results m
USING g3_mr_merge x
WHERE m.id = x.id AND x.rn > 1;

UPDATE match_results m
SET job_hash = l.survivor_hash
FROM g3_losers l
WHERE m.job_hash = l.loser_hash;

-- `job_applications` tiene la MISMA forma de clave única
-- (`uq_application_user_job`) y por tanto el mismo fallo. Aquí toda fila es
-- señal del usuario por definición, así que el orden de preferencia es:
-- candidatura ya enviada (`applied_at`), luego la del superviviente, luego la
-- más antigua. Hoy son 0 filas (medido 2026-08-26), pero la guarda queda con
-- la forma correcta.
CREATE TEMP TABLE g3_app_merge ON COMMIT DROP AS
SELECT
    a.id,
    row_number() OVER (
        PARTITION BY a.user_id, COALESCE(l.survivor_hash, a.job_hash)
        ORDER BY (a.applied_at IS NOT NULL) DESC,
                 (l.loser_hash IS NULL) DESC,
                 a.created_at ASC,
                 a.id ASC
    ) AS rn
FROM job_applications a
LEFT JOIN g3_losers l ON l.loser_hash = a.job_hash
WHERE a.job_hash IN (SELECT loser_hash FROM g3_losers)
   OR a.job_hash IN (SELECT survivor_hash FROM g3_losers);

CREATE UNIQUE INDEX ON g3_app_merge (id);

DELETE FROM job_applications a
USING g3_app_merge x
WHERE a.id = x.id AND x.rn > 1;

UPDATE job_applications a
SET job_hash = l.survivor_hash
FROM g3_losers l
WHERE a.job_hash = l.loser_hash;

UPDATE generated_documents d
SET job_hash = l.survivor_hash
FROM g3_losers l
WHERE d.job_hash = l.loser_hash;

-- `jobs.duplicate_of` es una referencia lógica a jobs.hash (sin FK).
UPDATE jobs j
SET duplicate_of = l.survivor_hash
FROM g3_losers l
WHERE j.duplicate_of = l.loser_hash;

-- ---------------------------------------------------------------------
-- 6. Borrar los clones (ya sin hijos que perder).
-- ---------------------------------------------------------------------
DELETE FROM jobs j USING g3_losers l WHERE j.hash = l.loser_hash;

-- ---------------------------------------------------------------------
-- 7. Reescribir el hash de los supervivientes.
--
-- Las tres FK a jobs(hash) son ON DELETE CASCADE pero NO ON UPDATE CASCADE, y
-- no son DEFERRABLE: hay que soltarlas para reescribir padre e hijos, y se
-- vuelven a crear IDÉNTICAS al terminar (el esquema queda como estaba: no se
-- necesita ninguna migración Alembic).
-- ---------------------------------------------------------------------
ALTER TABLE match_results DROP CONSTRAINT match_results_job_hash_fkey;
ALTER TABLE job_applications DROP CONSTRAINT job_applications_job_hash_fkey;
ALTER TABLE generated_documents DROP CONSTRAINT generated_documents_job_hash_fkey;

UPDATE jobs j
SET hash = s.new_hash
FROM g3_survivors s
WHERE j.hash = s.survivor_hash;

UPDATE match_results m
SET job_hash = s.new_hash
FROM g3_survivors s
WHERE m.job_hash = s.survivor_hash;

UPDATE job_applications a
SET job_hash = s.new_hash
FROM g3_survivors s
WHERE a.job_hash = s.survivor_hash;

UPDATE generated_documents d
SET job_hash = s.new_hash
FROM g3_survivors s
WHERE d.job_hash = s.survivor_hash;

UPDATE jobs j
SET duplicate_of = s.new_hash
FROM g3_survivors s
WHERE j.duplicate_of = s.survivor_hash;

ALTER TABLE match_results
    ADD CONSTRAINT match_results_job_hash_fkey
    FOREIGN KEY (job_hash) REFERENCES jobs(hash) ON DELETE CASCADE;
ALTER TABLE job_applications
    ADD CONSTRAINT job_applications_job_hash_fkey
    FOREIGN KEY (job_hash) REFERENCES jobs(hash) ON DELETE CASCADE;
ALTER TABLE generated_documents
    ADD CONSTRAINT generated_documents_job_hash_fkey
    FOREIGN KEY (job_hash) REFERENCES jobs(hash) ON DELETE CASCADE;

-- ---------------------------------------------------------------------
-- 8. Informe
-- ---------------------------------------------------------------------
SELECT 'reescritas'            AS concepto, count(*) AS filas FROM g3_survivors
UNION ALL
SELECT 'clones fusionados',    count(*) FROM g3_losers
UNION ALL
SELECT 'match_results descartados por la fusion',
       count(*) FROM g3_mr_merge WHERE rn > 1
UNION ALL
SELECT '  ... de ellos CON senal del usuario',
       count(*) FROM g3_mr_merge WHERE rn > 1 AND has_signal
UNION ALL
SELECT 'job_applications descartadas por la fusion',
       count(*) FROM g3_app_merge WHERE rn > 1
UNION ALL
SELECT 'intactas (ya canonicas o no verificadas)',
       count(*)
FROM jobs j
WHERE j.source = 'irishjobs'
  AND j.hash NOT IN (SELECT new_hash FROM g3_survivors)
UNION ALL
SELECT 'irishjobs tras el script', count(*) FROM jobs WHERE source = 'irishjobs';

-- ---------------------------------------------------------------------
-- CAMBIAR A `COMMIT;` PARA APLICARLO DE VERDAD.
-- ---------------------------------------------------------------------
ROLLBACK;
