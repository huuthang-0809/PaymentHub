import json
import os
from collections import Counter
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from pymongo import MongoClient

from canonical.adapters.vn import MongoVnSource, VNMapping
from reconciliation.adapters import CsvFixtureParser
from reconciliation.domain import MatchingEngine, ReconResult
from shared_kernel.enums import Instrument, Region


def _result_row(result: ReconResult) -> dict[str, object]:
    return {
        "result_type": result.result_type.value,
        "region": result.region.value,
        "tenant_id": result.tenant_id,
        "provider": result.provider,
        "system_txn_id": result.system_txn_id,
        "provider_txn_id": result.provider_txn_id,
        "system_amount_minor": result.system_amount_minor,
        "provider_amount_minor": result.provider_amount_minor,
        "delta_amount": result.delta_amount,
        "source_file": result.source_file,
        "source_row_number": result.source_row_number,
        "reason": result.reason,
    }


def main() -> None:
    statement = Path(
        os.getenv(
            "PROVIDER_STATEMENT_FILE",
            "data/provider/inbox/onepay_2026-09-22.csv",
        )
    )
    business_date = date.fromisoformat(os.getenv("MONGO_BUSINESS_DATE", "2026-09-22"))
    report = CsvFixtureParser().parse(statement.read_bytes(), statement.name)
    if not report.valid:
        print(
            json.dumps(
                {
                    "statement": str(statement),
                    "issues": [
                        {
                            "row": issue.row,
                            "field": issue.field,
                            "code": issue.code.value,
                            "message": issue.message,
                        }
                        for issue in report.issues
                    ],
                },
                indent=2,
            )
        )
        raise SystemExit(1)

    with MongoClient(
        os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        tz_aware=True,
        serverSelectionTimeoutMS=5_000,
    ) as client:
        source = MongoVnSource(
            client,
            "paymenthub_vn",
            "PaymentTransactions",
            VNMapping(instrument_by_method={"CARD": Instrument.CARD}),
            ZoneInfo("Asia/Ho_Chi_Minh"),
        )
        system_rows = [
            row
            for row in source.get_transactions(Region.VN, None, "ONEPAY", business_date)
            if not row.is_deleted and not row.is_test_data
        ]

    results = MatchingEngine().match(
        os.getenv("RECON_RUN_ID", "local-demo-run"),
        list(report.rows),
        system_rows,
    )
    summary = Counter(result.result_type.value for result in results)
    print(
        json.dumps(
            {
                "statement": str(statement),
                "system_rows": len(system_rows),
                "provider_rows": len(report.rows),
                "summary": dict(sorted(summary.items())),
                "results": [_result_row(result) for result in results],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
