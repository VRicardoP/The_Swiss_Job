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
-- FIXTURE G6 irishjobs (generada) — swissjobhunter_test
BEGIN;
INSERT INTO users (id,email,hashed_password,is_active,plan,gdpr_consent) VALUES ('11111111-1111-1111-1111-111111111111','g6i@example.invalid','x',true,'free',true);

INSERT INTO jobs (hash,source,title,company,url,remote,tags,first_seen_at,last_seen_at,is_active) VALUES
  ('d53d5c54c366fdfbcbf04d8fb41c1cff','irishjobs','Lead MS Fabric Architect','NTT Data','https://www.irishjobs.ie/job/lead-ms-fabric-architect/ntt-data-job900001',true,'[]'::jsonb,now()-interval '30 days',now()-interval '20 days',true),
  ('0bd74c43a087cc7381cfb86cc2199040','irishjobs','Lead MS Fabric Architect','NTT Data','https://www.irishjobs.ie/job/lead-architect/ntt-data-job900001',true,'[]'::jsonb,now()-interval '5 days',now()-interval '2 days',true),
  ('931ab4155f32ceb157b534069bc57454','irishjobs','Lead MS Fabric Architect','NTT Data','https://www.irishjobs.ie/job/lead-fabric/ntt-data-job900001',true,'[]'::jsonb,now()-interval '2 days',now()-interval '0 days',true),
  ('fff857b883198fa27d34035df3b6fcbc','irishjobs','Finance Business Partner','Acme','https://www.irishjobs.ie/job/finance-business-partner/acme-job900002',true,'[]'::jsonb,now()-interval '10 days',now()-interval '3 days',true),
  ('6d9374330433019e64befb1b4d35b252','irishjobs','Finance Business Partner','Acme','https://www.jobs.ie/job/finance-business-partner/acme-job900003',true,'[]'::jsonb,now()-interval '9 days',now()-interval '3 days',true),
  ('74f6648bf42dbd7b9e548581ecf36393','irishjobs','Office Assistant','Delta','https://www.irishjobs.ie/job/office-assistant/delta',true,'[]'::jsonb,now()-interval '12 days',now()-interval '6 days',true);

INSERT INTO match_results (id,user_id,job_hash,score_embedding,score_salary,score_location,score_recency,score_llm,score_final,matching_skills,missing_skills,feedback,application_status) VALUES
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','0bd74c43a087cc7381cfb86cc2199040',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,NULL,'detected'),
  (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','931ab4155f32ceb157b534069bc57454',.5,.5,.5,.5,0,50,'[]'::jsonb,'[]'::jsonb,'thumbs_up','applied');
-- <<< FIXTURE

-- >>> ASERCIONES
-- ASERCIONES G6 irishjobs (generadas)
SELECT '== ASERCIONES ==' AS bloque;
SELECT 'jobs = 4 (A colapsa 3->1, B sigue 2, C intacta)', (SELECT count(*) FROM jobs)=4;
SELECT 'A colapsa al canonico 3c03e086a31d…', EXISTS(SELECT 1 FROM jobs WHERE hash='3c03e086a31d46752d05ca62f1beb620');
SELECT 'B NO se fusiona: las DOS filas cross-host sobreviven', (SELECT count(*) FROM jobs WHERE hash IN ('8d0767c95aae93eab20ad5a9a1d556f4','5c1bb0c803edcd7cc5a5a1f68159fbdb'))=2;
SELECT 'C intacta (sin -job<id>, fuera del mapa)', EXISTS(SELECT 1 FROM jobs WHERE hash='74f6648bf42dbd7b9e548581ecf36393');
SELECT 'sin duplicados (user_id,job_hash)', NOT EXISTS(SELECT 1 FROM match_results GROUP BY user_id,job_hash HAVING count(*)>1);
SELECT 'UNA sola fila de match, la que trae SENAL', (SELECT count(*)=1 AND bool_and(feedback='thumbs_up') FROM match_results WHERE job_hash='3c03e086a31d46752d05ca62f1beb620');
SELECT 'A conserva el first_seen_at mas antiguo', (SELECT first_seen_at < now()-interval '25 days' FROM jobs WHERE hash='3c03e086a31d46752d05ca62f1beb620');
SELECT 'G7/P3-8: ninguna fila queda ACTIVA con duplicate_of puesto', NOT EXISTS(SELECT 1 FROM jobs WHERE is_active AND duplicate_of IS NOT NULL);
ROLLBACK;
