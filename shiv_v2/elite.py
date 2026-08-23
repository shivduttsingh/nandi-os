from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from shiv_v1.engine import SimilarityStats
from .replay import WalkForwardResult
from .strategy import SessionBucket, V2Decision, VolatilityBand


@dataclass(frozen=True)
class EliteAssessment:
    status: str
    live_candidate: bool
    validated: bool
    sample_size: int
    observed_win_rate: float | None
    walk_forward_win_rate: float | None
    confidence_lower_bound: float | None
    blockers: tuple[str, ...]
    reason: str


def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float | None:
    """95% binomial lower bound used as a conservatism check, not a forecast."""
    if total <= 0:
        return None
    p = wins / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    margin = z * sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return max(0.0, (centre - margin) / denominator) * 100.0


def assess_a_plus_plus_plus_plus(
    decision: V2Decision,
    similarity: SimilarityStats,
    walk_forward: WalkForwardResult,
    *,
    minimum_sample: int = 50,
    minimum_observed_win_rate: float = 85.0,
    minimum_walk_forward_trades: int = 20,
    minimum_walk_forward_win_rate: float = 80.0,
    minimum_confidence_lower_bound: float = 75.0,
) -> EliteAssessment:
    """Ultra-selective A++++ research gate. It never guarantees the next trade."""
    blockers: list[str] = []
    selected = decision.strike_selection.selected

    # Live-market gates. M/W is intentionally absent from this function.
    if not decision.actionable:
        blockers.append("V2 direction and ENTRY READY are not both confirmed.")
    if decision.setup_quality < max(90.0, decision.required_quality + 8.0):
        blockers.append("Setup quality has not cleared the A++++ margin.")
    if decision.base.mtf_agreement < max(90.0, decision.required_mtf + 5.0):
        blockers.append("Multi-timeframe agreement is below the A++++ threshold.")
    if decision.base.persistence_count < max(3, decision.policy.minimum_persistence + 1):
        blockers.append("Directional persistence is not long enough for A++++.")
    if selected is None:
        blockers.append("No ATM/near-ATM contract has passed selection.")
    else:
        if selected.score < 90.0:
            blockers.append("Selected option contract score is below 90/100.")
        if selected.spread_pct is None or selected.spread_pct > 1.5:
            blockers.append("Selected option spread is not verified at 1.5% or tighter.")
        if selected.volume_ratio is not None and selected.volume_ratio < 1.10:
            blockers.append("Selected option volume is not expanding enough for A++++.")
        if selected.responsiveness_pct < 1.0:
            blockers.append("Selected option premium response is too weak for A++++.")
    if decision.breakout.blocked:
        blockers.append("False-breakout/premium-divergence protection is blocking the setup.")
    if decision.decay.blocked:
        blockers.append("Setup is expired or already chased.")
    if decision.session.bucket not in {SessionBucket.MORNING, SessionBucket.AFTERNOON}:
        blockers.append("A++++ is restricted to the cleaner morning/afternoon windows.")
    if decision.volatility.band in {VolatilityBand.EXTREME, VolatilityBand.UNAVAILABLE}:
        blockers.append("Volatility is extreme or unavailable.")
    if decision.blockers:
        blockers.extend(decision.blockers)

    live_candidate = not blockers

    # Historical validation gates. These are observations, never a future guarantee.
    lower_bound = wilson_lower_bound(similarity.wins, similarity.sample_size)
    historical_blockers: list[str] = []
    if similarity.sample_size < minimum_sample:
        historical_blockers.append(f"Needs at least {minimum_sample} comparable completed outcomes.")
    if similarity.win_rate is None or similarity.win_rate < minimum_observed_win_rate:
        historical_blockers.append(
            f"Comparable observed win rate must be at least {minimum_observed_win_rate:.0f}%."
        )
    if similarity.average_points is None or similarity.average_points <= 0:
        historical_blockers.append("Comparable setups must have positive average premium points.")
    if lower_bound is None or lower_bound < minimum_confidence_lower_bound:
        historical_blockers.append(
            f"95% Wilson lower confidence bound must be at least {minimum_confidence_lower_bound:.0f}%."
        )
    if walk_forward.selected_trades < minimum_walk_forward_trades:
        historical_blockers.append(
            f"Needs at least {minimum_walk_forward_trades} selected walk-forward test trades."
        )
    if walk_forward.observed_win_rate is None or walk_forward.observed_win_rate < minimum_walk_forward_win_rate:
        historical_blockers.append(
            f"Walk-forward observed win rate must be at least {minimum_walk_forward_win_rate:.0f}%."
        )
    if walk_forward.average_points is None or walk_forward.average_points <= 0:
        historical_blockers.append("Walk-forward selected trades must have positive average points.")

    validated = live_candidate and not historical_blockers
    all_blockers = tuple(dict.fromkeys(blockers + historical_blockers))

    if validated:
        status = f"A++++ VALIDATED {decision.side}"
        reason = (
            "The live elite gates and completed comparable/walk-forward evidence all pass. "
            "The historical rates remain observations, not a guarantee for the next trade."
        )
    elif live_candidate:
        status = f"A++++ CANDIDATE {decision.side} — PAPER ONLY"
        reason = (
            "The live market setup clears the ultra-selective gates, but the evidence base has not "
            "earned the A++++ validated label yet."
        )
    else:
        status = "A++++ LOCKED"
        reason = "One or more live elite gates are missing, so Shiv does not promote this setup to A++++."

    return EliteAssessment(
        status=status,
        live_candidate=live_candidate,
        validated=validated,
        sample_size=similarity.sample_size,
        observed_win_rate=similarity.win_rate,
        walk_forward_win_rate=walk_forward.observed_win_rate,
        confidence_lower_bound=round(lower_bound, 1) if lower_bound is not None else None,
        blockers=all_blockers[:16],
        reason=reason,
    )
