# Week 2 synthetic MongoDB data

The generator creates deterministic raw source documents for the regional
adapters. MongoDB stores source-shaped data; it does not store canonical
transactions.

Default output:

| Database | Collection | Documents |
|---|---|---:|
| `paymenthub_vn` | `payment_transactions` | 5,000 |
| `paymenthub_asia_id` | `payment_transactions` | 2,500 |
| `paymenthub_asia_ph` | `payment_transactions` | 2,500 |
| `paymenthub_india` | `payment_transactions` | 5,000 |

Start MongoDB and run the one-shot generator:

```bash
docker compose up -d --build mongodb generator
docker compose logs generator
```

Run the generator again with the same seed:

```bash
docker compose run --rm generator
```

Documents use deterministic `_id` values and are written with bulk upserts, so
rerunning the same seed does not increase collection counts. Change the scale
or seed without editing code:

```bash
GENERATOR_SEED=20260924 RECORDS_PER_REGION=32 \
  docker compose run --rm generator
```

The generated answer is written to `generated/ground_truth.json`. The JSON file
is intentionally ignored by Git because it can be reproduced from the seed.

Verify collection counts:

```bash
docker compose exec mongodb mongosh --quiet --eval '
for (const name of [
  "paymenthub_vn",
  "paymenthub_asia_id",
  "paymenthub_asia_ph",
  "paymenthub_india"
]) {
  const count = db.getSiblingDB(name).payment_transactions.countDocuments({});
  print(name + ": " + count);
}'
```

The dataset includes pending, success, failed, refund, unknown status, invalid
amount, missing currency, non-monetary transactions, and intentional duplicate
provider IDs. Mongo `_id` values remain unique.
