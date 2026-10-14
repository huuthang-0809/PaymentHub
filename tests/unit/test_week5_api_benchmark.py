import json
from pathlib import Path

from tests.bench.run_w5_api import ENDPOINTS, percentile


RESULT = Path(__file__).parents[1] / "bench/result-w5.json"


def test_nearest_rank_p95() -> None:
    assert percentile([float(value) for value in range(1, 101)], 0.95) == 95.0


def test_committed_w5_api_result_proves_overlap_and_success() -> None:
    result = json.loads(RESULT.read_text())
    assert result["pass"] is True
    assert result["load"]["messages_confirmed"] > 0
    assert result["load"]["requests_while_producer_active"] > 0
    assert result["api"]["failures"] == []
    assert set(result["api"]["per_endpoint"]) == {
        endpoint.split("?", 1)[0] for endpoint in ENDPOINTS
    }
