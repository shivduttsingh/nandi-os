from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from scripts import run_shiv_aplus_public_backtest as base

_original_previous_contract_reference = base.previous_contract_reference
_reference_cache: dict[tuple[object, object, int, str], tuple[float, float] | None] = {}


def _cached_previous_contract_reference(all_opt, day, expiry, strike, side):
    key = (day, expiry, int(strike), str(side))
    if key not in _reference_cache:
        _reference_cache[key] = _original_previous_contract_reference(
            all_opt, day, expiry, int(strike), str(side)
        )
    return _reference_cache[key]


base.previous_contract_reference = _cached_previous_contract_reference

from scripts.run_shiv_aplus_hf_backtest import main as base_main
from scripts.run_shiv_mtf_matrix_hf import run as run_mtf_matrix


def _arg_value(flag: str, default: str) -> str:
    try:
        idx = sys.argv.index(flag)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return default


def main() -> int:
    rc = base_main()
    if rc != 0:
        return rc

    start = date.fromisoformat(_arg_value("--start", "2026-05-01"))
    end = date.fromisoformat(_arg_value("--end", "2026-06-30"))
    output = Path(_arg_value("--output", "shiv_aplus_hf_backtest_results.json"))
    cache = Path(_arg_value("--cache", ".cache/hf-india-options"))

    mtf_payload = run_mtf_matrix(start, end, cache)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["shiv_mtf_matrix"] = mtf_payload
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nSHIV MTF MATRIX RANKING")
    print(json.dumps(mtf_payload["ranking"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
