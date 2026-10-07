import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bson.decimal128 import Decimal128
from pymongo import MongoClient, ReplaceOne

from .generator import (
    COLLECTION,
    GeneratorConfig,
    generate_dataset,
    generate_provider_statements,
)


def _mongo_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return Decimal128(value)
    if isinstance(value, dict):
        return {key: _mongo_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mongo_value(item) for item in value]
    return value


def _write_documents(
    client: MongoClient[Any],
    database: str,
    documents: list[dict[str, Any]],
    batch_id: str,
    *,
    cleanup_stale: bool = True,
) -> None:
    collection = client[database][COLLECTION]
    generated_ids = [document["_id"] for document in documents]
    if cleanup_stale:
        collection.delete_many({
            "testBatchId": {"$exists": True, "$ne": batch_id},
        })
    collection.delete_many(
        {"testBatchId": batch_id, "_id": {"$nin": generated_ids}}
    )
    batch_size = 500
    for offset in range(0, len(documents), batch_size):
        batch = documents[offset : offset + batch_size]
        operations = [
            ReplaceOne({"_id": document["_id"]}, _mongo_value(document), upsert=True)
            for document in batch
        ]
        collection.bulk_write(operations, ordered=False)


def main() -> None:
    config = GeneratorConfig(
        seed=int(os.getenv("GENERATOR_SEED", "20260923")),
        records_per_region=int(os.getenv("RECORDS_PER_REGION", "5000")),
    )
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
    output_path = Path(os.getenv("GROUND_TRUTH_PATH", "/output/ground_truth.json"))
    datasets, ground_truth = generate_dataset(config)
    statements = generate_provider_statements(datasets, config)

    with MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000) as client:
        client.admin.command("ping")
        for database, documents in datasets.items():
            _write_documents(client, database, documents, config.batch_id)
        client["paymenthub_generator"]["generation_runs"].replace_one(
            {"_id": config.batch_id},
            {
                "_id": config.batch_id,
                "seed": config.seed,
                "records_per_region": config.records_per_region,
                "status": "COMPLETED",
                "finished_at": datetime.now(UTC),
                "expected_documents": ground_truth["expected_documents"],
            },
            upsert=True,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    statement_directory = Path(
        os.getenv("PROVIDER_STATEMENTS_DIR", "/output/provider-statements")
    )
    statement_directory.mkdir(parents=True, exist_ok=True)
    statement_rows = {}
    for filename, content in statements.items():
        (statement_directory / filename).write_text(content, encoding="utf-8")
        statement_rows[filename] = max(0, len(content.splitlines()) - 1)
    ground_truth["provider_statements"] = statement_rows
    output_path.write_text(
        json.dumps(ground_truth, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        f"seeded batch={config.batch_id} records_per_region={config.records_per_region}"
    )


if __name__ == "__main__":
    main()
