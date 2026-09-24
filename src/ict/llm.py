"""OpenAI-compatible chat client. Callers pass only the current step's JSON."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ict.config import llm_settings

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class LLMResult:
    text: str
    model_name: str
    model_version: str
    prompt_version: str
    latency_ms: int


class LLMError(RuntimeError):
    pass


class LLMClient:
    def complete(self, *, step: str, prompt_version: str, user: str) -> LLMResult:
        raise NotImplementedError


class OpenAICompatibleClient(LLMClient):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 180) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, *, step: str, prompt_version: str, user: str) -> LLMResult:
        system = (PROMPT_DIR / f"{prompt_version}.md").read_text(encoding="utf-8")
        started = time.perf_counter()
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
            },
            timeout=self.timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise LLMError(f"{step} http {response.status_code}: {response.text[:400]}")
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        return LLMResult(text, self.model, self.model, prompt_version, latency_ms)


class FakeLLMClient(LLMClient):
    def __init__(self, responses: dict[str, str | list[str]]) -> None:
        self.responses = responses
        self.used: dict[str, int] = {}

    def complete(self, *, step: str, prompt_version: str, user: str) -> LLMResult:
        if step not in self.responses:
            raise LLMError(f"no fake response for {step}")
        payload = self.responses[step]
        if isinstance(payload, list):
            index = self.used.get(step, 0)
            text = payload[min(index, len(payload) - 1)]
            self.used[step] = index + 1
        else:
            text = payload
        return LLMResult(text, "fake", "test", prompt_version, 1)


def build_client() -> LLMClient | None:
    settings = llm_settings()
    if not settings["base_url"] or not settings["api_key"] or not settings["model"]:
        return None
    return OpenAICompatibleClient(settings["base_url"], settings["api_key"], settings["model"])


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output has no JSON object")
    return json.loads(cleaned[start : end + 1])
