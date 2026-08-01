from datetime import datetime
from tempfile import TemporaryDirectory

from nandi_oi.alerts import AlertDelivery
from nandi_oi.market_schedule import IST, MarketSchedule
from nandi_oi.models import Decision, OptionLeg, OptionSnapshot
from nandi_oi.store import NandiStore
from nandi_oi.worker import NandiWorker


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
        report = worker.ensure_daily_report(now.replace(hour=16))
        assert report["snapshots"] == 1
        assert store.recent_alerts()[0]["level"] == "DAILY_REPORT"


def test_worker_schedule_blocks_a_weekend_session():
    worker = NandiWorker(client=FakeClient(), engine=FakeEngine(), store=NandiStore(":memory:"),
                         alert_sink=FakeAlertSink(), schedule=MarketSchedule())
    assert worker.schedule.status(datetime(2026, 8, 1, 10, 0, tzinfo=IST)).state == "WEEKEND"
