from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Iterable, Mapping

from nandi_oi.models import IntradayCandle
from nandi_v2.atm_strategy import ATMConfirmationAssessment, ATMConfirmationSignal
from nandi_v2.strike_window_strategy import StrikeWindowAssessment, StrikeWindowSignal


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"


class MarketRegime(str, Enum):
    TREND_UP = "TRENDING UP"
    TREND_DOWN = "TRENDING DOWN"
    BREAKOUT_UP = "BREAKOUT UP"
    BREAKOUT_DOWN = "BREAKOUT DOWN"
    REVERSAL_UP = "REVERSAL UP"
    REVERSAL_DOWN = "REVERSAL DOWN"
    SIDEWAYS = "SIDEWAYS"
    UNAVAILABLE = "UNAVAILABLE"


class SetupStage(str, Enum):
    NEUTRAL = "NEUTRAL"
    CE_DEVELOPING = "CE DEVELOPING"
    PE_DEVELOPING = "PE DEVELOPING"
    CE_READY = "CE READY"
    PE_READY = "PE READY"
    CONFIRM_CE = "CONFIRM CE"
    CONFIRM_PE = "CONFIRM PE"
    STRONG_CE = "STRONG CE"
    STRONG_PE = "STRONG PE"
    A_PLUS_CE = "A+ CE"
    A_PLUS_PE = "A+ PE"
    NO_TRADE = "NO TRADE"


@dataclass(frozen=True)
class RegimeAssessment:
    regime: MarketRegime
    direction: Direction
    efficiency: float
    range_pct: float
    net_change_pct: float
    reason: str


@dataclass(frozen=True)
class TimeframeAssessment:
    interval_minutes: int
    regime: MarketRegime
    direction: Direction
    strength: float
    efficiency: float
    net_change_pct: float
    reason: str


@dataclass(frozen=True)
class MultiTimeframeAssessment:
    direction: Direction
    agreement_score: float
    bullish_weight: float
    bearish_weight: float
    neutral_weight: float
    conflict: bool
    rows: tuple[TimeframeAssessment, ...]
    reason: str


@dataclass(frozen=True)
class SimilarityStats:
    sample_size: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    average_points: float | None = None
    status: str = "UNVALIDATED"


@dataclass(frozen=True)
class EntryPlan:
    status: str
    strike: float | None
    entry: float | None
    stop: float | None
    target_1: float | None
    target_2: float | None
    initial_risk_points: float | None
    reason: str


@dataclass(frozen=True)
class ExitPlan:
    status: str
    stop: float
    trail_stop: float | None
    unrealized_points: float
    reason: str


@dataclass(frozen=True)
class ShivDecision:
    stage: SetupStage
    side: str
    setup_quality: float
    regime: MarketRegime
    mtf_direction: Direction
    mtf_agreement: float
    persistence_count: int
    component_scores: tuple[tuple[str, float], ...]
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    entry_plan: EntryPlan
    similarity: SimilarityStats
    signature: str

    @property
    def actionable(self) -> bool:
        return self.stage in {
            SetupStage.CONFIRM_CE,
            SetupStage.CONFIRM_PE,
            SetupStage.STRONG_CE,
            SetupStage.STRONG_PE,
            SetupStage.A_PLUS_CE,
            SetupStage.A_PLUS_PE,
        }


def _pct(start: float, end: float) -> float:
    return (end / start - 1.0) * 100.0 if start > 0 else 0.0


def trend_efficiency(candles: Iterable[IntradayCandle], lookback: int = 10) -> float:
    items = list(candles)
    window = items[-max(2, lookback + 1):]
    if len(window) < 2:
        return 0.0
    net = abs(window[-1].close - window[0].close)
    path = sum(abs(right.close - left.close) for left, right in zip(window[:-1], window[1:]))
    return net / path if path > 0 else 0.0


def classify_market_regime(
    candles: Iterable[IntradayCandle],
    *,
    lookback: int = 12,
) -> RegimeAssessment:
    """Classify completed NIFTY candles without EMA or forward-looking data."""
    items = list(candles)
    if len(items) < 6:
        return RegimeAssessment(
            MarketRegime.UNAVAILABLE,
            Direction.UNAVAILABLE,
            0.0,
            0.0,
            0.0,
            "Needs at least six completed candles for regime classification.",
        )

    window = items[-min(len(items), lookback):]
    latest = window[-1]
    previous = window[:-1]
    efficiency = trend_efficiency(window, min(10, len(window) - 1))
    high = max(item.high for item in window)
    low = min(item.low for item in window)
    midpoint = mean(item.close for item in window)
    range_pct = (high - low) / midpoint * 100.0 if midpoint > 0 else 0.0
    net_change = _pct(window[0].close, latest.close)

    prior_high = max(item.high for item in previous)
    prior_low = min(item.low for item in previous)
    breakout_up = latest.close > prior_high
    breakout_down = latest.close < prior_low

    recent = window[-4:]
    higher_highs = sum(right.high > left.high for left, right in zip(recent[:-1], recent[1:]))
    higher_lows = sum(right.low >= left.low for left, right in zip(recent[:-1], recent[1:]))
    lower_highs = sum(right.high <= left.high for left, right in zip(recent[:-1], recent[1:]))
    lower_lows = sum(right.low < left.low for left, right in zip(recent[:-1], recent[1:]))
    bullish_structure = higher_highs + higher_lows >= 4
    bearish_structure = lower_highs + lower_lows >= 4

    prior_anchor = window[max(0, len(window) - 6)].close
    prior_move = _pct(window[0].close, prior_anchor)
    recent_move = _pct(recent[0].close, recent[-1].close)

    if breakout_up and efficiency >= 0.35:
        return RegimeAssessment(
            MarketRegime.BREAKOUT_UP,
            Direction.BULLISH,
            round(efficiency, 3),
            round(range_pct, 3),
            round(net_change, 3),
            "Latest completed candle closed above the prior range with directional efficiency.",
        )
    if breakout_down and efficiency >= 0.35:
        return RegimeAssessment(
            MarketRegime.BREAKOUT_DOWN,
            Direction.BEARISH,
            round(efficiency, 3),
            round(range_pct, 3),
            round(net_change, 3),
            "Latest completed candle closed below the prior range with directional efficiency.",
        )

    if prior_move <= -0.08 and recent_move >= 0.08 and bullish_structure:
        return RegimeAssessment(
            MarketRegime.REVERSAL_UP,
            Direction.BULLISH,
            round(efficiency, 3),
            round(range_pct, 3),
            round(net_change, 3),
            "Recent higher-high/higher-low structure is reversing a previously falling window.",
        )
    if prior_move >= 0.08 and recent_move <= -0.08 and bearish_structure:
        return RegimeAssessment(
            MarketRegime.REVERSAL_DOWN,
            Direction.BEARISH,
            round(efficiency, 3),
            round(range_pct, 3),
            round(net_change, 3),
            "Recent lower-high/lower-low structure is reversing a previously rising window.",
        )

    if efficiency < 0.28 or (not bullish_structure and not bearish_structure and abs(net_change) < 0.12):
        return RegimeAssessment(
            MarketRegime.SIDEWAYS,
            Direction.NEUTRAL,
            round(efficiency, 3),
            round(range_pct, 3),
            round(net_change, 3),
            "Price path is inefficient or mixed, so Shiv treats the market as sideways.",
        )

    if net_change > 0 and bullish_structure:
        return RegimeAssessment(
            MarketRegime.TREND_UP,
            Direction.BULLISH,
            round(efficiency, 3),
            round(range_pct, 3),
            round(net_change, 3),
            "Completed candles maintain higher structure with positive net movement.",
        )
    if net_change < 0 and bearish_structure:
        return RegimeAssessment(
            MarketRegime.TREND_DOWN,
            Direction.BEARISH,
            round(efficiency, 3),
            round(range_pct, 3),
            round(net_change, 3),
            "Completed candles maintain lower structure with negative net movement.",
        )

    return RegimeAssessment(
        MarketRegime.SIDEWAYS,
        Direction.NEUTRAL,
        round(efficiency, 3),
        round(range_pct, 3),
        round(net_change, 3),
        "Direction exists but structure is not clean enough for a trend classification.",
    )


def assess_timeframe(interval_minutes: int, candles: Iterable[IntradayCandle]) -> TimeframeAssessment:
    regime = classify_market_regime(candles)
    if regime.direction == Direction.UNAVAILABLE:
        strength = 0.0
    elif regime.direction == Direction.NEUTRAL:
        strength = max(10.0, min(35.0, regime.efficiency * 100.0))
    else:
        regime_base = 95.0 if regime.regime in {MarketRegime.BREAKOUT_UP, MarketRegime.BREAKOUT_DOWN} else 82.0
        if regime.regime in {MarketRegime.REVERSAL_UP, MarketRegime.REVERSAL_DOWN}:
            regime_base = 70.0
        strength = min(100.0, regime_base * 0.75 + regime.efficiency * 25.0)
    return TimeframeAssessment(
        interval_minutes=interval_minutes,
        regime=regime.regime,
        direction=regime.direction,
        strength=round(strength, 1),
        efficiency=regime.efficiency,
        net_change_pct=regime.net_change_pct,
        reason=regime.reason,
    )


def combine_timeframes(
    rows: Iterable[TimeframeAssessment],
    *,
    weights: Mapping[int, float] | None = None,
) -> MultiTimeframeAssessment:
    assessments = tuple(rows)
    if not assessments:
        return MultiTimeframeAssessment(
            Direction.UNAVAILABLE, 0.0, 0.0, 0.0, 1.0, True, tuple(), "No timeframe evidence is available."
        )
    configured = dict(weights or {1: 0.15, 3: 0.20, 5: 0.30, 15: 0.35})
    raw_weights = {row.interval_minutes: configured.get(row.interval_minutes, 0.15) for row in assessments}
    total = sum(raw_weights.values()) or 1.0
    bullish = bearish = neutral = 0.0
    for row in assessments:
        weight = raw_weights[row.interval_minutes] / total
        conviction = max(0.15, row.strength / 100.0)
        if row.direction == Direction.BULLISH:
            bullish += weight * conviction
        elif row.direction == Direction.BEARISH:
            bearish += weight * conviction
        else:
            neutral += weight

    directional_total = bullish + bearish
    if directional_total <= 0.05:
        direction = Direction.NEUTRAL
        agreement = 0.0
    elif bullish >= bearish:
        direction = Direction.BULLISH
        agreement = bullish / directional_total * 100.0
    else:
        direction = Direction.BEARISH
        agreement = bearish / directional_total * 100.0
    conflict = bullish >= 0.18 and bearish >= 0.18
    if neutral >= 0.55:
        direction = Direction.NEUTRAL
    reason = (
        f"Core NIFTY timeframes agree {agreement:.0f}% on {direction.value.lower()} structure."
        if direction not in {Direction.NEUTRAL, Direction.UNAVAILABLE}
        else "Core NIFTY timeframes do not have a clean directional majority."
    )
    return MultiTimeframeAssessment(
        direction=direction,
        agreement_score=round(agreement, 1),
        bullish_weight=round(bullish, 3),
        bearish_weight=round(bearish, 3),
        neutral_weight=round(neutral, 3),
        conflict=conflict,
        rows=assessments,
        reason=reason,
    )


def infer_candidate_side(
    mtf: MultiTimeframeAssessment,
    atm: ATMConfirmationAssessment,
    strike: StrikeWindowAssessment,
    oi_side: str,
) -> str:
    votes = {"CE": 0.0, "PE": 0.0}
    if mtf.direction == Direction.BULLISH:
        votes["CE"] += 0.25
    elif mtf.direction == Direction.BEARISH:
        votes["PE"] += 0.25
    if atm.signal == ATMConfirmationSignal.CONFIRM_CE:
        votes["CE"] += 0.20
    elif atm.signal == ATMConfirmationSignal.CONFIRM_PE:
        votes["PE"] += 0.20
    if strike.signal == StrikeWindowSignal.CONFIRM_CE:
        votes["CE"] += 0.30
    elif strike.signal == StrikeWindowSignal.CONFIRM_PE:
        votes["PE"] += 0.30
    if oi_side in votes:
        votes[oi_side] += 0.25
    if max(votes.values()) < 0.40 or abs(votes["CE"] - votes["PE"]) < 0.15:
        return "NONE"
    return "CE" if votes["CE"] > votes["PE"] else "PE"


def next_persistence(previous_side: str, previous_count: int, candidate_side: str) -> tuple[str, int]:
    if candidate_side not in {"CE", "PE"}:
        return "", 0
    if candidate_side == previous_side:
        return candidate_side, previous_count + 1
    return candidate_side, 1


def setup_signature(
    *,
    interval_minutes: int,
    regime: MarketRegime,
    mtf_direction: Direction,
    atm_signal: ATMConfirmationSignal,
    strike_label: str,
    oi_side: str,
) -> str:
    return "|".join(
        (
            f"tf={interval_minutes}",
            f"regime={regime.value}",
            f"mtf={mtf_direction.value}",
            f"atm={atm_signal.value}",
            f"window={strike_label}",
            f"oi={oi_side}",
        )
    )


def build_entry_plan(
    side: str,
    strike: float | None,
    candles: Iterable[IntradayCandle],
    *,
    setup_quality: float,
) -> EntryPlan:
    items = list(candles)
    if side not in {"CE", "PE"} or strike is None or len(items) < 3:
        return EntryPlan("WAIT", strike, None, None, None, None, None, "No executable ATM paper entry is available yet.")
    latest = items[-1]
    prior = items[-3:-1]
    prior_high = max(item.high for item in prior)
    bullish_body = latest.close > latest.open
    breakout_close = latest.close > prior_high and bullish_body
    pullback_reclaim = latest.low <= prior[-1].close and latest.close >= prior[-1].close and bullish_body
    current = latest.close
    stop_distance = 3.5
    if setup_quality < 65.0:
        return EntryPlan(
            "WAIT — SETUP DEVELOPING",
            strike,
            current,
            current - stop_distance,
            current + 8.0,
            current + 10.0,
            stop_distance,
            "The setup has not reached confirmation quality, so Shiv does not chase the premium.",
        )
    if not (breakout_close or pullback_reclaim):
        return EntryPlan(
            "WAIT — ENTRY TRIGGER",
            strike,
            current,
            current - stop_distance,
            current + 8.0,
            current + 10.0,
            stop_distance,
            "Directional evidence is present, but Shiv is waiting for a completed premium breakout or pullback reclaim.",
        )
    trigger = "completed premium breakout" if breakout_close else "completed pullback reclaim"
    return EntryPlan(
        "ENTRY READY",
        strike,
        round(current, 2),
        round(current - stop_distance, 2),
        round(current + 8.0, 2),
        round(current + 10.0, 2),
        stop_distance,
        f"{side} evidence is confirmed and the ATM option printed a {trigger}.",
    )


def manage_paper_exit(
    entry: float,
    current: float,
    high_since_entry: float,
    *,
    initial_stop_points: float = 3.5,
) -> ExitPlan:
    profit = current - entry
    if current <= entry - initial_stop_points:
        return ExitPlan("EXIT — STOP", entry - initial_stop_points, None, round(profit, 2), "Initial paper stop was reached.")
    if profit >= 10.0:
        return ExitPlan("EXIT — TARGET 2", entry, max(entry + 6.0, high_since_entry - 2.0), round(profit, 2), "The +10 point objective was reached.")
    if profit >= 8.0:
        trail = max(entry + 4.0, high_since_entry - 2.0)
        return ExitPlan("BOOK / TRAIL RUNNER", trail, trail, round(profit, 2), "The +8 point objective was reached; protect the remaining paper runner.")
    if profit >= 6.0:
        trail = max(entry + 2.0, high_since_entry - 2.5)
        return ExitPlan("TRAIL", trail, trail, round(profit, 2), "Profit is above +6; trail while preserving at least +2 points.")
    if profit >= 4.0:
        return ExitPlan("BREAKEVEN", entry, entry, round(profit, 2), "Profit is above +4; initial risk can be removed in the paper model.")
    return ExitPlan("HOLD", entry - initial_stop_points, None, round(profit, 2), "Neither the risk stop nor the profit-management thresholds have been reached.")


def _side_matches_direction(side: str, direction: Direction) -> bool:
    return (side == "CE" and direction == Direction.BULLISH) or (side == "PE" and direction == Direction.BEARISH)


def _strike_matches(side: str, strike: StrikeWindowAssessment) -> bool:
    return (side == "CE" and strike.signal == StrikeWindowSignal.CONFIRM_CE) or (
        side == "PE" and strike.signal == StrikeWindowSignal.CONFIRM_PE
    )


def _atm_matches(side: str, atm: ATMConfirmationAssessment) -> bool:
    return (side == "CE" and atm.signal == ATMConfirmationSignal.CONFIRM_CE) or (
        side == "PE" and atm.signal == ATMConfirmationSignal.CONFIRM_PE
    )


def _stage_for(side: str, quality: float, persistence: int, hard_ready: bool, blocked: bool) -> SetupStage:
    if blocked:
        return SetupStage.NO_TRADE
    if side not in {"CE", "PE"}:
        return SetupStage.NEUTRAL
    developing = SetupStage.CE_DEVELOPING if side == "CE" else SetupStage.PE_DEVELOPING
    ready = SetupStage.CE_READY if side == "CE" else SetupStage.PE_READY
    confirm = SetupStage.CONFIRM_CE if side == "CE" else SetupStage.CONFIRM_PE
    strong = SetupStage.STRONG_CE if side == "CE" else SetupStage.STRONG_PE
    a_plus = SetupStage.A_PLUS_CE if side == "CE" else SetupStage.A_PLUS_PE
    if quality < 55:
        return developing
    if not hard_ready or persistence < 2:
        return ready
    if quality >= 85 and persistence >= 3:
        return a_plus
    if quality >= 75:
        return strong
    if quality >= 65:
        return confirm
    return ready


def build_shiv_decision(
    *,
    interval_minutes: int,
    primary_regime: RegimeAssessment,
    mtf: MultiTimeframeAssessment,
    atm: ATMConfirmationAssessment,
    strike: StrikeWindowAssessment,
    oi_side: str,
    oi_score: float,
    candidate_side: str,
    persistence_count: int,
    option_spread_pct: float | None,
    option_strike: float | None,
    option_candles: Iterable[IntradayCandle],
    similarity: SimilarityStats | None = None,
) -> ShivDecision:
    side = candidate_side if candidate_side in {"CE", "PE"} else "NONE"
    reasons: list[str] = []
    blockers: list[str] = []

    if primary_regime.regime == MarketRegime.SIDEWAYS:
        blockers.append("SIDEWAYS — directional option entries are blocked until structure improves.")
    if primary_regime.regime == MarketRegime.UNAVAILABLE:
        blockers.append("Primary timeframe regime is unavailable.")
    if mtf.conflict:
        blockers.append("Multi-timeframe NIFTY structure is conflicting.")
    if mtf.direction == Direction.NEUTRAL:
        blockers.append("Multi-timeframe NIFTY structure is neutral.")
    if side in {"CE", "PE"} and not _side_matches_direction(side, mtf.direction):
        blockers.append(f"{side} candidate conflicts with the core multi-timeframe NIFTY direction.")
    if strike.signal == StrikeWindowSignal.UNAVAILABLE:
        blockers.append("ATM ±2 confirmation is unavailable.")
    if side in {"CE", "PE"} and strike.signal in {StrikeWindowSignal.CONFIRM_CE, StrikeWindowSignal.CONFIRM_PE} and not _strike_matches(side, strike):
        blockers.append("ATM ±2 confirmation points to the opposite option side.")
    if option_spread_pct is not None and option_spread_pct > 3.0:
        blockers.append(f"ATM option spread is {option_spread_pct:.2f}%, above the 3% liquidity limit.")

    chosen_atm_move = None
    if side == "CE":
        chosen_atm_move = atm.ce_change_pct
    elif side == "PE":
        chosen_atm_move = atm.pe_change_pct
    if chosen_atm_move is not None and chosen_atm_move >= 8.0:
        blockers.append("ATM premium is already extended by 8%+ across the confirmation window; Shiv will not chase it.")

    regime_points = 0.0
    if side in {"CE", "PE"} and _side_matches_direction(side, primary_regime.direction):
        regime_points = 15.0 if primary_regime.regime in {
            MarketRegime.TREND_UP,
            MarketRegime.TREND_DOWN,
            MarketRegime.BREAKOUT_UP,
            MarketRegime.BREAKOUT_DOWN,
        } else 10.0
        reasons.append(f"Primary regime is {primary_regime.regime.value.lower()} and supports {side}.")

    mtf_points = 0.0
    if side in {"CE", "PE"} and _side_matches_direction(side, mtf.direction):
        mtf_points = min(20.0, 20.0 * mtf.agreement_score / 100.0)
        reasons.append(f"Core NIFTY timeframes support {side} at {mtf.agreement_score:.0f}% agreement.")

    atm_points = 0.0
    if side in {"CE", "PE"} and _atm_matches(side, atm):
        atm_points = min(15.0, 15.0 * atm.agreement_score / 100.0)
        reasons.append(f"ATM premium confirms {side} with {atm.agreement_score:.0f}/100 agreement strength.")

    strike_points = 0.0
    if side in {"CE", "PE"} and _strike_matches(side, strike):
        strike_points = min(25.0, 25.0 * strike.agreement_score / 100.0)
        reasons.append(f"ATM ±2 weighted cluster confirms {side} with {strike.agreement_score:.0f}/100 setup quality.")
    elif side in {"CE", "PE"} and strike.status_label.startswith("WAIT"):
        strike_points = min(10.0, 10.0 * strike.agreement_score / 100.0)
        reasons.append(f"ATM ±2 is still developing: {strike.status_label}.")

    oi_points = 0.0
    if side in {"CE", "PE"} and oi_side == side:
        oi_points = min(20.0, 20.0 * max(0.0, min(100.0, oi_score)) / 100.0)
        reasons.append(f"OI/execution engine supports {side} at {oi_score:.0f}/100.")

    liquidity_points = 0.0
    if option_spread_pct is None:
        liquidity_points = 2.0
    elif option_spread_pct <= 1.5:
        liquidity_points = 5.0
    elif option_spread_pct <= 3.0:
        liquidity_points = 3.0

    components = (
        ("Market regime", round(regime_points, 1)),
        ("Multi-timeframe", round(mtf_points, 1)),
        ("ATM premium", round(atm_points, 1)),
        ("ATM ±2 cluster", round(strike_points, 1)),
        ("OI / execution", round(oi_points, 1)),
        ("Liquidity", round(liquidity_points, 1)),
    )
    quality = round(sum(value for _, value in components), 1)

    hard_ready = (
        side in {"CE", "PE"}
        and _side_matches_direction(side, mtf.direction)
        and _strike_matches(side, strike)
        and oi_side == side
        and not blockers
    )
    stage = _stage_for(side, quality, persistence_count, hard_ready, bool(blockers))
    entry_plan = build_entry_plan(side, option_strike, option_candles, setup_quality=quality)
    stats = similarity or SimilarityStats()
    signature = setup_signature(
        interval_minutes=interval_minutes,
        regime=primary_regime.regime,
        mtf_direction=mtf.direction,
        atm_signal=atm.signal,
        strike_label=strike.status_label or strike.signal.value,
        oi_side=oi_side,
    )
    if stage == SetupStage.NO_TRADE and blockers:
        reasons.append("Shiv is deliberately rejecting the trade instead of forcing a CE/PE call.")
    elif stage in {SetupStage.CE_READY, SetupStage.PE_READY} and persistence_count < 2:
        reasons.append(f"Directional candidate must persist for another fresh evaluation ({persistence_count}/2).")

    return ShivDecision(
        stage=stage,
        side=side,
        setup_quality=quality,
        regime=primary_regime.regime,
        mtf_direction=mtf.direction,
        mtf_agreement=mtf.agreement_score,
        persistence_count=persistence_count,
        component_scores=components,
        reasons=tuple(dict.fromkeys(reasons))[:8],
        blockers=tuple(dict.fromkeys(blockers))[:8],
        entry_plan=entry_plan,
        similarity=stats,
        signature=signature,
    )
