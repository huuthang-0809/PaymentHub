import csv
import io
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pyarrow.parquet as pq

from reconciliation.adapters.http import router
from reconciliation.application.query import QueryReconciliation
from reconciliation.adapters.report import export_report
from reconciliation.application.ports import StoredRun
from reconciliation.domain import ReconResult, ReconResultType
from shared_kernel import Region, TxnType, CanonicalStatus


@pytest.fixture
def setup_api():
    run_id = uuid4()
    row = ReconResult(str(run_id), ReconResultType.MISSING_IN_PROVIDER,
                      Region.INDIA, None, 'RAZORPAY', txn_type=TxnType.REFUND,
                      system_txn_id='=unsafe', system_status=CanonicalStatus.SUCCESS,
                      system_amount_minor=123456789012345678901234567890,
                      system_currency='INR', source_file='fixture.csv')
    rows = (row, replace(row, system_txn_id='second'),
            replace(row, result_type=ReconResultType.INVALID_DATA, system_amount_minor=None))
    metadata = dict(run_id=run_id, status='COMPLETED', started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC), region='INDIA', provider='RAZORPAY',
                    tenant_id=None, business_date=date(2026, 9, 22))
    stored = StoredRun(3, 0, {'MISSING_IN_PROVIDER': 2, 'INVALID_DATA': 1}, rows)
    app = FastAPI()
    app.include_router(router)

    def query(operation, **parameters):
        if operation == 'runs':
            return [metadata] if all(value is None or metadata[key] == value
                                     for key, value in parameters.items()) else []
        if parameters['run_id'] != run_id:
            return None
        return metadata, stored if metadata['status'] == 'COMPLETED' else None

    class Reader:
        def runs(self, **filters):
            return query('runs', **filters)

        def snapshot(self, run_id):
            return query('snapshot', run_id=run_id)

    app.state.query_reconciliation = QueryReconciliation(Reader(), export_report)
    return TestClient(app), run_id, metadata


def test_summary_results_filter_and_cursor(setup_api):
    client, run_id, _ = setup_api
    base = f'/reconciliation-runs/{run_id}'
    summary = client.get(base + '/summary').json()
    assert summary['system_count'] == 3
    assert summary['meta']['data_as_of']
    assert summary['financial_summary']['refunds_by_currency'][0]['count'] == 3
    response = client.get(base + '/results', params={'limit': 1, 'result': 'MISSING_IN_PROVIDER'})
    assert response.status_code == 200
    first = response.json()
    second = client.get(base + '/results', params={
        'limit': 1, 'result': 'MISSING_IN_PROVIDER', 'cursor': first['next_cursor'],
    }).json()
    assert first['items'][0]['result_key'] == 1
    assert second['items'][0]['result_key'] == 2
    assert second['next_cursor'] is None
    assert client.get(base + '/results', params={'cursor': first['next_cursor']}).status_code == 422
    for params in [{'cursor': '!'}, {'cursor': 'W10='}, {'limit': 0}, {'limit': 201},
                   {'sort': 'raw'}, {'result': 'invalid'}]:
        assert client.get(base + '/results', params=params).status_code == 422
    assert client.get('/reconciliation-runs', params={'provider': 'razorpay'}).json()['items']
    assert client.get('/reconciliation-runs', params={'region': 'VN'}).json()['items'] == []
    assert client.get(f'/reconciliation-runs/{uuid4()}/summary').status_code == 404
    assert client.get('/reconciliation-runs/not-uuid/summary').status_code == 422


@pytest.mark.parametrize('status', ['RUNNING', 'FAILED'])
def test_unfinished_runs_only_expose_metadata(setup_api, status):
    client, run_id, metadata = setup_api
    metadata['status'] = status
    metadata['finished_at'] = None
    base = f'/reconciliation-runs/{run_id}'
    body = client.get(base + '/summary').json()
    assert body['run']['status'] == status
    assert body['financial_summary'] is None
    for endpoint in ['/results', '/report', '/report?format=parquet']:
        assert client.get(base + endpoint).status_code == 409


def test_exports_preserve_money_and_audit_evidence(setup_api, tmp_path):
    client, run_id, _ = setup_api
    base = f'/reconciliation-runs/{run_id}'
    csv_response = client.get(base + '/report')
    assert csv_response.status_code == 200
    assert csv_response.headers['x-data-as-of']
    csv_rows = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    assert csv_rows[0]['system_txn_id'] == "'=unsafe"
    assert csv_rows[0]['system_amount_minor'] == '123456789012345678901234567890'
    workbook = load_workbook(io.BytesIO(client.get(base + '/report?format=xlsx').content))
    values = list(workbook['Results'].values)
    assert values[1][values[0].index('system_txn_id')] == '=unsafe'
    assert values[1][values[0].index('system_amount_minor')] == csv_rows[0]['system_amount_minor']
    assert all(cell.data_type != 'f' for row in workbook['Results'] for cell in row)
    parquet = client.get(base + '/report?format=parquet')
    assert parquet.status_code == 200
    path = tmp_path / 'results.parquet'
    path.write_bytes(parquet.content)
    table = pq.read_table(path)
    dictionary = json.loads(table.schema.metadata[b'data_dictionary'])
    assert set(dictionary) == set(csv_rows[0])
    assert 'raw' not in dictionary
    with duckdb.connect() as connection:
        count, amount = connection.execute(
            'SELECT count(*), sum(system_amount_minor) FROM read_parquet(?)', [str(path)]
        ).fetchone()
    assert count == 3
    assert int(amount) == 2 * int(csv_rows[0]['system_amount_minor'])
    assert client.get(base + '/report?format=bad').status_code == 422


def test_query_failure_returns_stable_error(setup_api):
    from reconciliation.application.ports import QueryUnavailable
    client, run_id, _ = setup_api

    def unavailable(*args, **kwargs):
        raise QueryUnavailable('private database diagnostic')

    client.app.state.query_reconciliation.reader.snapshot = unavailable
    response = client.get(f'/reconciliation-runs/{run_id}/summary')
    assert response.status_code == 503
    assert response.json() == {'detail': 'QUERY_UNAVAILABLE'}


def test_empty_exports_have_headers_and_schema():
    from reconciliation.adapters.report import export_report, COLUMNS
    csv_content, _ = export_report([], 'csv', datetime.now(UTC))
    assert next(csv.reader(io.StringIO(csv_content.decode('utf-8-sig')))) == COLUMNS
    parquet, _ = export_report([], 'parquet', datetime.now(UTC))
    table = pq.read_table(io.BytesIO(parquet))
    assert table.num_rows == 0
    assert table.column_names == COLUMNS
