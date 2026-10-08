"""Stage 0 guards for the transport layer: one shared pool, a bounded retry, an atomic counter.

Nothing here touches the network or a model. The point is that replacing `httpx.post` with a
shared `httpx.Client` plus a retry policy did not change what any caller sees.
"""

from __future__ import annotations

import json
import threading
from collections import Counter

import httpx
import pytest

from ict import http as http_module
from ict.concurrency import CallCounter, bump_count, counts_snapshot
from ict.llm import LLMError, LLMRetryable, OpenAICompatibleClient
from ict.parse.client import ParseClient

OK_BODY = {"choices": [{"message": {"content": '{"ok": true}'}}]}


@pytest.fixture(autouse=True)
def _isolated_pool():
    """No test shares the real pool, and none leaves a client behind."""
    for client in http_module._clients.values():
        client.close()
    http_module._clients.clear()
    yield
    for client in http_module._clients.values():
        client.close()
    http_module._clients.clear()


def _client(handler, timeout: float = 5) -> OpenAICompatibleClient:
    http_module._clients["llm"] = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleClient("https://example.invalid", "key", "model", timeout=timeout)


def _complete(client, monkeypatch, *, attempts: str = "3", wait: str = "0"):
    """A real `complete()` call, with retries made instant so the tests stay fast."""
    monkeypatch.setenv("ICT_LLM_RETRY_ATTEMPTS", attempts)
    monkeypatch.setenv("ICT_LLM_RETRY_WAIT", wait)
    return client.complete(step="test", prompt_version="announcement-v1", user="{}")


def test_shared_client_is_reused_per_role():
    assert http_module.shared_client("llm") is http_module.shared_client("llm")
    assert http_module.shared_client("llm") is not http_module.shared_client("parse")


def test_chat_call_goes_through_the_shared_pool(monkeypatch):
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=OK_BODY)

    result = _complete(_client(handler), monkeypatch)
    assert seen == ["/chat/completions"]
    assert result.text == '{"ok": true}'


def test_timeout_is_still_per_request(monkeypatch):
    seen: dict = {}

    def handler(request):
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=OK_BODY)

    _complete(_client(handler, timeout=42), monkeypatch)
    # Reading may wait; connecting must not. A dead endpoint is a different failure from a slow one.
    assert seen["timeout"]["read"] == 42.0
    assert seen["timeout"]["connect"] == 15.0


def test_parse_connect_timeout_is_not_the_read_timeout(tmp_path):
    """An unreachable GPU box must not hold the single-document queue for ten minutes per file."""
    seen: dict = {}

    def handler(request):
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"ok": True, "pages": [], "tables": [], "meta": {}})

    http_module._clients["parse"] = httpx.Client(transport=httpx.MockTransport(handler))
    document = tmp_path / "a.pdf"
    document.write_bytes(b"%PDF-1.4\n")
    ParseClient("https://example.invalid", "", timeout=600, connect_timeout=10).parse(document)
    assert seen["timeout"]["read"] == 600.0
    assert seen["timeout"]["connect"] == 10.0


def test_vision_call_uses_the_same_pool(monkeypatch):
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"kind": "other"}'}}]})

    monkeypatch.setenv("ICT_LLM_RETRY_WAIT", "0")
    result = _client(handler).complete_with_images(step="test", prompt_version="screen-page-v1",
                                                   images=["data:image/png;base64,AA=="], user_text="{}")
    assert seen == ["/chat/completions"]
    assert result.text == '{"kind": "other"}'


def _capture(monkeypatch, call) -> dict:
    """Run one call against a mock transport and hand back the request it produced."""
    seen: dict = {}

    def handler(request):
        seen.update(method=request.method, path=request.url.path,
                    auth=request.headers.get("authorization"), body=json.loads(request.content))
        return httpx.Response(200, json=OK_BODY)

    monkeypatch.setenv("ICT_LLM_RETRY_WAIT", "0")
    call(_client(handler))
    return seen


def test_chat_request_shape_is_pinned(monkeypatch):
    """The chat call is byte-for-byte what it was before the shared pool: same URL, same body."""
    seen = _capture(monkeypatch, lambda c: c.complete(step="test",
                                                      prompt_version="announcement-v1", user="{}"))
    assert seen["method"] == "POST"
    assert seen["path"] == "/chat/completions"
    assert seen["auth"] == "Bearer key"
    body = seen["body"]
    assert body["model"] == "model"
    assert body["temperature"] == 0
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "{}"


def test_thinking_request_still_drops_forced_json(monkeypatch):
    seen = _capture(monkeypatch, lambda c: c.complete(step="test", prompt_version="merge-objects-v3",
                                                      user="{}", thinking=True))
    body = seen["body"]
    assert body["thinking"] == {"type": "enabled"}
    assert "response_format" not in body


def test_vision_request_shape_is_pinned(monkeypatch):
    seen = _capture(monkeypatch, lambda c: c.complete_with_images(
        step="test", prompt_version="screen-page-v1", images=["data:image/png;base64,AA=="],
        user_text="{}"))
    body = seen["body"]
    assert body["max_tokens"] == 300
    content = body["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}


def test_throttling_is_retried(monkeypatch):
    attempts: list = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=OK_BODY)

    result = _complete(_client(handler), monkeypatch)
    assert len(attempts) == 2
    assert result.text == '{"ok": true}'


def test_transport_error_is_retried(monkeypatch):
    attempts: list = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=OK_BODY)

    result = _complete(_client(handler), monkeypatch)
    assert len(attempts) == 2
    assert result.text == '{"ok": true}'


def test_bad_credentials_are_not_retried(monkeypatch):
    attempts: list = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(LLMError):
        _complete(_client(handler), monkeypatch)
    assert len(attempts) == 1


def test_retries_are_bounded(monkeypatch):
    attempts: list = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(503, text="nope")

    with pytest.raises(LLMRetryable):
        _complete(_client(handler), monkeypatch, attempts="3")
    assert len(attempts) == 3


def test_call_counter_survives_concurrent_increments():
    counter = CallCounter()

    def work():
        for _ in range(500):
            bump_count(counter, "screen_files")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert counter["screen_files"] == 4000
    assert counts_snapshot(counter) == {"screen_files": 4000}


def test_plain_counter_still_works():
    counter: Counter = Counter()
    bump_count(counter, "locate_pages")
    bump_count(counter, "locate_pages")
    assert counts_snapshot(counter) == {"locate_pages": 2}
