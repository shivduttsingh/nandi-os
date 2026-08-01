from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping

from .backtest import BacktestResult, NandiBacktester
from .evidence_backtest import EvidenceBacktester
from .models import OptionSnapshot
from .rsi_backtest import RsiLevelBacktester, RsiTouchResult, RsiTouchAnalyzer, TIMEFRAMES


@dataclass(frozen=True)
class UnifiedStrategyRun:
    """One strategy/contract replay within one comparable historical window."""

    strategy: str
    contract: str
    rules: str
    result: BacktestResult

    @property
    def label(self) -> str:
        return f"{self.strategy} — {self.contract}"

    def summary_row(self) -> dict[str, object]:
        return {
            "Strategy": self.strategy,
            "Contract": self.contract,
            "Rules": self.rules,
            "Snapshots": self.result.snapshots,
            "Trades": len(self.result.trades),
            "Wins": self.result.wins,
            "Win rate %": round(self.result.win_rate, 1),
            "Net premium points": self.result.net_points,
            "Maximum drawdown": self.result.max_drawdown,
            "NO TRADE decisions": self.result.no_trade_decisions,
        }


@dataclass(frozen=True)
class UnifiedBacktestResult:
    start_date: date
    end_date: date
    runs: tuple[UnifiedStrategyRun, ...]
    rsi_touches: tuple[tuple[str, RsiTouchResult], ...] = ()

    def summary_rows(self) -> list[dict[str, object]]:
        return [run.summary_row() for run in self.runs]

    def ledger_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for run in self.runs:
            for trade in run.result.rows():
                rows.append({
                    "Strategy": run.strategy,
                    "Contract": run.contract,
                    "Rules": run.rules,
                    **trade,
                })
        return sorted(rows, key=lambda item: (item["opened_at"], item["Strategy"], item["Contract"]))

    def equity_rows(self) -> list[dict[str, object]]:
        """Keep individual curves separate; summing strategies would imply shared capital."""
        rows: list[dict[str, object]] = []
        for run in self.runs:
            points = 0.0
            for trade in run.result.trades:
                points = round(points + trade.pnl_points, 2)
                rows.append({
                    "Closed at": trade.closed_at,
                    "Strategy run": run.label,
                    "Cumulative premium points": points,
                })
        return rows

    def rsi_touch_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for strategy, touches in self.rsi_touches:
            for row in touches.summary_rows():
                rows.append({"Strategy": strategy, **row})
        return rows


class UnifiedBacktester:
    """Runs every implemented Nandi strategy over identical saved market data.

    OI V1 is a current-week chain strategy, so it is replayed on nearest-weekly
    contracts. Each saved RSI configuration is replayed independently on both
    nearest-weekly and nearest-monthly contracts. The result intentionally does
    not combine their P&L into a fictional shared portfolio.
    """

    OI_RULES = "OI V1: current-week chain; 20% premium stop; 30% premium target"

    def __init__(self, *, rsi_timeframes: Iterable[int] = TIMEFRAMES) -> None:
        self.rsi_timeframes = tuple(dict.fromkeys(int(value) for value in rsi_timeframes))
        if not self.rsi_timeframes or any(value not in TIMEFRAMES for value in self.rsi_timeframes):
            raise ValueError("Select supported RSI timeframes")

    @staticmethod
    def _configs(configs: Mapping[str, Mapping[str, object]]) -> list[tuple[str, int, float, float]]:
        result: list[tuple[str, int, float, float]] = []
        for name, raw in configs.items():
            label = str(name).strip()
            if not label:
                raise ValueError("Every saved RSI strategy needs a name")
            try:
                length = int(raw["length"])
                lower = float(raw["lower"])
                upper = float(raw["upper"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid RSI settings for {label}") from exc
            # Constructor is the single source of validation for RSI inputs.
            RsiLevelBacktester(length, lower, upper, stop_pct=0.05)
            result.append((label, length, lower, upper))
        if not result:
            raise ValueError("Select at least one saved RSI strategy")
        return result

    def run(
        self,
        weekly_snapshots: Iterable[OptionSnapshot],
        monthly_snapshots: Iterable[OptionSnapshot],
        rsi_strategies: Mapping[str, Mapping[str, object]],
        *,
        one_minute_closes: Mapping[datetime, float] | None = None,
    ) -> UnifiedBacktestResult:
        weekly = tuple(sorted(weekly_snapshots, key=lambda item: item.timestamp))
        monthly = tuple(sorted(monthly_snapshots, key=lambda item: item.timestamp))
        if not weekly:
            raise ValueError("No nearest-weekly historical snapshots were available")
        if not monthly:
            raise ValueError("No nearest-monthly historical snapshots were available")
        configs = self._configs(rsi_strategies)
        evidence_runs = EvidenceBacktester().run(weekly).runs
        runs: list[UnifiedStrategyRun] = [
            UnifiedStrategyRun(
                item.name, "Nearest weekly evidence check", item.rules, item.result,
            )
            for item in evidence_runs
        ] + [
            UnifiedStrategyRun(
                "Nandi OI V1", "Nearest weekly", self.OI_RULES,
                NandiBacktester(stop_pct=0.20, target_pct=0.30).run(weekly),
            )
        ]
        touch_results: list[tuple[str, RsiTouchResult]] = []
        for name, length, lower, upper in configs:
            rules = f"NIFTY RSI({length}) {lower:g}/{upper:g}; 5% option-premium stop; opposite RSI target"
            replay = RsiLevelBacktester(length, lower, upper, stop_pct=0.05)
            runs.extend((
                UnifiedStrategyRun(name, "Nearest weekly", rules, replay.run(weekly)),
                UnifiedStrategyRun(name, "Nearest monthly", rules, replay.run(monthly)),
            ))
            if one_minute_closes is not None:
                touch_results.append((
                    name,
                    RsiTouchAnalyzer(length, lower, upper, self.rsi_timeframes).run(
                        one_minute_closes, weekly[0].timestamp.date(), weekly[-1].timestamp.date(),
                    ),
                ))
        return UnifiedBacktestResult(
            weekly[0].timestamp.date(), weekly[-1].timestamp.date(), tuple(runs), tuple(touch_results),
        )
