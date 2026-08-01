from datetime import datetime
import os
from tempfile import TemporaryDirectory

from nandi_oi.alerts import AlertDelivery
from nandi_oi.market_schedule import IST, MarketSchedule
from nandi_oi.models import Decision, OptionLeg, OptionSnapshot
from nandi_oi.store import NandiStore
from nandi_oi.worker import NandiWorker, preflight_report


class FakeClient:
    def fetch_snapshot(self, timestamp=None):
        return OptionSnapshot(timestamp, 25000, 20, 24990, 24950,
                              (OptionLeg(25000, "CE", 1000, -100, 120, 10, volume=100),
                               OptionLeg(25000, "PE", 1200, 100, 90, -5, volume=100)), "2026-08-06")


class FakeEngine:
    def add_snapshot(self, _snapshot):
        return Decision("BUY CE", 85, 25, 85, True, 25000, ("OI confirmed",), ())


class FakeAlertSink:
    def __init__(self):
        self.sent = []

    def send(self, title, message, level="INFO"):
        self.sent.append((title, message, level))
        return AlertDelivery(True)


def test_worker_saves_analysis_health_and_alert():
    with TemporaryDirectory() as folder:
        store = NandiStore(f"{folder}/nandi.db")
        alerts = FakeAlertSink()
        worker = NandiWorker(client=FakeClient(), engine=FakeEngine(), store=store, alert_sink=alerts, interval_seconds=30)
        now = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
        snapshot, decision = worker.run_once(now)
        assert snapshot.spot == 25000
        assert decision.action == "BUY CE"
        assert store.latest_analysis()["action"] == "BUY CE"
        assert store.worker_status()["last_snapshot"].startswith("2026-08-03T10:00")
        assert len(alerts.sent) == 1
        # An unchanged approved action should not produce a second alert, even
        # when a new worker object is created after a restart.
        restarted = NandiWorker(client=FakeClient(), engine=FakeEngine(), store=store,
                                alert_sink=alerts, interval_seconds=30)
        restarted.run_once(now)
        assert len(alerts.sent) == 1
        assert store.worker_last_action() == "BUY CE"
        report = worker.ensure_daily_report(now.replace(hour=16))
        assert report["snapshots"] == 2
        assert store.recent_alerts()[0]["level"] == "DAILY_REPORT"


def test_worker_schedule_blocks_a_weekend_session():
    worker = NandiWorker(client=FakeClient(), engine=FakeEngine(), store=NandiStore(":memory:"),
                         alert_sink=FakeAlertSink(), schedule=MarketSchedule())
    assert worker.schedule.status(datetime(2026, 8, 1, 10, 0, tzinfo=IST)).state == "WEEKEND"


def test_preflight_checks_local_storage_and_token_without_network_request():
    original_token = os.environ.get("UPSTOX_ACCESS_TOKEN")
    original_path = os.environ.get("NANDI_DB_PATH")
    try:
        with TemporaryDirectory() as folder:
            os.environ["UPSTOX_ACCESS_TOKEN"] = "read-only-test-token"
            os.environ["NANDI_DB_PATH"] = f"{folder}/nandi.db"
            ready, checks = preflight_report(datetime(2026, 8, 3, 10, 0, tzinfo=IST))
        assert ready
        assert {item["Check"] for item in checks} == {
            "Local database", "Upstox analytics token", "NSE schedule",
        }
    finally:
        if original_token is None:
            os.environ.pop("UPSTOX_ACCESS_TOKEN", None)
        else:
            os.environ["UPSTOX_ACCESS_TOKEN"] = original_token
        if original_path is None:
            os.environ.pop("NANDI_DB_PATH", None)
        else:
            os.environ["NANDI_DB_PATH"] = original_path
