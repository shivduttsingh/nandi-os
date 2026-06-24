from config.settings import RESEARCH_DISCLAIMER


BLOCKED_FINANCE_PHRASES = [
    "guaranteed profit",
    "sure shot",
    "100% sure",
    "risk free",
    "must buy",
    "must sell",
]


def finance_language_guard(text: str) -> str:
    """Soft guardrail for product copy and finance output."""
    clean = text or ""
    replacements = {
        "BUY CALL": "Call-side research scenario",
        "BUY PUT": "Put-side research scenario",
        "BUY": "Bullish research view",
        "SELL": "Bearish research view",
        "signal": "research output",
        "trade now": "review the setup manually",
    }
    for old, new in replacements.items():
        clean = clean.replace(old, new)
        clean = clean.replace(old.lower(), new.lower())
    return clean


def research_disclaimer() -> str:
    return RESEARCH_DISCLAIMER


def is_safe_finance_text(text: str) -> bool:
    lower = (text or "").lower()
    return not any(phrase in lower for phrase in BLOCKED_FINANCE_PHRASES)
