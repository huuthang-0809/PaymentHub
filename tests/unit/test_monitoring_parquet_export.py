from __future__ import annotations

import json

import duckdb

from bootstrap.export_monitoring_parquet import DATASETS, export_parquet


def test_export_writes_all_datasets_and_duckdb_reads_them(tmp_path) -> None:
    def fetch(_base_url, endpoint):
        return [{"endpoint": endpoint, "count": 1}]

    dictionary = export_parquet(tmp_path, base_url="http://test", fetch=fetch)

    assert set(dictionary["datasets"]) == set(DATASETS)
    committed = json.loads((tmp_path / "data-dictionary.json").read_text())
    assert committed == dictionary
    with duckdb.connect() as connection:
        for name in DATASETS:
            assert connection.execute(
                "SELECT count(*) FROM read_parquet(?)",
                [str(tmp_path / f"{name}.parquet")],
            ).fetchone()[0] == 1
