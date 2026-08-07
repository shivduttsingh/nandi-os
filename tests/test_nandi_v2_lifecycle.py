from __future__ import annotations

from datetime import datetime, timezone

from nandi_v2.lifecycle import TradeState, TradeStatus, advance_trade_state
from nandi_v2.models import Decision, DecisionAction, ScoreBreakdown, TradeLevels


EMPTY = ScoreBreakdown(10, 10, 10, 10, 10, 10, 5, 5)
NOW = datetime.now(timezone.utc)


def decision(action: DecisionAction, score: float = 82.0, stop: float = 24620.0, t1: float = 24700.0, t2: float = 24750.0) -> Decision:
    return Decision(
        action=action,
        score=score,
        ce_score=score if action in {DecisionAction.BUY_CE, DecisionAction.PREPARE_CE} else 20.0,
        pe_score=score if action in {DecisionAction.BUY_PE, DecisionAction.PREPARE_PE} else 20.0,
        selected_strike=24650.0,
        market_state="TEST",
        breakdown=EMPTY,
        opposite_breakdown=EMPTY,
        levels=TradeLevels(entry=24660.0, stop=stop, target_1=t1, target_2=t2),
        generated_at=NOW,
        data_timestamp=NOW,
    )


def test_prepare_does_not_open_trade() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.PREPARE_CE, 70.0), 24660.0, NOW)
    assert state.status == TradeStatus.PREPARE_CE
    assert not state.active


def test_buy_opens_trade() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    assert state.status == TradeStatus.ACTIVE_CE
    assert state.active
    assert state.entry_spot == 24660.0


def test_target_one_books_partial_and_moves_stop_to_entry() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    state = advance_trade_state(state, decision(DecisionAction.BUY_CE), 24705.0, NOW)
    assert state.status == TradeStatus.PARTIAL
    assert state.partial_booked
    assert state.stop_spot == state.entry_spot


def test_target_two_exits_trade() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    state = advance_trade_state(state, decision(DecisionAction.BUY_CE), 24755.0, NOW)
    assert state.status == TradeStatus.EXIT
    assert "Target 2" in state.reason


def test_stop_exits_trade() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    state = advance_trade_state(state, decision(DecisionAction.BUY_CE), 24615.0, NOW)
    assert state.status == TradeStatus.EXIT
    assert "stop-loss" in state.reason


def test_opposite_signal_exits_trade() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    state = advance_trade_state(state, decision(DecisionAction.BUY_PE, stop=24700.0, t1=24600.0, t2=24550.0), 24655.0, NOW)
    assert state.status == TradeStatus.EXIT
    assert "Opposite-side" in state.reason
