"""Verify DQ SQL aggregation against raw-result rule semantics on PostgreSQL."""

import os
from datetime import date, time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from reconciliation.adapters.data_quality import (
    DQ_RESULT_GROUPS_SQL, RULES, PostgreDataQualityStore,
)
from reconciliation.adapters.postgres import PostgreReconRepository
from reconciliation.domain import ReconResult, ReconResultType
from shared_kernel import Region


pytestmark = pytest.mark.skipif(
    not os.getenv("F06_TEST_DATABASE_URL"),
    reason="Set F06_TEST_DATABASE_URL to a disposable migrated PostgreSQL database",
)
BUSINESS_DATE = date(2026, 10, 9)


def complete_run(connection, classifications, business_date=BUSINESS_DATE):
    provider = f"TEST{uuid4().hex[:12].upper()}"
    repository = PostgreReconRepository(connection)
    with repository.claim_run(
        business_date=business_date, region=Region.VN, tenant_id=None,
        provider=provider, statement=Path("dq.csv"), statement_content=b"dq fixture",
        business_timezone="UTC", cutoff_time=time.max,
    ) as claim:
        rows = [
            ReconResult(
                run_id=str(claim.run_id), result_type=classification,
                region=Region.VN, tenant_id=None, provider=provider,
            ) for classification in classifications
        ]
        repository.complete_run(
            claim=claim, system_rows=len(rows), provider_rows=0,
            provider_txns=[], results=rows,
        )
    return claim.run_id


@pytest.mark.parametrize("classifications", [
    [],
    [ReconResultType.MATCHED, ReconResultType.FUZZY_MATCHED, ReconResultType.NOT_RECONCILABLE],
    list(ReconResultType) * 3,
    [ReconResultType.INVALID_DATA] * 100,
])
def test_aggregated_findings_equal_raw_rule_semantics_and_are_idempotent(classifications):
    with psycopg.connect(os.environ["F06_TEST_DATABASE_URL"]) as connection:
        run_id = complete_run(connection, classifications)
        # Neither another run on the same date nor another date may enter totals.
        complete_run(connection, [ReconResultType.INVALID_DATA] * 5)
        complete_run(connection, [ReconResultType.INVALID_DATA] * 7, date(2026, 10, 8))
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                DQ_RESULT_GROUPS_SQL,
                ([rule.result_type for rule in RULES], BUSINESS_DATE, run_id),
            )
            groups = cursor.fetchall()
        assert len(groups) == len(set(classifications))
        assert sum(group["row_count"] for group in groups) == len(classifications)
        rule_types = {rule.result_type for rule in RULES}
        assert all(group["affected_keys"] is None for group in groups
                   if group["result_type"] not in rule_types)

        store = PostgreDataQualityStore(connection)
        store.evaluate(run_id)
        store.evaluate(run_id)
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT rule_code, rule_version, severity, status, evaluated_count, "
                "affected_count, affected_result_keys FROM dq_daily "
                "WHERE run_id = %s ORDER BY rule_code", (run_id,),
            )
            actual = cursor.fetchall()
        expected = []
        for rule in RULES:
            keys = [index for index, result in enumerate(classifications, 1)
                    if result.value == rule.result_type]
            expected.append(dict(
                rule_code=rule.code, rule_version=rule.version, severity=rule.severity,
                status="FAILED" if keys else "PASSED", evaluated_count=len(classifications),
                affected_count=len(keys), affected_result_keys=keys,
            ))
        assert actual == sorted(expected, key=lambda finding: finding["rule_code"])


@pytest.mark.parametrize("status", ["MISSING", "RUNNING", "FAILED"])
def test_unfinished_or_unknown_run_does_not_create_findings(status):
    with psycopg.connect(os.environ["F06_TEST_DATABASE_URL"]) as connection:
        run_id = uuid4()
        if status != "MISSING":
            repository = PostgreReconRepository(connection)
            with repository.claim_run(
                run_id=run_id, business_date=BUSINESS_DATE, region=Region.VN,
                tenant_id=None, provider=f"TEST{uuid4().hex[:12].upper()}",
                statement=Path("unfinished.csv"), statement_content=b"unfinished",
                business_timezone="UTC", cutoff_time=time.max,
            ) as claim:
                if status == "FAILED":
                    repository.fail_run(claim, ValueError("test failure"))
        PostgreDataQualityStore(connection).evaluate(run_id)
        assert connection.execute(
            "SELECT COUNT(*) FROM dq_daily WHERE run_id = %s", (run_id,),
        ).fetchone()[0] == 0
