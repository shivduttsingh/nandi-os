from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Iterable, Mapping

from .backtest import BacktestResult, NandiBacktester
from .evidence_backtest import EvidenceBacktester, EvidenceSignalEngine
from .engine import NandiOIEngine
from .models import OptionSnapshot
from .rsi_backtest import RsiLevelBacktester, RsiTouchResult, RsiTouchAnalyzer, TIMEFRAMES, wilder_rsi
from .technical_indicators import technical_context


@dataclass(frozen=True)
class StrategyBackground:
    """Plain-language explanation of the exact historical test being shown."""

    data_used: str
    technical_analysis: str
    entry_rule: str
    paper_risk: str
    purpose: str
    rsi_length: int = 14
    rsi_lower: float = 24.0
    rsi_upper: float = 72.0


def strategy_background(strategy: str, rules: str) -> StrategyBackground:
    """Keep every backtest comparable while making its inputs explicit to the user."""
    descriptions = {
        "OI flow": StrategyBackground(
            "Nearby call and put change-in-OI plus option premium movement.",
            "Open-interest activity: call covering / put writing versus put covering / call writing.",
            "Validation signal needs a 55+ directional OI score and at least a 10-point lead.",
            "20% option-premium stop and 30% target, used only to compare this one evidence gate.",
            "Shows whether OI positioning alone had useful historical direction before it is combined with other checks.",
        ),
        "NIFTY price structure": StrategyBackground(
            "NIFTY spot, intraday recent high and intraday recent low.",
            "Price-action breakout and breakdown confirmation.",
            "Buy CE only after an upward breakout; buy PE only after a downward breakdown.",
            "20% option-premium stop and 30% target, used only to compare this one evidence gate.",
            "Tests price structure by itself; it is not the full Nandi decision.",
        ),
        "OI-wall movement": StrategyBackground(
            "Highest nearby call-OI and put-OI strike on each five-minute snapshot.",
            "Support/resistance wall movement around the ATM option strikes.",
            "Both CE and PE OI walls must shift upward for CE or downward for PE.",
            "20% option-premium stop and 30% target, used only to compare this one evidence gate.",
            "Tests whether moving OI walls added directional information on their own.",
        ),
        "Option premium and liquidity": StrategyBackground(
            "ATM CE/PE premium change, volume and bid/ask spread.",
            "Option momentum with a liquidity quality filter.",
            "ATM premium must rise and the option must have usable volume and spread.",
            "20% option-premium stop and 30% target, used only to compare this one evidence gate.",
            "Tests whether option confirmation alone was informative before it is combined with OI and price.",
        ),
        "Three-snapshot OI persistence": StrategyBackground(
            "Three consecutive nearby OI-flow readings from the same NIFTY option chain.",
            "Persistence: does the same directional positioning remain after the first snapshot?",
            "A direction must remain valid for all three required snapshots.",
            "20% option-premium stop and 30% target, used only to compare this one evidence gate.",
            "Prevents a one-snapshot OI move from being treated as a reliable setup.",
        ),
        "Nandi OI V1": StrategyBackground(
            "OI flow, OI walls, NIFTY price structure, ATM premium/liquidity and three-snapshot persistence.",
            "A weighted evidence score with price and option-premium confirmation.",
            "Score must be at least 80/100 with a 20-point lead, price/premium confirmation and persistence.",
            "20% option-premium stop, 30% premium target, maximum three paper trades per day.",
            "This is the production paper-research rule: all OI evidence gates must agree before a signal is approved.",
        ),
    }
    if strategy in descriptions:
        return descriptions[strategy]

    match = re.search(r"RSI\((\d+)\)\s+([\d.]+)/([\d.]+)", rules)
    if match:
        length, lower, upper = int(match.group(1)), float(match.group(2)), float(match.group(3))
    else:
        length, lower, upper = 14, 24.0, 72.0
    return StrategyBackground(
        "Five-minute NIFTY spot history and the nearest weekly or monthly option premium.",
        f"Wilder RSI({length}) calculated without future-data access.",
        f"Buy CE on an RSI touch at or below {lower:g}; buy PE on an RSI touch at or above {upper:g}.",
        "5% option-premium stop; exit when RSI reaches the opposite configured level.",
        "Tests this saved RSI configuration separately on the selected expiry contract.",
        length, lower, upper,
    )


OI_STRATEGY_NAMES = (
    "OI flow",
    "NIFTY price structure",
    "OI-wall movement",
    "Option premium and liquidity",
    "Three-snapshot OI persistence",
    "Nandi OI V1",
)


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

    @property
    def background(self) -> StrategyBackground:
        return strategy_background(self.strategy, self.rules)

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
    weekly_snapshots: tuple[OptionSnapshot, ...] = ()
    monthly_snapshots: tuple[OptionSnapshot, ...] = ()

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

    def strategy_run(self, label: str) -> UnifiedStrategyRun:
        for run in self.runs:
            if run.label == label:
                return run
        raise ValueError(f"Unknown strategy run: {label}")

    def available_dates(self) -> tuple[date, ...]:
        return tuple(sorted({item.timestamp.date() for item in self.weekly_snapshots}))

    def _snapshots_for(self, run: UnifiedStrategyRun) -> tuple[OptionSnapshot, ...]:
        return self.monthly_snapshots if "monthly" in run.contract.lower() else self.weekly_snapshots

    def daily_strategy_rows(self, selected_date: date | None = None) -> list[dict[str, object]]:
        """One daily outcome row per strategy, including days with no paper entry."""
        rows: list[dict[str, object]] = []
        for run in self.runs:
            snapshots = self._snapshots_for(run)
            dates = sorted({item.timestamp.date() for item in snapshots})
            for day in dates:
                if selected_date and day != selected_date:
                    continue
                day_snapshots = [item for item in snapshots if item.timestamp.date() == day]
                trades = [trade for trade in run.result.trades if trade.opened_at.date() == day]
                wins = sum(trade.pnl_points > 0 for trade in trades)
                points = round(sum(trade.pnl_points for trade in trades), 2)
                rows.append({
                    "Date": day,
                    "Strategy": run.strategy,
                    "Contract": run.contract,
                    "Technical analysis": run.background.technical_analysis,
                    "Historical snapshots": len(day_snapshots),
                    "Paper trades": len(trades),
                    "Wins": wins,
                    "Win rate %": round(wins / len(trades) * 100, 1) if trades else 0.0,
                    "Net premium points": points,
                    "Daily result": "No paper entry" if not trades else ("Net positive" if points > 0 else "Net negative" if points < 0 else "Flat"),
                })
        return sorted(rows, key=lambda item: (item["Date"], item["Strategy"], item["Contract"]))

    @staticmethod
    def _walls(engine: NandiOIEngine, snapshot: OptionSnapshot) -> tuple[float | None, float | None]:
        legs = engine._nearby_legs(snapshot)
        calls = [leg for leg in legs if leg.side == "CE"]
        puts = [leg for leg in legs if leg.side == "PE"]
        ce_wall = max(calls, key=lambda item: item.oi).strike if calls else None
        pe_wall = max(puts, key=lambda item: item.oi).strike if puts else None
        return ce_wall, pe_wall

    def daily_chart_rows(
        self, selected_date: date, *, run: UnifiedStrategyRun | None = None,
        rsi_length: int = 14, rsi_lower: float = 24.0, rsi_upper: float = 72.0,
    ) -> list[dict[str, object]]:
        """Return the same historical evidence Nandi used, not a decorative chart."""
        source = self._snapshots_for(run) if run else self.weekly_snapshots
        ordered = tuple(sorted(source, key=lambda item: item.timestamp))
        rsi_values = wilder_rsi([item.spot for item in ordered], rsi_length)
        context_values = technical_context([item.spot for item in ordered])
        engine = NandiOIEngine()
        strategy_engine: NandiOIEngine | EvidenceSignalEngine | None = None
        current_day: date | None = None
        rows: list[dict[str, object]] = []
        for snapshot, rsi, context in zip(ordered, rsi_values, context_values):
            day = snapshot.timestamp.date()
            if day != current_day:
                engine = NandiOIEngine()
                if run and run.strategy in OI_STRATEGY_NAMES and run.strategy != "Nandi OI V1":
                    strategy_engine = EvidenceSignalEngine(EvidenceBacktester.variant(run.strategy))
                else:
                    strategy_engine = None
                current_day = day
            decision = engine.add_snapshot(snapshot)
            strategy_decision = strategy_engine.add_snapshot(snapshot) if strategy_engine else decision
            oi_bull, oi_bear, _ = engine._oi_premium_scores(snapshot)
            price_bull, price_bear, _ = engine._price_scores(snapshot)
            wall_bull, wall_bear, _ = engine._wall_scores(snapshot)
            premium_bull, premium_bear, liquidity, _ = engine._premium_liquidity_scores(snapshot)
            persistent_bull, persistent_bear = engine._persistent_direction()
            nearby = engine._nearby_legs(snapshot)
            ce_change = sum(item.change_oi for item in nearby if item.side == "CE")
            pe_change = sum(item.change_oi for item in nearby if item.side == "PE")
            ce_wall, pe_wall = self._walls(engine, snapshot)
            atm = engine._atm(snapshot)
            atm_ce = next((item for item in nearby if item.side == "CE" and item.strike == atm), None)
            atm_pe = next((item for item in nearby if item.side == "PE" and item.strike == atm), None)
            if day == selected_date:
                rows.append({
                    "Timestamp": snapshot.timestamp,
                    "NIFTY spot": snapshot.spot,
                    "NIFTY change": snapshot.spot_change,
                    "Recent high": snapshot.recent_high,
                    "Recent low": snapshot.recent_low,
                    "Nearby CE ΔOI": ce_change,
                    "Nearby PE ΔOI": pe_change,
                    "CE OI wall": ce_wall,
                    "PE OI wall": pe_wall,
                    "ATM CE premium": atm_ce.ltp if atm_ce else None,
                    "ATM PE premium": atm_pe.ltp if atm_pe else None,
                    "OI bullish score": round(oi_bull, 1),
                    "OI bearish score": round(oi_bear, 1),
                    "Price bullish": price_bull,
                    "Price bearish": price_bear,
                    "Wall bullish": wall_bull,
                    "Wall bearish": wall_bear,
                    "Premium bullish": premium_bull,
                    "Premium bearish": premium_bear,
                    "Liquidity score": liquidity,
                    "Persistence bullish": 100.0 if persistent_bull else 0.0,
                    "Persistence bearish": 100.0 if persistent_bear else 0.0,
                    "Nandi bullish score": decision.bullish_score,
                    "Nandi bearish score": decision.bearish_score,
                    "Final action": decision.action,
                    "Strategy bullish score": strategy_decision.bullish_score,
                    "Strategy bearish score": strategy_decision.bearish_score,
                    "Strategy action": strategy_decision.action,
                    f"RSI({rsi_length})": round(rsi, 2) if rsi is not None else None,
                    "RSI lower level": rsi_lower,
                    "RSI upper level": rsi_upper,
                    **context,
                })
        return rows

    def option_chain_rows(self, timestamp: datetime, run: UnifiedStrategyRun | None = None) -> list[dict[str, object]]:
        """Expose the exact ATM ±5 rows used for an OI point on the daily chart."""
        source = self._snapshots_for(run) if run else self.weekly_snapshots
        snapshot = next((item for item in source if item.timestamp == timestamp), None)
        if not snapshot:
            raise ValueError("The requested historical option snapshot is not available")
        engine = NandiOIEngine()
        atm = engine._atm(snapshot)
        rows = []
        for leg in sorted(engine._nearby_legs(snapshot), key=lambda item: (item.strike, item.side)):
            rows.append({
                "Timestamp": snapshot.timestamp,
                "Expiry": snapshot.expiry,
                "NIFTY spot": snapshot.spot,
                "ATM strike": atm,
                "Strike": leg.strike,
                "Side": leg.side,
                "Distance from ATM": round(leg.strike - atm, 2),
                "Open interest": leg.oi,
                "Change in OI": leg.change_oi,
                "Option premium": leg.ltp,
                "Premium change": leg.change_ltp,
                "Volume": leg.volume,
                "Bid": leg.bid,
                "Ask": leg.ask,
                "Spread %": round(leg.spread_pct, 2),
                "OI/premium activity": leg.activity,
            })
        return rows

    def data_provenance_rows(self, timestamp: datetime, run: UnifiedStrategyRun) -> list[dict[str, object]]:
        """State the data source and replay limits alongside each chart and calculation."""
        snapshot = next((item for item in self._snapshots_for(run) if item.timestamp == timestamp), None)
        if not snapshot:
            raise ValueError("The requested historical option snapshot is not available")
        return [
            {"Item": "Underlying", "Value": "NIFTY spot index"},
            {"Item": "Option data source", "Value": "Upstox Plus expired-instrument historical option candles"},
            {"Item": "Snapshot interval", "Value": "Five-minute historical replay"},
            {"Item": "Option contract used", "Value": f"{run.contract} • {snapshot.expiry}"},
            {"Item": "Displayed option rows", "Value": "ATM ±5 strikes (the exact nearby window in this replay)"},
            {"Item": "Selected strategy contract", "Value": run.contract},
            {"Item": "Selected timestamp", "Value": snapshot.timestamp.isoformat(sep=" ", timespec="minutes")},
            {"Item": "Future-data access", "Value": "None — calculations replay each snapshot chronologically"},
            {"Item": "Broker orders", "Value": "None — paper research only"},
        ]

    @staticmethod
    def calculation_rows(evidence: Mapping[str, object]) -> list[dict[str, object]]:
        """Show the exact weighted score calculation used by Nandi OI V1."""
        components = (
            ("OI flow", 0.35, "OI bullish score", "OI bearish score", "Nearby CE/PE option-chain positioning"),
            ("NIFTY price structure", 0.25, "Price bullish", "Price bearish", "Breakout above recent high or breakdown below recent low"),
            ("OI-wall movement", 0.15, "Wall bullish", "Wall bearish", "Largest nearby CE and PE OI walls move together"),
            ("ATM option premium", 0.10, "Premium bullish", "Premium bearish", "ATM premium rises with usable liquidity"),
            ("Three-snapshot persistence", 0.10, "Persistence bullish", "Persistence bearish", "The OI direction remains confirmed for three snapshots"),
            ("Liquidity quality", 0.05, "Liquidity score", "Liquidity score", "Volume and bid/ask spread pass the quality filter"),
        )
        rows: list[dict[str, object]] = []
        for name, weight, bull_key, bear_key, purpose in components:
            bull = float(evidence.get(bull_key, 0.0) or 0.0)
            bear = float(evidence.get(bear_key, 0.0) or 0.0)
            rows.append({
                "Calculation": name,
                "Weight": f"{weight * 100:.0f}%",
                "Bullish raw": round(bull, 1),
                "Bullish contribution": round(bull * weight, 1),
                "Bearish raw": round(bear, 1),
                "Bearish contribution": round(bear * weight, 1),
                "What Nandi checks": purpose,
            })
        return rows

    @staticmethod
    def approval_rows(evidence: Mapping[str, object]) -> list[dict[str, object]]:
        """Make the non-score approval gates visible beside the weighted calculation."""
        bull = float(evidence.get("Nandi bullish score", 0.0) or 0.0)
        bear = float(evidence.get("Nandi bearish score", 0.0) or 0.0)
        lead = abs(bull - bear)
        return [
            {"Approval gate": "Score", "Requirement": "At least 80/100", "Observed": f"Bull {bull:.1f} / Bear {bear:.1f}", "Pass": max(bull, bear) >= 80},
            {"Approval gate": "Directional lead", "Requirement": "At least 20 points", "Observed": f"{lead:.1f} points", "Pass": lead >= 20},
            {"Approval gate": "NIFTY price", "Requirement": "Breakout or breakdown", "Observed": f"Bull {evidence.get('Price bullish', 0):.0f} / Bear {evidence.get('Price bearish', 0):.0f}", "Pass": bool(evidence.get("Price bullish") or evidence.get("Price bearish"))},
            {"Approval gate": "ATM premium", "Requirement": "Premium confirmation", "Observed": f"Bull {evidence.get('Premium bullish', 0):.0f} / Bear {evidence.get('Premium bearish', 0):.0f}", "Pass": bool(evidence.get("Premium bullish") or evidence.get("Premium bearish"))},
            {"Approval gate": "Persistence", "Requirement": "Three confirming snapshots", "Observed": f"Bull {evidence.get('Persistence bullish', 0):.0f} / Bear {evidence.get('Persistence bearish', 0):.0f}", "Pass": bool(evidence.get("Persistence bullish") or evidence.get("Persistence bearish"))},
            {"Approval gate": "Final paper action", "Requirement": "All relevant gates align", "Observed": str(evidence.get("Final action", "NO TRADE")), "Pass": evidence.get("Final action") != "NO TRADE"},
        ]

    @staticmethod
    def strategy_calculation_rows(run: UnifiedStrategyRun, evidence: Mapping[str, object]) -> list[dict[str, object]]:
        """Display the specific formula for one strategy instead of a generic all-strategy table."""
        if run.strategy == "Nandi OI V1":
            return UnifiedBacktestResult.calculation_rows(evidence)
        if run.strategy == "OI flow":
            bull, bear = float(evidence["OI bullish score"]), float(evidence["OI bearish score"])
            return [
                {"Calculation": "Bullish nearby OI score", "Observed": bull, "Rule": "At least 55"},
                {"Calculation": "Bearish nearby OI score", "Observed": bear, "Rule": "At least 55"},
                {"Calculation": "Bullish lead", "Observed": round(bull - bear, 1), "Rule": "At least +10 for CE"},
                {"Calculation": "Bearish lead", "Observed": round(bear - bull, 1), "Rule": "At least +10 for PE"},
            ]
        if run.strategy == "NIFTY price structure":
            return [
                {"Calculation": "NIFTY spot", "Observed": evidence["NIFTY spot"], "Rule": "Compare with recent high/low"},
                {"Calculation": "NIFTY change", "Observed": evidence["NIFTY change"], "Rule": "Must agree with breakout direction"},
                {"Calculation": "Recent high", "Observed": evidence["Recent high"], "Rule": "Spot above for a bullish breakout"},
                {"Calculation": "Recent low", "Observed": evidence["Recent low"], "Rule": "Spot below for a bearish breakdown"},
            ]
        if run.strategy == "OI-wall movement":
            return [
                {"Calculation": "CE OI wall", "Observed": evidence["CE OI wall"], "Rule": "Largest nearby call OI strike"},
                {"Calculation": "PE OI wall", "Observed": evidence["PE OI wall"], "Rule": "Largest nearby put OI strike"},
                {"Calculation": "Bullish wall score", "Observed": evidence["Wall bullish"], "Rule": "100 means both walls shifted upward"},
                {"Calculation": "Bearish wall score", "Observed": evidence["Wall bearish"], "Rule": "100 means both walls shifted downward"},
            ]
        if run.strategy == "Option premium and liquidity":
            return [
                {"Calculation": "ATM CE premium", "Observed": evidence["ATM CE premium"], "Rule": "Premium must confirm CE direction"},
                {"Calculation": "ATM PE premium", "Observed": evidence["ATM PE premium"], "Rule": "Premium must confirm PE direction"},
                {"Calculation": "Bullish premium score", "Observed": evidence["Premium bullish"], "Rule": "100 with liquidity quality"},
                {"Calculation": "Bearish premium score", "Observed": evidence["Premium bearish"], "Rule": "100 with liquidity quality"},
                {"Calculation": "Liquidity score", "Observed": evidence["Liquidity score"], "Rule": "100 means volume/spread passed"},
            ]
        if run.strategy == "Three-snapshot OI persistence":
            return [
                {"Calculation": "Bullish persistence", "Observed": evidence["Persistence bullish"], "Rule": "100 after three bullish OI snapshots"},
                {"Calculation": "Bearish persistence", "Observed": evidence["Persistence bearish"], "Rule": "100 after three bearish OI snapshots"},
                {"Calculation": "Required snapshots", "Observed": 3, "Rule": "Same OI direction must persist"},
            ]
        return [
            {"Calculation": f"RSI({run.background.rsi_length})", "Observed": evidence.get(f"RSI({run.background.rsi_length})"), "Rule": run.background.entry_rule},
            {"Calculation": "Lower RSI level", "Observed": run.background.rsi_lower, "Rule": "CE entry zone"},
            {"Calculation": "Upper RSI level", "Observed": run.background.rsi_upper, "Rule": "PE entry zone"},
        ]

    @staticmethod
    def strategy_approval_rows(run: UnifiedStrategyRun, evidence: Mapping[str, object]) -> list[dict[str, object]]:
        if run.strategy == "Nandi OI V1":
            return UnifiedBacktestResult.approval_rows(evidence)
        if run.strategy == "OI flow":
            bull, bear = float(evidence["OI bullish score"]), float(evidence["OI bearish score"])
            return [
                {"Approval gate": "Bullish OI entry", "Requirement": "Score ≥55 and lead ≥10", "Observed": f"Score {bull:.1f}, lead {bull-bear:.1f}", "Pass": bull >= 55 and bull - bear >= 10},
                {"Approval gate": "Bearish OI entry", "Requirement": "Score ≥55 and lead ≥10", "Observed": f"Score {bear:.1f}, lead {bear-bull:.1f}", "Pass": bear >= 55 and bear - bull >= 10},
            ]
        if run.strategy == "NIFTY price structure":
            return [
                {"Approval gate": "Bullish breakout", "Requirement": "Spot > recent high and positive change", "Observed": f"Score {evidence['Price bullish']:.0f}", "Pass": evidence["Price bullish"] == 100},
                {"Approval gate": "Bearish breakdown", "Requirement": "Spot < recent low and negative change", "Observed": f"Score {evidence['Price bearish']:.0f}", "Pass": evidence["Price bearish"] == 100},
            ]
        if run.strategy == "OI-wall movement":
            return [
                {"Approval gate": "Bullish wall shift", "Requirement": "Both nearby OI walls move up", "Observed": evidence["Wall bullish"], "Pass": evidence["Wall bullish"] == 100},
                {"Approval gate": "Bearish wall shift", "Requirement": "Both nearby OI walls move down", "Observed": evidence["Wall bearish"], "Pass": evidence["Wall bearish"] == 100},
            ]
        if run.strategy == "Option premium and liquidity":
            return [
                {"Approval gate": "Bullish premium", "Requirement": "ATM CE premium + liquidity pass", "Observed": evidence["Premium bullish"], "Pass": evidence["Premium bullish"] == 100 and evidence["Liquidity score"] == 100},
                {"Approval gate": "Bearish premium", "Requirement": "ATM PE premium + liquidity pass", "Observed": evidence["Premium bearish"], "Pass": evidence["Premium bearish"] == 100 and evidence["Liquidity score"] == 100},
            ]
        if run.strategy == "Three-snapshot OI persistence":
            return [
                {"Approval gate": "Bullish persistence", "Requirement": "Three bullish OI snapshots", "Observed": evidence["Persistence bullish"], "Pass": evidence["Persistence bullish"] == 100},
                {"Approval gate": "Bearish persistence", "Requirement": "Three bearish OI snapshots", "Observed": evidence["Persistence bearish"], "Pass": evidence["Persistence bearish"] == 100},
            ]
        rsi = evidence.get(f"RSI({run.background.rsi_length})")
        return [
            {"Approval gate": "CE RSI entry", "Requirement": f"RSI ≤ {run.background.rsi_lower:g}", "Observed": rsi, "Pass": rsi is not None and rsi <= run.background.rsi_lower},
            {"Approval gate": "PE RSI entry", "Requirement": f"RSI ≥ {run.background.rsi_upper:g}", "Observed": rsi, "Pass": rsi is not None and rsi >= run.background.rsi_upper},
        ]


def run_one_oi_strategy(strategy: str, snapshots: Iterable[OptionSnapshot]) -> UnifiedBacktestResult:
    """Build one auditable daily result for one named OI strategy screen."""
    weekly = tuple(sorted(snapshots, key=lambda item: item.timestamp))
    if not weekly:
        raise ValueError("No nearest-weekly historical snapshots were available")
    if strategy not in OI_STRATEGY_NAMES:
        raise ValueError(f"Unknown OI strategy: {strategy}")
    if strategy == "Nandi OI V1":
        run = UnifiedStrategyRun(
            strategy, "Nearest weekly", UnifiedBacktester.OI_RULES,
            NandiBacktester(stop_pct=0.20, target_pct=0.30).run(weekly),
        )
    else:
        evidence = EvidenceBacktester().run_one(weekly, strategy)
        run = UnifiedStrategyRun(
            evidence.name, "Nearest weekly evidence check", evidence.rules, evidence.result,
        )
    return UnifiedBacktestResult(
        weekly[0].timestamp.date(), weekly[-1].timestamp.date(), (run,), (), weekly, (),
    )


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
            weekly[0].timestamp.date(), weekly[-1].timestamp.date(), tuple(runs), tuple(touch_results), weekly, monthly,
        )
