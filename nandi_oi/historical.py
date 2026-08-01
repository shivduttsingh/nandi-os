from __future__ import annotations

import calendar
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from typing import Callable
from urllib.parse import quote

from .configuration import is_configured_value
from .models import OptionLeg, OptionSnapshot
from .upstox import UpstoxAPIError, UpstoxOptionChainClient


def exactly_three_months_before(value: date) -> date:
    """Return the same calendar day three months earlier, clamped when necessary."""
    month_index = value.year * 12 + value.month - 1 - 3
    year, zero_month = divmod(month_index, 12)
    month = zero_month + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def monthly_expiries(expiries: list[date]) -> list[date]:
    """Use the final expiry date listed by Upstox in each calendar month."""
    by_month: dict[tuple[int, int], date] = {}
    for expiry in sorted(expiries):
        by_month[(expiry.year, expiry.month)] = expiry
    return sorted(
        expiry for expiry in by_month.values()
        if expiry.day >= calendar.monthrange(expiry.year, expiry.month)[1] - 6
    )


class UpstoxHistoricalClient(UpstoxOptionChainClient):
    """Build five-minute Nandi snapshots from Upstox Plus expired instruments."""

    def get_expiries(self) -> list[date]:
        data = self._get("/expired-instruments/expiries", {"instrument_key": self.instrument_key}).get("data", [])
        return sorted(datetime.strptime(item, "%Y-%m-%d").date() for item in data)

    def get_contracts(self, expiry: date) -> list[dict]:
        cache = getattr(self, "_contract_cache", {})
        if expiry in cache:
            return cache[expiry]
        contracts = self._get("/expired-instruments/option/contract", {
            "instrument_key": self.instrument_key, "expiry_date": expiry.isoformat(),
        }).get("data", [])
        cache[expiry] = contracts
        self._contract_cache = cache
        return contracts

    def _get_url(self, url: str) -> dict:
        # Reuse the authenticated transport while allowing v3 and encoded path parameters.
        from json import JSONDecodeError, loads
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        if not is_configured_value(self.access_token):
            raise UpstoxAPIError(
                "UPSTOX_ACCESS_TOKEN is missing or still contains the sample placeholder"
            )
        request = Request(url, headers={
            "Accept": "application/json", "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "Nandi-OI-Research/1.0",
        })
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403 or "UDAPI1149" in body:
                raise UpstoxAPIError("Historical options require an Upstox Plus subscription.") from exc
            raise UpstoxAPIError(f"Upstox returned HTTP {exc.code}: {body[:300]}") from exc
        except (URLError, TimeoutError, JSONDecodeError) as exc:
            raise UpstoxAPIError(f"Unable to read Upstox historical data: {exc}") from exc
        if payload.get("status") != "success":
            raise UpstoxAPIError(f"Upstox historical request failed: {payload}")
        return payload

    @staticmethod
    def _candles(payload: dict) -> list[list]:
        return list((payload.get("data") or {}).get("candles") or [])

    def spot_candles(self, start: date, end: date, interval_minutes: int = 5) -> dict[datetime, float]:
        if interval_minutes not in {1, 2, 3, 5, 10, 15, 30, 60}:
            raise ValueError("Unsupported historical candle interval")
        key = quote(self.instrument_key, safe="")
        result: dict[datetime, float] = {}
        # Upstox caps 1–15 minute requests at one month, so fetch safe 28-day chunks.
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=27))
            url = f"https://api.upstox.com/v3/historical-candle/{key}/minutes/{interval_minutes}/{chunk_end}/{chunk_start}"
            for row in self._candles(self._get_url(url)):
                timestamp = datetime.fromisoformat(row[0])
                result[timestamp] = float(row[4])
            chunk_start = chunk_end + timedelta(days=1)
        return result

    def option_candles(self, instrument_key: str, start: date, end: date) -> dict[datetime, tuple[float, float, float, float, float, float]]:
        cache_key = (instrument_key, start, end)
        cache = getattr(self, "_option_candle_cache", {})
        if cache_key in cache:
            return cache[cache_key]
        key = quote(instrument_key, safe="")
        url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{key}/5minute/{end}/{start}"
        result: dict[datetime, tuple[float, float, float, float, float, float]] = {}
        for row in self._candles(self._get_url(url)):
            result[datetime.fromisoformat(row[0])] = (
                float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                float(row[5]), float(row[6]),
            )
        cache[cache_key] = result
        self._option_candle_cache = cache
        return result

    def build_snapshots(
        self, start: date, end: date, progress: Callable[[int, int, str], None] | None = None,
        expiry_mode: str = "weekly",
    ) -> list[OptionSnapshot]:
        if start > end:
            raise ValueError("Backtest start date must be on or before the end date")
        if expiry_mode not in {"weekly", "monthly"}:
            raise ValueError("Expiry mode must be weekly or monthly")
        all_expiries = self.get_expiries()
        available = all_expiries if expiry_mode == "weekly" else monthly_expiries(all_expiries)
        horizon = 7 if expiry_mode == "weekly" else 40
        expiries = [item for item in available if start <= item <= end + timedelta(days=horizon)]
        if not expiries:
            raise UpstoxAPIError(f"No expired NIFTY {expiry_mode} contracts were available for this period")
        spot = self.spot_candles(start, end)
        if not spot:
            raise UpstoxAPIError("No historical NIFTY candles were returned")

        dates_by_expiry: dict[date, list[date]] = defaultdict(list)
        for trading_day in sorted({timestamp.date() for timestamp in spot}):
            expiry = next((item for item in expiries if item >= trading_day), None)
            if expiry:
                dates_by_expiry[expiry].append(trading_day)

        jobs: list[tuple[dict, date, date]] = []
        for expiry, trading_days in dates_by_expiry.items():
            period_spots = [price for timestamp, price in spot.items() if timestamp.date() in trading_days]
            low, high = min(period_spots) - 250, max(period_spots) + 250
            contracts = [item for item in self.get_contracts(expiry) if low <= float(item["strike_price"]) <= high]
            jobs.extend((item, min(trading_days), max(trading_days)) for item in contracts)

        by_time: dict[datetime, list[OptionLeg]] = defaultdict(list)
        expiry_by_time: dict[datetime, str] = {}
        total = len(jobs)
        for index, (contract, first_day, last_day) in enumerate(jobs, 1):
            candles = self.option_candles(contract["instrument_key"], first_day, last_day)
            previous: tuple[float, float, float] | None = None
            for timestamp in sorted(candles):
                open_price, high_price, low_price, ltp, volume, oi = candles[timestamp]
                prior_ltp, _, prior_oi = previous or (ltp, volume, oi)
                side = str(contract["instrument_type"])
                by_time[timestamp].append(OptionLeg(
                    strike=float(contract["strike_price"]), side=side, oi=oi,
                    change_oi=oi - prior_oi, ltp=ltp, change_ltp=ltp - prior_ltp,
                    volume=volume, bid=ltp, ask=ltp, open_price=open_price,
                    high_price=high_price, low_price=low_price,
                ))
                expiry_by_time[timestamp] = str(contract["expiry"])
                previous = (ltp, volume, oi)
            if progress:
                progress(index, total, str(contract.get("trading_symbol", "option")))

        snapshots: list[OptionSnapshot] = []
        prior_spots: deque[float] = deque(maxlen=20)
        last_day: date | None = None
        for timestamp in sorted(set(spot).intersection(by_time)):
            if timestamp.date() != last_day:
                prior_spots.clear()
                last_day = timestamp.date()
            price = spot[timestamp]
            prior = prior_spots[-1] if prior_spots else price
            recent_high = max(prior_spots) if prior_spots else price
            recent_low = min(prior_spots) if prior_spots else price
            strikes = sorted({leg.strike for leg in by_time[timestamp]}, key=lambda strike: abs(strike - price))[:11]
            legs = tuple(leg for leg in by_time[timestamp] if leg.strike in set(strikes))
            if len(legs) >= 12:
                snapshots.append(OptionSnapshot(
                    timestamp=timestamp, spot=price, spot_change=price - prior,
                    recent_high=recent_high, recent_low=recent_low, legs=legs,
                    expiry=expiry_by_time[timestamp],
                ))
            prior_spots.append(price)
        return snapshots
