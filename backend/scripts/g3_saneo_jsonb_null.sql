-- =====================================================================
-- G3/P2-7 — Saneo de columnas JSONB envenenadas con el valor JSON `null`
-- =====================================================================
--
-- QUÉ REPARA
--   Un `PUT /api/v1/profile` o `PUT /api/v1/searches/{id}` con un `null`
--   explícito (p.ej. `{"skills": null}`) escribía el valor JSON `null` en una
--   columna JSONB declarada NOT NULL. Postgres lo acepta: `'null'::jsonb` NO
--   es SQL NULL, así que la restricción NOT NULL no salta. SQLAlchemy lo
--   devuelve luego como None y la validación Pydantic de la respuesta falla,
--   de modo que la fila queda ILEGIBLE:
--     - user_profiles.skills/languages/locations -> GET /api/v1/profile y
--       GET /api/v1/profile/export (portabilidad GDPR) responden 500 para
--       siempre.
--     - saved_searches.filters -> UNA fila envenenada tumba el listado
--       completo `GET /api/v1/searches` del usuario, filas sanas incluidas.
--
--   El código ya no puede crear filas nuevas así (routers/profile.py y
--   routers/saved_searches.py descartan los None que no son anulables en BD,
--   y los schemas de respuesta leen un None como valor vacío). Este script
--   repara los datos HISTÓRICOS: devuelve a cada columna su valor vacío
--   correcto (`[]` para las listas, `{}` para los filtros).
--
-- POR QUÉ NO SE EJECUTA AQUÍ
--   Es una ESCRITURA sobre la base de datos de producción. El ciclo G3 solo
--   ejecutó el recuento de SOLO LECTURA del bloque 1. En la medición del
--   2026-08-26 sobre `swissjobhunter` el resultado fue 0 filas afectadas en
--   las cuatro columnas (3 perfiles, 0 búsquedas guardadas), así que no hubo
--   nada que reparar y el script queda preparado por si aparece una fila
--   corrupta en otro despliegue (p.ej. el NAS).
--
-- CÓMO APLICARLO
--   1) Recuento previo (solo lectura), para decidir si hace falta:
--        docker compose exec -T postgres psql -U swissjob -d swissjobhunter \
--          -v ON_ERROR_STOP=1 -f - < backend/scripts/g3_saneo_jsonb_null.sql
--      (tal cual, el fichero SOLO cuenta: los UPDATE están comentados).
--   2) Si el recuento es > 0: haz copia de seguridad
--        docker compose exec -T postgres pg_dump -U swissjob swissjobhunter \
--          > /ruta/segura/swissjobhunter_pre_g3.sql
--      descomenta el BLOQUE 2 y vuelve a ejecutar el fichero. El bloque va en
--      una transacción explícita: revisa el recuento de filas de cada UPDATE
--      antes del COMMIT.
--   3) Vuelve a ejecutar el BLOQUE 1: debe dar 0 en todas las columnas.
-- =====================================================================


-- ---------------------------------------------------------------------
-- BLOQUE 1 — RECUENTO PREVIO (solo lectura, seguro en producción)
-- ---------------------------------------------------------------------
SELECT
    count(*) FILTER (WHERE jsonb_typeof(skills) = 'null')    AS skills_null,
    count(*) FILTER (WHERE jsonb_typeof(languages) = 'null') AS languages_null,
    count(*) FILTER (WHERE jsonb_typeof(locations) = 'null') AS locations_null,
    count(*)                                                 AS total_profiles
FROM user_profiles;

SELECT
    count(*) FILTER (WHERE jsonb_typeof(filters) = 'null') AS filters_null,
    count(*)                                               AS total_searches
FROM saved_searches;

-- Filas concretas afectadas (para el registro del saneo).
SELECT id, user_id,
       jsonb_typeof(skills)    AS skills,
       jsonb_typeof(languages) AS languages,
       jsonb_typeof(locations) AS locations
FROM user_profiles
WHERE jsonb_typeof(skills) = 'null'
   OR jsonb_typeof(languages) = 'null'
   OR jsonb_typeof(locations) = 'null';

SELECT id, user_id, name
FROM saved_searches
WHERE jsonb_typeof(filters) = 'null';


-- ---------------------------------------------------------------------
-- BLOQUE 2 — REPARACIÓN (DESCOMENTAR SOLO TRAS COPIA DE SEGURIDAD)
-- ---------------------------------------------------------------------
-- BEGIN;
--
-- -- Listas JSONB NOT NULL: el valor vacío correcto es `[]` (default=list).
-- UPDATE user_profiles
--    SET skills = '[]'::jsonb
--  WHERE jsonb_typeof(skills) = 'null';
--
-- UPDATE user_profiles
--    SET languages = '[]'::jsonb
--  WHERE jsonb_typeof(languages) = 'null';
--
-- UPDATE user_profiles
--    SET locations = '[]'::jsonb
--  WHERE jsonb_typeof(locations) = 'null';
--
-- -- Filtros JSONB NOT NULL: el valor vacío correcto es `{}` (default=dict),
-- -- que la tarea de búsquedas guardadas interpreta como «sin filtros».
-- UPDATE saved_searches
--    SET filters = '{}'::jsonb
--  WHERE jsonb_typeof(filters) = 'null';
--
-- -- Revisa los recuentos de filas de arriba antes de confirmar.
-- COMMIT;
