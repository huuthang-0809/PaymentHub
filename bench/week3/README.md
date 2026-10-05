# Week 3 — benchmark partition cho reconciliation audit data

Benchmark này so sánh hai mô hình trên cùng một fixture:

- `*_before`: bảng thường, mô phỏng schema hiện tại.
- `*_after`: `recon_result` và `provider_txn` partition theo ngày
  `business_date`, có index bắt đầu bằng `run_id` trong từng partition.
- `recon_run` luôn là bảng thường.

`business_date` được snapshot từ `recon_run` vào hai bảng detail. Đây là ngày
nghiệp vụ đã áp dụng timezone/cutoff, không phải ngày insert hoặc phần date của
`transaction_time`.

## Các query được đo

| Query | Luồng ứng dụng | Điều cần quan sát |
|---|---|---|
| 1 | Trang chi tiết report của một run | Partition pruning + index `(run_id, result_type, result_key)` |
| 2 | Báo cáo tổng hợp theo khoảng 7 ngày | Chỉ đọc 7 partition trong khoảng ngày |

Query 1 phản ánh `PostgreReconQuery.results_page` hiện tại. Trong thiết kế
partition, repository đọc `business_date` từ `recon_run` trước rồi thêm nó vào
điều kiện truy vấn detail. Query 2 là query báo cáo theo kỳ dự kiến triển khai.

## Chạy benchmark

Khởi động PostgreSQL và migration service nếu chưa chạy:

```bash
docker compose up -d postgres migrate
```

Chạy bằng Python:

```bash
uv run python bench/week3/run.py --repeats 5
```

Hoặc chạy toàn bộ EXPLAIN bằng `psql`:

```bash
psql "$DATABASE_URL" -f bench/week3/explain.sql
```

Python runner ghi execution plan và bảng tổng hợp vào `bench/week3/results/`.
Toàn bộ benchmark chạy trong một transaction và rollback khi kết thúc, nên
không thay đổi schema ứng dụng.

Fixture mặc định gồm 30 ngày, 40 run/ngày và 1.000 provider/results mỗi run:
1,2 triệu dòng trong mỗi bảng detail của mỗi mô hình. Số liệu nhỏ chỉ kiểm tra
được tính đúng và kế hoạch thực thi; kết luận hiệu năng cần dùng dữ liệu gần với
production và cùng cấu hình PostgreSQL.

## Điều kiện để áp dụng vào production

Migration thật cần thêm `business_date NOT NULL` vào cả hai bảng detail, copy
giá trị từ `recon_run`, và đưa partition key vào mọi `PRIMARY KEY`/`UNIQUE`
constraint. Foreign key detail nên gồm `(run_id, job_run_id, business_date)` để
DB ngăn một row bị gắn sai ngày của run.

Ứng dụng phải luôn truyền cả `business_date` và `run_id` khi đọc detail. Nếu chỉ
lọc `run_id`, PostgreSQL không thể loại partition theo ngày ở planning time.
