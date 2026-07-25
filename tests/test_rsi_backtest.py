from datetime import datetime, timedelta

from nandi_oi.models import OptionLeg, OptionSnapshot
from nandi_oi.rsi_backtest import RSI2472Backtester, wilder_rsi


def test_wilder_rsi_reaches_expected_extremes():
    falling = wilder_rsi([100 - index for index in range(20)])
    rising = wilder_rsi([100 + index for index in range(20)])
    assert falling[-1] == 0.0
    assert rising[-1] == 100.0


def test_rsi_24_signal_buys_ce_on_next_candle_and_hits_target():
    start = datetime(2026, 7, 20, 9, 15)
    snapshots = []
    for index in range(20):
        ce_high = 130.0 if index == 16 else 100.0
        legs = (
            OptionLeg(
                strike=25000, side="CE", oi=1000, change_oi=0, ltp=100,
                change_ltp=0, volume=1000, bid=99, ask=101,
                open_price=100, high_price=ce_high, low_price=100,
            ),
            OptionLeg(
                strike=25000, side="PE", oi=1000, change_oi=0, ltp=100,
                change_ltp=0, volume=1000, bid=99, ask=101,
                open_price=100, high_price=100, low_price=100,
            ),
        )
        spot = 25000 - index * 10
        snapshots.append(OptionSnapshot(
            timestamp=start + timedelta(minutes=5 * index), spot=spot,
            spot_change=-10, recent_high=spot + 10, recent_low=spot - 10,
            legs=legs, expiry="2026-07-23",
        ))

    result = RSI2472Backtester().run(snapshots)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.action == "BUY CE"
    assert trade.opened_at == snapshots[15].timestamp
    assert trade.stop_price == 80.0
    assert trade.target_price == 130.0
    assert trade.exit_reason == "Target"
    assert trade.pnl_points == 30.0
