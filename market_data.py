import pandas as pd
from core.speed_engine import ttl_cache

try:
    import yfinance as yf
except Exception:  # Keeps app importable before dependencies are installed.
    yf = None


@ttl_cache(seconds=45)
def download_market_data(symbol: str, interval: str = "5m", period: str = "5d") -> pd.DataFrame:
    if yf is None:
        raise ImportError("yfinance is not installed. Run: pip install yfinance")

    df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing market data columns: {missing}")

    if "Volume" not in df.columns:
        df["Volume"] = 0

    return df.dropna().copy()
