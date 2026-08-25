from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts import search_shiv_precision_v1 as base
from scripts import search_shiv_precision_v1_fast as focused  # noqa: F401 - installs focused selector


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=float, choices=(5.0, 7.0), required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cache", default=".cache/shiv-precision")
    args = p.parse_args()

    base.TARGET_POINTS = float(args.target)
    # Reuse the locked Jan-Feb / Mar-Apr / May-Jun protocol and focused rule family.
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "search_shiv_precision_v1.py",
            "--start", "2026-01-01",
            "--end", "2026-06-30",
            "--output", args.output,
            "--cache", args.cache,
        ]
        code = base.main()
    finally:
        sys.argv = old_argv

    path = Path(args.output)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["strategy"] = f"SHIV PRECISION SCALP +{args.target:g}/-5 research"
    payload["benchmark"] = f"+{args.target:g} NIFTY points before -5 within 15 minutes; same 1m candle target+stop counts as LOSS"
    payload["target_points"] = args.target
    payload["stop_points"] = 5.0
    final = payload.get("may_june_final_test") or {}
    n = int(final.get("trades", 0) or 0)
    rate = float(final.get("win_rate_pct", 0.0) or 0.0)
    streak = int(final.get("max_losing_streak", 999) or 999)
    payload["accept_for_paper_live"] = bool(n >= 20 and rate >= 70.0 and streak <= 3)
    payload["target_band_met"] = bool(n >= 20 and 70.0 <= rate <= 90.0)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "strategy": payload["strategy"],
        "final": final,
        "accept_for_paper_live": payload["accept_for_paper_live"],
        "target_band_met": payload["target_band_met"],
    }, indent=2))
    return int(code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
