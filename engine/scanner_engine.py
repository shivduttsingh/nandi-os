from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from engine.market_data_engine import (
    download_daily_data_for_symbols,
    get_nse_stock_universe,
)


RSI_LENGTH = 14
RSI_SOURCE = "Close"
RSI_METHOD = "TradingView-style Wilder/RMA RSI"

BUY_ZONE_RSI = 24
BUY_WATCH_RSI = 35
SELL_EXIT_RSI = 72

FALLBACK_LIQUID_NSE_SYMBOLS = [
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "TCS",
    "SBIN",
    "AXISBANK",
    "KOTAKBANK",
    "LT",
    "BHARTIARTL",
    "ITC",
    "HINDUNILVR",
    "BAJFINANCE",
    "HCLTECH",
    "SUNPHARMA",
    "MARUTI",
    "TITAN",
    "ULTRACEMCO",
    "NTPC",
    "POWERGRID",
    "ONGC",
    "ADANIENT",
    "ADANIPORTS",
    "TATASTEEL",
    "JSWSTEEL",
    "COALINDIA",
    "WIPRO",
    "TECHM",
    "M&M",
    "BAJAJFINSV",
    "HDFCLIFE",
    "SBILIFE",
    "HEROMOTOCO",
    "EICHERMOT",
    "DRREDDY",
    "CIPLA",
    "DIVISLAB",
    "GRASIM",
    "BPCL",
    "IOC",
    "HINDALCO",
    "APOLLOHOSP",
    "BRITANNIA",
    "NESTLEIND",
    "ASIANPAINT",
    "TATAMOTORS",
    "INDUSINDBK",
    "BAJAJ-AUTO",
    "SHRIRAMFIN",
    "UPL",
]


def clean_text(value) -> str:
    if pd.isna(value):
        return ""

    text = str(value).strip()

    if text.lower() in ["nan", "none", "nat"]:
        return ""

    return text


def to_yahoo_symbol(symbol: str) -> str:
    symbol = clean_text(symbol).upper()

    if symbol.endswith(".NS"):
        return symbol

    return f"{symbol}.NS"


def build_fallback_universe(limit: Optional[int] = None) -> pd.DataFrame:
    symbols = FALLBACK_LIQUID_NSE_SYMBOLS.copy()

    if limit:
        symbols = symbols[: int(limit)]

    rows = []

    for symbol in symbols:
        rows.append(
            {
                "symbol": symbol,
                "name": symbol,
                "exchange": "NSE",
                "instrument_type": "stock",
                "segment": "equity",
                "tradingview_symbol": f"NSE:{symbol}",
            }
        )

    return pd.DataFrame(rows)


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


def clean_downloaded_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    data = df.copy()

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [str(col[-1]) for col in data.columns]

    required_cols = ["Open", "High", "Low", "Close", "Volume"]

    for col in required_cols:
        if col not in data.columns:
            data[col] = pd.NA

    data = data[required_cols].copy()

    for col in required_cols:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    data = data.dropna(subset=["Close"])

    return data


def direct_download_daily_data(
    symbols: List[str],
    period: str = "6mo",
    interval: str = "1d",
    chunk_size: int = 25,
) -> Dict[str, pd.DataFrame]:
    output: Dict[str, pd.DataFrame] = {}

    clean_symbols = []

    for symbol in symbols:
        symbol = clean_text(symbol).upper()

        if symbol and symbol not in clean_symbols:
            clean_symbols.append(symbol)

    if not clean_symbols:
        return output

    for start in range(0, len(clean_symbols), chunk_size):
        batch_symbols = clean_symbols[start : start + chunk_size]
        yahoo_symbols = [to_yahoo_symbol(symbol) for symbol in batch_symbols]

        try:
            raw = yf.download(
                tickers=yahoo_symbols,
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception:
            continue

        if raw is None or raw.empty:
            continue

        for original_symbol, yahoo_symbol in zip(batch_symbols, yahoo_symbols):
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    available = list(raw.columns.get_level_values(0).unique())

                    if yahoo_symbol not in available:
                        continue

                    df = raw[yahoo_symbol].copy()
                else:
                    df = raw.copy()

                df = clean_downloaded_df(df)

                if df.empty:
                    continue

                output[original_symbol] = df

            except Exception:
                continue

    return output


def analyse_stock_with_rsi_strategy(
    symbol: str,
    name: str,
    df: pd.DataFrame,
) -> Dict:
    data = clean_downloaded_df(df)

    if data.empty:
        return {
            "symbol": symbol,
            "name": name,
            "score": 0,
            "action": "SKIP",
            "reason": "No candle data available.",
        }

    if len(data) < 55:
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

    close = float(latest["Close"]) if pd.notna(latest["Close"]) else 0
    rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else 0
    sma20 = float(latest["SMA20"]) if pd.notna(latest["SMA20"]) else 0
    sma50 = float(latest["SMA50"]) if pd.notna(latest["SMA50"]) else 0
    volume = float(latest["Volume"]) if pd.notna(latest["Volume"]) else 0
    avg_volume = float(latest["AVG_VOLUME20"]) if pd.notna(latest["AVG_VOLUME20"]) else 0
    momentum20 = float(latest["MOMENTUM20"]) if pd.notna(latest["MOMENTUM20"]) else 0

    if rsi <= 0:
        return {
            "symbol": symbol,
            "name": name,
            "score": 0,
            "action": "SKIP",
            "reason": "RSI not available.",
        }

    score = 0
    reasons: List[str] = []

    if rsi <= BUY_ZONE_RSI:
        action = "BUY ZONE"
        score = 90
        reasons.append("RSI is at or below 24. This is the main buy zone.")
    elif BUY_ZONE_RSI < rsi <= BUY_WATCH_RSI:
        action = "BUY WATCH"
        score = 75
        reasons.append("RSI is between 24 and 35. This is a buy-watch recovery zone.")
    elif BUY_WATCH_RSI < rsi < SELL_EXIT_RSI:
        action = "WAIT"
        score = 40
        reasons.append("RSI is between 35 and 72. This is not a fresh buy zone.")
    else:
        action = "SELL / EXIT"
        score = 5
        reasons.append("RSI is at or above 72. This is sell/exit zone, not buy zone.")

    volume_ratio = 0

    if avg_volume and avg_volume > 0:
        volume_ratio = volume / avg_volume

    if action in ["BUY ZONE", "BUY WATCH"]:
        if sma20 and close > sma20:
            score += 5
            reasons.append("Price is above 20-day average.")
        else:
            reasons.append("Price is below 20-day average.")

        if sma50 and close > sma50:
            score += 5
            reasons.append("Price is above 50-day average.")
        else:
            reasons.append("Price is below 50-day average.")

        if volume_ratio >= 1.5:
            score += 5
            reasons.append("Volume is strongly above 20-day average.")
        elif volume_ratio >= 1.1:
            score += 3
            reasons.append("Volume is above average.")
        else:
            reasons.append("Volume confirmation is weak.")

        if momentum20 > 0:
            score += 5
            reasons.append("20-day momentum is positive.")
        else:
            reasons.append("20-day momentum is not positive yet.")
    else:
        if action == "WAIT":
            reasons.append("Nandi will wait until RSI comes closer to the buy zone.")
        elif action == "SELL / EXIT":
            reasons.append("Nandi will avoid buy because RSI is already high.")

    score = max(0, min(100, int(score)))

    return {
        "symbol": symbol,
        "name": name,
        "close": round(close, 2),
        "rsi": round(rsi, 2),
        "rsi_length": RSI_LENGTH,
        "rsi_source": RSI_SOURCE,
        "rsi_method": RSI_METHOD,
        "buy_zone": f"RSI <= {BUY_ZONE_RSI}",
        "buy_watch_zone": f"RSI {BUY_ZONE_RSI} to {BUY_WATCH_RSI}",
        "sell_exit_zone": f"RSI >= {SELL_EXIT_RSI}",
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


def run_scan_for_universe(
    universe: pd.DataFrame,
    top_n: int = 10,
    period: str = "6mo",
) -> pd.DataFrame:
    if universe is None or universe.empty:
        return pd.DataFrame()

    universe = universe.copy()

    if "symbol" not in universe.columns:
        return pd.DataFrame()

    if "name" not in universe.columns:
        universe["name"] = universe["symbol"]

    universe["symbol"] = universe["symbol"].apply(clean_text).str.upper()
    universe["name"] = universe["name"].apply(clean_text)

    universe = universe[universe["symbol"].str.len() > 0]
    universe = universe.drop_duplicates(subset=["symbol"], keep="first")

    symbols = universe["symbol"].astype(str).str.upper().tolist()

    if not symbols:
        return pd.DataFrame()

    data_map = download_daily_data_for_symbols(
        symbols=symbols,
        period=period,
        interval="1d",
        chunk_size=25,
    )

    if not data_map:
        data_map = direct_download_daily_data(
            symbols=symbols,
            period=period,
            interval="1d",
            chunk_size=25,
        )

    results = []

    for _, row in universe.iterrows():
        symbol = clean_text(row.get("symbol", "")).upper()
        name = clean_text(row.get("name", ""))

        if not symbol:
            continue

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

    action_rank = {
        "BUY ZONE": 1,
        "BUY WATCH": 2,
        "WAIT": 3,
        "SELL / EXIT": 4,
    }

    report["action_rank"] = report["action"].map(action_rank).fillna(9)

    report = report.sort_values(
        by=["action_rank", "score", "volume_ratio", "momentum20_pct"],
        ascending=[True, False, False, False],
    )

    report = report.drop(columns=["action_rank"])

    return report.head(top_n).reset_index(drop=True)


def run_nandi_market_scan(
    max_symbols: int = 250,
    top_n: int = 10,
    period: str = "6mo",
) -> pd.DataFrame:
    universe = get_nse_stock_universe(limit=max_symbols)

    report = run_scan_for_universe(
        universe=universe,
        top_n=top_n,
        period=period,
    )

    if not report.empty:
        return report

    fallback_universe = build_fallback_universe(limit=max_symbols)

    fallback_report = run_scan_for_universe(
        universe=fallback_universe,
        top_n=top_n,
        period=period,
    )

    return fallback_report


def run_quick_scan() -> pd.DataFrame:
    return run_nandi_market_scan(
        max_symbols=50,
        top_n=10,
        period="6mo",
    )
