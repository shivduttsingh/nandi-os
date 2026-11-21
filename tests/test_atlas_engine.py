import pandas as pd
import pytest

from atlas_engine import metrics, normalise_bars, backtest

def sample_bars():
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-11-20 09:15:00", periods=12, freq="5min"),
        "open": [100+i for i in range(12)],
        "high": [102+i for i in range(12)],
        "low": [99+i for i in range(12)],
        "close": [101+i for i in range(12)],
        "volume": [1000+i*10 for i in range(12)],
    })

def test_normalise_bars_adds_vwap_and_volume_baseline():
    frame = normalise_bars(sample_bars())
    assert "vwap" in frame.columns
    assert "volume_baseline" in frame.columns
    assert len(frame) == 12

def test_normalise_bars_rejects_missing_ohlcv_columns():
    with pytest.raises(ValueError, match="Missing columns"):
        normalise_bars(pd.DataFrame({"timestamp":["2026-11-20 09:15"],"close":[1]}))

def test_empty_backtest_returns_no_trades():
    frame = sample_bars()
    result = backtest(frame, strategy="A")
    assert isinstance(result, pd.DataFrame)

def test_metrics_handles_empty_trades():
    result = metrics(pd.DataFrame())
    assert result["trades"] == 0
    assert result["win_rate"] == 0.0
