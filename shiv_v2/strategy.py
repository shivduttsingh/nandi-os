from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from statistics import mean
from typing import Iterable

from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.models import OptionChainSnapshot
from shiv_v1.engine import Direction, MarketRegime, MultiTimeframeAssessment, RegimeAssessment, ShivDecision


class SessionBucket(str, Enum):
    OPENING = "OPENING"
    MORNING = "MORNING"
    MIDDAY = "MIDDAY"
    AFTERNOON = "AFTERNOON"
    CLOSING = "CLOSING"
    FINAL_MINUTES = "FINAL MINUTES"


class VolatilityBand(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RegimePolicy:
    minimum_quality: float
    minimum_mtf: float
    minimum_persistence: int
    maximum_spread_pct: float
    maximum_chase_pct: float
    stop_points: float
    target_1_points: float
    target_2_points: float
    allow_entries: bool = True


@dataclass(frozen=True)
class SessionContext:
    bucket: SessionBucket
    quality_adjustment: float
    mtf_adjustment: float
    allow_new_entries: bool
    reason: str


@dataclass(frozen=True)
class VolatilityContext:
    band: VolatilityBand
    atr_points: float | None
    atr_pct: float | None
    atm_iv: float | None
    days_to_expiry: int | None
    expiry_label: str
    quality_adjustment: float
    stop_multiplier: float
    target_multiplier: float
    reason: str


@dataclass(frozen=True)
class PatternAssessment:
    label: str
    side: str
    confidence: float
    neckline: float | None
    confirmed: bool
    reason: str


@dataclass(frozen=True)
class BreakoutAssessment:
    blocked: bool
    status: str
    reason: str


@dataclass(frozen=True)
class SetupDecay:
    blocked: bool
    status: str
    age_bars: float
    premium_move_pct: float
    reason: str


@dataclass(frozen=True)
class StrikeCandidate:
    strike: float
    offset: int
    score: float
    premium: float
    spread_pct: float | None
    volume_ratio: float | None
    responsiveness_pct: float
    open_interest: float


@dataclass(frozen=True)
class StrikeSelection:
    selected: StrikeCandidate | None
    candidates: tuple[StrikeCandidate, ...]
    reason: str


@dataclass(frozen=True)
class AdaptiveEntryPlan:
    status: str
    strike: float | None
    entry: float | None
    stop: float | None
    target_1: float | None
    target_2: float | None
    reason: str


@dataclass(frozen=True)
class AdaptiveExitPlan:
    status: str
    stop: float
    trail_stop: float | None
    unrealized_points: float
    reason: str


@dataclass(frozen=True)
class V2Decision:
    status: str
    side: str
    setup_quality: float
    required_quality: float
    required_mtf: float
    policy: RegimePolicy
    session: SessionContext
    volatility: VolatilityContext
    pattern: PatternAssessment
    breakout: BreakoutAssessment
    decay: SetupDecay
    strike_selection: StrikeSelection
    entry_plan: AdaptiveEntryPlan
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    signature: str
    base: ShivDecision

    @property
    def directional_confirmation(self) -> bool:
        return self.status.startswith(("CONFIRM", "STRONG", "A+"))

    @property
    def actionable(self) -> bool:
        return self.directional_confirmation and self.entry_plan.status == "ENTRY READY"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _pct(start: float, end: float) -> float:
    return (end / start - 1.0) * 100.0 if start > 0 else 0.0


def policy_for(regime: MarketRegime, interval_minutes: int) -> RegimePolicy:
    """Return deterministic regime-specific gates; these are not performance claims."""
    if regime in {MarketRegime.TREND_UP, MarketRegime.TREND_DOWN}:
        policy = RegimePolicy(68.0, 70.0, 2, 3.0, 6.0, 3.5, 8.0, 12.0)
    elif regime in {MarketRegime.BREAKOUT_UP, MarketRegime.BREAKOUT_DOWN}:
        policy = RegimePolicy(72.0, 75.0, 2, 2.7, 5.0, 3.5, 8.0, 11.0)
    elif regime in {MarketRegime.REVERSAL_UP, MarketRegime.REVERSAL_DOWN}:
        policy = RegimePolicy(78.0, 78.0, 3, 2.5, 4.0, 3.0, 6.0, 8.0)
    elif regime == MarketRegime.SIDEWAYS:
        policy = RegimePolicy(90.0, 90.0, 4, 2.0, 2.5, 2.5, 4.0, 6.0, allow_entries=False)
    else:
        policy = RegimePolicy(90.0, 90.0, 4, 2.0, 3.0, 3.0, 5.0, 7.0, allow_entries=False)

    # Faster bars are noisier. Higher-timeframe bars receive a small relaxation.
    quality = policy.minimum_quality
    mtf = policy.minimum_mtf
    persistence = policy.minimum_persistence
    if interval_minutes <= 2:
        quality += 6.0
        mtf += 5.0
        persistence = max(persistence, 3)
    elif interval_minutes == 3:
        quality += 3.0
        mtf += 2.0
    elif interval_minutes >= 15:
        quality -= 2.0
        mtf -= 2.0
    return RegimePolicy(
        minimum_quality=_clamp(quality, 60.0, 92.0),
        minimum_mtf=_clamp(mtf, 55.0, 92.0),
        minimum_persistence=persistence,
        maximum_spread_pct=policy.maximum_spread_pct,
        maximum_chase_pct=policy.maximum_chase_pct,
        stop_points=policy.stop_points,
        target_1_points=policy.target_1_points,
        target_2_points=policy.target_2_points,
        allow_entries=policy.allow_entries,
    )


def classify_session(now: datetime) -> SessionContext:
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 35:
        return SessionContext(SessionBucket.OPENING, 5.0, 5.0, True, "Opening volatility requires stronger confirmation.")
    if minutes < 11 * 60 + 30:
        return SessionContext(SessionBucket.MORNING, 0.0, 0.0, True, "Normal morning confirmation rules apply.")
    if minutes < 13 * 60 + 30:
        return SessionContext(SessionBucket.MIDDAY, 7.0, 5.0, True, "Midday compression is filtered more aggressively.")
    if minutes < 14 * 60 + 45:
        return SessionContext(SessionBucket.AFTERNOON, 2.0, 2.0, True, "Afternoon setups need a small confirmation premium.")
    if minutes < 15 * 60 + 20:
        return SessionContext(SessionBucket.CLOSING, 5.0, 3.0, True, "Closing volatility requires stronger evidence and tighter management.")
    return SessionContext(SessionBucket.FINAL_MINUTES, 12.0, 8.0, False, "No new option entry is opened in the final ten minutes.")


def _atr(candles: Iterable[IntradayCandle], period: int = 14) -> float | None:
    items = list(candles)
    if len(items) < 2:
        return None
    window = items[-min(len(items), period + 1):]
    true_ranges: list[float] = []
    for previous, current in zip(window[:-1], window[1:]):
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return mean(true_ranges) if true_ranges else None


def volatility_context(
    candles: Iterable[IntradayCandle],
    *,
    atm_iv: float | None,
    expiry: str,
    now: datetime,
) -> VolatilityContext:
    items = list(candles)
    atr = _atr(items)
    latest = items[-1].close if items else 0.0
    atr_pct = atr / latest * 100.0 if atr is not None and latest > 0 else None
    if atr_pct is None:
        band = VolatilityBand.UNAVAILABLE
    elif atr_pct < 0.055:
        band = VolatilityBand.LOW
    elif atr_pct < 0.14:
        band = VolatilityBand.NORMAL
    elif atr_pct < 0.24:
        band = VolatilityBand.HIGH
    else:
        band = VolatilityBand.EXTREME

    expiry_date: date | None = None
    for pattern in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            expiry_date = datetime.strptime(expiry, pattern).date()
            break
        except ValueError:
            continue
    dte = max(0, (expiry_date - now.date()).days) if expiry_date else None
    if dte == 0:
        expiry_label = "EXPIRY DAY"
    elif dte == 1:
        expiry_label = "NEAR EXPIRY"
    elif dte is None:
        expiry_label = "EXPIRY UNKNOWN"
    else:
        expiry_label = f"{dte} DTE"

    quality_adjustment = 0.0
    stop_multiplier = 1.0
    target_multiplier = 1.0
    if band == VolatilityBand.LOW:
        quality_adjustment += 3.0
        stop_multiplier = 0.9
        target_multiplier = 0.8
    elif band == VolatilityBand.HIGH:
        quality_adjustment += 4.0
        stop_multiplier = 1.1
        target_multiplier = 1.15
    elif band == VolatilityBand.EXTREME:
        quality_adjustment += 8.0
        stop_multiplier = 1.15
        target_multiplier = 1.15
    if dte == 0:
        quality_adjustment += 5.0
        target_multiplier *= 0.9
    elif dte == 1:
        quality_adjustment += 2.0

    iv_text = f"; ATM IV {atm_iv:.1f}" if atm_iv is not None and isfinite(atm_iv) and atm_iv > 0 else ""
    reason = f"ATR regime is {band.value.lower()} ({atr_pct:.3f}% of NIFTY)" if atr_pct is not None else "ATR context is unavailable"
    reason += f"; {expiry_label.lower()}{iv_text}."
    return VolatilityContext(
        band=band,
        atr_points=round(atr, 2) if atr is not None else None,
        atr_pct=round(atr_pct, 3) if atr_pct is not None else None,
        atm_iv=round(float(atm_iv), 2) if atm_iv is not None and isfinite(atm_iv) and atm_iv > 0 else None,
        days_to_expiry=dte,
        expiry_label=expiry_label,
        quality_adjustment=quality_adjustment,
        stop_multiplier=stop_multiplier,
        target_multiplier=target_multiplier,
        reason=reason,
    )


def _pivot_lows(items: list[IntradayCandle]) -> list[int]:
    return [index for index in range(1, len(items) - 1) if items[index].low <= items[index - 1].low and items[index].low <= items[index + 1].low]


def _pivot_highs(items: list[IntradayCandle]) -> list[int]:
    return [index for index in range(1, len(items) - 1) if items[index].high >= items[index - 1].high and items[index].high >= items[index + 1].high]


def detect_mw_pattern(candles: Iterable[IntradayCandle], *, tolerance_pct: float = 0.18) -> PatternAssessment:
    """Detect confirmed/developing M/W structures from completed candles only."""
    items = list(candles)[-30:]
    if len(items) < 8:
        return PatternAssessment("NONE", "NONE", 0.0, None, False, "Not enough completed candles for M/W structure.")

    lows = _pivot_lows(items)
    highs = _pivot_highs(items)
    best: PatternAssessment | None = None

    for first, second in zip(lows[:-1], lows[1:]):
        if second - first < 3:
            continue
        low_a, low_b = items[first].low, items[second].low
        average_low = (low_a + low_b) / 2.0
        difference = abs(low_a - low_b) / average_low * 100.0 if average_low > 0 else 999.0
        if difference > tolerance_pct:
            continue
        neckline = max(item.high for item in items[first: second + 1])
        rebound = (neckline / average_low - 1.0) * 100.0 if average_low > 0 else 0.0
        if rebound < 0.08:
            continue
        confirmed = items[-1].close > neckline
        confidence = _clamp(55.0 + rebound * 80.0 - difference * 40.0 + (15.0 if confirmed else 0.0), 0.0, 100.0)
        candidate = PatternAssessment(
            "W CONFIRMED" if confirmed else "W DEVELOPING",
            "CE",
            round(confidence, 1),
            round(neckline, 2),
            confirmed,
            "Two comparable swing lows formed and the neckline was broken." if confirmed else "Two comparable swing lows formed; neckline confirmation is still pending.",
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    for first, second in zip(highs[:-1], highs[1:]):
        if second - first < 3:
            continue
        high_a, high_b = items[first].high, items[second].high
        average_high = (high_a + high_b) / 2.0
        difference = abs(high_a - high_b) / average_high * 100.0 if average_high > 0 else 999.0
        if difference > tolerance_pct:
            continue
        neckline = min(item.low for item in items[first: second + 1])
        drop = (1.0 - neckline / average_high) * 100.0 if average_high > 0 else 0.0
        if drop < 0.08:
            continue
        confirmed = items[-1].close < neckline
        confidence = _clamp(55.0 + drop * 80.0 - difference * 40.0 + (15.0 if confirmed else 0.0), 0.0, 100.0)
        candidate = PatternAssessment(
            "M CONFIRMED" if confirmed else "M DEVELOPING",
            "PE",
            round(confidence, 1),
            round(neckline, 2),
            confirmed,
            "Two comparable swing highs formed and the neckline was broken." if confirmed else "Two comparable swing highs formed; neckline confirmation is still pending.",
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    return best or PatternAssessment("NONE", "NONE", 0.0, None, False, "No clean M/W structure is present in the current window.")


def false_breakout_check(
    side: str,
    nifty_candles: Iterable[IntradayCandle],
    option_candles: Iterable[IntradayCandle],
) -> BreakoutAssessment:
    nifty = list(nifty_candles)
    option = list(option_candles)
    if side not in {"CE", "PE"} or len(nifty) < 4 or len(option) < 3:
        return BreakoutAssessment(False, "UNAVAILABLE", "Not enough completed candles for false-breakout validation.")

    prior_nifty = nifty[-4:-1]
    latest_nifty = nifty[-1]
    prior_option = option[-2]
    latest_option = option[-1]
    prior_high = max(item.high for item in prior_nifty)
    prior_low = min(item.low for item in prior_nifty)

    if side == "CE":
        prior_break = nifty[-2].close > max(item.high for item in nifty[-4:-2])
        fell_back = prior_break and latest_nifty.close <= prior_high
        option_failed = latest_nifty.close > prior_high and latest_option.close <= prior_option.close
        if fell_back:
            return BreakoutAssessment(True, "FALSE BREAKOUT", "NIFTY broke upward and then closed back inside the prior range.")
        if option_failed:
            return BreakoutAssessment(True, "PREMIUM DIVERGENCE", "NIFTY is above the prior range but the selected CE premium did not follow through.")
    else:
        prior_break = nifty[-2].close < min(item.low for item in nifty[-4:-2])
        fell_back = prior_break and latest_nifty.close >= prior_low
        option_failed = latest_nifty.close < prior_low and latest_option.close <= prior_option.close
        if fell_back:
            return BreakoutAssessment(True, "FALSE BREAKDOWN", "NIFTY broke downward and then closed back inside the prior range.")
        if option_failed:
            return BreakoutAssessment(True, "PREMIUM DIVERGENCE", "NIFTY is below the prior range but the selected PE premium did not follow through.")
    return BreakoutAssessment(False, "PASSED", "No immediate NIFTY/option false-breakout conflict is visible.")


def setup_decay(
    *,
    first_seen_at: datetime | None,
    now: datetime,
    interval_minutes: int,
    first_premium: float | None,
    current_premium: float | None,
    maximum_chase_pct: float,
    maximum_age_bars: int = 4,
) -> SetupDecay:
    if first_seen_at is None or first_premium is None or current_premium is None or first_premium <= 0:
        return SetupDecay(False, "FRESH", 0.0, 0.0, "This is a fresh directional candidate.")
    age_minutes = max(0.0, (now - first_seen_at).total_seconds() / 60.0)
    age_bars = age_minutes / max(1, interval_minutes)
    premium_move = _pct(first_premium, current_premium)
    if premium_move >= maximum_chase_pct:
        return SetupDecay(True, "MISSED / DO NOT CHASE", round(age_bars, 1), round(premium_move, 2), f"Premium has already advanced {premium_move:.1f}% since the setup first appeared.")
    if age_bars > maximum_age_bars:
        return SetupDecay(True, "EXPIRED", round(age_bars, 1), round(premium_move, 2), f"The candidate is {age_bars:.1f} primary bars old without a clean entry trigger.")
    return SetupDecay(False, "ACTIVE", round(age_bars, 1), round(premium_move, 2), "The setup remains within the allowed age and chase window.")


def _snapshot_row(snapshot: OptionChainSnapshot, strike: float):
    return next((row for row in snapshot.rows if abs(row.strike - strike) < 0.01), None)


def select_option_strike(
    snapshot: OptionChainSnapshot,
    window: Iterable[OptionStrikeCandles],
    side: str,
) -> StrikeSelection:
    if side not in {"CE", "PE"}:
        return StrikeSelection(None, tuple(), "No directional side is available for strike selection.")
    candidates: list[StrikeCandidate] = []
    for item in window:
        candles = list(item.ce_candles if side == "CE" else item.pe_candles)
        if not candles:
            continue
        row = _snapshot_row(snapshot, item.strike)
        leg = (row.ce if side == "CE" else row.pe) if row is not None else None
        bid = float(leg.bid) if leg else 0.0
        ask = float(leg.ask) if leg else 0.0
        spread = None
        if bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread = (ask - bid) / mid * 100.0 if mid > 0 else None
        volumes = [max(0.0, candle.volume) for candle in candles[-4:]]
        volume_ratio = None
        if len(volumes) >= 3 and sum(volumes[:-1]) > 0:
            baseline = mean(volumes[:-1])
            volume_ratio = volumes[-1] / baseline if baseline > 0 else None
        responsiveness = _pct(candles[-min(4, len(candles))].close, candles[-1].close)
        oi = float(leg.oi) if leg else float(candles[-1].open_interest)
        premium = float(leg.ltp) if leg and leg.ltp > 0 else float(candles[-1].close)

        if spread is None:
            liquidity_score = 8.0
        elif spread <= 1.0:
            liquidity_score = 25.0
        elif spread <= 2.0:
            liquidity_score = 20.0
        elif spread <= 3.0:
            liquidity_score = 12.0
        else:
            liquidity_score = 0.0
        volume_score = _clamp((volume_ratio or 0.5) / 1.5 * 20.0, 0.0, 20.0)
        responsiveness_score = _clamp(max(0.0, responsiveness) / 4.0 * 30.0, 0.0, 30.0)
        oi_score = 10.0 if oi > 0 else 0.0
        proximity_score = {0: 15.0, -1: 12.0, 1: 12.0, -2: 7.0, 2: 7.0}.get(item.offset, 5.0)
        score = liquidity_score + volume_score + responsiveness_score + oi_score + proximity_score
        candidates.append(StrikeCandidate(
            strike=float(item.strike),
            offset=int(item.offset),
            score=round(score, 1),
            premium=round(premium, 2),
            spread_pct=round(spread, 2) if spread is not None else None,
            volume_ratio=round(volume_ratio, 2) if volume_ratio is not None else None,
            responsiveness_pct=round(responsiveness, 2),
            open_interest=round(oi, 2),
        ))
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    selected = candidates[0] if candidates else None
    if selected is None:
        reason = "No usable option contract exists inside ATM ±2."
    else:
        reason = f"Selected {selected.strike:.0f} {side} (ATM {selected.offset:+d}) from liquidity, volume, premium response, OI and ATM proximity."
    return StrikeSelection(selected, tuple(candidates), reason)


def _adaptive_levels(policy: RegimePolicy, volatility: VolatilityContext) -> tuple[float, float, float]:
    stop = _clamp(policy.stop_points * volatility.stop_multiplier, 2.0, 5.0)
    target_1 = _clamp(policy.target_1_points * volatility.target_multiplier, 4.0, 14.0)
    target_2 = _clamp(policy.target_2_points * volatility.target_multiplier, target_1 + 1.0, 18.0)
    return round(stop, 2), round(target_1, 2), round(target_2, 2)


def adaptive_entry_plan(
    *,
    side: str,
    selection: StrikeSelection,
    option_candles: Iterable[IntradayCandle],
    confirmed: bool,
    policy: RegimePolicy,
    volatility: VolatilityContext,
    breakout: BreakoutAssessment,
    decay: SetupDecay,
) -> AdaptiveEntryPlan:
    selected = selection.selected
    items = list(option_candles)
    if side not in {"CE", "PE"} or selected is None or len(items) < 3:
        return AdaptiveEntryPlan("WAIT", selected.strike if selected else None, None, None, None, None, "No executable selected-option entry is available.")
    current = float(items[-1].close)
    stop_points, target_1_points, target_2_points = _adaptive_levels(policy, volatility)
    if decay.blocked:
        return AdaptiveEntryPlan(decay.status, selected.strike, current, current - stop_points, current + target_1_points, current + target_2_points, decay.reason)
    if breakout.blocked:
        return AdaptiveEntryPlan("WAIT — FALSE BREAKOUT", selected.strike, current, current - stop_points, current + target_1_points, current + target_2_points, breakout.reason)
    if not confirmed:
        return AdaptiveEntryPlan("WAIT — V2 GATE", selected.strike, current, current - stop_points, current + target_1_points, current + target_2_points, "The adaptive V2 confirmation gate has not cleared yet.")

    prior = items[-3:-1]
    latest = items[-1]
    prior_high = max(item.high for item in prior)
    bullish_body = latest.close > latest.open
    breakout_close = latest.close > prior_high and bullish_body
    reclaim = latest.low <= prior[-1].close and latest.close >= prior[-1].close and bullish_body
    if not (breakout_close or reclaim):
        return AdaptiveEntryPlan("WAIT — ENTRY TRIGGER", selected.strike, current, current - stop_points, current + target_1_points, current + target_2_points, "V2 direction is confirmed, but the selected premium has not printed a completed breakout/reclaim trigger.")
    trigger = "premium breakout" if breakout_close else "pullback reclaim"
    return AdaptiveEntryPlan(
        "ENTRY READY",
        selected.strike,
        round(current, 2),
        round(current - stop_points, 2),
        round(current + target_1_points, 2),
        round(current + target_2_points, 2),
        f"Adaptive gates cleared and the selected {side} printed a completed {trigger}.",
    )


def manage_adaptive_exit(
    entry: float,
    current: float,
    high_since_entry: float,
    *,
    regime: MarketRegime,
    volatility: VolatilityContext,
    minutes_held: float = 0.0,
) -> AdaptiveExitPlan:
    policy = policy_for(regime, 5)
    stop_points, target_1, target_2 = _adaptive_levels(policy, volatility)
    profit = current - entry
    if current <= entry - stop_points:
        return AdaptiveExitPlan("EXIT — STOP", round(entry - stop_points, 2), None, round(profit, 2), "Adaptive initial paper stop was reached.")
    if profit >= target_2:
        return AdaptiveExitPlan("EXIT — TARGET 2", round(entry, 2), round(max(entry + target_1 * 0.7, high_since_entry - 2.0), 2), round(profit, 2), "Adaptive second objective was reached.")
    if profit >= target_1:
        trail_gap = 2.5 if regime in {MarketRegime.TREND_UP, MarketRegime.TREND_DOWN} else 1.8
        trail = max(entry + target_1 * 0.45, high_since_entry - trail_gap)
        return AdaptiveExitPlan("BOOK / TRAIL RUNNER", round(trail, 2), round(trail, 2), round(profit, 2), "First adaptive objective was reached; protect a runner according to regime.")
    breakeven_trigger = min(4.0, target_1 * 0.55)
    if profit >= breakeven_trigger:
        trail = entry if profit < target_1 * 0.8 else entry + max(1.0, target_1 * 0.25)
        return AdaptiveExitPlan("BREAKEVEN / PROTECT", round(trail, 2), round(trail, 2), round(profit, 2), "Enough progress exists to reduce paper risk.")
    if minutes_held >= 25 and profit <= 1.0 and regime in {MarketRegime.REVERSAL_UP, MarketRegime.REVERSAL_DOWN}:
        return AdaptiveExitPlan("EXIT — TIME DECAY", round(entry - stop_points, 2), None, round(profit, 2), "Reversal setup failed to progress within the intended holding window.")
    return AdaptiveExitPlan("HOLD", round(entry - stop_points, 2), None, round(profit, 2), "Adaptive stop/target/time conditions have not been reached.")


def _selected_candles(window: Iterable[OptionStrikeCandles], selection: StrikeSelection, side: str) -> tuple[IntradayCandle, ...]:
    selected = selection.selected
    if selected is None:
        return tuple()
    series = next((item for item in window if abs(item.strike - selected.strike) < 0.01), None)
    if series is None:
        return tuple()
    return series.ce_candles if side == "CE" else series.pe_candles


def build_v2_decision(
    *,
    base: ShivDecision,
    primary_regime: RegimeAssessment,
    mtf: MultiTimeframeAssessment,
    snapshot: OptionChainSnapshot,
    option_window: Iterable[OptionStrikeCandles],
    primary_nifty: Iterable[IntradayCandle],
    now: datetime,
    first_seen_at: datetime | None,
    first_premium: float | None,
    expiry: str,
) -> V2Decision:
    side = base.side if base.side in {"CE", "PE"} else "NONE"
    policy = policy_for(primary_regime.regime, max(1, int(getattr(base, "entry_plan", None) is not None and 5 or 5)))
    # The caller sets the real primary timeframe into the signature via base.signature; infer it safely below.
    interval = 5
    for part in base.signature.split("|"):
        if part.startswith("tf="):
            try:
                interval = int(part.split("=", 1)[1])
            except ValueError:
                pass
            break
    policy = policy_for(primary_regime.regime, interval)
    session = classify_session(now)

    rows = sorted(snapshot.rows, key=lambda row: abs(row.strike - snapshot.spot))
    atm_row = rows[0] if rows else None
    atm_leg = None
    if atm_row is not None and side in {"CE", "PE"}:
        atm_leg = atm_row.ce if side == "CE" else atm_row.pe
    atm_iv = float(atm_leg.iv) if atm_leg is not None and atm_leg.iv > 0 else None
    volatility = volatility_context(primary_nifty, atm_iv=atm_iv, expiry=expiry, now=now)
    pattern = detect_mw_pattern(primary_nifty)
    selection = select_option_strike(snapshot, option_window, side)
    option_candles = _selected_candles(option_window, selection, side)
    breakout = false_breakout_check(side, primary_nifty, option_candles)
    current_premium = option_candles[-1].close if option_candles else None
    decay = setup_decay(
        first_seen_at=first_seen_at,
        now=now,
        interval_minutes=interval,
        first_premium=first_premium,
        current_premium=current_premium,
        maximum_chase_pct=policy.maximum_chase_pct,
    )

    required_quality = policy.minimum_quality + session.quality_adjustment + volatility.quality_adjustment
    required_mtf = policy.minimum_mtf + session.mtf_adjustment
    reasons: list[str] = [session.reason, volatility.reason, selection.reason]
    blockers: list[str] = []

    if not policy.allow_entries:
        blockers.append(f"{primary_regime.regime.value} policy blocks directional option entries.")
    if not session.allow_new_entries:
        blockers.append(session.reason)
    if side not in {"CE", "PE"}:
        blockers.append("No CE/PE candidate has enough cross-engine directional edge.")
    if base.setup_quality < required_quality:
        blockers.append(f"Setup quality {base.setup_quality:.1f} is below the adaptive requirement {required_quality:.1f}.")
    if mtf.agreement_score < required_mtf:
        blockers.append(f"MTF agreement {mtf.agreement_score:.0f}% is below the adaptive requirement {required_mtf:.0f}%.")
    if base.persistence_count < policy.minimum_persistence:
        blockers.append(f"Persistence {base.persistence_count}/{policy.minimum_persistence} is not complete for this regime/timeframe.")
    if selection.selected is None or selection.selected.score < 50.0:
        blockers.append("No ATM/near-ATM contract passed the V2 option-quality selector.")
    elif selection.selected.spread_pct is not None and selection.selected.spread_pct > policy.maximum_spread_pct:
        blockers.append(f"Selected option spread {selection.selected.spread_pct:.2f}% exceeds the {policy.maximum_spread_pct:.2f}% regime limit.")
    if breakout.blocked:
        blockers.append(breakout.reason)
    if decay.blocked:
        blockers.append(decay.reason)

    if pattern.side == side and pattern.confirmed:
        reduction = min(4.0, pattern.confidence / 25.0)
        required_quality = max(60.0, required_quality - reduction)
        reasons.append(f"{pattern.label} supports {side}; required quality is reduced by {reduction:.1f} points, not replaced.")
    elif pattern.side in {"CE", "PE"} and pattern.side != side and pattern.confidence >= 65.0:
        blockers.append(f"Opposing {pattern.label} structure conflicts with the {side} candidate.")
    elif pattern.side == side:
        reasons.append(f"{pattern.label} is developing but is not counted as confirmed evidence yet.")

    # Re-check quality after any confirmed-pattern reduction.
    blockers = [item for item in blockers if not item.startswith("Setup quality ")]
    if base.setup_quality < required_quality:
        blockers.append(f"Setup quality {base.setup_quality:.1f} is below the adaptive requirement {required_quality:.1f}.")

    confirmed = not blockers
    if decay.blocked:
        status = decay.status
    elif blockers:
        status = "NO TRADE" if any(
            token in " ".join(blockers).upper()
            for token in ("BLOCKS", "FALSE", "CONFLICT", "SPREAD", "FINAL", "NO CE/PE", "EXCEEDS")
        ) else "WAIT"
    elif base.setup_quality >= max(88.0, required_quality + 10.0) and base.persistence_count >= policy.minimum_persistence + 1:
        status = f"A+ {side}"
    elif base.setup_quality >= max(80.0, required_quality + 5.0):
        status = f"STRONG {side}"
    else:
        status = f"CONFIRM {side}"

    entry = adaptive_entry_plan(
        side=side,
        selection=selection,
        option_candles=option_candles,
        confirmed=confirmed,
        policy=policy,
        volatility=volatility,
        breakout=breakout,
        decay=decay,
    )
    if pattern.label != "NONE":
        reasons.append(pattern.reason)
    reasons.append(breakout.reason)
    if not decay.blocked:
        reasons.append(decay.reason)

    selected_strike = selection.selected.strike if selection.selected else 0.0
    selected_offset = selection.selected.offset if selection.selected else 99
    signature = "|".join((
        base.signature,
        f"session={session.bucket.value}",
        f"vol={volatility.band.value}",
        f"pattern={pattern.label}",
        f"strike_offset={selected_offset}",
        f"strike={selected_strike:.0f}",
    ))
    return V2Decision(
        status=status,
        side=side,
        setup_quality=base.setup_quality,
        required_quality=round(required_quality, 1),
        required_mtf=round(required_mtf, 1),
        policy=policy,
        session=session,
        volatility=volatility,
        pattern=pattern,
        breakout=breakout,
        decay=decay,
        strike_selection=selection,
        entry_plan=entry,
        reasons=tuple(dict.fromkeys(reasons))[:10],
        blockers=tuple(dict.fromkeys(blockers))[:10],
        signature=signature,
        base=base,
    )
