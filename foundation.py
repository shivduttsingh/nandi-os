from dataclasses import dataclass
from typing import List

from config.settings import ADVISOR_PROFILE, INDIAN_MARKET_SCOPE


@dataclass(frozen=True)
class FoundationPillar:
    name: str
    status: str
    detail: str


FOUNDATION_PRINCIPLES = [
    "Private-first system for Shivdutt, not a public advisory product.",
    "Indian-market focus before global expansion.",
    "Risk control before opportunity chasing.",
    "Every research output should include reason, confidence, risk zone, and invalidation.",
    "Memory and journal should improve discipline over time.",
    "Important actions should be auditable in local logs.",
    "AI model routing should stay modular so the core app does not depend on one provider.",
]


def advisor_profile() -> str:
    return ADVISOR_PROFILE


def market_scope() -> List[str]:
    return list(INDIAN_MARKET_SCOPE)


def foundation_pillars() -> List[FoundationPillar]:
    return [
        FoundationPillar(
            "Security",
            "Active",
            "Login is protected by Streamlit secrets; future upgrades can add session expiry and encrypted local storage.",
        ),
        FoundationPillar(
            "Audit Trail",
            "Active",
            "Key actions are written to local logs so Nandi can be reviewed instead of acting like a black box.",
        ),
        FoundationPillar(
            "Financial Intelligence",
            "Active",
            "NFRM-1 scores market structure with confidence, risk zone, invalidation level, and evidence.",
        ),
        FoundationPillar(
            "Risk Engine",
            "Active",
            "Capital and risk-per-idea settings keep guidance connected to controlled risk budgets.",
        ),
        FoundationPillar(
            "Memory Core",
            "Active",
            "SQLite memory stores user-approved preferences, lessons, research rules, and system notes.",
        ),
        FoundationPillar(
            "Model Router",
            "Ready",
            "The routing layer is prepared for future OpenAI, Gemini, DeepSeek, and local-model support.",
        ),
    ]


def foundation_score() -> int:
    active = sum(1 for pillar in foundation_pillars() if pillar.status == "Active")
    return round((active / len(foundation_pillars())) * 100)
