import os
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bson.decimal128 import Decimal128
from pymongo import MongoClient

from .config import scope_specs
from .generator import (
    COLLECTION,
    GeneratorConfig,
    iter_scope_documents,
    scope_document_count,
    write_streaming_ground_truth,
)
from .oracle import write_provider_statement


DOCUMENT_BATCH_SIZE = 10_000


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
    _write_document_iterator(
        client,
        database,
        iter(documents),
        batch_id,
        cleanup_stale=cleanup_stale,
    )


def _write_document_iterator(
    client: MongoClient[Any],
    database: str,
    documents: Iterable[dict[str, Any]],
    batch_id: str,
    *,
    cleanup_stale: bool = True,
    batch_size: int = DOCUMENT_BATCH_SIZE,
) -> int:
    """Write an iterator using bounded batches and release each batch after insert."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    collection = client[database][COLLECTION]
    if cleanup_stale:
        collection.delete_many({
            "testBatchId": {"$exists": True, "$ne": batch_id},
        })
    collection.delete_many({"testBatchId": batch_id})

    written = 0
    batch: list[dict[str, Any]] = []
    for document in documents:
        batch.append(_mongo_value(document))
        if len(batch) == batch_size:
            collection.insert_many(batch, ordered=False)
            written += len(batch)
            batch.clear()
    if batch:
        collection.insert_many(batch, ordered=False)
        written += len(batch)
        batch.clear()
    return written


def main() -> None:
    config = GeneratorConfig(
        seed=int(os.getenv("GENERATOR_SEED", "20260923")),
        records_per_region=int(os.getenv("RECORDS_PER_REGION", "5000")),
    )
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
    output_path = Path(os.getenv("GROUND_TRUTH_PATH", "/output/ground_truth.json"))
    with MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000) as client:
        client.admin.command("ping")
        for scope in scope_specs(config):
            _write_document_iterator(
                client,
                scope.database,
                iter_scope_documents(config, scope),
                config.batch_id,
            )
        client["paymenthub_generator"]["generation_runs"].replace_one(
            {"_id": config.batch_id},
            {
                "_id": config.batch_id,
                "seed": config.seed,
                "records_per_region": config.records_per_region,
                "status": "COMPLETED",
                "finished_at": datetime.now(UTC),
                "expected_documents": {
                    "VN": config.records_per_region,
                    "ASIA": config.records_per_region,
                    "INDIA": config.records_per_region,
                },
            },
            upsert=True,
        )

    statement_directory = Path(
        os.getenv("PROVIDER_STATEMENTS_DIR", "/output/provider-statements")
    )
    statement_directory.mkdir(parents=True, exist_ok=True)
    statement_rows: dict[str, int] = {}
    for scope in scope_specs(config):
        statement_rows[scope.filename] = write_provider_statement(
            statement_directory / scope.filename,
            config,
            scope,
            scope_document_count(config, scope),
        )
    write_streaming_ground_truth(output_path, config, statement_rows)
    print(
        f"seeded batch={config.batch_id} records_per_region={config.records_per_region}"
    )


if __name__ == "__main__":
    main()
