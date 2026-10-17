"""OpenAI-compatible chat client. Callers pass only the current step's JSON."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tenacity import (Retrying, retry_if_exception_type, stop_after_attempt,
                      wait_exponential_jitter, wait_fixed)

from ict.concurrency import LLM_GATE, bump_count
from ict.config import concurrency_settings, llm_settings
from ict.http import shared_client

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# Throttling and server-side blips are worth another try; every other status is a real answer and
# must fail immediately, exactly as it did before there was a retry at all.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class LLMRetryable(RuntimeError):
    """A failure the transport may retry. Never raised for an answer the model actually produced."""


def retrying() -> Retrying:
    """One retry policy for every chat call, read from the settings at call time."""
    settings = concurrency_settings()
    wait = float(settings["llm_retry_wait"])
    return Retrying(
        stop=stop_after_attempt(max(1, int(settings["llm_retry_attempts"]))),
        # A wait of 0 means "retry immediately", which is what the offline tests want.
        wait=wait_exponential_jitter(initial=wait, max=20.0) if wait > 0 else wait_fixed(0),
        retry=retry_if_exception_type((LLMRetryable, httpx.TransportError)),
        reraise=True,
    )


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
    usage: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


@dataclass
class ValidationIssue:
    message: str
    category: str = "schema"
    path: str = "$"

    def as_dict(self):
        return {"category": self.category, "path": self.path, "message": self.message}


class LLMValidationError(ValueError):
    def __init__(self, issues, attempts):
        self.issues, self.attempts = issues, attempts
        super().__init__("; ".join(item.message for item in issues))


class LLMClient:
    def complete(self, *, step: str, prompt_version: str, user: str,
                 thinking: bool = False) -> LLMResult:
        raise NotImplementedError

    def complete_with_images(self, *, step: str, prompt_version: str, images: list[str],
                             user_text: str) -> LLMResult:
        """Optional vision call; models without image input simply do not implement it."""
        raise NotImplementedError


class OpenAICompatibleClient(LLMClient):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 180,
                 connect_timeout: float = 15) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.connect_timeout = connect_timeout

    def _send_once(self, step: str, request: dict) -> httpx.Response:
        """One attempt: hold the process-wide gate, post, and say whether it is worth retrying."""
        with LLM_GATE:
            response = shared_client("llm").post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=request,
                timeout=httpx.Timeout(self.timeout, connect=self.connect_timeout),
            )
        if response.status_code in RETRYABLE_STATUS:
            raise LLMRetryable(f"{step} http {response.status_code}: {response.text[:200]}")
        return response

    def _post(self, step: str, request: dict) -> httpx.Response:
        return retrying()(self._send_once, step, request)

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
        response = self._post(step, request)
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
        return LLMResult(text, self.model, self.model, prompt_version, latency_ms, reasoning, payload.get("usage") or {})

    def complete_with_images(self, *, step: str, prompt_version: str, images: list[str],
                             user_text: str) -> LLMResult:
        """One user turn of page pictures plus the instruction. No conversation state."""
        system = load_prompt(prompt_version)
        content: list[dict] = [{"type": "text", "text": user_text}]
        content += [{"type": "image_url", "image_url": {"url": uri}} for uri in images]
        started = time.perf_counter()
        response = self._post(step, {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 300,
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        })
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise LLMError(f"{step} http {response.status_code}: {response.text[:300]}")
        payload = response.json()
        produced = payload["choices"][0]["message"]["content"]
        return LLMResult(produced, self.model, self.model, prompt_version, latency_ms, usage=payload.get("usage") or {})


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
    from ict.replay import RecordingLLM, ReplayLLM, cassette_path, mode

    state = mode()
    if state == "replay":
        # The answers are already on disk: no endpoint, no key, no network.
        return ReplayLLM(cassette_path())
    settings = llm_settings()
    if not settings["base_url"] or not settings["api_key"] or not settings["model"]:
        return None
    client = OpenAICompatibleClient(settings["base_url"], settings["api_key"], settings["model"],
                                    timeout=float(settings["timeout"]),
                                    connect_timeout=float(settings["connect_timeout"]))
    return RecordingLLM(client, cassette_path()) if state == "record" else client


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
    receiver=None,
) -> dict:
    """Repair representation/plan errors once; a receiver may retain independent valid items.

    Dependent merge plans never use a receiver. Business uncertainty must be returned as audit,
    not represented by a validation exception. An unexpected validator exception is an
    implementation error and is never sent back to the model as its own mistake.
    """
    payload = user
    attempts, issues = [], []
    for attempt in range(2):
        try:
            result = llm.complete(step=step, prompt_version=prompt_version, user=payload,
                                  thinking=thinking)
        except LLMError as exc:
            if receiver is None or not receiver.received:
                raise
            attempts.append({"attempt": attempt + 1, "issues": [ValidationIssue(str(exc), "transport").as_dict()]})
            return receiver.result(attempts)
        if notes is not None:
            notes["reasoning"] = (getattr(result, "reasoning", "") or "")[:1200]
            notes.setdefault("calls", []).append({"attempt": attempt + 1,
                "latency_ms": result.latency_ms, "usage": getattr(result, "usage", {}),
                "prompt_version": prompt_version, "model_name": result.model_name})
        if counter is not None:
            bump_count(counter, step)
        try:
            parsed = parse_json_object(result.text)
        except (ValueError, json.JSONDecodeError) as exc:
            issues = [ValidationIssue(str(exc))]
        else:
            try:
                checked = validate(parsed)
            except Exception as exc:
                issues = [ValidationIssue(str(exc), "implementation")]
                raise LLMValidationError(issues, attempts) from exc
            issues = ([checked] if isinstance(checked, ValidationIssue) else
                      [ValidationIssue(checked)] if checked else [])
            if not issues and receiver is not None:
                issues = receiver.accept(parsed, attempt)
            if not issues:
                attempts.append({"attempt": attempt + 1, "issues": []})
                if notes is not None:
                    notes["attempts"] = attempts
                return receiver.result(attempts) if receiver is not None else parsed
        attempts.append({"attempt": attempt + 1, "issues": [item.as_dict() for item in issues]})
        if notes is not None:
            notes["attempts"] = attempts
        if any(item.category not in {"schema", "plan_constraint"} for item in issues):
            raise LLMValidationError(issues, attempts)
        if attempt == 0:
            repair = {"previous_response": result.text,
                      "validation_issues": [item.as_dict() for item in issues]}
            if receiver is not None:
                repair["accepted_independent_outputs"] = receiver.result(attempts)
            payload = (user + "\n\n协议修复：以下是上次回答与具体错误。保留合法信息，只修复列出的错误；"
                       "带依赖的计划须返回完整合法计划。独立通道已接收的条目由程序保留，不必重复。\n" +
                       json.dumps(repair, ensure_ascii=False))
    if receiver is not None and receiver.received:
        return receiver.result(attempts)
    raise LLMValidationError(issues, attempts)
