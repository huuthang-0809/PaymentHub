-- Current result-page shape: run_id is known, business_date is not supplied.
SELECT result_key, result_type, system_txn_id, provider_txn_id,
       system_amount_minor, provider_amount_minor, delta_amount,
       source_file, source_row_number, reason
FROM recon_result_before
WHERE run_id = md5('w3-run-900')::uuid
  AND result_type = 'AMOUNT_MISMATCH'
ORDER BY result_key
LIMIT 51;

