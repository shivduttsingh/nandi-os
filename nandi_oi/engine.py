from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import mean

from .models import Decision, OptionLeg, OptionSnapshot


@dataclass(frozen=True)
class EngineConfig:
    strikes_each_side: int = 5
    approval_score: float = 80.0
    minimum_lead: float = 20.0
    persistence_snapshots: int = 3
    maximum_spread_pct: float = 3.0
    minimum_volume: float = 1.0


class NandiOIEngine:
    """Scores OI, premium, wall movement, price and liquidity as one strategy."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.history: deque[OptionSnapshot] = deque(maxlen=max(10, self.config.persistence_snapshots + 2))

    @staticmethod
    def _atm(snapshot: OptionSnapshot) -> float:
        strikes = sorted({leg.strike for leg in snapshot.legs})
        if not strikes:
            raise ValueError("Snapshot contains no option legs")
        return min(strikes, key=lambda strike: abs(strike - snapshot.spot))

    def _nearby_legs(self, snapshot: OptionSnapshot) -> list[OptionLeg]:
        atm = self._atm(snapshot)
        strikes = sorted({leg.strike for leg in snapshot.legs}, key=lambda strike: abs(strike - atm))
        allowed = set(strikes[: 1 + (2 * self.config.strikes_each_side)])
        return [leg for leg in snapshot.legs if leg.strike in allowed]

    @staticmethod
    def _distance_weight(strike: float, atm: float, step: float) -> float:
        distance = round(abs(strike - atm) / step) if step else 0
        return {0: 1.00, 1: 0.85, 2: 0.70, 3: 0.55, 4: 0.40, 5: 0.30}.get(distance, 0.15)

    @staticmethod
    def _strike_step(legs: list[OptionLeg]) -> float:
        strikes = sorted({leg.strike for leg in legs})
        gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
        return min(gaps) if gaps else 50.0

    def _oi_premium_scores(self, snapshot: OptionSnapshot) -> tuple[float, float, list[str]]:
        legs = self._nearby_legs(snapshot)
        atm = self._atm(snapshot)
        step = self._strike_step(legs)
        bull = bear = total_weight = 0.0
        reasons: list[str] = []

        for leg in legs:
            weight = self._distance_weight(leg.strike, atm, step)
            total_weight += weight
            activity = leg.activity
            if leg.side == "CE" and activity == "SHORT COVERING":
                bull += weight
            elif leg.side == "PE" and activity == "FRESH WRITING":
                bull += weight
            elif leg.side == "CE" and activity == "FRESH WRITING":
                bear += weight
            elif leg.side == "PE" and activity == "SHORT COVERING":
                bear += weight

        denominator = max(total_weight / 2, 1.0)
        bull_pct = min(100.0, bull / denominator * 100)
        bear_pct = min(100.0, bear / denominator * 100)
        if bull_pct >= 55:
            reasons.append("Nearby strikes show call covering and/or put writing")
        if bear_pct >= 55:
            reasons.append("Nearby strikes show put covering and/or call writing")
        return bull_pct, bear_pct, reasons

    def _wall_scores(self, current: OptionSnapshot) -> tuple[float, float, list[str]]:
        if len(self.history) < 2:
            return 50.0, 50.0, []
        previous = self.history[-2]

        def walls(snapshot: OptionSnapshot) -> tuple[float, float]:
            legs = self._nearby_legs(snapshot)
            ce = [leg for leg in legs if leg.side == "CE"]
            pe = [leg for leg in legs if leg.side == "PE"]
            return max(ce, key=lambda x: x.oi).strike, max(pe, key=lambda x: x.oi).strike

        old_ce, old_pe = walls(previous)
        new_ce, new_pe = walls(current)
        if new_ce > old_ce and new_pe > old_pe:
            return 100.0, 0.0, ["CE and PE OI walls shifted upward"]
        if new_ce < old_ce and new_pe < old_pe:
            return 0.0, 100.0, ["CE and PE OI walls shifted downward"]
        return 50.0, 50.0, []

    def _price_scores(self, snapshot: OptionSnapshot) -> tuple[float, float, list[str]]:
        bull = 100.0 if snapshot.spot > snapshot.recent_high and snapshot.spot_change > 0 else 0.0
        bear = 100.0 if snapshot.spot < snapshot.recent_low and snapshot.spot_change < 0 else 0.0
        reasons = []
        if bull:
            reasons.append("NIFTY confirmed an upward breakout")
        if bear:
            reasons.append("NIFTY confirmed a downward breakdown")
        return bull, bear, reasons

    def _premium_liquidity_scores(self, snapshot: OptionSnapshot) -> tuple[float, float, float, list[str]]:
        legs = self._nearby_legs(snapshot)
        atm = self._atm(snapshot)
        candidates = sorted(legs, key=lambda x: (abs(x.strike - atm), -x.volume))
        ce = next((x for x in candidates if x.side == "CE"), None)
        pe = next((x for x in candidates if x.side == "PE"), None)

        def quality(leg: OptionLeg | None) -> float:
            if not leg or leg.volume < self.config.minimum_volume:
                return 0.0
            if leg.spread_pct > self.config.maximum_spread_pct:
                return 0.0
            return 100.0 if leg.change_ltp > 0 else 0.0

        liquidity = mean([
            100.0 if ce and ce.volume >= self.config.minimum_volume and ce.spread_pct <= self.config.maximum_spread_pct else 0.0,
            100.0 if pe and pe.volume >= self.config.minimum_volume and pe.spread_pct <= self.config.maximum_spread_pct else 0.0,
        ])
        reasons = ["ATM options have acceptable volume and spread"] if liquidity == 100 else []
        return quality(ce), quality(pe), liquidity, reasons

    def _persistent_direction(self) -> tuple[bool, bool]:
        count = self.config.persistence_snapshots
        if len(self.history) < count:
            return False, False
        recent = list(self.history)[-count:]
        raw = [self._oi_premium_scores(s)[:2] for s in recent]
        bull = all(b >= 55 and b > r for b, r in raw)
        bear = all(r >= 55 and r > b for b, r in raw)
        return bull, bear

    def add_snapshot(self, snapshot: OptionSnapshot) -> Decision:
        self.history.append(snapshot)
        oi_bull, oi_bear, reasons = self._oi_premium_scores(snapshot)
        price_bull, price_bear, price_reasons = self._price_scores(snapshot)
        wall_bull, wall_bear, wall_reasons = self._wall_scores(snapshot)
        premium_bull, premium_bear, liquidity, premium_reasons = self._premium_liquidity_scores(snapshot)
        persistent_bull, persistent_bear = self._persistent_direction()

        bull = (
            oi_bull * 0.35 + price_bull * 0.25 + wall_bull * 0.15
            + premium_bull * 0.10 + (100.0 if persistent_bull else 0.0) * 0.10 + liquidity * 0.05
        )
        bear = (
            oi_bear * 0.35 + price_bear * 0.25 + wall_bear * 0.15
            + premium_bear * 0.10 + (100.0 if persistent_bear else 0.0) * 0.10 + liquidity * 0.05
        )
        bull, bear = round(bull, 1), round(bear, 1)
        blockers: list[str] = []
        action = "NO TRADE"

        bull_ok = bull >= self.config.approval_score and bull - bear >= self.config.minimum_lead
        bear_ok = bear >= self.config.approval_score and bear - bull >= self.config.minimum_lead
        if bull_ok and price_bull and premium_bull and persistent_bull:
            action = "BUY CE"
        elif bear_ok and price_bear and premium_bear and persistent_bear:
            action = "BUY PE"
        else:
            if len(self.history) < self.config.persistence_snapshots:
                blockers.append(f"Waiting for {self.config.persistence_snapshots} confirming snapshots")
            if not price_bull and not price_bear:
                blockers.append("NIFTY has not confirmed a breakout or breakdown")
            if abs(bull - bear) < self.config.minimum_lead:
                blockers.append("Bullish and bearish evidence is too close")
            if max(bull, bear) < self.config.approval_score:
                blockers.append("Probability score is below approval threshold")

        selected = self._select_strike(snapshot, action) if action != "NO TRADE" else None
        all_reasons = tuple(dict.fromkeys(reasons + price_reasons + wall_reasons + premium_reasons))
        return Decision(
            action=action,
            bullish_score=bull,
            bearish_score=bear,
            confidence=max(bull, bear),
            approved=action != "NO TRADE",
            selected_strike=selected,
            reasons=all_reasons,
            blockers=tuple(dict.fromkeys(blockers)),
        )

    def _select_strike(self, snapshot: OptionSnapshot, action: str) -> float | None:
        side = "CE" if action == "BUY CE" else "PE"
        atm = self._atm(snapshot)
        legs = [x for x in self._nearby_legs(snapshot) if x.side == side]
        liquid = [x for x in legs if x.volume >= self.config.minimum_volume and x.spread_pct <= self.config.maximum_spread_pct]
        candidates = liquid or legs
        if not candidates:
            return None
        # ATM first; if equal, favour one-strike ITM and then higher volume.
        return min(candidates, key=lambda x: (abs(x.strike - atm), -x.volume)).strike
