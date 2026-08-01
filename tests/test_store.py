from datetime import datetime
from tempfile import TemporaryDirectory

from nandi_oi.models import Decision, OptionLeg, OptionSnapshot
from nandi_oi.store import NandiStore


def sample_analysis():
    snapshot = OptionSnapshot(
        datetime(2026, 8, 3, 10, 0), 25000, 10, 25010, 24980,
        (OptionLeg(25000, "CE", 1000, -100, 120, 10, volume=500),
         OptionLeg(25000, "PE", 1200, 150, 90, -5, volume=600)), "2026-08-06",
    )
    decision = Decision("BUY CE", 84, 30, 84, True, 25000, ("Put writing confirmed",), ())
    return snapshot, decision


def test_store_persists_analysis_health_and_alerts():
    with TemporaryDirectory() as folder:
        store = NandiStore(f"{folder}/nandi.db")
        snapshot, decision = sample_analysis()
        decision_id = store.save_analysis(snapshot, decision)
        store.heartbeat("RUNNING", "MARKET_OPEN", last_snapshot=snapshot.timestamp, now=snapshot.timestamp)
        store.record_alert("TRADE_SETUP", "BUY CE", "Evidence", decision_id, now=snapshot.timestamp)

        latest = store.latest_analysis()
        assert latest is not None
        assert latest["action"] == "BUY CE"
        assert latest["evidence"]["score"][0]["Evidence"] == "Bullish score"
        assert store.worker_status()["status"] == "RUNNING"
        assert store.recent_alerts()[0]["title"] == "BUY CE"
        report = store.build_daily_report(snapshot.timestamp.date(), now=snapshot.timestamp)
        assert report["snapshots"] == 1
        assert report["buy_ce_setups"] == 1
        store.save_rsi_strategy("RSI 14 — 24/72", 14, 24, 72, now=snapshot.timestamp)
        assert store.rsi_strategies() == {
            "RSI 14 — 24/72": {"length": 14, "lower": 24.0, "upper": 72.0}
        }
