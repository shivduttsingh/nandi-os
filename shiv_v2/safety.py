from __future__ import annotations

from dataclasses import replace
from threading import Lock

from shiv_v1.engine import SetupStage
from . import strategy as _strategy
from .strategy import AdaptiveEntryPlan, PatternAssessment, V2Decision


_raw_build_v2_decision = _strategy.build_v2_decision
_mw_exclusion_lock = Lock()


def _excluded_mw_pattern(*_args, **_kwargs) -> PatternAssessment:
    """Keep the user's M/W concept completely outside Shiv's decision scope."""
    return PatternAssessment(
        label="EXCLUDED",
        side="NONE",
        confidence=0.0,
        neckline=None,
        confirmed=False,
        reason="M/W is explicitly excluded from Shiv decision logic and contributes no points, blockers or trade direction.",
    )


def _build_without_mw(**kwargs) -> V2Decision:
    # The V2 builder currently owns an experimental M/W detector. For the live
    # Shiv composition we neutralize only that dependency while the builder
    # runs, then restore the module immediately. The lock prevents concurrent
    # Shiv evaluations from seeing an intermediate detector state.
    with _mw_exclusion_lock:
        original_detector = _strategy.detect_mw_pattern
        _strategy.detect_mw_pattern = _excluded_mw_pattern
        try:
            return _raw_build_v2_decision(**kwargs)
        finally:
            _strategy.detect_mw_pattern = original_detector


def build_safe_v2_decision(**kwargs) -> V2Decision:
    """Apply V2 adaptivity while excluding M/W and preserving hard safety gates."""
    decision = _build_without_mw(**kwargs)
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
