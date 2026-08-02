from __future__ import annotations

from math import sqrt
from typing import Iterable


def simple_moving_average(values: Iterable[float], period: int) -> list[float | None]:
    """Trailing-only SMA; a value is never calculated with a future candle."""
    records = [float(value) for value in values]
    if period < 2:
        raise ValueError("Moving-average period must be at least 2")
    result: list[float | None] = [None] * len(records)
    for index in range(period - 1, len(records)):
        result[index] = sum(records[index - period + 1:index + 1]) / period
    return result


def exponential_moving_average(values: Iterable[float], period: int) -> list[float | None]:
    """Trailing-only EMA seeded from the first complete lookback window."""
    records = [float(value) for value in values]
    if period < 2:
        raise ValueError("Moving-average period must be at least 2")
    result: list[float | None] = [None] * len(records)
    if len(records) < period:
        return result
    seed_index = period - 1
    current = sum(records[:period]) / period
    result[seed_index] = current
    multiplier = 2 / (period + 1)
    for index in range(period, len(records)):
        current = (records[index] - current) * multiplier + current
        result[index] = current
    return result


def _ema_optional(values: list[float | None], period: int) -> list[float | None]:
    valid = [(index, value) for index, value in enumerate(values) if value is not None]
    output: list[float | None] = [None] * len(values)
    calculated = exponential_moving_average([float(value) for _, value in valid], period)
    for (index, _), value in zip(valid, calculated):
        output[index] = value
    return output


def technical_context(values: Iterable[float]) -> list[dict[str, float | None]]:
    """Technical context derived only from the five-minute NIFTY spots Nandi replayed.

    These indicators are exposed for inspection. They are not silently added to
    the OI V1 approval logic or treated as separately validated strategies.
    """
    records = [float(value) for value in values]
    sma_20 = simple_moving_average(records, 20)
    ema_9 = exponential_moving_average(records, 9)
    ema_21 = exponential_moving_average(records, 21)
    mid = sma_20
    upper: list[float | None] = [None] * len(records)
    lower: list[float | None] = [None] * len(records)
    for index in range(19, len(records)):
        window = records[index - 19:index + 1]
        average = sum(window) / 20
        standard_deviation = sqrt(sum((value - average) ** 2 for value in window) / 20)
        upper[index] = average + 2 * standard_deviation
        lower[index] = average - 2 * standard_deviation

    fast = exponential_moving_average(records, 12)
    slow = exponential_moving_average(records, 26)
    macd_line: list[float | None] = [
        round(float(first) - float(second), 4) if first is not None and second is not None else None
        for first, second in zip(fast, slow)
    ]
    macd_signal = _ema_optional(macd_line, 9)
    macd_histogram = [
        round(float(line) - float(signal), 4) if line is not None and signal is not None else None
        for line, signal in zip(macd_line, macd_signal)
    ]
    rate_of_change: list[float | None] = [None] * len(records)
    for index in range(10, len(records)):
        base = records[index - 10]
        rate_of_change[index] = ((records[index] - base) / base * 100) if base else None

    return [
        {
            "SMA 20": sma_20[index],
            "EMA 9": ema_9[index],
            "EMA 21": ema_21[index],
            "Bollinger upper": upper[index],
            "Bollinger middle": mid[index],
            "Bollinger lower": lower[index],
            "MACD line": macd_line[index],
            "MACD signal": macd_signal[index],
            "MACD histogram": macd_histogram[index],
            "ROC 10 %": rate_of_change[index],
        }
        for index in range(len(records))
    ]
