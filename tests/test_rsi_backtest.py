from datetime import date, datetime, timedelta

from nandi_oi.rsi_backtest import RsiLevelBacktester, RsiTouchAnalyzer, _resample_closes, wilder_rsi
from nandi_oi.models import OptionLeg, OptionSnapshot


def test_wilder_rsi_reaches_expected_extremes():
    falling = wilder_rsi([100 - index for index in range(20)])
    rising = wilder_rsi([100 + index for index in range(20)])
    assert falling[-1] == 0.0
    assert rising[-1] == 100.0


def test_resample_uses_last_close_and_aligns_to_market_open():
    start = datetime(2026, 7, 20, 9, 15)
    closes = {start + timedelta(minutes=index): 100 + index for index in range(6)}
    assert _resample_closes(closes, 5) == [
        (start + timedelta(minutes=4), 104.0),
        (start + timedelta(minutes=5), 105.0),
    ]


def test_touch_analyzer_counts_zone_entry_once_not_every_zone_candle():
    start = datetime(2026, 7, 20, 9, 15)
    prices = [100 + index for index in range(16)]
    prices.extend([114 - index for index in range(20)])
    closes = {start + timedelta(minutes=index): price for index, price in enumerate(prices)}
    result = RsiTouchAnalyzer(
        length=5, lower=30, upper=70, timeframes=(1,),
    ).run(closes, date(2026, 7, 20), date(2026, 7, 20))
    summary = result.summaries[0]
    assert summary.upper_touches == 1
    assert summary.lower_touches == 1
    assert summary.upper_zone_candles > summary.upper_touches
    assert summary.lower_zone_candles > summary.lower_touches


def test_level_backtest_uses_five_percent_stop():
    start = datetime(2026, 7, 20, 9, 15)
    snapshots = []
    for index in range(14):
        spot = 100 - index
        low = 94 if index == 7 else 100
        legs = (
            OptionLeg(100, "CE", 1000, 0, 100, 0, 1000, 99, 101, 100, 100, low),
            OptionLeg(100, "PE", 1000, 0, 100, 0, 1000, 99, 101, 100, 100, 100),
        )
        snapshots.append(OptionSnapshot(
            start + timedelta(minutes=5 * index), spot, -1, spot + 1, spot - 1,
            legs, "2026-07-23",
        ))
    result = RsiLevelBacktester(length=5, lower=30, upper=70).run(snapshots)
    assert result.trades[0].stop_price == 95.0
    assert result.trades[0].exit_reason == "5% stop loss"
    assert result.trades[0].pnl_points == -5.0
