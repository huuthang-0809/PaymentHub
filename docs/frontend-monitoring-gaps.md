# Frontend monitoring coverage and backend gaps

This document records which of the five operational questions can be answered
from the current API and which backend contracts are still required.

## Current FE coverage

The frontend consumes `GET /metrics/windows` and now exposes:

- event and monetary-event change between the latest two 1/5/15-minute windows;
- volume by currency without combining currencies;
- success/failure/pending/unknown breakdown by provider, region, tenant,
  transaction type, and raw channel;
- non-terminal count, four pending-age buckets, oldest pending age, late-event
  count, source-event count, freshness lag, and data-quality signals;
- filters for region, provider, tenant, transaction type, channel, currency,
  and time range.

The current volume change indicator is only a comparison with the immediately
preceding window. A change of at least 50% is highlighted, but it is explicitly
not a statistical anomaly classification.

## Gap 1: volume anomaly baseline

To classify volume as anomalous rather than merely changed, add a reporting or
anomaly endpoint. Suggested response fields:

```json
{
  "scope": {
    "region": "VN",
    "tenant_id": null,
    "provider": "ONEPAY",
    "txn_type": "PAYMENT",
    "channel": "QR"
  },
  "window_start": "2026-09-30T10:00:00Z",
  "window_size_minutes": 5,
  "actual_count": 1200,
  "expected_count": 780,
  "lower_bound": 600,
  "upper_bound": 960,
  "deviation_percent": 53.85,
  "status": "SPIKE",
  "sample_size": 288,
  "data_as_of": "2026-09-30T10:06:00Z"
}
```

The backend must own baseline selection, minimum sample size, seasonality,
thresholds, and insufficient-data behavior. These rules must not be hard-coded
in the browser.

## Gap 2: callback and polling aggregates

`CanonicalTransaction` contains `callback_attempts` and
`provider_poll_count`, and source adapters map some of these values. The
monitoring query and window aggregates do not currently expose enough data to
detect abnormal behavior.

Add per-window, per-scope metrics such as:

- `callback_attempt_count`, `callback_success_count`,
  `callback_failure_count`, and callback latency percentiles;
- `callback_retry_count` and `callback_exhausted_count`;
- `poll_attempt_count`, `poll_timeout_count`, `poll_failure_count`, and oldest
  actively polled transaction age;
- affected transaction count and masked sample references for drill-down.

Do not infer successful callbacks only from a non-zero attempt count. Persist
an explicit callback outcome and timestamps.

## Gap 3: retry, queue, and DLQ processing

General retry and DLQ behavior is designed but not implemented. Add metrics
from the actual worker/broker rather than reconstructing them from canonical
transactions:

- queue depth, enqueue/dequeue rate, oldest-message age, and consumer lag;
- processing success/failure/timeout counts and duration percentiles;
- retry scheduled/executed/exhausted counts grouped by stable error code;
- DLQ size, oldest DLQ item age, and reprocessing results;
- worker heartbeat, checkpoint age, and last successful batch.

A suggested endpoint is `GET /operations/integration-health`, filterable by
region, tenant, provider, component, and time range.

## Gap 4: provider health and alerts

The target endpoints in the technical design are not implemented:

- `GET /health/providers`
- `GET /alerts`
- `GET /_meta/freshness`

Provider health evaluation must run in the backend and should return:

- `HEALTHY`, `DEGRADED`, `UNAVAILABLE`, or `NO_DATA`;
- stable reason/rule codes and human-readable descriptions;
- scope, sample size, evaluated windows, threshold and observed value;
- `started_at`, `last_checked_at`, `last_healthy_at`, and `data_as_of`;
- consecutive breach/recovery counters and cooldown state;
- related open alert IDs.

Alerts require severity, lifecycle status, occurrence count, acknowledgement,
resolution timestamps, and cursor pagination. The frontend must render this
authoritative state rather than deriving availability from success rate alone.

## API completeness notes

- `GET /metrics/windows` is capped at 500 rows. Global KPI totals require a
  server-side summary endpoint or cursor pagination.
- Late-event aggregates do not expose transaction identities, so the frontend
  cannot drill down to affected transactions.
- Parse-error counts do not include masked sample references; the existing FE
  drill-down to invalid-amount transactions is only an approximation.
