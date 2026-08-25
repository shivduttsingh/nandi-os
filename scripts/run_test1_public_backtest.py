from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from test1.public_backtest import run_public_test1_backtest


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated TEST 1 against the public Bhav offline sample")
    parser.add_argument("--start", type=parse_date, default=date(2026, 6, 1))
    parser.add_argument("--end", type=parse_date, default=date(2026, 6, 30))
    parser.add_argument("--output", default="test1_public_backtest_results.json")
    args = parser.parse_args()

    report = run_public_test1_backtest(args.start, args.end)
    payload = report.to_json()
    Path(args.output).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
