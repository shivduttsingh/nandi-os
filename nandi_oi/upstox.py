from __future__ import annotations

import json
import os
from collections import deque
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .configuration import is_configured_value
from .market_schedule import IST
from .models import IntradayCandle, OptionLeg, OptionSnapshot


class UpstoxAPIError(RuntimeError):
    pass


class UpstoxOptionChainClient:
    """Read-only Upstox option-chain adapter for NIFTY paper research."""

    BASE_URL = "https://api.upstox.com/v2"
    BASE_URL_V3 = "https://api.upstox.com/v3"

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

    def _request_json(self, url: str) -> dict[str, Any]:
        if not is_configured_value(self.access_token):
            raise UpstoxAPIError(
                "UPSTOX_ACCESS_TOKEN is missing or still contains the sample placeholder"
            )
        request = Request(
            url,
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

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        return self._request_json(f"{self.BASE_URL}{path}?{urlencode(params)}")

    def _get_v3(self, path: str) -> dict[str, Any]:
        return self._request_json(f"{self.BASE_URL_V3}{path}")

    @staticmethod
    def _validate_candle_interval(interval_minutes: int) -> None:
        if not 1 <= interval_minutes <= 300:
            raise ValueError("Upstox minute interval must be between 1 and 300")

    @classmethod
    def _parse_candles(cls, payload: dict[str, Any]) -> tuple[IntradayCandle, ...]:
        rows = (payload.get("data") or {}).get("candles") or []
        candles: list[IntradayCandle] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            try:
                timestamp = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                if timestamp.tzinfo is not None:
                    timestamp = timestamp.astimezone(IST).replace(tzinfo=None)
                opened, high, low, closed = (float(row[index]) for index in range(1, 5))
            except (TypeError, ValueError):
                continue
            if min(opened, high, low, closed) <= 0 or high < low:
                continue
            candles.append(
                IntradayCandle(
                    timestamp=timestamp,
                    open=opened,
                    high=high,
                    low=low,
                    close=closed,
                    volume=cls._number(row[5]) if len(row) > 5 else 0.0,
                    open_interest=cls._number(row[6]) if len(row) > 6 else 0.0,
                )
            )
        return tuple(sorted(candles, key=lambda candle: candle.timestamp))

    def fetch_intraday_candles(self, interval_minutes: int = 15) -> tuple[IntradayCandle, ...]:
        """Return today's NIFTY candles from Upstox V3, oldest first."""
        self._validate_candle_interval(interval_minutes)
        instrument = quote(self.instrument_key, safe="")
        payload = self._get_v3(
            f"/historical-candle/intraday/{instrument}/minutes/{interval_minutes}"
        )
        candles = self._parse_candles(payload)
        if not candles:
            raise UpstoxAPIError("Upstox returned no valid NIFTY intraday candles")
        return candles

    def fetch_historical_candles(
        self,
        from_date: date,
        to_date: date,
        interval_minutes: int = 15,
    ) -> tuple[IntradayCandle, ...]:
        """Return a bounded Upstox V3 history window, oldest first."""
        self._validate_candle_interval(interval_minutes)
        if from_date > to_date:
            raise ValueError("Historical candle from_date must be on or before to_date")
        span_days = (to_date - from_date).days + 1
        maximum_days = 31 if interval_minutes <= 15 else 92
        if span_days > maximum_days:
            raise ValueError(
                f"Upstox allows at most {maximum_days} calendar days for this minute interval"
            )
        instrument = quote(self.instrument_key, safe="")
        payload = self._get_v3(
            f"/historical-candle/{instrument}/minutes/{interval_minutes}/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )
        candles = self._parse_candles(payload)
        if not candles:
            raise UpstoxAPIError("Upstox returned no valid NIFTY historical candles")
        return candles

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
            # Upstox responses do not provide a capture timestamp.  Nandi's
            # displayed and stored session time is always IST, not the cloud
            # server's local timezone.
            timestamp=timestamp or datetime.now(IST).replace(tzinfo=None),
            spot=spot,
            spot_change=spot - prior_spot,
            recent_high=recent_high,
            recent_low=recent_low,
            legs=tuple(legs),
            expiry=str(rows[0].get("expiry") or self.expiry),
        )

    def fetch_snapshot(self) -> OptionSnapshot:
        return self.parse_chain(self.fetch_raw_chain())
