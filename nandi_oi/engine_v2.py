from __future__ import annotations

from collections import deque
from statistics import median

from .engine import EngineConfig, NandiOIEngine
from .models import Decision, OptionLeg, OptionSnapshot


class NandiOIEngineV2(NandiOIEngine):
    """Regime-gated, multi-strike OI-flow engine using only observable fields."""

    def __init__(self) -> None:
        super().__init__(EngineConfig(
            strikes_each_side=3, approval_score=75.0, minimum_lead=25.0,
            persistence_snapshots=3, maximum_spread_pct=3.0, minimum_volume=1.0,
        ))
        self.history: deque[OptionSnapshot] = deque(maxlen=30)

    @staticmethod
    def _flow_strength(leg: OptionLeg) -> float:
        prior_oi = max(abs(leg.oi - leg.change_oi), 1.0)
        prior_ltp = max(abs(leg.ltp - leg.change_ltp), 0.05)
        oi_strength = min(abs(leg.change_oi) / prior_oi / 0.005, 1.0)
        premium_strength = min(abs(leg.change_ltp) / prior_ltp / 0.02, 1.0)
        return 0.65 * oi_strength + 0.35 * premium_strength

    @staticmethod
    def _supports(leg: OptionLeg) -> tuple[bool, bool]:
        activity = leg.activity
        bullish = (
            (leg.side == "CE" and activity in {"FRESH BUYING", "SHORT COVERING"})
            or (leg.side == "PE" and activity == "FRESH WRITING")
        )
        bearish = (
            (leg.side == "PE" and activity in {"FRESH BUYING", "SHORT COVERING"})
            or (leg.side == "CE" and activity == "FRESH WRITING")
        )
        return bullish, bearish

    def _flow_scores(self, snapshot: OptionSnapshot) -> tuple[float, float, int, int]:
        legs = self._nearby_legs(snapshot)
        atm = self._atm(snapshot)
        step = self._strike_step(legs)
        bull = bear = total = 0.0
        bull_count = bear_count = 0
        for leg in legs:
            weight = self._distance_weight(leg.strike, atm, step)
            total += weight
            bullish, bearish = self._supports(leg)
            strength = self._flow_strength(leg) * weight
            if bullish:
                bull += strength
                bull_count += 1
            if bearish:
                bear += strength
                bear_count += 1
        denominator = max(total / 2, 1.0)
        return min(100.0, bull / denominator * 100), min(100.0, bear / denominator * 100), bull_count, bear_count

    def _regime(self) -> tuple[str, float]:
        if len(self.history) < 8:
            return "UNCONFIRMED", 0.0
        recent = list(self.history)[-8:]
        recent = [item for item in recent if item.timestamp.date() == recent[-1].timestamp.date()]
        if len(recent) < 8:
            return "UNCONFIRMED", 0.0
        long_prices = [item.spot for item in recent]
        short_prices = long_prices[-4:]

        def movement(prices: list[float]) -> tuple[float, float]:
            displacement = prices[-1] - prices[0]
            path = sum(abs(b - a) for a, b in zip(prices, prices[1:]))
            return displacement, abs(displacement) / path if path else 0.0

        long_move, long_efficiency = movement(long_prices)
        short_move, short_efficiency = movement(short_prices)
        long_minimum = long_prices[-1] * 0.001
        short_minimum = short_prices[-1] * 0.0005
        efficiency = min(long_efficiency, short_efficiency)
        if efficiency >= 0.55 and long_move >= long_minimum and short_move >= short_minimum:
            return "UPTREND", efficiency
        if efficiency >= 0.55 and long_move <= -long_minimum and short_move <= -short_minimum:
            return "DOWNTREND", efficiency
        return "SIDEWAYS", efficiency

    def _volume_confirmation(self, snapshot: OptionSnapshot, direction: str) -> int:
        previous = list(self.history)[:-1][-4:]
        if len(previous) < 3:
            return 0
        confirmed = 0
        for leg in self._nearby_legs(snapshot):
            bullish, bearish = self._supports(leg)
            supports = bullish if direction == "BULL" else bearish
            prior_volumes = [
                old.volume for item in previous for old in item.legs
                if old.strike == leg.strike and old.side == leg.side and old.volume > 0
            ]
            if supports and prior_volumes and leg.volume >= median(prior_volumes):
                confirmed += 1
        return confirmed

    def _persistent_flow(self, direction: str) -> bool:
        if len(self.history) < self.config.persistence_snapshots:
            return False
        recent = list(self.history)[-self.config.persistence_snapshots:]
        scores = [self._flow_scores(item) for item in recent]
        if direction == "BULL":
            return all(bull >= 45 and bull > bear and bull_count >= 3 for bull, bear, bull_count, _ in scores)
        return all(bear >= 45 and bear > bull and bear_count >= 3 for bull, bear, _, bear_count in scores)

    def add_snapshot(self, snapshot: OptionSnapshot) -> Decision:
        self.history.append(snapshot)
        flow_bull, flow_bear, bull_count, bear_count = self._flow_scores(snapshot)
        regime, efficiency = self._regime()
        price_bull, price_bear, _ = self._price_scores(snapshot)
        wall_bull, wall_bear, _ = self._wall_scores(snapshot)
        premium_bull, premium_bear, liquidity, _ = self._premium_liquidity_scores(snapshot)
        persistent_bull = self._persistent_flow("BULL")
        persistent_bear = self._persistent_flow("BEAR")
        volume_bull = self._volume_confirmation(snapshot, "BULL")
        volume_bear = self._volume_confirmation(snapshot, "BEAR")

        regime_bull = 100.0 if regime == "UPTREND" and price_bull else 0.0
        regime_bear = 100.0 if regime == "DOWNTREND" and price_bear else 0.0
        bull = round(
            flow_bull * 0.35 + regime_bull * 0.30 + wall_bull * 0.10
            + premium_bull * 0.10 + (100 if persistent_bull else 0) * 0.10 + liquidity * 0.05, 1,
        )
        bear = round(
            flow_bear * 0.35 + regime_bear * 0.30 + wall_bear * 0.10
            + premium_bear * 0.10 + (100 if persistent_bear else 0) * 0.10 + liquidity * 0.05, 1,
        )

        action = "NO TRADE"
        if (
            bull >= self.config.approval_score and bull - bear >= self.config.minimum_lead
            and regime_bull and premium_bull and persistent_bull and bull_count >= 3 and volume_bull >= 3
        ):
            action = "BUY CE"
        elif (
            bear >= self.config.approval_score and bear - bull >= self.config.minimum_lead
            and regime_bear and premium_bear and persistent_bear and bear_count >= 3 and volume_bear >= 3
        ):
            action = "BUY PE"

        reasons: list[str] = []
        blockers: list[str] = []
        if action != "NO TRADE":
            reasons.extend([
                f"{max(bull_count, bear_count)} nearby option legs confirm OI flow",
                f"Market regime is {regime.lower()} (efficiency {efficiency:.0%})",
                "OI flow persisted across three snapshots",
                f"{max(volume_bull, volume_bear)} confirming legs have sustained relative volume",
                "Underlying breakout and option premium agree",
            ])
        else:
            if regime in {"SIDEWAYS", "UNCONFIRMED"}:
                blockers.append(f"Market regime is {regime.lower()}")
            if max(bull_count, bear_count) < 3:
                blockers.append("Fewer than three nearby legs confirm the direction")
            if not persistent_bull and not persistent_bear:
                blockers.append("OI flow has not persisted for three snapshots")
            if max(volume_bull, volume_bear) < 3:
                blockers.append("Fewer than three confirming legs have sustained relative volume")
            if max(bull, bear) < self.config.approval_score:
                blockers.append("V2 quality score is below 75")

        selected = self._select_strike(snapshot, action) if action != "NO TRADE" else None
        return Decision(
            action=action, bullish_score=bull, bearish_score=bear,
            confidence=max(bull, bear), approved=action != "NO TRADE",
            selected_strike=selected, reasons=tuple(reasons), blockers=tuple(blockers),
        )
