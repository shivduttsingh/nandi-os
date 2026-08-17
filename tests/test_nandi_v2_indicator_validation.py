from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle, OptionLeg, OptionSnapshot
from nandi_v2.indicator_validation import (
    IndicatorSignal,
    IndividualIndicatorBacktester,
    aggregate_candles,
)
from nandi_v2.technical import NANDI_TOP_10_INDICATORS


def candle(at: datetime, opened: float, closed: float) -> IntradayCandle:
    return IntradayCandle(
        timestamp=at,
        open=opened,
        high=max(opened, closed) + 1,
        low=min(opened, closed) - 1,
        close=closed,
        volume=100,
    )


def snapshot(at: datetime, *, ce_low: float, ce_high: float) -> OptionSnapshot:
    legs = []
    for strike in (24950.0, 25000.0, 25050.0):
        legs.extend(
            (
                OptionLeg(
                    strike=strike,
                    side="CE",
                    oi=1000,
                    change_oi=0,
                    ltp=105,
                    change_ltp=0,
                    open_price=100,
                    high_price=ce_high,
                    low_price=ce_low,
                ),
                OptionLeg(
                    strike=strike,
                    side="PE",
                    oi=1000,
                    change_oi=0,
                    ltp=95,
                    change_ltp=0,
                    open_price=100,
                    high_price=105,
                    low_price=98,
                ),
            )
        )
    return OptionSnapshot(
        timestamp=at,
        spot=25010,
        spot_change=0,
        recent_high=25020,
        recent_low=24980,
        legs=tuple(legs),
        expiry="2026-08-20",
    )


def test_five_minute_candles_aggregate_into_complete_fifteen_minute_bar():
    start = datetime(2026, 8, 17, 9, 15)
    candles = (
        candle(start, 100, 101),
        candle(start + timedelta(minutes=5), 101, 103),
        candle(start + timedelta(minutes=10), 103, 102),
        candle(start + timedelta(minutes=15), 102, 104),
    )

    result = aggregate_candles(candles, 15)

    assert len(result) == 1
    assert result[0].timestamp == start
    assert result[0].open == 100
    assert result[0].close == 102
    assert result[0].volume == 300


def test_indicator_trade_enters_next_option_open_and_closes_at_target():
    at = datetime(2026, 8, 17, 9, 30)
    engine = IndividualIndicatorBacktester()
    result = engine._run_indicator(
        "RSI 14",
        (IndicatorSignal("RSI 14", at, "CE", 80.0),),
        (snapshot(at, ce_low=99, ce_high=110),),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.strike == 25000
    assert trade.entry_premium == 100.25
    assert trade.exit_reason == "7.5% target"
    assert trade.premium_points > 0


def test_same_option_candle_stop_and_target_uses_conservative_stop_first():
    at = datetime(2026, 8, 17, 9, 30)
    engine = IndividualIndicatorBacktester()
    result = engine._run_indicator(
        "RSI 14",
        (IndicatorSignal("RSI 14", at, "CE", 80.0),),
        (snapshot(at, ce_low=90, ce_high=120),),
    )

    assert result.trades[0].exit_reason == "5% stop"
    assert result.trades[0].premium_points < 0


def test_report_always_keeps_each_top_ten_indicator_separate():
    start = datetime(2026, 8, 17, 9, 15)
    candles = tuple(
        candle(start + timedelta(minutes=5 * index), 25000 + index, 25001 + index)
        for index in range(6)
    )
    report = IndividualIndicatorBacktester().run(
        candles,
        (snapshot(start + timedelta(minutes=15), ce_low=99, ce_high=110),),
    )

    assert [row["Indicator"] for row in report.summary_rows()] == list(NANDI_TOP_10_INDICATORS)
    assert all("Status" in row and "Win rate %" in row for row in report.summary_rows())
