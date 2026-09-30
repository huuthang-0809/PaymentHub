-- 30 days, 40 runs/day, 1,000 provider/results rows per run.
INSERT INTO recon_run (
    run_id, job_run_id, business_date, region, tenant_id, provider,
    source_file, file_checksum, status, started_at, finished_at
)
SELECT md5('w3-run-' || n)::uuid,
       md5('w3-job-' || n)::uuid,
       DATE '2026-01-01' + ((n - 1) / 40)::integer,
       'ASIA',
       'tenant-' || ((n - 1) % 10),
       (ARRAY['ESPAY', '2C2P'])[1 + ((n - 1) % 2)],
       'statement-' || n || '.csv',
       md5('w3-file-' || n) || md5('w3-file-' || n),
       'COMPLETED',
       TIMESTAMPTZ '2026-01-01 00:00:00+00' + ((n - 1) * interval '10 minutes'),
       TIMESTAMPTZ '2026-01-01 00:01:00+00' + ((n - 1) * interval '10 minutes')
FROM generate_series(1, 1200) AS g(n);

INSERT INTO provider_txn_before (
    business_date, run_id, job_run_id, region, tenant_id, provider, txn_type,
    provider_txn_id, provider_ref_id, merchant_ref, merchant_id,
    amount_minor, currency, status, transaction_time,
    source_file, source_row_number, raw, ingested_at
)
SELECT r.business_date, r.run_id, r.job_run_id, r.region, r.tenant_id,
       r.provider, 'PAYMENT', 'provider-' || n, NULL, 'merchant-ref-' || n,
       'merchant-' || (n % 100), 100000 + (n % 1000), 'IDR', 'SUCCESS',
       r.business_date::timestamptz + ((n - 1) % 1000) * interval '1 second',
       r.source_file, 1 + ((n - 1) % 1000),
       jsonb_build_object('provider_txn_id', 'provider-' || n),
       r.finished_at
FROM generate_series(1, 1200000) AS g(n)
JOIN recon_run AS r
  ON r.run_id = md5('w3-run-' || (1 + (n - 1) / 1000))::uuid;

INSERT INTO recon_result_before (
    business_date, run_id, job_run_id, result_key, result_type,
    region, tenant_id, provider, txn_type, system_txn_id,
    system_amount_minor, system_currency, provider_txn_id,
    provider_amount_minor, provider_currency, delta_amount,
    source_file, source_row_number, reason, created_at
)
SELECT r.business_date, r.run_id, r.job_run_id, 1 + ((n - 1) % 1000),
       CASE
           WHEN n % 100 = 1 THEN 'FUZZY_MATCHED'
           WHEN n % 10 = 0 THEN 'AMOUNT_MISMATCH'
           ELSE 'MATCHED'
       END,
       r.region, r.tenant_id, r.provider, 'PAYMENT', 'system-' || n,
       100000 + (n % 1000), 'IDR', 'provider-' || n,
       100000 + (n % 1000) - CASE WHEN n % 10 = 0 THEN 1000 ELSE 0 END,
       'IDR', CASE WHEN n % 10 = 0 THEN 1000 ELSE 0 END,
       r.source_file, 1 + ((n - 1) % 1000),
       CASE WHEN n % 10 = 0 THEN 'amount differs' END,
       r.finished_at
FROM generate_series(1, 1200000) AS g(n)
JOIN recon_run AS r
  ON r.run_id = md5('w3-run-' || (1 + (n - 1) / 1000))::uuid;

INSERT INTO provider_txn_after (
    business_date, run_id, job_run_id, region, tenant_id, provider, txn_type,
    provider_txn_id, provider_ref_id, merchant_ref, merchant_id,
    amount_minor, currency, status, transaction_time,
    source_file, source_row_number, raw, ingested_at
)
SELECT business_date, run_id, job_run_id, region, tenant_id, provider,
       txn_type, provider_txn_id, provider_ref_id, merchant_ref, merchant_id,
       amount_minor, currency, status, transaction_time,
       source_file, source_row_number, raw, ingested_at
FROM provider_txn_before;

INSERT INTO recon_result_after (
    business_date, run_id, job_run_id, result_key, result_type,
    region, tenant_id, provider, txn_type, system_txn_id,
    system_amount_minor, system_currency, provider_txn_id,
    provider_amount_minor, provider_currency, delta_amount,
    source_file, source_row_number, reason, created_at
)
SELECT business_date, run_id, job_run_id, result_key, result_type,
       region, tenant_id, provider, txn_type, system_txn_id,
       system_amount_minor, system_currency, provider_txn_id,
       provider_amount_minor, provider_currency, delta_amount,
       source_file, source_row_number, reason, created_at
FROM recon_result_before;

ANALYZE recon_run;
ANALYZE recon_result_before;
ANALYZE provider_txn_before;
ANALYZE recon_result_after;
ANALYZE provider_txn_after;

