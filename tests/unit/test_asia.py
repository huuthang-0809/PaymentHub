from canonical.adapters.asia import AsiaMapping, asia_to_canonical
from shared_kernel import AmountStatus, CanonicalStatus, TxnType
from datetime import datetime, timezone
from decimal import Decimal

SYNTHETIC_MAPPING = AsiaMapping(
    type_code={7: TxnType.PAYMENT, 8: TxnType.REFUND, 9:TxnType.CARD_LINK}
)

doc = {
    "_id": "ai",
    "CreatedAt": datetime(2026, 9, 21, tzinfo=timezone.utc),
    "Status": 2,
    "Type": 7,
    "Currency": "IDR",
    "AmountAsDecimal": Decimal("100"),
    "Amount": 100,
    "ExternalTxnId": "e1",
    "PaymentRefId": "r1",
    "MerchantCode": "merchant",
    "BillIds": ["bill1", "bill2"],
    "IsSentCallback": True,
}


def map_doc(document: dict[str, object], tenant_id: str = "asia-id"):
    return asia_to_canonical(document, tenant_id, SYNTHETIC_MAPPING)

def test_happy_mapping():
    txn = map_doc(doc)
    assert(txn.amount_minor, txn.currency, txn.amount_status) == (100, "IDR", AmountStatus.OK)