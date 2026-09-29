import hashlib
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from reconciliation.adapters import PostgreReconRepository
from reconciliation.domain import (
    ProviderTransaction,
    ReconResult,
    ReconResultType,
)
from shared_kernel import Region, TxnType


RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
STATEMENT_CONTENT = b"provider statement bytes\n"


def _connection() -> tuple[MagicMock, MagicMock, MagicMock]:
    connection = MagicMock()
    transaction = MagicMock()
    cursor_context = MagicMock()
    cursor = MagicMock()
    connection.transaction.return_value = transaction
    connection.cursor.return_value = cursor_context
    cursor_context.__enter__.return_value = cursor
    return connection, transaction, cursor


def _provider_transaction() -> ProviderTransaction:
    return ProviderTransaction(
        provider="ESPAY",
        region=Region.ASIA,
        tenant_id="asia-id",
        provider_txn_id="provider-001",
        provider_ref_id="reference-001",
        merchant_ref="merchant-reference-001",
        merchant_id="merchant-001",
        amount_minor=149_000,
        currency="IDR",
        status="SUCCESS",
        txn_type=TxnType.PAYMENT,
        transaction_time=datetime(2026, 9, 22, 1, tzinfo=timezone.utc),
        source_file="espay.csv",
        source_row_number=2,
        raw={"account": "masked", "provider_status": "00"},
    )


def _result() -> ReconResult:
    return ReconResult(
        run_id=str(RUN_ID),
        result_type=ReconResultType.MATCHED,
        region=Region.ASIA,
        tenant_id="asia-id",
        provider="ESPAY",
        txn_type=TxnType.PAYMENT,
        system_txn_id="system-001",
        system_amount_minor=149_000,
        system_currency="IDR",
        provider_txn_id="provider-001",
        provider_amount_minor=149_000,
        provider_currency="IDR",
        delta_amount=0,
        source_file="espay.csv",
        source_row_number=2,
        reason="Exact provider transaction ID",
    )


def test_save_run_persists_checksum_timezone_scope_and_lineage() -> None:
    connection, transaction, cursor = _connection()
    repository = PostgreReconRepository(
        connection,
        business_timezone="Asia/Jakarta",
    )

    job_run_id = repository.save_run(
        run_id=RUN_ID,
        business_date=date(2026, 9, 22),
        region=Region.ASIA,
        tenant_id="asia-id",
        provider="ESPAY",
        statement=Path("espay.csv"),
        statement_content=STATEMENT_CONTENT,
        provider_txns=[_provider_transaction()],
        results=[_result()],
    )

    job_values = cursor.execute.call_args_list[0].args[1]
    run_values = cursor.execute.call_args_list[1].args[1]
    provider_values = cursor.executemany.call_args_list[0].args[1][0]
    result_values = cursor.executemany.call_args_list[1].args[1][0]

    assert job_values[0] == job_run_id
    assert job_values[3:5] == ("asia-id", "ESPAY")
    assert run_values == (
        RUN_ID,
        job_run_id,
        date(2026, 9, 22),
        "ASIA",
        "asia-id",
        "ESPAY",
        "espay.csv",
        hashlib.sha256(STATEMENT_CONTENT).hexdigest(),
        "Asia/Jakarta",
        time.min,
    )
    assert provider_values[:2] == (RUN_ID, job_run_id)
    assert provider_values[3] == "asia-id"
    assert provider_values[16].obj == {
        "account": "masked",
        "provider_status": "00",
    }
    assert result_values[:2] == (RUN_ID, job_run_id)
    assert result_values[8:14] == (
        "system-001",
        149_000,
        "IDR",
        "provider-001",
        149_000,
        "IDR",
    )
    transaction.__enter__.assert_called_once_with()
    transaction.__exit__.assert_called_once_with(None, None, None)


def test_save_run_propagates_detail_failure_to_transaction_context() -> None:
    connection, transaction, cursor = _connection()
    cursor.executemany.side_effect = [None, RuntimeError("result insert failed")]
    repository = PostgreReconRepository(connection)

    with pytest.raises(RuntimeError, match="result insert failed"):
        repository.save_run(
            run_id=RUN_ID,
            business_date=date(2026, 9, 22),
            region=Region.ASIA,
            tenant_id="asia-id",
            provider="ESPAY",
            statement=Path("espay.csv"),
            statement_content=STATEMENT_CONTENT,
            provider_txns=[_provider_transaction()],
            results=[_result()],
        )

    exit_args = transaction.__exit__.call_args.args
    assert exit_args[0] is RuntimeError
    assert str(exit_args[1]) == "result insert failed"


def test_repository_rejects_unknown_business_timezone() -> None:
    connection, _, _ = _connection()

    with pytest.raises(ValueError, match="Unknown business timezone"):
        PostgreReconRepository(connection, business_timezone="Not/AZone")

