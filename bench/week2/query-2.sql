-- Pre-partition database page shape measured by the week 2 index benchmark.
-- Concrete values keep this file directly runnable by EXPLAIN and psql.
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
