from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from engine.universe_engine import load_master_universe


def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in ["nan", "none", "nat"]:
        return ""
    return text


def to_yahoo_nse_symbol(symbol: str) -> str:
    symbol = clean_text(symbol).upper()

    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol

    return f"{symbol}.NS"


def get_nse_stock_universe(limit: Optional[int] = None) -> pd.DataFrame:
    universe = load_master_universe(force_refresh=False)

    if universe.empty:
        return pd.DataFrame()

    df = universe.copy()

    for col in ["exchange", "instrument_type", "segment", "symbol", "name", "tradingview_symbol"]:
        if col not in df.columns:
            df[col] = ""

    df["exchange"] = df["exchange"].apply(clean_text).str.upper()
    df["instrument_type"] = df["instrument_type"].apply(clean_text).str.lower()
    df["segment"] = df["segment"].apply(clean_text).str.lower()
    df["symbol"] = df["symbol"].apply(clean_text).str.upper()
    df["name"] = df["name"].apply(clean_text)
    df["tradingview_symbol"] = df["tradingview_symbol"].apply(clean_text).str.upper()

    missing_exchange = df["exchange"].eq("") & df["tradingview_symbol"].str.startswith("NSE:")
    df.loc[missing_exchange, "exchange"] = "NSE"

    missing_symbol = df["symbol"].eq("") & df["tradingview_symbol"].str.startswith("NSE:")
    df.loc[missing_symbol, "symbol"] = df.loc[missing_symbol, "tradingview_symbol"].str.replace("NSE:", "", regex=False)

    df = df[
        (df["exchange"] == "NSE")
        & (df["instrument_type"] == "stock")
        & (df["segment"] == "equity")
    ]

    df = df[df["symbol"].str.len() > 0]
    df = df[~df["symbol"].isin(["NAN", "NONE", ""])]
    df = df.drop_duplicates(subset=["symbol"], keep="first")

    # Avoid symbols that Yahoo usually cannot read properly.
    df = df[~df["symbol"].str.contains(" ", na=False)]
    df = df[~df["symbol"].str.contains("&", na=False)]

    if limit:
        df = df.head(limit)

    return df.reset_index(drop=True)


def download_daily_data_for_symbols(
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
        yahoo_symbols = [to_yahoo_nse_symbol(symbol) for symbol in batch_symbols]

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
                    available_tickers = list(raw.columns.get_level_values(0).unique())

                    if yahoo_symbol not in available_tickers:
                        continue

                    df = raw[yahoo_symbol].copy()
                else:
                    df = raw.copy()

                df = df.dropna(how="all")

                if df.empty:
                    continue

                required_cols = ["Open", "High", "Low", "Close", "Volume"]

                for col in required_cols:
                    if col not in df.columns:
                        df[col] = pd.NA

                df = df[required_cols].dropna(subset=["Close"])

                if len(df) < 60:
                    continue

                output[original_symbol] = df

            except Exception:
                continue

    return output
