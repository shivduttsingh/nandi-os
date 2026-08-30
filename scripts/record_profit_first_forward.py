from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from nandi_v2.profit_first import UpstoxProfitFirstHistory
from nandi_v2.profit_first_reporting import (
    forward_run_row,
    forward_trade_rows,
    merge_forward_ledger,
    merge_forward_runs,
    read_forward_ledger,
    read_forward_runs,
    save_forward_data,
)

IST = ZoneInfo("Asia/Kolkata")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record one frozen PROFIT FIRST forward-paper day from read-only Upstox data."
    )
    parser.add_argument(
        "--date",
        dest="test_date",
        default=os.getenv("PROFIT_FIRST_DATE", "").strip(),
        help="Trading date in YYYY-MM-DD. Defaults to the current India date.",
    )
    return parser.parse_args()


def resolve_date(raw: str) -> date:
    return date.fromisoformat(raw) if raw else datetime.now(IST).date()


def main() -> int:
    args = parse_args()
    test_date = resolve_date(args.test_date)
    if test_date.weekday() >= 5:
        print(json.dumps({"status": "SKIPPED_WEEKEND", "date": test_date.isoformat()}))
        return 0

    token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "UPSTOX_ACCESS_TOKEN is required. Add the read-only token as a GitHub Actions secret."
        )

    history = UpstoxProfitFirstHistory(token)
    summary, trades, _, _ = history.run_backtest(test_date, test_date)
    recorded_at = datetime.now(IST)

    existing_ledger = read_forward_ledger()
    existing_runs = read_forward_runs()
    incoming_trades = forward_trade_rows(
        trades,
        test_date=test_date,
        recorded_at=recorded_at,
    )
    incoming_run = forward_run_row(
        summary,
        test_date=test_date,
        status="OK",
        recorded_at=recorded_at,
    )

    ledger = merge_forward_ledger(existing_ledger, incoming_trades)
    runs = merge_forward_runs(existing_runs, incoming_run)
    save_forward_data(ledger, runs)

    print(
        json.dumps(
            {
                "status": "OK",
                "date": test_date.isoformat(),
                "trades": summary["trades"],
                "wins": summary["wins"],
                "losses": summary["losses"],
                "win_rate": summary["win_rate"],
                "net_points": summary["net_points"],
                "profit_factor": summary["profit_factor"],
                "ledger_rows": int(len(ledger)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
