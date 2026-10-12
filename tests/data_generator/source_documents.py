from datetime import timedelta
from decimal import Decimal
from typing import Any

from shared_kernel import TxnType

from .config import (
    MUTATION_CYCLE,
    GeneratorConfig,
    base_case,
    major_amount,
    scope_specs,
)


def _monitoring_state(config: GeneratorConfig, index: int, provider: str) -> str:
    if config.profile != "monitoring-rich":
        return "success"

    scope_records = config.records_per_region
    if provider in {"ESPAY", "2C2P"}:
        scope_records = (
            (config.records_per_region + 1) // 2
            if provider == "ESPAY"
            else config.records_per_region // 2
        )
    progress = index / max(1, scope_records - 1)
    provider_offset = {
        "ONEPAY": 11,
        "ESPAY": 29,
        "2C2P": 47,
        "RAZORPAY": 71,
    }[provider]
    bucket = (
        index * 37
        + config.seed * 101
        + provider_offset * 53
        + (index // 97) * 19
    ) % 1_000

    incidents = {
        (20260926, "ONEPAY"): (0.48, 0.51, "failed", 260),
        (20260929, "ESPAY"): (0.60, 0.64, "pending", 220),
        (20261002, "RAZORPAY"): (0.70, 0.73, "failed", 320),
        (20261005, "2C2P"): (0.38, 0.43, "failed", 180),
    }
    incident = incidents.get((config.seed, provider))
    if incident is not None:
        start, end, incident_state, incident_rate = incident
        if start <= progress < end and bucket < incident_rate:
            return incident_state

    failed_rate, pending_rate = {
        "ONEPAY": (25, 10),
        "ESPAY": (35, 20),
        "2C2P": (15, 10),
        "RAZORPAY": (30, 15),
    }[provider]
    if bucket < failed_rate:
        return "failed"
    if bucket < failed_rate + pending_rate:
        return "pending"
    if bucket < failed_rate + pending_rate + 5:
        return "unknown"
    return "success"


def vn_document(config: GeneratorConfig, index: int) -> dict[str, Any]:
    case = base_case(config, scope_specs(config)[0], index)
    amount: Any = str(major_amount(case))
    if case.mutation == "invalid-data":
        amount = "10.5"
    method = (
        "LINKCARD"
        if case.txn_type is TxnType.CARD_LINK
        else case.txn_type.value
        if case.txn_type is TxnType.REFUND
        else "CARD"
    )
    parent_index = (index // len(MUTATION_CYCLE)) * len(MUTATION_CYCLE)
    state = _monitoring_state(config, index, case.scope.provider)
    status = (
        {"success": 1, "failed": 2, "pending": 0, "unknown": 5}[state]
        if config.profile == "monitoring-rich"
        else 4 if case.mutation == "exact-match" else 1
    )
    return {
        "_id": case.system_txn_id,
        "Amount": amount,
        "BillIds": [f"vn-bill-{index:06d}"],
        "CreatedOn": case.transaction_time,
        "UpdatedOn": case.transaction_time + timedelta(seconds=30),
        "CustomerId": f"vn-customer-{index:06d}",
        "IsDeleted": False,
        "IsSentCallback": True,
        "MerchantId": case.merchant_id,
        "Message": "synthetic provider decline" if state == "failed" else None,
        "OrderInfo": (
            f"{case.scope.slug}-{config.seed}-{parent_index:06d}"
            if case.txn_type is TxnType.REFUND
            else None
        ),
        "PaidDate": (
            case.transaction_time + timedelta(seconds=20)
            if state == "success"
            else None
        ),
        "PaymentMethod": method,
        "PaymentProvider": 1,
        "PaymentTransactionId": case.provider_txn_id,
        "PollCount": index % 4,
        "ServiceType": "PAYMENT",
        "Status": status,
        "Success": state == "success",
        "testBatchId": config.batch_id,
        "testCaseId": case.case_id,
    }


def asia_document(
    config: GeneratorConfig, index: int, tenant_id: str
) -> dict[str, Any]:
    scope = next(item for item in scope_specs(config) if item.tenant_id == tenant_id)
    case = base_case(config, scope, index)
    amount = str(major_amount(case))
    amount_cross_check: Any = amount
    if case.mutation == "invalid-data":
        amount_cross_check = str(major_amount(case) + Decimal("1"))
    type_code = {
        TxnType.PAYMENT: 1,
        TxnType.REFUND: 2,
        TxnType.CARD_LINK: 3,
        TxnType.TOKENIZATION: 4,
    }[case.txn_type]
    cycle = index // len(MUTATION_CYCLE)
    parent_index = cycle * len(MUTATION_CYCLE)
    state = _monitoring_state(config, index, case.scope.provider)
    status = {"success": 2, "failed": 3, "pending": 1, "unknown": 99}[state]
    if case.txn_type is TxnType.REFUND and state == "success":
        status = 4
    channels = ("QR", "APP", "WEB", "POS") if config.profile == "monitoring-rich" else ("QR",)
    payment_methods = ("qr", "card", "ewallet") if config.profile == "monitoring-rich" else ("qr",)
    return {
        "_id": case.system_txn_id,
        "Amount": amount_cross_check,
        "AmountAsDecimal": amount,
        "BillIds": [f"{tenant_id}-bill-{index:06d}"],
        "Channel": channels[index % len(channels)],
        "CreatedAt": case.transaction_time,
        "Currency": scope.currency,
        "CustomerId": f"{tenant_id}-customer-{index:06d}",
        "ExternalTxnId": case.provider_txn_id,
        "IsDeleted": False,
        "IsSentCallback": True,
        "MerchantCode": case.merchant_id,
        "MerchantRefCode": f"{tenant_id}-provider-merchant-{index:06d}",
        "Paid": state == "success",
        "PaidDate": case.transaction_time + timedelta(seconds=20) if state == "success" else None,
        "PaymentMethodId": payment_methods[index % len(payment_methods)],
        "PaymentRefId": case.provider_ref_id,
        "RefundFor": (
            f"{scope.slug}-{config.seed}-{parent_index:06d}"
            if case.txn_type is TxnType.REFUND
            else None
        ),
        "ServiceId": "PAYMENT",
        "Status": status,
        "TenantId": tenant_id,
        "Type": type_code,
        "UpdatedAt": case.transaction_time + timedelta(seconds=30),
        "isSyntheticTestData": None,
        "testBatchId": config.batch_id,
        "testCaseId": case.case_id,
    }


def india_document(config: GeneratorConfig, index: int) -> dict[str, Any]:
    case = base_case(config, scope_specs(config)[3], index)
    amount: Any = major_amount(case)
    if case.mutation == "invalid-data":
        amount = Decimal("10.001")
    parent_index = (index // len(MUTATION_CYCLE)) * len(MUTATION_CYCLE)
    refund_case = base_case(config, case.scope, parent_index + 4)
    state = _monitoring_state(config, index, case.scope.provider)
    terminal = (
        case.txn_type is not TxnType.TOKENIZATION
        and state not in {"pending", "unknown"}
    )
    instruments = (
        ("card", "virtual_account", "ewallet", "qr")
        if config.profile == "monitoring-rich"
        else ("card",)
    )
    baseline_status = (
        "processed"
        if case.txn_type is TxnType.REFUND
        else "refunded"
        if case.mutation == "exact-match"
        else "captured"
        if terminal
        else "created"
    )
    monitoring_status = {
        "success": "captured",
        "failed": "failed",
        "pending": "pending",
        "unknown": "unrecognized",
    }[state]
    document_status = (
        monitoring_status
        if config.profile == "monitoring-rich" and case.txn_type is TxnType.PAYMENT
        else "success"
        if config.profile == "monitoring-rich" and state == "success"
        else state
        if config.profile == "monitoring-rich"
        else baseline_status
    )
    return {
        "_id": case.system_txn_id,
        "amount": amount,
        "amountRefunded": (
            major_amount(refund_case)
            if case.mutation == "exact-match"
            else Decimal("0")
        ),
        "createdAt": case.transaction_time,
        "updatedAt": case.transaction_time + timedelta(seconds=30),
        "currency": case.scope.currency,
        "customerId": f"india-customer-{index:06d}",
        "failureReason": "synthetic provider decline" if state == "failed" else None,
        "gatewayCreatedAt": case.transaction_time + timedelta(seconds=5),
        "instrumentType": instruments[index % len(instruments)],
        "merchantId": case.merchant_id,
        "paidAt": (
            case.transaction_time + timedelta(seconds=20) if state == "success" else None
        ),
        "reconcileAttempts": index % 4,
        "relatedPaymentId": (
            f"{case.scope.provider.lower()}-{config.seed}-{parent_index:06d}"
            if case.txn_type is TxnType.REFUND
            else None
        ),
        "serviceType": "PAYMENT",
        "sourceIds": [f"india-source-{index:06d}"],
        "status": document_status,
        "terminal": terminal,
        "transactionId": case.provider_txn_id,
        "type": case.txn_type.value,
        "merchant_callback_log": [{}],
        "testBatchId": config.batch_id,
        "testCaseId": case.case_id,
    }
