"""Offline protocol and flow regressions. No real model, OCR or database endpoints."""
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from ict.cancellation import Cancellation
from ict.concurrency import ParseQueue
from ict.documents import read_native
from ict.fields import STEP_FIELDS
from ict.index.build import build_index
from ict.llm import LLMResult, complete_json
from ict.parse.census import FileEntry, sniff
from ict.parse.client import ParseClient, ParseError
from ict.parse.peek import peek_entry
from ict.receiving import ExtractionReceiver
from ict.schemas import (AnnouncementUnderstanding, CandidateEvidence, FileDecision, FileDecisions,
                         PackageUnderstanding, ProjectPlan, ProjectPlans)
from ict.steps import s04_attach
from ict.steps.s03_plan import plan_projects
from test_rules import _cob, _sub


class Fixed:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.inputs = []

    def complete(self, *, step, prompt_version, user, **kwargs):
        self.inputs.append((step, prompt_version, user))
        answer = self.answers[min(len(self.inputs) - 1, len(self.answers) - 1)]
        return LLMResult(json.dumps(answer), "fixed", "fixed", prompt_version, 0)


def plans(*needs):
    return ProjectPlans(run_id="r", status="success", projects=[ProjectPlan(project_id="项目|1", package_no="1",
        needs=list(needs), has_attachment=True, needs_attachment=True, status="success")])


def decisions():
    return FileDecisions(run_id="r", status="success", file_decisions=[FileDecision(file_id="a", file_class="bid_quote",
        priority=1, read_strategy="target_pages", reason="screen", expected_fields=["brand"],
        evidence_refs=["file:a:native:p1"], native_segments=[{"source_ref": "file:a:native:p1", "text": "设备 品牌"}],
        screen={"requires_deep_parse": False})])


def source():
    item = _cob("native", "pdf", 20, {"object_name": "设备", "brand": "品牌"})
    item.evidence = CandidateEvidence(source_refs=["file:a:native:p1"])
    return item


def finish(ref="file:a:native:p1"):
    return {"continuation": "finish", "needs_hit_ids": [], "evidence_refs": [ref],
            "kind": "native_continuation", "confidence": 0.1, "reason": "本文件所需证据已取得，无需后文"}


def test_retired_table_and_location_protocols_leave_active_registry():
    assert 'html-tables-v6' not in STEP_FIELDS and 'locate-pages-v5' not in STEP_FIELDS
    assert 'screen-page-v9' in STEP_FIELDS and 'announcement-body-v6' in STEP_FIELDS


def test_missing_old_objects_are_discovery_and_supplier_tasks_reference_subs():
    understanding = AnnouncementUnderstanding(run_id="r", announcement_id="a", project_name="项目",
        announcement_type="winning_announcement", package_mode="single", status="success",
        packages=[PackageUnderstanding(package_no="1", project_id="项目|1", package_evidence_text="1")])
    empty = plan_projects("r", understanding, [], True)
    assert all(not n["target_candidate_ids"] for n in empty.projects[0].needs
               if n["field"] in {"object_name", "brand", "spec_model", "quantity", "unit_price", "total_price"})
    sub = _sub("sub", "供应商")
    with_sub = plan_projects("r", understanding, [sub], True)
    for need in with_sub.projects[0].needs:
        if need["field"] in {"is_winner", "score"}:
            assert need["target_entity"] == "sub" and need["target_candidate_ids"] == ["sub"]


def test_receiver_keeps_independent_candidates_without_task_resolution_channel():
    context = {'file_id': 'a', 'page_no': 1, 'source_segments': [{'source_ref': 'p'}]}
    receiver = ExtractionReceiver([context])
    issues = receiver.accept({'candidates': [
        {'entity_type': 'cob', 'source_ref': 'p', 'fields': {'object_name': '设备'}},
        {'entity_type': 'cob', 'source_ref': 'invented', 'fields': {'object_name': '不接收'}}]}, 0)
    assert issues and len(receiver.outputs['candidates']) == 1
    assert set(receiver.outputs) == {'candidates', 'package_amounts'}


@pytest.mark.parametrize("name,raw", [("left.rar", b"Rar!\x1a\x07bad"), ("fake.pdf", b"Rar!\x1a\x07bad"),
                                    ("left.7z", b"7z\xbc\xaf\x27\x1c"), ("left.zip", b"PK\x03\x04bad"),
                                    ("empty.zip", b"PK\x05\x06" + b"\0" * 18)])
def test_parse_client_refuses_archives_before_http(tmp_path, monkeypatch, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    monkeypatch.setattr("ict.parse.client.shared_client", lambda *a: pytest.fail("must not create HTTP client"))
    with pytest.raises(ParseError, match="archive_not_expanded"):
        ParseClient("https://never.invalid").parse(path)


def test_text_container_is_read_locally_even_when_named_pdf(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_text("<html>" + "采购信息" * 80, encoding="utf-8")
    fmt, note = sniff(path)
    assert fmt == "text"
    entry = FileEntry("a", path.name, path.name, path, path.stat().st_size, ".pdf", fmt, note, "digest")
    assert peek_entry(entry).readable
    assert read_native(path, fmt)[0]["source"] == "text"


def test_parse_queue_cancels_one_owner_and_preserves_other_owners():
    queue = ParseQueue(1)
    entered, released = threading.Event(), threading.Event()

    def active(*, cancel):
        cancel.bind(released.set)
        entered.set()
        assert released.wait(5)
        return "cancelled" if cancel.is_set() else "ok"
    first = queue.submit_cancellable("A", active)
    assert entered.wait(5)
    pending = queue.submit_cancellable("A", lambda **kw: pytest.fail("cancelled pending request ran"))
    other = queue.submit_cancellable("B", lambda **kw: "other")
    assert queue.cancel_owner("A") == 2
    assert first.result(5) == "cancelled" and pending.cancelled() and other.result(5) == "other"
    assert queue.submit_cancellable("A", lambda **kw: pytest.fail("new request after cancel")).cancelled()
    queue.begin_owner("A")
    assert queue.submit_cancellable("A", lambda **kw: "new run").result(5) == "new run"
    queue.shutdown()


def test_cancellation_interrupts_only_owned_stream_and_records_timing(tmp_path, monkeypatch):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    entered, released, closed = threading.Event(), threading.Event(), threading.Event()
    cancel = Cancellation()

    class Stream:
        def get_extra_info(self, name):
            return SimpleNamespace(shutdown=lambda how: released.set())
        def close(self):
            closed.set()

    def handler(request):
        request.extensions["trace"]("connection.connect_tcp.complete", {"return_value": Stream()})
        entered.set()
        assert released.wait(5)
        raise OSError("cancelled socket")
    real_client = httpx.Client
    monkeypatch.setattr("ict.parse.client.httpx.Client", lambda: real_client(transport=httpx.MockTransport(handler)))
    events = []
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(ParseClient("https://never.invalid").parse, path, cancel=cancel, progress=events.append)
        assert entered.wait(5)
        cancel.set()
        with pytest.raises(ParseError, match="parse_cancelled"):
            future.result(5)
    assert closed.is_set() and any(e["event"] == "request_failed" for e in events)


def test_parse_transport_separates_upload_server_timer_and_download(tmp_path, monkeypatch):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4\n" * 100)

    def handler(request):
        trace = request.extensions["trace"]
        trace("http11.send_request_body.started", {})
        trace("http11.send_request_body.complete", {})
        trace("http11.receive_response_headers.complete", {})
        return httpx.Response(200, json={"ok": True, "meta": {"ms": 3000}, "pages": []})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("ict.parse.client.shared_client", lambda *a: client)
    events = []
    answer = ParseClient("https://never.invalid").parse(path, progress=events.append)
    timing = answer["meta"]["client_timing"]
    assert timing["upload_complete"] <= timing["response_headers"] <= timing["download_complete"]
    assert answer["meta"]["ms"] == 3000
    assert max(e["bytes_read"] for e in events if e["event"] == "upload_read") == path.stat().st_size
    client.close()


def test_gap_snapshot_has_no_mutable_resolution_lifecycle():
    understanding = AnnouncementUnderstanding(run_id='r', status='success', project_name='项目',
        announcement_type='winning_announcement', package_mode='single', packages=[
        PackageUnderstanding(package_no='1', project_id='项目|1', package_evidence_text='原文')])
    snapshot = plan_projects('r', understanding, [], True).model_dump()
    assert not any({'state','purpose','need_id','covered_target_candidate_ids'} & set(need)
                   for project in snapshot['projects'] for need in project['needs'])
