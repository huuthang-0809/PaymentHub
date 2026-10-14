"""Combine committed run summaries by business date without loading evidence."""
from datetime import date, timedelta
from reconciliation.domain import ReconResultType


def period_start(day, group_by):
    if group_by == 'week':
        return day - timedelta(days=day.weekday())
    if group_by == 'month':
        return day.replace(day=1)
    return day


class _Totals:
    def __init__(self):
        self.counts = dict(run_count=0, system_count=0, provider_count=0,
                           financial_missing_run_count=0, unallocated_currency_count=0)
        self.summary = {result.value: 0 for result in ReconResultType}
        self.currencies = {}

    def add(self, row):
        self.counts['run_count'] += 1
        for key in ('system_count', 'provider_count', 'unallocated_currency_count'):
            self.counts[key] += row.get(key) or 0
        for key, count in (row.get('summary') or {}).items():
            self.summary[key] += count
        if row.get('currency_totals') is None:
            self.counts['financial_missing_run_count'] += 1
        for currency in row.get('currency_totals') or []:
            target = self.currencies.setdefault(currency['currency'], dict(
                currency=currency['currency'], system_amount_minor=0,
                provider_amount_minor=0, system_amount_count=0, provider_amount_count=0,
                system_missing_amount_count=0, provider_missing_amount_count=0,
            ))
            for key in target.keys() - {'currency'}:
                target[key] += int(currency.get(key) or 0)

    def view(self):
        currencies = []
        for key in sorted(self.currencies):
            item = dict(self.currencies[key])
            incomplete = any((self.counts['financial_missing_run_count'],
                              self.counts['unallocated_currency_count'],
                              item['system_missing_amount_count'],
                              item['provider_missing_amount_count']))
            delta = None if incomplete else item['system_amount_minor'] - item['provider_amount_minor']
            for side in ('system', 'provider'):
                amount = item[f'{side}_amount_minor']
                item[f'{side}_amount_minor'] = str(amount) if item[f'{side}_amount_count'] else None
            item['delta_amount_minor'] = str(delta) if delta is not None else None
            currencies.append(item)
        return {**self.counts, **self.summary, 'totals_by_currency': currencies}


def build_analytics(rows, from_date: date, to_date: date, group_by: str):
    if from_date > to_date:
        raise ValueError('from must be on or before to')
    if group_by not in {'day', 'week', 'month', 'none'}:
        raise ValueError('Invalid groupBy')
    totals, groups, daily_runs = _Totals(), {}, []
    # Include empty business periods so charts do not silently skip missing days.
    day = from_date
    while day <= to_date:
        start = from_date if group_by == 'none' else period_start(day, group_by)
        groups.setdefault(start, _Totals())
        if day == to_date:
            break
        day += timedelta(days=1)
    for row in rows:
        start = from_date if group_by == 'none' else period_start(row['business_date'], group_by)
        groups[start].add(row)
        totals.add(row)
        summary = {result.value: (row.get('summary') or {}).get(result.value, 0)
                   for result in ReconResultType}
        daily_runs.append(dict(
            business_date=row['business_date'].isoformat(), run_id=str(row['run_id']),
            region=row['region'], tenant_id=row['tenant_id'], provider=row['provider'],
            system_count=row.get('system_count') or 0,
            provider_count=row.get('provider_count') or 0,
            result_count=sum(summary.values()), **summary,
        ))
    buckets = [dict(period_start=start.isoformat(), **group.view())
               for start, group in sorted(groups.items())]
    return dict(totals=totals.view(), buckets=buckets, daily_runs=daily_runs,
                outcomes=[dict(result=result, count=count)
                          for result, count in totals.summary.items()],
                financial_buckets=[dict(period_start=bucket['period_start'], **currency)
                                   for bucket in buckets for currency in bucket['totals_by_currency']])