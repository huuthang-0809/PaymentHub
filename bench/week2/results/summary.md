# Kết quả EXPLAIN trước/sau index

- PostgreSQL: `PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, 64-bit`
- `work_mem`: `4MB`
- Fixture: 1,000,000 runs, 3,000,000 results
- Số lần đo mỗi phase: 5

| Query | Dòng | Trước index (ms) | Sau index (ms) | Cùng kết quả |
|---|---:|---:|---:|---|
| 1 | 167 | 14.029 | 0.137 | Có |
| 2 | 51 | 0.360 | 0.148 | Có |

Execution plan đầy đủ:

- `query-1-before.txt` / `query-1-after.txt`
- `query-2-before.txt` / `query-2-after.txt`