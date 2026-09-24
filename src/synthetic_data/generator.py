from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from canonical.adapters.asia import AsiaMapping, asia_to_canonical
from canonical.adapters.india import india_to_canonical
from canonical.adapters.vn import VNMapping, vn_to_canonical
from canonical.domain import CanonicalTransaction
from shared_kernel import TxnType
from shared_kernel.enums import Instrument


DATABASE_VN = "paymenthub_vn"
DATABASE_ASIA_ID = "paymenthub_asia_id"
DATABASE_ASIA_PH = "paymenthub_asia_ph"
DATABASE_INDIA = "paymenthub_india"
COLLECTION = "payment_transactions"

VN_MAPPING = VNMapping(
    instrument_by_method={"CARD": Instrument.CARD},
    card_link_methods=frozenset({"LINKCARD"}),
)
ASIA_MAPPING = AsiaMapping(
    instrument_code={
        "card": Instrument.CARD,
        "qr": Instrument.QR,
        "virtual-account": Instrument.VIRTUAL_ACCOUNT,
    },
    synthetic_flag_value={"synthetic": True},
)


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    seed: int = 20260923
    records_per_region: int = 5_000

    def __post_init__(self) -> None:
        if self.records_per_region <= 0:
            raise ValueError("records_per_region must be positive")
        try:
            datetime.strptime(str(self.seed), "%Y%m%d")
        except ValueError as exc:
            raise ValueError("seed must use YYYYMMDD format") from exc

    @property
    def batch_id(self) -> str:
        return f"seed-{self.seed}"

    @property
    def base_time(self) -> datetime:
        parsed = datetime.strptime(str(self.seed), "%Y%m%d")
        return parsed.replace(tzinfo=UTC)


def _scenario(index: int) -> str:
    scenarios = (
        "pending",
        "success",
        "success",
        "success",
        "success",
        "success",
        "success",
        "success",
        "failed",
        "failed",
        "refund",
        "unknown_status",
        "invalid_amount",
        "missing_currency",
        "non_monetary",
        "duplicate_provider_id",
    )
    return scenarios[index % len(scenarios)]


def _event_time(config: GeneratorConfig, index: int) -> datetime:
    return config.base_time + timedelta(seconds=index)


def _vn_document(config: GeneratorConfig, index: int) -> dict[str, Any]:
    scenario = _scenario(index)
    status = {
        "pending": 0,
        "failed": 2,
        "refund": 4,
        "unknown_status": 99,
    }.get(scenario, 1)
    method = "REFUND" if scenario == "refund" else "CARD"
    if scenario == "non_monetary":
        method = "LINKCARD"
    amount: Any = str(100_000 + index)
    if scenario in {"invalid_amount", "missing_currency"}:
        amount = "10.5"
    provider_txn_id = (
        "onepay-intentional-duplicate"
        if scenario == "duplicate_provider_id"
        else f"onepay-{config.seed}-{index:06d}"
    )
    created_at = _event_time(config, index)
    return {
        "_id": f"vn-{config.seed}-{index:06d}",
        "Amount": amount,
        "BillIds": [f"vn-bill-{index:06d}"],
        "CreatedOn": created_at,
        "UpdatedOn": created_at + timedelta(seconds=30),
        "CustomerId": f"vn-customer-{index % 100:03d}",
        "IsDeleted": False,
        "IsSentCallback": scenario == "success",
        "MerchantId": f"vn-merchant-{index % 10:02d}",
        "Message": "declined" if scenario == "failed" else None,
        "OrderInfo": f"vn-parent-{index:06d}" if scenario == "refund" else None,
        "PaidDate": created_at + timedelta(seconds=20) if status in {1, 3, 4} else None,
        "PaymentMethod": method,
        "PaymentProvider": 1,
        "PaymentTransactionId": provider_txn_id,
        "PollCount": index % 4,
        "ServiceType": "PAYMENT",
        "Status": status,
        "Success": status in {1, 3, 4},
        "testBatchId": config.batch_id,
    }


def _asia_document(
    config: GeneratorConfig, index: int, tenant_id: str
) -> dict[str, Any]:
    scenario = _scenario(index)
    currency = "IDR" if tenant_id == "asia-id" else "PHP"
    status = {
        "pending": 1,
        "failed": 3,
        "refund": 4,
        "unknown_status": 99,
    }.get(scenario, 2)
    txn_type = 2 if scenario == "refund" else 1
    if scenario == "non_monetary":
        txn_type = 3
    amount: Any = "100000" if currency == "IDR" else "100.00"
    amount_cross_check: Any = amount
    if scenario == "invalid_amount":
        amount_cross_check = "100001" if currency == "IDR" else "101.00"
    source_currency: str | None = None if scenario == "missing_currency" else currency
    external_txn_id = (
        f"{tenant_id}-intentional-duplicate"
        if scenario == "duplicate_provider_id"
        else f"{tenant_id}-provider-{config.seed}-{index:06d}"
    )
    created_at = _event_time(config, index)
    return {
        "_id": f"{tenant_id}-{config.seed}-{index:06d}",
        "Amount": amount_cross_check,
        "AmountAsDecimal": amount,
        "BillIds": [f"{tenant_id}-bill-{index:06d}"],
        "Channel": "QR",
        "CreatedAt": created_at,
        "Currency": source_currency,
        "CustomerId": f"{tenant_id}-customer-{index % 100:03d}",
        "ExternalTxnId": external_txn_id,
        "IsDeleted": False,
        "IsSentCallback": scenario == "success",
        "MerchantCode": f"{tenant_id}-merchant-{index % 10:02d}",
        "MerchantRefCode": f"{tenant_id}-provider-merchant-{index % 10:02d}",
        "Paid": status in {2, 4, 5},
        "PaidDate": created_at + timedelta(seconds=20) if status in {2, 4, 5} else None,
        "PaymentMethodId": "qr",
        "PaymentRefId": f"{tenant_id}-ref-{config.seed}-{index:06d}",
        "RefundFor": f"{tenant_id}-parent-{index:06d}" if scenario == "refund" else None,
        "ServiceId": "PAYMENT",
        "Status": status,
        "TenantId": tenant_id,
        "Type": txn_type,
        "UpdatedAt": created_at + timedelta(seconds=30),
        "isSyntheticTestData": "synthetic" if scenario == "non_monetary" else None,
        "testBatchId": config.batch_id,
    }


def _india_document(config: GeneratorConfig, index: int) -> dict[str, Any]:
    scenario = _scenario(index)
    txn_type = "REFUND" if scenario == "refund" else "PAYMENT"
    status = {
        "pending": "pending",
        "failed": "failed",
        "refund": "processed",
        "unknown_status": "new-provider-status",
    }.get(scenario, "captured")
    amount: Any = Decimal("100.50")
    if scenario == "invalid_amount":
        amount = "100.001"
    currency: str | None = None if scenario == "missing_currency" else "INR"
    transaction_id = (
        "razorpay-intentional-duplicate"
        if scenario == "duplicate_provider_id"
        else f"razorpay-{config.seed}-{index:06d}"
    )
    created_at = _event_time(config, index)
    terminal = status not in {"pending", "new-provider-status"}
    return {
        "_id": f"india-{config.seed}-{index:06d}",
        "amount": amount,
        "amountRefunded": Decimal("100.50") if scenario == "refund" else Decimal("0"),
        "createdAt": created_at,
        "updatedAt": created_at + timedelta(seconds=30),
        "currency": currency,
        "customerId": f"india-customer-{index % 100:03d}",
        "failureReason": "declined" if scenario == "failed" else None,
        "gatewayCreatedAt": created_at + timedelta(seconds=5),
        "instrumentType": "card",
        "merchantId": f"india-merchant-{index % 10:02d}",
        "paidAt": created_at + timedelta(seconds=20) if terminal else None,
        "reconcileAttempts": index % 4,
        "referenceId": f"india-reference-{index:06d}",
        "relatedPaymentId": f"razorpay-parent-{index:06d}" if scenario == "refund" else None,
        "serviceType": "PAYMENT",
        "sourceIds": [f"india-source-{index:06d}"],
        "status": status,
        "terminal": terminal,
        "transactionId": transaction_id,
        "type": txn_type,
        "merchant_callback_log": [{}] if scenario == "success" else [],
        "testBatchId": config.batch_id,
    }


def _summarize(
    transactions: list[CanonicalTransaction], scenario_counts: Counter[str]
) -> dict[str, Any]:
    return {
        "total": len(transactions),
        "by_status": dict(sorted(Counter(txn.status.value for txn in transactions).items())),
        "by_type": dict(sorted(Counter(txn.txn_type.value for txn in transactions).items())),
        "by_amount_status": dict(
            sorted(Counter(txn.amount_status.value for txn in transactions).items())
        ),
        "scenarios": dict(sorted(scenario_counts.items())),
    }


def generate_dataset(
    config: GeneratorConfig = GeneratorConfig(),
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    vn_documents = [_vn_document(config, index) for index in range(config.records_per_region)]
    india_documents = [
        _india_document(config, index) for index in range(config.records_per_region)
    ]
    asia_id_count = (config.records_per_region + 1) // 2
    asia_ph_count = config.records_per_region // 2
    asia_id_documents = [
        _asia_document(config, index, "asia-id") for index in range(asia_id_count)
    ]
    asia_ph_documents = [
        _asia_document(config, index + asia_id_count, "asia-ph")
        for index in range(asia_ph_count)
    ]

    vn_transactions = [vn_to_canonical(document, VN_MAPPING) for document in vn_documents]
    asia_transactions = [
        asia_to_canonical(document, "asia-id", ASIA_MAPPING)
        for document in asia_id_documents
    ] + [
        asia_to_canonical(document, "asia-ph", ASIA_MAPPING)
        for document in asia_ph_documents
    ]
    india_transactions = [india_to_canonical(document) for document in india_documents]

    scenario_counts = Counter(
        _scenario(index) for index in range(config.records_per_region)
    )
    datasets = {
        DATABASE_VN: vn_documents,
        DATABASE_ASIA_ID: asia_id_documents,
        DATABASE_ASIA_PH: asia_ph_documents,
        DATABASE_INDIA: india_documents,
    }
    ground_truth = {
        "seed": config.seed,
        "batch_id": config.batch_id,
        "records_per_region": config.records_per_region,
        "collection": COLLECTION,
        "expected_documents": {
            "VN": len(vn_documents),
            "ASIA": len(asia_id_documents) + len(asia_ph_documents),
            "INDIA": len(india_documents),
        },
        "canonical": {
            "VN": _summarize(vn_transactions, scenario_counts),
            "ASIA": _summarize(asia_transactions, scenario_counts),
            "INDIA": _summarize(india_transactions, scenario_counts),
        },
    }
    return datasets, ground_truth
