"""Read committed snapshots and paginate with cursors bound to query scope."""
from datetime import UTC, datetime
from .ports import ReconQuery, ReportExporter, RunNotFound, RunNotCompleted, ExportFailed

import base64
import json
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

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


def summary_view(snapshot):
    metadata, stored = snapshot
    return {
        'run': metadata,
        'system_count': stored.system_rows if stored else None,
        'provider_count': stored.provider_rows if stored else None,
        'summary': stored.summary if stored else None,
        'financial_summary': stored.financial_summary if stored else None,
        'meta': {'data_as_of': metadata['finished_at'] or metadata['started_at']},
    }


class QueryReconciliation:
    """Read use cases over committed reconciliation evidence."""

    def __init__(self, reader: "ReconQuery", exporter: "ReportExporter"):
        self.reader = reader
        self.exporter = exporter

    def analytics(self, *, from_date, to_date, region, group_by='day',
                  tenant_id=None, provider=None):
        from .analytics import build_analytics

        if from_date > to_date:
            raise ValueError('from must be on or before to')
        rows = self.reader.analytics_rows(
            from_date=from_date, to_date=to_date,
            region=region.value, tenant_id=tenant_id,
            provider=provider.strip().upper() if provider else None,
        )
        return dict(
            **build_analytics(rows, from_date, to_date, group_by),
            **{'from': from_date, 'to': to_date, 'groupBy': group_by, 'region': region.value},
            meta={'date_basis': 'business_date', 'run_policy': 'latest_completed_per_scope_date',
                  'data_as_of': datetime.now(UTC)},
        )

    def snapshot(self, run_id, require_completed=False, *, include_results=True):
        snapshot = self.reader.snapshot(run_id, include_results=include_results)
        if snapshot is None:
            raise RunNotFound("Run not found")
        if require_completed and snapshot[1] is None:
            raise RunNotCompleted("Run is not completed")
        return snapshot

    def summary(self, run_id, include_refund_checks=True):
        if include_refund_checks:
            return summary_view(self.snapshot(run_id, include_results=False))
        snapshot = self.reader.snapshot(run_id, include_results=False, include_refund_checks=False)
        if snapshot is None:
            raise RunNotFound('Run not found')
        return summary_view(snapshot)

    def refund_checks(self, run_id, *, limit=50, cursor=None):
        scope = ['refund-checks', str(run_id)]
        cursor_error = None
        try:
            after = decode_cursor(cursor, scope)
            if after is not None and (not after.isascii() or not after.isdigit()
                                      or not 1 <= int(after) <= 2_147_483_647):
                raise ValueError('Invalid cursor for this query')
            after = int(after) if after else 0
        except ValueError as error:
            cursor_error, after = error, 0
        snapshot = self.reader.refund_checks_page(
            run_id, after=after, fetch_limit=0 if cursor_error else limit + 1)
        if snapshot is None:
            raise RunNotFound('Run not found')
        metadata, rows = snapshot
        if metadata['status'] != 'COMPLETED':
            raise RunNotCompleted('Run is not completed')
        if cursor_error:
            raise cursor_error
        page = rows[:limit]
        return dict(items=page, next_cursor=encode_cursor(scope, str(page[-1]['check_key']))
                    if len(rows) > limit else None, meta={'data_as_of': metadata['finished_at']})

    def runs(self, *, run_id=None, region=None, tenant_id=None, provider=None,
             business_date=None, status=None, limit=50, cursor=None, sort='run_id'):
        filters = dict(run_id=run_id, region=region.value if region else None,
                       tenant_id=tenant_id,
                       provider=provider.strip().upper() if provider else None,
                       business_date=business_date, status=status)
        scope = ['runs', *[str(value) for value in filters.values()], sort]
        after_value = decode_cursor(cursor, scope)
        try:
            after = UUID(after_value) if after_value is not None else None
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid cursor for this query') from error
        rows = self.reader.runs(
            **filters, after=after, fetch_limit=limit + 1,
        )
        page = rows[:limit]
        next_cursor = (
            encode_cursor(scope, str(page[-1]['run_id']))
            if len(rows) > limit else None
        )
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
