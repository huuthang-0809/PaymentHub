from contextlib import contextmanager

import pytest
from psycopg import OperationalError

from reconciliation.adapters.query import PooledReconQuery, PooledReconRunDeleter
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
