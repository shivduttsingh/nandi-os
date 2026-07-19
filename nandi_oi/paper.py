from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class PaperTrade:
    trade_id: str
    opened_at: str
    action: str
    strike: float
    expiry: str
    entry_price: float
    stop_price: float
    target_price: float
    confidence: float
    status: str = "OPEN"
    closed_at: str = ""
    exit_price: float = 0.0
    pnl_points: float = 0.0
    exit_reason: str = ""


class PaperJournal:
    fields = list(PaperTrade.__dataclass_fields__)

    def __init__(self, path: str = "data/oi_paper_trades.csv") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[PaperTrade]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        numeric = {"strike", "entry_price", "stop_price", "target_price", "confidence", "exit_price", "pnl_points"}
        return [PaperTrade(**{key: float(value or 0) if key in numeric else value for key, value in row.items()}) for row in rows]

    def _write(self, trades: list[PaperTrade]) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fields)
            writer.writeheader()
            writer.writerows(asdict(trade) for trade in trades)

    def trades_today(self) -> list[PaperTrade]:
        today = datetime.now().date().isoformat()
        return [trade for trade in self.all() if trade.opened_at.startswith(today)]

    def open_trade(
        self, action: str, strike: float, expiry: str, entry_price: float,
        stop_price: float, target_price: float, confidence: float,
    ) -> PaperTrade:
        if len(self.trades_today()) >= 3:
            raise ValueError("Maximum three paper trades per day reached")
        if any(trade.status == "OPEN" for trade in self.all()):
            raise ValueError("Close the existing paper trade before opening another")
        now = datetime.now()
        trade = PaperTrade(
            trade_id=now.strftime("%Y%m%d%H%M%S"), opened_at=now.isoformat(timespec="seconds"),
            action=action, strike=strike, expiry=expiry, entry_price=entry_price,
            stop_price=stop_price, target_price=target_price, confidence=confidence,
        )
        trades = self.all() + [trade]
        self._write(trades)
        return trade


    def close_trade(self, trade_id: str, exit_price: float, reason: str) -> PaperTrade:
        trades = self.all()
        match = next((trade for trade in trades if trade.trade_id == trade_id), None)
        if not match or match.status != "OPEN":
            raise ValueError("Open paper trade not found")
        match.status = "CLOSED"
        match.closed_at = datetime.now().isoformat(timespec="seconds")
        match.exit_price = exit_price
        match.pnl_points = round(exit_price - match.entry_price, 2)
        match.exit_reason = reason
        self._write(trades)
        return match
