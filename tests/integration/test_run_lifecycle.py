"""Run against a disposable, migrated PostgreSQL database via F06_TEST_DATABASE_URL."""

import os
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from reconciliation.adapters import CsvFixtureParser, PostgreReconRepository
from reconciliation.application import RunMatching, RunInProgress
from reconciliation.domain import ProviderTransaction
from shared_kernel import Region, TxnType


pytestmark = pytest.mark.skipif(
    not os.getenv("F06_TEST_DATABASE_URL"),
    reason="Set F06_TEST_DATABASE_URL to a disposable migrated PostgreSQL database",
)

STATEMENT = b"""provider,region,tenant_id,provider_txn_id,provider_ref_id,merchant_ref,merchant_id,amount_minor,currency,status,txn_type,transaction_time
ONEPAY,VN,,onepay-f06-001,,system-f06-001,merchant-001,149000,VND,SUCCESS,PAYMENT,2026-09-22T01:00:00+00:00
"""


class EmptySource:
    def __init__(self) -> None:
        self.calls = 0

    def get_transactions(self, *args):
        self.calls += 1
        return ()


def _claim(repo, provider, *, region=Region.VN, tenant_id=None):
    return repo.claim_run(
        business_date=date(2026, 9, 22), region=region,
        tenant_id=tenant_id, provider=provider, statement=Path("f06.csv"),
        statement_content=STATEMENT,
    )


def test_replay_concurrency_recovery_and_scope_mismatch() -> None:
    url = os.environ["F06_TEST_DATABASE_URL"]
    provider = f"TEST{uuid4().hex[:12].upper()}"
    with psycopg.connect(url) as first, psycopg.connect(url) as second:
        repo1, repo2 = PostgreReconRepository(first), PostgreReconRepository(second)
        with _claim(repo1, provider) as initial:
            assert initial.existing is None
            with pytest.raises(RunInProgress):
                with _claim(repo2, provider):
                    pass
            # Simulates a dead worker: durable RUNNING metadata, no detail set.
        with _claim(repo2, provider) as resumed:
            assert resumed.run_id == initial.run_id
            assert resumed.job_run_id == initial.job_run_id
            repo2.complete_run(claim=resumed, system_rows=0, provider_txns=[], results=[])
        with _claim(repo1, provider) as replay:
            assert replay.run_id == initial.run_id
            assert replay.existing is not None
            assert replay.existing.summary == {}
        with pytest.raises(ValueError, match="different region or tenant"):
            with _claim(repo1, provider, region=Region.INDIA):
                pass
        assert first.execute(
            "SELECT count(*) FROM recon_run WHERE provider = %s", (provider,)
        ).fetchone()[0] == 1


def test_pipeline_replay_and_failed_detail_transaction() -> None:
    url = os.environ["F06_TEST_DATABASE_URL"]
    provider = f"TEST{uuid4().hex[:12].upper()}"
    source = EmptySource()
    with psycopg.connect(url) as connection:
        repo = PostgreReconRepository(connection)
        runner = RunMatching(source, CsvFixtureParser(), repo)
        # Use an out-of-scope statement to check durable validation failure.
        with pytest.raises(ValueError):
            runner.execute(
                statement=Path("f06.csv"), statement_content=STATEMENT,
                business_date=date(2026, 9, 22), region=Region.VN,
                tenant_id=None, provider=provider,
            )
        assert connection.execute(
            "SELECT status FROM recon_run WHERE provider = %s", (provider,)
        ).fetchone()[0] == "FAILED"

        # A different file with the requested provider succeeds and replays.
        content = STATEMENT.replace(b"ONEPAY", provider.encode())
        values = dict(
            statement=Path("f06.csv"), statement_content=content,
            business_date=date(2026, 9, 22), region=Region.VN,
            tenant_id=None, provider=provider,
        )
        output = runner.execute(**values)
        assert output.summary == {"MISSING_IN_SYSTEM": 1}
        assert source.calls == 1
        replay = runner.execute(**values)
        assert replay == output
        assert source.calls == 1
        assert connection.execute(
            "SELECT count(*) FROM recon_result WHERE run_id = %s",
            (output.run_id,),
        ).fetchone()[0] == 1

        # Duplicate source rows fail inside the detail transaction; no partial rows.
        with _claim(repo, provider + "X") as claim:
            txn = ProviderTransaction(
                provider=provider + "X", region=Region.VN, tenant_id=None,
                provider_txn_id="x", provider_ref_id=None, merchant_ref=None,
                merchant_id=None, amount_minor=1, currency="VND",
                status="SUCCESS", txn_type=TxnType.PAYMENT,
                transaction_time=datetime(2026, 9, 22, tzinfo=timezone.utc),
                source_file="f06.csv", source_row_number=2,
            )
            with pytest.raises(psycopg.errors.UniqueViolation) as failure:
                repo.complete_run(
                    claim=claim, system_rows=0, provider_txns=[txn, txn],
                    results=[],
                )
            repo.fail_run(claim, failure.value)
        assert connection.execute(
            "SELECT status FROM recon_run WHERE run_id = %s", (claim.run_id,)
        ).fetchone()[0] == "FAILED"
        assert connection.execute(
            "SELECT count(*) FROM provider_txn WHERE run_id = %s",
            (claim.run_id,),
        ).fetchone()[0] == 0
        connection.commit()
        with _claim(repo, provider + "X") as retry:
            assert retry.run_id == claim.run_id
            assert retry.job_run_id == claim.job_run_id
            repo.complete_run(
                claim=retry, system_rows=0, provider_txns=[], results=[],
            )
        assert connection.execute(
            "SELECT status FROM recon_run WHERE run_id = %s", (claim.run_id,)
        ).fetchone()[0] == "COMPLETED"
