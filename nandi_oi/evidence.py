from __future__ import annotations

from collections.abc import Iterable, Mapping

from .models import Decision, OptionLeg, OptionSnapshot


def _atm(snapshot: OptionSnapshot) -> float:
    strikes = sorted({leg.strike for leg in snapshot.legs})
    return min(strikes, key=lambda strike: abs(strike - snapshot.spot))


def _nearest(snapshot: OptionSnapshot, side: str) -> OptionLeg | None:
    atm = _atm(snapshot)
    legs = [leg for leg in snapshot.legs if leg.side == side]
    return min(legs, key=lambda leg: abs(leg.strike - atm), default=None)


def activity_explanation(leg: OptionLeg) -> str:
    explanations = {
        "FRESH BUYING": "OI and premium are rising: directional demand is building.",
        "FRESH WRITING": "OI is rising while premium falls: writers are adding supply.",
        "SHORT COVERING": "OI is falling while premium rises: short positions are being closed.",
        "LONG UNWINDING": "OI and premium are falling: existing long positions are exiting.",
        "NEUTRAL": "OI or premium change is flat, so this leg adds no directional evidence.",
    }
    return explanations[leg.activity]


def oi_rows(snapshot: OptionSnapshot) -> list[dict[str, object]]:
    return [
        {
            "Strike": leg.strike,
            "Side": leg.side,
            "OI": leg.oi,
            "OI change": leg.change_oi,
            "Activity": leg.activity,
            "Explanation": activity_explanation(leg),
        }
        for leg in sorted(snapshot.legs, key=lambda leg: (leg.strike, leg.side))
    ]


def premium_rows(snapshot: OptionSnapshot) -> list[dict[str, object]]:
    result = []
    for side in ("CE", "PE"):
        leg = _nearest(snapshot, side)
        if not leg:
            continue
        result.append({
            "Contract": f"ATM {side}",
            "Strike": leg.strike,
            "Premium": leg.ltp,
            "Premium change": leg.change_ltp,
            "Volume": leg.volume,
            "Spread %": round(leg.spread_pct, 2),
            "Explanation": (
                "Premium is rising, supporting that side's momentum."
                if leg.change_ltp > 0 else
                "Premium is falling, weakening that side's momentum."
                if leg.change_ltp < 0 else
                "Premium is unchanged, so it does not confirm momentum."
            ),
        })
    return result


def structure_rows(snapshot: OptionSnapshot) -> list[dict[str, object]]:
    if snapshot.spot > snapshot.recent_high and snapshot.spot_change > 0:
        state = "Upward breakout"
        explanation = "Spot is above its recent high and moving higher."
    elif snapshot.spot < snapshot.recent_low and snapshot.spot_change < 0:
        state = "Downward breakdown"
        explanation = "Spot is below its recent low and moving lower."
    else:
        state = "Range / unconfirmed"
        explanation = "Spot has not confirmed a breakout or breakdown."
    return [{
        "Spot": snapshot.spot,
        "Recent high": snapshot.recent_high,
        "Recent low": snapshot.recent_low,
        "Spot change": snapshot.spot_change,
        "Structure": state,
        "Explanation": explanation,
    }]


def score_rows(decision: Decision) -> list[dict[str, object]]:
    lead = round(decision.bullish_score - decision.bearish_score, 1)
    return [
        {"Evidence": "Bullish score", "Score": decision.bullish_score},
        {"Evidence": "Bearish score", "Score": decision.bearish_score},
        {"Evidence": "Directional lead", "Score": abs(lead)},
        {"Evidence": "Approval confidence", "Score": decision.confidence},
    ]


def decision_history_rows(history: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "time": item.get("time", ""),
            "spot": item.get("spot", 0),
            "bullish": item.get("bullish", 0),
            "bearish": item.get("bearish", 0),
            "decision": item.get("decision", "NO TRADE"),
            "confidence": item.get("confidence", 0),
        }
        for item in history
    ]


def expiry_comparison_rows(weekly: object, monthly: object) -> list[dict[str, object]]:
    return [
        {
            "Contract": label,
            "Trades": len(result.trades),
            "Wins": result.wins,
            "Win rate %": round(result.win_rate, 1),
            "Net premium points": result.net_points,
            "Maximum drawdown": result.max_drawdown,
            "Explanation": (
                "Compare contract liquidity and realised premium points; this is a replay, not a forecast."
            ),
        }
        for label, result in (("Nearest weekly", weekly), ("Nearest monthly", monthly))
    ]


def live_evidence(snapshot: OptionSnapshot, decision: Decision) -> dict[str, list[dict[str, object]]]:
    """Presentation data only; it never changes the strategy decision."""
    return {
        "oi": oi_rows(snapshot),
        "premium": premium_rows(snapshot),
        "structure": structure_rows(snapshot),
        "score": score_rows(decision),
    }
