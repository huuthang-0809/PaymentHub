import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bson.decimal128 import Decimal128
from pymongo import MongoClient, ReplaceOne

from .generator import COLLECTION, GeneratorConfig, generate_dataset


def _mongo_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return Decimal128(value)
    if isinstance(value, dict):
        return {key: _mongo_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mongo_value(item) for item in value]
    return value


def _write_documents(
    client: MongoClient[Any], database: str, documents: list[dict[str, Any]]
) -> None:
    collection = client[database][COLLECTION]
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

    with MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000) as client:
        client.admin.command("ping")
        for database, documents in datasets.items():
            _write_documents(client, database, documents)
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
    output_path.write_text(
        json.dumps(ground_truth, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        f"seeded batch={config.batch_id} records_per_region={config.records_per_region}"
    )


if __name__ == "__main__":
    main()
