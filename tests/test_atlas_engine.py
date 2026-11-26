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

def test_normalise_bars_rejects_inconsistent_ohlc():
    frame = sample_bars()
    frame.loc[0, "high"] = frame.loc[0, "close"] - 1
    with pytest.raises(ValueError, match="Invalid OHLCV rows"):
        normalise_bars(frame)


def test_normalise_bars_rejects_negative_volume():
    frame = sample_bars()
    frame.loc[0, "volume"] = -1
    with pytest.raises(ValueError, match="Invalid OHLCV rows"):
        normalise_bars(frame)


def test_backtest_rejects_invalid_parameters():
    frame = sample_bars()
    with pytest.raises(ValueError, match="risk_reward"):
        backtest(frame, risk_reward=0)
    with pytest.raises(ValueError, match="cost_points"):
        backtest(frame, cost_points=-0.1)
    with pytest.raises(ValueError, match="max_hold_bars"):
        backtest(frame, max_hold_bars=0)
    with pytest.raises(ValueError, match="strategy"):
        backtest(frame, strategy="D")


def test_backtest_never_uses_next_session_candle(monkeypatch):
    import atlas_engine

    frame = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-11-20 15:20", "2026-11-20 15:25",
            "2026-11-23 09:15", "2026-11-23 09:20",
        ]),
        "open": [100, 101, 200, 201],
        "high": [102, 103, 202, 203],
        "low": [99, 100, 199, 200],
        "close": [101, 102, 201, 202],
        "volume": [100, 110, 120, 130],
    })
    signal = atlas_engine.Signal(
        "A", "CE", "2026-11-20 15:25:00", 102, 92, 122, "test signal", 80
    )
    monkeypatch.setattr(atlas_engine, "generate_signals", lambda *args, **kwargs: [signal])
    trades = atlas_engine.backtest(frame, strategy="A", max_hold_bars=9)
    assert trades.empty
