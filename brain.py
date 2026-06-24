from dataclasses import dataclass
from core.memory import MemoryStore
from core.foundation import advisor_profile
from core.safety_engine import research_disclaimer
from config.settings import APP_MODE, APP_VERSION


@dataclass
class BrainResponse:
    intent: str
    answer: str
    confidence: int


class NandiBrain:
    """Nandi's local control layer.

    This is intentionally simple in V2: route, remember, and explain. Later this
    layer can connect to OpenAI/Gemini/DeepSeek through model_router.py.
    """

    def __init__(self):
        self.memory = MemoryStore()

    def classify(self, message: str) -> str:
        m = (message or "").lower()
        if any(w in m for w in ["nifty", "market", "option", "finance", "rsi", "strategy", "backtest"]):
            return "finance_research"
        if any(w in m for w in ["remember", "save", "note"]):
            return "memory"
        if any(w in m for w in ["goal", "task", "plan"]):
            return "goals"
        return "general"

    def respond(self, message: str) -> BrainResponse:
        intent = self.classify(message)
        if intent == "finance_research":
            return BrainResponse(
                intent=intent,
                confidence=82,
                answer=(
                    "I can guide this as your private financial intelligence advisor. "
                    "Open the Finance page and run NFRM-1 to view market structure, "
                    "advisor view, confidence score, invalidation level, and risk zone. "
                    "A strong answer should include evidence, risk, and what would make the idea invalid.\n\n"
                    + research_disclaimer()
                ),
            )
        if intent == "memory":
            return BrainResponse(
                intent=intent,
                confidence=70,
                answer="Use the Memory page to save this properly with category and importance. I will store it in local SQLite memory.",
            )
        if intent == "goals":
            return BrainResponse(
                intent=intent,
                confidence=68,
                answer="Goals module is ready as a V2 placeholder. Next upgrade will add task database, priorities, reminders, and daily execution tracking.",
            )
        return BrainResponse(
            intent=intent,
            confidence=65,
            answer=(
                f"Nandi OS is running in {APP_MODE}. Current core: {APP_VERSION}.\n\n"
                f"{advisor_profile()}\n\n"
                "Ask me about finance guidance, memory, goals, foundation strength, or system setup."
            ),
        )
