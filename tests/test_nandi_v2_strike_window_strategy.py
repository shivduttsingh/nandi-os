from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.strike_window_strategy import (
    StrikeWindowSignal,
    assess_strike_window_confirmation,
    strike_offset_label,
)


def candles(start: float, changes: tuple[float, ...]) -> tuple[IntradayCandle, ...]:
    price = start
    output = []
    for index, change in enumerate(changes):
        opened = price
        price += change
        output.append(
            IntradayCandle(
                timestamp=datetime(2026, 8, 17, 9, 15) + timedelta(minutes=15 * index),
                open=opened,
                high=max(opened, price) + 1,
                low=min(opened, price) - 1,
                close=price,
            )
        )
    return tuple(output)


def window(
    ce_changes: tuple[float, ...],
    pe_changes: tuple[float, ...],
) -> tuple[OptionStrikeCandles, ...]:
    return tuple(
        OptionStrikeCandles(
            strike=25000 + offset * 50,
            expiry="2026-08-20",
            offset=offset,
            ce_candles=candles(100 + offset * 5, ce_changes),
            pe_candles=candles(100 - offset * 5, pe_changes),
        )
        for offset in range(-2, 3)
    )


def test_rising_nifty_and_four_of_five_ce_breadth_confirm_ce():
    series = list(window((0, 2, 2, 2), (0, -1, -1, -1)))
    weak = series[-1]
    series[-1] = OptionStrikeCandles(
        strike=weak.strike,
        expiry=weak.expiry,
        offset=weak.offset,
        ce_candles=candles(110, (0, -1, -1, -1)),
        pe_candles=weak.pe_candles,
    )

    result = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10)),
        series,
    )

    assert result.signal == StrikeWindowSignal.CONFIRM_CE
    assert result.ce_positive_strikes == 4
    assert result.dominant_strikes >= 4
    assert result.agreement_score > 0


def test_falling_nifty_and_pe_breadth_confirm_pe():
    result = assess_strike_window_confirmation(
        candles(25000, (0, -10, -10, -10)),
        window((0, -1, -1, -1), (0, 2, 2, 2)),
    )

    assert result.signal == StrikeWindowSignal.CONFIRM_PE
    assert result.pe_positive_strikes == 5


def test_three_of_five_breadth_is_wait_even_when_median_is_positive():
    series = list(window((0, 2, 2, 2), (0, -1, -1, -1)))
    for index in (0, 4):
        item = series[index]
        series[index] = OptionStrikeCandles(
            strike=item.strike,
            expiry=item.expiry,
            offset=item.offset,
            ce_candles=candles(100, (0, -1, -1, -1)),
            pe_candles=item.pe_candles,
        )

    result = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10)),
        series,
    )

    assert result.signal == StrikeWindowSignal.WAIT
    assert result.ce_positive_strikes == 3
    assert result.agreement_score == 0


def test_incomplete_window_or_short_history_is_unavailable():
    full = window((0, 2, 2, 2), (0, -1, -1, -1))
    incomplete = assess_strike_window_confirmation(
        candles(25000, (0, 10, 10, 10)),
        full[:-1],
    )
    short = assess_strike_window_confirmation(
        candles(25000, (0, 10)),
        tuple(
            OptionStrikeCandles(
                strike=item.strike,
                expiry=item.expiry,
                offset=item.offset,
                ce_candles=item.ce_candles[:2],
                pe_candles=item.pe_candles[:2],
            )
            for item in full
        ),
    )

    assert incomplete.signal == StrikeWindowSignal.UNAVAILABLE
    assert short.signal == StrikeWindowSignal.UNAVAILABLE


def test_strike_labels_name_both_wings_and_atm():
    assert [strike_offset_label(offset) for offset in range(-2, 3)] == [
        "2 strikes below ATM",
        "1 strike below ATM",
        "ATM",
        "1 strike above ATM",
        "2 strikes above ATM",
    ]
