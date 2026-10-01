"""PostgreSQL committed evidence queries and connection ownership."""
from decimal import Decimal

from psycopg import Error as DatabaseError
from reconciliation.application.ports import QueryUnavailable

from psycopg.rows import dict_row
from reconciliation.adapters.postgres import PostgreReconRepository
from reconciliation.application.ports import StoredRun

RESULT_PAGE_SQL = '''
SELECT result_key, run_id::text AS run_id,
       result_type, region, tenant_id, provider, txn_type,
       system_txn_id, system_provider_txn_id, system_status,
       system_amount_minor, system_currency, refund_of, refunded_amount_minor,
       provider_txn_id, provider_amount_minor, provider_currency,
       delta_amount, source_file, source_row_number, reason
FROM recon_result
WHERE business_date = %(business_date)s
  AND run_id = %(run_id)s
  AND (%(result)s::text IS NULL OR result_type = %(result)s)
  AND (%(after)s::bigint IS NULL OR result_key > %(after)s::bigint)
ORDER BY result_key
LIMIT %(fetch_limit)s;
'''


class PostgreReconQuery:
    def __init__(self, connection):
        self.connection = connection

    def runs(self, region=None, tenant_id=None, provider=None, business_date=None, status=None):
        clauses, values = [], []
        for field, value in [('region', region), ('tenant_id', tenant_id),
                             ('provider', provider), ('business_date', business_date),
                             ('status', status)]:
            if value is not None:
                clauses.append(f'{field} = %s')
                values.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('SELECT run_id, job_run_id, business_date, region, tenant_id, '
                           'provider, source_file, file_checksum, business_timezone, '
                           'cutoff_time, status, started_at, finished_at FROM recon_run'
                           + where + ' ORDER BY run_id', values)
            return cursor.fetchall()

    def snapshot(self, run_id) -> tuple[dict, StoredRun | None]: 
        with self.connection.transaction():
            # One consistent view even if a failed run is concurrently retried.
            # Create a snapshot at the moment transaction begin. All select query in transaction will only see data in snapshot:
            self.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with self.connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute('SELECT run_id, job_run_id, business_date, region, tenant_id, '
                               'provider, source_file, file_checksum, business_timezone, '
                               'cutoff_time, status, started_at, finished_at '
                               'FROM recon_run WHERE run_id = %s', (run_id,))
                metadata = cursor.fetchone()
            if metadata is None:
                return None
            stored = None
            if metadata['status'] == 'COMPLETED':
                with self.connection.cursor() as cursor:
                    stored = PostgreReconRepository._load_completed(
                        cursor, run_id, metadata['job_run_id'],
                        metadata['business_date'],
                    )
            return metadata, stored

    def results_page(self, run_id, *, result=None, after=None, fetch_limit=51):
        with self.connection.transaction():
            self.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with self.connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    'SELECT status, finished_at, business_date '
                    'FROM recon_run WHERE run_id = %s',
                    (run_id,),
                )
                metadata = cursor.fetchone()
                if metadata is None:
                    return None
                if metadata['status'] != 'COMPLETED' or fetch_limit == 0:
                    return metadata, []
                cursor.execute(RESULT_PAGE_SQL, dict(
                    business_date=metadata['business_date'], run_id=run_id,
                    result=result, after=after, fetch_limit=fetch_limit,
                ))
                rows = [
                    {key: int(value) if isinstance(value, Decimal) else value
                     for key, value in row.items()}
                    for row in cursor.fetchall()
                ]
                return metadata, rows


class PooledReconQuery:
    """Own connection lifetime and translate driver errors at the boundary."""

    def __init__(self, pool):
        self.pool = pool

    def runs(self, **filters):
        try:
            with self.pool.connection() as connection:
                return PostgreReconQuery(connection).runs(**filters)
        except DatabaseError as error:
            raise QueryUnavailable('QUERY_UNAVAILABLE') from error

    def snapshot(self, run_id):
        try:
            with self.pool.connection() as connection:
                return PostgreReconQuery(connection).snapshot(run_id)
        except DatabaseError as error:
            raise QueryUnavailable('QUERY_UNAVAILABLE') from error

    def results_page(self, run_id, **parameters):
        try:
            with self.pool.connection() as connection:
                return PostgreReconQuery(connection).results_page(run_id, **parameters)
        except DatabaseError as error:
            raise QueryUnavailable('QUERY_UNAVAILABLE') from error
