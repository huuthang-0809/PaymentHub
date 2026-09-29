import hashlib
from collections import Counter
from datetime import date, time
from pathlib import Path
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from reconciliation.domain import ProviderTransaction, ReconResult
from shared_kernel import Region


class PostgreReconRepository:
    """Persist one complete reconciliation run in a single transaction."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def save_run(
        self,
        *,
        run_id: UUID,
        business_date: date,
        region: Region,
        provider: str,
        statement: Path,
        statement_content: bytes,
        provider_txns: list[ProviderTransaction],
        results: list[ReconResult],
    ) -> UUID:
        job_run_id = uuid4()
        checksum = hashlib.sha256(statement_content).hexdigest()
        summary = Counter(result.result_type.value for result in results)

        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO job_run_log (
                    job_run_id, job_name, business_date, region, provider,
                    status, finished_at, metrics
                ) VALUES (%s, 'reconciliation', %s, %s, %s, 'COMPLETED', now(), %s)
                """,
                (
                    job_run_id,
                    business_date,
                    region.value,
                    provider,
                    Jsonb(
                        {
                            "provider_rows": len(provider_txns),
                            "results": len(results),
                            "summary": dict(summary),
                        }
                    ),
                ),
            )
            cursor.execute(
                """
                INSERT INTO recon_run (
                    run_id, job_run_id, business_date, region, provider,
                    source_file, file_checksum, business_timezone, cutoff_time,
                    status, finished_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'Asia/Ho_Chi_Minh', %s,
                    'COMPLETED', now()
                )
                """,
                (
                    run_id,
                    job_run_id,
                    business_date,
                    region.value,
                    provider,
                    str(statement),
                    checksum,
                    time.min,
                ),
            )
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
                    system_amount_minor, system_currency, provider_txn_id,
                    provider_amount_minor, provider_currency, delta_amount,
                    source_file, source_row_number, reason
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                [
                    (
                        run_id,
                        job_run_id,
                        str(index),
                        result.result_type.value,
                        result.region.value,
                        result.tenant_id,
                        result.provider,
                        result.txn_type.value if result.txn_type else None,
                        result.system_txn_id,
                        result.system_amount_minor,
                        result.system_currency,
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

        return job_run_id
