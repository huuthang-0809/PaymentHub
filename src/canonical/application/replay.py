import random
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ReplaySource(Generic[T]):
    """Replay values with optional deterministic shuffle and injected delay."""

    transactions: tuple[T, ...]
    shuffle: bool = False
    delay_seconds: float = 0.0
    seed: int = 0
    sleeper: Callable[[float], None] = time.sleep

    def __init__(
        self,
        transactions: Iterable[T],
        *,
        shuffle: bool = False,
        delay_seconds: float = 0.0,
        seed: int = 0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        object.__setattr__(self, "transactions", tuple(transactions))
        object.__setattr__(self, "shuffle", shuffle)
        object.__setattr__(self, "delay_seconds", delay_seconds)
        object.__setattr__(self, "seed", seed)
        object.__setattr__(self, "sleeper", sleeper)

    def replay(self) -> Iterator[T]:
        rows = list(self.transactions)
        if self.shuffle:
            random.Random(self.seed).shuffle(rows)
        for row in rows:
            if self.delay_seconds:
                self.sleeper(self.delay_seconds)
            yield row
