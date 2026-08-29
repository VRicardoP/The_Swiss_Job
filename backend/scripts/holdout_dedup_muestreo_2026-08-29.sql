\pset pager off
SET search_path = jobhunt, public;
CREATE TEMP TABLE marco AS
SELECT v.id, orv.text_hash, sl.source_id,
       orv.content->>'title'    AS title,
       lower(coalesce(orv.content->>'company',''))  AS comp,
       coalesce(orv.content->>'location','')        AS loc
FROM jobhunt.vacancies v
JOIN jobhunt.offer_revisions orv ON orv.id = v.current_offer_revision_id
JOIN jobhunt.source_listing_incarnations pi ON pi.id = v.primary_incarnation_id
JOIN jobhunt.source_listings sl ON sl.id = pi.source_listing_id
WHERE v.archived_at IS NULL AND v.merged_into IS NULL;
SELECT setseed(0.230823);
CREATE TEMP TABLE bloqueo AS
SELECT a.id AS va, b.id AS vb, (a.source_id <> b.source_id) AS cross_src
FROM marco a JOIN marco b
  ON a.comp = b.comp AND a.comp <> '' AND a.id < b.id
  AND similarity(a.title, b.title) >= 0.55
  AND a.text_hash <> b.text_hash;
CREATE TEMP TABLE muestra AS
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

-- ENMIENDA DE INDEPENDENCIA (protocolo, 2026-08-23): fuera todo par que YA
-- exista en labeled_dedup_pairs, en cualquier sentido. De esa tabla se leen
-- SOLO los refs; ningún veredicto entra aquí. La regla multi-ciudad se ratificó
-- con esos pares, así que medirían memoria y no generalización.
CREATE TEMP TABLE ref_de_vacante AS
SELECT DISTINCT ON (l.external_id) l.external_id AS job_ref, i.vacancy_id
FROM jobhunt.source_listings l
JOIN jobhunt.sources src ON src.id = l.source_id
JOIN jobhunt.source_listing_incarnations i ON i.source_listing_id = l.id
WHERE src.name LIKE 'legacy:%'
ORDER BY l.external_id, i.seq DESC, i.first_seen_at DESC, i.id;

CREATE TEMP TABLE ya_etiquetados AS
SELECT DISTINCT least(ra.vacancy_id, rb.vacancy_id) AS v1,
                greatest(ra.vacancy_id, rb.vacancy_id) AS v2
FROM jobhunt.labeled_dedup_pairs p
JOIN ref_de_vacante ra ON ra.job_ref = p.job_ref_a
JOIN ref_de_vacante rb ON rb.job_ref = p.job_ref_b;

DELETE FROM muestra m
WHERE EXISTS (
  SELECT 1 FROM ya_etiquetados y
  WHERE y.v1 = least(m.va, m.vb) AND y.v2 = greatest(m.va, m.vb)
);
SELECT 'excluidos_por_independencia', 60 - count(*) FROM muestra;
SELECT estrato, count(*) FROM muestra GROUP BY 1 ORDER BY 1;
SELECT 'muestra_final', count(*) FROM muestra;

-- La hoja: barajada y SIN estrato. El orden lo fija un hash estable del par,
-- no el estrato ni el azar de la sesión. Se emite con \o (no \copy: COPY
-- reescapa el texto y rompe el JSON).
\pset format unaligned
\pset tuples_only on
\o /tmp/hoja_ciega.json
SELECT json_agg(x ORDER BY x->>'p') FROM (
  SELECT json_build_object(
    'p', 'P' || lpad((row_number() OVER (ORDER BY md5(m.va::text || m.vb::text)))::text, 2, '0'),
    'a', json_build_object('titulo', ra.content->>'title', 'empresa', ra.content->>'company',
         'lugar', ra.content->>'location', 'remoto', ra.content->>'remote',
         'salario', ra.content->>'salary', 'fuente', sa.name,
         'desc', left(coalesce(ra.content->>'description',''), 600)),
    'b', json_build_object('titulo', rb.content->>'title', 'empresa', rb.content->>'company',
         'lugar', rb.content->>'location', 'remoto', rb.content->>'remote',
         'salario', rb.content->>'salary', 'fuente', sb.name,
         'desc', left(coalesce(rb.content->>'description',''), 600))) AS x
  FROM muestra m
  JOIN jobhunt.vacancies va ON va.id=m.va
  JOIN jobhunt.offer_revisions ra ON ra.id=va.current_offer_revision_id
  JOIN jobhunt.source_listing_incarnations pa ON pa.id=va.primary_incarnation_id
  JOIN jobhunt.source_listings sla ON sla.id=pa.source_listing_id
  JOIN jobhunt.sources sa ON sa.id=sla.source_id
  JOIN jobhunt.vacancies vb ON vb.id=m.vb
  JOIN jobhunt.offer_revisions rb ON rb.id=vb.current_offer_revision_id
  JOIN jobhunt.source_listing_incarnations pb ON pb.id=vb.primary_incarnation_id
  JOIN jobhunt.source_listings slb ON slb.id=pb.source_listing_id
  JOIN jobhunt.sources sb ON sb.id=slb.source_id
) s;
\o
-- La CLAVE, aparte: estrato y vacantes. NO se abre hasta después de etiquetar.
\o /tmp/clave_estratos.txt
SELECT 'P' || lpad((row_number() OVER (ORDER BY md5(m.va::text || m.vb::text)))::text, 2, '0')
       || '|' || m.estrato || '|' || m.va || '|' || m.vb FROM muestra m;
\o
