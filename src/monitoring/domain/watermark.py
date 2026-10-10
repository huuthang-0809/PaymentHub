from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from shared_kernel import ensure_utc


class EventTiming(str, Enum):
    ON_TIME = "ON_TIME"
    LATE = "LATE"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class WatermarkPolicy:
    """Demo event-time policy; production thresholds require separate approval."""

    allowed_lateness: timedelta = timedelta(minutes=5)
    retention: timedelta = timedelta(hours=24)

    def __post_init__(self) -> None:
        if self.allowed_lateness < timedelta(0):
            raise ValueError("allowed_lateness cannot be negative")
        if self.retention <= self.allowed_lateness:
            raise ValueError("retention must be greater than allowed_lateness")

    def classify(
        self, event_time: datetime, watermark: datetime | None
    ) -> EventTiming:
        event_time = ensure_utc(event_time)
        if watermark is None:
            return EventTiming.ON_TIME
        watermark = ensure_utc(watermark)
        if event_time < watermark - self.retention:
            return EventTiming.EXPIRED
        if event_time < watermark - self.allowed_lateness:
            return EventTiming.LATE
        return EventTiming.ON_TIME
