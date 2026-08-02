from nandi_oi.technical_indicators import (
    exponential_moving_average,
    simple_moving_average,
    technical_context,
)


def test_moving_averages_only_start_after_their_lookback_window():
    values = list(range(1, 25))
    sma = simple_moving_average(values, 5)
    ema = exponential_moving_average(values, 5)
    assert sma[:4] == [None, None, None, None]
    assert ema[:4] == [None, None, None, None]
    assert sma[4] == 3.0
    assert ema[4] == 3.0
    assert sma[-1] == 22.0


def test_technical_context_exposes_all_displayed_calculations_without_future_values():
    rows = technical_context(range(1, 60))
    assert len(rows) == 59
    assert rows[8]["EMA 9"] is not None
    assert rows[19]["Bollinger upper"] is not None
    assert rows[24]["MACD line"] is None
    assert rows[33]["MACD line"] is not None
    assert rows[10]["ROC 10 %"] == 1000.0
