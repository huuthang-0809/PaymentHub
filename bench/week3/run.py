"""Benchmark daily partitions and run indexes without mutating app tables."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from statistics import median

import psycopg
from dotenv import load_dotenv


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
QUERY_NAMES = {
    1: "report page for one run",
    2: "seven-day report",
}


def walk_plan(node):
    yield node
    for child in node.get("Plans", []):
        yield from walk_plan(child)


def plan_evidence(plan):
    nodes = list(walk_plan(plan["Plan"]))
    relations = sorted(
        node["Relation Name"] for node in nodes if "Relation Name" in node
    )
    return {
        "planning_ms": plan["Planning Time"],
        "execution_ms": plan["Execution Time"],
        "shared_hit_blocks": sum(node.get("Shared Hit Blocks", 0) for node in nodes),
        "shared_read_blocks": sum(node.get("Shared Read Blocks", 0) for node in nodes),
        "relations_scanned": relations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "results")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")

    load_dotenv(ROOT / ".env", override=False)
    try:
        url = os.environ["DATABASE_URL"].replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
    except KeyError as error:
        raise SystemExit("DATABASE_URL is required (set it in .env or the shell)") from error

    args.output.mkdir(parents=True, exist_ok=True)
    summary = {
        "repeats": args.repeats,
        "fixture": {"days": 30, "runs": 1200, "rows_per_detail_table": 1200000},
        "queries": {},
    }

    with psycopg.connect(url) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                summary["postgres_version"] = cursor.fetchone()[0]
                cursor.execute("SHOW work_mem")
                summary["work_mem"] = cursor.fetchone()[0]
                cursor.execute((HERE / "schema.sql").read_text())
                cursor.execute((HERE / "setup.sql").read_text())

                for number, name in QUERY_NAMES.items():
                    phases = {}
                    expected_digest = None
                    expected_count = None
                    for phase in ("before", "after"):
                        query = (HERE / f"query-{number}-{phase}.sql").read_text()
                        cursor.execute(query)
                        rows = cursor.fetchall()
                        digest = hashlib.sha256(
                            json.dumps(rows, default=str).encode()
                        ).hexdigest()
                        if expected_digest is None:
                            expected_digest, expected_count = digest, len(rows)
                        assert len(rows) == expected_count and digest == expected_digest, (
                            f"Query {number}: partitioned schema changed results"
                        )

                        samples = []
                        plans = []
                        for _ in range(args.repeats):
                            cursor.execute(
                                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query
                            )
                            plan = cursor.fetchone()[0][0]
                            plans.append(plan)
                            samples.append(plan["Execution Time"])
                        cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + query)
                        output = "\n".join(row[0] for row in cursor.fetchall()) + "\n"
                        (args.output / f"query-{number}-{phase}.txt").write_text(output)

                        evidence = plan_evidence(plans[-1])
                        evidence.update(
                            rows=len(rows),
                            result_sha256=digest,
                            median_execution_ms=median(samples),
                        )
                        phases[phase] = evidence
                    summary["queries"][str(number)] = {"name": name, **phases}
        finally:
            connection.rollback()

    (args.output / "result-w3.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    lines = [
        "# Week 3 partition benchmark",
        "",
        f"- PostgreSQL: `{summary['postgres_version']}`",
        f"- `work_mem`: `{summary['work_mem']}`",
        "- Fixture: 30 daily partitions, 1,200 runs, "
        "1,200,000 rows per detail table per model",
        f"- Repeats per phase: {args.repeats}",
        "",
        "| Query | Rows | Before (ms) | After (ms) | Change | After scans | Same result |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for number, item in summary["queries"].items():
        before, after = item["before"], item["after"]
        same = (
            before["rows"] == after["rows"]
            and before["result_sha256"] == after["result_sha256"]
        )
        change = (
            (after["median_execution_ms"] / before["median_execution_ms"] - 1) * 100
            if before["median_execution_ms"]
            else 0
        )
        lines.append(
            f"| {number} — {item['name']} | {before['rows']:,} | "
            f"{before['median_execution_ms']:.3f} | "
            f"{after['median_execution_ms']:.3f} | "
            f"{change:+.1f}% | {len(after['relations_scanned'])} | "
            f"{'yes' if same else 'no'} |"
        )
    lines.extend(
        [
            "",
            "Full plans: `query-<n>-before.txt` and `query-<n>-after.txt`.",
            "Machine-readable evidence: `result-w3.json`.",
            "A negative change means the partitioned model was faster. Tiny one-run "
            "queries are dominated by index/cache effects; use the plans and date-range "
            "query as the main partition-pruning evidence.",
            "",
        ]
    )
    report = "\n".join(lines)
    (args.output / "summary.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
