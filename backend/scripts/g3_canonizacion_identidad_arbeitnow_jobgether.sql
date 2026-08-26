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
-- URGENCIA: **ALTA — el fallo es PÉRDIDA SILENCIOSA DE DATOS *Y* DUPLICACIÓN
-- DEL CORPUS, a la vez.**
--
-- ⚠ RECTIFICACIÓN (G5, 2026-08-26). Las DOS redacciones anteriores de este
-- encabezado eran medias verdades y un operador decidía la urgencia leyéndolas:
-- la primera decía «duplicando el corpus» (y omitía la pérdida); la segunda
-- —de G4— decía que la duplicación «NO» ocurre (y omitía los clones). Ocurren
-- **AMBAS**, por dos ramas distintas, y el peor caso es su SUMA.
--
-- El fix cambia la identidad que emiten los providers y YA ESTÁ DESPLEGADO en
-- el código. Si una cosecha corre ANTES que este script, el
-- `ON CONFLICT (hash)` no reconoce NINGUNA de las 5.825 filas existentes
-- (4.233 arbeitnow + 1.592 jobgether — medido en producción 2026-08-26: el
-- 100 % cambia de hash). A partir de ahí, lo que pasa depende de si el portal
-- re-lista la vacante en la MISMA url o en una NUEVA:
--
--   RAMA 1 — el portal re-lista en la MISMA url. El INSERT con hash nuevo
--     CHOCA con el índice UNIQUE `ix_jobs_url`: `UniqueViolationError`, el
--     savepoint por-oferta la aborta y **la oferta se descarta sin refrescar
--     `last_seen_at`**. Exposición medida por corrida: 0 en la mayoría de los
--     días, pero 143/199 (72 %) el 2026-08-16 y 128/304 (42 %) el 08-17.
--     SEÑAL: desde G4 se tipa como `JobIdentityConflictError` y sale en
--     `summary["identity_conflicts"]` + `unhealthy`.
--
--   RAMA 2 — el portal re-lista con un id NUEVO en la url (que es EXACTAMENTE
--     el fenómeno que motivó canonizar: "…-stuttgart-459633" y
--     "…-stuttgart-198909" son la misma vacante). `jobs.url` es distinta, así
--     que **no hay choque alguno: el INSERT tiene ÉXITO**. Entra una fila
--     **CLON** con `first_seen_at` de hoy y la histórica **deja de refrescar
--     `last_seen_at` para siempre**. Corpus duplicado *y* fila original
--     abandonada, las dos cosas.
--     ESTADO MEDIDO (producción, 2026-08-26, SOLO LECTURA, RE-MEDIDO en G6):
--     **383 filas clon en arbeitnow** (4.233 filas para 3.850 identidades
--     canónicas, en 284 grupos) y **23 en jobgether** (1.592 filas / 1.569
--     identidades, 21 grupos): 406 en total, exactamente las que el PASO 5
--     fusiona. Las cifras que traían G4/G5 (388 y 81) contaban URLs canónicas
--     distintas, no identidades: el slug de jobgether sin su ObjectId NO es
--     único —«…/offer/account-manager» lo usan CUATRO empresas distintas— y
--     esas filas son vacantes DIFERENTES que el script, con razón, no fusiona,
--     porque el `hash` incluye título y empresa. Clones nuevos por día en la
--     última semana: 58 el 08-25, 52 el 08-21, 31 el 08-20, 25 el 08-19.
--     SEÑAL: hasta G5 **NINGUNA**. Ni `identity_conflicts` (no hay excepción
--     que contar), ni el dedup fuzzy (`find_fuzzy_duplicate` excluye a
--     propósito los pares de la MISMA fuente), ni `harvest_window.watch_drift`
--     (exige `recognized == 0` y esta deriva es PARCIAL). El run salía
--     `new=1, errors=0, identity_conflicts=0, unhealthy=[]`. Desde G5 la
--     detecta `Deduplicator.find_same_source_clone` y la cuenta
--     `summary["identity_clones"]` — **como ALARMA: no marca `duplicate_of`
--     ni desactiva nada.**
--
-- Y en las DOS ramas, las 5.825 filas históricas dejan de refrescar
-- `last_seen_at` → `cleanup_stale_jobs` las archiva y luego las BORRA al
-- cumplirse `max_age_days`, arrastrando por FK `ON DELETE CASCADE` sus
-- `match_results` y `generated_documents`; hasta entonces se sirven al usuario
-- sin re-verificar. La fuente, mientras tanto, ingiere las URLs nuevas, así que
-- `job_count > 0` y `classify(...)` la da por `ok`.
--
-- Nada de lo anterior REPARA la deriva: la hace visible. Cada `daily_harvest`
-- que pase sin ejecutar este script amplía el daño. Este script hace el
-- puente: deja las filas viejas con la identidad nueva, conservando su
-- `first_seen_at`.
--
-- ORDEN OBLIGATORIO (no es una recomendación): **parar el worker/beat →
-- `pg_dump` → ejecutar este script → rearrancar el worker**. El worker NO
-- debe correr una cosecha con el código nuevo sobre datos sin migrar.
--
-- ESTADO: **NO EJECUTADO contra ninguna base real**. Pendiente de aplicar.
--
-- HAY UN SEGUNDO SCRIPT PENDIENTE con la misma forma y la misma urgencia:
-- `g6_canonizacion_identidad_irishjobs.sql` (G6/P2-3: 40 filas clon en 919, el
-- portal reedita el slug conservando el `-job<id>`). Los DOS se aplican en la
-- MISMA parada del worker, en cualquier orden: no comparten ni una fila.
--
-- VALIDACIÓN (G6, 2026-08-26 — sustituye a la de G3/lote B). La fixture de G3
-- tenía **un clon por superviviente** y por eso NO podía alcanzar el fallo del
-- PASO 5 que G6 encontró: el ensayo montaba el escenario que esquiva la
-- condición. La fixture de ensayo vive ahora en
-- `g3_canonizacion_ensayo_g6.sql` (con la orden exacta para relanzarla) y sí
-- incluye esa forma. Ensayado contra `swissjobhunter_test` (NUNCA contra
-- `swissjobhunter`), 9 filas en 4 grupos:
--   · superviviente + DOS clones con match del MISMO usuario en los dos y
--     ninguno en el superviviente — la forma que ABORTABA;
--   · superviviente + clon con la SEÑAL del usuario en el CLON;
--   · superviviente + DOS clones con `job_applications` en los dos y
--     `generated_documents` en ambos;
--   · una oferta que la canonización no cambia.
-- Resultado ejecutado: **3 identidades reescritas, 5 clones fusionados, 13/13
-- aserciones en verde**, sin violación de unicidad, `first_seen_at` = el de la
-- aparición más antigua, `last_seen_at`/`is_active` heredados del grupo, la
-- fila de match con señal CONSERVADA, la candidatura enviada conservada, los
-- dos documentos generados reapuntados y las 3 FK restauradas. Los 3 hashes
-- canónicos resultantes COINCIDEN carácter a carácter con los que emiten hoy
-- `ArbeitnowProvider` y `JobgetherProvider` (verificado ejecutando su
-- `compute_hash` + `canonical_identity_*` sobre las mismas URLs). Todo dentro
-- de una transacción revertida; residuo verificado: 0 filas en las 5 tablas.
-- JAMÁS contra producción sin backup previo (`pg_dump`) y sin haberlo
-- ensayado antes sobre una copia. Orden OBLIGATORIO (ver arriba):
--   1) parar el worker/beat (que no arranque una cosecha a medias),
--   2) `pg_dump` de la base,
--   3) ejecutar este script sobre la COPIA y revisar el informe final,
--   4) ejecutarlo sobre la base real,
--   5) rearrancar el worker.
--
-- COTA G4 — **CERRADA en G6**. G4 dejó escrito que el `DELETE` del PASO 5 se
-- quedaba SIEMPRE con la fila del superviviente sin mirar si la del clon lleva
-- `feedback`, `feedback_implicit`, `draft_letter` o un `application_status`
-- avanzado. Ya no: el PASO 5 fusiona el grupo entero prefiriendo la fila CON
-- señal del usuario —la regla de `maintenance_tasks.py:283-294`— y solo a
-- igualdad de señal se queda con la del superviviente. Medido en producción
-- 2026-08-26 (SOLO LECTURA): 75 `match_results` de clones, **30** filas que la
-- fusión descarta y **0** de ellas con señal; 0 `job_applications` y 0
-- `generated_documents` de clones. El informe del PASO 8 imprime esas dos
-- cifras en cada ejecución, así que la fusión no ocurre en silencio.
-- Re-medirlo antes de aplicarlo si ha pasado tiempo.
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
--
-- ⚠ COTA CERRADA (G7/P3-8). `is_active = j.is_active OR g.any_active` a secas
-- puede RESUCITAR un superviviente que está inactivo PORQUE `mark_duplicate` lo
-- marcó: esa función pone `duplicate_of` Y `is_active = FALSE` a la vez
-- (`job_repository.py:536`), así que si alguno de sus clones sigue activo el OR
-- lo dejaría `is_active = TRUE` CON `duplicate_of` puesto — un estado que ni
-- `mark_duplicate` ni el `ON CONFLICT` producen jamás
-- (`job_repository.py:416-417`: `case((duplicate_of IS NOT NULL, False),
-- else_=True)`). Medido contra producción el 2026-08-26: de los 12
-- supervivientes con `duplicate_of` en las tres fuentes, los 12 están
-- inactivos, y 0 tienen un clon activo — así que hoy NO hay ningún caso. El
-- `AND j.duplicate_of IS NULL` cierra la cota sin cambiar nada de lo medido.
-- ---------------------------------------------------------------------
UPDATE jobs j
SET last_seen_at = GREATEST(j.last_seen_at, g.max_last_seen),
    is_active    = (j.is_active OR g.any_active) AND j.duplicate_of IS NULL
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
