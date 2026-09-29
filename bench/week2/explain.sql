-- psql entry point, isolated migration schema rolled back at end.
\set ON_ERROR_STOP on
\pset pager off
BEGIN;
\ir schema.sql
\ir setup.sql
\ir drop-index-1.sql
\echo QUERY 1 BEFORE INDEX
EXPLAIN (ANALYZE, BUFFERS)
-- Run-list query shape from PostgreReconQuery.runs.
SELECT run_id, job_run_id, business_date, region, tenant_id,
       provider, source_file, file_checksum, business_timezone,
       cutoff_time, status, started_at, finished_at
FROM recon_run
WHERE business_date = DATE '2026-01-23'
  AND region = 'ASIA'
  AND tenant_id = 'bench-tenant-17'
  AND provider = 'ESPAY'
ORDER BY run_id;

\ir index-1.sql
\echo QUERY 1 AFTER INDEX
EXPLAIN (ANALYZE, BUFFERS)
-- Run-list query shape from PostgreReconQuery.runs.
SELECT run_id, job_run_id, business_date, region, tenant_id,
       provider, source_file, file_checksum, business_timezone,
       cutoff_time, status, started_at, finished_at
FROM recon_run
WHERE business_date = DATE '2026-01-23'
  AND region = 'ASIA'
  AND tenant_id = 'bench-tenant-17'
  AND provider = 'ESPAY'
ORDER BY run_id;

\ir drop-index-2.sql
\echo QUERY 2 BEFORE INDEX
EXPLAIN (ANALYZE, BUFFERS)
-- First database page for the filtered /results endpoint.
SELECT result_key, run_id::text AS run_id,
       result_type, region, tenant_id, provider, txn_type,
       system_txn_id, system_provider_txn_id, system_status,
       system_amount_minor, system_currency, refund_of, refunded_amount_minor,
       provider_txn_id, provider_amount_minor, provider_currency,
       delta_amount, source_file, source_row_number, reason
FROM recon_result
WHERE run_id = md5('run-123')::uuid
  AND result_type = 'FUZZY_MATCHED'
ORDER BY result_key
LIMIT 51;
\ir index-2.sql
\echo QUERY 2 AFTER INDEX
EXPLAIN (ANALYZE, BUFFERS)
-- First database page for the filtered /results endpoint.
SELECT result_key, run_id::text AS run_id,
       result_type, region, tenant_id, provider, txn_type,
       system_txn_id, system_provider_txn_id, system_status,
       system_amount_minor, system_currency, refund_of, refunded_amount_minor,
       provider_txn_id, provider_amount_minor, provider_currency,
       delta_amount, source_file, source_row_number, reason
FROM recon_result
WHERE run_id = md5('run-123')::uuid
  AND result_type = 'FUZZY_MATCHED'
ORDER BY result_key
LIMIT 51;
ROLLBACK;
