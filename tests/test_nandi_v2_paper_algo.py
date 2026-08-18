from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nandi_oi.models import OptionStrikeCandles
from nandi_v2.paper_algo import (
    ATM_ALGO,
    ATM_TWO_STRIKE_ALGO,
    PaperAlgoStore,
    advance_paper_algo,
    directional_two_strike_contract,
)


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 8, 18, 10, 0, tzinfo=IST)


def update(store, **overrides):
    values = {
        "strategy": ATM_ALGO,
        "signal_side": "CE",
        "signal_key": "2026-08-18T09:45:00:CE:25000",
        "strike": 25000.0,
        "expiry": "2026-08-20",
        "premium": 100.0,
        "now": NOW,
        "market_open": True,
    }
    values.update(overrides)
    return advance_paper_algo(store, **values)


def test_confirmed_signal_opens_exact_130_quantity_paper_position(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))

    result = update(store)

    assert result.position is not None
    assert result.position.quantity == 130
    assert result.position.entry_premium == 100.0
    assert result.position.target_premium == 108.0
    assert result.position.stop_premium == 96.0
    assert PaperAlgoStore(str(tmp_path / "paper.sqlite")).position(ATM_ALGO) == result.position


def test_eight_point_target_books_1040_paper_profit(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    update(store)

    result = update(store, premium=108.4, now=NOW + timedelta(minutes=2))

    assert result.position is None
    assert result.closed_trade is not None
    assert result.closed_trade.exit_premium == 108.0
    assert result.closed_trade.premium_points == 8.0
    assert result.closed_trade.paper_pnl == 1040.0
    assert store.position(ATM_ALGO) is None


def test_four_point_stop_books_520_paper_loss(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    update(store)

    result = update(store, premium=95.5, now=NOW + timedelta(minutes=1))

    assert result.closed_trade is not None
    assert result.closed_trade.exit_premium == 96.0
    assert result.closed_trade.premium_points == -4.0
    assert result.closed_trade.paper_pnl == -520.0


def test_atm_and_atm_two_strike_books_are_independent(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    atm = update(store)
    wing = update(
        store,
        strategy=ATM_TWO_STRIKE_ALGO,
        signal_side="PE",
        signal_key="2026-08-18T09:45:00:PE:24900",
        strike=24900.0,
        premium=80.0,
    )

    assert atm.position is not None
    assert wing.position is not None
    assert store.position(ATM_ALGO).strike == 25000.0
    assert store.position(ATM_TWO_STRIKE_ALGO).strike == 24900.0


def test_same_completed_signal_cannot_reenter_after_exit(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    update(store)
    update(store, premium=108.0, now=NOW + timedelta(minutes=1))

    duplicate = update(store, premium=101.0, now=NOW + timedelta(minutes=6))

    assert duplicate.position is None
    assert "already used" in duplicate.message
    assert len(store.recent_trades()) == 1


def test_closed_session_waits_and_maximum_hold_exits_at_observed_premium(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    closed = update(store, market_open=False)
    assert closed.position is None

    update(store)
    timed = update(store, premium=102.0, now=NOW + timedelta(minutes=45))

    assert timed.closed_trade is not None
    assert timed.closed_trade.exit_premium == 102.0
    assert timed.closed_trade.paper_pnl == 260.0
    assert "45-minute" in timed.closed_trade.exit_reason


def test_maximum_hold_uses_last_observed_premium_if_contract_leaves_window(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    update(store)
    update(store, premium=102.0, now=NOW + timedelta(minutes=10))

    timed = update(store, premium=0.0, now=NOW + timedelta(minutes=45))

    assert timed.closed_trade is not None
    assert timed.closed_trade.exit_premium == 102.0
    assert timed.closed_trade.paper_pnl == 260.0
    assert "last observed premium" in timed.closed_trade.exit_reason


def test_new_signal_waits_for_five_minute_cooldown(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    update(store)
    update(store, premium=108.0, now=NOW + timedelta(minutes=1))

    waiting = update(
        store,
        signal_key="2026-08-18T10:00:00:CE:25000",
        now=NOW + timedelta(minutes=3),
    )
    reopened = update(
        store,
        signal_key="2026-08-18T10:00:00:CE:25000",
        now=NOW + timedelta(minutes=6),
    )

    assert waiting.position is None
    assert "cooldown" in waiting.message
    assert reopened.position is not None


def test_each_algo_stops_after_three_completed_trades_per_day(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))
    for index in range(3):
        key = f"signal-{index}"
        opened_at = NOW + timedelta(minutes=index * 2)
        update(store, signal_key=key, now=opened_at, cooldown_minutes=0)
        update(
            store,
            signal_key=key,
            premium=108.0,
            now=opened_at + timedelta(minutes=1),
            cooldown_minutes=0,
        )

    limited = update(
        store,
        signal_key="signal-3",
        now=NOW + timedelta(minutes=7),
        cooldown_minutes=0,
    )

    assert limited.position is None
    assert "Daily limit of 3" in limited.message
    assert len(store.recent_trades(ATM_ALGO)) == 3


def test_invalid_signal_or_premium_never_opens_position(tmp_path):
    store = PaperAlgoStore(str(tmp_path / "paper.sqlite"))

    assert update(store, signal_side="NONE").position is None
    assert update(store, premium=0.0).position is None
    assert store.recent_trades() == []


def test_atm_two_strike_algo_selects_directional_otm_contract():
    window = tuple(
        OptionStrikeCandles(
            strike=25000 + offset * 50,
            expiry="2026-08-20",
            offset=offset,
            ce_candles=tuple(),
            pe_candles=tuple(),
        )
        for offset in range(-2, 3)
    )

    assert directional_two_strike_contract(window, "CE").strike == 25100
    assert directional_two_strike_contract(window, "PE").strike == 24900
    assert directional_two_strike_contract(window, "NONE") is None
