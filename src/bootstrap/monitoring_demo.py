import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from canonical.application import ReplaySource
from canonical.domain import CanonicalTransaction
from monitoring.adapters.memory import InMemoryEventUnitOfWork
from monitoring.application import ProcessCanonicalEvents
from monitoring.domain import CanonicalEvent, WindowAggregate, WindowAggregator
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType, parse_iso_utc
from shared_kernel.enums import Instrument


DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "monitoring"
    / "events.jsonl"
)


def _event(values: dict[str, Any]) -> CanonicalEvent:
    instrument = values.get("instrument")
    return CanonicalEvent(
        event_id=str(values["event_id"]),
        parse_error=bool(values.get("parse_error", False)),
        transaction=CanonicalTransaction(
            region=Region(values["region"]),
            tenant_id=values.get("tenant_id"),
            txn_id=str(values["txn_id"]),
            txn_type=TxnType(values["txn_type"]),
            instrument=Instrument(instrument) if instrument else None,
            channel_raw=values.get("channel_raw"),
            provider=str(values["provider"]),
            status=CanonicalStatus(values["status"]),
            amount_status=AmountStatus(values["amount_status"]),
            amount_minor=values.get("amount_minor"),
            currency=values.get("currency"),
            created_at=parse_iso_utc(values["created_at"]),
            updated_at=(
                parse_iso_utc(values["updated_at"])
                if values.get("updated_at") is not None
                else None
            ),
            is_terminal=bool(values["is_terminal"]),
            is_money_movement=bool(values["is_money_movement"]),
            is_reconcilable=bool(values["is_reconcilable"]),
            is_deleted=bool(values.get("is_deleted", False)),
            is_test_data=bool(values.get("is_test_data", False)),
        ),
    )


def load_fixture(path: Path) -> tuple[CanonicalEvent, ...]:
    return tuple(
        _event(json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    )


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _aggregate_dict(aggregate: WindowAggregate) -> dict[str, Any]:
    key = aggregate.key
    return {
        "window_size_minutes": key.window.size_minutes,
        "window_start": _iso(key.window.start),
        "window_end": _iso(key.window.end),
        "region": key.region.value,
        "tenant_id": key.tenant_scope or None,
        "provider": key.provider,
        "txn_type": key.txn_type.value,
        "instrument": key.instrument or None,
        "channel_raw": key.channel_raw or None,
        "currency": key.currency or None,
        "total_event_count": aggregate.total_event_count,
        "operation_event_count": aggregate.operation_event_count,
        "success_count": aggregate.success_count,
        "failed_count": aggregate.failed_count,
        "pending_count": aggregate.pending_count,
        "unknown_count": aggregate.unknown_count,
        "success_rate": (
            str(aggregate.success_rate) if aggregate.success_rate is not None else None
        ),
        "failed_rate": (
            str(aggregate.failed_rate) if aggregate.failed_rate is not None else None
        ),
        "unknown_rate": (
            str(aggregate.unknown_rate) if aggregate.unknown_rate is not None else None
        ),
        "monetary_count": aggregate.monetary_count,
        "monetary_amount_minor": aggregate.monetary_amount_minor,
        "invalid_amount_count": aggregate.invalid_amount_count,
        "missing_currency_count": aggregate.missing_currency_count,
        "parse_error_count": aggregate.parse_error_count,
        "late_event_count": aggregate.late_event_count,
        "non_terminal_count": aggregate.non_terminal_count,
        "pending_age_lt_5m_count": aggregate.pending_age_lt_5m_count,
        "pending_age_5_to_15m_count": aggregate.pending_age_5_to_15m_count,
        "pending_age_15_to_60m_count": aggregate.pending_age_15_to_60m_count,
        "pending_age_gte_60m_count": aggregate.pending_age_gte_60m_count,
        "oldest_pending_age_seconds": aggregate.oldest_pending_age_seconds,
        "freshness_lag_seconds": aggregate.freshness_lag_seconds,
        "last_event_at": _iso(aggregate.last_event_at),
        "computed_at": _iso(aggregate.computed_at),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay the monitoring golden fixture")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--window-size", type=int, default=5, choices=(1, 5, 15))
    parser.add_argument("--replays", type=int, default=2)
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    parser.add_argument("--computed-at", default="2026-09-25T11:00:00Z")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.replays <= 0:
        raise SystemExit("--replays must be positive")

    events = load_fixture(args.fixture)
    unit_of_work = InMemoryEventUnitOfWork()
    processor = ProcessCanonicalEvents(
        unit_of_work,
        WindowAggregator((args.window_size,)),
    )
    computed_at = parse_iso_utc(args.computed_at)
    runs = []
    output = None
    for replay_number in range(1, args.replays + 1):
        source = ReplaySource(
            events,
            shuffle=args.shuffle,
            delay_seconds=args.delay_seconds,
            seed=args.seed,
        )
        output = processor.execute(source.replay(), computed_at=computed_at)
        runs.append({"replay": replay_number, **asdict(output.summary)})

    assert output is not None
    document = {
        "fixture": str(args.fixture),
        "runs": runs,
        "aggregates": [_aggregate_dict(row) for row in output.aggregates],
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
