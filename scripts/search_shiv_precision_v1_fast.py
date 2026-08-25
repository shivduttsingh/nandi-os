from __future__ import annotations

from itertools import product

from scripts import search_shiv_precision_v1 as base


def focused_candidate_rules(rows):
    dev = rows[rows.month.isin([1, 2])]
    val = rows[rows.month.isin([3, 4])]
    candidates = []
    # Precision family: both 15m and 3m structure must align and the completed
    # 5m signal candle must close in the directional acceptance zone. This
    # leaves 576 pre-specified combinations; May-Jun remains untouched.
    for side, gate, impulse_min, eff_min, atm_min, dom_min, oi_req, time_mode in product(
        ("CE", "PE", "BOTH"),
        ("breakout", "pullback_reclaim"),
        (4.0, 8.0),
        (0.35, 0.55),
        (0.5, 1.0),
        (65.0, 75.0),
        (False, True),
        ("ALL", "MORNING", "AFTERNOON"),
    ):
        rule = {
            "side": side,
            "gate": gate,
            "impulse_min": impulse_min,
            "efficiency_min": eff_min,
            "atm_move_min": atm_min,
            "atm_edge_min": 1.0,
            "dominance_min": dom_min,
            "structure15": True,
            "structure3": True,
            "position_required": True,
            "oi_required": oi_req,
            "volume_ratio_min": 1.0,
            "time_mode": time_mode,
        }
        dn, dw, dl, dr = base._score(base.apply_rule(dev, rule))
        vn, vw, vl, vr = base._score(base.apply_rule(val, rule))
        if dn < 10 or vn < 12:
            continue
        candidates.append({
            **rule,
            "dev_trades": dn,
            "dev_wins": dw,
            "dev_win_rate": dr,
            "validation_trades": vn,
            "validation_wins": vw,
            "validation_win_rate": vr,
            "robust_rate": min(dr, vr),
        })
    candidates.sort(
        key=lambda x: (
            x["robust_rate"],
            min(x["dev_trades"], x["validation_trades"]),
            x["validation_win_rate"],
        ),
        reverse=True,
    )
    return candidates


base.candidate_rules = focused_candidate_rules

if __name__ == "__main__":
    raise SystemExit(base.main())
