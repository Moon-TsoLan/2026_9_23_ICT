"""The GPU queue must take turns between announcements and still run every job."""

from __future__ import annotations

import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from ict.concurrency import ParseQueue
from ict.schemas import AttachmentIndex, FileDecision, FileDecisions, IndexedFile
from ict.steps import s04_attach


def test_a_small_announcement_is_not_stuck_behind_a_big_one():
    """Ten files for A and one for B: B must not be dispatched after all ten of A.

    The first job blocks, so it is guaranteed to be in flight while everything else is submitted -
    that makes the dispatch order deterministic instead of a race between submission and dispatch.
    """
    release = threading.Event()
    running = threading.Event()
    order: list[str] = []
    queue = ParseQueue(workers=1)

    def job(name: str, block: bool = False):
        order.append(name)
        if block:
            running.set()
            release.wait(timeout=5)

    queue.submit("lead", job, "L1", True)
    assert running.wait(timeout=5), "the first job never started"
    for index in range(1, 11):
        queue.submit("A", job, "A%d" % index)
    last = queue.submit("B", job, "B1")
    release.set()

    assert last.result(timeout=10) is None
    assert order[0] == "L1"
    # A plain FIFO puts B1 at the very end (index 11). Rotation puts it right after A's first file.
    assert order.index("B1") <= 3, order
    assert order[-1] == "A10"
    queue.shutdown()


def test_each_owner_keeps_its_own_order():
    order: list[str] = []
    queue = ParseQueue(workers=1)

    def job(name: str):
        order.append(name)

    futures = [queue.submit("A", job, "A%d" % index) for index in range(1, 4)]
    futures += [queue.submit("B", job, "B%d" % index) for index in range(1, 3)]
    for future in futures:
        future.result(timeout=10)
    assert [name for name in order if name.startswith("A")] == ["A1", "A2", "A3"]
    assert [name for name in order if name.startswith("B")] == ["B1", "B2"]
    queue.shutdown()


def test_a_failing_job_reaches_its_own_future_only():
    queue = ParseQueue(workers=1)

    def boom():
        raise ValueError("endpoint said no")

    bad = queue.submit("A", boom)
    good = queue.submit("A", lambda: "fine")
    with pytest.raises(ValueError):
        bad.result(timeout=10)
    assert good.result(timeout=10) == "fine"
    queue.shutdown()


def test_two_inflight_workers_really_run_two_documents():
    """`ICT_PARSE_MAX_INFLIGHT=2` must mean two at once, or the experiment measures nothing."""
    barrier = threading.Barrier(2, timeout=5)
    queue = ParseQueue(workers=2)

    def job():
        barrier.wait()
        return "ok"

    first, second = queue.submit("A", job), queue.submit("B", job)
    assert first.result(timeout=10) == "ok"
    assert second.result(timeout=10) == "ok"
    queue.shutdown()


def test_parse_pages_hands_the_announcement_to_the_queue(monkeypatch, tmp_path):
    """Rotation keys on the owner, so step 4c has to pass one. Without it, rotation is a no-op."""
    document = tmp_path / "a.pdf"
    document.write_bytes(b"%PDF-1.4\n")
    owners: list[str] = []

    def fake_submit(owner, func, *args, **kwargs):
        owners.append(owner)
        future: Future = Future()
        future.set_result({"pages": [{"page_no": 1, "markdown": ""}], "meta": {}})
        return future

    monkeypatch.setattr(s04_attach.PARSE_QUEUE, "submit", fake_submit)
    # `client.parse` is read as the argument to submit(), so the stub needs the attribute.
    monkeypatch.setattr(s04_attach, "_client", lambda: SimpleNamespace(parse=lambda *a, **k: None))
    index = AttachmentIndex(announcement_id="demo", attachment_directory=str(tmp_path), files=[
        IndexedFile(file_id="a001", display_name="a.pdf", relative_path="a.pdf", extension=".pdf",
                    readability="text_extractable", fmt="image", page_count=1)])
    files = FileDecisions(run_id="run_demo", status="success", file_decisions=[
        FileDecision(file_id="a001", file_class="bid_quote", priority=90.0,
                     read_strategy="target_pages", reason="test")])
    s04_attach.parse_pages("run_demo", files, index, [])
    assert owners == ["run_demo"]
