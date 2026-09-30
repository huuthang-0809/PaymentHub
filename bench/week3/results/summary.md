# Week 3 partition benchmark

- PostgreSQL: `PostgreSQL 15.19 (Debian 15.19-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit`
- `work_mem`: `4MB`
- Fixture: 30 daily partitions, 1,200 runs, 1,200,000 rows per detail table per model
- Repeats per phase: 5

| Query | Rows | Before (ms) | After (ms) | Change | After scans | Same result |
|---|---:|---:|---:|---:|---:|---|
| 1 — report page for one run | 51 | 0.121 | 0.113 | -6.6% | 1 | yes |
| 2 — seven-day report | 42 | 168.206 | 83.629 | -50.3% | 7 | yes |

Full plans: `query-<n>-before.txt` and `query-<n>-after.txt`.
Machine-readable evidence: `result-w3.json`.
A negative change means the partitioned model was faster. Tiny one-run queries are dominated by index/cache effects; use the plans and date-range query as the main partition-pruning evidence.
