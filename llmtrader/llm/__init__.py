import json

from .base import LLMClient, LLMError, LLMResponse
from .clients import OllamaClient, OpenAICompatClient, build_client

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "MockClient",
    "OllamaClient",
    "OpenAICompatClient",
    "build_client",
]


class MockClient(LLMClient):
    name = "mock"

    def __init__(self, responses=None, model="mock", **kwargs):
        super().__init__(model)
        self.responses = list(responses or [])
        self.calls = []

    def complete(self, system, user, json_schema=None):
        self.calls.append({"system": system, "user": user})
        if self.responses:
            payload = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        else:
            payload = {
                "action": "hold",
                "confidence": 5,
                "entry": None,
                "stop_loss": None,
                "take_profit": None,
                "size_multiplier": 1.0,
                "max_hold_minutes": 60,
                "thesis": "mock",
                "invalidation": "mock",
            }
        if callable(payload):
            payload = payload(system, user)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return LLMResponse(text=text, model=self.model, latency_ms=0)
