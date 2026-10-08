from contextlib import contextmanager

import pytest
from psycopg import OperationalError
from uuid import UUID

from reconciliation.adapters.query import (
    PostgreReconQuery,
    PooledReconQuery,
    PooledReconRunDeleter,
)
from reconciliation.application.ports import QueryUnavailable


@pytest.mark.parametrize('operation', ['runs', 'snapshot', 'results_page'])
def test_pool_errors_are_translated_to_application_errors(operation):
    class UnavailablePool:
        @contextmanager
        def connection(self):
            raise OperationalError('private connection details')
            yield

    reader = PooledReconQuery(UnavailablePool())
    with pytest.raises(QueryUnavailable, match='QUERY_UNAVAILABLE'):
        if operation == 'runs':
            reader.runs()
        else:
            getattr(reader, operation)('run-id')


def test_delete_pool_errors_are_translated_to_application_errors():
    class UnavailablePool:
        @contextmanager
        def connection(self):
            raise OperationalError('private connection details')
            yield

    with pytest.raises(QueryUnavailable, match='QUERY_UNAVAILABLE'):
        PooledReconRunDeleter(UnavailablePool())('run-id')


def test_run_cursor_and_limit_are_applied_by_postgres():
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, parameters):
            self.sql = sql
            self.parameters = parameters

        def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def cursor(self, **kwargs):
            return self.cursor_instance

    connection = Connection()
    after = UUID(int=7)

    PostgreReconQuery(connection).runs(
        provider='VNPAY', after=after, fetch_limit=51,
    )

    assert 'provider = %s AND run_id > %s' in connection.cursor_instance.sql
    assert connection.cursor_instance.sql.endswith('ORDER BY run_id LIMIT %s')
    assert connection.cursor_instance.parameters == ['VNPAY', after, 51]
