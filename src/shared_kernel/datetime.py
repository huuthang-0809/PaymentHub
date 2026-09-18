from datetime import datetime, timezone

def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("Naive datetimes are strictly prohibited. Datetime must be timezone-aware")
    return dt.astimezone(timezone.utc)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso_utc(dt_str: str) -> datetime:
    dt = datetime.fromisoformat(dt_str.strip())
    return ensure_utc(dt)