# Kết quả EXPLAIN trước/sau index

- PostgreSQL: `16.15`
- `work_mem`: `4MB`
- Fixture: 1.000.000 runs, 3.000.000 results
- Mỗi phase đo 5 lần; bảng dùng median `Execution Time`.
- Hash và số dòng kết quả trước/sau giống nhau.

| Query | Dòng | Trước index (ms) | Sau index (ms) | Cải thiện |
|---|---:|---:|---:|---:|
| 1 — danh sách run theo scope | 167 | 8,355 | 0,225 | ~37 lần |
| 2 — trang `FUZZY_MATCHED` | 51 | 1,584 | 0,195 | ~8 lần |

Query 1 giảm candidate từ 16.700 xuống 167 và shared buffer hit từ
12.712 xuống 170. Query 2 giảm candidate từ 10.000 xuống 100 và shared buffer
hit từ 344 xuống 103.

Execution plan đầy đủ:

- `query-1-before.txt` / `query-1-after.txt`
- `query-2-before.txt` / `query-2-after.txt`