from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import pandas as pd

REQUIRED = {"timestamp", "open", "high", "low", "close", "volume"}

@dataclass
class Signal:
    strategy: str
    side: str
    timestamp: str
    entry_spot: float
    stop_spot: float
    target_spot: float
    reason: str
    score: int

def normalise_bars(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError("Missing columns: " + ", ".join(sorted(missing)))
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=list(REQUIRED)).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if df.empty:
        raise ValueError("No valid OHLCV rows were found.")
    invalid_price = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    invalid_range = (
        (df["high"] < df["low"])
        | (df["high"] < df[["open", "close"]].max(axis=1))
        | (df["low"] > df[["open", "close"]].min(axis=1))
    )
    invalid_volume = df["volume"] < 0
    invalid = invalid_price | invalid_range | invalid_volume
    if invalid.any():
        raise ValueError(
            f"Invalid OHLCV rows: {int(invalid.sum())} row(s) have non-positive prices, "
            "inconsistent high/low values, or negative volume."
        )
    if "vwap" in df.columns:
        df["vwap"] = pd.to_numeric(df["vwap"], errors="coerce")
    else:
        df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3
    df["volume_baseline"] = df.groupby(df["timestamp"].dt.date)["volume"].transform(lambda s: s.shift(1).rolling(10, min_periods=3).median())
    return df

def _opening_ranges(df: pd.DataFrame) -> dict:
    ranges = {}
    for day, g in df.groupby(df["timestamp"].dt.date):
        first = g[(g["timestamp"].dt.hour == 9) & (g["timestamp"].dt.minute >= 15) & (g["timestamp"].dt.minute < 30)]
        if len(first) >= 2:
            ranges[day] = (float(first["high"].max()), float(first["low"].min()))
    return ranges

def generate_signals(frame: pd.DataFrame, max_hold_bars: int = 9) -> list[Signal]:
    df = normalise_bars(frame)
    ranges = _opening_ranges(df)
    signals: list[Signal] = []
    for day, g in df.groupby(df["timestamp"].dt.date):
        if day not in ranges:
            continue
        hi, lo = ranges[day]
        idxs = list(g.index)
        post = [i for i in idxs if df.loc[i, "timestamp"].hour > 9 or (df.loc[i, "timestamp"].hour == 9 and df.loc[i, "timestamp"].minute >= 30)]
        if len(post) < 3:
            continue
        a_emitted = False
        b_emitted = False
        for pos, i in enumerate(post):
            row = df.loc[i]
            if not a_emitted and pos >= 1:
                prev = df.loc[post[pos-1]]
                long_retest = row["low"] <= hi * 1.0015 and row["close"] > hi and row["close"] >= row["open"]
                short_retest = row["high"] >= lo * 0.9985 and row["close"] < lo and row["close"] <= row["open"]
                vol_ok = pd.notna(row["volume_baseline"]) and row["volume"] >= max(1.0, float(row["volume_baseline"]))
                vwap_ok_long = row["close"] >= row["vwap"]
                vwap_ok_short = row["close"] <= row["vwap"]
                if long_retest and vol_ok and vwap_ok_long and prev["close"] > hi:
                    risk = max(float(row["close"] - min(row["low"], hi - 1)), 8.0)
                    signals.append(Signal("A", "CE", str(row["timestamp"]), float(row["close"]), float(row["close"]-risk), float(row["close"]+2*risk), "Opening-range breakout, retest held, volume and VWAP aligned.", 78))
                    a_emitted = True
                elif short_retest and vol_ok and vwap_ok_short and prev["close"] < lo:
                    risk = max(float(max(row["high"], lo + 1) - row["close"]), 8.0)
                    signals.append(Signal("A", "PE", str(row["timestamp"]), float(row["close"]), float(row["close"]+risk), float(row["close"]-2*risk), "Opening-range breakdown, retest rejected, volume and VWAP aligned.", 78))
                    a_emitted = True
            if not b_emitted and pos >= 2:
                prev = df.loc[post[pos-1]]
                prior = df.loc[post[pos-2]]
                upper_fail = prev["high"] > hi and prev["close"] < hi and row["close"] < min(prev["low"], prior["low"])
                lower_fail = prev["low"] < lo and prev["close"] > lo and row["close"] > max(prev["high"], prior["high"])
                if upper_fail:
                    risk = max(float(prev["high"] - row["close"]), 8.0)
                    signals.append(Signal("B", "PE", str(row["timestamp"]), float(row["close"]), float(prev["high"]+1), float(row["close"]-2*risk), "Failed upside breakout; close returned inside range and downside structure broke.", 82))
                    b_emitted = True
                elif lower_fail:
                    risk = max(float(row["close"] - prev["low"]), 8.0)
                    signals.append(Signal("B", "CE", str(row["timestamp"]), float(row["close"]), float(prev["low"]-1), float(row["close"]+2*risk), "Failed downside breakdown; close reclaimed range and upside structure broke.", 82))
                    b_emitted = True
            if a_emitted and b_emitted:
                break
    return sorted(signals, key=lambda x: x.timestamp)

def backtest(frame: pd.DataFrame, strategy: str = "C", risk_reward: float = 2.0, cost_points: float = 1.0, max_hold_bars: int = 9) -> pd.DataFrame:
    if strategy not in {"A", "B", "C"}:
        raise ValueError("strategy must be one of: A, B, C")
    if risk_reward <= 0:
        raise ValueError("risk_reward must be greater than zero")
    if cost_points < 0:
        raise ValueError("cost_points cannot be negative")
    if max_hold_bars < 1:
        raise ValueError("max_hold_bars must be at least one")
    df = normalise_bars(frame)
    sigs = generate_signals(df, max_hold_bars=max_hold_bars)
    chosen = []
    if strategy == "C":
        by_time: dict[str, list[Signal]] = {}
        for sig in sigs:
            by_time.setdefault(sig.timestamp, []).append(sig)
        for same_bar in by_time.values():
            if len({x.side for x in same_bar}) == 1:
                chosen.append(max(same_bar, key=lambda x: x.score))
    else:
        chosen = [s for s in sigs if s.strategy == strategy]
    trades = []
    last_exit = -999
    for s in chosen:
        entry_idx = df.index[df["timestamp"].astype(str) == s.timestamp]
        if len(entry_idx) == 0:
            continue
        i = int(entry_idx[0])
        if i <= last_exit:
            continue
        risk = abs(s.entry_spot - s.stop_spot)
        if risk <= 0:
            continue
        # Intraday signals must never be carried into the next session by a
        # historical replay. If the signal is the final bar of the day there
        # is no observable forward candle, so it cannot be backtested.
        session_day = df.loc[i, "timestamp"].date()
        session_indices = df.index[df["timestamp"].dt.date == session_day]
        session_last = int(session_indices[-1])
        if i >= session_last:
            continue
        exit_idx = min(i + max_hold_bars, session_last)
        target = s.entry_spot + risk * risk_reward if s.side == "CE" else s.entry_spot - risk * risk_reward
        exit_price = float(df.iloc[exit_idx]["close"])
        exit_time = df.iloc[exit_idx]["timestamp"]
        outcome = "TIME"
        for j in range(i+1, exit_idx+1):
            bar = df.iloc[j]
            if s.side == "CE":
                if bar["low"] <= s.stop_spot:
                    exit_price, exit_time, outcome = s.stop_spot, bar["timestamp"], "STOP"
                    break
                if bar["high"] >= target:
                    exit_price, exit_time, outcome = target, bar["timestamp"], "TARGET"
                    break
            else:
                if bar["high"] >= s.stop_spot:
                    exit_price, exit_time, outcome = s.stop_spot, bar["timestamp"], "STOP"
                    break
                if bar["low"] <= target:
                    exit_price, exit_time, outcome = target, bar["timestamp"], "TARGET"
                    break
        gross = (exit_price - s.entry_spot) if s.side == "CE" else (s.entry_spot - exit_price)
        net = gross - float(cost_points)
        trades.append({"strategy":s.strategy,"side":s.side,"entry_time":s.timestamp,"exit_time":str(exit_time),"entry_spot":s.entry_spot,"exit_spot":float(exit_price),"stop_spot":s.stop_spot,"target_spot":float(target),"exit_reason":outcome,"gross_points":round(gross,2),"net_points":round(net,2),"result":"WIN" if net>0 else "LOSS","reason":s.reason})
        last_exit = int(df.index[df["timestamp"] == exit_time][-1])
    return pd.DataFrame(trades)

def metrics(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {"trades":0,"win_rate":0.0,"net_points":0.0,"profit_factor":0.0,"max_drawdown":0.0,"expectancy":0.0}
    wins = trades.loc[trades.net_points > 0, "net_points"]
    losses = trades.loc[trades.net_points <= 0, "net_points"]
    gross_loss = abs(float(losses.sum()))
    equity = trades["net_points"].cumsum()
    dd = equity - equity.cummax().clip(lower=0)
    return {"trades":len(trades),"win_rate":round(100*len(wins)/len(trades),1),"net_points":round(float(trades.net_points.sum()),2),"profit_factor":round(float(wins.sum())/gross_loss,2) if gross_loss else float("inf"),"max_drawdown":round(float(dd.min()),2),"expectancy":round(float(trades.net_points.mean()),2)}
