"""Postgres snapshot query adapter. Never returns raw provider payloads."""
from psycopg.rows import dict_row
from reconciliation.adapters.postgres import PostgreReconRepository


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

    def snapshot(self, run_id):
        with self.connection.transaction():
            # One consistent view even if a failed run is concurrently retried.
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
                        cursor, run_id, metadata['job_run_id'])
            return metadata, stored
