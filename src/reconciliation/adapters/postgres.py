import hashlib
import json
from collections import Counter
from contextlib import contextmanager
from datetime import date, time
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg import Connection
from psycopg.types.json import Jsonb

from reconciliation.application.ports import RunClaim, RunInProgress, StoredRun
from reconciliation.domain import ProviderTransaction, ReconResult, ReconResultType
from shared_kernel import CanonicalStatus, Region, TxnType


class PostgreReconRepository:
    """Claim a run durably, then commit its details and completion atomically."""

    def __init__(
        self,
        connection: Connection,
        *,
        business_timezone: str = "UTC",
    ) -> None:
        try:
            ZoneInfo(business_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(
                f"Unknown business timezone: {business_timezone}"
            ) from error
        self.connection = connection
        self.business_timezone = business_timezone

    @contextmanager #alway run 'finally' last to unlock key
    def claim_run(
        self,
        *,
        business_date: date,
        region: Region,
        tenant_id: str | None,
        provider: str,
        statement: Path,
        statement_content: bytes,
        run_id: UUID | None = None,
    ) -> Iterator[RunClaim]:
        """Create hash and lock key. Insert into job_run_log and recon_run with status RUNNING"""
        checksum = hashlib.sha256(statement_content).hexdigest()
        # Session lock spans the Mongo/matching phase; unlike a transaction lock,
        # it survives the durable RUNNING commit and is released on connection loss.
        lock_key = int.from_bytes(
            hashlib.sha256(
                json.dumps(
                    [business_date.isoformat(), provider, checksum],
                    separators=(",", ":"),
                ).encode()
            ).digest()[:8],
            "big", signed=True,
        )
        locked = False
        try:
            with self.connection.transaction(), self.connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
                locked = cursor.fetchone()[0]
                if not locked:
                    raise RunInProgress("Identical reconciliation input is running")
                cursor.execute(
                    """
                    SELECT r.run_id, r.job_run_id, r.status, r.region, r.tenant_id
                    FROM recon_run AS r
                    WHERE r.business_date = %s AND r.provider = %s
                      AND r.file_checksum = %s
                    """,
                    (business_date, provider, checksum),
                )
                row = cursor.fetchone()
                if row is None:
                    resolved_run_id, job_run_id = run_id or uuid4(), uuid4()
                    cursor.execute(
                        """
                        INSERT INTO job_run_log (
                            job_run_id, job_name, business_date, region,
                            tenant_id, provider, status
                        ) VALUES (%s, 'reconciliation', %s, %s, %s, %s, 'RUNNING')
                        """,
                        (job_run_id, business_date, region.value, tenant_id, provider),
                    )
                    cursor.execute(
                        """
                        INSERT INTO recon_run (
                            run_id, job_run_id, business_date, region, tenant_id,
                            provider, source_file, file_checksum, business_timezone,
                            cutoff_time, status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'RUNNING')
                        """,
                        (resolved_run_id, job_run_id, business_date, region.value,
                         tenant_id, provider, str(statement), checksum,
                         self.business_timezone, time.min),
                    )
                    claim = RunClaim(resolved_run_id, job_run_id)
                else:
                    (resolved_run_id, job_run_id, current_status,
                     stored_region, stored_tenant_id) = row
                    if stored_region != region.value or stored_tenant_id != tenant_id:
                        raise ValueError(
                            "Statement checksum already belongs to a different "
                            "region or tenant for this date and provider"
                        )
                    if current_status == "COMPLETED":
                        claim = RunClaim(
                            resolved_run_id, job_run_id,
                            self._load_completed(cursor, resolved_run_id, job_run_id),
                        )
                    else:
                        # An available lock means no other live owner. Reuse the
                        # same run after a failed process or a crashed worker.
                        cursor.execute(
                            """
                            UPDATE recon_run SET status = 'RUNNING',
                                started_at = now(), finished_at = NULL
                            WHERE run_id = %s
                            """,
                            (resolved_run_id,),
                        )
                        cursor.execute(
                            """
                            UPDATE job_run_log SET status = 'RUNNING',
                                started_at = now(), finished_at = NULL,
                                error_code = NULL, error_message = NULL
                            WHERE job_run_id = %s
                            """,
                            (job_run_id,),
                        )
                        claim = RunClaim(resolved_run_id, job_run_id)
            yield claim
        finally:
            if locked:
                with self.connection.transaction(), self.connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))

    @staticmethod
    def _load_completed(cursor, run_id: UUID, job_run_id: UUID) -> StoredRun:
        cursor.execute(
            "SELECT metrics FROM job_run_log WHERE job_run_id = %s",
            (job_run_id,),
        )
        metrics = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT result_type, region, tenant_id, provider, txn_type,
                   system_txn_id, system_provider_txn_id, system_status,
                   system_amount_minor, system_currency,
                   refund_of, refunded_amount_minor,
                   provider_txn_id, provider_amount_minor, provider_currency,
                   delta_amount, source_file, source_row_number, reason
            FROM recon_result WHERE run_id = %s ORDER BY result_key
            """,
            (run_id,),
        )
        results = tuple(
            ReconResult(
                run_id=str(run_id), result_type=ReconResultType(row[0]),
                region=Region(row[1]), tenant_id=row[2], provider=row[3],
                txn_type=TxnType(row[4]) if row[4] is not None else None,
                system_txn_id=row[5], system_provider_txn_id=row[6],
                system_status=(CanonicalStatus(row[7]) if row[7] else None),
                system_amount_minor=row[8], system_currency=row[9],
                refund_of=row[10], refunded_amount_minor=row[11],
                provider_txn_id=row[12], provider_amount_minor=row[13],
                provider_currency=row[14], delta_amount=row[15],
                source_file=row[16], source_row_number=row[17], reason=row[18],
            )
            for row in cursor.fetchall()
        )
        return StoredRun(
            # Runs created before F06 did not store system_rows in metrics.
            system_rows=metrics.get(
                "system_rows",
                sum(result.system_txn_id is not None for result in results),
            ),
            provider_rows=metrics.get("provider_rows", 0),
            summary=metrics.get("summary", {}), results=results,
        )

    def complete_run(
        self,
        *,
        claim: RunClaim,
        system_rows: int,
        provider_txns: list[ProviderTransaction],
        results: list[ReconResult],
    ) -> None:
        run_id, job_run_id = claim.run_id, claim.job_run_id
        summary = Counter(result.result_type.value for result in results)

        with self.connection.transaction(), self.connection.cursor() as cursor: #transaction: for begin a transaction and cursor: for execute SQL
            cursor.executemany(
                """
                INSERT INTO provider_txn (
                    run_id, job_run_id, region, tenant_id, provider, txn_type,
                    provider_txn_id, provider_ref_id, merchant_ref, merchant_id,
                    amount_minor, currency, status, transaction_time,
                    source_file, source_row_number, raw
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                [
                    (
                        run_id,
                        job_run_id,
                        txn.region.value,
                        txn.tenant_id,
                        txn.provider,
                        txn.txn_type.value,
                        txn.provider_txn_id,
                        txn.provider_ref_id,
                        txn.merchant_ref,
                        txn.merchant_id,
                        txn.amount_minor,
                        txn.currency,
                        txn.status,
                        txn.transaction_time,
                        txn.source_file,
                        txn.source_row_number,
                        Jsonb(txn.raw),
                    )
                    for txn in provider_txns
                ],
            )
            cursor.executemany(
                """
                INSERT INTO recon_result (
                    run_id, job_run_id, result_key, result_type, region,
                    tenant_id, provider, txn_type, system_txn_id,
                    system_provider_txn_id, system_status,
                    system_amount_minor, system_currency,
                    refund_of, refunded_amount_minor, provider_txn_id,
                    provider_amount_minor, provider_currency, delta_amount,
                    source_file, source_row_number, reason
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                [
                    (
                        run_id,
                        job_run_id,
                        index,
                        result.result_type.value,
                        result.region.value,
                        result.tenant_id,
                        result.provider,
                        result.txn_type.value if result.txn_type else None,
                        result.system_txn_id,
                        result.system_provider_txn_id,
                        result.system_status.value if result.system_status else None,
                        result.system_amount_minor,
                        result.system_currency,
                        result.refund_of,
                        result.refunded_amount_minor,
                        result.provider_txn_id,
                        result.provider_amount_minor,
                        result.provider_currency,
                        result.delta_amount,
                        result.source_file,
                        result.source_row_number,
                        result.reason,
                    )
                    for index, result in enumerate(results, start=1)
                ],
            )
            cursor.execute(
                "UPDATE recon_run SET status = 'COMPLETED', finished_at = now() "
                "WHERE run_id = %s AND status = 'RUNNING'",
                (run_id,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Run is no longer claimable")
            cursor.execute(
                """
                UPDATE job_run_log SET status = 'COMPLETED', finished_at = now(),
                    error_code = NULL, error_message = NULL, metrics = %s
                WHERE job_run_id = %s AND status = 'RUNNING'
                """,
                (Jsonb({"system_rows": system_rows,
                        "provider_rows": len(provider_txns),
                        "results": len(results), "summary": dict(summary)}),
                 job_run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Job is no longer claimable")

    def fail_run(self, claim: RunClaim, error: Exception) -> None:
        # Separate transaction: detail writes have already rolled back.
        code = "INVALID_STATEMENT" if isinstance(error, ValueError) else "PROCESSING_FAILED"
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE recon_run SET status = 'FAILED', finished_at = now() "
                "WHERE run_id = %s AND status = 'RUNNING'",
                (claim.run_id,),
            )
            cursor.execute(
                """
                UPDATE job_run_log SET status = 'FAILED', finished_at = now(),
                    error_code = %s, error_message = %s
                WHERE job_run_id = %s AND status = 'RUNNING'
                """,
                (code, str(error)[:1000] if code == "INVALID_STATEMENT"
                 else type(error).__name__, claim.job_run_id),
            )
