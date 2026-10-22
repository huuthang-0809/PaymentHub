-- Run after migrations and after loading representative canonical_event rows.
-- Query 1: status monitoring by region and time.
EXPLAIN (ANALYZE, BUFFERS)
SELECT status, count(*)
FROM canonical_event
WHERE region = 'VN'
  AND event_date BETWEEN DATE '2026-09-01' AND DATE '2026-09-30'
  AND is_deleted = false
  AND is_test_data = false
GROUP BY status;

-- Query 2: provider transaction lookup.
EXPLAIN (ANALYZE, BUFFERS)
SELECT event_date, region, txn_id, status, updated_at
FROM canonical_event
WHERE provider = 'ONEPAY'
  AND updated_at >= TIMESTAMPTZ '2026-09-01 00:00:00+00'
ORDER BY updated_at DESC
LIMIT 100;

-- Create candidate indexes, then run both EXPLAIN statements again.
CREATE INDEX IF NOT EXISTS ix_canonical_event_monitoring
    ON canonical_event (region, event_date, status)
    WHERE is_deleted = false AND is_test_data = false;

CREATE INDEX IF NOT EXISTS ix_canonical_event_provider_updated
    ON canonical_event (provider, updated_at DESC);
