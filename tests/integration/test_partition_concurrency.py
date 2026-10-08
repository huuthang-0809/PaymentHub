"""Run against a disposable, migrated database via F06_TEST_DATABASE_URL."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.pq import TransactionStatus

from reconciliation.adapters import PostgreReconRepository
from reconciliation.domain import ReconResult, ReconResultType
from shared_kernel import Region


pytestmark = pytest.mark.skipif(
    not os.getenv("F06_TEST_DATABASE_URL"),
    reason="Set F06_TEST_DATABASE_URL to a disposable migrated PostgreSQL database",
)


@pytest.mark.parametrize("autocommit", [False, True])
def test_concurrent_runs_release_read_locks_before_creating_partitions(autocommit):
    url = os.environ["F06_TEST_DATABASE_URL"]
    business_date = date(2040, 1, 2 if autocommit else 1)
    ready = Barrier(2, timeout=10)

    def complete_run():
        provider = f"TEST{uuid4().hex[:12].upper()}"
        with psycopg.connect(url, autocommit=autocommit) as connection:
            with connection.transaction():
                connection.execute("SET statement_timeout = '10s'")
            repository = PostgreReconRepository(connection)
            with repository.claim_run(
                business_date=business_date,
                region=Region.VN,
                tenant_id=None,
                provider=provider,
                statement=Path("concurrent.csv"),
                statement_content=provider.encode(),
                business_timezone="UTC",
                cutoff_time=time(0),
            ) as claim:
                repository.late_provider_transactions(
                    business_date=business_date,
                    region=Region.VN,
                    tenant_id=None,
                    provider=provider,
                    start_utc=datetime(2040, 1, 1, tzinfo=timezone.utc),
                    end_utc=datetime(2040, 1, 3, tzinfo=timezone.utc),
                )
                assert connection.info.transaction_status == TransactionStatus.IDLE
                ready.wait()
                repository.complete_run(
                    claim=claim,
                    system_rows=1,
                    provider_rows=0,
                    provider_txns=[],
                    results=[ReconResult(
                        run_id=str(claim.run_id),
                        result_type=ReconResultType.MISSING_IN_PROVIDER,
                        region=Region.VN,
                        tenant_id=None,
                        provider=provider,
                        system_txn_id="system-001",
                    )],
                )
                assert connection.info.transaction_status == TransactionStatus.IDLE
                return claim.run_id

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(complete_run) for _ in range(2)]
        run_ids = [future.result(timeout=30) for future in futures]

    with psycopg.connect(url, autocommit=True) as connection:
        for run_id in run_ids:
            assert connection.execute(
                "SELECT status FROM recon_run WHERE run_id = %s", (run_id,),
            ).fetchone() == ("COMPLETED",)
            assert connection.execute(
                "SELECT count(*) FROM recon_result WHERE business_date = %s "
                "AND run_id = %s", (business_date, run_id),
            ).fetchone() == (1,)
