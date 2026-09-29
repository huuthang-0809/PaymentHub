-- Exact definition from migration 0003.
CREATE INDEX ix_recon_run_scope_date
ON recon_run (business_date, region, tenant_id, provider);