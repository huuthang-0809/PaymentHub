-- Run schema.sql first in the same transaction. All FK and CHECK constraints remain enabled.
INSERT INTO job_run_log (job_run_id, job_name, business_date, region, tenant_id, provider, status)
SELECT md5('job-' || n)::uuid, 'reconciliation',
       DATE '2026-01-01' + ((n - 1) % 30)::integer,
       'ASIA', 'bench-tenant-' || (((n - 1) / 30) % 100),
       (ARRAY['ESPAY','2C2P'])[1 + ((n - 1) / 3000) % 2], 'COMPLETED'
FROM generate_series(1, 1000000) AS g(n);

INSERT INTO recon_run
    (run_id, job_run_id, business_date, region, tenant_id, provider,
     source_file, file_checksum, business_timezone, cutoff_time, status,
     started_at, finished_at)
SELECT md5('run-' || n)::uuid, md5('job-' || n)::uuid,
       DATE '2026-01-01' + ((n - 1) % 30)::integer,
       'ASIA', 'bench-tenant-' || (((n - 1) / 30) % 100),
       (ARRAY['ESPAY','2C2P'])[1 + ((n - 1) / 3000) % 2],
       'fixture-' || n || '.csv', md5('file-' || n),
       'Asia/Jakarta', TIME '00:00', 'COMPLETED',
       TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:01:00+00'
FROM generate_series(1, 1000000) AS g(n);

INSERT INTO recon_result
    (run_id, job_run_id, result_key, result_type, region, tenant_id, provider,
     txn_type, system_txn_id, system_amount_minor, system_currency,
     provider_txn_id, provider_amount_minor, provider_currency,
     delta_amount, source_file, source_row_number)
SELECT md5('run-' || (1 + (n - 1) / 10000))::uuid,
       md5('job-' || (1 + (n - 1) / 10000))::uuid,
       (1 + (n - 1) % 10000)::text,
       CASE
           WHEN n % 100 = 1 THEN 'FUZZY_MATCHED'
           WHEN n % 10 = 0 THEN 'AMOUNT_MISMATCH'
           ELSE 'MATCHED'
       END,
       r.region, r.tenant_id, r.provider, 'PAYMENT', 'txn-' || n, 100000, 'IDR',
       'provider-' || n, CASE WHEN n % 10 = 0 THEN 99000 ELSE 100000 END,
       'IDR', CASE WHEN n % 10 = 0 THEN 1000 ELSE 0 END,
       'fixture.csv', 1 + (n - 1) % 10000 + 1
FROM generate_series(1, 3000000) AS g(n)
JOIN recon_run r ON r.run_id = md5('run-' || (1 + (n - 1) / 10000))::uuid;

ANALYZE job_run_log;
ANALYZE recon_run;
ANALYZE recon_result;