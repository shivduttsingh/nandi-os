from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
UNDERLYING = "NSE_INDEX|Nifty 50"
START = date(2026, 8, 24)
END = date(2026, 8, 28)
OUT_JSON = Path("profit_first_aug24_28_2026.json")
OUT_CSV = Path("profit_first_aug24_28_2026_trades.csv")


def api_json(url: str) -> dict:
    request = Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "Nandi-Profit-First-Research/1.0",
    })
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc
    if payload.get("status") != "success":
        raise RuntimeError(f"Upstox API returned non-success: {payload}")
    return payload


def candles(payload: dict) -> pd.DataFrame:
    rows = (payload.get("data") or {}).get("candles") or []
    data = []
    for row in rows:
        if len(row) < 5:
            continue
        ts = pd.Timestamp(row[0])
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
        data.append({
            "dt": ts,
            "Open": float(row[1]),
            "High": float(row[2]),
            "Low": float(row[3]),
            "Close": float(row[4]),
            "Volume": float(row[5]) if len(row) > 5 else 0.0,
            "OI": float(row[6]) if len(row) > 6 else 0.0,
        })
    return pd.DataFrame(data).sort_values("dt").drop_duplicates("dt").reset_index(drop=True) if data else pd.DataFrame()


def spot_history() -> pd.DataFrame:
    key = quote(UNDERLYING, safe="")
    url = f"https://api.upstox.com/v3/historical-candle/{key}/minutes/1/{END.isoformat()}/{START.isoformat()}"
    return candles(api_json(url))


def contracts_for_expiry(expiry: date, expired: bool) -> list[dict]:
    params = urlencode({"instrument_key": UNDERLYING, "expiry_date": expiry.isoformat()})
    if expired:
        url = f"https://api.upstox.com/v2/expired-instruments/option/contract?{params}"
    else:
        url = f"https://api.upstox.com/v2/option/contract?{params}"
    data = api_json(url).get("data") or []
    return list(data)


def option_history(instrument_key: str, day: date, expired: bool) -> pd.DataFrame:
    key = quote(instrument_key, safe="")
    ds = day.isoformat()
    if expired:
        url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{key}/1minute/{ds}/{ds}"
    else:
        url = f"https://api.upstox.com/v3/historical-candle/{key}/minutes/1/{ds}/{ds}"
    return candles(api_json(url))


def metrics(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "net_points": 0.0, "expectancy": None, "profit_factor": None}
    gains = float(g.loc[g.pnl > 0, "pnl"].sum())
    losses = float(-g.loc[g.pnl <= 0, "pnl"].sum())
    return {
        "trades": int(len(g)),
        "wins": int((g.pnl > 0).sum()),
        "losses": int((g.pnl <= 0).sum()),
        "win_rate": round(float((g.pnl > 0).mean() * 100), 2),
        "net_points": round(float(g.pnl.sum()), 2),
        "expectancy": round(float(g.pnl.mean()), 2),
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
    }


def main() -> None:
    if not TOKEN:
        result = {
            "status": "UPSTOX_SECRET_NOT_AVAILABLE",
            "period": "2026-08-24 to 2026-08-28",
            "message": "GitHub Actions does not have UPSTOX_ACCESS_TOKEN configured; no P&L was estimated.",
        }
        OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        OUT_CSV.write_text("", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return

    spot = spot_history()
    if spot.empty:
        raise RuntimeError("No NIFTY spot candles returned for Aug 24-28")
    spot["day"] = spot.dt.dt.date
    spot["minute"] = spot.dt.dt.hour * 60 + spot.dt.dt.minute
    spot["r1"] = spot.groupby("day")["Close"].pct_change() * 100.0
    spot_by_dt = spot.set_index("dt")

    # NIFTY weekly expiry rollover for this week: Aug-25 then Sep-1.
    expiry_info = {
        date(2026, 8, 24): (date(2026, 8, 25), True),
        date(2026, 8, 25): (date(2026, 8, 25), True),
        date(2026, 8, 26): (date(2026, 9, 1), False),
        date(2026, 8, 27): (date(2026, 9, 1), False),
        date(2026, 8, 28): (date(2026, 9, 1), False),
    }

    contract_maps: dict[tuple[date, bool], dict[float, dict[str, str]]] = {}
    for expiry, expired in sorted(set(expiry_info.values())):
        rows = contracts_for_expiry(expiry, expired)
        mapping: dict[float, dict[str, str]] = {}
        for item in rows:
            try:
                strike = float(item.get("strike_price"))
            except (TypeError, ValueError):
                continue
            side = str(item.get("instrument_type") or "").upper()
            ikey = str(item.get("instrument_key") or "").strip()
            if side not in {"CE", "PE"} or not ikey:
                continue
            mapping.setdefault(strike, {})[side] = ikey
        mapping = {k: v for k, v in mapping.items() if "CE" in v and "PE" in v}
        if not mapping:
            raise RuntimeError(f"No complete CE/PE contracts returned for expiry {expiry}")
        contract_maps[(expiry, expired)] = mapping

    option_cache: dict[tuple[str, date, bool], pd.DataFrame] = {}

    def get_option(key: str, day: date, expired: bool) -> pd.DataFrame:
        ck = (key, day, expired)
        if ck not in option_cache:
            option_cache[ck] = option_history(key, day, expired)
        return option_cache[ck]

    records: list[dict] = []
    blocked_until: pd.Timestamp | None = None

    for row in spot.itertuples(index=False):
        dt = pd.Timestamp(row.dt)
        day = row.day
        minute = int(row.minute)
        r1 = row.r1
        if day not in expiry_info:
            continue
        if minute < 720 or minute > 840 or pd.isna(r1) or abs(float(r1)) < 0.05:
            continue
        if blocked_until is not None and dt <= blocked_until:
            continue

        entry_dt = dt + pd.Timedelta(minutes=1)
        if entry_dt not in spot_by_dt.index or entry_dt.date() != day:
            continue
        entry_spot = float(spot_by_dt.loc[entry_dt, "Close"])
        side = "PE" if float(r1) > 0 else "CE"
        expiry, expired = expiry_info[day]
        cmap = contract_maps[(expiry, expired)]
        strike = min(cmap, key=lambda x: (abs(x - entry_spot), x))
        ikey = cmap[strike][side]
        opt = get_option(ikey, day, expired)
        if opt.empty:
            continue
        index_by_dt = {pd.Timestamp(v): i for i, v in enumerate(opt.dt)}
        if entry_dt not in index_by_dt:
            continue
        p = index_by_dt[entry_dt]
        last = min(p + 29, len(opt) - 1)
        if last <= p:
            continue
        entry = float(opt.loc[p, "Open"]) + 0.20
        exit_dt = pd.Timestamp(opt.loc[last, "dt"])
        exit_price = float(opt.loc[last, "Close"])
        pnl = exit_price - entry - 0.50
        records.append({
            "signal_dt": dt,
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "expiry": expiry.isoformat(),
            "side": side,
            "strike": strike,
            "spot_r1_pct": float(r1),
            "entry": entry,
            "exit": exit_price,
            "pnl": pnl,
        })
        blocked_until = exit_dt

    trades = pd.DataFrame(records)
    if not trades.empty:
        trades["day"] = pd.to_datetime(trades.signal_dt).dt.date
    trades.to_csv(OUT_CSV, index=False)

    daily = []
    for day in [START + timedelta(days=i) for i in range((END - START).days + 1)]:
        if day.weekday() >= 5:
            continue
        g = trades[trades.day == day] if not trades.empty else pd.DataFrame(columns=["pnl"])
        row = {"date": day.isoformat()}
        row.update(metrics(g))
        daily.append(row)

    result = {
        "status": "EXACT_FROZEN_RULE_REPLAY",
        "period": "2026-08-24 to 2026-08-28",
        "strategy": "Profit-first midday 1m reversal",
        "rules": {
            "window": "12:00-14:00 IST",
            "trigger": "abs NIFTY 1m return >= 0.05%",
            "direction": "up -> ATM PE; down -> ATM CE",
            "entry": "next-minute option open +0.20",
            "exit": "30 option bars later at close",
            "friction": "0.50 additional option points/trade",
            "positioning": "one open trade at a time",
        },
        "source": "Upstox read-only historical NIFTY and option candles",
        "week": metrics(trades if not trades.empty else pd.DataFrame(columns=["pnl"])),
        "daily": daily,
    }
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
