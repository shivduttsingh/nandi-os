from __future__ import annotations

import json
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

PUBLIC_DATA_URL = "https://raw.githubusercontent.com/rajmaurya0904/bhav/main/sample_data/nifty_1y_1min.xlsx"
UNDERLYING = "NSE_INDEX|Nifty 50"


@dataclass(frozen=True)
class ProfitFirstRules:
    signal_start_minute: int = 12 * 60
    signal_end_minute: int = 14 * 60
    trigger_pct: float = 0.05
    hold_bars: int = 30
    entry_slippage: float = 0.20
    friction: float = 0.50


RULES = ProfitFirstRules()


def signal_side(return_pct: float, rules: ProfitFirstRules = RULES) -> str | None:
    if return_pct >= rules.trigger_pct:
        return "PE"
    if return_pct <= -rules.trigger_pct:
        return "CE"
    return None


def choose_atm_strike(strikes: list[float] | tuple[float, ...], signal_spot: float) -> float:
    if not strikes:
        raise ValueError("No option strikes are available")
    return float(min(strikes, key=lambda strike: (abs(float(strike) - signal_spot), float(strike))))


def metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "net_points": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "trading_days": 0,
            "avg_points_per_trading_day": None,
            "max_drawdown": 0.0,
        }
    gains = float(trades.loc[trades.pnl > 0, "pnl"].sum())
    losses = float(-trades.loc[trades.pnl <= 0, "pnl"].sum())
    equity = trades["pnl"].cumsum()
    drawdown = float((equity.cummax() - equity).max())
    days = int(trades["day"].nunique())
    return {
        "trades": int(len(trades)),
        "wins": int((trades.pnl > 0).sum()),
        "losses": int((trades.pnl <= 0).sum()),
        "win_rate": round(float((trades.pnl > 0).mean() * 100), 2),
        "net_points": round(float(trades.pnl.sum()), 2),
        "expectancy": round(float(trades.pnl.mean()), 2),
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
        "trading_days": days,
        "avg_points_per_trading_day": round(float(trades.pnl.sum() / days), 2) if days else None,
        "max_drawdown": round(drawdown, 2),
    }


def _summaries(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        cols = ["date", "trades", "wins", "losses", "win_rate", "net_points", "expectancy", "profit_factor"]
        return pd.DataFrame(columns=cols), pd.DataFrame(columns=["month"] + cols[1:])
    daily_rows = []
    for day, group in trades.groupby("day", sort=True):
        row = {"date": str(day)}
        row.update(metrics(group))
        daily_rows.append(row)
    monthly_rows = []
    for month, group in trades.groupby("month", sort=True):
        row = {"month": str(month)}
        row.update(metrics(group))
        monthly_rows.append(row)
    return pd.DataFrame(daily_rows), pd.DataFrame(monthly_rows)


def _replay_frames(
    spot: pd.DataFrame,
    options: pd.DataFrame,
    start_date: date,
    end_date: date,
    rules: ProfitFirstRules = RULES,
) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    spot = spot.copy()
    options = options.copy()
    spot["dt"] = pd.to_datetime(spot["dt"])
    options["dt"] = pd.to_datetime(options["dt"])
    spot = spot.sort_values("dt").reset_index(drop=True)
    options = options.sort_values(["date", "Strike", "Type", "dt"]).reset_index(drop=True)

    spot["date"] = spot["dt"].dt.date
    spot["minute"] = spot["dt"].dt.hour * 60 + spot["dt"].dt.minute
    spot["r1"] = spot.groupby("date")["Close"].pct_change() * 100.0

    groups = {
        key: group.sort_values("dt").reset_index(drop=True)
        for key, group in options.groupby(["date", "Strike", "Type"], sort=False)
    }
    available: dict[pd.Timestamp, list[float]] = {}
    for (dt, strike), group in options.groupby(["dt", "Strike"]):
        if group["Type"].nunique() == 2:
            available.setdefault(pd.Timestamp(dt), []).append(float(strike))

    records: list[dict] = []
    blocked_until: pd.Timestamp | None = None

    for row in spot.itertuples(index=False):
        dt = pd.Timestamp(row.dt)
        day = row.date
        if day < start_date or day > end_date:
            continue
        minute = int(row.minute)
        if minute < rules.signal_start_minute or minute > rules.signal_end_minute:
            continue
        if pd.isna(row.r1):
            continue
        side = signal_side(float(row.r1), rules)
        if side is None:
            continue
        if blocked_until is not None and dt <= blocked_until:
            continue

        signal_spot = float(row.Close)
        entry_dt = dt + pd.Timedelta(minutes=1)
        strikes = available.get(entry_dt, [])
        if not strikes:
            continue
        strike = choose_atm_strike(strikes, signal_spot)
        contract = groups.get((day, strike, side))
        if contract is None:
            continue
        locations = contract.index[contract["dt"] == entry_dt].tolist()
        if not locations:
            continue
        p = int(locations[0])
        last = min(p + rules.hold_bars - 1, len(contract) - 1)
        if last <= p:
            continue

        entry_price = float(contract.loc[p, "Open"]) + rules.entry_slippage
        exit_dt = pd.Timestamp(contract.loc[last, "dt"])
        exit_price = float(contract.loc[last, "Close"])
        pnl = exit_price - entry_price - rules.friction
        records.append(
            {
                "signal_dt": dt,
                "entry_dt": entry_dt,
                "exit_dt": exit_dt,
                "side": side,
                "strike": strike,
                "signal_spot": signal_spot,
                "spot_r1_pct": float(row.r1),
                "entry": entry_price,
                "exit": exit_price,
                "pnl": pnl,
            }
        )
        blocked_until = exit_dt

    trades = pd.DataFrame(records)
    if not trades.empty:
        trades["day"] = pd.to_datetime(trades["signal_dt"]).dt.date
        trades["month"] = pd.to_datetime(trades["signal_dt"]).dt.to_period("M").astype(str)
    else:
        trades = pd.DataFrame(
            columns=[
                "signal_dt", "entry_dt", "exit_dt", "side", "strike", "signal_spot",
                "spot_r1_pct", "entry", "exit", "pnl", "day", "month",
            ]
        )
    daily, monthly = _summaries(trades)
    return metrics(trades), trades, daily, monthly


def run_public_backtest(
    start_date: date,
    end_date: date,
    rules: ProfitFirstRules = RULES,
) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    minimum = date(2025, 7, 3)
    maximum = date(2026, 6, 30)
    if start_date > end_date:
        raise ValueError("Backtest start date must be on or before end date")
    if start_date < minimum or end_date > maximum:
        raise ValueError(f"Public dataset supports {minimum} through {maximum}")

    with tempfile.TemporaryDirectory() as td:
        workbook = Path(td) / "nifty_1y_1min.xlsx"
        urllib.request.urlretrieve(PUBLIC_DATA_URL, workbook)
        spot = pd.read_excel(workbook, sheet_name="Spot_1min")
        options = pd.read_excel(workbook, sheet_name="ATM_Options_1min")

    spot["dt"] = pd.to_datetime(spot.Date.astype(str) + " " + spot.Time.astype(str))
    options["dt"] = pd.to_datetime(options.Date.astype(str) + " " + options.Time.astype(str))
    options["date"] = options["dt"].dt.date
    return _replay_frames(spot, options, start_date, end_date, rules)


class UpstoxProfitFirstHistory:
    def __init__(self, access_token: str, timeout_seconds: float = 30.0) -> None:
        self.access_token = access_token.strip()
        self.timeout_seconds = timeout_seconds
        if not self.access_token:
            raise ValueError("Upstox access token is required")

    def _json(self, url: str) -> dict:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "Nandi-Profit-First/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Upstox HTTP {exc.code}: {body[:500]}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read Upstox data: {exc}") from exc
        if payload.get("status") != "success":
            raise RuntimeError(f"Upstox request failed: {payload}")
        return payload

    @staticmethod
    def _candles(payload: dict) -> pd.DataFrame:
        rows = (payload.get("data") or {}).get("candles") or []
        values = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            ts = pd.Timestamp(row[0])
            if ts.tzinfo is not None:
                ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
            values.append(
                {
                    "dt": ts,
                    "Open": float(row[1]),
                    "High": float(row[2]),
                    "Low": float(row[3]),
                    "Close": float(row[4]),
                    "Volume": float(row[5]) if len(row) > 5 else 0.0,
                    "OI": float(row[6]) if len(row) > 6 else 0.0,
                }
            )
        if not values:
            return pd.DataFrame(columns=["dt", "Open", "High", "Low", "Close", "Volume", "OI"])
        return pd.DataFrame(values).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)

    def _spot_history(self, start_date: date, end_date: date) -> pd.DataFrame:
        frames = []
        cursor = start_date
        key = quote(UNDERLYING, safe="")
        while cursor <= end_date:
            chunk_end = min(end_date, cursor + timedelta(days=29))
            url = (
                f"https://api.upstox.com/v3/historical-candle/{key}/minutes/1/"
                f"{chunk_end.isoformat()}/{cursor.isoformat()}"
            )
            frames.append(self._candles(self._json(url)))
            cursor = chunk_end + timedelta(days=1)
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)

    def _all_expiries(self) -> list[date]:
        params = urlencode({"instrument_key": UNDERLYING})
        values: set[date] = set()
        try:
            payload = self._json(f"https://api.upstox.com/v2/expired-instruments/expiries?{params}")
            for item in payload.get("data") or []:
                try:
                    values.add(date.fromisoformat(str(item)))
                except ValueError:
                    continue
        except RuntimeError as exc:
            if "1149" not in str(exc):
                raise

        payload = self._json(f"https://api.upstox.com/v2/option/contract?{params}")
        for item in payload.get("data") or []:
            expiry = item.get("expiry")
            if expiry:
                try:
                    values.add(date.fromisoformat(str(expiry)))
                except ValueError:
                    continue
        return sorted(values)

    def _contracts(self, expiry: date, expired: bool) -> dict[float, dict[str, str]]:
        params = urlencode({"instrument_key": UNDERLYING, "expiry_date": expiry.isoformat()})
        endpoint = "expired-instruments/option/contract" if expired else "option/contract"
        payload = self._json(f"https://api.upstox.com/v2/{endpoint}?{params}")
        mapping: dict[float, dict[str, str]] = {}
        for item in payload.get("data") or []:
            try:
                strike = float(item.get("strike_price"))
            except (TypeError, ValueError):
                continue
            side = str(item.get("instrument_type") or "").upper()
            key = str(item.get("instrument_key") or "").strip()
            if side in {"CE", "PE"} and key:
                mapping.setdefault(strike, {})[side] = key
        return {strike: legs for strike, legs in mapping.items() if "CE" in legs and "PE" in legs}

    def _option_history(self, instrument_key: str, day: date, expired: bool) -> pd.DataFrame:
        key = quote(instrument_key, safe="")
        ds = day.isoformat()
        if expired:
            url = (
                f"https://api.upstox.com/v2/expired-instruments/historical-candle/"
                f"{key}/1minute/{ds}/{ds}"
            )
        else:
            url = f"https://api.upstox.com/v3/historical-candle/{key}/minutes/1/{ds}/{ds}"
        return self._candles(self._json(url))

    def run_backtest(
        self,
        start_date: date,
        end_date: date,
        rules: ProfitFirstRules = RULES,
    ) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        if start_date > end_date:
            raise ValueError("Backtest start date must be on or before end date")
        if (end_date - start_date).days > 31:
            raise ValueError("Recent Upstox replay is limited to 32 calendar days per run")

        spot = self._spot_history(start_date, end_date)
        if spot.empty:
            raise RuntimeError("Upstox returned no NIFTY 1-minute spot data for the selected dates")
        spot["date"] = spot["dt"].dt.date
        spot["minute"] = spot["dt"].dt.hour * 60 + spot["dt"].dt.minute
        spot["r1"] = spot.groupby("date")["Close"].pct_change() * 100.0

        expiries = self._all_expiries()
        if not expiries:
            raise RuntimeError("Upstox returned no NIFTY option expiries")

        contract_cache: dict[tuple[date, bool], dict[float, dict[str, str]]] = {}
        option_cache: dict[tuple[str, date, bool], pd.DataFrame] = {}
        records: list[dict] = []
        blocked_until: pd.Timestamp | None = None
        today = date.today()

        for row in spot.itertuples(index=False):
            dt = pd.Timestamp(row.dt)
            day = row.date
            minute = int(row.minute)
            if day < start_date or day > end_date:
                continue
            if minute < rules.signal_start_minute or minute > rules.signal_end_minute or pd.isna(row.r1):
                continue
            side = signal_side(float(row.r1), rules)
            if side is None:
                continue
            if blocked_until is not None and dt <= blocked_until:
                continue

            possible = [expiry for expiry in expiries if expiry >= day]
            if not possible:
                continue
            expiry = min(possible)
            expired = expiry < today
            cache_key = (expiry, expired)
            if cache_key not in contract_cache:
                contract_cache[cache_key] = self._contracts(expiry, expired)
            contracts = contract_cache[cache_key]
            if not contracts:
                continue

            signal_spot = float(row.Close)
            strike = choose_atm_strike(tuple(contracts), signal_spot)
            instrument_key = contracts[strike][side]
            option_key = (instrument_key, day, expired)
            if option_key not in option_cache:
                option_cache[option_key] = self._option_history(instrument_key, day, expired)
            option = option_cache[option_key]
            if option.empty:
                continue

            entry_dt = dt + pd.Timedelta(minutes=1)
            locations = option.index[option["dt"] == entry_dt].tolist()
            if not locations:
                continue
            p = int(locations[0])
            last = min(p + rules.hold_bars - 1, len(option) - 1)
            if last <= p:
                continue
            entry_price = float(option.loc[p, "Open"]) + rules.entry_slippage
            exit_dt = pd.Timestamp(option.loc[last, "dt"])
            exit_price = float(option.loc[last, "Close"])
            pnl = exit_price - entry_price - rules.friction
            records.append(
                {
                    "signal_dt": dt,
                    "entry_dt": entry_dt,
                    "exit_dt": exit_dt,
                    "expiry": expiry.isoformat(),
                    "side": side,
                    "strike": strike,
                    "signal_spot": signal_spot,
                    "spot_r1_pct": float(row.r1),
                    "entry": entry_price,
                    "exit": exit_price,
                    "pnl": pnl,
                }
            )
            blocked_until = exit_dt

        trades = pd.DataFrame(records)
        if not trades.empty:
            trades["day"] = pd.to_datetime(trades["signal_dt"]).dt.date
            trades["month"] = pd.to_datetime(trades["signal_dt"]).dt.to_period("M").astype(str)
        else:
            trades = pd.DataFrame(
                columns=[
                    "signal_dt", "entry_dt", "exit_dt", "expiry", "side", "strike",
                    "signal_spot", "spot_r1_pct", "entry", "exit", "pnl", "day", "month",
                ]
            )
        daily, monthly = _summaries(trades)
        return metrics(trades), trades, daily, monthly
