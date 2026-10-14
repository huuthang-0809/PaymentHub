"""Evaluate and expose simple data-quality rules for completed runs."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Mapping
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg import Error as DatabaseError
from psycopg.rows import dict_row

from reconciliation.application.ports import QueryUnavailable
from shared_kernel import Region


@dataclass(frozen=True, slots=True)
class DataQualityRule:
    code: str
    result_type: str
    severity: str
    version: int = 1


# Each rule maps one reconciliation problem to a daily quality finding.
RULES = (
    DataQualityRule("DUPLICATE_TRANSACTION", "DUPLICATE", "WARNING"),
    DataQualityRule("INVALID_DATA", "INVALID_DATA", "CRITICAL"),
    DataQualityRule("CURRENCY_MISMATCH", "CURRENCY_MISMATCH", "CRITICAL"),
    DataQualityRule("MISSING_IN_SYSTEM", "MISSING_IN_SYSTEM", "CRITICAL"),
    DataQualityRule("MISSING_IN_PROVIDER", "MISSING_IN_PROVIDER", "WARNING"),
)


DQ_RESULT_GROUPS_SQL = """
    SELECT result_type, COUNT(*) AS row_count,
           ARRAY_AGG(result_key ORDER BY result_key)
               FILTER (WHERE result_type = ANY(%s::text[])) AS affected_keys
    FROM recon_result
    WHERE business_date = %s AND run_id = %s
    GROUP BY result_type
"""


def evaluate_rules(groups: list[Mapping[str, object]]) -> list[dict]:
    """Build all rule findings from SQL counts and ordered affected-key arrays."""
    # Include healthy/non-DQ classifications in the denominator as before.
    evaluated_count = sum(int(group["row_count"]) for group in groups)
    keys_by_type = {group["result_type"]: group["affected_keys"] for group in groups}
    findings = []
    for rule in RULES:
        affected = keys_by_type.get(rule.result_type) or []
        findings.append({
            "rule_code": rule.code,
            "rule_version": rule.version,
            "severity": rule.severity,
            "status": "FAILED" if affected else "PASSED",
            "evaluated_count": evaluated_count,
            "affected_count": len(affected),
            "affected_result_keys": affected,
        })
    return findings


class PostgreDataQualityStore:
    def __init__(self, connection) -> None:
        self.connection = connection

    def evaluate(self, run_id: UUID) -> None:
        """Evaluate a completed run and upsert five idempotent rows."""
        # dict_row lets rule code access columns by stable names instead of indexes.
        with self.connection.transaction(), self.connection.cursor(
            row_factory=dict_row
        ) as cursor:
            cursor.execute(
                """
                SELECT run_id, job_run_id, business_date, region, tenant_id,
                       provider, status
                FROM recon_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None or run["status"] != "COMPLETED":
                return
            cursor.execute(
                DQ_RESULT_GROUPS_SQL,
                ([rule.result_type for rule in RULES], run["business_date"], run_id),
            )
            findings = evaluate_rules(cursor.fetchall())
            cursor.executemany(
                """
                INSERT INTO dq_daily (
                    business_date, run_id, job_run_id, region, tenant_id,
                    provider, rule_code, rule_version, severity, status,
                    evaluated_count, affected_count, affected_result_keys
                ) VALUES (
                    %(business_date)s, %(run_id)s, %(job_run_id)s,
                    %(region)s, %(tenant_id)s, %(provider)s,
                    %(rule_code)s, %(rule_version)s, %(severity)s, %(status)s,
                    %(evaluated_count)s, %(affected_count)s,
                    %(affected_result_keys)s
                )
                ON CONFLICT (business_date, run_id, rule_code, rule_version)
                DO UPDATE SET
                    severity = EXCLUDED.severity,
                    status = EXCLUDED.status,
                    evaluated_count = EXCLUDED.evaluated_count,
                    affected_count = EXCLUDED.affected_count,
                    affected_result_keys = EXCLUDED.affected_result_keys,
                    evaluated_at = now()
                """,
                [{**run, **finding} for finding in findings],
            )


class PooledDataQualityQuery:
    def __init__(self, pool) -> None:
        self.pool = pool

    def list(self, **filters) -> list[dict]:
        clauses = []
        values = []
        limit = filters.pop("limit", 100)
        # Field names are fixed here, so user input cannot become SQL syntax.
        for field in (
            "run_id", "business_date", "region", "tenant_id", "provider",
            "status", "severity", "rule_code",
        ):
            value = filters.get(field)
            if value is not None:
                clauses.append(f"{field} = %s")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            with self.pool.connection() as connection, connection.cursor(
                row_factory=dict_row
            ) as cursor:
                cursor.execute(
                    "SELECT business_date, run_id, region, tenant_id, provider, "
                    "rule_code, rule_version, severity, status, evaluated_count, "
                    "affected_count, affected_result_keys, evaluated_at "
                    "FROM dq_daily" + where
                    + " ORDER BY business_date DESC, evaluated_at DESC, rule_code "
                    "LIMIT %s",
                    [*values, limit],
                )
                return cursor.fetchall()
        except DatabaseError as error:
            raise QueryUnavailable("QUERY_UNAVAILABLE") from error


# APIRouter adds the internal endpoint to the existing FastAPI application.
router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/data-quality")
def get_data_quality(
    request: Request,
    run_id: UUID | None = None,
    business_date: date | None = None,
    region: Region | None = None,
    tenant_id: str | None = None,
    provider: str | None = None,
    status: Literal["PASSED", "FAILED"] | None = None,
    severity: Literal["WARNING", "CRITICAL"] | None = None,
    rule_code: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    try:
        items = request.app.state.data_quality.list(
            run_id=run_id,
            business_date=business_date,
            region=region.value if region else None,
            tenant_id=tenant_id,
            provider=provider.strip().upper() if provider else None,
            status=status,
            severity=severity,
            rule_code=rule_code.strip().upper() if rule_code else None,
            limit=limit,
        )
        # jsonable_encoder converts UUID, date and datetime values for JSON.
        return jsonable_encoder({
            "items": items,
            "meta": {"data_as_of": datetime.now(UTC)},
        })
    except QueryUnavailable as error:
        # HTTPException hides private database details from the API response.
        raise HTTPException(503, "QUERY_UNAVAILABLE") from error
