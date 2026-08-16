from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import sqrt
from statistics import mean
from typing import Iterable

from nandi_oi.models import IntradayCandle


# A compact operator view spanning distinct evidence families. This is a Nandi
# product selection, not a claim that an exchange publishes an indicator-usage ranking.
NANDI_TOP_10_INDICATORS = (
    "Close vs SMA 20",
    "MACD histogram",
    "Supertrend 10/3",
    "DMI / ADX 14",
    "RSI 14",
    "Stochastic %K 14",
    "Bollinger position 20/2",
    "Donchian breakout 20",
    "ATR expansion 14",
    "Pivot position",
)


class TechnicalDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class IndicatorVote:
    name: str
    family: str
    direction: TechnicalDirection
    strength: float
    value: str
    reason: str

    @property
    def available(self) -> bool:
        return self.direction != TechnicalDirection.UNAVAILABLE


@dataclass(frozen=True)
class TechnicalAssessment:
    direction: TechnicalDirection
    setup_score: float
    bullish_score: float
    bearish_score: float
    coverage: float
    votes: tuple[IndicatorVote, ...]
    family_rows: tuple[dict[str, object], ...]
    blockers: tuple[str, ...] = tuple()

    def side_score(self, side: str) -> float:
        return self.bullish_score if side == "CE" else self.bearish_score


def _ema(values: list[float], period: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if period < 2 or len(values) < period:
        return output
    current = mean(values[:period])
    output[period - 1] = current
    multiplier = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current = current + multiplier * (values[index] - current)
        output[index] = current
    return output


def _sma(values: list[float], period: int) -> float | None:
    return mean(values[-period:]) if len(values) >= period else None


def _atr(candles: list[IntradayCandle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    ranges = []
    for previous, current in zip(candles[-period - 1:-1], candles[-period:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return mean(ranges)


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    changes = [right - left for left, right in zip(values[-period - 1:-1], values[-period:])]
    gains = mean(max(value, 0.0) for value in changes)
    losses = mean(max(-value, 0.0) for value in changes)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _vote(
    name: str,
    family: str,
    direction: TechnicalDirection,
    value: float | str,
    reason: str,
    strength: float = 1.0,
) -> IndicatorVote:
    rendered = f"{value:.2f}" if isinstance(value, float) else str(value)
    return IndicatorVote(
        name=name,
        family=family,
        direction=direction,
        strength=round(max(0.0, min(1.0, float(strength))), 3),
        value=rendered,
        reason=reason,
    )


def _warmup(name: str, family: str, required: int, actual: int) -> IndicatorVote:
    return _vote(
        name,
        family,
        TechnicalDirection.UNAVAILABLE,
        "WARMUP",
        f"Needs {required} completed candles; {actual} available.",
        0.0,
    )


def _compare(
    name: str,
    family: str,
    left: float | None,
    right: float | None,
    reason_label: str,
    scale: float,
    required: int,
    actual: int,
) -> IndicatorVote:
    if left is None or right is None:
        return _warmup(name, family, required, actual)
    difference = left - right
    neutral_band = max(abs(scale) * 0.05, 1e-9)
    if difference > neutral_band:
        direction = TechnicalDirection.BULLISH
    elif difference < -neutral_band:
        direction = TechnicalDirection.BEARISH
    else:
        direction = TechnicalDirection.NEUTRAL
    strength = min(1.0, abs(difference) / max(abs(scale), 1e-9))
    return _vote(
        name,
        family,
        direction,
        difference,
        f"{reason_label}: {left:.2f} versus {right:.2f}.",
        strength,
    )


def indicator_votes(candles: Iterable[IntradayCandle]) -> tuple[IndicatorVote, ...]:
    bars = sorted(tuple(candles), key=lambda item: item.timestamp)
    count = len(bars)
    if not bars:
        return tuple(_warmup(name, family, required, 0) for name, family, required in _catalogue())

    closes = [float(item.close) for item in bars]
    highs = [float(item.high) for item in bars]
    lows = [float(item.low) for item in bars]
    volumes = [max(0.0, float(item.volume)) for item in bars]
    close = closes[-1]
    previous_close = closes[-2] if count >= 2 else close
    atr = _atr(list(bars), 14)
    price_scale = atr or max(abs(close) * 0.001, 1.0)
    sma_5 = _sma(closes, 5)
    sma_20 = _sma(closes, 20)
    ema_9_values = _ema(closes, 9)
    ema_21_values = _ema(closes, 21)
    ema_12_values = _ema(closes, 12)
    ema_26_values = _ema(closes, 26)
    ema_9 = ema_9_values[-1]
    ema_21 = ema_21_values[-1]

    votes: list[IndicatorVote] = [
        _compare("Close vs SMA 5", "Trend", close, sma_5, "Close compared with SMA 5", price_scale, 5, count),
        _compare("Close vs SMA 20", "Trend", close, sma_20, "Close compared with SMA 20", price_scale, 20, count),
        _compare("SMA 5 vs SMA 20", "Trend", sma_5, sma_20, "Fast SMA compared with slow SMA", price_scale, 20, count),
        _compare("Close vs EMA 9", "Trend", close, ema_9, "Close compared with EMA 9", price_scale, 9, count),
        _compare("EMA 9 vs EMA 21", "Trend", ema_9, ema_21, "Fast EMA compared with slow EMA", price_scale, 21, count),
    ]

    macd_line: list[float | None] = [
        fast - slow if fast is not None and slow is not None else None
        for fast, slow in zip(ema_12_values, ema_26_values)
    ]
    valid_macd = [float(value) for value in macd_line if value is not None]
    signal_values = _ema(valid_macd, 9)
    signal = signal_values[-1] if signal_values else None
    macd = macd_line[-1]
    histogram = macd - signal if macd is not None and signal is not None else None
    votes.extend(
        [
            _compare("MACD line", "Trend", macd, 0.0 if macd is not None else None, "MACD line compared with zero", price_scale, 26, count),
            _compare("MACD histogram", "Trend", histogram, 0.0 if histogram is not None else None, "MACD histogram compared with zero", price_scale, 34, count),
        ]
    )

    if atr is None:
        votes.append(_warmup("Supertrend 10/3", "Trend", 15, count))
    else:
        midpoint = (bars[-1].high + bars[-1].low) / 2.0
        upper = midpoint + 3.0 * atr
        lower = midpoint - 3.0 * atr
        if close > upper:
            supertrend_direction = TechnicalDirection.BULLISH
        elif close < lower:
            supertrend_direction = TechnicalDirection.BEARISH
        else:
            supertrend_direction = (
                TechnicalDirection.BULLISH if close > (sma_20 or midpoint)
                else TechnicalDirection.BEARISH if close < (sma_20 or midpoint)
                else TechnicalDirection.NEUTRAL
            )
        votes.append(_vote("Supertrend 10/3", "Trend", supertrend_direction, close - midpoint, "ATR trend regime around the latest candle.", min(1.0, abs(close - midpoint) / max(atr, 1e-9))))

    if count < 15:
        votes.append(_warmup("DMI / ADX 14", "Trend", 15, count))
    else:
        plus_dm = 0.0
        minus_dm = 0.0
        true_range = 0.0
        for previous, current in zip(bars[-15:-1], bars[-14:]):
            upward = current.high - previous.high
            downward = previous.low - current.low
            plus_dm += upward if upward > downward and upward > 0 else 0.0
            minus_dm += downward if downward > upward and downward > 0 else 0.0
            true_range += max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
        plus_di = plus_dm / true_range * 100.0 if true_range else 0.0
        minus_di = minus_dm / true_range * 100.0 if true_range else 0.0
        dx = abs(plus_di - minus_di) / max(plus_di + minus_di, 1e-9) * 100.0
        dmi_direction = TechnicalDirection.BULLISH if plus_di > minus_di else TechnicalDirection.BEARISH if minus_di > plus_di else TechnicalDirection.NEUTRAL
        votes.append(_vote("DMI / ADX 14", "Trend", dmi_direction, f"+DI {plus_di:.1f} / -DI {minus_di:.1f} / DX {dx:.1f}", "Directional movement and trend strength.", min(1.0, dx / 35.0)))

    if count < 14:
        votes.append(_warmup("Aroon oscillator 14", "Trend", 14, count))
    else:
        window_highs = highs[-14:]
        window_lows = lows[-14:]
        periods_since_high = 13 - max(range(14), key=window_highs.__getitem__)
        periods_since_low = 13 - min(range(14), key=window_lows.__getitem__)
        aroon_up = (14 - periods_since_high) / 14 * 100.0
        aroon_down = (14 - periods_since_low) / 14 * 100.0
        oscillator = aroon_up - aroon_down
        aroon_direction = TechnicalDirection.BULLISH if oscillator > 15 else TechnicalDirection.BEARISH if oscillator < -15 else TechnicalDirection.NEUTRAL
        votes.append(_vote("Aroon oscillator 14", "Trend", aroon_direction, oscillator, "Recency of the latest 14-bar high versus low.", min(1.0, abs(oscillator) / 70.0)))

    rsi = _rsi(closes, 14)
    if rsi is None:
        votes.append(_warmup("RSI 14", "Momentum", 15, count))
    else:
        rsi_direction = TechnicalDirection.BULLISH if rsi >= 55 else TechnicalDirection.BEARISH if rsi <= 45 else TechnicalDirection.NEUTRAL
        votes.append(_vote("RSI 14", "Momentum", rsi_direction, rsi, "Momentum regime; extreme readings are not automatic entries.", min(1.0, abs(rsi - 50.0) / 25.0)))

    stochastic_values: list[float] = []
    if count >= 14:
        for index in range(13, count):
            low_14 = min(lows[index - 13:index + 1])
            high_14 = max(highs[index - 13:index + 1])
            stochastic_values.append((closes[index] - low_14) / max(high_14 - low_14, 1e-9) * 100.0)
    stochastic_k = stochastic_values[-1] if stochastic_values else None
    stochastic_d = mean(stochastic_values[-3:]) if len(stochastic_values) >= 3 else None
    for name, value, required in (("Stochastic %K 14", stochastic_k, 14), ("Stochastic %D 3", stochastic_d, 16)):
        if value is None:
            votes.append(_warmup(name, "Momentum", required, count))
        else:
            direction = TechnicalDirection.BULLISH if value >= 55 else TechnicalDirection.BEARISH if value <= 45 else TechnicalDirection.NEUTRAL
            votes.append(_vote(name, "Momentum", direction, value, "Stochastic position inside the recent range.", min(1.0, abs(value - 50.0) / 35.0)))

    if count < 14:
        votes.append(_warmup("Williams %R 14", "Momentum", 14, count))
        votes.append(_warmup("CCI 14", "Momentum", 14, count))
    else:
        highest = max(highs[-14:])
        lowest = min(lows[-14:])
        williams = (highest - close) / max(highest - lowest, 1e-9) * -100.0
        williams_direction = TechnicalDirection.BULLISH if williams > -45 else TechnicalDirection.BEARISH if williams < -55 else TechnicalDirection.NEUTRAL
        votes.append(_vote("Williams %R 14", "Momentum", williams_direction, williams, "Momentum position inside the 14-bar range.", min(1.0, abs(williams + 50.0) / 35.0)))
        typical = [(item.high + item.low + item.close) / 3.0 for item in bars[-14:]]
        typical_mean = mean(typical)
        mean_deviation = mean(abs(value - typical_mean) for value in typical)
        cci = (typical[-1] - typical_mean) / max(0.015 * mean_deviation, 1e-9)
        cci_direction = TechnicalDirection.BULLISH if cci > 25 else TechnicalDirection.BEARISH if cci < -25 else TechnicalDirection.NEUTRAL
        votes.append(_vote("CCI 14", "Momentum", cci_direction, cci, "Commodity Channel Index around its zero line.", min(1.0, abs(cci) / 150.0)))

    if count < 11:
        votes.append(_warmup("ROC 10", "Momentum", 11, count))
        votes.append(_warmup("Momentum 10", "Momentum", 11, count))
    else:
        roc = (close / closes[-11] - 1.0) * 100.0 if closes[-11] else 0.0
        momentum = close - closes[-11]
        votes.append(_vote("ROC 10", "Momentum", TechnicalDirection.BULLISH if roc > 0.05 else TechnicalDirection.BEARISH if roc < -0.05 else TechnicalDirection.NEUTRAL, roc, "Ten-bar percentage rate of change.", min(1.0, abs(roc) / 0.8)))
        votes.append(_vote("Momentum 10", "Momentum", TechnicalDirection.BULLISH if momentum > price_scale * 0.1 else TechnicalDirection.BEARISH if momentum < -price_scale * 0.1 else TechnicalDirection.NEUTRAL, momentum, "Ten-bar absolute price momentum.", min(1.0, abs(momentum) / max(price_scale * 3.0, 1e-9))))

    if count < 20:
        votes.append(_warmup("Bollinger position 20/2", "Volatility", 20, count))
        votes.append(_warmup("Keltner channel 20", "Volatility", 20, count))
        votes.append(_warmup("Donchian breakout 20", "Volatility", 21, count))
    else:
        average = mean(closes[-20:])
        deviation = sqrt(mean((value - average) ** 2 for value in closes[-20:]))
        band_unit = max(2.0 * deviation, 1e-9)
        bollinger_position = (close - average) / band_unit
        bollinger_direction = TechnicalDirection.BULLISH if bollinger_position > 0.15 else TechnicalDirection.BEARISH if bollinger_position < -0.15 else TechnicalDirection.NEUTRAL
        votes.append(_vote("Bollinger position 20/2", "Volatility", bollinger_direction, bollinger_position, "Close position relative to the Bollinger midline and bands.", min(1.0, abs(bollinger_position))))
        keltner_mid = _ema(closes, 20)[-1]
        if atr is None or keltner_mid is None:
            votes.append(_warmup("Keltner channel 20", "Volatility", 20, count))
        else:
            upper = keltner_mid + 1.5 * atr
            lower = keltner_mid - 1.5 * atr
            keltner_direction = TechnicalDirection.BULLISH if close > upper else TechnicalDirection.BEARISH if close < lower else TechnicalDirection.NEUTRAL
            votes.append(_vote("Keltner channel 20", "Volatility", keltner_direction, close - keltner_mid, "Breakout beyond the ATR-based Keltner channel.", min(1.0, abs(close - keltner_mid) / max(1.5 * atr, 1e-9))))
        if count < 21:
            votes.append(_warmup("Donchian breakout 20", "Volatility", 21, count))
        else:
            prior_high = max(highs[-21:-1])
            prior_low = min(lows[-21:-1])
            donchian_direction = TechnicalDirection.BULLISH if close > prior_high else TechnicalDirection.BEARISH if close < prior_low else TechnicalDirection.NEUTRAL
            distance = close - prior_high if close > prior_high else prior_low - close if close < prior_low else 0.0
            votes.append(_vote("Donchian breakout 20", "Volatility", donchian_direction, distance, "Close versus the prior 20-bar high and low.", min(1.0, abs(distance) / max(price_scale, 1e-9))))

    if atr is None:
        votes.append(_warmup("ATR expansion 14", "Volatility", 15, count))
    else:
        move = close - previous_close
        atr_direction = TechnicalDirection.BULLISH if move > atr * 0.25 else TechnicalDirection.BEARISH if move < -atr * 0.25 else TechnicalDirection.NEUTRAL
        votes.append(_vote("ATR expansion 14", "Volatility", atr_direction, atr, "Latest directional move measured against ATR.", min(1.0, abs(move) / max(atr, 1e-9))))

    latest = bars[-1]
    heikin_close = (latest.open + latest.high + latest.low + latest.close) / 4.0
    heikin_open = (bars[-2].open + bars[-2].close) / 2.0 if count >= 2 else (latest.open + latest.close) / 2.0
    heikin_direction = TechnicalDirection.BULLISH if heikin_close > heikin_open else TechnicalDirection.BEARISH if heikin_close < heikin_open else TechnicalDirection.NEUTRAL
    votes.append(_vote("Heikin-Ashi trend", "Structure", heikin_direction, heikin_close - heikin_open, "Synthetic candle body direction.", min(1.0, abs(heikin_close - heikin_open) / max(price_scale, 1e-9))))

    if count < 2:
        votes.append(_warmup("Pivot position", "Structure", 2, count))
    else:
        prior = bars[-2]
        pivot = (prior.high + prior.low + prior.close) / 3.0
        votes.append(_compare("Pivot position", "Structure", close, pivot, "Close compared with the prior-bar pivot", price_scale, 2, count))

    session_bars = [item for item in bars if item.timestamp.date() == latest.timestamp.date()]
    session_volumes = [max(0.0, float(item.volume)) for item in session_bars]
    session_volume = sum(session_volumes)
    if session_volume <= 0:
        votes.append(_vote("Session VWAP", "Participation", TechnicalDirection.UNAVAILABLE, "NO INDEX VOLUME", "The latest NIFTY session has no usable traded volume.", 0.0))
    else:
        typical_prices = [(item.high + item.low + item.close) / 3.0 for item in session_bars]
        vwap = sum(price * volume for price, volume in zip(typical_prices, session_volumes)) / session_volume
        votes.append(_compare("Session VWAP", "Participation", close, vwap, "Close compared with session VWAP", price_scale, 1, count))

    total_volume = sum(volumes)
    if total_volume <= 0:
        votes.append(_vote("OBV slope", "Participation", TechnicalDirection.UNAVAILABLE, "NO INDEX VOLUME", "The NIFTY index candle feed has no usable traded volume.", 0.0))
    else:
        obv = [0.0]
        for prior_close, current_close, volume in zip(closes[:-1], closes[1:], volumes[1:]):
            obv.append(obv[-1] + volume if current_close > prior_close else obv[-1] - volume if current_close < prior_close else obv[-1])
        lookback = min(5, len(obv) - 1)
        slope = obv[-1] - obv[-1 - lookback] if lookback else 0.0
        recent_volume = sum(volumes[-max(lookback, 1):])
        obv_direction = TechnicalDirection.BULLISH if slope > 0 else TechnicalDirection.BEARISH if slope < 0 else TechnicalDirection.NEUTRAL
        votes.append(_vote("OBV slope", "Participation", obv_direction, slope, "Five-bar On-Balance Volume slope.", min(1.0, abs(slope) / max(recent_volume, 1e-9))))

    return tuple(votes)


def _catalogue() -> tuple[tuple[str, str, int], ...]:
    return (
        ("Close vs SMA 5", "Trend", 5),
        ("Close vs SMA 20", "Trend", 20),
        ("SMA 5 vs SMA 20", "Trend", 20),
        ("Close vs EMA 9", "Trend", 9),
        ("EMA 9 vs EMA 21", "Trend", 21),
        ("MACD line", "Trend", 26),
        ("MACD histogram", "Trend", 34),
        ("Supertrend 10/3", "Trend", 15),
        ("DMI / ADX 14", "Trend", 15),
        ("Aroon oscillator 14", "Trend", 14),
        ("RSI 14", "Momentum", 15),
        ("Stochastic %K 14", "Momentum", 14),
        ("Stochastic %D 3", "Momentum", 16),
        ("Williams %R 14", "Momentum", 14),
        ("CCI 14", "Momentum", 14),
        ("ROC 10", "Momentum", 11),
        ("Momentum 10", "Momentum", 11),
        ("Bollinger position 20/2", "Volatility", 20),
        ("Keltner channel 20", "Volatility", 20),
        ("Donchian breakout 20", "Volatility", 21),
        ("ATR expansion 14", "Volatility", 15),
        ("Heikin-Ashi trend", "Structure", 1),
        ("Pivot position", "Structure", 2),
        ("Session VWAP", "Participation", 1),
        ("OBV slope", "Participation", 2),
    )


def assess_technicals(
    candles: Iterable[IntradayCandle],
    *,
    minimum_coverage: float = 55.0,
) -> TechnicalAssessment:
    votes = indicator_votes(candles)
    available = [vote for vote in votes if vote.available]
    coverage = len(available) / max(len(votes), 1) * 100.0
    family_rows: list[dict[str, object]] = []
    family_directions: list[tuple[str, TechnicalDirection, float]] = []
    for family in ("Trend", "Momentum", "Volatility", "Structure", "Participation"):
        family_votes = [vote for vote in available if vote.family == family]
        if not family_votes:
            family_rows.append({"Family": family, "Direction": "UNAVAILABLE", "Bullish": 0, "Bearish": 0, "Neutral": 0, "Available": 0})
            continue
        bullish = sum(vote.strength for vote in family_votes if vote.direction == TechnicalDirection.BULLISH)
        bearish = sum(vote.strength for vote in family_votes if vote.direction == TechnicalDirection.BEARISH)
        neutral = sum(max(0.25, vote.strength) for vote in family_votes if vote.direction == TechnicalDirection.NEUTRAL)
        directional_total = bullish + bearish + neutral
        edge = (bullish - bearish) / max(directional_total, 1e-9)
        direction = TechnicalDirection.BULLISH if edge >= 0.12 else TechnicalDirection.BEARISH if edge <= -0.12 else TechnicalDirection.NEUTRAL
        strength = min(1.0, abs(edge))
        family_directions.append((family, direction, strength))
        family_rows.append({
            "Family": family,
            "Direction": direction.value,
            "Bullish": round(bullish, 2),
            "Bearish": round(bearish, 2),
            "Neutral": round(neutral, 2),
            "Available": len(family_votes),
        })

    bullish_families = sum(max(0.25, strength) for _, direction, strength in family_directions if direction == TechnicalDirection.BULLISH)
    bearish_families = sum(max(0.25, strength) for _, direction, strength in family_directions if direction == TechnicalDirection.BEARISH)
    neutral_families = sum(0.25 for _, direction, _ in family_directions if direction == TechnicalDirection.NEUTRAL)
    total = bullish_families + bearish_families + neutral_families
    bullish_score = bullish_families / max(total, 1e-9) * 100.0
    bearish_score = bearish_families / max(total, 1e-9) * 100.0
    blockers: list[str] = []
    if coverage < minimum_coverage:
        direction = TechnicalDirection.UNAVAILABLE
        setup_score = 0.0
        blockers.append(f"Technical indicator coverage is only {coverage:.0f}% (minimum {minimum_coverage:.0f}%).")
    elif bullish_score >= 55.0 and bullish_score - bearish_score >= 12.0:
        direction = TechnicalDirection.BULLISH
        setup_score = bullish_score
    elif bearish_score >= 55.0 and bearish_score - bullish_score >= 12.0:
        direction = TechnicalDirection.BEARISH
        setup_score = bearish_score
    else:
        direction = TechnicalDirection.NEUTRAL
        setup_score = max(bullish_score, bearish_score)
        blockers.append("Technical families do not have a clear directional majority.")

    return TechnicalAssessment(
        direction=direction,
        setup_score=round(setup_score, 1),
        bullish_score=round(bullish_score, 1),
        bearish_score=round(bearish_score, 1),
        coverage=round(coverage, 1),
        votes=votes,
        family_rows=tuple(family_rows),
        blockers=tuple(blockers),
    )


def technical_rows(assessment: TechnicalAssessment) -> list[dict[str, object]]:
    return [
        {
            "Family": vote.family,
            "Indicator": vote.name,
            "Signal": vote.direction.value,
            "Strength": round(vote.strength * 100.0, 1),
            "Value": vote.value,
            "Reason": vote.reason,
        }
        for vote in assessment.votes
    ]
