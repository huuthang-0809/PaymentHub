from datetime import UTC, datetime, timedelta

import pytest

from monitoring.domain import EventTiming, WatermarkPolicy


WATERMARK = datetime(2026, 9, 25, 10, 30, tzinfo=UTC)


def test_demo_watermark_policy_boundaries() -> None:
    policy = WatermarkPolicy(
        allowed_lateness=timedelta(minutes=5),
        retention=timedelta(hours=24),
    )

    assert policy.classify(WATERMARK, None) is EventTiming.ON_TIME
    assert policy.classify(WATERMARK - timedelta(minutes=5), WATERMARK) is EventTiming.ON_TIME
    assert policy.classify(WATERMARK - timedelta(minutes=5, seconds=1), WATERMARK) is EventTiming.LATE
    assert policy.classify(WATERMARK - timedelta(hours=24), WATERMARK) is EventTiming.LATE
    assert policy.classify(WATERMARK - timedelta(hours=24, seconds=1), WATERMARK) is EventTiming.EXPIRED


def test_invalid_watermark_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="retention"):
        WatermarkPolicy(timedelta(minutes=5), timedelta(minutes=5))
