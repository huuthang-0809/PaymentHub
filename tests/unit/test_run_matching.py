from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from bootstrap.run_matching import run_matching
from canonical.adapters.vn import VNMapping, vn_to_canonical
from shared_kernel import Region
from shared_kernel.enums import Instrument


CSV_STATEMENT = b"""provider,region,tenant_id,provider_txn_id,provider_ref_id,merchant_ref,merchant_id,amount_minor,currency,status,txn_type,transaction_time
ONEPAY,VN,,onepay-payment-001,,system-001,merchant-001,149000,VND,SUCCESS,PAYMENT,2026-09-22T01:00:00+00:00
"""


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None
        self.job_run_id = UUID("00000000-0000-0000-0000-000000000002")

    def save_run(self, **values: Any) -> UUID:
        self.saved = values
        return self.job_run_id


def _adapter_transaction(*, deleted: bool = False):
    return vn_to_canonical(
        {
            "_id": "system-deleted" if deleted else "system-001",
            "CreatedOn": "2026-09-22T01:00:00+00:00",
            "PaymentMethod": "CARD",
            "Status": 1,
            "Amount": "149000",
            "PaymentTransactionId": "unused" if deleted else "onepay-payment-001",
            "MerchantId": "merchant-001",
            "IsDeleted": deleted,
        },
        VNMapping(instrument_by_method={"CARD": Instrument.CARD}),
    )


def test_pipeline_accepts_adapter_output_without_loading_mongo(tmp_path: Path) -> None:
    statement = tmp_path / "onepay.csv"
    statement.write_bytes(CSV_STATEMENT)
    repository = RecordingRepository()
    run_id = UUID("00000000-0000-0000-0000-000000000001")

    output = run_matching(
        [_adapter_transaction(), _adapter_transaction(deleted=True)],
        statement=statement,
        business_date=date(2026, 9, 22),
        region=Region.VN,
        provider="onepay",
        repository=repository,
        run_id=run_id,
    )

    assert output.run_id == run_id
    assert output.job_run_id == repository.job_run_id
    assert output.system_rows == 1
    assert output.provider_rows == 1
    assert output.summary == {"MATCHED": 1}
    assert repository.saved is not None
    assert repository.saved["provider"] == "ONEPAY"
    assert len(repository.saved["results"]) == 1
