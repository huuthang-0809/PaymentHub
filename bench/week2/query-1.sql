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