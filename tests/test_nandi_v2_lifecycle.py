from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
        levels=TradeLevels(
            entry=24660.0,
            stop=stop,
            target_1=t1,
            target_2=t2,
            option_ltp=100.0,
            option_entry=100.0,
            option_stop=95.0,
            option_target_1=107.5,
            option_target_2=112.5,
        ),
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


def test_opposite_signal_cannot_flip_trade_during_minimum_hold() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    state = advance_trade_state(state, decision(DecisionAction.BUY_PE, stop=24700.0, t1=24600.0, t2=24550.0), 24655.0, NOW)
    assert state.status == TradeStatus.HOLD
    assert "warning only" in state.reason


def test_opposite_signal_exits_after_minimum_hold() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    state = advance_trade_state(
        state,
        decision(DecisionAction.BUY_PE, stop=24700.0, t1=24600.0, t2=24550.0),
        24655.0,
        NOW + timedelta(minutes=15),
    )
    assert state.status == TradeStatus.EXIT
    assert "minimum hold" in state.reason


def test_exit_blocks_immediate_reverse_entry_for_five_minutes() -> None:
    state = advance_trade_state(TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW)
    state = advance_trade_state(state, decision(DecisionAction.BUY_CE), 24615.0, NOW + timedelta(minutes=2))
    blocked = advance_trade_state(
        state,
        decision(DecisionAction.BUY_PE, stop=24700.0, t1=24600.0, t2=24550.0),
        24610.0,
        NOW + timedelta(minutes=3),
    )
    reopened = advance_trade_state(
        blocked,
        decision(DecisionAction.BUY_PE, stop=24700.0, t1=24600.0, t2=24550.0),
        24610.0,
        NOW + timedelta(minutes=7),
    )

    assert blocked.status == TradeStatus.EXIT
    assert "cooldown" in blocked.reason
    assert reopened.status == TradeStatus.ACTIVE_PE


def test_option_premium_stop_exits_immediately() -> None:
    state = advance_trade_state(
        TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW,
        option_premium=100.0, expiry="13-Aug-2026",
    )
    state = advance_trade_state(
        state, decision(DecisionAction.BUY_CE), 24660.0, NOW + timedelta(minutes=2),
        option_premium=94.9,
    )
    assert state.status == TradeStatus.EXIT
    assert "premium stop-loss" in state.reason


def test_option_target_one_books_partial_and_protects_premium_entry() -> None:
    state = advance_trade_state(
        TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW,
        option_premium=100.0, expiry="13-Aug-2026",
    )
    state = advance_trade_state(
        state, decision(DecisionAction.BUY_CE), 24660.0, NOW + timedelta(minutes=5),
        option_premium=108.0,
    )
    assert state.status == TradeStatus.PARTIAL
    assert state.stop_premium == state.entry_premium == 100.0


def test_maximum_hold_exits_still_open_trade() -> None:
    state = advance_trade_state(
        TradeState(), decision(DecisionAction.BUY_CE), 24660.0, NOW,
        option_premium=100.0,
    )
    state = advance_trade_state(
        state, decision(DecisionAction.BUY_CE), 24660.0, NOW + timedelta(minutes=45),
        option_premium=101.0, maximum_hold_minutes=45,
    )
    assert state.status == TradeStatus.EXIT
    assert "Maximum 45-minute" in state.reason
