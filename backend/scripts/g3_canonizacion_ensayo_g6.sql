-- =====================================================================
-- G6/P1-1 · ENSAYO del script de canonización — fixture + aserciones
-- =====================================================================
--
-- POR QUÉ EXISTE ESTE FICHERO
-- ---------------------------
-- La validación de G3/G4 usó una fixture de 5 filas con **un clon por
-- superviviente**. Esa forma es ESTRUCTURALMENTE INCAPAZ de alcanzar el fallo
-- que G6 encontró (dos clones del mismo superviviente con match del MISMO
-- usuario y ninguno en el superviviente → `uq_match_user_job`): el test montaba
-- el escenario que esquiva la condición. Esta fixture SÍ la incluye, y con el
-- PASO 5 anterior el ensayo aborta con
-- `duplicate key value violates unique constraint "uq_match_user_job"`.
--
-- QUÉ CUBRE
-- ---------
--   GRUPO A (arbeitnow) superviviente + DOS clones; un usuario con match en los
--           DOS clones y NINGUNO en el superviviente  → la forma que abortaba.
--   GRUPO B (arbeitnow) superviviente + UN clon; el usuario tiene match en los
--           dos y la SEÑAL (feedback + draft_letter + application_status) está
--           en el CLON  → comprueba que se conserva la fila con señal
--           (la COTA que G4 dejó abierta).
--   GRUPO C (jobgether) superviviente + DOS clones; el usuario tiene
--           `job_applications` en los DOS clones (colisión de
--           `uq_application_user_job`) y `generated_documents` en ambos
--           (esa tabla NO tiene clave única: las dos filas deben sobrevivir).
--   GRUPO D  oferta que la canonización no cambia → debe quedar intacta.
--
-- USO — nada se escribe: todo termina en ROLLBACK. NUNCA contra
-- `swissjobhunter`; la base de ensayo es `swissjobhunter_test`.
--
--   cd backend/scripts && { \
--     sed -n '/^-- >>> FIXTURE/,/^-- <<< FIXTURE/p' g3_canonizacion_ensayo_g6.sql; \
--     grep -v '^ROLLBACK;$' g3_canonizacion_identidad_arbeitnow_jobgether.sql; \
--     sed -n '/^-- >>> ASERCIONES/,$p'  g3_canonizacion_ensayo_g6.sql; } \
--   | docker compose -f ../../docker-compose.yml exec -T postgres \
--       psql -U swissjob -d swissjobhunter_test -q
--
-- Los `hash` canónicos que se afirman abajo se verificaron ejecutando contra el
-- código vivo de los providers (`ArbeitnowProvider.compute_hash` +
-- `canonical_identity_url`, `JobgetherProvider.compute_hash` +
-- `canonical_identity_slug`): coinciden carácter a carácter.
-- =====================================================================

-- >>> FIXTURE
BEGIN;
-- FIXTURE G6 (generada por gen_fixture.py) — swissjobhunter_test
INSERT INTO users (id,email,hashed_password,is_active,plan,gdpr_consent) VALUES
  ('11111111-1111-1111-1111-111111111111','g6a@example.invalid','x',true,'free',true),
  ('22222222-2222-2222-2222-222222222222','g6b@example.invalid','x',true,'free',true);

INSERT INTO jobs (hash,source,title,company,url,remote,tags,first_seen_at,last_seen_at,is_active) VALUES
  ('255441d50729d7dba2318c6a2f1feddb','arbeitnow','Social Media Manager','Acme','https://www.arbeitnow.com/jobs/companies/acme/social-media-manager-berlin-100',false,'[]'::jsonb,now()-interval '30 days',now()-interval '20 days',true),
  ('bc8aefb6e8ac3b2a225befd512748df2','arbeitnow','Social Media Manager','Acme','https://www.arbeitnow.com/jobs/companies/acme/social-media-manager-berlin-200',false,'[]'::jsonb,now()-interval '10 days',now()-interval '5 days',false),
  ('a965882d9061c12bcb4af2b40bda7c3c','arbeitnow','Social Media Manager','Acme','https://www.arbeitnow.com/jobs/companies/acme/social-media-manager-berlin-300',false,'[]'::jsonb,now()-interval '2 days',now()-interval '0 days',true),
  ('05a9868f187d3276a1d23c7f9592a9f7','arbeitnow','Backend Engineer','Beta AG','https://www.arbeitnow.com/jobs/companies/beta-ag/backend-engineer-zurich-11',false,'[]'::jsonb,now()-interval '40 days',now()-interval '30 days',true),
  ('9b2e511801ab269e1b7025213a3056a6','arbeitnow','Backend Engineer','Beta AG','https://www.arbeitnow.com/jobs/companies/beta-ag/backend-engineer-zurich-22',false,'[]'::jsonb,now()-interval '5 days',now()-interval '1 days',true),
  ('86c860d1f96c9a63c8e448497d2c3697','jobgether','Data Analyst','Gamma','https://jobgether.com/offer/aaaaaaaaaaaaaaaaaaaaaaaa-data-analyst',false,'[]'::jsonb,now()-interval '20 days',now()-interval '15 days',true),
  ('f37a05df158e5cca315fc9d6af553b09','jobgether','Data Analyst','Gamma','https://jobgether.com/offer/bbbbbbbbbbbbbbbbbbbbbbbb-data-analyst',false,'[]'::jsonb,now()-interval '9 days',now()-interval '4 days',false),
  ('c98f4c9aa3fdcdd8d270a35bb6fedfd2','jobgether','Data Analyst','Gamma','https://jobgether.com/offer/cccccccccccccccccccccccc-data-analyst',false,'[]'::jsonb,now()-interval '3 days',now()-interval '0 days',true),
  ('8c6ecae2c9de23f6ee99b8607222156f','arbeitnow','Office Manager','Delta','https://www.arbeitnow.com/jobs/companies/delta/office-manager',false,'[]'::jsonb,now()-interval '12 days',now()-interval '6 days',true);

INSERT INTO match_results (id,user_id,job_hash,score_embedding,score_salary,score_location,score_recency,score_llm,score_final,matching_skills,missing_skills,feedback,draft_letter,application_status) VALUES
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','bc8aefb6e8ac3b2a225befd512748df2',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,NULL,NULL,'detected'),
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','a965882d9061c12bcb4af2b40bda7c3c',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,NULL,NULL,'detected'),
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','05a9868f187d3276a1d23c7f9592a9f7',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,NULL,NULL,'detected'),
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','9b2e511801ab269e1b7025213a3056a6',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,'thumbs_up','borrador del usuario','applied'),
  (gen_random_uuid(),'22222222-2222-2222-2222-222222222222','8c6ecae2c9de23f6ee99b8607222156f',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,NULL,NULL,'detected');

INSERT INTO job_applications (id,user_id,job_hash,status,applied_at) VALUES
  (gen_random_uuid(),'22222222-2222-2222-2222-222222222222','f37a05df158e5cca315fc9d6af553b09','saved',NULL),
  (gen_random_uuid(),'22222222-2222-2222-2222-222222222222','c98f4c9aa3fdcdd8d270a35bb6fedfd2','applied',now()-interval '1 day');

INSERT INTO generated_documents (id,user_id,job_hash,doc_type,content) VALUES
  (gen_random_uuid(),'22222222-2222-2222-2222-222222222222','f37a05df158e5cca315fc9d6af553b09','cv','doc-clon-1'),
  (gen_random_uuid(),'22222222-2222-2222-2222-222222222222','c98f4c9aa3fdcdd8d270a35bb6fedfd2','letter','doc-clon-2');
-- <<< FIXTURE

-- >>> ASERCIONES
-- ASERCIONES G6 (generadas). Se ejecutan ANTES del ROLLBACK.
SELECT '== jobs tras el script ==' AS bloque;
SELECT hash, source, left(title,28) AS titulo, url, is_active, (first_seen_at < now()-interval '25 days') AS fs_antiguo FROM jobs ORDER BY source, title;
SELECT '== match_results tras el script ==' AS bloque;
SELECT user_id, job_hash, feedback, application_status, draft_letter FROM match_results ORDER BY job_hash;
SELECT '== job_applications / generated_documents ==' AS bloque;
SELECT user_id, job_hash, status FROM job_applications ORDER BY job_hash;
SELECT user_id, job_hash, doc_type, content FROM generated_documents ORDER BY content;
SELECT '== ASERCIONES ==' AS bloque;
SELECT 'jobs = 4 (3 canonizados + 1 intacto)' AS asercion, (SELECT count(*) FROM jobs) = 4 AS ok;
SELECT 'grupo A colapsa a 6cb0bb5e7e470fc24297a6b9e82475b9', EXISTS(SELECT 1 FROM jobs WHERE hash='6cb0bb5e7e470fc24297a6b9e82475b9');
SELECT 'grupo B colapsa a 18b46241e698f694e8c14c475ab5ba8e', EXISTS(SELECT 1 FROM jobs WHERE hash='18b46241e698f694e8c14c475ab5ba8e');
SELECT 'grupo C colapsa a e7825e1f57ccea81d637a6f16cd6f6b1', EXISTS(SELECT 1 FROM jobs WHERE hash='e7825e1f57ccea81d637a6f16cd6f6b1');
SELECT 'sin duplicados (user_id,job_hash) en match_results', NOT EXISTS(SELECT 1 FROM match_results GROUP BY user_id,job_hash HAVING count(*)>1);
SELECT 'sin duplicados (user_id,job_hash) en job_applications', NOT EXISTS(SELECT 1 FROM job_applications GROUP BY user_id,job_hash HAVING count(*)>1);
SELECT 'grupo A: UNA sola fila de match para U1', (SELECT count(*) FROM match_results WHERE user_id='11111111-1111-1111-1111-111111111111' AND job_hash='6cb0bb5e7e470fc24297a6b9e82475b9') = 1;
SELECT 'grupo B: la fila conservada es la que trae SENAL del usuario', (SELECT feedback='thumbs_up' AND draft_letter IS NOT NULL AND application_status='applied' FROM match_results WHERE user_id='11111111-1111-1111-1111-111111111111' AND job_hash='18b46241e698f694e8c14c475ab5ba8e');
SELECT 'grupo C: UNA sola candidatura, la ENVIADA (applied)', (SELECT count(*)=1 AND bool_and(status='applied') FROM job_applications WHERE user_id='22222222-2222-2222-2222-222222222222' AND job_hash='e7825e1f57ccea81d637a6f16cd6f6b1');
SELECT 'grupo C: los DOS documentos generados sobreviven reapuntados', (SELECT count(*) FROM generated_documents WHERE job_hash='e7825e1f57ccea81d637a6f16cd6f6b1') = 2;
SELECT 'grupo D intacto (no entra en el mapa)', EXISTS(SELECT 1 FROM jobs WHERE hash='8c6ecae2c9de23f6ee99b8607222156f');
SELECT 'grupo A hereda is_active y last_seen del clon mas reciente', (SELECT is_active AND last_seen_at > now()-interval '1 hour' FROM jobs WHERE hash='6cb0bb5e7e470fc24297a6b9e82475b9');
SELECT 'las 3 FK a jobs(hash) estan restauradas', (SELECT count(*) FROM pg_constraint WHERE conname IN ('match_results_job_hash_fkey','job_applications_job_hash_fkey','generated_documents_job_hash_fkey')) = 3;
SELECT 'G7/P3-8: ninguna fila queda ACTIVA con duplicate_of puesto', NOT EXISTS(SELECT 1 FROM jobs WHERE is_active AND duplicate_of IS NOT NULL);
ROLLBACK;
