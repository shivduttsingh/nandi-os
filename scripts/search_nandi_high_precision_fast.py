from __future__ import annotations

from itertools import product
from scripts import search_nandi_high_precision_hf as base


def focused_select_rule(rows):
    march = rows[rows["month"] == 3]
    april = rows[rows["month"] == 4]
    candidates = []
    # Intentionally small hypothesis family to limit data-mining:
    # both Nandi engines must confirm the same side and structure must align.
    for side_mode, atm_min, win_min, oi_req, vol_req, eff_min, dom_min, gate in product(
        ("CE", "PE", "BOTH"),
        (90, 95),
        (80, 90),
        (False, True),
        (False, True),
        (0.0, 0.4),
        (70, 80),
        ("immediate", "body", "breakout"),
    ):
        def filt(df):
            mask = (
                df.atm_match
                & df.window_match
                & df.structure_aligned
                & (df.atm_score >= atm_min)
                & (df.window_score >= win_min)
                & (df.weighted_dom >= dom_min)
                & (df.gate == gate)
            )
            if side_mode != "BOTH":
                mask &= df.side == side_mode
            if oi_req:
                mask &= df.oi_supports
            if vol_req:
                mask &= df.volume_expanding
            if eff_min:
                mask &= df.trend_eff >= eff_min
            return df[mask]

        mn, mw, mr = base._score(filt(march))
        an, aw, ar = base._score(filt(april))
        if mn < 8 or an < 8:
            continue
        candidates.append({
            "side": side_mode,
            "atm_min": atm_min,
            "window_min": win_min,
            "oi_required": oi_req,
            "volume_required": vol_req,
            "structure_required": True,
            "trend_eff_min": eff_min,
            "weighted_dom_min": dom_min,
            "gate": gate,
            "march_n": mn,
            "march_win_rate": mr,
            "april_n": an,
            "april_win_rate": ar,
            "robust_rate": min(mr, ar),
            "total_dev_trades": mn + an,
        })
    candidates.sort(
        key=lambda x: (x["robust_rate"], min(x["march_n"], x["april_n"]), x["total_dev_trades"]),
        reverse=True,
    )
    return candidates


base.select_rule = focused_select_rule

if __name__ == "__main__":
    raise SystemExit(base.main())
