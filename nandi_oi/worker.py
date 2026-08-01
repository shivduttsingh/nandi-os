from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from datetime import datetime
from typing import Any

from .alerts import AlertSink, WebhookAlertSink
from .configuration import is_configured_value
from .engine import NandiOIEngine
from .market_schedule import IST, MarketSchedule
from .models import Decision, OptionSnapshot
from .store import NandiStore
from .upstox import UpstoxAPIError, UpstoxOptionChainClient


LOGGER = logging.getLogger("nandi.worker")


def schedule_from_environment() -> MarketSchedule:
    return MarketSchedule.from_iso_dates(os.getenv("NANDI_NSE_HOLIDAYS", "").split(","))


class NandiWorker:
    """Independent paper-research process; it has no broker-order capability."""

    def __init__(self, client: UpstoxOptionChainClient | None = None, engine: NandiOIEngine | None = None,
                 store: NandiStore | None = None, alert_sink: AlertSink | None = None,
                 schedule: MarketSchedule | None = None, interval_seconds: int | None = None) -> None:
        self.client = client or UpstoxOptionChainClient()
        self.engine = engine or NandiOIEngine()
        self.store = store or NandiStore()
        self.alert_sink = alert_sink or WebhookAlertSink()
        self.schedule = schedule or schedule_from_environment()
        self.interval_seconds = max(10, interval_seconds or int(os.getenv("NANDI_CAPTURE_SECONDS", "30")))
        self.running = True
        self.worker_name = "nandi-live"

    @staticmethod
    def _explanation(snapshot: OptionSnapshot, decision: Decision) -> str:
        direction = decision.action if decision.approved else "WAIT"
        details = "; ".join((decision.reasons if decision.approved else decision.blockers)[:3]) or "Evidence is still forming"
        strike = f" {decision.selected_strike:.0f}" if decision.selected_strike else ""
        return (
            f"{direction}{strike} | NIFTY {snapshot.spot:.2f} | Bullish {decision.bullish_score:.1f} | "
            f"Bearish {decision.bearish_score:.1f} | Setup quality {decision.confidence:.1f}/100 | "
            f"{details} | Data {snapshot.timestamp.strftime('%H:%M:%S')} IST"
        )

    def run_once(self, now: datetime | None = None) -> tuple[OptionSnapshot, Decision]:
        local_now = self.schedule.to_ist(now or datetime.now(IST))
        snapshot = self.client.fetch_snapshot(timestamp=local_now.replace(tzinfo=None))
        decision = self.engine.add_snapshot(snapshot)
        decision_id = self.store.save_analysis(snapshot, decision, source="background-worker")
        self.store.heartbeat("RUNNING", "MARKET_OPEN", last_snapshot=snapshot.timestamp, now=local_now.replace(tzinfo=None))
        previous_action = self.store.worker_last_action(self.worker_name)
        if decision.approved and decision.action != previous_action:
            title = f"Nandi approved {decision.action}"
            message = self._explanation(snapshot, decision)
            delivery = self.alert_sink.send(title, message, "TRADE_SETUP")
            self.store.record_alert("TRADE_SETUP", title, message, decision_id, delivered=delivery.delivered,
                                    delivery_error=delivery.error, now=local_now.replace(tzinfo=None))
        self.store.set_worker_last_action(
            decision.action, worker_name=self.worker_name, now=local_now.replace(tzinfo=None),
        )
        return snapshot, decision

    def stop(self, *_args) -> None:
        self.running = False

    def ensure_daily_report(self, now: datetime) -> dict | None:
        local = self.schedule.to_ist(now)
        status = self.schedule.status(local)
        if status.state != "MARKET_CLOSED":
            return None
        if self.store.daily_report(local.date()):
            return self.store.daily_report(local.date())
        report = self.store.build_daily_report(local.date(), now=local.replace(tzinfo=None))
        if report:
            title = f"Nandi daily report — {local.date().isoformat()}"
            delivery = self.alert_sink.send(title, report["summary"], "DAILY_REPORT")
            self.store.record_alert("DAILY_REPORT", title, report["summary"], delivered=delivery.delivered,
                                    delivery_error=delivery.error, now=local.replace(tzinfo=None))
        return report

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        LOGGER.info("Nandi background worker started")
        backoff = self.interval_seconds
        while self.running:
            now = datetime.now(IST)
            market_status = self.schedule.status(now)
            if not market_status.is_open:
                self.ensure_daily_report(now)
                self.store.heartbeat("IDLE", market_status.state, now=market_status.observed_at.replace(tzinfo=None))
                time.sleep(min(60, self.interval_seconds))
                continue
            try:
                snapshot, decision = self.run_once(now)
                LOGGER.info("%s NIFTY %.2f quality %.1f", decision.action, snapshot.spot, decision.confidence)
                backoff = self.interval_seconds
                time.sleep(self.interval_seconds)
            except UpstoxAPIError as exc:
                LOGGER.error("Upstox error: %s", exc)
                self.store.heartbeat("DEGRADED", market_status.state, error=str(exc), now=now.replace(tzinfo=None))
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
            except Exception as exc:
                LOGGER.exception("Unexpected worker error")
                self.store.heartbeat("ERROR", market_status.state, error=str(exc), now=now.replace(tzinfo=None))
                time.sleep(min(60, backoff))
        stopped_at = datetime.now(IST)
        self.store.heartbeat("STOPPED", self.schedule.status(stopped_at).state, now=stopped_at.replace(tzinfo=None))


def preflight_report(now: datetime | None = None) -> tuple[bool, list[dict[str, Any]]]:
    """Check the local worker setup without contacting Upstox or exposing secrets."""
    schedule = schedule_from_environment()
    status = schedule.status(now or datetime.now(IST))
    checks: list[dict[str, Any]] = []
    try:
        store = NandiStore()
        store.worker_status()
        checks.append({"Check": "Local database", "Status": "READY", "Detail": str(store.path)})
    except Exception as exc:
        checks.append({"Check": "Local database", "Status": "ERROR", "Detail": str(exc)})
    token_ready = is_configured_value(os.getenv("UPSTOX_ACCESS_TOKEN", ""))
    checks.append({
        "Check": "Upstox analytics token",
        "Status": "READY" if token_ready else "NEEDS ACTION",
        "Detail": "Configured" if token_ready else "Add a real read-only token to UPSTOX_ACCESS_TOKEN in .env.",
    })
    checks.append({
        "Check": "NSE schedule",
        "Status": status.state,
        "Detail": f"{status.reason} Next session: {status.next_open.strftime('%d %b %Y %I:%M %p IST')}.",
    })
    return all(item["Status"] != "ERROR" for item in checks) and token_ready, checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Nandi always-on paper research worker")
    parser.add_argument("--once", action="store_true", help="Capture one snapshot regardless of market hours")
    parser.add_argument("--check", action="store_true", help="Check local setup without requesting market data")
    parser.add_argument("--interval", type=int, default=None, help="Seconds between live snapshots")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("NANDI_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.check:
        ready, checks = preflight_report()
        for check in checks:
            LOGGER.info("%s: %s — %s", check["Check"], check["Status"], check["Detail"])
        if not ready:
            raise SystemExit(1)
        return
    worker = NandiWorker(interval_seconds=args.interval)
    if args.once:
        snapshot, decision = worker.run_once()
        LOGGER.info("Captured %.2f: %s", snapshot.spot, decision.action)
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
