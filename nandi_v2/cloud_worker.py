from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass, replace
from datetime import datetime, time as clock_time
from zoneinfo import ZoneInfo

from .email_alerts import SMTPEmailAlertSink, SMTPSettings
from .engine import decide
from .history import DecisionHistory
from .lifecycle import TradeState, TradeStatus, advance_trade_state
from .models import Decision, DecisionAction, MarketContext, OptionChainSnapshot
from .nse import NSEDataError, NSEPublicClient

IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger("nandi.cloud_worker")


@dataclass(frozen=True)
class WorkerConfig:
    warm_start: clock_time = clock_time(8, 55)
    regular_open: clock_time = clock_time(9, 15)
    regular_close: clock_time = clock_time(15, 30)
    shutdown_at: clock_time = clock_time(16, 0)
    oi_refresh_seconds: int = 30
    spot_refresh_seconds: int = 3
    idle_sleep_seconds: int = 30
    trade_threshold: float = 75.0
    email_threshold: float = 80.0
    confirmation_snapshots: int = 2
    db_path: str = "data/nandi_v2.sqlite"
    holidays: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        def _int(name: str, default: int, legacy: str | None = None) -> int:
            raw = os.getenv(name)
            if raw is None and legacy:
                raw = os.getenv(legacy)
            try:
                return int(raw if raw is not None else default)
            except ValueError:
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except ValueError:
                return default

        holidays = frozenset(
            item.strip() for item in os.getenv("NANDI_NSE_HOLIDAYS", "").split(",") if item.strip()
        )
        return cls(
            oi_refresh_seconds=max(15, _int("NANDI_NSE_OI_REFRESH_SECONDS", 30, "NANDI_OI_REFRESH_SECONDS")),
            spot_refresh_seconds=max(2, _int("NANDI_NSE_SPOT_REFRESH_SECONDS", 3, "NANDI_SPOT_REFRESH_SECONDS")),
            idle_sleep_seconds=max(5, _int("NANDI_IDLE_SLEEP_SECONDS", 30)),
            trade_threshold=_float("NANDI_TRADE_THRESHOLD", 75.0),
            email_threshold=_float("NANDI_EMAIL_THRESHOLD", 80.0),
            confirmation_snapshots=max(1, _int("NANDI_CONFIRMATION_SNAPSHOTS", 2)),
            db_path=os.getenv("NANDI_DB_PATH", "data/nandi_v2.sqlite"),
            holidays=holidays,
        )


def is_weekday(now: datetime) -> bool:
    return now.astimezone(IST).weekday() < 5


def worker_window(now: datetime, config: WorkerConfig) -> str:
    local = now.astimezone(IST)
    if not is_weekday(local) or local.date().isoformat() in config.holidays:
        return "OFF"
    current = local.time().replace(tzinfo=None)
    if current < config.warm_start or current >= config.shutdown_at:
        return "OFF"
    if current < config.regular_open:
        return "WARMUP"
    if current <= config.regular_close:
        return "LIVE"
    return "COOLDOWN"


def _momentum_rsi(points: list[tuple[datetime, float]], period: int = 14) -> float | None:
    if len(points) < period + 1:
        return None
    prices = [value for _, value in points]
    changes = [b - a for a, b in zip(prices, prices[1:])][-period:]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _context(now: datetime, points: list[tuple[datetime, float]]) -> MarketContext:
    prices = [value for _, value in points]
    previous = prices[-2] if len(prices) >= 2 else None
    reference = prices[-41:-1] if len(prices) >= 3 else prices[:-1]
    return MarketContext(
        observed_at=now,
        previous_spot=previous,
        recent_high=max(reference) if reference else None,
        recent_low=min(reference) if reference else None,
        momentum_rsi=_momentum_rsi(points),
    )


class NandiCloudWorker:
    """Browser-independent NIFTY research worker."""

    def __init__(self, config: WorkerConfig | None = None) -> None:
        self.config = config or WorkerConfig.from_env()
        self.nse = NSEPublicClient()
        self.history = DecisionHistory(self.config.db_path)
        self.email = SMTPEmailAlertSink(SMTPSettings.from_mapping())
        restored = self.history.latest_trade_state()
        today = datetime.now(IST).date()
        if restored.active and restored.opened_at is not None:
            opened = restored.opened_at.astimezone(IST) if restored.opened_at.tzinfo else restored.opened_at.replace(tzinfo=IST)
            if opened.date() != today:
                restored = TradeState(status=TradeStatus.FLAT, updated_at=datetime.now(IST), reason="Previous-session cloud trade state expired on restart.")
        self.trade_state = restored
        self.latest_chain: OptionChainSnapshot | None = None
        self.latest_spot: float | None = None
        self.points: list[tuple[datetime, float]] = []
        self.last_oi_fetch = 0.0
        self.last_spot_fetch = 0.0
        self.last_confirmed_snapshot = ""
        self.candidate_side = ""
        self.candidate_count = 0
        self.stop_requested = False

    def request_stop(self, *_: object) -> None:
        self.stop_requested = True

    def _append_spot(self, stamp: datetime, spot: float) -> None:
        item = (stamp, float(spot))
        if not self.points or self.points[-1] != item:
            self.points.append(item)
        self.points = self.points[-1200:]

    def _refresh(self, monotonic_now: float, *, force_oi: bool = False) -> None:
        if force_oi or self.latest_chain is None or monotonic_now - self.last_oi_fetch >= self.config.oi_refresh_seconds:
            try:
                chain = self.nse.fetch_option_chain("NIFTY")
                self.latest_chain = chain
                self.latest_spot = chain.spot
                self._append_spot(chain.timestamp, chain.spot)
                LOG.info("OI snapshot %s spot %.2f expiry %s", chain.timestamp.isoformat(), chain.spot, chain.expiry)
            except NSEDataError as exc:
                LOG.warning("OI refresh failed: %s", exc)
            finally:
                self.last_oi_fetch = monotonic_now

        if self.latest_spot is None or monotonic_now - self.last_spot_fetch >= self.config.spot_refresh_seconds:
            try:
                spot, stamp = self.nse.fetch_nifty_spot()
                self.latest_spot = spot
                self._append_spot(stamp, spot)
            except NSEDataError as exc:
                LOG.warning("Spot refresh failed: %s", exc)
            finally:
                self.last_spot_fetch = monotonic_now

    def _confirm(self, raw: Decision) -> Decision:
        if raw.action not in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
            self.candidate_side = ""
            self.candidate_count = 0
            self.last_confirmed_snapshot = ""
            return raw
        snapshot_key = raw.data_timestamp.isoformat() if raw.data_timestamp else ""
        if snapshot_key == self.last_confirmed_snapshot:
            count = self.candidate_count
        elif self.candidate_side == raw.side:
            count = self.candidate_count + 1
        else:
            count = 1
        self.candidate_side = raw.side
        self.candidate_count = count
        self.last_confirmed_snapshot = snapshot_key
        if count >= self.config.confirmation_snapshots:
            return raw
        action = DecisionAction.PREPARE_CE if raw.side == "CE" else DecisionAction.PREPARE_PE
        return replace(raw, action=action, blockers=tuple(dict.fromkeys(raw.blockers + (f"Waiting for fresh NSE confirmation {count}/{self.config.confirmation_snapshots}",))))

    def _record_decision(self, decision: Decision, snapshot: OptionChainSnapshot) -> str:
        stamp = decision.data_timestamp or decision.generated_at or datetime.now(IST)
        unique_key = f"{stamp.isoformat()}:{decision.side}:{int(decision.selected_strike or 0)}:{snapshot.expiry}"
        self.history.append(decision, snapshot.spot, snapshot.expiry, signal_key=unique_key)
        return unique_key

    def _send_entry_email(self, decision: Decision, snapshot: OptionChainSnapshot, signal_key: str) -> None:
        if decision.action not in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
            return
        if decision.score < self.config.email_threshold or self.history.alert_exists(signal_key):
            return
        delivery = self.email.send_decision(decision, snapshot.spot, snapshot.expiry)
        self.history.record_alert(signal_key, delivery.delivered, delivery.error)
        if delivery.delivered:
            LOG.info("Trade alert emailed: %s score %.1f", decision.action.value, decision.score)
        else:
            LOG.warning("Trade email not delivered: %s", delivery.error)

    def evaluate_once(self, now: datetime | None = None) -> Decision | None:
        now = (now or datetime.now(IST)).astimezone(IST)
        mode = worker_window(now, self.config)
        if mode == "OFF":
            return None
        monotonic_now = time.monotonic()
        self._refresh(monotonic_now, force_oi=(mode == "WARMUP" and self.latest_chain is None))
        if self.latest_chain is None or self.latest_spot is None:
            return None
        snapshot = replace(self.latest_chain, spot=float(self.latest_spot))
        context = _context(now, self.points)
        self.history.append_market_frame(snapshot, context)
        raw = decide(snapshot, context, trade_threshold=self.config.trade_threshold, prepare_threshold=max(60.0, self.config.trade_threshold - 10.0))
        decision = self._confirm(raw) if mode == "LIVE" else replace(raw, action=DecisionAction.NO_TRADE, blockers=tuple(dict.fromkeys((f"Worker mode {mode}: new entries disabled",) + raw.blockers)))
        previous = self.trade_state
        if mode == "LIVE":
            current = advance_trade_state(previous, decision, snapshot.spot, now)
        elif previous.active:
            current = advance_trade_state(previous, decision, snapshot.spot, now)
            if mode == "COOLDOWN":
                current = replace(current, status=TradeStatus.EXIT, updated_at=now, reason="Regular NSE session ended; cloud worker closed the research trade state.")
        else:
            current = TradeState(status=TradeStatus.FLAT, updated_at=now, reason=f"Worker mode {mode}.")
        self.trade_state = current
        if current != previous:
            self.history.append_trade_event(current, spot=snapshot.spot, decision=decision)
        signal_key = self._record_decision(decision, snapshot)
        if not previous.active and current.active:
            self._send_entry_email(decision, snapshot, signal_key)
        return decision

    def run_forever(self) -> None:
        LOG.info("Nandi cloud worker started; trading-day window 08:55-16:00 IST")
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        while not self.stop_requested:
            now = datetime.now(IST)
            mode = worker_window(now, self.config)
            if mode == "OFF":
                time.sleep(self.config.idle_sleep_seconds)
                continue
            try:
                self.evaluate_once(now)
            except Exception:
                LOG.exception("Unhandled worker iteration failure")
            time.sleep(1 if mode == "LIVE" else 5)
        LOG.info("Nandi cloud worker stopped")


def main() -> None:
    logging.basicConfig(level=os.getenv("NANDI_LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    NandiCloudWorker().run_forever()


if __name__ == "__main__":
    main()
