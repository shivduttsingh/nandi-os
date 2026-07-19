from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import OptionLeg, OptionSnapshot


class UpstoxAPIError(RuntimeError):
    pass


class UpstoxOptionChainClient:
    """Read-only Upstox option-chain adapter for NIFTY paper research."""

    BASE_URL = "https://api.upstox.com/v2"

    def __init__(
        self,
        access_token: str | None = None,
        instrument_key: str = "NSE_INDEX|Nifty 50",
        expiry: str = "current_week",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.access_token = access_token or os.getenv("UPSTOX_ACCESS_TOKEN", "")
        self.instrument_key = instrument_key
        self.expiry = expiry
        self.timeout_seconds = timeout_seconds
        self._last: dict[str, tuple[float, float]] = {}
        self._prior_spots: deque[float] = deque(maxlen=20)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.access_token:
            raise UpstoxAPIError("UPSTOX_ACCESS_TOKEN is missing")
        request = Request(
            f"{self.BASE_URL}{path}?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "Nandi-OI-Research/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise UpstoxAPIError(f"Upstox returned HTTP {exc.code}: {body[:500]}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UpstoxAPIError(f"Unable to read Upstox data: {exc}") from exc
        if payload.get("status") != "success":
            raise UpstoxAPIError(f"Upstox request failed: {payload}")
        return payload

    def fetch_raw_chain(self) -> list[dict[str, Any]]:
        payload = self._get(
            "/option/chain",
            {"instrument_key": self.instrument_key, "expiry_date": self.expiry},
        )
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise UpstoxAPIError("Upstox returned an empty option chain")
        return data

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def parse_chain(self, rows: list[dict[str, Any]], timestamp: datetime | None = None) -> OptionSnapshot:
        if not rows:
            raise UpstoxAPIError("Cannot parse an empty option chain")
        spot = self._number(rows[0].get("underlying_spot_price"))
        if spot <= 0:
            raise UpstoxAPIError("Upstox option chain did not contain a valid NIFTY spot price")

        prior_spot = self._prior_spots[-1] if self._prior_spots else spot
        recent_high = max(self._prior_spots) if self._prior_spots else spot
        recent_low = min(self._prior_spots) if self._prior_spots else spot
        legs: list[OptionLeg] = []

        for row in rows:
            strike = self._number(row.get("strike_price"))
            for source, side in (("call_options", "CE"), ("put_options", "PE")):
                option = row.get(source) or {}
                key = str(option.get("instrument_key") or f"{strike}:{side}")
                market = option.get("market_data") or {}
                oi = self._number(market.get("oi"))
                ltp = self._number(market.get("ltp"))
                previous = self._last.get(key)
                # First snapshot uses Upstox prev_oi and close; subsequent snapshots use actual deltas.
                previous_oi = previous[0] if previous else self._number(market.get("prev_oi"))
                previous_ltp = previous[1] if previous else self._number(market.get("close_price"))
                legs.append(
                    OptionLeg(
                        strike=strike,
                        side=side,
                        oi=oi,
                        change_oi=oi - previous_oi,
                        ltp=ltp,
                        change_ltp=ltp - previous_ltp,
                        volume=self._number(market.get("volume")),
                        bid=self._number(market.get("bid_price")),
                        ask=self._number(market.get("ask_price")),
                    )
                )
                self._last[key] = (oi, ltp)

        self._prior_spots.append(spot)
        return OptionSnapshot(
            timestamp=timestamp or datetime.now(),
            spot=spot,
            spot_change=spot - prior_spot,
            recent_high=recent_high,
            recent_low=recent_low,
            legs=tuple(legs),
            expiry=str(rows[0].get("expiry") or self.expiry),
        )

    def fetch_snapshot(self) -> OptionSnapshot:
        return self.parse_chain(self.fetch_raw_chain())
