from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.strike_window_strategy import (
    StrikeWindowSignal,
    assess_strike_window_confirmation,
    strike_offset_label,
)


def candles(
    start: float,
    changes: tuple[float, ...],
    *,
    volume: float = 0.0,
    open_interest: float = 0.0,
) -> tuple[IntradayCandle, ...]:
    price = start
    output = []
    for index, change in enumerate(changes):
        opened = price
        price += change
        output.append(
            IntradayCandle(
                timestamp=datetime(2026, 8, 17, 9, 15)
                + timedelta(minutes=15 * index),
                open=opened,
                high=max(opened, price) + 1,
                low=min(opened, price) - 1,
                close=price,
                volume=volume * (1 + index * 0.2) if volume else 0.0,
                open_interest=(
                    open_interest * (1 + index * 0.01) if open_interest else 0.0
                ),
            )
        )
    return tuple(output)


def window(
    ce_changes: tuple[float, ...],
    pe_changes: tuple[float, ...],
    *,
    volume: float = 0.0,
    open_interest: float = 0.0,
) -> tuple[OptionStrikeCandles, ...]:
    return tuple(
        OptionStrikeCandles(
            strike=25000 + offset * 50,
            expiry="2026-08-20",
            offset=offset,
            ce_candles=candles(
                100 + offset * 5,
                ce_changes,
                volume=volume,
                open_interest=open_interest,
            ),
            pe_candles=candles(
                100 - offset * 5,
                pe_changes,
                volume=volume,
                open_interest=open_interest,
            ),
        )
        for offset in range(-2, 3)
    )


def test_rising_nifty_and_four_of_five_ce_breadth_confirm_ce():
    series = list(window((0, 2, 2, 2, 2), (0, -1, -1, -1, -1)))
    weak = series[-1]
    series[-1] = OptionStrikeCandles(
        strike=weak.strike,
        expiry=weak.expiry,
        offset=weak.offset,
        ce_candles=candles(110, (0, -1, -1, -1, -1)),
        pe_candles=weak.pe_candles,
    )

    result = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10, 10)),
        series,
    )

    assert result.signal == StrikeWindowSignal.CONFIRM_CE
    assert result.ce_positive_strikes == 4
    assert result.weighted_dominance_pct >= 70
    assert result.persistence_bars == 2
    assert result.agreement_score >= 65
    assert result.status_label == "CONFIRM CE"


def test_falling_nifty_and_pe_breadth_confirm_pe():
    result = assess_strike_window_confirmation(
        candles(25000, (0, -10, -10, -10, -10)),
        window((0, -1, -1, -1, -1), (0, 2, 2, 2, 2)),
    )

    assert result.signal == StrikeWindowSignal.CONFIRM_PE
    assert result.pe_positive_strikes == 5
    assert result.nifty_structure == "BEARISH"


def test_three_of_five_breadth_is_wait_even_if_score_reaches_threshold():
    series = list(window((0, 2, 2, 2, 2), (0, -1, -1, -1, -1)))
    for index in (0, 4):
        item = series[index]
        series[index] = OptionStrikeCandles(
            strike=item.strike,
            expiry=item.expiry,
            offset=item.offset,
            ce_candles=candles(100, (0, -1, -1, -1, -1)),
            pe_candles=item.pe_candles,
        )

    result = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10, 10)),
        series,
    )

    assert result.signal == StrikeWindowSignal.WAIT
    assert result.ce_positive_strikes == 3
    assert result.agreement_score >= 65
    assert "3/5" in result.reason


def test_opposite_nifty_structure_blocks_ce_and_reports_conflict():
    result = assess_strike_window_confirmation(
        candles(25000, (0, -10, -10, -10, -10)),
        window((0, 2, 2, 2, 2), (0, -1, -1, -1, -1)),
    )

    assert result.signal == StrikeWindowSignal.WAIT
    assert result.status_label == "WAIT — CONFLICTING EVIDENCE"
    assert any("conflicts" in blocker for blocker in result.blockers)


def test_sideways_chop_returns_explicit_no_trade_state():
    result = assess_strike_window_confirmation(
        candles(25000, (0, 1, -1, 1, -1)),
        window((0, 0.1, -0.1, 0.1, -0.1), (0, 0.1, -0.1, 0.1, -0.1)),
    )

    assert result.signal == StrikeWindowSignal.WAIT
    assert result.status_label == "SIDEWAYS — NO TRADE"
    assert result.trend_efficiency is not None
    assert result.trend_efficiency < 0.35


def test_persistence_filter_blocks_one_bar_direction_flip():
    result = assess_strike_window_confirmation(
        candles(25000, (0, 0, 0, 0, 30)),
        window((0, 0, 0, 0, 5), (0, 0, 0, 0, -3)),
    )

    assert result.signal == StrikeWindowSignal.WAIT
    assert result.persistence_bars < 2
    assert any("persisted" in blocker for blocker in result.blockers)


def test_oi_volume_and_vwap_raise_full_quality_without_changing_signal_contract():
    result = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10, 10), volume=1000),
        window(
            (0, 2, 2, 2, 2),
            (0, -1, -1, -1, -1),
            volume=1000,
            open_interest=10000,
        ),
    )

    assert result.signal == StrikeWindowSignal.CONFIRM_CE
    assert result.status_label == "A+ CE SETUP"
    assert result.oi_confirmation == "SUPPORTS"
    assert result.volume_confirmation == "EXPANDING"
    assert result.vwap_confirmation == "BULLISH"
    assert result.agreement_score == 100.0
    assert dict(result.component_scores)["OI confirmation"] == 15.0


def test_incomplete_window_or_short_history_is_unavailable():
    full = window((0, 2, 2, 2, 2), (0, -1, -1, -1, -1))
    incomplete = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10, 10)),
        full[:-1],
    )
    short = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10)),
        tuple(
            OptionStrikeCandles(
                strike=item.strike,
                expiry=item.expiry,
                offset=item.offset,
                ce_candles=item.ce_candles[:4],
                pe_candles=item.pe_candles[:4],
            )
            for item in full
        ),
    )

    assert incomplete.signal == StrikeWindowSignal.UNAVAILABLE
    assert short.signal == StrikeWindowSignal.UNAVAILABLE
    assert "persistence" in short.reason


def test_strike_labels_name_both_wings_and_atm():
    assert [strike_offset_label(offset) for offset in range(-2, 3)] == [
        "2 strikes below ATM",
        "1 strike below ATM",
        "ATM",
        "1 strike above ATM",
        "2 strikes above ATM",
    ]
