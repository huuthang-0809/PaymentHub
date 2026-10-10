from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from shared_kernel import ensure_utc


SUPPORTED_WINDOW_MINUTES = frozenset({1, 5, 15})


@dataclass(frozen=True, slots=True)
class Window:
    size_minutes: int
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.size_minutes not in SUPPORTED_WINDOW_MINUTES:
            raise ValueError("window size must be one of 1, 5 or 15 minutes")
        start = ensure_utc(self.start)
        end = ensure_utc(self.end)
        if end != start + timedelta(minutes=self.size_minutes):
            raise ValueError("window end must equal start plus window size")
        if start.second or start.microsecond or start.minute % self.size_minutes:
            raise ValueError("window start is not aligned to its size")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @classmethod
    def containing(cls, event_time: datetime, size_minutes: int = 5) -> "Window":
        if size_minutes not in SUPPORTED_WINDOW_MINUTES:
            raise ValueError("window size must be one of 1, 5 or 15 minutes")
        event_time = ensure_utc(event_time)
        start = event_time.replace(
            minute=(event_time.minute // size_minutes) * size_minutes,
            second=0,
            microsecond=0,
            tzinfo=UTC,
        )
        return cls(
            size_minutes=size_minutes,
            start=start,
            end=start + timedelta(minutes=size_minutes),
        )

    def contains(self, event_time: datetime) -> bool:
        event_time = ensure_utc(event_time)
        return self.start <= event_time < self.end
