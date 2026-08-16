from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle
from nandi_v2.atm_strategy import ATMConfirmationSignal, assess_atm_confirmation


def candles(start: float, changes: tuple[float, ...]) -> tuple[IntradayCandle, ...]:
    price = start
    output = []
    for index, change in enumerate(changes):
        opened = price
        price += change
        output.append(
            IntradayCandle(
                timestamp=datetime(2026, 8, 16, 9, 15) + timedelta(minutes=15 * index),
                open=opened,
                high=max(opened, price) + 1,
                low=min(opened, price) - 1,
                close=price,
            )
        )
    return tuple(output)


def test_rising_nifty_and_outperforming_ce_confirm_ce():
    result = assess_atm_confirmation(
        candles(25000, (0, 10, 10, 10)),
        candles(100, (0, 2, 2, 2)),
        candles(100, (0, -1, -1, -1)),
    )

    assert result.signal == ATMConfirmationSignal.CONFIRM_CE
    assert result.agreement_score > 0


def test_falling_nifty_and_outperforming_pe_confirm_pe():
    result = assess_atm_confirmation(
        candles(25000, (0, -10, -10, -10)),
        candles(100, (0, -1, -1, -1)),
        candles(100, (0, 2, 2, 2)),
    )

    assert result.signal == ATMConfirmationSignal.CONFIRM_PE


def test_conflicting_premiums_wait_and_score_is_not_probability():
    result = assess_atm_confirmation(
        candles(25000, (0, 10, 10, 10)),
        candles(100, (0, -1, -1, -1)),
        candles(100, (0, 2, 2, 2)),
    )

    assert result.signal == ATMConfirmationSignal.WAIT
    assert result.agreement_score == 0


def test_short_history_is_unavailable():
    result = assess_atm_confirmation(
        candles(25000, (1, 1)), candles(100, (1, 1)), candles(100, (-1, -1)),
    )

    assert result.signal == ATMConfirmationSignal.UNAVAILABLE
