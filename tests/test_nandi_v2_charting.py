from datetime import datetime
from zoneinfo import ZoneInfo

from nandi_oi.models import IntradayCandle
from nandi_v2.charting import candlestick_chart_html, completed_candles, merge_candles


IST = ZoneInfo("Asia/Kolkata")


def candle(hour: int, minute: int, close: float) -> IntradayCandle:
    return IntradayCandle(
        datetime(2026, 8, 12, hour, minute), close - 5, close + 10, close - 10, close,
    )


def test_forming_candle_is_excluded_from_completed_structure():
    candles = (candle(9, 15, 25010), candle(9, 30, 25020), candle(9, 45, 25030))
    observed = datetime(2026, 8, 12, 9, 52, tzinfo=IST)

    completed = completed_candles(candles, observed, 15)

    assert [item.timestamp.minute for item in completed] == [15, 30]


def test_chart_uses_candlestick_series_and_upstox_attribution():
    html = candlestick_chart_html((candle(9, 15, 25010),), interval_minutes=15)

    assert "addCandlestickSeries" in html
    assert "read-only Upstox V3 OHLC data" in html
    assert '"open":25005' in html


def test_historical_and_intraday_candles_merge_without_duplicate_timestamps():
    history = (candle(9, 15, 25010), candle(9, 30, 25020))
    intraday = (candle(9, 30, 25120), candle(9, 45, 25130))

    merged = merge_candles(history, intraday)

    assert [item.timestamp.minute for item in merged] == [15, 30, 45]
    assert merged[1].close == 25120
