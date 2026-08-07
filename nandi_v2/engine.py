from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from math import isfinite
from statistics import mean
from typing import Iterable, Literal

from .models import (
    Decision,
    DecisionAction,
    MarketContext,
    OptionChainSnapshot,
    OptionLeg,
    ScoreBreakdown,
    StrikeRow,
    TradeLevels,
)

Side = Literal["CE", "PE"]


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not denominator:
        return default
    return numerator / denominator


def option_activity(leg: OptionLeg) -> str:
    """Classify one option leg from OI change and premium change."""
    if leg.change_oi > 0 and leg.change < 0:
        return "WRITING"
    if leg.change_oi > 0 and leg.change > 0:
        return "LONG BUILDUP"
    if leg.change_oi < 0 and leg.change > 0:
        return "SHORT COVERING"
    if leg.change_oi < 0 and leg.change < 0:
        return "LONG UNWINDING"
    if leg.change_oi > 0:
        return "OI BUILDUP"
    if leg.change_oi < 0:
        return "OI UNWINDING"
    return "NEUTRAL"


def nearest_atm(rows: Iterable[StrikeRow], spot: float) -> StrikeRow:
    values = tuple(rows)
    if not values:
        raise ValueError("Option chain has no strike rows")
    return min(values, key=lambda row: abs(row.strike - spot))


def limited_rows(snapshot: OptionChainSnapshot, wings: int = 5) -> tuple[StrikeRow, ...]:
    rows = sorted(snapshot.rows, key=lambda row: row.strike)
    if not rows:
        return tuple()
    atm = min(range(len(rows)), key=lambda index: abs(rows[index].strike - snapshot.spot))
    start = max(0, atm - wings)
    end = min(len(rows), atm + wings + 1)
    return tuple(rows[start:end])


def _distance_weight(strike: float, spot: float, step: float) -> float:
    distance = abs(strike - spot) / max(step, 1.0)
    return 1.0 / (1.0 + 0.28 * distance)


def _normalise_signal(value: float, scale: float, maximum: float) -> float:
    if scale <= 0:
        return 0.0
    return round(clamp(value / scale * maximum, 0.0, maximum), 1)


def _strike_step(rows: tuple[StrikeRow, ...]) -> float:
    strikes = sorted({row.strike for row in rows})
    differences = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return min(differences) if differences else 50.0


def _support_resistance(rows: tuple[StrikeRow, ...], spot: float) -> tuple[float, float]:
    below = [row for row in rows if row.strike <= spot]
    above = [row for row in rows if row.strike >= spot]
    support_pool = below or list(rows)
    resistance_pool = above or list(rows)
    step = _strike_step(rows)

    def put_strength(row: StrikeRow) -> float:
        writing_bonus = max(row.pe.change_oi, 0.0) * (1.25 if option_activity(row.pe) == "WRITING" else 0.8)
        return (max(row.pe.oi, 0.0) + writing_bonus) * _distance_weight(row.strike, spot, step)

    def call_strength(row: StrikeRow) -> float:
        writing_bonus = max(row.ce.change_oi, 0.0) * (1.25 if option_activity(row.ce) == "WRITING" else 0.8)
        return (max(row.ce.oi, 0.0) + writing_bonus) * _distance_weight(row.strike, spot, step)

    support = max(support_pool, key=put_strength).strike
    resistance = max(resistance_pool, key=call_strength).strike
    return support, resistance


def _market_state(snapshot: OptionChainSnapshot, context: MarketContext) -> str:
    spot = snapshot.spot
    previous = context.previous_spot
    high = context.recent_high
    low = context.recent_low
    rsi = context.momentum_rsi
    if previous is None:
        return "UNCONFIRMED"
    move = spot - previous
    if high is not None and spot > high and move > 0:
        return "BULLISH BREAKOUT"
    if low is not None and spot < low and move < 0:
        return "BEARISH BREAKDOWN"
    if move > 0 and (rsi is None or rsi >= 52):
        return "BULLISH TREND"
    if move < 0 and (rsi is None or rsi <= 48):
        return "BEARISH TREND"
    return "RANGE / COMPRESSION"


def _structure_score(side: Side, snapshot: OptionChainSnapshot, context: MarketContext) -> float:
    spot = snapshot.spot
    previous = context.previous_spot
    high = context.recent_high
    low = context.recent_low
    rsi = context.momentum_rsi
    score = 0.0
    if previous is not None:
        delta = spot - previous
        direction = delta if side == "CE" else -delta
        score += clamp(direction / 3.0, 0.0, 8.0)
    if high is not None and low is not None and high > low:
        position = (spot - low) / (high - low)
        directional_position = position if side == "CE" else 1.0 - position
        score += clamp(directional_position * 7.0, 0.0, 7.0)
        if side == "CE" and spot > high:
            score += 5.0
        elif side == "PE" and spot < low:
            score += 5.0
    else:
        score += 3.0
    if rsi is not None:
        if side == "CE" and rsi >= 55:
            score += 2.0
        elif side == "PE" and rsi <= 45:
            score += 2.0
    return round(clamp(score, 0.0, 20.0), 1)


def _oi_score(side: Side, rows: tuple[StrikeRow, ...], spot: float) -> tuple[float, list[str]]:
    if not rows:
        return 0.0, []
    step = _strike_step(rows)
    bullish = 0.0
    bearish = 0.0
    reasons: list[str] = []
    call_positive = sum(max(row.ce.change_oi, 0.0) for row in rows)
    put_positive = sum(max(row.pe.change_oi, 0.0) for row in rows)
    scale = max(call_positive + put_positive, 1.0)
    for row in rows:
        weight = _distance_weight(row.strike, spot, step)
        ce_activity = option_activity(row.ce)
        pe_activity = option_activity(row.pe)
        ce_size = abs(row.ce.change_oi) * weight
        pe_size = abs(row.pe.change_oi) * weight
        if ce_activity == "WRITING":
            bearish += ce_size * 1.25
        elif ce_activity in {"SHORT COVERING", "LONG BUILDUP"}:
            bullish += ce_size * (1.25 if ce_activity == "SHORT COVERING" else 1.0)
        if pe_activity == "WRITING":
            bullish += pe_size * 1.25
        elif pe_activity in {"SHORT COVERING", "LONG BUILDUP"}:
            bearish += pe_size * (1.25 if pe_activity == "SHORT COVERING" else 1.0)
    directional = bullish if side == "CE" else bearish
    score = _normalise_signal(directional, scale * 0.55, 20.0)
    strongest_call = max(rows, key=lambda row: max(row.ce.change_oi, 0.0))
    strongest_put = max(rows, key=lambda row: max(row.pe.change_oi, 0.0))
    if side == "CE":
        if option_activity(strongest_put.pe) == "WRITING":
            reasons.append(f"Put writing is strongest near {strongest_put.strike:.0f}")
        call_cover = max(rows, key=lambda row: -row.ce.change_oi)
        if option_activity(call_cover.ce) == "SHORT COVERING":
            reasons.append(f"Call short covering is visible near {call_cover.strike:.0f}")
    else:
        if option_activity(strongest_call.ce) == "WRITING":
            reasons.append(f"Call writing is strongest near {strongest_call.strike:.0f}")
        put_exit = max(rows, key=lambda row: -row.pe.change_oi)
        if option_activity(put_exit.pe) == "SHORT COVERING":
            reasons.append(f"Put short covering is visible near {put_exit.strike:.0f}")
    return score, reasons


def _premium_score(side: Side, rows: tuple[StrikeRow, ...], spot: float) -> tuple[float, list[str]]:
    if not rows:
        return 0.0, []
    atm = nearest_atm(rows, spot)
    candidates = sorted(rows, key=lambda row: abs(row.strike - spot))[:3]
    legs = [row.ce if side == "CE" else row.pe for row in candidates]
    positive = [max(leg.change, 0.0) for leg in legs]
    ltp_values = [max(leg.ltp, 1.0) for leg in legs]
    percent_changes = [safe_ratio(change, ltp) * 100.0 for change, ltp in zip(positive, ltp_values)]
    average_change = mean(percent_changes) if percent_changes else 0.0
    atm_leg = atm.ce if side == "CE" else atm.pe
    score = clamp(average_change * 2.3, 0.0, 10.0)
    if atm_leg.change > 0:
        score += 3.0
    if option_activity(atm_leg) in {"LONG BUILDUP", "SHORT COVERING"}:
        score += 2.0
    reasons = []
    if score >= 8:
        reasons.append(f"{side} premium is expanding around ATM")
    return round(clamp(score, 0.0, 15.0), 1), reasons


def _location_score(side: Side, spot: float, support: float, resistance: float, step: float) -> tuple[float, list[str], list[str]]:
    score = 0.0
    reasons: list[str] = []
    blockers: list[str] = []
    if side == "CE":
        room = resistance - spot
        support_distance = spot - support
        if spot >= support:
            score += 5.0
        if support_distance <= step * 1.5:
            score += 4.0
            reasons.append(f"NIFTY is holding above support near {support:.0f}")
        if room >= step * 0.7:
            score += 6.0
        else:
            blockers.append(f"Upside room to Call resistance {resistance:.0f} is limited")
    else:
        room = spot - support
        resistance_distance = resistance - spot
        if spot <= resistance:
            score += 5.0
        if resistance_distance <= step * 1.5:
            score += 4.0
            reasons.append(f"NIFTY is below resistance near {resistance:.0f}")
        if room >= step * 0.7:
            score += 6.0
        else:
            blockers.append(f"Downside room to Put support {support:.0f} is limited")
    return round(clamp(score, 0.0, 15.0), 1), reasons, blockers


def _momentum_score(side: Side, context: MarketContext) -> tuple[float, list[str]]:
    rsi = context.momentum_rsi
    if rsi is None or not isfinite(rsi):
        return 4.0, []
    if side == "CE":
        score = clamp((rsi - 40.0) / 25.0 * 10.0, 0.0, 10.0)
    else:
        score = clamp((60.0 - rsi) / 25.0 * 10.0, 0.0, 10.0)
    reason = f"Spot momentum RSI is {rsi:.1f}"
    return round(score, 1), [reason] if score >= 6 else []


def _volume_score(side: Side, rows: tuple[StrikeRow, ...], spot: float) -> tuple[float, list[str]]:
    candidates = sorted(rows, key=lambda row: abs(row.strike - spot))[:5]
    call_volume = sum(max(row.ce.volume, 0.0) for row in candidates)
    put_volume = sum(max(row.pe.volume, 0.0) for row in candidates)
    total = call_volume + put_volume
    if total <= 0:
        return 3.0, []
    share = call_volume / total if side == "CE" else put_volume / total
    score = clamp((share - 0.30) / 0.40 * 10.0, 0.0, 10.0)
    reason = f"{side} volume share near ATM is {share:.0%}"
    return round(score, 1), [reason] if score >= 6 else []


def _levels(side: Side, snapshot: OptionChainSnapshot, support: float, resistance: float, step: float) -> TradeLevels:
    spot = snapshot.spot
    if side == "CE":
        stop = max(support - step * 0.10, spot - step * 0.60)
        target_1 = resistance if resistance > spot + step * 0.35 else spot + step * 0.70
        target_2 = max(target_1 + step * 0.50, spot + step * 1.20)
    else:
        stop = min(resistance + step * 0.10, spot + step * 0.60)
        target_1 = support if support < spot - step * 0.35 else spot - step * 0.70
        target_2 = min(target_1 - step * 0.50, spot - step * 1.20)
    risk = abs(spot - stop)
    reward = abs(target_1 - spot)
    rr = safe_ratio(reward, risk)
    return TradeLevels(entry=spot, stop=round(stop, 2), target_1=round(target_1, 2), target_2=round(target_2, 2), support=support, resistance=resistance, reward_risk=round(rr, 2))


def _risk_score(levels: TradeLevels) -> tuple[float, list[str], list[str]]:
    rr = levels.reward_risk or 0.0
    if rr >= 1.5:
        return 5.0, [f"Reward-risk is approximately 1:{rr:.2f}"], []
    if rr >= 1.15:
        return 3.0, [], []
    return 0.0, [], [f"Reward-risk is only approximately 1:{rr:.2f}"]


def _freshness_score(snapshot: OptionChainSnapshot, context: MarketContext) -> tuple[float, list[str]]:
    observed = context.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    stamp = snapshot.timestamp
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = max(0.0, (observed - stamp).total_seconds())
    if age <= 30:
        return 5.0, []
    if age <= 90:
        return 4.0, []
    if age <= 180:
        return 2.0, [f"OI snapshot is {age:.0f} seconds old"]
    return 0.0, [f"OI snapshot is stale ({age:.0f} seconds old)"]


def _build_side(side: Side, snapshot: OptionChainSnapshot, context: MarketContext, rows: tuple[StrikeRow, ...], support: float, resistance: float) -> tuple[ScoreBreakdown, TradeLevels, list[str], list[str]]:
    step = _strike_step(rows)
    structure = _structure_score(side, snapshot, context)
    oi, oi_reasons = _oi_score(side, rows, snapshot.spot)
    premium, premium_reasons = _premium_score(side, rows, snapshot.spot)
    location, location_reasons, location_blockers = _location_score(side, snapshot.spot, support, resistance, step)
    momentum, momentum_reasons = _momentum_score(side, context)
    volume, volume_reasons = _volume_score(side, rows, snapshot.spot)
    levels = _levels(side, snapshot, support, resistance, step)
    risk, risk_reasons, risk_blockers = _risk_score(levels)
    freshness, freshness_reasons = _freshness_score(snapshot, context)
    breakdown = ScoreBreakdown(market_structure=structure, oi_positioning=oi, premium_confirmation=premium, location=location, momentum=momentum, volume=volume, risk_reward=risk, freshness=freshness)
    reasons = oi_reasons + premium_reasons + location_reasons + momentum_reasons + volume_reasons + risk_reasons
    blockers = location_blockers + risk_blockers
    if freshness == 0:
        blockers.extend(freshness_reasons)
    elif freshness_reasons:
        reasons.extend(freshness_reasons)
    if premium < 5:
        blockers.append(f"{side} premium is not confirming the move")
    return breakdown, levels, reasons, blockers


def decide(snapshot: OptionChainSnapshot, context: MarketContext, *, trade_threshold: float = 75.0, prepare_threshold: float = 65.0, minimum_edge: float = 8.0) -> Decision:
    rows = limited_rows(snapshot, wings=5)
    now = context.observed_at
    empty = ScoreBreakdown(0, 0, 0, 0, 0, 0, 0, 0)
    if len(rows) < 5:
        return Decision(action=DecisionAction.NO_TRADE, score=0.0, ce_score=0.0, pe_score=0.0, selected_strike=None, market_state="INSUFFICIENT DATA", breakdown=empty, opposite_breakdown=empty, levels=TradeLevels(), blockers=("At least five nearby strikes are required",), generated_at=now, data_timestamp=snapshot.timestamp)
    support, resistance = _support_resistance(rows, snapshot.spot)
    ce, ce_levels, ce_reasons, ce_blockers = _build_side("CE", snapshot, context, rows, support, resistance)
    pe, pe_levels, pe_reasons, pe_blockers = _build_side("PE", snapshot, context, rows, support, resistance)
    ce_score = ce.total
    pe_score = pe.total
    market_state = _market_state(snapshot, context)
    preferred: Side = "CE" if ce_score >= pe_score else "PE"
    score = max(ce_score, pe_score)
    edge = abs(ce_score - pe_score)
    preferred_breakdown = ce if preferred == "CE" else pe
    opposite_breakdown = pe if preferred == "CE" else ce
    preferred_levels = ce_levels if preferred == "CE" else pe_levels
    preferred_reasons = ce_reasons if preferred == "CE" else pe_reasons
    preferred_blockers = ce_blockers if preferred == "CE" else pe_blockers
    hard_blockers = list(dict.fromkeys(preferred_blockers))
    if edge < minimum_edge:
        hard_blockers.append(f"CE and PE scores conflict; the edge is only {edge:.1f} points")
    if market_state == "RANGE / COMPRESSION" and score < trade_threshold + 5:
        hard_blockers.append("Price is still in range/compression")
    if support == resistance:
        hard_blockers.append("Support and resistance have collapsed into the same strike")
    if score >= trade_threshold and not hard_blockers:
        action = DecisionAction.BUY_CE if preferred == "CE" else DecisionAction.BUY_PE
    elif score >= prepare_threshold and edge >= minimum_edge:
        action = DecisionAction.PREPARE_CE if preferred == "CE" else DecisionAction.PREPARE_PE
    else:
        action = DecisionAction.NO_TRADE
    atm = nearest_atm(rows, snapshot.spot).strike
    reasons = tuple(dict.fromkeys(preferred_reasons))[:5]
    blockers = tuple(dict.fromkeys(hard_blockers))[:5]
    if action == DecisionAction.NO_TRADE and not blockers:
        blockers = ("The setup has not reached the minimum score and directional edge",)
    return Decision(action=action, score=score, ce_score=ce_score, pe_score=pe_score, selected_strike=atm, market_state=market_state, breakdown=preferred_breakdown, opposite_breakdown=opposite_breakdown, levels=preferred_levels, reasons=reasons, blockers=blockers, generated_at=now, data_timestamp=snapshot.timestamp)


def with_spot(snapshot: OptionChainSnapshot, spot: float, timestamp: datetime | None = None) -> OptionChainSnapshot:
    return replace(snapshot, spot=float(spot), timestamp=timestamp or snapshot.timestamp)


def strike_evidence_rows(snapshot: OptionChainSnapshot) -> list[dict[str, object]]:
    rows = limited_rows(snapshot, wings=5)
    atm = nearest_atm(rows, snapshot.spot).strike if rows else None
    output = []
    for row in rows:
        output.append({"CE LTP": row.ce.ltp, "CE Chg": row.ce.change, "CE OI": row.ce.oi, "CE COI": row.ce.change_oi, "CE Vol": row.ce.volume, "CE activity": option_activity(row.ce), "Strike": row.strike, "ATM": "ATM" if row.strike == atm else "", "PE activity": option_activity(row.pe), "PE Vol": row.pe.volume, "PE COI": row.pe.change_oi, "PE OI": row.pe.oi, "PE Chg": row.pe.change, "PE LTP": row.pe.ltp})
    return output
