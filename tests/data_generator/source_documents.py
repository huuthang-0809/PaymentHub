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
        "Message": None,
        "OrderInfo": (
            f"{case.scope.slug}-{config.seed}-{parent_index:06d}"
            if case.txn_type is TxnType.REFUND
            else None
        ),
        "PaidDate": case.transaction_time + timedelta(seconds=20),
        "PaymentMethod": method,
        "PaymentProvider": 1,
        "PaymentTransactionId": case.provider_txn_id,
        "PollCount": index % 4,
        "ServiceType": "PAYMENT",
        "Status": 4 if case.mutation == "exact-match" else 1,
        "Success": True,
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
    return {
        "_id": case.system_txn_id,
        "Amount": amount_cross_check,
        "AmountAsDecimal": amount,
        "BillIds": [f"{tenant_id}-bill-{index:06d}"],
        "Channel": "QR",
        "CreatedAt": case.transaction_time,
        "Currency": scope.currency,
        "CustomerId": f"{tenant_id}-customer-{index:06d}",
        "ExternalTxnId": case.provider_txn_id,
        "IsDeleted": False,
        "IsSentCallback": True,
        "MerchantCode": case.merchant_id,
        "MerchantRefCode": f"{tenant_id}-provider-merchant-{index:06d}",
        "Paid": True,
        "PaidDate": case.transaction_time + timedelta(seconds=20),
        "PaymentMethodId": "qr",
        "PaymentRefId": case.provider_ref_id,
        "RefundFor": (
            f"{scope.slug}-{config.seed}-{parent_index:06d}"
            if case.txn_type is TxnType.REFUND
            else None
        ),
        "ServiceId": "PAYMENT",
        "Status": 4 if case.txn_type is TxnType.REFUND else 2,
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
    terminal = case.txn_type is not TxnType.TOKENIZATION
    parent_index = (index // len(MUTATION_CYCLE)) * len(MUTATION_CYCLE)
    refund_case = base_case(config, case.scope, parent_index + 4)
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
        "failureReason": None,
        "gatewayCreatedAt": case.transaction_time + timedelta(seconds=5),
        "instrumentType": "card",
        "merchantId": case.merchant_id,
        "paidAt": (
            case.transaction_time + timedelta(seconds=20) if terminal else None
        ),
        "reconcileAttempts": index % 4,
        "relatedPaymentId": (
            f"{case.scope.provider.lower()}-{config.seed}-{parent_index:06d}"
            if case.txn_type is TxnType.REFUND
            else None
        ),
        "serviceType": "PAYMENT",
        "sourceIds": [f"india-source-{index:06d}"],
        "status": (
            "processed"
            if case.txn_type is TxnType.REFUND
            else "refunded"
            if case.mutation == "exact-match"
            else "captured"
            if terminal
            else "created"
        ),
        "terminal": terminal,
        "transactionId": case.provider_txn_id,
        "type": case.txn_type.value,
        "merchant_callback_log": [{}],
        "testBatchId": config.batch_id,
        "testCaseId": case.case_id,
    }
