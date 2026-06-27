from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from engine.universe_engine import load_master_universe


def to_yahoo_nse_symbol(symbol: str) -> str:
    symbol = str(symbol).strip().upper()

    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol

    return f"{symbol}.NS"


def get_nse_stock_universe(limit: Optional[int] = None) -> pd.DataFrame:
    universe = load_master_universe(force_refresh=False)

    if universe.empty:
        return pd.DataFrame()

    df = universe.copy()

    for col in ["exchange", "instrument_type", "segment", "symbol", "name"]:
        if col not in df.columns:
            df[col] = ""

    df["exchange"] = df["exchange"].astype(str).str.upper().str.strip()
    df["instrument_type"] = df["instrument_type"].astype(str).str.lower().str.strip()
    df["segment"] = df["segment"].astype(str).str.lower().str.strip()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()

    df = df[
        (df["exchange"] == "NSE")
        & (df["instrument_type"] == "stock")
        & (df["segment"] == "equity")
    ]

    df = df[df["symbol"].str.len() > 0]
    df = df.drop_duplicates(subset=["symbol"], keep="first")
    df = df[~df["symbol"].isin(["NAN", "NONE", ""])]

    if limit:
        df = df.head(limit)

    return df.reset_index(drop=True)


def download_daily_data_for_symbols(
    symbols: List[str],
    period: str = "6mo",
    interval: str = "1d",
    chunk_size: int = 40,
) -> Dict[str, pd.DataFrame]:
    output: Dict[str, pd.DataFrame] = {}

    clean_symbols = []

    for symbol in symbols:
        symbol = str(symbol).strip().upper()

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
                    if yahoo_symbol not in raw.columns.get_level_values(0):
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
