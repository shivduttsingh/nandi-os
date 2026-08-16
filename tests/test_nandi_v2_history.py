from datetime import datetime
from zoneinfo import ZoneInfo

from nandi_v2.history import DecisionHistory
from nandi_v2.lifecycle import TradeState, TradeStatus
from nandi_v2.models import MarketContext, OptionChainSnapshot, OptionLeg, StrikeRow


IST = ZoneInfo("Asia/Kolkata")


def test_trade_lifecycle_state_persists_across_store_instances(tmp_path):
    path = tmp_path / "nandi.sqlite"
    store = DecisionHistory(str(path))
    now = datetime(2026, 8, 7, 10, 15, tzinfo=IST)
    state = TradeState(
        status=TradeStatus.ACTIVE_CE,
        side="CE",
        entry_spot=24650.0,
        stop_spot=24620.0,
        target_1=24700.0,
        target_2=24750.0,
        selected_strike=24650.0,
        opened_at=now,
        updated_at=now,
        reason="Confirmed Nandi entry.",
    )

    assert store.append_trade_event(state, spot=24650.0)
    restored = DecisionHistory(str(path)).latest_trade_state()

    assert restored.status == TradeStatus.ACTIVE_CE
    assert restored.side == "CE"
    assert restored.entry_spot == 24650.0
    assert restored.stop_spot == 24620.0
    assert restored.target_2 == 24750.0
    assert restored.opened_at == now


def test_trade_event_is_deduplicated(tmp_path):
    store = DecisionHistory(str(tmp_path / "nandi.sqlite"))
    now = datetime(2026, 8, 7, 10, 20, tzinfo=IST)
    state = TradeState(status=TradeStatus.PREPARE_PE, side="PE", selected_strike=24600.0, updated_at=now)

    assert store.append_trade_event(state, spot=24620.0)
    assert not store.append_trade_event(state, spot=24620.0)
    assert len(store.recent_trade_events()) == 1
    assert store.trade_events()[0]["Status"] == TradeStatus.PREPARE_PE.value


def test_market_frames_round_trip_for_replay(tmp_path):
    store = DecisionHistory(str(tmp_path / "nandi.sqlite"))
    stamp = datetime(2026, 8, 7, 10, 30, tzinfo=IST)
    snapshot = OptionChainSnapshot(
        timestamp=stamp,
        expiry="13-Aug-2026",
        spot=24650.0,
        rows=(
            StrikeRow(
                24650.0,
                OptionLeg(ltp=120.0, change=4.0, oi=1000.0, change_oi=-100.0, volume=200.0, iv=12.0),
                OptionLeg(ltp=110.0, change=-3.0, oi=1400.0, change_oi=150.0, volume=180.0, iv=13.0),
            ),
        ),
        source="NSE test rolling delta",
        raw_timestamp="07-Aug-2026 10:30:00",
    )
    context = MarketContext(
        observed_at=stamp,
        previous_spot=24640.0,
        recent_high=24655.0,
        recent_low=24620.0,
        momentum_rsi=58.0,
    )

    assert store.append_market_frame(snapshot, context)
    assert not store.append_market_frame(snapshot, context)
    snapshots, contexts = store.replay_data("2026-08-07")

    assert len(snapshots) == 1
    assert snapshots[0].timestamp == stamp
    assert snapshots[0].rows[0].ce.change_oi == -100.0
    assert snapshots[0].source == "NSE test rolling delta"
    assert contexts[0].observed_at == stamp
    assert contexts[0].momentum_rsi == 58.0
    assert store.replay_days() == ["2026-08-07"]
