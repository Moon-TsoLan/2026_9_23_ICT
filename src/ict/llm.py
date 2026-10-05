"""OpenAI-compatible chat client. Callers pass only the current step's JSON."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import httpx

from ict.config import llm_settings

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(prompt_version: str) -> str:
    """Prompt text plus the field definitions this step sends and expects."""
    from ict.fields import field_block
    base = (PROMPT_DIR / f"{prompt_version}.md").read_text(encoding="utf-8").rstrip()
    block = field_block(prompt_version)
    return base + (("\n\n" + block + "\n") if block else "\n")


@dataclass
class LLMResult:
    text: str
    model_name: str
    model_version: str
    prompt_version: str
    latency_ms: int
    # Kept only where thinking is switched on, for the audit trail. No rule reads it.
    reasoning: str = ""


class LLMError(RuntimeError):
    pass


class LLMClient:
    def complete(self, *, step: str, prompt_version: str, user: str,
                 thinking: bool = False) -> LLMResult:
        raise NotImplementedError

    def complete_with_images(self, *, step: str, prompt_version: str, images: list[str],
                             user_text: str) -> LLMResult:
        """Optional vision call; models without image input simply do not implement it."""
        raise NotImplementedError


class OpenAICompatibleClient(LLMClient):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 180) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, *, step: str, prompt_version: str, user: str,
                 thinking: bool = False) -> LLMResult:
        system = load_prompt(prompt_version)
        started = time.perf_counter()
        request: dict = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if thinking:
            # Thinking and a forced json_object response do not mix reliably on this endpoint, so
            # the JSON is read out of the text instead: parse_json_object already strips fences and
            # prose. Every other step keeps the strict shape it always had.
            request["thinking"] = {"type": "enabled"}
        else:
            request["thinking"] = {"type": "disabled"}
            request["response_format"] = {"type": "json_object"}
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=request,
            timeout=self.timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise LLMError(f"{step} http {response.status_code}: {response.text[:400]}")
        payload = response.json()
        message = payload["choices"][0]["message"]
        text = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        if thinking and "{" not in text and "{" in reasoning:
            # Some reasoning endpoints leave the answer in the chain-of-thought field. Taking it
            # only when the visible text holds no JSON at all keeps a real answer untouched and
            # avoids the whole package silently falling back to the baseline.
            text = reasoning
        return LLMResult(text, self.model, self.model, prompt_version, latency_ms, reasoning)

    def complete_with_images(self, *, step: str, prompt_version: str, images: list[str],
                             user_text: str) -> LLMResult:
        """One user turn of page pictures plus the instruction. No conversation state."""
        system = load_prompt(prompt_version)
        content: list[dict] = [{"type": "text", "text": user_text}]
        content += [{"type": "image_url", "image_url": {"url": uri}} for uri in images]
        started = time.perf_counter()
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "max_tokens": 300,
                "thinking": {"type": "disabled"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            },
            timeout=self.timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise LLMError(f"{step} http {response.status_code}: {response.text[:300]}")
        payload = response.json()
        produced = payload["choices"][0]["message"]["content"]
        return LLMResult(produced, self.model, self.model, prompt_version, latency_ms)


class FakeLLMClient(LLMClient):
    def __init__(self, responses: dict[str, str | list[str]]) -> None:
        self.responses = responses
        self.used: dict[str, int] = {}

    def complete(self, *, step: str, prompt_version: str, user: str,
                 thinking: bool = False) -> LLMResult:
        if step not in self.responses:
            raise LLMError(f"no fake response for {step}")
        self.thinking_requested = thinking
        payload = self.responses[step]
        if isinstance(payload, list):
            index = self.used.get(step, 0)
            text = payload[min(index, len(payload) - 1)]
            self.used[step] = index + 1
        else:
            text = payload
        return LLMResult(text, "fake", "test", prompt_version, 1)

    def complete_with_images(self, *, step: str, prompt_version: str, images: list[str],
                             user_text: str) -> LLMResult:
        return self.complete(step=step, prompt_version=prompt_version, user=user_text)


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


def complete_json(
    llm: LLMClient,
    *,
    step: str,
    prompt_version: str,
    user: str,
    validate,
    counter: Counter | None = None,
    thinking: bool = False,
    notes: dict | None = None,
) -> dict:
    """Call the model and, if the JSON or enum check fails, retry once with the error."""
    payload = user
    last_error = "model output has no JSON object"
    parsed: dict | None = None
    for attempt in range(2):
        result = llm.complete(step=step, prompt_version=prompt_version, user=payload,
                              thinking=thinking)
        if notes is not None:
            notes["reasoning"] = (getattr(result, "reasoning", "") or "")[:1200]
        if counter is not None:
            counter[step] += 1
        try:
            parsed = parse_json_object(result.text)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            parsed = None
        else:
            last_error = validate(parsed) or ""
            if not last_error:
                return parsed
        if attempt == 0:
            payload = f"{user}\n\n上一次输出不合法：{last_error}。请只输出符合枚举的 JSON。"
    raise ValueError(last_error)
