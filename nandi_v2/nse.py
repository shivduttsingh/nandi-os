from __future__ import annotations

import json
from datetime import datetime
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

from .models import OptionChainSnapshot, OptionLeg, StrikeRow

IST = ZoneInfo("Asia/Kolkata")


class NSEDataError(RuntimeError):
    """Raised when NSE public-site data cannot be retrieved or parsed."""


class NSEPublicClient:
    """Conservative NSE adapter with rolling snapshot deltas and no broker fallback.

    NSE retired the old ``option-chain-indices`` endpoint in 2026. The current
    flow first resolves the nearest expiry from ``option-chain-contract-info``
    and then fetches the chain from ``option-chain-v3``.
    """

    BASE_URL = "https://www.nseindia.com"
    OPTION_CHAIN_PAGE = "/option-chain?date=-&instrument=-&symbol=NIFTY"
    CONTRACT_INFO_API = "/api/option-chain-contract-info?symbol={symbol}"
    OPTION_CHAIN_V3_API = "/api/option-chain-v3?{query}"
    ALL_INDICES_API = "/api/allIndices"

    def __init__(self, timeout_seconds: float = 12.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self.BASE_URL}/option-chain",
            "X-Requested-With": "XMLHttpRequest",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        self._primed = False
        self._previous_chain: OptionChainSnapshot | None = None

    def _request(self, path: str, *, accept_json: bool = True) -> bytes:
        headers = dict(self._headers)
        if not accept_json:
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers.pop("X-Requested-With", None)
        request = Request(f"{self.BASE_URL}{path}", headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code in {401, 403, 429}:
                self._primed = False
            raise NSEDataError(f"NSE returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise NSEDataError(f"NSE request failed: {exc}") from exc

    def _prime(self) -> None:
        if self._primed:
            return
        self._request(self.OPTION_CHAIN_PAGE, accept_json=False)
        self._primed = True

    def _json(self, path: str) -> dict[str, Any]:
        self._prime()
        payload = self._request(path)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._primed = False
            raise NSEDataError("NSE returned an unreadable response") from exc
        if not isinstance(value, dict):
            raise NSEDataError("NSE returned an unexpected response shape")
        return value

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _timestamp(value: Any, *, required: bool = True) -> tuple[datetime, str]:
        raw = str(value or "").strip()
        patterns = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
        for pattern in patterns:
            try:
                return datetime.strptime(raw, pattern).replace(tzinfo=IST), raw
            except ValueError:
                continue
        if required:
            raise NSEDataError(f"NSE timestamp is missing or invalid: {raw or 'blank'}")
        return datetime.now(IST), raw

    @classmethod
    def _leg(cls, value: Any) -> OptionLeg:
        data = value if isinstance(value, dict) else {}
        return OptionLeg(
            ltp=cls._number(data.get("lastPrice")),
            change=cls._number(data.get("change")),
            oi=cls._number(data.get("openInterest")),
            change_oi=cls._number(data.get("changeinOpenInterest")),
            volume=cls._number(data.get("totalTradedVolume")),
            iv=cls._number(data.get("impliedVolatility")),
            bid=cls._number(data.get("bidprice") or data.get("bidPrice")),
            ask=cls._number(data.get("askPrice") or data.get("askprice")),
        )

    @staticmethod
    def _rolling_leg(current: OptionLeg, previous: OptionLeg | None) -> OptionLeg:
        if previous is None:
            return OptionLeg(
                current.ltp, 0.0, current.oi, 0.0, current.volume, current.iv,
                current.bid, current.ask,
            )
        return OptionLeg(
            ltp=current.ltp,
            change=current.ltp - previous.ltp,
            oi=current.oi,
            change_oi=current.oi - previous.oi,
            volume=max(0.0, current.volume - previous.volume),
            iv=current.iv,
            bid=current.bid,
            ask=current.ask,
        )

    @classmethod
    def rolling_snapshot(cls, current: OptionChainSnapshot, previous: OptionChainSnapshot | None) -> OptionChainSnapshot:
        if previous is None or previous.expiry != current.expiry:
            rows = tuple(StrikeRow(row.strike, cls._rolling_leg(row.ce, None), cls._rolling_leg(row.pe, None)) for row in current.rows)
            return OptionChainSnapshot(current.timestamp, current.expiry, current.spot, rows, source=f"{current.source} · rolling baseline", raw_timestamp=current.raw_timestamp)
        previous_rows = {row.strike: row for row in previous.rows}
        rows = []
        for row in current.rows:
            old = previous_rows.get(row.strike)
            rows.append(StrikeRow(row.strike, cls._rolling_leg(row.ce, old.ce if old else None), cls._rolling_leg(row.pe, old.pe if old else None)))
        return OptionChainSnapshot(current.timestamp, current.expiry, current.spot, tuple(rows), source=f"{current.source} · rolling delta", raw_timestamp=current.raw_timestamp)

    def _parse_option_chain(self, payload: dict[str, Any], expiry: str | None = None) -> OptionChainSnapshot:
        records = payload.get("records")
        if not isinstance(records, dict):
            raise NSEDataError("NSE option-chain records are missing")
        expiries = records.get("expiryDates")
        available_expiries = [str(value) for value in expiries] if isinstance(expiries, list) else []
        selected_expiry = expiry or (available_expiries[0] if available_expiries else "")
        data = records.get("data")
        if not isinstance(data, list):
            raise NSEDataError("NSE option-chain rows are missing")
        rows: list[StrikeRow] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            row_expiry = str(item.get("expiryDate") or "")
            if selected_expiry and row_expiry and row_expiry != selected_expiry:
                continue
            strike = self._number(item.get("strikePrice"))
            if strike <= 0:
                continue
            rows.append(StrikeRow(strike=strike, ce=self._leg(item.get("CE")), pe=self._leg(item.get("PE"))))
        if not rows:
            raise NSEDataError(f"No NIFTY option rows were returned for expiry {selected_expiry or 'nearest'}")
        timestamp, raw_timestamp = self._timestamp(records.get("timestamp") or payload.get("timestamp"), required=True)
        spot = self._number(records.get("underlyingValue"))
        if spot <= 0:
            filtered = payload.get("filtered")
            spot = self._number(filtered.get("underlyingValue")) if isinstance(filtered, dict) else 0.0
        if spot <= 0:
            for row in data:
                if isinstance(row, dict):
                    for side in ("CE", "PE"):
                        leg = row.get(side)
                        if isinstance(leg, dict):
                            spot = self._number(leg.get("underlyingValue"))
                            if spot > 0:
                                break
                    if spot > 0:
                        break
        if spot <= 0:
            raise NSEDataError("NSE option chain did not include the NIFTY underlying value")
        return OptionChainSnapshot(timestamp=timestamp, expiry=selected_expiry, spot=spot, rows=tuple(sorted(rows, key=lambda row: row.strike)), source="NSE option-chain-v3", raw_timestamp=raw_timestamp)

    def _nearest_expiry(self, symbol: str) -> str:
        payload = self._json(self.CONTRACT_INFO_API.format(symbol=quote(symbol)))
        expiries = payload.get("expiryDates")
        if not isinstance(expiries, list) or not expiries:
            records = payload.get("records")
            expiries = records.get("expiryDates") if isinstance(records, dict) else None
        values = [str(value).strip() for value in expiries or [] if str(value).strip()]
        if not values:
            raise NSEDataError("NSE contract-info returned no option expiries")
        return values[0]

    def fetch_option_chain(self, symbol: str = "NIFTY", expiry: str | None = None) -> OptionChainSnapshot:
        symbol = symbol.strip().upper() or "NIFTY"
        selected_expiry = expiry or self._nearest_expiry(symbol)
        query = urlencode({"type": "Indices", "symbol": symbol, "expiry": selected_expiry})
        payload = self._json(self.OPTION_CHAIN_V3_API.format(query=query))
        raw_snapshot = self._parse_option_chain(payload, selected_expiry)
        if self._previous_chain and raw_snapshot.timestamp <= self._previous_chain.timestamp:
            raise NSEDataError("NSE option-chain timestamp did not advance")
        rolling = self.rolling_snapshot(raw_snapshot, self._previous_chain)
        self._previous_chain = raw_snapshot
        return rolling

    def fetch_nifty_spot(self) -> tuple[float, datetime]:
        payload = self._json(self.ALL_INDICES_API)
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise NSEDataError("NSE indices data are missing")
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = str(item.get("index") or item.get("indexSymbol") or "").strip().upper()
            if name in {"NIFTY 50", "NIFTY"}:
                value = self._number(item.get("last") or item.get("lastPrice"))
                if value <= 0:
                    continue
                stamp, _ = self._timestamp(item.get("timeVal") or payload.get("timestamp"), required=False)
                return value, stamp
        raise NSEDataError("NIFTY 50 was not found in the NSE indices response")


def parse_option_chain_payload(payload: dict[str, Any], expiry: str | None = None) -> OptionChainSnapshot:
    return NSEPublicClient()._parse_option_chain(payload, expiry)
