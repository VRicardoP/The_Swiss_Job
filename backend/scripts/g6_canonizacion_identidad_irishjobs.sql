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
-- LO QUE ESTE SCRIPT **NO** ARREGLA (medido, no supuesto; cifras RECTIFICADAS
-- en G8/P3-7d — el desglose 38+35+16 = 89 era internamente coherente con un
-- total equivocado). Re-medido el 2026-08-26 (SOLO LECTURA):
--   · **105** grupos de irishjobs comparten `description` byte a byte (no 89);
--     ninguna de las 919 filas tiene `description` NULL ni vacía;
--   · los **40** clones de deriva de slug comparten los 40 su descripción con
--     su superviviente (no «38 de 40») -> los arregla este script;
--   · el resto son el mismo anuncio en los DOS hosts con ids DISTINTOS (cada
--     host acuña el suyo: 0 ids compartidos sobre 919 filas) o re-emisiones con
--     id nuevo en el mismo host -> NO son resolubles por identidad de URL, y
--     fusionarlos por título+empresa destruiría vacantes reales.
--   · **4 grupos** (`-job107803769`, `…775`, `…776`, `-job107875381`) comparten
--     el MISMO `-job<id>` con TÍTULOS DISTINTOS: son 8 filas activas que este
--     script deja con identidades distintas sobre la MISMA url canónica
--     (G8/P3-7e, omisión que G7 ya señaló). AGRAVANTE: `url` SÍ se refresca en
--     el upsert (`job_repository.py:147-158`, `set_<col> = excluded.col`), así
--     que si el portal revierte el slug a uno ya usado por la gemela, el
--     `SET url` choca con `ix_jobs_url` -> `JobIdentityConflictError` -> oferta
--     descartada sin refrescar `last_seen_at`. La rama 1 que este script existe
--     para eliminar queda PERMANENTE para esos 4 pares.
-- La alarma `Deduplicator.find_same_source_clone` los sigue vigilando.
--
-- ORDEN OBLIGATORIO (no es una recomendación). El worker legacy NO debe correr
-- una cosecha con el código nuevo sobre datos sin migrar: el `hash` nuevo no
-- reconocería ninguna de las 919 filas y el `ON CONFLICT (hash)` fallaría
-- contra `ix_jobs_url` (rama 1: la oferta se descarta sin refrescar) o crearía
-- un clon (rama 2). Es el mismo par de ramas de
-- `g3_canonizacion_identidad_arbeitnow_jobgether.sql`; si ese está pendiente,
-- se aplican LOS DOS en la misma parada.
--
-- ORDEN OPERATIVO COMPLETO (G8/P2-6 — sustituye al «parar el worker → dump →
-- ejecutar → rearrancar» anterior, que NO contemplaba la sombra CDC):
--   1) `docker compose stop worker worker-ai`   (cosecha legacy)
--   2) `docker compose stop core-worker`        (proyector de la sombra;
--      `core-capture` puede seguir: el PASO 7c va en la misma transacción que
--      la reescritura del hash y la decodificación lógica solo entrega la
--      transacción al COMMIT)
--   3) `pg_dump` de la base, esquema `jobhunt` incluido
--   4) ensayo sobre la COPIA (o `swissjobhunter_test` + fixture) y revisión
--      del informe: las dos líneas `sombra: …` deben cuadrar
--   5) ejecutar sobre la base real (última línea `COMMIT;`) — LOS DOS scripts
--   6) `docker compose start core-worker worker worker-ai`
--   7) comprobación posterior (SOLO LECTURA) de slots huérfanos por fuente
-- El detalle completo, con el porqué de cada paso y la comprobación final,
-- está en el encabezado de `g3_canonizacion_identidad_arbeitnow_jobgether.sql`.
-- NO hay que soltar ni recrear el slot `jobhunt_shadow` ni re-sembrar el
-- snapshot: el PASO 7c reapunta los slots de `jobhunt.source_listings` en la
-- misma transacción, y el GATE-SOMBRA sigue siendo válido.
--
-- ESTADO: **APLICADO a `swissjobhunter` (local) el 2026-08-27**, autorizado por
-- el propietario, en la misma parada de workers que
-- `g3_canonizacion_identidad_arbeitnow_jobgether.sql`. Ensayo en seco previo
-- contra los mismos datos y ejecución con copia temporal en COMMIT: cifras
-- idénticas en las dos pasadas. Resultado: 879 reescritas, 40 clones
-- fusionados, 0 match_results descartados, 879 slots de sombra reapuntados,
-- 40 slots de clones.
-- NO aplicado todavía en el NAS: allí corren imágenes anteriores a estos fixes.
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
--
-- ⚠ G8/P3-7f — LO QUE EL SUPERVIVIENTE **NO** HEREDA: el PASO 7b le pasa
-- `last_seen_at` e `is_active` del grupo, y nada más (`url`, `description`,
-- `salary_*`, `published_at`, `content_hash`, `embedding` se quedan en la
-- versión vieja aunque el clon borrado traiga el contenido actual; en 294 de
-- los 345 grupos con clones de las tres fuentes algún clon es más reciente).
-- Se autocura en la primera cosecha posterior.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE g3_survivors ON COMMIT DROP AS
SELECT DISTINCT ON (new_hash)
    new_hash,
    old_hash AS survivor_hash,
    source
FROM g3_map
ORDER BY new_hash, first_seen_at ASC, old_hash ASC;

CREATE UNIQUE INDEX ON g3_survivors (new_hash);
CREATE UNIQUE INDEX ON g3_survivors (survivor_hash);

CREATE TEMP TABLE g3_losers ON COMMIT DROP AS
SELECT m.old_hash AS loser_hash, s.survivor_hash, m.new_hash, m.source
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
-- 4. El superviviente hereda la última vista y la actividad del grupo:
--    aquí solo se CALCULA (los clones todavía existen); se APLICA en el
--    PASO 7b, cuando el superviviente ya lleva su hash canónico.
--    (first_seen_at NO se toca: ya es el mínimo del grupo por construcción).
--
-- ⚠ G8/P2-6 — POR QUÉ SE CALCULA AQUÍ Y SE APLICA DESPUÉS. `jobs` es tabla
-- CONTRACTUAL del CDC de la sombra, con `"pk": "hash"`. Un `UPDATE` del
-- superviviente ANTES del PASO 7 viaja al proyector bajo el hash VIEJO, que el
-- PASO 7c ya habrá reapuntado: no resuelve slot, el sink intenta dar de alta
-- uno nuevo con la MISMA `url_normalized`, choca con `uq_listing_source_url` y
-- sale por `logger.warning("sink: listing %r saltado")`. Aplicando la herencia
-- DESPUÉS del PASO 7, el único evento del superviviente va bajo el hash
-- canónico y el proyector lo resuelve por su camino normal. Ver la nota
-- extendida y las cifras medidas en el script hermano
-- `g3_canonizacion_identidad_arbeitnow_jobgether.sql`.
--
-- ⚠ COTA CERRADA (G7/P3-8). `is_active = j.is_active OR g.any_active` a secas
-- puede RESUCITAR un superviviente que está inactivo PORQUE `mark_duplicate` lo
-- marcó: esa función pone `duplicate_of` Y `is_active = FALSE` a la vez
-- (`job_repository.py:536`), así que si alguno de sus clones sigue activo el OR
-- lo dejaría `is_active = TRUE` CON `duplicate_of` puesto — un estado que ni
-- `mark_duplicate` ni el `ON CONFLICT` producen jamás
-- (`job_repository.py:416-417`: `case((duplicate_of IS NOT NULL, False),
-- else_=True)`).
--
-- ⚠ G8/P3-7a — «un estado que ni `mark_duplicate` ni el `ON CONFLICT` producen
-- jamás» es FALSO como invariante global: producción tiene **1 fila**
-- (`workingnomads`, `0c089993…`) activa con `duplicate_of` puesto, sin
-- productor identificado y preexistente. No la toca ninguno de los dos
-- scripts; se declara aquí porque el ORDEN OPERATIVO manda ensayar sobre una
-- copia de producción y una aserción sin acotar daría `f` por ella.
--
-- ⚠ G8/P3-7b — el denominador correcto. Medido de nuevo el 2026-08-26 (SOLO
-- LECTURA, las tres fuentes): 12 filas con `duplicate_of` puesto, de las que
-- **10 son supervivientes** del mapa y solo **2 son supervivientes CON
-- clones** — los únicos que el `OR g.any_active` puede tocar. Los 12 están
-- inactivos y ninguno tiene un clon activo, así que hoy NO hay ningún caso; el
-- `AND j.duplicate_of IS NULL` cierra la cota sin cambiar nada de lo medido.
-- Lo que sí cambia es la MORDIDA: el GRUPO E/D de la fixture de ensayo monta
-- por fin esa forma y sin el `AND` el ensayo aborta (G8/P2-5).
-- ---------------------------------------------------------------------
CREATE TEMP TABLE g3_inherit ON COMMIT DROP AS
SELECT l.survivor_hash,
       max(jl.last_seen_at) AS max_last_seen,
       bool_or(jl.is_active) AS any_active
FROM g3_losers l
JOIN jobs jl ON jl.hash = l.loser_hash
GROUP BY l.survivor_hash;

CREATE UNIQUE INDEX ON g3_inherit (survivor_hash);

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
-- "uq_match_user_job"`.
--
-- ⚠ G8/P3-7c — esas cifras eran las del script HERMANO. Re-medido el
-- 2026-08-26 (SOLO LECTURA): los «60 grupos canónicos con >= 2 clones y 1
-- grupo con la colisión clon↔clon exacta» son de arbeitnow/jobgether. En
-- **irishjobs** hay **40 grupos con clones y 0 con dos o más**: los 40 clones
-- están en 40 grupos de 2, y la colisión clon↔clon es ESTRUCTURALMENTE
-- INALCANZABLE hoy en esta fuente. La guarda se conserva igual —el portal
-- puede reeditar el slug una tercera vez— y el GRUPO A de la fixture de ensayo
-- sigue modelando esa forma a propósito, aunque la fuente no la presente.
-- Fallaba cerrado (todo en una transacción), pero dejaba al operador con el
-- worker parado y la migración a medias.
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
-- 7b. Aplicar la herencia calculada en el PASO 4, ya sobre el hash canónico
--     (ver la nota de G8/P2-6 en el PASO 4: así el superviviente emite UN
--     solo evento CDC y va bajo la pk que el PASO 7c deja en los slots).
-- ---------------------------------------------------------------------
UPDATE jobs j
SET last_seen_at = GREATEST(j.last_seen_at, g.max_last_seen),
    is_active    = (j.is_active OR g.any_active) AND j.duplicate_of IS NULL
FROM g3_inherit g
JOIN g3_survivors s ON s.survivor_hash = g.survivor_hash
WHERE j.hash = s.new_hash;

-- ---------------------------------------------------------------------
-- 7c. Reapuntar los slots de la SOMBRA CDC al hash canónico (G8/P2-6).
--
-- `jobs` es tabla contractual del slot lógico `jobhunt_shadow` con
-- `"pk": "hash"` (`jobhunt_core/shadow/capture.py`), y `jobhunt.source_listings`
-- guarda ese hash LITERAL en `external_id` (el proyector construye
-- `RawListing(external_id=pk, …)`). Al reescribir la pk, wal2json emite
-- `columns.hash = <NUEVO>` y deja el viejo solo en `identity`/`oldkeys`
-- —verificado ejecutando un slot wal2json de prueba sobre
-- `swissjobhunter_test`—, y `capture._change_row` se queda con `columns`: el
-- hash viejo NO genera ningún evento. Sin este paso, el slot antiguo queda
-- abierto para siempre y el alta del nuevo choca con `uq_listing_source_url`
-- (el script no toca `jobs.url`) → `ON CONFLICT DO NOTHING` → la fila legacy
-- se vuelve INVISIBLE para la sombra sin un solo error. Medido contra
-- producción el 2026-08-26 (SOLO LECTURA): 5.263 slots de arbeitnow/jobgether
-- + 879 de irishjobs quedarían huérfanos, más 411 de clones.
--
-- Los slots de los CLONES no se tocan: su fila muere en el PASO 6 y el `op=D`
-- correspondiente cierra su encarnación por el camino normal del proyector.
--
-- Va en la MISMA transacción que la reescritura del hash a propósito: la
-- decodificación lógica solo entrega la transacción al COMMIT (wal2json v2 sin
-- streaming de transacciones en curso), así que la sombra jamás observa un
-- estado intermedio.
--
-- Si el esquema `jobhunt` no existe (despliegue sin Fase B) el paso es un
-- no-op declarado en el informe.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE g3_shadow_report (concepto text, filas bigint) ON COMMIT DROP;

DO $$
DECLARE
    n_colision bigint;
    n_remap    bigint;
    n_cierre   bigint;
BEGIN
    IF to_regclass('jobhunt.source_listings') IS NULL THEN
        INSERT INTO g3_shadow_report
        VALUES ('sombra: esquema jobhunt ausente, PASO 7c omitido', 0);
        RETURN;
    END IF;

    -- Guarda: el external_id canónico no puede estar ya ocupado en la misma
    -- fuente (violaría `uq_listing_source_external`). Se aborta, no se adivina.
    SELECT count(*) INTO n_colision
    FROM g3_survivors s
    JOIN jobhunt.sources src ON src.name = 'legacy:' || s.source
    JOIN jobhunt.source_listings sl
      ON sl.source_id = src.id AND sl.external_id = s.new_hash;
    IF n_colision > 0 THEN
        RAISE EXCEPTION
            'ABORTADO: % slots de sombra ya usan el external_id canónico', n_colision;
    END IF;

    SELECT count(*) INTO n_cierre
    FROM g3_losers l
    JOIN jobhunt.sources src ON src.name = 'legacy:' || l.source
    JOIN jobhunt.source_listings sl
      ON sl.source_id = src.id AND sl.external_id = l.loser_hash;

    UPDATE jobhunt.source_listings sl
    SET external_id = s.new_hash
    FROM g3_survivors s
    JOIN jobhunt.sources src ON src.name = 'legacy:' || s.source
    WHERE sl.source_id = src.id
      AND sl.external_id = s.survivor_hash;
    GET DIAGNOSTICS n_remap = ROW_COUNT;

    INSERT INTO g3_shadow_report VALUES
        ('sombra: slots reapuntados al hash canonico', n_remap),
        ('sombra: slots de clones (los cierra el op=D del PASO 6)', n_cierre);
END
$$;

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
SELECT 'irishjobs tras el script', count(*) FROM jobs WHERE source = 'irishjobs'
UNION ALL
SELECT concepto, filas FROM g3_shadow_report;

-- ---------------------------------------------------------------------
-- CAMBIAR A `COMMIT;` PARA APLICARLO DE VERDAD.
-- ---------------------------------------------------------------------
ROLLBACK;
