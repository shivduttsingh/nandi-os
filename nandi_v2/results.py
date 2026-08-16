from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


Period = Literal["daily", "weekly", "monthly"]


@dataclass(frozen=True)
class CompletedTrade:
    opened_at: datetime
    closed_at: datetime
    side: str
    entry_spot: float
    exit_spot: float
    points: float
    hold_minutes: float
    strike: float | None
    exit_reason: str


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def completed_trades(events: list[dict[str, Any]]) -> tuple[CompletedTrade, ...]:
    """Pair persisted ACTIVE and EXIT transitions without inventing missing trades."""
    active: dict[str, Any] | None = None
    trades: list[CompletedTrade] = []
    ordered = sorted(events, key=lambda item: str(item.get("Time") or ""))
    for event in ordered:
        status = str(event.get("Status") or "")
        side = str(event.get("Side") or "")
        stamp = _timestamp(event.get("Time"))
        spot = event.get("Spot")
        if stamp is None or spot is None:
            continue
        if status in {"ACTIVE CE", "ACTIVE PE"} and side in {"CE", "PE"}:
            if active is None:
                active = event
            continue
        if status != "EXIT" or active is None or side != str(active.get("Side") or ""):
            continue
        opened = _timestamp(active.get("Time"))
        entry = active.get("Spot")
        if opened is None or entry is None:
            active = None
            continue
        raw = float(spot) - float(entry)
        points = raw if side == "CE" else -raw
        trades.append(
            CompletedTrade(
                opened_at=opened,
                closed_at=stamp,
                side=side,
                entry_spot=float(entry),
                exit_spot=float(spot),
                points=round(points, 2),
                hold_minutes=round(max(0.0, (stamp - opened).total_seconds() / 60.0), 1),
                strike=float(active["Strike"]) if active.get("Strike") is not None else None,
                exit_reason=str(event.get("Reason") or ""),
            )
        )
        active = None
    return tuple(trades)


def trade_rows(trades: tuple[CompletedTrade, ...]) -> list[dict[str, Any]]:
    return [
        {
            "Entry": trade.opened_at,
            "Exit": trade.closed_at,
            "Side": trade.side,
            "Strike": trade.strike,
            "Entry NIFTY": trade.entry_spot,
            "Exit NIFTY": trade.exit_spot,
            "NIFTY points": trade.points,
            "Hold minutes": trade.hold_minutes,
            "Result": "WIN" if trade.points > 0 else "LOSS" if trade.points < 0 else "FLAT",
            "Exit reason": trade.exit_reason,
        }
        for trade in trades
    ]


def _period_key(trade: CompletedTrade, period: Period) -> str:
    day = trade.closed_at.date()
    if period == "daily":
        return day.isoformat()
    if period == "weekly":
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02d}"
    return f"{day.year}-{day.month:02d}"


def _maximum_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def result_rows(trades: tuple[CompletedTrade, ...], period: Period) -> list[dict[str, Any]]:
    groups: dict[str, list[CompletedTrade]] = {}
    for trade in sorted(trades, key=lambda item: item.closed_at):
        groups.setdefault(_period_key(trade, period), []).append(trade)
    rows: list[dict[str, Any]] = []
    for label, items in groups.items():
        wins = sum(item.points > 0 for item in items)
        losses = sum(item.points < 0 for item in items)
        net = round(sum(item.points for item in items), 2)
        rows.append({
            "Period": label,
            "Trades": len(items),
            "CE trades": sum(item.side == "CE" for item in items),
            "PE trades": sum(item.side == "PE" for item in items),
            "Wins": wins,
            "Losses": losses,
            "Win rate %": round(wins / len(items) * 100.0, 1),
            "Net NIFTY points": net,
            "Average hold minutes": round(sum(item.hold_minutes for item in items) / len(items), 1),
            "Maximum drawdown": _maximum_drawdown([item.points for item in items]),
            "Result": "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT",
        })
    return rows
