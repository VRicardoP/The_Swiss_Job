-- =====================================================================
-- G6/P2-3 · ENSAYO de `g6_canonizacion_identidad_irishjobs.sql`
-- =====================================================================
--
-- QUÉ CUBRE
--   GRUPO A  mismo `-job<id>`, TRES slugs distintos (superviviente + 2 clones)
--            y un usuario con match en los DOS clones y ninguno en el
--            superviviente: la colisión clon↔clon de `uq_match_user_job` que
--            abortaba el script hermano (G6/P1-1). La SEÑAL del usuario está
--            en uno de los clones y debe sobrevivir.
--   GRUPO B  el MISMO anuncio en los DOS hosts con ids DISTINTOS. **NO debe
--            fusionarse**: los ids son de verdad distintos y fusionarlos por
--            título+empresa destruiría vacantes reales.
--   GRUPO C  oferta sin `-job<id>` reconocible: fuera del mapa, intacta.
--   GRUPO D  (G8/P2-5) superviviente INACTIVO **con `duplicate_of` puesto** +
--            clon ACTIVO: la única forma que puede refutar el
--            `AND j.duplicate_of IS NULL`. Sin él, el `OR g.any_active`
--            resucita al superviviente. Hasta G8 la fixture no escribía
--            `duplicate_of` en ninguna fila y el `NOT EXISTS` era cierto por
--            vacuidad.
--   SOMBRA   (G8/P2-6) `jobhunt` mínimo (sources + source_listings con la misma
--            unicidad que el real) y un slot por fila: comprueban que el
--            PASO 7c reapunta los slots de los supervivientes al hash canónico.
--
-- USO — nada se escribe: todo termina en ROLLBACK. NUNCA contra
-- `swissjobhunter`; la base de ensayo es `swissjobhunter_test`.
--
--   cd backend/scripts && { \
--     sed -n '/^-- >>> FIXTURE/,/^-- <<< FIXTURE/p' g6_canonizacion_ensayo_irishjobs.sql; \
--     grep -v '^ROLLBACK;$' g6_canonizacion_identidad_irishjobs.sql; \
--     sed -n '/^-- >>> ASERCIONES/,$p'  g6_canonizacion_ensayo_irishjobs.sql; } \
--   | docker compose -f ../../docker-compose.yml exec -T postgres \
--       psql -U swissjob -d swissjobhunter_test -q
-- =====================================================================

-- >>> FIXTURE
-- FIXTURE G6+G8 irishjobs — swissjobhunter_test
BEGIN;
-- Sombra CDC mínima (G8/P2-6): `swissjobhunter_test` no lleva el esquema
-- `jobhunt` del core. `CREATE SCHEMA` es transaccional: el ROLLBACK no deja
-- residuo.
CREATE SCHEMA jobhunt;
CREATE TABLE jobhunt.sources (
    id   uuid PRIMARY KEY,
    name varchar(200) NOT NULL UNIQUE
);
CREATE TABLE jobhunt.source_listings (
    id             uuid PRIMARY KEY,
    source_id      uuid NOT NULL REFERENCES jobhunt.sources(id),
    external_id    varchar(200)  NOT NULL,
    url_normalized varchar(2048) NOT NULL,
    CONSTRAINT uq_listing_source_external UNIQUE (source_id, external_id),
    CONSTRAINT uq_listing_source_url      UNIQUE (source_id, url_normalized)
);
INSERT INTO jobhunt.sources (id,name) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000003','legacy:irishjobs');

INSERT INTO users (id,email,hashed_password,is_active,plan,gdpr_consent) VALUES ('11111111-1111-1111-1111-111111111111','g6i@example.invalid','x',true,'free',true);

INSERT INTO jobs (hash,source,title,company,url,remote,tags,first_seen_at,last_seen_at,is_active) VALUES
  ('d53d5c54c366fdfbcbf04d8fb41c1cff','irishjobs','Lead MS Fabric Architect','NTT Data','https://www.irishjobs.ie/job/lead-ms-fabric-architect/ntt-data-job900001',true,'[]'::jsonb,now()-interval '30 days',now()-interval '20 days',true),
  ('0bd74c43a087cc7381cfb86cc2199040','irishjobs','Lead MS Fabric Architect','NTT Data','https://www.irishjobs.ie/job/lead-architect/ntt-data-job900001',true,'[]'::jsonb,now()-interval '5 days',now()-interval '2 days',true),
  ('931ab4155f32ceb157b534069bc57454','irishjobs','Lead MS Fabric Architect','NTT Data','https://www.irishjobs.ie/job/lead-fabric/ntt-data-job900001',true,'[]'::jsonb,now()-interval '2 days',now()-interval '0 days',true),
  ('fff857b883198fa27d34035df3b6fcbc','irishjobs','Finance Business Partner','Acme','https://www.irishjobs.ie/job/finance-business-partner/acme-job900002',true,'[]'::jsonb,now()-interval '10 days',now()-interval '3 days',true),
  ('6d9374330433019e64befb1b4d35b252','irishjobs','Finance Business Partner','Acme','https://www.jobs.ie/job/finance-business-partner/acme-job900003',true,'[]'::jsonb,now()-interval '9 days',now()-interval '3 days',true),
  ('74f6648bf42dbd7b9e548581ecf36393','irishjobs','Office Assistant','Delta','https://www.irishjobs.ie/job/office-assistant/delta',true,'[]'::jsonb,now()-interval '12 days',now()-interval '6 days',true);

-- GRUPO D (G8/P2-5): superviviente INACTIVO con `duplicate_of` + clon ACTIVO.
INSERT INTO jobs (hash,source,title,company,url,remote,tags,first_seen_at,last_seen_at,is_active,duplicate_of) VALUES
  ('2ff56628f862d7ff36cdf8e7b28ed2de','irishjobs','Head of Compliance','Epsilon Ltd','https://www.irishjobs.ie/job/head-of-compliance/epsilon-job900004',true,'[]'::jsonb,now()-interval '35 days',now()-interval '25 days',false,'deadbeefdeadbeefdeadbeefdeadbeef'),
  ('da9fc4f505ac865614bd8c0aaab98482','irishjobs','Head of Compliance','Epsilon Ltd','https://www.irishjobs.ie/job/head-compliance-dublin/epsilon-job900004',true,'[]'::jsonb,now()-interval '4 days',now()-interval '0 days',true,NULL);

-- Un slot de sombra por fila legacy (external_id = el hash).
INSERT INTO jobhunt.source_listings (id, source_id, external_id, url_normalized)
SELECT gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-000000000003'::uuid, j.hash, j.url FROM jobs j;

INSERT INTO match_results (id,user_id,job_hash,score_embedding,score_salary,score_location,score_recency,score_llm,score_final,matching_skills,missing_skills,feedback,application_status) VALUES
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','0bd74c43a087cc7381cfb86cc2199040',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,NULL,'detected'),
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','931ab4155f32ceb157b534069bc57454',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,'thumbs_up','applied');
-- <<< FIXTURE

-- >>> ASERCIONES
-- ASERCIONES G6 irishjobs (generadas)
-- G8/P3-7f: las aserciones se ACUMULAN en una tabla y un bloque final ABORTA
-- si alguna sale `f` **o NULL**. Antes cada una era un `SELECT` suelto: `psql`
-- terminaba con código 0 imprimiendo `f`, y una subconsulta escalar sobre una
-- fila inexistente salía en blanco (NULL), no `f` — un ensayo en rojo se leía
-- como uno en verde.
CREATE TEMP TABLE g8_asserts (nombre text, ok boolean) ON COMMIT DROP;

SELECT '== ASERCIONES ==' AS bloque;
INSERT INTO g8_asserts VALUES ('jobs = 5 (A colapsa 3->1, B sigue 2, C intacta, D colapsa 2->1)', (SELECT count(*) FROM jobs)=5);
INSERT INTO g8_asserts VALUES ('A colapsa al canonico 3c03e086a31d…', EXISTS(SELECT 1 FROM jobs WHERE hash='3c03e086a31d46752d05ca62f1beb620'));
INSERT INTO g8_asserts VALUES ('B NO se fusiona: las DOS filas cross-host sobreviven', (SELECT count(*) FROM jobs WHERE hash IN ('8d0767c95aae93eab20ad5a9a1d556f4','5c1bb0c803edcd7cc5a5a1f68159fbdb'))=2);
INSERT INTO g8_asserts VALUES ('C intacta (sin -job<id>, fuera del mapa)', EXISTS(SELECT 1 FROM jobs WHERE hash='74f6648bf42dbd7b9e548581ecf36393'));
INSERT INTO g8_asserts VALUES ('sin duplicados (user_id,job_hash)', NOT EXISTS(SELECT 1 FROM match_results GROUP BY user_id,job_hash HAVING count(*)>1));
INSERT INTO g8_asserts VALUES ('UNA sola fila de match, la que trae SENAL', (SELECT count(*)=1 AND bool_and(feedback='thumbs_up') FROM match_results WHERE job_hash='3c03e086a31d46752d05ca62f1beb620'));
INSERT INTO g8_asserts VALUES ('A conserva el first_seen_at mas antiguo', (SELECT first_seen_at < now()-interval '25 days' FROM jobs WHERE hash='3c03e086a31d46752d05ca62f1beb620'));
-- G7/P3-8 + G8/P2-5 + G8/P3-7a: ACOTADA a las filas que el script reescribe
-- (escanear `jobs` entera daba `f` sobre una copia de producción por la fila
-- preexistente `workingnomads 0c089993…`, que este script no toca).
INSERT INTO g8_asserts VALUES ('G7/P3-8: ninguna fila REESCRITA queda ACTIVA con duplicate_of puesto', NOT EXISTS(SELECT 1 FROM jobs WHERE is_active AND duplicate_of IS NOT NULL AND hash IN (SELECT new_hash FROM g3_survivors)));
INSERT INTO g8_asserts VALUES ('grupo D: el superviviente con duplicate_of NO resucita pese al clon activo', (SELECT NOT is_active FROM jobs WHERE hash='04aed658d2dc116c0d996455c556cc35'));
INSERT INTO g8_asserts VALUES ('sombra: los 5 slots vivos resuelven a una fila de jobs', (SELECT count(*) FROM jobhunt.source_listings sl JOIN jobs j ON j.hash=sl.external_id) = 5);
INSERT INTO g8_asserts VALUES ('sombra: ningun slot sigue apuntando al hash PRE-canonizacion', NOT EXISTS(SELECT 1 FROM jobhunt.source_listings WHERE external_id IN (SELECT survivor_hash FROM g3_survivors)));
INSERT INTO g8_asserts VALUES ('sombra: los 3 slots de clones quedan (los cierra el op=D del PASO 6)', (SELECT count(*) FROM jobhunt.source_listings sl WHERE NOT EXISTS(SELECT 1 FROM jobs j WHERE j.hash=sl.external_id)) = 3);
INSERT INTO g8_asserts VALUES ('sombra: el informe declara 4 slots reapuntados', (SELECT filas FROM g3_shadow_report WHERE concepto LIKE 'sombra: slots reapuntados%') = 4);
SELECT '== RESULTADO DEL ENSAYO ==' AS bloque;
SELECT nombre, ok FROM g8_asserts;
DO $g8$
DECLARE
    n_rojo  int;
    n_total int;
BEGIN
    SELECT count(*) FILTER (WHERE ok IS NOT TRUE), count(*)
      INTO n_rojo, n_total FROM g8_asserts;
    IF n_rojo > 0 THEN
        RAISE EXCEPTION 'ENSAYO FALLIDO: % de % aserciones en rojo o NULL', n_rojo, n_total;
    END IF;
    RAISE NOTICE 'ENSAYO OK: %/% aserciones en verde', n_total, n_total;
END
$g8$;
ROLLBACK;
