from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .backtest import BacktestResult, NandiBacktester
from .engine import NandiOIEngine
from .models import Decision, OptionSnapshot


@dataclass(frozen=True)
class EvidenceBacktestRun:
    name: str
    rules: str
    result: BacktestResult


@dataclass(frozen=True)
class EvidenceBacktestResult:
    runs: tuple[EvidenceBacktestRun, ...]


@dataclass(frozen=True)
class EvidenceVariant:
    key: str
    name: str
    rules: str


class EvidenceSignalEngine:
    """Replay one evidence gate while retaining the live engine's raw calculations.

    These are validation experiments, not replacement trading strategies. Each
    experiment uses the same nearest-weekly historical snapshots and the same
    paper exit rules so its historical behaviour can be compared fairly.
    """

    def __init__(self, variant: EvidenceVariant) -> None:
        self.variant = variant
        self.engine = NandiOIEngine()

    def _decision(self, snapshot: OptionSnapshot, bull: float, bear: float,
                  bullish: bool, bearish: bool, reason: str) -> Decision:
        # A conflicting one-factor signal is deliberately a NO TRADE result.
        if bullish and not bearish:
            action = "BUY CE"
        elif bearish and not bullish:
            action = "BUY PE"
        else:
            action = "NO TRADE"
        selected = self.engine._select_strike(snapshot, action) if action != "NO TRADE" else None
        return Decision(
            action=action,
            bullish_score=round(bull, 1),
            bearish_score=round(bear, 1),
            confidence=round(max(bull, bear), 1),
            approved=action != "NO TRADE",
            selected_strike=selected,
            reasons=(reason,) if action != "NO TRADE" else (),
            blockers=() if action != "NO TRADE" else (f"{self.variant.name} did not give one clear direction",),
        )

    def add_snapshot(self, snapshot: OptionSnapshot) -> Decision:
        engine = self.engine
        engine.history.append(snapshot)
        oi_bull, oi_bear, _ = engine._oi_premium_scores(snapshot)
        price_bull, price_bear, _ = engine._price_scores(snapshot)
        wall_bull, wall_bear, _ = engine._wall_scores(snapshot)
        premium_bull, premium_bear, liquidity, _ = engine._premium_liquidity_scores(snapshot)
        persistent_bull, persistent_bear = engine._persistent_direction()

        if self.variant.key == "OI_FLOW":
            return self._decision(
                snapshot, oi_bull, oi_bear,
                oi_bull >= 55 and oi_bull - oi_bear >= 10,
                oi_bear >= 55 and oi_bear - oi_bull >= 10,
                "Nearby OI flow favoured this direction",
            )
        if self.variant.key == "PRICE_STRUCTURE":
            return self._decision(
                snapshot, price_bull, price_bear, bool(price_bull), bool(price_bear),
                "NIFTY price structure confirmed this direction",
            )
        if self.variant.key == "OI_WALLS":
            return self._decision(
                snapshot, wall_bull, wall_bear, wall_bull == 100, wall_bear == 100,
                "Nearby OI walls shifted in this direction",
            )
        if self.variant.key == "PREMIUM_LIQUIDITY":
            return self._decision(
                snapshot, premium_bull, premium_bear,
                premium_bull == 100 and liquidity == 100,
                premium_bear == 100 and liquidity == 100,
                "ATM option premium and liquidity confirmed this direction",
            )
        if self.variant.key == "PERSISTENCE":
            return self._decision(
                snapshot, 100 if persistent_bull else 0, 100 if persistent_bear else 0,
                persistent_bull, persistent_bear,
                "OI flow persisted across three snapshots in this direction",
            )
        raise ValueError(f"Unsupported evidence variant: {self.variant.key}")


class EvidenceBacktester:
    """Backtest every individual OI V1 evidence gate before the final combined rule."""

    VARIANTS = (
        EvidenceVariant(
            "OI_FLOW", "OI flow", "Change-in-OI flow: nearby call/put positioning with a clear directional lead",
        ),
        EvidenceVariant(
            "PRICE_STRUCTURE", "NIFTY price structure", "NIFTY upward breakout or downward breakdown only",
        ),
        EvidenceVariant(
            "OI_WALLS", "OI-wall movement", "Nearby call and put OI walls shift together up or down",
        ),
        EvidenceVariant(
            "PREMIUM_LIQUIDITY", "Option premium and liquidity", "ATM premium rises with acceptable volume and spread",
        ),
        EvidenceVariant(
            "PERSISTENCE", "Three-snapshot OI persistence", "Directional OI flow remains confirmed for three snapshots",
        ),
    )

    @classmethod
    def variant(cls, name: str) -> EvidenceVariant:
        for item in cls.VARIANTS:
            if item.name == name:
                return item
        raise ValueError(f"Unknown OI evidence strategy: {name}")

    def run_one(self, snapshots: Iterable[OptionSnapshot], name: str) -> EvidenceBacktestRun:
        """Replay one named evidence strategy without calculating unrelated strategies."""
        records = tuple(sorted(snapshots, key=lambda item: item.timestamp))
        if not records:
            raise ValueError("No historical snapshots were available for evidence backtesting")
        variant = self.variant(name)
        result = NandiBacktester(
            stop_pct=0.20, target_pct=0.30,
            engine_factory=lambda: EvidenceSignalEngine(variant),
        ).run(records)
        return EvidenceBacktestRun(variant.name, variant.rules, result)

    def run(self, snapshots: Iterable[OptionSnapshot]) -> EvidenceBacktestResult:
        records = tuple(sorted(snapshots, key=lambda item: item.timestamp))
        if not records:
            raise ValueError("No historical snapshots were available for evidence backtesting")
        runs = []
        for variant in self.VARIANTS:
            runs.append(self.run_one(records, variant.name))
        return EvidenceBacktestResult(tuple(runs))
