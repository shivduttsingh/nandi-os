from __future__ import annotations

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

from scripts.run_shiv_aplus_hf_backtest import main


if __name__ == "__main__":
    raise SystemExit(main())
