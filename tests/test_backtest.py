from datetime import date, datetime, timedelta

from nandi_oi.backtest import NandiBacktester
from nandi_oi.historical import UpstoxHistoricalClient, exactly_three_months_before
from nandi_oi.models import OptionLeg, OptionSnapshot


def snapshot(at: datetime, spot: float, ce_ltp: float, bullish: bool = True) -> OptionSnapshot:
    legs = []
    for strike in range(24750, 25300, 50):
        legs.extend([
            OptionLeg(
                strike, "CE", 1000, -100 if bullish else 100,
                ce_ltp, 5 if bullish else -5, 10000, ce_ltp, ce_ltp,
            ),
            OptionLeg(
                strike, "PE", 1000, 100 if bullish else -100,
                100, -5 if bullish else 5, 10000, 100, 100,
            ),
        ])
    return OptionSnapshot(
        timestamp=at, spot=spot, spot_change=10 if bullish else -10,
        recent_high=spot - 1 if bullish else spot + 20,
        recent_low=spot - 20 if bullish else spot + 1,
        legs=tuple(legs), expiry="2026-07-23",
    )


def test_three_month_window_is_calendar_accurate():
    assert exactly_three_months_before(date(2026, 7, 31)) == date(2026, 4, 30)
    assert exactly_three_months_before(date(2026, 5, 20)) == date(2026, 2, 20)


def test_manual_date_range_rejects_reverse_dates():
    client = UpstoxHistoricalClient(access_token="unused")
    try:
        client.build_snapshots(date(2026, 6, 2), date(2026, 6, 1))
    except ValueError as exc:
        assert "start date" in str(exc)
    else:
        raise AssertionError("Expected a reverse-date validation error")


def test_backtest_reuses_persistence_and_closes_target():
    start = datetime(2026, 7, 20, 9, 20)
    snapshots = [
        snapshot(start + timedelta(minutes=5 * index), 25000 + index * 10, 100 + index * 10)
        for index in range(8)
    ]
    result = NandiBacktester().run(snapshots)
    assert len(result.trades) >= 1
    assert result.trades[0].exit_reason == "Target"
    assert result.net_points > 0


def test_backtest_rejects_empty_history():
    try:
        NandiBacktester().run([])
    except ValueError as exc:
        assert "No historical snapshots" in str(exc)
    else:
        raise AssertionError("Expected an empty-history error")
