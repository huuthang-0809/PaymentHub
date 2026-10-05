"""Run reproducible EXPLAIN comparisons without mutating application tables."""
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

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE / 'results')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    load_dotenv(ROOT / '.env', override=False)
    url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {
        'repeats': args.repeats,
        'fixture_rows': {'runs': 1000000, 'results': 3000000},
        'queries': {},
    }
    with psycopg.connect(url) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT version()')
                summary['postgres_version'] = cur.fetchone()[0]
                cur.execute('SHOW work_mem')
                summary['work_mem'] = cur.fetchone()[0]
                cur.execute((HERE / 'schema.sql').read_text())
                cur.execute((HERE / 'setup.sql').read_text())
                for number in (1, 2):
                    query = (HERE / f'query-{number}.sql').read_text()
                    cur.execute((HERE / f'drop-index-{number}.sql').read_text())
                    phases = {}
                    evidence = None
                    for phase in ('before', 'after'):
                        if phase == 'after':
                            cur.execute((HERE / f'index-{number}.sql').read_text())
                        # Fetch once per phase: warm cache and check identical ordered results.
                        cur.execute(query)
                        rows = cur.fetchall()
                        digest = hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()
                        if evidence is None:
                            evidence = digest
                        assert digest == evidence, 'Index changed query results'
                        plans = []
                        for _ in range(args.repeats):
                            cur.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + query)
                            plans.append(cur.fetchone()[0][0])
                        cur.execute('EXPLAIN (ANALYZE, BUFFERS) ' + query)
                        (args.output / f'query-{number}-{phase}.txt').write_text('\n'.join(row[0] for row in cur.fetchall()) + '\n')
                        phases[phase] = {'rows': len(rows), 'result_sha256': digest,
                                         'median_execution_ms': median(p['Execution Time'] for p in plans)}
                    summary['queries'][str(number)] = phases
        finally:
            conn.rollback()  # Removes the entire benchmark schema, tables and indexes.
    lines = [
        '# Kết quả EXPLAIN trước/sau index',
        '',
        f"- PostgreSQL: `{summary['postgres_version']}`",
        f"- `work_mem`: `{summary['work_mem']}`",
        f"- Fixture: {summary['fixture_rows']['runs']:,} runs, "
        f"{summary['fixture_rows']['results']:,} results",
        f"- Số lần đo mỗi phase: {summary['repeats']}",
        '',
        '| Query | Dòng | Trước index (ms) | Sau index (ms) | Cùng kết quả |',
        '|---|---:|---:|---:|---|',
    ]
    for number, phases in summary['queries'].items():
        before, after = phases['before'], phases['after']
        same_result = (
            before['rows'] == after['rows']
            and before['result_sha256'] == after['result_sha256']
        )
        lines.append(
            f"| {number} | {before['rows']:,} | "
            f"{before['median_execution_ms']:.3f} | "
            f"{after['median_execution_ms']:.3f} | "
            f"{'Có' if same_result else 'Không'} |"
        )
    lines.extend([
        '',
        'Execution plan đầy đủ:',
        '',
        '- `query-1-before.txt` / `query-1-after.txt`',
        '- `query-2-before.txt` / `query-2-after.txt`',
        '',
    ])
    report = '\n'.join(lines)
    (args.output / 'summary.md').write_text(report)
    print(report)


if __name__ == '__main__':
    main()