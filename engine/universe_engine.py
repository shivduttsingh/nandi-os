from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


DATA_DIR = Path("data")
MASTER_UNIVERSE_PATH = DATA_DIR / "master_universe.csv"

REFRESH_HOURS = 24

NSE_EQUITY_URLS = [
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
]

BSE_COMPANY_URLS = [
    "https://www.bseindia.com/downloads1/List_of_companies.csv",
]


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,text/plain,*/*",
}


CORE_MARKETS = [
    {
        "exchange": "NSE",
        "symbol": "NIFTY",
        "name": "NIFTY 50",
        "instrument_type": "index",
        "segment": "index",
        "tradingview_symbol": "NSE:NIFTY",
        "source": "core",
    },
    {
        "exchange": "NSE",
        "symbol": "BANKNIFTY",
        "name": "BANK NIFTY",
        "instrument_type": "index",
        "segment": "index",
        "tradingview_symbol": "NSE:BANKNIFTY",
        "source": "core",
    },
    {
        "exchange": "NSE",
        "symbol": "CNXFINANCE",
        "name": "FINNIFTY",
        "instrument_type": "index",
        "segment": "index",
        "tradingview_symbol": "NSE:CNXFINANCE",
        "source": "core",
    },
    {
        "exchange": "BSE",
        "symbol": "SENSEX",
        "name": "SENSEX",
        "instrument_type": "index",
        "segment": "index",
        "tradingview_symbol": "BSE:SENSEX",
        "source": "core",
    },
    {
        "exchange": "NSE",
        "symbol": "INDIAVIX",
        "name": "India VIX",
        "instrument_type": "index",
        "segment": "volatility",
        "tradingview_symbol": "NSE:INDIAVIX",
        "source": "core",
    },
    {
        "exchange": "MCX",
        "symbol": "CRUDEOIL1!",
        "name": "Crude Oil Continuous Futures",
        "instrument_type": "commodity",
        "segment": "commodity",
        "tradingview_symbol": "MCX:CRUDEOIL1!",
        "source": "core",
    },
    {
        "exchange": "MCX",
        "symbol": "NATURALGAS1!",
        "name": "Natural Gas Continuous Futures",
        "instrument_type": "commodity",
        "segment": "commodity",
        "tradingview_symbol": "MCX:NATURALGAS1!",
        "source": "core",
    },
    {
        "exchange": "MCX",
        "symbol": "GOLD1!",
        "name": "Gold Continuous Futures",
        "instrument_type": "commodity",
        "segment": "commodity",
        "tradingview_symbol": "MCX:GOLD1!",
        "source": "core",
    },
]


def _download_csv(url: str) -> Optional[pd.DataFrame]:
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()

        text = response.text.strip()

        if not text or len(text) < 50:
            return None

        return pd.read_csv(StringIO(text))
    except Exception:
        return None


def _clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        str(col).strip().upper().replace("\n", " ").replace("\r", " ")
        for col in df.columns
    ]
    return df


def load_nse_equity_universe() -> pd.DataFrame:
    raw = None

    for url in NSE_EQUITY_URLS:
        raw = _download_csv(url)
        if raw is not None and not raw.empty:
            break

    if raw is None or raw.empty:
        return pd.DataFrame()

    df = _clean_column_names(raw)

    symbol_col = None
    name_col = None
    isin_col = None
    series_col = None
    listing_col = None

    for col in df.columns:
        if col == "SYMBOL":
            symbol_col = col
        elif "NAME" in col and "COMPANY" in col:
            name_col = col
        elif "ISIN" in col:
            isin_col = col
        elif col == "SERIES":
            series_col = col
        elif "DATE" in col and "LISTING" in col:
            listing_col = col

    if symbol_col is None:
        return pd.DataFrame()

    clean = pd.DataFrame()
    clean["exchange"] = "NSE"
    clean["symbol"] = df[symbol_col].astype(str).str.strip().str.upper()

    if name_col:
        clean["name"] = df[name_col].astype(str).str.strip()
    else:
        clean["name"] = clean["symbol"]

    if isin_col:
        clean["isin"] = df[isin_col].astype(str).str.strip()
    else:
        clean["isin"] = ""

    if series_col:
        clean["series"] = df[series_col].astype(str).str.strip().str.upper()
    else:
        clean["series"] = ""

    if listing_col:
        clean["listing_date"] = df[listing_col].astype(str).str.strip()
    else:
        clean["listing_date"] = ""

    clean["instrument_type"] = "stock"
    clean["segment"] = "equity"
    clean["tradingview_symbol"] = "NSE:" + clean["symbol"]
    clean["source"] = "nse"
    clean["active_status"] = "active"

    clean = clean[clean["symbol"].notna()]
    clean = clean[clean["symbol"].str.len() > 0]

    # Prefer normal listed equity series.
    if "series" in clean.columns:
        eq_rows = clean[clean["series"].eq("EQ")]
        if not eq_rows.empty:
            clean = eq_rows

    return clean


def load_bse_universe() -> pd.DataFrame:
    raw = None

    for url in BSE_COMPANY_URLS:
        raw = _download_csv(url)
        if raw is not None and not raw.empty:
            break

    if raw is None or raw.empty:
        return pd.DataFrame()

    df = _clean_column_names(raw)

    # BSE file formats can change, so we search defensively.
    code_col = None
    name_col = None
    isin_col = None

    for col in df.columns:
        if "SCRIP CODE" in col or "SECURITY CODE" in col or col == "CODE":
            code_col = col
        elif "SECURITY NAME" in col or "NAME" in col or "COMPANY" in col:
            name_col = col
        elif "ISIN" in col:
            isin_col = col

    if code_col is None:
        return pd.DataFrame()

    clean = pd.DataFrame()
    clean["exchange"] = "BSE"
    clean["symbol"] = df[code_col].astype(str).str.strip()

    if name_col:
        clean["name"] = df[name_col].astype(str).str.strip()
    else:
        clean["name"] = clean["symbol"]

    if isin_col:
        clean["isin"] = df[isin_col].astype(str).str.strip()
    else:
        clean["isin"] = ""

    clean["series"] = ""
    clean["listing_date"] = ""
    clean["instrument_type"] = "stock"
    clean["segment"] = "equity"
    clean["tradingview_symbol"] = "BSE:" + clean["symbol"]
    clean["source"] = "bse"
    clean["active_status"] = "active"

    clean = clean[clean["symbol"].notna()]
    clean = clean[clean["symbol"].str.len() > 0]

    return clean


def core_market_universe() -> pd.DataFrame:
    df = pd.DataFrame(CORE_MARKETS)
    df["isin"] = ""
    df["series"] = ""
    df["listing_date"] = ""
    df["active_status"] = "active"
    return df


def build_master_universe() -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    frames = []

    core = core_market_universe()
    frames.append(core)

    nse = load_nse_equity_universe()
    if not nse.empty:
        frames.append(nse)

    bse = load_bse_universe()
    if not bse.empty:
        frames.append(bse)

    if frames:
        master = pd.concat(frames, ignore_index=True)
    else:
        master = core

    master["symbol"] = master["symbol"].astype(str).str.strip().str.upper()
    master["exchange"] = master["exchange"].astype(str).str.strip().str.upper()
    master["tradingview_symbol"] = master["tradingview_symbol"].astype(str).str.strip()

    master["search_text"] = (
        master["exchange"].fillna("")
        + " "
        + master["symbol"].fillna("")
        + " "
        + master["name"].fillna("")
        + " "
        + master["tradingview_symbol"].fillna("")
    ).str.upper()

    master["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    master = master.drop_duplicates(
        subset=["exchange", "symbol", "tradingview_symbol"],
        keep="first",
    )

    preferred_cols = [
        "exchange",
        "symbol",
        "name",
        "isin",
        "series",
        "listing_date",
        "instrument_type",
        "segment",
        "tradingview_symbol",
        "source",
        "active_status",
        "last_updated",
        "search_text",
    ]

    for col in preferred_cols:
        if col not in master.columns:
            master[col] = ""

    master = master[preferred_cols].sort_values(["exchange", "symbol"])

    master.to_csv(MASTER_UNIVERSE_PATH, index=False)

    return master


def cache_is_fresh() -> bool:
    if not MASTER_UNIVERSE_PATH.exists():
        return False

    modified_time = datetime.fromtimestamp(MASTER_UNIVERSE_PATH.stat().st_mtime)
    return datetime.now() - modified_time < timedelta(hours=REFRESH_HOURS)


def load_master_universe(force_refresh: bool = False) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not force_refresh and cache_is_fresh():
        try:
            return pd.read_csv(MASTER_UNIVERSE_PATH)
        except Exception:
            pass

    try:
        return build_master_universe()
    except Exception:
        if MASTER_UNIVERSE_PATH.exists():
            return pd.read_csv(MASTER_UNIVERSE_PATH)

        return core_market_universe()


def search_universe(query: str, limit: int = 50) -> pd.DataFrame:
    universe = load_master_universe(force_refresh=False)

    if query is None or not str(query).strip():
        return universe.head(limit)

    q = str(query).strip().upper()

    if "search_text" not in universe.columns:
        universe["search_text"] = (
            universe["exchange"].fillna("")
            + " "
            + universe["symbol"].fillna("")
            + " "
            + universe["name"].fillna("")
            + " "
            + universe["tradingview_symbol"].fillna("")
        ).str.upper()

    result = universe[universe["search_text"].str.contains(q, na=False)]

    return result.head(limit)


def get_universe_stats() -> dict:
    universe = load_master_universe(force_refresh=False)

    return {
        "total_instruments": int(len(universe)),
        "nse_count": int((universe["exchange"] == "NSE").sum()) if "exchange" in universe else 0,
        "bse_count": int((universe["exchange"] == "BSE").sum()) if "exchange" in universe else 0,
        "mcx_count": int((universe["exchange"] == "MCX").sum()) if "exchange" in universe else 0,
        "last_updated": str(universe["last_updated"].iloc[0]) if "last_updated" in universe and len(universe) else "",
    }
