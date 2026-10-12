import json
import os
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from .generator import (
    COLLECTION,
    DATABASE_ASIA_ID,
    DATABASE_ASIA_PH,
    DATABASE_INDIA,
    DATABASE_VN,
)
from .ground_truth import assert_matches_ground_truth


def main() -> None:
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
    truth_path = Path(os.getenv("GROUND_TRUTH_PATH", "/output/ground_truth.json"))
    truth: dict[str, Any] = json.loads(truth_path.read_text(encoding="utf-8"))
    batch_id = truth["batch_id"]
    with MongoClient(
        mongo_uri, tz_aware=True, serverSelectionTimeoutMS=10_000
    ) as client:
        datasets = {
            database: list(
                client[database][COLLECTION].find({"testBatchId": batch_id})
            )
            for database in (
                DATABASE_VN,
                DATABASE_ASIA_ID,
                DATABASE_ASIA_PH,
                DATABASE_INDIA,
            )
        }
    assert_matches_ground_truth(datasets, truth)
    print(f"ground truth matched for batch={batch_id}")


if __name__ == "__main__":
    main()
