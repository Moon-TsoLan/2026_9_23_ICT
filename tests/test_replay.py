"""Offline guards for the cassette layer. No network, no model, no work tree."""

from __future__ import annotations

import os

import pytest

from ict import replay
from ict import config
from ict.llm import LLMClient, LLMResult


class FakeInner(LLMClient):
    """Stands in for the real endpoint: a fixed answer, plus a count of how often it was asked."""

    def __init__(self, text: str = '{"ok": true}', reasoning: str = "") -> None:
        self.text = text
        self.reasoning = reasoning
        self.calls = 0

    def complete(self, *, step, prompt_version, user, thinking=False) -> LLMResult:
        self.calls += 1
        return LLMResult(self.text, "fake", "fake", prompt_version, 7, self.reasoning)

    def complete_with_images(self, *, step, prompt_version, images, user_text) -> LLMResult:
        self.calls += 1
        return LLMResult(self.text, "fake", "fake", prompt_version, 7, self.reasoning)


@pytest.fixture(autouse=True)
def _clean():
    replay.clear_misses()
    replay._cache.clear()
    yield
    replay.clear_misses()
    replay._cache.clear()


def _record(tmp_path, **kwargs) -> "replay.RecordingLLM":
    recorder = replay.RecordingLLM(FakeInner(**kwargs), tmp_path / "llm.jsonl")
    recorder.complete(step="screen_files", prompt_version="screen-file-v1", user='{"a": 1}')
    return recorder


def test_a_recorded_answer_replays_unchanged(tmp_path):
    recorder = _record(tmp_path)
    inner = FakeInner()
    recorder.complete(step="screen_files", prompt_version="screen-file-v1", user='{"a": 2}')

    replayed = replay.ReplayLLM(tmp_path / "llm.jsonl")
    result = replayed.complete(step="screen_files", prompt_version="screen-file-v1", user='{"a": 1}')
    assert result.text == '{"ok": true}'
    assert result.prompt_version == "screen-file-v1"
    assert inner.calls == 0
    assert replay.misses() == []


def test_reasoning_is_replayed_too(tmp_path):
    """The merge step's thinking excerpt ends up in merge_notes, so it must survive the cassette."""
    recorder = replay.RecordingLLM(FakeInner(reasoning="因为两行名称相同"), tmp_path / "llm.jsonl")
    recorder.complete(step="merge_candidates", prompt_version="merge-objects-v2", user="{}",
                      thinking=True)
    replayed = replay.ReplayLLM(tmp_path / "llm.jsonl")
    result = replayed.complete(step="merge_candidates", prompt_version="merge-objects-v2", user="{}",
                               thinking=True)
    assert result.reasoning == "因为两行名称相同"


def test_an_unrecorded_request_fails_loudly(tmp_path):
    _record(tmp_path)
    replayed = replay.ReplayLLM(tmp_path / "llm.jsonl")
    with pytest.raises(replay.CassetteMiss):
        replayed.complete(step="screen_files", prompt_version="screen-file-v1", user='{"a": 9}')
    assert [item["step"] for item in replay.misses()] == ["screen_files"]


def test_the_key_follows_every_input_that_changes_the_answer():
    base = replay.request_key("screen_files", "screen-file-v1", "{}", False)
    assert base != replay.request_key("screen_files", "screen-file-v1", '{"a": 1}', False)
    assert base != replay.request_key("screen_files", "screen-file-v1", "{}", True)
    assert base != replay.request_key("locate_pages", "screen-file-v1", "{}", False)
    assert base != replay.request_key("screen_files", "announcement-v1", "{}", False)
    assert base != replay.request_key("screen_images", "screen-file-v1", "{}", False, ["data:x"])
    assert base == replay.request_key("screen_files", "screen-file-v1", "{}", False)


def test_vision_calls_replay_by_image_content(tmp_path):
    recorder = replay.RecordingLLM(FakeInner(text='{"kind": "other"}'), tmp_path / "llm.jsonl")
    recorder.complete_with_images(step="screen_images", prompt_version="screen-page-v1",
                                  images=["data:image/png;base64,AA=="], user_text="{}")
    replayed = replay.ReplayLLM(tmp_path / "llm.jsonl")
    assert replayed.complete_with_images(step="screen_images", prompt_version="screen-page-v1",
                                         images=["data:image/png;base64,AA=="],
                                         user_text="{}").text == '{"kind": "other"}'
    with pytest.raises(replay.CassetteMiss):
        replayed.complete_with_images(step="screen_images", prompt_version="screen-page-v1",
                                      images=["data:image/png;base64,BB=="], user_text="{}")


def test_live_mode_is_the_default(monkeypatch):
    monkeypatch.delenv("ICT_LLM_MODE", raising=False)
    assert replay.mode() == "live"
    monkeypatch.setenv("ICT_LLM_MODE", "REPLAY")
    assert replay.mode() == "replay"
    monkeypatch.setenv("ICT_LLM_MODE", "nonsense")
    assert replay.mode() == "live"


def test_the_env_file_cannot_switch_on_replay(monkeypatch, tmp_path):
    """A stale line left in `.env` must not be able to fake every model answer."""
    (tmp_path / ".env").write_text(
        "ICT_LLM_MODE=replay\nICT_CASSETTE=/tmp/stale.jsonl\nICT_REPLAY_LATENCY_MS=500\n"
        "ICT_LLM_BASE_URL=https://example.invalid\n", encoding="utf-8")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    for key in ("ICT_LLM_MODE", "ICT_CASSETTE", "ICT_REPLAY_LATENCY_MS", "ICT_LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    config.load_local_env()
    assert "ICT_LLM_MODE" not in os.environ
    assert "ICT_CASSETTE" not in os.environ
    assert "ICT_REPLAY_LATENCY_MS" not in os.environ
    assert os.environ["ICT_LLM_BASE_URL"] == "https://example.invalid"
