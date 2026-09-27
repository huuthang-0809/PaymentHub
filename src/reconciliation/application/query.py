"""Read committed snapshots and paginate with cursors bound to query scope."""
import base64
import json
from dataclasses import asdict
from decimal import Decimal

from reconciliation.application.summary import build_financial_summary


def result_dict(row):
    return {key: int(value) if isinstance(value, Decimal) else value
            for key, value in asdict(row).items()}


def paginate(items, limit, cursor, scope, key):
    after = None
    if cursor:
        try:
            payload = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
            if payload['scope'] != scope or not isinstance(payload['after'], str):
                raise ValueError
            after = payload['after']
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
            raise ValueError('Invalid cursor for this query') from error
    remaining = [item for item in items if after is None or key(item) > after]
    page = remaining[:limit]
    next_cursor = None
    if len(remaining) > limit:
        next_cursor = base64.urlsafe_b64encode(json.dumps({
            'scope': scope, 'after': key(page[-1]),
        }).encode()).decode()
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
