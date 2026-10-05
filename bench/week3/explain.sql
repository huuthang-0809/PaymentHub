\set ON_ERROR_STOP on
\pset pager off
BEGIN;
\ir schema.sql
\ir setup.sql

\echo QUERY 1 BEFORE: report page
EXPLAIN (ANALYZE, BUFFERS)
SELECT result_key, result_type, system_txn_id, provider_txn_id,
       system_amount_minor, provider_amount_minor, delta_amount,
       source_file, source_row_number, reason
FROM recon_result_before
WHERE run_id = md5('w3-run-900')::uuid
  AND result_type = 'AMOUNT_MISMATCH'
ORDER BY result_key
LIMIT 51;
\echo QUERY 1 AFTER: report page
EXPLAIN (ANALYZE, BUFFERS)
SELECT result_key, result_type, system_txn_id, provider_txn_id,
       system_amount_minor, provider_amount_minor, delta_amount,
       source_file, source_row_number, reason
FROM recon_result_after
WHERE business_date = DATE '2026-01-23'
  AND run_id = md5('w3-run-900')::uuid
  AND result_type = 'AMOUNT_MISMATCH'
ORDER BY result_key
LIMIT 51;

\echo QUERY 2 BEFORE: seven-day report
EXPLAIN (ANALYZE, BUFFERS)
SELECT business_date, provider, result_type,
       count(*) AS result_count,
       sum(system_amount_minor) AS system_amount_minor,
       sum(provider_amount_minor) AS provider_amount_minor
FROM recon_result_before
WHERE business_date >= DATE '2026-01-20'
  AND business_date < DATE '2026-01-27'
GROUP BY business_date, provider, result_type
ORDER BY business_date, provider, result_type;
\echo QUERY 2 AFTER: seven-day report
EXPLAIN (ANALYZE, BUFFERS)
SELECT business_date, provider, result_type,
       count(*) AS result_count,
       sum(system_amount_minor) AS system_amount_minor,
       sum(provider_amount_minor) AS provider_amount_minor
FROM recon_result_after
WHERE business_date >= DATE '2026-01-20'
  AND business_date < DATE '2026-01-27'
GROUP BY business_date, provider, result_type
ORDER BY business_date, provider, result_type;

ROLLBACK;
