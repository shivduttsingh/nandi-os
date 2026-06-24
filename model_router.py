from dataclasses import dataclass


@dataclass
class ModelRoute:
    task_type: str
    preferred_model: str
    reason: str


class ModelRouter:
    """Placeholder router for future multi-model architecture."""

    def route(self, task_type: str) -> ModelRoute:
        task_type = (task_type or "general").lower()
        if "code" in task_type:
            return ModelRoute(task_type, "DeepSeek / GPT coding model", "Coding and logic-heavy task.")
        if "document" in task_type or "pdf" in task_type:
            return ModelRoute(task_type, "Gemini / long-context model", "Large document reading task.")
        if "finance" in task_type or "strategy" in task_type:
            return ModelRoute(task_type, "GPT reasoning model", "Needs explanation, risk framing, and structured reasoning.")
        return ModelRoute(task_type, "GPT reasoning model", "Default balanced reasoning path.")
