from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable


class FundamentalBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FundamentalDefinition:
    key: str
    name: str
    category: str
    description: str
    max_age_minutes: int


@dataclass(frozen=True)
class FundamentalFactor:
    key: str
    name: str
    category: str
    bias: FundamentalBias
    impact: float
    confidence: float
    observed_at: datetime
    max_age_minutes: int
    source: str = "Manual research input"
    note: str = ""

    def age_minutes(self, now: datetime) -> float:
        observed = self.observed_at
        current = now
        if observed.tzinfo is None and current.tzinfo is not None:
            observed = observed.replace(tzinfo=current.tzinfo)
        elif observed.tzinfo is not None and current.tzinfo is None:
            current = current.replace(tzinfo=observed.tzinfo)
        return max(0.0, (current - observed).total_seconds() / 60.0)

    def fresh(self, now: datetime) -> bool:
        return self.age_minutes(now) <= self.max_age_minutes


@dataclass(frozen=True)
class FundamentalAssessment:
    direction: FundamentalBias
    setup_score: float
    bullish_score: float
    bearish_score: float
    coverage: float
    factors: tuple[FundamentalFactor, ...]
    blockers: tuple[str, ...] = tuple()
    reasons: tuple[str, ...] = tuple()

    def side_score(self, side: str) -> float:
        return self.bullish_score if side == "CE" else self.bearish_score


FUNDAMENTAL_CATALOGUE: tuple[FundamentalDefinition, ...] = (
    FundamentalDefinition(
        "global_risk",
        "Global market risk",
        "Global",
        "US and Asian risk tone, volatility and broad risk-on/risk-off behaviour.",
        240,
    ),
    FundamentalDefinition(
        "gift_nifty",
        "GIFT NIFTY lead",
        "Global",
        "Pre-open and live directional lead relative to the prior NSE close.",
        120,
    ),
    FundamentalDefinition(
        "currency_commodities",
        "USDINR, DXY and crude",
        "Macro",
        "Currency and crude-oil pressure relevant to Indian equities.",
        240,
    ),
    FundamentalDefinition(
        "institutional_flows",
        "FII and DII flows",
        "Flows",
        "Latest published institutional cash-market flow context.",
        2160,
    ),
    FundamentalDefinition(
        "macro_policy",
        "Macro and policy",
        "Macro",
        "RBI, inflation, rates, fiscal, regulatory and material policy developments.",
        4320,
    ),
    FundamentalDefinition(
        "heavyweight_earnings",
        "NIFTY heavyweight earnings",
        "Corporate",
        "Results or guidance from index-heavy constituents.",
        1440,
    ),
    FundamentalDefinition(
        "event_risk",
        "Scheduled and breaking event risk",
        "Events",
        "Known high-impact events or breaking news that can invalidate normal technical behaviour.",
        360,
    ),
)


def definition_map() -> dict[str, FundamentalDefinition]:
    return {item.key: item for item in FUNDAMENTAL_CATALOGUE}


def assess_fundamentals(
    factors: Iterable[FundamentalFactor],
    now: datetime | None = None,
    *,
    minimum_coverage: float = 60.0,
) -> FundamentalAssessment:
    current = now or datetime.now(timezone.utc)
    latest = {factor.key: factor for factor in factors}
    fresh_known = [
        factor
        for factor in latest.values()
        if factor.key in definition_map()
        and factor.bias != FundamentalBias.UNKNOWN
        and factor.fresh(current)
    ]
    coverage = len(fresh_known) / len(FUNDAMENTAL_CATALOGUE) * 100.0
    bullish_weight = sum(
        max(0.0, factor.impact) * max(0.0, factor.confidence)
        for factor in fresh_known
        if factor.bias == FundamentalBias.BULLISH
    )
    bearish_weight = sum(
        max(0.0, factor.impact) * max(0.0, factor.confidence)
        for factor in fresh_known
        if factor.bias == FundamentalBias.BEARISH
    )
    neutral_weight = sum(
        max(10.0, factor.impact) * max(0.1, factor.confidence)
        for factor in fresh_known
        if factor.bias == FundamentalBias.NEUTRAL
    )
    total = bullish_weight + bearish_weight + neutral_weight
    bullish_score = bullish_weight / total * 100.0 if total else 0.0
    bearish_score = bearish_weight / total * 100.0 if total else 0.0
    blockers: list[str] = []
    reasons: list[str] = []

    missing = [definition.name for definition in FUNDAMENTAL_CATALOGUE if definition.key not in {factor.key for factor in fresh_known}]
    if coverage < minimum_coverage:
        direction = FundamentalBias.UNKNOWN
        setup_score = 0.0
        blockers.append(
            f"Fresh fundamental coverage is only {coverage:.0f}% (minimum {minimum_coverage:.0f}%)."
        )
        if missing:
            blockers.append("Missing or stale: " + ", ".join(missing[:4]))
    elif bullish_score >= 55.0 and bullish_score - bearish_score >= 12.0:
        direction = FundamentalBias.BULLISH
        setup_score = bullish_score
    elif bearish_score >= 55.0 and bearish_score - bullish_score >= 12.0:
        direction = FundamentalBias.BEARISH
        setup_score = bearish_score
    else:
        direction = FundamentalBias.NEUTRAL
        setup_score = max(50.0, bullish_score, bearish_score)
        reasons.append("Fundamental inputs are sufficiently fresh but directionally balanced.")

    directional = sorted(
        (factor for factor in fresh_known if factor.bias in {FundamentalBias.BULLISH, FundamentalBias.BEARISH}),
        key=lambda factor: factor.impact * factor.confidence,
        reverse=True,
    )
    reasons.extend(
        f"{factor.name}: {factor.bias.value.lower()} ({factor.note or factor.source})"
        for factor in directional[:3]
    )

    return FundamentalAssessment(
        direction=direction,
        setup_score=round(setup_score, 1),
        bullish_score=round(bullish_score, 1),
        bearish_score=round(bearish_score, 1),
        coverage=round(coverage, 1),
        factors=tuple(sorted(latest.values(), key=lambda factor: factor.key)),
        blockers=tuple(blockers),
        reasons=tuple(reasons),
    )


def fundamental_rows(
    assessment: FundamentalAssessment,
    now: datetime,
) -> list[dict[str, object]]:
    definitions = definition_map()
    factors = {factor.key: factor for factor in assessment.factors}
    rows: list[dict[str, object]] = []
    for definition in FUNDAMENTAL_CATALOGUE:
        factor = factors.get(definition.key)
        rows.append(
            {
                "Category": definition.category,
                "Factor": definition.name,
                "Bias": factor.bias.value if factor else FundamentalBias.UNKNOWN.value,
                "Impact": round(factor.impact, 1) if factor else 0.0,
                "Confidence": round(factor.confidence * 100.0, 1) if factor else 0.0,
                "Age minutes": round(factor.age_minutes(now), 1) if factor else None,
                "Fresh": factor.fresh(now) if factor else False,
                "Source": factor.source if factor else "Not connected",
                "Note": factor.note if factor else definition.description,
            }
        )
    return rows
