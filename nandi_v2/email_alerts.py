from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Mapping

from .models import Decision, DecisionAction


@dataclass(frozen=True)
class SMTPSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_tls: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.port and self.username and self.password and self.sender and self.recipient)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None = None) -> "SMTPSettings":
        data = values or {}
        return cls(
            host=str(data.get("host") or os.getenv("NANDI_SMTP_HOST", "")),
            port=int(data.get("port") or os.getenv("NANDI_SMTP_PORT", "587")),
            username=str(data.get("username") or os.getenv("NANDI_SMTP_USERNAME", "")),
            password=str(data.get("password") or os.getenv("NANDI_SMTP_PASSWORD", "")),
            sender=str(data.get("sender") or os.getenv("NANDI_ALERT_SENDER", "")),
            recipient=str(data.get("recipient") or os.getenv("NANDI_ALERT_RECIPIENT", "")),
            use_tls=str(data.get("use_tls", os.getenv("NANDI_SMTP_USE_TLS", "true"))).lower() not in {"0", "false", "no"},
        )


@dataclass(frozen=True)
class EmailDelivery:
    delivered: bool
    error: str = ""


class SMTPEmailAlertSink:
    def __init__(self, settings: SMTPSettings, timeout_seconds: float = 15.0) -> None:
        self.settings = settings
        self.timeout_seconds = timeout_seconds

    def send_decision(self, decision: Decision, spot: float, expiry: str) -> EmailDelivery:
        if not self.settings.configured:
            return EmailDelivery(False, "SMTP email alerts are not configured")
        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = self.settings.recipient
        message["Subject"] = f"NANDI ALERT — {decision.action.value} — Score {decision.score:.1f}"
        levels = decision.levels
        reasons = "\n".join(f"{index}. {reason}" for index, reason in enumerate(decision.reasons, 1))
        message.set_content("\n".join([
            "NANDI LIVE TRADE ALERT", "", f"Decision: {decision.action.value}", f"Setup score: {decision.score:.1f}/100",
            f"CE score: {decision.ce_score:.1f}/100", f"PE score: {decision.pe_score:.1f}/100", f"Market state: {decision.market_state}",
            f"NIFTY spot: {spot:.2f}", f"Expiry: {expiry}", f"Selected strike: {decision.selected_strike:.0f}" if decision.selected_strike else "Selected strike: —", "",
            f"Entry: {levels.entry:.2f}" if levels.entry is not None else "Entry: —",
            f"Spot stop-loss: {levels.stop:.2f}" if levels.stop is not None else "Spot stop-loss: —",
            f"Target 1: {levels.target_1:.2f}" if levels.target_1 is not None else "Target 1: —",
            f"Target 2: {levels.target_2:.2f}" if levels.target_2 is not None else "Target 2: —",
            f"Reward-risk: 1:{levels.reward_risk:.2f}" if levels.reward_risk is not None else "Reward-risk: —", "", "Key evidence:",
            reasons or "No evidence text was recorded.", "", f"Signal time: {decision.generated_at.isoformat() if decision.generated_at else '—'}",
            f"OI data time: {decision.data_timestamp.isoformat() if decision.data_timestamp else '—'}", "",
            "This is a research alert, not an order. Confirm the live market and risk before acting.",
        ]))
        try:
            with smtplib.SMTP(self.settings.host, self.settings.port, timeout=self.timeout_seconds) as client:
                if self.settings.use_tls:
                    client.starttls(context=ssl.create_default_context())
                client.login(self.settings.username, self.settings.password)
                client.send_message(message)
            return EmailDelivery(True)
        except (OSError, smtplib.SMTPException) as exc:
            return EmailDelivery(False, f"Email delivery failed: {exc}")


def is_entry_alert(decision: Decision, minimum_score: float = 80.0) -> bool:
    return decision.action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE} and decision.score >= minimum_score
