from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

DATA_URL = "https://raw.githubusercontent.com/rajmaurya0904/bhav/main/sample_data/nifty_1y_1min.xlsx"
OUT_JSON = Path("profit_first_midday_reversal.json")
OUT_CSV = Path("profit_first_midday_reversal_trades.csv")


def metrics(g: pd.DataFrame) -> dict:
    gains = float(g.loc[g.pnl > 0, "pnl"].sum())
    losses = float(-g.loc[g.pnl < 0, "pnl"].sum())
    daily = g.groupby("day")["pnl"].sum()
    equity = g["pnl"].cumsum()
    drawdown = float((equity.cummax() - equity).max()) if len(g) else 0.0
    return {
        "trades": int(len(g)),
        "wins": int((g.pnl > 0).sum()),
        "losses": int((g.pnl <= 0).sum()),
        "win_rate": round(float((g.pnl > 0).mean() * 100), 2),
        "net_points": round(float(g.pnl.sum()), 2),
        "expectancy": round(float(g.pnl.mean()), 2),
        "profit_factor": round(gains / losses, 3) if losses else None,
        "trading_days": int(g.day.nunique()),
        "avg_points_per_trading_day": round(float(g.pnl.sum() / g.day.nunique()), 2),
        "profitable_days_pct": round(float((daily > 0).mean() * 100), 2),
        "max_drawdown": round(drawdown, 2),
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        workbook = Path(td) / "nifty_1y_1min.xlsx"
        urllib.request.urlretrieve(DATA_URL, workbook)
        spot = pd.read_excel(workbook, sheet_name="Spot_1min")
        opt = pd.read_excel(workbook, sheet_name="ATM_Options_1min")

    spot["dt"] = pd.to_datetime(spot.Date.astype(str) + " " + spot.Time.astype(str))
    spot = spot.sort_values("dt").reset_index(drop=True)
    spot["date"] = spot.dt.dt.date
    spot["minute"] = spot.dt.dt.hour * 60 + spot.dt.dt.minute
    spot["r1"] = spot.groupby("date")["Close"].pct_change() * 100

    opt["dt"] = pd.to_datetime(opt.Date.astype(str) + " " + opt.Time.astype(str))
    opt["date"] = opt.dt.dt.date
    opt = opt.sort_values(["date", "Strike", "Type", "dt"]).reset_index(drop=True)

    spot_lookup = spot.set_index("dt")["Close"].to_dict()
    groups = {
        key: g.sort_values("dt").reset_index(drop=True)
        for key, g in opt.groupby(["date", "Strike", "Type"], sort=False)
    }
    available: dict[pd.Timestamp, list[float]] = {}
    for (dt, strike), g in opt.groupby(["dt", "Strike"]):
        if g.Type.nunique() == 2:
            available.setdefault(dt, []).append(strike)

    records = []
    blocked_until = None
    for row in spot.itertuples(index=False):
        dt, date, minute, r1 = row.dt, row.date, row.minute, row.r1
        if dt < pd.Timestamp("2025-07-03") or dt > pd.Timestamp("2026-06-30 15:29:00"):
            continue
        # Setup: midday 1-minute spot mean reversion.
        if minute < 720 or minute > 840 or pd.isna(r1) or abs(r1) < 0.05:
            continue
        if blocked_until is not None and dt <= blocked_until:
            continue

        side = "PE" if r1 > 0 else "CE"
        entry_dt = dt + pd.Timedelta(minutes=1)
        if entry_dt not in spot_lookup or entry_dt.date() != date:
            continue
        strikes = available.get(entry_dt, [])
        if not strikes:
            continue
        entry_spot = spot_lookup[entry_dt]
        strike = min(strikes, key=lambda x: (abs(x - entry_spot), x))
        contract = groups.get((date, strike, side))
        if contract is None:
            continue
        loc = np.flatnonzero(contract.dt.to_numpy() == np.datetime64(entry_dt))
        if len(loc) == 0:
            continue
        p = int(loc[0])
        last = min(p + 29, len(contract) - 1)
        if last <= p:
            continue

        # Conservative execution convention used in the research suite.
        entry = float(contract.loc[p, "Open"]) + 0.20
        exit_dt = contract.loc[last, "dt"]
        exit_price = float(contract.loc[last, "Close"])
        pnl = exit_price - entry - 0.50
        records.append({
            "signal_dt": dt,
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "side": side,
            "strike": strike,
            "spot_r1_pct": float(r1),
            "entry": entry,
            "exit": exit_price,
            "pnl": pnl,
        })
        blocked_until = exit_dt

    trades = pd.DataFrame(records)
    trades["month"] = trades.signal_dt.dt.to_period("M").astype(str)
    trades["day"] = trades.signal_dt.dt.date
    trades.to_csv(OUT_CSV, index=False)

    monthly = []
    for month, g in trades.groupby("month"):
        row = {"month": month}
        row.update(metrics(g))
        monthly.append(row)

    dev = trades[(trades.signal_dt >= "2025-07-03") & (trades.signal_dt < "2026-01-01")]
    oos = trades[(trades.signal_dt >= "2026-01-01") & (trades.signal_dt < "2026-07-01")]
    result = {
        "name": "Profit-first midday 1m reversal",
        "status": "RESEARCH_CANDIDATE_NOT_PRODUCTION",
        "rules": {
            "signal_window": "12:00-14:00 IST",
            "spot_trigger": "absolute NIFTY 1-minute return >= 0.05%",
            "direction": "spot up -> buy ATM PE; spot down -> buy ATM CE",
            "entry": "next-minute ATM option open +0.20 points",
            "exit": "30 option bars later at close",
            "friction": "additional 0.50 option points/trade",
            "positioning": "one open trade at a time",
        },
        "development_2025_07_to_12": metrics(dev),
        "oos_2026_01_to_06": metrics(oos),
        "monthly": monthly,
        "data_limit": "Provided ATM option workbook ends 2026-06-30; July-August 2026 cannot be tested from this file.",
    }
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
