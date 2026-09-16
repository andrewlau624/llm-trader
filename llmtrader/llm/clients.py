import json
import time

import requests

from .base import LLMClient, LLMError, LLMResponse


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self, model="qwen2.5:7b", host="http://localhost:11434", temperature=0.2,
                 timeout=180, keep_alive="30m", num_ctx=8192, think=None):
        super().__init__(model, temperature, timeout)
        self.host = host.rstrip("/")
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.think = think

    def complete(self, system, user, json_schema=None):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if json_schema:
            payload["format"] = json_schema
        if self.think is not None:
            payload["think"] = self.think
        started = time.time()
        try:
            r = requests.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LLMError(f"ollama request failed: {e}") from e
        data = r.json()
        text = (data.get("message") or {}).get("content") or ""
        if not text.strip():
            raise LLMError("ollama returned an empty response")
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            latency_ms=int((time.time() - started) * 1000),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            raw=data,
        )


class OpenAICompatClient(LLMClient):
    name = "openai_compat"

    def __init__(self, model, base_url, api_key, temperature=0.2, timeout=120,
                 extra_headers=None):
        super().__init__(model, temperature, timeout)
        self.base_url = base_url
        self.api_key = api_key
        self.extra_headers = extra_headers or {}

    def complete(self, system, user, json_schema=None):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        started = time.time()
        try:
            r = requests.post(self.base_url, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            body = getattr(getattr(e, "response", None), "text", "")
            raise LLMError(f"chat request failed: {e} {body[:200]}") from e
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"no choices in response: {json.dumps(data)[:200]}")
        text = choices[0].get("message", {}).get("content") or ""
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            latency_ms=int((time.time() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw=data,
        )


def build_client(cfg):
    backend = (cfg.llm_backend or "ollama").lower()
    if backend == "ollama":
        return OllamaClient(
            model=cfg.ollama_model,
            host=cfg.ollama_host,
            temperature=cfg.llm_temperature,
            timeout=cfg.llm_timeout_s,
        )
    if backend == "deepseek":
        from ..config import get_env

        return OpenAICompatClient(
            model=cfg.ollama_model if cfg.ollama_model.startswith("deepseek") else "deepseek-chat",
            base_url="https://api.deepseek.com/v1/chat/completions",
            api_key=get_env("DEEPSEEK_API_KEY"),
            temperature=cfg.llm_temperature,
            timeout=cfg.llm_timeout_s,
        )
    if backend == "opencode-go":
        from ..config import get_env

        return OpenAICompatClient(
            model=cfg.ollama_model,
            base_url="https://opencode.ai/zen/go/v1/chat/completions",
            api_key=get_env("OPENCODE_API_KEY"),
            temperature=cfg.llm_temperature,
            timeout=cfg.llm_timeout_s,
            extra_headers={
                "x-opencode-session": get_env("OPENCODE_SESSION", "llm-trader"),
                "User-Agent": "llm-trader/0.1",
            },
        )
    raise LLMError(f"unknown llm_backend: {backend}")
