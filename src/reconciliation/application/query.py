"""Read committed snapshots and paginate with cursors bound to query scope."""
from datetime import UTC, datetime
from .ports import ReconQuery, ReportExporter, RunNotFound, RunNotCompleted, ExportFailed

import base64
import json
from dataclasses import asdict
from decimal import Decimal

from reconciliation.application.summary import build_financial_summary


def result_dict(row):
    return {key: int(value) if isinstance(value, Decimal) else value
            for key, value in asdict(row).items()}


def decode_cursor(cursor, scope):
    if not cursor:
        return None
    try:
        payload = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
        if payload['scope'] != scope or not isinstance(payload['after'], str):
            raise ValueError
        return payload['after']
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
        raise ValueError('Invalid cursor for this query') from error


def encode_cursor(scope, after):
    return base64.urlsafe_b64encode(json.dumps({
        'scope': scope, 'after': after,
    }).encode()).decode()


def paginate(items, limit, cursor, scope, key):
    after = decode_cursor(cursor, scope)
    remaining = [item for item in items if after is None or key(item) > after]
    page = remaining[:limit]
    next_cursor = None
    if len(remaining) > limit:
        next_cursor = encode_cursor(scope, key(page[-1]))
    return page, next_cursor


def summary_view(snapshot):
    metadata, stored = snapshot
    return {
        'run': metadata,
        'system_count': stored.system_rows if stored else None,
        'provider_count': stored.provider_rows if stored else None,
        'summary': stored.summary if stored else None,
        'financial_summary': build_financial_summary(stored.results) if stored else None,
        'meta': {'data_as_of': metadata['finished_at'] or metadata['started_at']},
    }


class QueryReconciliation:
    """Read use cases over committed reconciliation evidence."""

    def __init__(self, reader: "ReconQuery", exporter: "ReportExporter"):
        self.reader = reader
        self.exporter = exporter

    def snapshot(self, run_id, require_completed=False):
        snapshot = self.reader.snapshot(run_id)
        if snapshot is None:
            raise RunNotFound("Run not found")
        if require_completed and snapshot[1] is None:
            raise RunNotCompleted("Run is not completed")
        return snapshot

    def summary(self, run_id):
        return summary_view(self.snapshot(run_id))

    def runs(self, *, run_id=None, region=None, tenant_id=None, provider=None,
             business_date=None, status=None, limit=50, cursor=None, sort='run_id'):
        filters = dict(run_id=run_id, region=region.value if region else None,
                       tenant_id=tenant_id,
                       provider=provider.strip().upper() if provider else None,
                       business_date=business_date, status=status)
        rows = self.reader.runs(**filters)
        scope = ['runs', *[str(value) for value in filters.values()], sort]
        page, next_cursor = paginate(rows, limit, cursor, scope, lambda row: str(row['run_id']))
        return dict(items=page, next_cursor=next_cursor, meta={'data_as_of': datetime.now(UTC)})

    def results(self, run_id, *, result=None, limit=50, cursor=None, sort='result_key'):
        scope = [str(run_id), result.value if result else None, sort]
        # Keep run-not-found/not-completed precedence over malformed cursor errors.
        cursor_error = None
        try:
            after = decode_cursor(cursor, scope)
            if after is not None and (
                len(after) != 20 or not after.isascii() or not after.isdigit()
                or not 1 <= int(after) <= 2_147_483_647 #max value of Postgresql integer
            ):
                raise ValueError('Invalid cursor for this query')
        except ValueError as error:
            cursor_error = error
            after = None
        snapshot = self.reader.results_page(
            run_id, result=result.value if result else None, after=after,
            fetch_limit=0 if cursor_error else limit + 1,
        )
        if snapshot is None:
            raise RunNotFound("Run not found")
        metadata, rows = snapshot
        if metadata['status'] != 'COMPLETED':
            raise RunNotCompleted("Run is not completed")
        if cursor_error:
            raise cursor_error
        page = rows[:limit]
        next_cursor = (
            encode_cursor(scope, f"{page[-1]['result_key']:020d}")
            if len(rows) > limit else None
        )
        return dict(items=page, next_cursor=next_cursor, meta={'data_as_of': metadata['finished_at']})

    def report(self, run_id, format):
        metadata, stored = self.snapshot(run_id, True)
        try:
            content, media_type = self.exporter(stored.results, format, metadata['finished_at'])
        except (ValueError, OverflowError, OSError) as error:
            raise ExportFailed('EXPORT_FAILED') from error
        return content, media_type, metadata['finished_at']
