-- =====================================================================
-- G3/P3-13 · Canonización de la identidad (`hash`) de arbeitnow y jobgether
-- =====================================================================
--
-- QUÉ HACE
-- --------
-- Reescribe el `hash` de las filas YA persistidas de `arbeitnow` y
-- `jobgether` al hash CANÓNICO que los providers empezaron a emitir con el
-- fix G3/P3-13 (identidad calculada sobre la URL sin el id volátil):
--
--   arbeitnow  ->  se descarta el sufijo `-<digitos>` final de la URL
--                  ("…-stuttgart-459633" y "…-stuttgart-198909" son la MISMA
--                  vacante reemitida).
--   jobgether  ->  se descarta el id inicial del slug (24 hex, ObjectId)
--                  en "https://jobgether.com/offer/<id>-<titulo>".
--
-- POR QUÉ HAY QUE EJECUTARLO **ANTES** DE LA SIGUIENTE COSECHA
-- -----------------------------------------------------------
-- El fix cambia la identidad que emiten los providers. Si la cosecha corre
-- ANTES que este script, el `ON CONFLICT (hash)` no reconocerá NINGUNA de las
-- ~5.700 filas existentes (4206 arbeitnow + 1536 jobgether): entrarían todas
-- como ofertas NUEVAS, con `first_seen_at` nuevo, disparando notificaciones y
-- digest masivos y duplicando el corpus. Este script hace el puente: deja las
-- filas viejas con la identidad nueva, conservando su `first_seen_at`.
--
-- ESTADO: **NO EJECUTADO contra ninguna base real**. Pendiente de aplicar.
--
-- VALIDACIÓN hecha (G3, lote B): el script se ensayó contra `swissjobhunter_test`
-- (NUNCA contra `swissjobhunter`) con una fixture sintética de 5 filas — 2 clones
-- de arbeitnow, 2 de jobgether y 1 oferta única. Resultado: 3 identidades
-- reescritas, 2 clones fusionados, `first_seen_at` = el de la aparición más
-- antigua y `last_seen_at` = el máximo del grupo; los 3 hashes resultantes
-- COINCIDEN carácter a carácter con los que emiten hoy `ArbeitnowProvider` y
-- `JobgetherProvider` tras el fix G3/P3-13. La fixture se borró al terminar.
-- JAMÁS contra producción sin backup previo (`pg_dump`) y sin haberlo
-- ensayado antes sobre una copia. Orden recomendado:
--   1) parar el worker/beat (que no arranque una cosecha a medias),
--   2) `pg_dump` de la base,
--   3) ejecutar este script sobre la COPIA y revisar el informe final,
--   4) ejecutarlo sobre la base real,
--   5) rearrancar el worker.
--
-- USO
-- ---
--   psql -U swissjob -d swissjobhunter -v ON_ERROR_STOP=1 \
--        -f g3_canonizacion_identidad_arbeitnow_jobgether.sql
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
-- Salvaguarda: solo se tocan las filas cuyo `hash` actual se REPRODUCE con los
-- campos almacenados y la URL real. Si una fila no verifica (título truncado a
-- 500, normalización posterior, etc.) no podemos garantizar que el hash nuevo
-- coincidirá con el que emitirá el provider, así que se deja fuera y se
-- reporta al final.
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
        CASE j.source
            WHEN 'arbeitnow' THEN regexp_replace(j.url, '-\d+/?$', '')
            WHEN 'jobgether' THEN regexp_replace(
                j.url,
                '^(https://jobgether\.com/offer/)[0-9a-f]{24}-',
                '\1'
            )
        END AS canonical_url
    FROM jobs j
    WHERE j.source IN ('arbeitnow', 'jobgether')
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
--    sus claves únicas (user_id, job_hash): si el usuario ya tiene fila para
--    el superviviente, la del clon se descarta (la del superviviente es la
--    buena: es la más antigua).
-- ---------------------------------------------------------------------
DELETE FROM match_results m
USING g3_losers l
WHERE m.job_hash = l.loser_hash
  AND EXISTS (
      SELECT 1 FROM match_results m2
      WHERE m2.user_id = m.user_id AND m2.job_hash = l.survivor_hash
  );

UPDATE match_results m
SET job_hash = l.survivor_hash
FROM g3_losers l
WHERE m.job_hash = l.loser_hash;

DELETE FROM job_applications a
USING g3_losers l
WHERE a.job_hash = l.loser_hash
  AND EXISTS (
      SELECT 1 FROM job_applications a2
      WHERE a2.user_id = a.user_id AND a2.job_hash = l.survivor_hash
  );

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
SELECT 'intactas (ya canonicas o no verificadas)',
       count(*)
FROM jobs j
WHERE j.source IN ('arbeitnow', 'jobgether')
  AND j.hash NOT IN (SELECT new_hash FROM g3_survivors)
UNION ALL
SELECT 'arbeitnow tras el script', count(*) FROM jobs WHERE source = 'arbeitnow'
UNION ALL
SELECT 'jobgether tras el script', count(*) FROM jobs WHERE source = 'jobgether';

-- ---------------------------------------------------------------------
-- CAMBIAR A `COMMIT;` PARA APLICARLO DE VERDAD.
-- ---------------------------------------------------------------------
ROLLBACK;
