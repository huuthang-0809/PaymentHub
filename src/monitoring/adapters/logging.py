import json
import logging
from collections.abc import Mapping
from typing import Any


MASKED = "***MASKED***"


def mask_raw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): MASKED if str(key).lower() == "raw" else mask_raw(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_raw(item) for item in value]
    return value


def log_event(logger: logging.Logger, event: str, **context: Any) -> None:
    payload = {"event": event, **mask_raw(context)}
    logger.info(json.dumps(payload, default=str, sort_keys=True))
