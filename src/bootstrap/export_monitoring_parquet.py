"""Export the four operator-facing monitoring datasets to verified Parquet."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import duckdb


DATASETS = {
    "window_metrics": "/metrics/windows?window_size=5&limit=500",
    "provider_health": "/health/providers?limit=500",
    "open_alerts": "/alerts?status=OPEN&limit=200",
    "freshness": "/_meta/freshness?limit=500",
}


def _fetch(base_url: str, path: str) -> list[dict[str, object]]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    rows: list[dict[str, object]] = []
    while url:
        with urllib.request.urlopen(url, timeout=30) as response:
            document = json.load(response)
        rows.extend(document["data"])
        cursor = document["meta"].get("next_cursor")
        if not cursor:
            break
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query = [(key, value) for key, value in query if key != "cursor"]
        query.append(("cursor", cursor))
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), "")
        )
    return rows


def export_parquet(
    output_dir: Path,
    *,
    base_url: str,
    fetch: Callable[[str, str], list[dict[str, object]]] = _fetch,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dictionary: dict[str, object] = {"schema_version": 1, "datasets": {}}
    connection = duckdb.connect()
    try:
        for name, endpoint in DATASETS.items():
            rows = fetch(base_url, endpoint)
            parquet_path = output_dir / f"{name}.parquet"
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", encoding="utf-8", delete=False
            ) as stream:
                json_path = Path(stream.name)
                for row in rows:
                    stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            try:
                connection.execute(f'DROP TABLE IF EXISTS "{name}"')
                if rows:
                    connection.execute(
                        f'CREATE TABLE "{name}" AS SELECT * FROM read_json_auto(?)',
                        [str(json_path)],
                    )
                else:
                    connection.execute(
                        f'CREATE TABLE "{name}" (empty_dataset BOOLEAN)'
                    )
                quoted = str(parquet_path).replace("'", "''")
                connection.execute(
                    f'COPY "{name}" TO \'{quoted}\' (FORMAT PARQUET, COMPRESSION ZSTD)'
                )
                verified_rows = connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(parquet_path)]
                ).fetchone()[0]
                columns = [
                    {"name": row[0], "duckdb_type": row[1], "nullable": row[2] == "YES"}
                    for row in connection.execute(f'DESCRIBE "{name}"').fetchall()
                ]
            finally:
                json_path.unlink(missing_ok=True)
            if verified_rows != len(rows):
                raise RuntimeError(f"Parquet row count mismatch for {name}")
            dictionary["datasets"][name] = {
                "source_endpoint": endpoint,
                "file": parquet_path.name,
                "row_count": verified_rows,
                "columns": columns,
            }
    finally:
        connection.close()
    dictionary_path = output_dir / "data-dictionary.json"
    dictionary_path.write_text(
        json.dumps(dictionary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return dictionary


def main() -> None:
    output = Path(os.getenv("MONITORING_EXPORT_DIR", "tests/generated/parquet"))
    result = export_parquet(
        output,
        base_url=os.getenv("MONITORING_API_URL", "http://localhost:8000"),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
