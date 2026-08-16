from __future__ import annotations

from dataclasses import dataclass, replace

from .fundamentals import FundamentalAssessment, FundamentalBias
from .models import Decision, DecisionAction
from .technical import TechnicalAssessment, TechnicalDirection


@dataclass(frozen=True)
class ConfluenceDecision:
    action: DecisionAction
    approved: bool
    setup_score: float
    microstructure_score: float
    technical_score: float
    fundamental_score: float
    agreement: str
    reasons: tuple[str, ...] = tuple()
    blockers: tuple[str, ...] = tuple()


def _expected_technical(side: str) -> TechnicalDirection:
    return TechnicalDirection.BULLISH if side == "CE" else TechnicalDirection.BEARISH


def _expected_fundamental(side: str) -> FundamentalBias:
    return FundamentalBias.BULLISH if side == "CE" else FundamentalBias.BEARISH


def combine_decision(
    base: Decision,
    technical: TechnicalAssessment,
    fundamental: FundamentalAssessment,
) -> ConfluenceDecision:
    """Use the new pillars as veto/confirmation gates around Nandi's OI engine.

    The confluence layer cannot invent a trade that the existing market/OI engine
    did not propose. It can only approve the same side, keep it in PREPARE, or
    block it with NO TRADE.
    """
    if base.action == DecisionAction.NO_TRADE or base.side == "NONE":
        return ConfluenceDecision(
            action=DecisionAction.NO_TRADE,
            approved=False,
            setup_score=round(base.score, 1),
            microstructure_score=round(base.score, 1),
            technical_score=technical.setup_score,
            fundamental_score=fundamental.setup_score,
            agreement="OI / execution engine has no approved side",
            blockers=base.blockers or ("The OI and execution-quality engine has not proposed a trade.",),
        )

    side = base.side
    expected_technical = _expected_technical(side)
    expected_fundamental = _expected_fundamental(side)
    blockers: list[str] = []
    reasons: list[str] = []

    if technical.direction == TechnicalDirection.UNAVAILABLE:
        blockers.extend(technical.blockers or ("Technical pillar is unavailable.",))
    elif technical.direction == TechnicalDirection.NEUTRAL:
        blockers.append("Technical pillar is sideways / neutral; a directional option entry is blocked.")
    elif technical.direction != expected_technical:
        blockers.append(
            f"Technical pillar is {technical.direction.value.lower()} and conflicts with the proposed {side} side."
        )
    else:
        reasons.append(f"Technical families confirm the {side} side at {technical.side_score(side):.1f}/100.")

    if fundamental.direction == FundamentalBias.UNKNOWN:
        blockers.extend(fundamental.blockers or ("Fundamental pillar is unavailable.",))
    elif fundamental.direction not in {FundamentalBias.NEUTRAL, expected_fundamental}:
        blockers.append(
            f"Fundamental pillar is {fundamental.direction.value.lower()} and conflicts with the proposed {side} side."
        )
    elif fundamental.direction == FundamentalBias.NEUTRAL:
        reasons.append("Fundamental inputs are fresh and neutral; they do not veto the technical side.")
    else:
        reasons.append(f"Fundamental inputs confirm the {side} side at {fundamental.side_score(side):.1f}/100.")

    technical_score = technical.side_score(side)
    fundamental_score = fundamental.side_score(side)
    if fundamental.direction == FundamentalBias.NEUTRAL:
        fundamental_score = max(50.0, fundamental.setup_score)
    setup_score = round(base.score * 0.45 + technical_score * 0.35 + fundamental_score * 0.20, 1)
    blockers = list(dict.fromkeys(blockers))
    reasons = list(dict.fromkeys(reasons))

    if blockers:
        return ConfluenceDecision(
            action=DecisionAction.NO_TRADE,
            approved=False,
            setup_score=setup_score,
            microstructure_score=round(base.score, 1),
            technical_score=round(technical_score, 1),
            fundamental_score=round(fundamental_score, 1),
            agreement="Pillars conflict or are incomplete",
            reasons=tuple(reasons[:4]),
            blockers=tuple(blockers[:5]),
        )

    approved = base.action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}
    agreement = "Fundamental + technical + OI aligned" if fundamental.direction == expected_fundamental else "Technical + OI aligned; fundamentals neutral"
    return ConfluenceDecision(
        action=base.action,
        approved=approved,
        setup_score=setup_score,
        microstructure_score=round(base.score, 1),
        technical_score=round(technical_score, 1),
        fundamental_score=round(fundamental_score, 1),
        agreement=agreement,
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
