from __future__ import annotations

from dataclasses import dataclass, replace

from .models import Decision, DecisionAction
from .technical import TechnicalAssessment, TechnicalDirection


@dataclass(frozen=True)
class ConfluenceDecision:
    action: DecisionAction
    approved: bool
    setup_score: float
    microstructure_score: float
    technical_score: float
    agreement: str
    reasons: tuple[str, ...] = tuple()
    blockers: tuple[str, ...] = tuple()


def _expected_technical(side: str) -> TechnicalDirection:
    return TechnicalDirection.BULLISH if side == "CE" else TechnicalDirection.BEARISH


def combine_decision(
    base: Decision,
    technical: TechnicalAssessment,
    *,
    minimum_setup_score: float = 75.0,
) -> ConfluenceDecision:
    """Confirm Nandi's OI/execution proposal with technical evidence.

    The confluence layer cannot invent a trade that the existing market/OI engine
    did not propose. It can only approve the same side, keep it in PREPARE, or
    block it with NO TRADE. Fundamentals are intentionally not a mandatory live
    gate because Nandi does not currently have a reliable connected fundamental
    feed.
    """
    if base.action == DecisionAction.NO_TRADE or base.side == "NONE":
        return ConfluenceDecision(
            action=DecisionAction.NO_TRADE,
            approved=False,
            setup_score=round(base.score, 1),
            microstructure_score=round(base.score, 1),
            technical_score=technical.setup_score,
            agreement="OI / execution engine has no approved side",
            blockers=base.blockers
            or ("The OI and execution-quality engine has not proposed a trade.",),
        )

    side = base.side
    expected_technical = _expected_technical(side)
    blockers: list[str] = []
    reasons: list[str] = []

    if technical.direction == TechnicalDirection.UNAVAILABLE:
        blockers.extend(technical.blockers or ("Technical pillar is unavailable.",))
    elif technical.direction == TechnicalDirection.NEUTRAL:
        blockers.append(
            "Technical pillar is sideways / neutral; a directional option entry is blocked."
        )
    elif technical.direction != expected_technical:
        blockers.append(
            f"Technical pillar is {technical.direction.value.lower()} and conflicts with the proposed {side} side."
        )
    else:
        reasons.append(
            f"Technical families confirm the {side} side at {technical.side_score(side):.1f}/100."
        )

    technical_score = technical.side_score(side)
    # Renormalised two-pillar score: OI/execution remains the primary input while
    # technical evidence supplies independent directional confirmation.
    setup_score = round(base.score * 0.55 + technical_score * 0.45, 1)
    if (
        base.action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}
        and setup_score < minimum_setup_score
    ):
        blockers.append(
            f"Unified setup score is {setup_score:.1f}/100 (minimum {minimum_setup_score:.1f})."
        )

    blockers = list(dict.fromkeys(blockers))
    reasons = list(dict.fromkeys(reasons))
    if blockers:
        return ConfluenceDecision(
            action=DecisionAction.NO_TRADE,
            approved=False,
            setup_score=setup_score,
            microstructure_score=round(base.score, 1),
            technical_score=round(technical_score, 1),
            agreement="Technical and OI evidence conflict or are incomplete",
            reasons=tuple(reasons[:4]),
            blockers=tuple(blockers[:5]),
        )

    approved = base.action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}
    return ConfluenceDecision(
        action=base.action,
        approved=approved,
        setup_score=setup_score,
        microstructure_score=round(base.score, 1),
        technical_score=round(technical_score, 1),
        agreement="Technical + OI aligned",
        reasons=tuple(reasons[:4]),
    )


def apply_confluence_gate(base: Decision, confluence: ConfluenceDecision) -> Decision:
    reasons = tuple(dict.fromkeys(confluence.reasons + base.reasons))[:5]
    blockers = tuple(dict.fromkeys(confluence.blockers + base.blockers))[:5]
    return replace(
        base,
        action=confluence.action,
        score=confluence.setup_score,
        reasons=reasons,
        blockers=blockers,
    )
