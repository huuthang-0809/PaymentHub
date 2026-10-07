from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reconciliation.domain import ProviderTransaction


@dataclass(frozen=True, slots=True)
class BusinessPeriod:
    """A business date labels the period ending at that date's cutoff."""

    business_date: date
    timezone: str
    cutoff_time: time
    start_utc: datetime
    end_utc: datetime


def resolve_business_period(
    business_date: date,
    timezone_name: str,
    cutoff_time: time,
) -> BusinessPeriod:
    if cutoff_time.tzinfo is not None:
        raise ValueError("cutoff_time must not contain a timezone")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown business timezone: {timezone_name}") from error

    start_local = datetime.combine(
        business_date - timedelta(days=1), cutoff_time, timezone # start: day D-1 -> day D
    )
    end_local = datetime.combine(business_date, cutoff_time, timezone)
    return BusinessPeriod(
        business_date=business_date,
        timezone=timezone_name,
        cutoff_time=cutoff_time,
        start_utc=start_local.astimezone(UTC),
        end_utc=end_local.astimezone(UTC),
    )


def classify_provider_rows(
    rows: list[ProviderTransaction],
    period: BusinessPeriod,
) -> tuple[list[ProviderTransaction], list[ProviderTransaction]]:
    """Return eligible rows and the complete persisted audit input."""

    eligible: list[ProviderTransaction] = []
    persisted: list[ProviderTransaction] = []
    for row in rows:
        in_period = period.start_utc <= row.transaction_time < period.end_utc
        classified = replace(row, late_arriving=row.transaction_time >= period.end_utc)
        persisted.append(classified)
        if in_period:
            eligible.append(classified)
    return eligible, persisted
