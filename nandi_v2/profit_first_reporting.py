from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .profit_first import metrics

FORWARD_DIR = Path("forward_data")
FORWARD_LEDGER_PATH = FORWARD_DIR / "profit_first_trades.csv"
FORWARD_RUNS_PATH = FORWARD_DIR / "profit_first_runs.csv"

TRADE_COLUMNS = [
    "forward_test_date",
    "signal_dt",
    "entry_dt",
    "exit_dt",
    "expiry",
    "side",
    "strike",
    "signal_spot",
    "spot_r1_pct",
    "entry",
    "exit",
    "pnl",
    "source",
    "recorded_at",
]

RUN_COLUMNS = [
    "test_date",
    "recorded_at",
    "status",
    "trades",
    "wins",
    "losses",
    "win_rate",
    "net_points",
    "expectancy",
    "profit_factor",
    "max_drawdown",
]

NUMERIC_TRADE_COLUMNS = [
    "strike",
    "signal_spot",
    "spot_r1_pct",
    "entry",
    "exit",
    "pnl",
]


def empty_forward_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def empty_forward_runs() -> pd.DataFrame:
    return pd.DataFrame(columns=RUN_COLUMNS)


def read_forward_ledger(path: Path = FORWARD_LEDGER_PATH) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return empty_forward_ledger()
    frame = pd.read_csv(path)
    for column in TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[TRADE_COLUMNS]


def read_forward_runs(path: Path = FORWARD_RUNS_PATH) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return empty_forward_runs()
    frame = pd.read_csv(path)
    for column in RUN_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[RUN_COLUMNS]


def prepare_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Normalise a PROFIT FIRST ledger for deterministic period reporting."""
    if trades is None or trades.empty:
        return pd.DataFrame(
            columns=list(TRADE_COLUMNS) + ["day", "week", "month"]
        )

    frame = trades.copy()
    if "signal_dt" not in frame.columns or "pnl" not in frame.columns:
        raise ValueError("PROFIT FIRST trades require signal_dt and pnl columns")

    frame["signal_dt"] = pd.to_datetime(frame["signal_dt"], errors="coerce")
    frame = frame.dropna(subset=["signal_dt"]).copy()
    frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce")
    frame = frame.dropna(subset=["pnl"]).copy()
    frame["day"] = frame["signal_dt"].dt.date
    iso = frame["signal_dt"].dt.isocalendar()
    frame["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    frame["month"] = frame["signal_dt"].dt.to_period("M").astype(str)
    return frame.sort_values("signal_dt").reset_index(drop=True)


def period_summary(trades: pd.DataFrame, period: str) -> pd.DataFrame:
    """Return daily, weekly or monthly metrics using the same P&L definitions as the engine."""
    if period not in {"day", "week", "month"}:
        raise ValueError("period must be day, week or month")
    frame = prepare_trades(trades)
    label = {"day": "date", "week": "week", "month": "month"}[period]
    columns = [
        label,
        "trades",
        "wins",
        "losses",
        "win_rate",
        "net_points",
        "expectancy",
        "profit_factor",
        "max_drawdown",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    for key, group in frame.groupby(period, sort=True):
        row = {label: str(key)}
        row.update(metrics(group))
        rows.append(row)
    return pd.DataFrame(rows)[columns]


def all_period_summaries(trades: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = prepare_trades(trades)
    overall = metrics(frame)
    return (
        overall,
        period_summary(frame, "day"),
        period_summary(frame, "week"),
        period_summary(frame, "month"),
    )


def _iso(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    stamp = pd.Timestamp(value)
    return stamp.isoformat()


def forward_trade_rows(
    trades: pd.DataFrame,
    *,
    test_date: date,
    recorded_at: datetime | None = None,
) -> pd.DataFrame:
    if trades is None or trades.empty:
        return empty_forward_ledger()

    recorded = recorded_at or datetime.now(timezone.utc)
    frame = trades.copy()
    for column in ["signal_dt", "entry_dt", "exit_dt"]:
        if column not in frame.columns:
            frame[column] = ""
        frame[column] = frame[column].map(_iso)
    if "expiry" not in frame.columns:
        frame["expiry"] = ""
    for column in NUMERIC_TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["forward_test_date"] = test_date.isoformat()
    frame["source"] = "UPSTOX_FORWARD_CLOSE"
    frame["recorded_at"] = recorded.isoformat()
    for column in TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[TRADE_COLUMNS].sort_values("signal_dt").reset_index(drop=True)


def merge_forward_ledger(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        base = empty_forward_ledger()
    else:
        base = existing.copy()
        for column in TRADE_COLUMNS:
            if column not in base.columns:
                base[column] = None
        base = base[TRADE_COLUMNS]

    if incoming is None or incoming.empty:
        return base.reset_index(drop=True)

    combined = pd.concat([base, incoming[TRADE_COLUMNS]], ignore_index=True)
    combined["strike"] = pd.to_numeric(combined["strike"], errors="coerce")
    combined = combined.drop_duplicates(
        subset=["forward_test_date", "signal_dt", "side", "strike"],
        keep="last",
    )
    return combined.sort_values(["forward_test_date", "signal_dt"]).reset_index(drop=True)


def forward_run_row(
    summary: dict,
    *,
    test_date: date,
    status: str = "OK",
    recorded_at: datetime | None = None,
) -> pd.DataFrame:
    recorded = recorded_at or datetime.now(timezone.utc)
    row = {
        "test_date": test_date.isoformat(),
        "recorded_at": recorded.isoformat(),
        "status": status,
        "trades": int(summary.get("trades", 0) or 0),
        "wins": int(summary.get("wins", 0) or 0),
        "losses": int(summary.get("losses", 0) or 0),
        "win_rate": summary.get("win_rate"),
        "net_points": float(summary.get("net_points", 0.0) or 0.0),
        "expectancy": summary.get("expectancy"),
        "profit_factor": summary.get("profit_factor"),
        "max_drawdown": float(summary.get("max_drawdown", 0.0) or 0.0),
    }
    return pd.DataFrame([row], columns=RUN_COLUMNS)


def merge_forward_runs(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        base = empty_forward_runs()
    else:
        base = existing.copy()
        for column in RUN_COLUMNS:
            if column not in base.columns:
                base[column] = None
        base = base[RUN_COLUMNS]
    if incoming is None or incoming.empty:
        return base.reset_index(drop=True)
    combined = pd.concat([base, incoming[RUN_COLUMNS]], ignore_index=True)
    combined = combined.drop_duplicates(subset=["test_date"], keep="last")
    return combined.sort_values("test_date").reset_index(drop=True)


def save_forward_data(
    ledger: pd.DataFrame,
    runs: pd.DataFrame,
    *,
    ledger_path: Path = FORWARD_LEDGER_PATH,
    runs_path: Path = FORWARD_RUNS_PATH,
) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    ledger[TRADE_COLUMNS].to_csv(ledger_path, index=False)
    runs[RUN_COLUMNS].to_csv(runs_path, index=False)


def rupee_pnl(points: float | int | None, quantity: int) -> float:
    return round(float(points or 0.0) * max(0, int(quantity)), 2)


def validation_status(
    summary: dict,
    *,
    minimum_trades: int = 100,
    target_win_rate: float = 70.0,
    minimum_profit_factor: float = 1.5,
) -> tuple[str, list[str]]:
    """Classify forward evidence without changing the trading rule."""
    trade_count = int(summary.get("trades", 0) or 0)
    if trade_count < minimum_trades:
        return "COLLECTING", [f"Need {minimum_trades - trade_count} more forward trades"]

    reasons: list[str] = []
    win_rate = summary.get("win_rate")
    profit_factor = summary.get("profit_factor")
    net_points = float(summary.get("net_points", 0.0) or 0.0)
    if win_rate is None or float(win_rate) < target_win_rate:
        reasons.append(f"Win rate below {target_win_rate:.0f}% target")
    if profit_factor is None or float(profit_factor) < minimum_profit_factor:
        reasons.append(f"Profit factor below {minimum_profit_factor:.2f}")
    if net_points <= 0:
        reasons.append("Forward net option points are not positive")
    return ("PASS", []) if not reasons else ("FAIL", reasons)
