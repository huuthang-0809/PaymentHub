"""Run against a disposable, migrated PostgreSQL database via F06_TEST_DATABASE_URL."""

import os
from dataclasses import replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from reconciliation.adapters import CsvFixtureParser, PostgreReconRepository
from reconciliation.application import RunMatching, RunInProgress
from reconciliation.application.summary import build_financial_summary
from reconciliation.domain import ProviderTransaction, ReconResult, ReconResultType
from shared_kernel import CanonicalStatus, Region, TxnType


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


def test_financial_summary_survives_postgres_replay() -> None:
    provider = f"TEST{uuid4().hex[:12].upper()}"
    with psycopg.connect(os.environ["F06_TEST_DATABASE_URL"]) as connection:
        repo = PostgreReconRepository(connection)
        with _claim(repo, provider, region=Region.INDIA) as claim:
            parent = ReconResult(
                run_id=str(claim.run_id), result_type=ReconResultType.MISSING_IN_PROVIDER,
                region=Region.INDIA, tenant_id=None, provider=provider,
                txn_type=TxnType.PAYMENT, system_txn_id="parent",
                system_provider_txn_id="pay-1", system_status=CanonicalStatus.PARTIAL_REFUNDED,
                system_amount_minor=10000, system_currency="INR", refunded_amount_minor=3000,
            )
            child = replace(
                parent, txn_type=TxnType.REFUND, system_txn_id="refund",
                system_provider_txn_id="refund-1", system_status=CanonicalStatus.SUCCESS,
                system_amount_minor=3000, refund_of="pay-1", refunded_amount_minor=None,
            )
            rows = [parent, child]
            repo.complete_run(
                claim=claim, system_rows=2, provider_rows=0,
                provider_txns=[], results=rows,
            )
        with _claim(repo, provider, region=Region.INDIA) as replay:
            assert replay.existing is not None
            actual = build_financial_summary(replay.existing.results)
            assert actual == build_financial_summary(rows)
            assert actual.totals_by_currency[0].delta_amount_minor == 13000
            assert actual.refund_parent_metrics.refunded_transaction_rate == "1.000000"
            assert actual.refund_cross_checks[0].status == "MATCHED"


def _claim(repo, provider, *, region=Region.VN, tenant_id=None):
    return repo.claim_run(
        business_date=date(2026, 9, 22), region=region,
        tenant_id=tenant_id, provider=provider, statement=Path("f06.csv"),
        statement_content=STATEMENT,
        business_timezone="UTC", cutoff_time=time.max,
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
            repo2.complete_run(
                claim=resumed, system_rows=0, provider_rows=0,
                provider_txns=[], results=[],
            )
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
                business_timezone="UTC", cutoff_time=time.max,
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
            business_timezone="UTC", cutoff_time=time.max,
        )
        output = runner.execute(**values)
        assert output.summary == {"MISSING_IN_SYSTEM": 1}
        assert source.calls == 1
        replay = runner.execute(**values)
        assert replay == replace(output, replayed=True)
        assert source.calls == 1
        assert connection.execute(
            "SELECT count(*) FROM recon_result WHERE run_id = %s",
            (output.run_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT business_date, tableoid::regclass::text "
            "FROM recon_result WHERE business_date = %s AND run_id = %s",
            (date(2026, 9, 22), output.run_id),
        ).fetchone() == (date(2026, 9, 22), "recon_result_20260922")

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
                    claim=claim, system_rows=0, provider_rows=2,
                    provider_txns=[txn, txn],
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
                claim=retry, system_rows=0, provider_rows=0,
                provider_txns=[], results=[],
            )
        assert connection.execute(
            "SELECT status FROM recon_run WHERE run_id = %s", (claim.run_id,)
        ).fetchone()[0] == "COMPLETED"


def test_query_reads_committed_snapshot_and_filters_runs():
    from reconciliation.adapters.query import PostgreReconQuery

    provider = f"TEST{uuid4().hex[:12].upper()}"
    with psycopg.connect(os.environ['F06_TEST_DATABASE_URL']) as connection:
        repo = PostgreReconRepository(connection)
        query = PostgreReconQuery(connection)
        with _claim(repo, provider) as claim:
            metadata, stored = query.snapshot(claim.run_id)
            assert metadata['status'] == 'RUNNING'
            assert stored is None
            row = ReconResult(
                run_id=str(claim.run_id), result_type=ReconResultType.MISSING_IN_PROVIDER,
                region=Region.VN, tenant_id=None, provider=provider,
                txn_type=TxnType.PAYMENT, system_txn_id='snapshot',
                system_amount_minor=123, system_currency='VND',
            )
            repo.complete_run(
                claim=claim, system_rows=1, provider_rows=0,
                provider_txns=[], results=[row],
            )
        metadata, stored = query.snapshot(claim.run_id)
        assert metadata['status'] == 'COMPLETED'
        assert metadata['finished_at'] is not None
        assert stored.results == (row,)
        page_metadata, page = query.results_page(
            claim.run_id,
            result='MISSING_IN_PROVIDER',
            after=None,
            fetch_limit=2,
        )
        assert page_metadata['status'] == 'COMPLETED'
        assert [result['result_key'] for result in page] == [1]
        _, next_page = query.results_page(
            claim.run_id,
            result='MISSING_IN_PROVIDER',
            after='00000000000000000001',
            fetch_limit=2,
        )
        assert next_page == []
        assert query.snapshot(uuid4()) is None
        runs = query.runs(provider=provider, status='COMPLETED')
        assert [run['run_id'] for run in runs] == [claim.run_id]
