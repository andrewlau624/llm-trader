from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int = 0
    prompt_tokens: int = None
    completion_tokens: int = None
    raw: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


class LLMClient:
    name = "base"

    def __init__(self, model, temperature=0.2, timeout=120):
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, system, user, json_schema=None):
        raise NotImplementedError
