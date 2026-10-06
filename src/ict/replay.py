"""Record and replay every model call, so a regression run needs no network and no luck.

The model is the only non-deterministic part of this pipeline: two live runs of the *same* code
disagree on step-1 fields, table roles, gate verdicts and merge deltas. That makes "did my change
alter the result?" unanswerable against a live endpoint. Freezing the model boundary turns the
question into a byte comparison:

    record   one live pass writes every request/response pair into a cassette
    replay   the same requests are answered from the cassette; a request the cassette has never
             seen fails loudly, because it means an input changed

A miss is also the cross-talk detector. With the model frozen, the only way an earlier step can
end up sending a different request is if data from a later step leaked back into it.

This is an offline affordance only. The mode is read from the process environment, `.env` cannot
turn it on, the default is `live`, and both other modes announce themselves on stderr.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from ict.concurrency import LLM_GATE
from ict.config import REPO_ROOT
from ict.llm import LLMClient, LLMResult, load_prompt

DEFAULT_CASSETTE = REPO_ROOT / "work" / "cassettes" / "llm.jsonl"
MODES = ("live", "record", "replay")

_cache_lock = threading.Lock()
_cache: dict[str, dict[str, dict]] = {}
_misses: list[dict] = []
_announced: set[str] = set()


class CassetteMiss(RuntimeError):
    """The pipeline asked something the cassette has never seen."""


def mode() -> str:
    """live | record | replay, from the process environment only."""
    value = (os.environ.get("ICT_LLM_MODE") or "live").strip().lower()
    return value if value in MODES else "live"


def cassette_path() -> Path:
    return Path(os.environ.get("ICT_CASSETTE") or DEFAULT_CASSETTE)


def simulated_latency_ms() -> int:
    """How long a replayed call pretends to take.

    Replay is instant, which makes it useless for measuring a speed-up. Setting this makes the
    cassette behave like a slow endpoint, inside the same gate, so a concurrency change can be
    timed offline and for free.
    """
    try:
        return max(0, int(os.environ.get("ICT_REPLAY_LATENCY_MS", "0")))
    except ValueError:
        return 0


def announce(state: str, path: Path) -> None:
    """Say out loud what the model layer is doing. Silence means a real endpoint."""
    key = state + "|" + str(path)
    if state == "live" or key in _announced:
        return
    _announced.add(key)
    detail = "no network" if state == "replay" else "network in use"
    print("[ict] LLM mode=%s cassette=%s (%s)" % (state, path, detail), file=sys.stderr, flush=True)


def request_key(step: str, prompt_version: str, user: str, thinking: bool = False,
                images: list[str] | None = None) -> str:
    """A content address for one model call.

    The system prompt is part of it, so editing a prompt file invalidates its recorded answers
    instead of silently replaying the old ones.
    """
    digest = hashlib.sha256()
    digest.update(load_prompt(prompt_version).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(json.dumps({"step": step, "prompt_version": prompt_version,
                              "thinking": bool(thinking), "user": user},
                             ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for image in images or []:
        digest.update(b"\x00")
        digest.update(hashlib.sha256(image.encode("utf-8")).digest())
    return digest.hexdigest()


def load_cassette(path: Path) -> dict[str, dict]:
    """Every recorded answer for this cassette, keyed by request. First recording wins."""
    name = str(Path(path))
    cached = _cache.get(name)
    if cached is not None:
        return cached
    with _cache_lock:
        cached = _cache.get(name)
        if cached is None:
            cached = {}
            file = Path(path)
            if file.exists():
                for line in file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    cached.setdefault(item["key"], item)
            _cache[name] = cached
    return cached


def record_count(path: Path | None = None) -> int:
    """How many answers the cassette holds on disk right now (never the cached copy)."""
    file = Path(path or cassette_path())
    if not file.exists():
        return 0
    return sum(1 for line in file.read_text(encoding="utf-8").splitlines() if line.strip())


def misses() -> list[dict]:
    """Everything a replay run was asked for and could not find."""
    return list(_misses)


def clear_misses() -> None:
    _misses.clear()


class RecordingLLM(LLMClient):
    """Pass every call through to the real client and write the answer to the cassette."""

    def __init__(self, inner: LLMClient, path: Path) -> None:
        self.inner = inner
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        announce("record", self.path)

    def _write(self, key: str, step: str, prompt_version: str, kind: str, result: LLMResult) -> None:
        line = {"key": key, "kind": kind, "step": step, "prompt_version": prompt_version,
                "text": result.text, "reasoning": result.reasoning,
                "model_name": result.model_name, "model_version": result.model_version,
                "latency_ms": result.latency_ms}
        payload = json.dumps(line, ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)

    def complete(self, *, step: str, prompt_version: str, user: str,
                 thinking: bool = False) -> LLMResult:
        result = self.inner.complete(step=step, prompt_version=prompt_version, user=user,
                                     thinking=thinking)
        self._write(request_key(step, prompt_version, user, thinking), step, prompt_version,
                    "chat", result)
        return result

    def complete_with_images(self, *, step: str, prompt_version: str, images: list[str],
                             user_text: str) -> LLMResult:
        result = self.inner.complete_with_images(step=step, prompt_version=prompt_version,
                                                 images=images, user_text=user_text)
        self._write(request_key(step, prompt_version, user_text, False, images), step,
                    prompt_version, "vision", result)
        return result


class ReplayLLM(LLMClient):
    """Answer from the cassette. A request that was never recorded is an error, never a guess."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        announce("replay", self.path)

    def _lookup(self, key: str, step: str, prompt_version: str) -> LLMResult:
        entries = load_cassette(self.path)
        item = entries.get(key)
        if item is None:
            _misses.append({"step": step, "prompt_version": prompt_version, "key": key[:12],
                            "cassette": str(self.path), "recorded": len(entries)})
            raise CassetteMiss("回放表里没有 %s (%s) key=%s，说明这一步的输入变了"
                               % (step, prompt_version, key[:12]))
        latency = simulated_latency_ms()
        if latency:
            # Inside the gate, like the real call: the endpoint's concurrency limit is part of
            # what is being simulated.
            with LLM_GATE:
                time.sleep(latency / 1000.0)
        return LLMResult(item.get("text") or "", item.get("model_name") or "",
                         item.get("model_version") or "", prompt_version, 0,
                         item.get("reasoning") or "")

    def complete(self, *, step: str, prompt_version: str, user: str,
                 thinking: bool = False) -> LLMResult:
        return self._lookup(request_key(step, prompt_version, user, thinking), step, prompt_version)

    def complete_with_images(self, *, step: str, prompt_version: str, images: list[str],
                             user_text: str) -> LLMResult:
        return self._lookup(request_key(step, prompt_version, user_text, False, images), step,
                            prompt_version)
