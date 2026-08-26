-- =====================================================================
-- G3 · Saneo de la familia de corrupción de SALARIOS (lote C)
-- Auditoría: AUDITORIA_GLOBAL_LEGACY_G3_2026-08-26.md · P2-3 / P2-4 / P2-5
-- =====================================================================
--
-- QUÉ REPARA
-- ----------
-- El fix de código (`services/data_normalizer.py`) impide que se sigan
-- escribiendo importes corruptos, pero NO toca las filas ya persistidas.
-- Este script repara esas filas. Son tres daños distintos:
--
--   P2-5 · Divisa desconocida guardada 1:1 como CHF.
--          `CURRENCY_TO_CHF.get(currency, 1.0)` devolvía 1.0 para toda divisa
--          fuera de CHF/EUR/USD/GBP, así que el importe NOMINAL se guardó como
--          si fueran francos (INR ≈ ×106, ZAR ≈ ×21, CAD ≈ ×1.6). Esas ofertas
--          puntúan al MÁXIMO en el factor salario del matching y desplazan a
--          las vacantes suizas reales.
--          Medición 2026-08-26 (producción): CAD 79 · AUD 16 · INR 9 · ZAR 3
--          = 107 filas, todas de `jobgether`.
--
--   P2-4 · Rango con la divisa repetida en el segundo extremo
--          («€90,000 - €95,000 per annum», forma canónica UK/IE): el regex de
--          rango no casaba, el parser caía al camino `single` y persistía
--          min == max, perdiendo la cota alta.
--          Medición 2026-08-26 (producción): 286 filas, casi todas de
--          `irishjobs` (StepStone IE/UK).
--
--   P2-3 · Separador de miles ESPACIO («100 000 CHF» → 100 CHF, error ×1000).
--          Medición 2026-08-26 (producción): 0 filas. El bug era real y con
--          productores vivos (careerjet, jooble, tes, irishjobs, financejobs),
--          pero todavía no había llegado ninguna oferta con ese formato.
--          El recuento se deja igualmente: si al aplicar el script hay filas,
--          se reparan por el mismo camino que P2-4.
--
-- POR QUÉ NO SE EJECUTA AQUÍ
-- --------------------------
-- Es una escritura sobre la base de datos de PRODUCCIÓN (`swissjobhunter`).
-- El corrector de la auditoría tiene prohibido escribir en producción: la
-- decisión de aplicarlo, y cuándo, es del propietario. El script se entrega
-- PREPARADO y NO EJECUTADO, y sólo se ha medido el alcance con SELECT.
--
-- CÓMO APLICARLO
-- --------------
--   1) Copia de seguridad (imprescindible, el saneo no es reversible):
--        docker compose exec -T postgres pg_dump -U swissjob -t jobs \
--          swissjobhunter > /ruta/segura/jobs_pre_g3.sql
--
--   2) Ensayo en seco — cambia el `COMMIT;` final por `ROLLBACK;` y ejecuta:
--        docker compose exec -T postgres psql -U swissjob -d swissjobhunter \
--          -v ON_ERROR_STOP=1 -f - < backend/scripts/g3_saneo_salarios.sql
--      Los recuentos y los `UPDATE n` se imprimen igual, sin escribir nada.
--
--   3) Aplicación real: restablece el `COMMIT;` y repite el comando.
--
--   4) Re-normalización (PASO 3, fuera de SQL — ver el bloque final): repuebla
--      los importes desde `salary_original`, que sigue intacto en la fila.
--
-- IMPORTANTE — la lista de divisas del PASO 1 es la que existía CUANDO se
-- escribieron los datos (CHF/EUR/USD/GBP), NO el mapa ampliado del fix. Las
-- filas dañadas son exactamente las que en su momento cayeron en el `1.0`; si
-- se usara el mapa nuevo (que ya incluye CAD/AUD/INR/ZAR/…) el UPDATE no
-- tocaría ninguna y el daño seguiría en la BD.
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------
-- PASO 0 · Recuento previo — alcance real antes de escribir nada
-- ---------------------------------------------------------------------
\echo '== P2-5: filas con divisa NO convertible cuando se escribieron =='
SELECT salary_currency,
       count(*) AS filas,
       count(*) FILTER (
           WHERE salary_min_chf IS NOT NULL OR salary_max_chf IS NOT NULL
       ) AS con_importe_chf_corrupto
FROM jobs
WHERE salary_currency IS NOT NULL
  AND salary_currency NOT IN ('CHF', 'EUR', 'USD', 'GBP')
GROUP BY 1
ORDER BY 2 DESC;

\echo '== P2-4: rangos con la divisa repetida que se guardaron como min==max =='
SELECT source, count(*) AS filas
FROM jobs
WHERE salary_original ~ '(CHF|EUR|USD|GBP|[€$£])\s*[0-9][0-9.,''’ ]*\s*(-|–|—|to)\s*(CHF|EUR|USD|GBP|[€$£])'
  AND salary_min_chf IS NOT NULL
  AND salary_min_chf = salary_max_chf
GROUP BY 1
ORDER BY 2 DESC;

\echo '== P2-3: salarios con separador de miles ESPACIO (normal o duro) =='
SELECT source, count(*) AS filas
FROM jobs
WHERE salary_original ~ '[0-9][  ][0-9]{3}'
  AND (salary_min_chf IS NOT NULL OR salary_max_chf IS NOT NULL)
GROUP BY 1
ORDER BY 2 DESC;

-- ---------------------------------------------------------------------
-- PASO 1 · P2-5 — vaciar los importes inventados a tasa 1:1
-- `salary_original` y `salary_currency` se CONSERVAN: el dato crudo no se
-- pierde y el PASO 3 puede recalcular el importe con el mapa ampliado.
-- ---------------------------------------------------------------------
UPDATE jobs
SET salary_min_chf = NULL,
    salary_max_chf = NULL
WHERE salary_currency IS NOT NULL
  AND salary_currency NOT IN ('CHF', 'EUR', 'USD', 'GBP')
  AND (salary_min_chf IS NOT NULL OR salary_max_chf IS NOT NULL);

-- ---------------------------------------------------------------------
-- PASO 2 · P2-4 y P2-3 — vaciar los importes mal parseados
-- No se pueden corregir desde las columnas numéricas (la cota alta se perdió
-- y el ×1000 no es distinguible a posteriori), pero SÍ desde `salary_original`,
-- que sigue en la fila. Se vacían aquí para que el PASO 3 los recalcule: sin
-- este vaciado `normalize_salary` hace early-return y no tocaría nada.
-- ---------------------------------------------------------------------
UPDATE jobs
SET salary_min_chf = NULL,
    salary_max_chf = NULL
WHERE salary_original IS NOT NULL
  AND (
        -- P2-4: divisa repetida en el segundo extremo → se persistió min==max
        (   salary_original ~ '(CHF|EUR|USD|GBP|[€$£])\s*[0-9][0-9.,''’ ]*\s*(-|–|—|to)\s*(CHF|EUR|USD|GBP|[€$£])'
        AND salary_min_chf IS NOT NULL
        AND salary_min_chf = salary_max_chf)
        -- P2-3: separador de miles espacio → importe dividido por 1000
        OR salary_original ~ '[0-9][  ][0-9]{3}'
      )
  AND (salary_min_chf IS NOT NULL OR salary_max_chf IS NOT NULL);

-- ---------------------------------------------------------------------
-- PASO 2b · Recuento posterior — debe quedar en 0 en las tres familias
-- ---------------------------------------------------------------------
\echo '== residuo tras el saneo (esperado: 0, 0, 0) =='
SELECT
    count(*) FILTER (
        WHERE salary_currency NOT IN ('CHF', 'EUR', 'USD', 'GBP')
          AND (salary_min_chf IS NOT NULL OR salary_max_chf IS NOT NULL)
    ) AS p2_5_residuo,
    count(*) FILTER (
        WHERE salary_original ~ '(CHF|EUR|USD|GBP|[€$£])\s*[0-9][0-9.,''’ ]*\s*(-|–|—|to)\s*(CHF|EUR|USD|GBP|[€$£])'
          AND salary_min_chf IS NOT NULL
          AND salary_min_chf = salary_max_chf
    ) AS p2_4_residuo,
    count(*) FILTER (
        WHERE salary_original ~ '[0-9][  ][0-9]{3}'
          AND (salary_min_chf IS NOT NULL OR salary_max_chf IS NOT NULL)
    ) AS p2_3_residuo
FROM jobs;

COMMIT;

-- =====================================================================
-- PASO 3 · Re-normalización desde `salary_original` (NO es SQL — NO EJECUTADO)
-- =====================================================================
-- Tras el saneo, las filas afectadas quedan con `salary_*_chf = NULL` y con
-- `salary_original`/`salary_currency` intactos. Este comando las vuelve a
-- pasar por el parser YA CORREGIDO y repuebla los importes. Es idempotente
-- (`normalize_salary` hace early-return sobre las filas que ya tienen importe)
-- y sólo toca filas con `salary_original` no vacío.
--
--   docker compose exec -T backend python - <<'PY'
--   import asyncio
--   from sqlalchemy import select
--   from database import async_session
--   from models.job import Job
--   from services.data_normalizer import DataNormalizer
--
--   async def main() -> None:
--       async with async_session() as db:
--           rows = (await db.execute(
--               select(Job).where(
--                   Job.salary_original.isnot(None),
--                   Job.salary_original != "",
--                   Job.salary_min_chf.is_(None),
--                   Job.salary_max_chf.is_(None),
--               )
--           )).scalars().all()
--           tocadas = 0
--           for job in rows:
--               out = DataNormalizer.normalize_salary({
--                   "salary_original": job.salary_original,
--                   "salary_currency": job.salary_currency,
--                   "salary_period": job.salary_period.value if job.salary_period else None,
--                   "salary_min_chf": None,
--                   "salary_max_chf": None,
--               })
--               if out.get("salary_min_chf") or out.get("salary_max_chf"):
--                   job.salary_min_chf = out.get("salary_min_chf")
--                   job.salary_max_chf = out.get("salary_max_chf")
--                   tocadas += 1
--           await db.commit()
--           print(f"filas repobladas: {tocadas} / {len(rows)}")
--
--   asyncio.run(main())
--   PY
--
-- Después conviene relanzar el matching (`run_all_matches`) para que los
-- scores del factor salario dejen de reflejar los importes corruptos.
-- =====================================================================
