from __future__ import annotations

from dataclasses import replace

from shiv_v1.engine import SetupStage
from .strategy import AdaptiveEntryPlan, V2Decision, build_v2_decision


def build_safe_v2_decision(**kwargs) -> V2Decision:
    """Apply V2 adaptivity without allowing it to bypass V1 hard safety blockers."""
    decision = build_v2_decision(**kwargs)
    base = kwargs["base"]
    mtf = kwargs["mtf"]
    hard_blockers: list[str] = []
    if base.stage == SetupStage.NO_TRADE:
        hard_blockers.extend(base.blockers or ("V1 evidence engine issued a hard NO TRADE gate.",))
    if mtf.conflict:
        hard_blockers.append("Core NIFTY timeframes contain a directional conflict; V2 cannot override it.")
    if not hard_blockers:
        return decision

    entry = decision.entry_plan
    safe_entry = AdaptiveEntryPlan(
        status="WAIT — HARD GATE",
        strike=entry.strike,
        entry=entry.entry,
        stop=entry.stop,
        target_1=entry.target_1,
        target_2=entry.target_2,
        reason="The underlying V1/MTF hard gate must clear before V2 can create an entry.",
    )
    return replace(
        decision,
        status="NO TRADE",
        blockers=tuple(dict.fromkeys((*hard_blockers, *decision.blockers)))[:10],
        entry_plan=safe_entry,
    )
