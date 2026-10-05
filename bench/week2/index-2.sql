-- Exact definition from migration 0004.
CREATE INDEX ix_recon_result_run_type
ON recon_result (run_id, result_type);