from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class AlertDelivery:
    delivered: bool
    error: str = ""


class AlertSink(Protocol):
    def send(self, title: str, message: str, level: str = "INFO") -> AlertDelivery: ...


class WebhookAlertSink:
    """Optional webhook delivery; the local alert history works without it."""

    def __init__(self, url: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.url = url or os.getenv("NANDI_ALERT_WEBHOOK_URL", "")
        self.timeout_seconds = timeout_seconds

    def send(self, title: str, message: str, level: str = "INFO") -> AlertDelivery:
        if not self.url:
            return AlertDelivery(False, "Outbound alert webhook is not configured")
        payload = json.dumps({"title": title, "message": message, "level": level}).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "Nandi-Research/1.0"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if 200 <= response.status < 300:
                    return AlertDelivery(True)
                return AlertDelivery(False, f"Webhook returned HTTP {response.status}")
        except HTTPError as exc:
            return AlertDelivery(False, f"Webhook returned HTTP {exc.code}")
        except (URLError, TimeoutError) as exc:
            return AlertDelivery(False, f"Webhook delivery failed: {exc}")
