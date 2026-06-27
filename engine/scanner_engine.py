from __future__ import annotations

from datetime import datetime
from typing import Dict, List

import pandas as pd

from engine.market_data_engine import (
    download_daily_data_for_symbols,
    get_nse_stock_universe,
)


RSI_LENGTH = 14
RSI_SOURCE = "Close"
RSI_METHOD = "TradingView-style Wilder/RMA RSI"


def calculate_rma(series: pd.Series, period: int = 14) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    rma = pd.Series(index=values.index, dtype="float64")

    seed = values.rolling(window=period, min_periods=period).mean()
    first_valid_index = seed.first_valid_index()

    if first_valid_index is None:
        return rma

    first_position = values.index.get_loc(first_valid_index)
    rma.iloc[first_position] = seed.iloc[first_position]

    for i in range(first_position + 1, len(values)):
        current_value = values.iloc[i]
        previous_rma = rma.iloc[i - 1]

        if pd.isna(current_value):
            rma.iloc[i] = previous_rma
        else:
            rma.iloc[i] = ((previous_rma * (period - 1)) + current_value) / period

    return rma


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    close = pd.to_numeric(close, errors="coerce")
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = calculate_rma(gain, period)
    avg_loss = calculate_rma(loss, period)

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50)

    return rsi


def analyse_stock_with_rsi_strategy(
    symbol: str,
    name: str,
    df: pd.DataFrame,
) -> Dict:
    data = df.copy()

    data["Close"] = pd.to_numeric(data["Close"], errors="coerce")
    data["Volume"] = pd.to_numeric(data["Volume"], errors="coerce")

    data = data.dropna(subset=["Close"])

    if len(data) < 80:
        return {
            "symbol": symbol,
            "name": name,
            "score": 0,
            "action": "SKIP",
            "reason": "Not enough candle data.",
        }

    data["RSI"] = calculate_rsi(data["Close"], RSI_LENGTH)
    data["SMA20"] = data["Close"].rolling(20).mean()
    data["SMA50"] = data["Close"].rolling(50).mean()
    data["AVG_VOLUME20"] = data["Volume"].rolling(20).mean()
    data["MOMENTUM20"] = data["Close"].pct_change(20) * 100

    latest = data.iloc[-1]

    close = float(latest["Close"])
    rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else 0
    sma20 = float(latest["SMA20"]) if pd.notna(latest["SMA20"]) else 0
    sma50 = float(latest["SMA50"]) if pd.notna(latest["SMA50"]) else 0
    volume = float(latest["Volume"]) if pd.notna(latest["Volume"]) else 0
    avg_volume = float(latest["AVG_VOLUME20"]) if pd.notna(latest["AVG_VOLUME20"]) else 0
    momentum20 = float(latest["MOMENTUM20"]) if pd.notna(latest["MOMENTUM20"]) else 0

    score = 0
    reasons: List[str] = []

    if rsi <= 24:
        score += 35
        reasons.append("TradingView-style RSI is near or below 24 buy-watch zone.")
    elif 24 < rsi <= 35:
        score += 25
        reasons.append("TradingView-style RSI is recovering from lower zone.")
    elif rsi >= 78:
        score -= 35
        reasons.append("TradingView-style RSI is near 78 exit/overbought zone.")
    else:
        score += 5
        reasons.append("TradingView-style RSI is neutral.")

    if sma20 and close > sma20:
        score += 15
        reasons.append("Price is above 20-day average.")
    else:
        score -= 5
        reasons.append("Price is below 20-day average.")

    if sma50 and close > sma50:
        score += 15
        reasons.append("Price is above 50-day average.")
    else:
        score -= 5
        reasons.append("Price is below 50-day average.")

    if sma20 and sma50 and sma20 > sma50:
        score += 10
        reasons.append("Short-term trend is stronger than medium-term trend.")

    volume_ratio = 0

    if avg_volume and avg_volume > 0:
        volume_ratio = volume / avg_volume

    if volume_ratio >= 1.5:
        score += 15
        reasons.append("Volume is strongly above 20-day average.")
    elif volume_ratio >= 1.1:
        score += 8
        reasons.append("Volume is above average.")
    else:
        reasons.append("Volume confirmation is weak.")

    if momentum20 > 5:
        score += 15
        reasons.append("20-day momentum is positive.")
    elif momentum20 > 0:
        score += 8
        reasons.append("20-day momentum is mildly positive.")
    else:
        score -= 8
        reasons.append("20-day momentum is negative.")

    score = max(0, min(100, int(score)))

    if score >= 75:
        action = "TOP WATCH"
    elif score >= 60:
        action = "WATCH"
    elif score >= 45:
        action = "WAIT"
    else:
        action = "AVOID"

    return {
        "symbol": symbol,
        "name": name,
        "close": round(close, 2),
        "rsi": round(rsi, 2),
        "rsi_length": RSI_LENGTH,
        "rsi_source": RSI_SOURCE,
        "rsi_method": RSI_METHOD,
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "volume_ratio": round(volume_ratio, 2),
        "momentum20_pct": round(momentum20, 2),
        "score": score,
        "action": action,
        "reason": " | ".join(reasons),
        "tradingview_symbol": f"NSE:{symbol}",
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_nandi_market_scan(
    max_symbols: int = 250,
    top_n: int = 10,
    period: str = "6mo",
) -> pd.DataFrame:
    universe = get_nse_stock_universe(limit=max_symbols)

    if universe.empty:
        return pd.DataFrame()

    symbols = universe["symbol"].astype(str).str.upper().tolist()

    data_map = download_daily_data_for_symbols(
        symbols=symbols,
        period=period,
        interval="1d",
        chunk_size=25,
    )

    results = []

    for _, row in universe.iterrows():
        symbol = str(row.get("symbol", "")).strip().upper()
        name = str(row.get("name", "")).strip()

        if symbol not in data_map:
            continue

        result = analyse_stock_with_rsi_strategy(
            symbol=symbol,
            name=name,
            df=data_map[symbol],
        )

        if result.get("action") != "SKIP":
            results.append(result)

    if not results:
        return pd.DataFrame()

    report = pd.DataFrame(results)

    report = report.sort_values(
        by=["score", "volume_ratio", "momentum20_pct"],
        ascending=[False, False, False],
    )

    return report.head(top_n).reset_index(drop=True)


def run_quick_scan() -> pd.DataFrame:
    return run_nandi_market_scan(
        max_symbols=250,
        top_n=10,
        period="6mo",
    )
