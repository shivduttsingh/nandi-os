from datetime import date

import pandas as pd

from nandi_v2.profit_first import RULES, _replay_frames, choose_atm_strike, signal_side


def test_signal_side_is_mean_reversion():
    assert signal_side(0.05) == "PE"
    assert signal_side(0.08) == "PE"
    assert signal_side(-0.05) == "CE"
    assert signal_side(-0.08) == "CE"
    assert signal_side(0.049) is None


def test_choose_atm_strike_is_deterministic():
    assert choose_atm_strike([24950.0, 25000.0, 25050.0], 25020.0) == 25000.0
    assert choose_atm_strike([24950.0, 25000.0], 24975.0) == 24950.0


def test_replay_uses_signal_close_for_atm_not_next_minute_close():
    spot = pd.DataFrame(
        [
            {"dt": "2026-01-02 11:59:00", "Open": 24990.0, "High": 24990.0, "Low": 24990.0, "Close": 24990.0},
            {"dt": "2026-01-02 12:00:00", "Open": 24990.0, "High": 25010.0, "Low": 24990.0, "Close": 25005.0},
            {"dt": "2026-01-02 12:01:00", "Open": 25100.0, "High": 25110.0, "Low": 25090.0, "Close": 25105.0},
        ]
    )
    rows = []
    for strike in (25000.0, 25100.0):
        for side in ("CE", "PE"):
            for minute in range(1, 31):
                ts = pd.Timestamp("2026-01-02 12:00:00") + pd.Timedelta(minutes=minute)
                price = 100.0 + minute if strike == 25000.0 else 200.0 + minute
                rows.append(
                    {
                        "dt": ts,
                        "date": ts.date(),
                        "Strike": strike,
                        "Type": side,
                        "Open": price,
                        "High": price,
                        "Low": price,
                        "Close": price,
                    }
                )
    options = pd.DataFrame(rows)

    summary, trades, _, _ = _replay_frames(
        spot,
        options,
        date(2026, 1, 2),
        date(2026, 1, 2),
        RULES,
    )
    assert summary["trades"] == 1
    assert float(trades.iloc[0]["strike"]) == 25000.0
