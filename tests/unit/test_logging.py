import json
import logging

from monitoring.adapters.logging import MASKED, log_event, mask_raw


def test_mask_raw_recursively_masks_sensitive_payload():
    assert mask_raw({"raw": {"card": "secret"}, "nested": [{"RAW": "secret"}]}) == {
        "raw": MASKED,
        "nested": [{"RAW": MASKED}],
    }


def test_log_event_outputs_json_and_never_raw(caplog):
    logger = logging.getLogger("test.paymenthub")
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(logger, "mapped", run_id="r1", raw={"secret": "value"})
    payload = json.loads(caplog.messages[-1])
    assert payload == {"event": "mapped", "raw": MASKED, "run_id": "r1"}
    assert "secret" not in caplog.messages[-1]
