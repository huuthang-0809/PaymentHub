"""Verify the complete generated dataset through the persisted recon pipeline.

This test is intentionally opt-in because it needs the disposable PostgreSQL and
MongoDB instances populated by the generator.

docker compose run --rm generator

F06_TEST_DATABASE_URL="postgresql://paymenthub:paymenthub@localhost:5432/paymenthub" \
F11_TEST_MONGO_URI="mongodb://localhost:27017" \
pytest tests/integration
"""

import json
import os
from dataclasses import asdict
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from pymongo import MongoClient

from canonical.adapters.asia import AsiaMapping, MongoAsiaSource
from canonical.adapters.india import MongoIndiaSource
from canonical.adapters.vn import MongoVnSource, VNMapping
from reconciliation.adapters import CsvFixtureParser, PostgreReconRepository
from reconciliation.adapters.query import PostgreReconQuery
from reconciliation.application import RunMatching
from shared_kernel import Region
from shared_kernel.enums import Instrument


POSTGRES_URL = os.getenv("F06_TEST_DATABASE_URL")
MONGO_URL = os.getenv("F11_TEST_MONGO_URI")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL or not MONGO_URL,
    reason=(
        "Set F06_TEST_DATABASE_URL and F11_TEST_MONGO_URI to disposable, "
        "migrated and generated test databases"
    ),
)

GENERATED_DIR = Path(__file__).resolve().parents[1] / "generated"
GROUND_TRUTH_PATH = GENERATED_DIR / "ground_truth.json"
STATEMENTS_DIR = GENERATED_DIR / "provider-statements"

BUSINESS_TIMEZONES = {
    Region.VN: "Asia/Ho_Chi_Minh",
    Region.INDIA: "Asia/Kolkata",
    (Region.ASIA, "asia-id"): "Asia/Jakarta",
    (Region.ASIA, "asia-ph"): "Asia/Manila",
}


def _asia_mapping() -> AsiaMapping:
    return AsiaMapping(
        instrument_code={
            "card": Instrument.CARD,
            "qr": Instrument.QR,
            "virtual-account": Instrument.VIRTUAL_ACCOUNT,
        },
        synthetic_flag_value={"synthetic": True},
    )


def _source(mongo: MongoClient, region: Region):
    if region is Region.VN:
        return MongoVnSource(
            mongo,
            "paymenthub_vn",
            "payment_transactions",
            mapping=VNMapping(
                instrument_by_method={"CARD": Instrument.CARD},
                card_link_methods=frozenset({"LINKCARD"}),
            ),
            business_timezone=ZoneInfo(BUSINESS_TIMEZONES[Region.VN]),
        )
    if region is Region.INDIA:
        return MongoIndiaSource(
            mongo,
            "paymenthub_india",
            "payment_transactions",
            business_timezone=ZoneInfo(BUSINESS_TIMEZONES[Region.INDIA]),
        )
    return MongoAsiaSource(
        mongo,
        {
            "asia-id": "paymenthub_asia_id",
            "asia-ph": "paymenthub_asia_ph",
        },
        "payment_transactions",
        mapping=_asia_mapping(),
        business_timezones={
            tenant: ZoneInfo(BUSINESS_TIMEZONES[(Region.ASIA, tenant)])
            for tenant in ("asia-id", "asia-ph")
        },
    )


def _timezone(region: Region, tenant_id: str | None) -> str:
    if region is Region.ASIA:
        assert tenant_id is not None
        return BUSINESS_TIMEZONES[(region, tenant_id)]
    return BUSINESS_TIMEZONES[region]


def _normalized_result(result) -> dict:
    values = asdict(result)
    values.pop("run_id")
    values["result_type"] = result.result_type.value
    values["region"] = result.region.value
    values["txn_type"] = result.txn_type.value if result.txn_type else None
    values["system_status"] = (
        result.system_status.value if result.system_status else None
    )
    # PostgreSQL NUMERIC columns are loaded as Decimal while the JSON oracle
    # stores exact minor-unit amounts as integers.
    values = {
        key: int(value) if isinstance(value, Decimal) else value
        for key, value in values.items()
    }
    return values


def _sorted_results(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda item: json.dumps(item, sort_keys=True))


def test_all_generated_reconciliation_results_match_ground_truth() -> None:
    oracle = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    business_date = date.fromisoformat(
        f"{oracle['batch_id'][5:9]}-{oracle['batch_id'][9:11]}-"
        f"{oracle['batch_id'][11:13]}"
    )

    with (
        MongoClient(MONGO_URL, tz_aware=True, serverSelectionTimeoutMS=5_000) as mongo,
        psycopg.connect(POSTGRES_URL) as connection,
    ):
        mongo.admin.command("ping")

        for filename, expected in sorted(oracle["reconciliation"].items()):
            scope = expected["scope"]
            region = Region(scope["region"])
            tenant_id = scope["tenant_id"]
            provider = scope["provider"]
            statement = STATEMENTS_DIR / filename
            # Keep the CSV rows byte-for-byte equivalent while giving this
            # integration suite its own idempotency checksum. This prevents a
            # pre-existing API/demo run for the generated file from being
            # replayed instead of exercising the current pipeline.
            statement_content = statement.read_bytes().rstrip(b"\r\n") + b"\r\n"

            runner = RunMatching(
                _source(mongo, region),
                CsvFixtureParser(),
                PostgreReconRepository(
                    connection,
                    business_timezone=_timezone(region, tenant_id),
                ),
            )
            output = runner.execute(
                statement=statement,
                statement_content=statement_content,
                business_date=business_date,
                region=region,
                tenant_id=tenant_id,
                provider=provider,
                business_timezone=_timezone(region, tenant_id),
                cutoff_time=time(2),
            )

            assert output.system_rows == expected["system_rows"], filename
            assert output.provider_rows == expected["provider_rows"], filename
            assert output.summary == expected["expected_summary"], filename

            # Read back from PostgreSQL so the assertion also covers persistence
            # serialization, rather than only checking the in-memory output.
            metadata, stored = PostgreReconQuery(connection).snapshot(output.run_id)
            assert metadata["status"] == "COMPLETED", filename
            assert stored is not None
            assert stored.system_rows == expected["system_rows"], filename
            assert stored.provider_rows == expected["provider_rows"], filename
            assert stored.summary == expected["expected_summary"], filename

            expected_results = []
            for result in expected["expected_results"]:
                values = dict(result)
                values.pop("case_id")
                expected_results.append(values)

            assert _sorted_results(
                [_normalized_result(result) for result in stored.results]
            ) == _sorted_results(expected_results), filename
