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
from ict.steps.s02_html import _validate_table
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


@pytest.mark.parametrize("scope", [["1"], ["1", "2"], 1, None, {"id": "1"}])
def test_html_scope_type_error_names_the_path_and_never_picks_first(scope):
    error = _validate_table({"table_role": "cob_detail", "package_scope": scope}, ["1", "2"])
    assert error.path == "$.package_scope" and "字符串" in error.message


def test_html_scope_protocol_repair_gets_precise_error_and_original_answer():
    model = Fixed({"table_role": "cob_detail", "package_scope": ["1", "2"]},
                  {"table_role": "cob_detail", "package_scope": "announcement"})
    answer = complete_json(model, step="understand_html_tables", prompt_version="html-tables-v6", user="{}",
                           validate=lambda value: _validate_table(value, ["1", "2"]))
    assert answer["package_scope"] == "announcement"
    repair = json.loads(model.inputs[1][2].split("\n")[-1])
    assert repair["validation_issues"][0]["path"] == "$.package_scope"
    assert json.loads(repair["previous_response"])["package_scope"] == ["1", "2"]


def test_business_values_are_not_top_level_table_or_location_outputs():
    for version in ("html-tables-v6", "locate-pages-v5"):
        assert not {"bid_amount", "review_status", "review_failure_reason"} & set(STEP_FIELDS[version][1])


def test_missing_old_objects_are_discovery_and_supplier_tasks_reference_subs():
    understanding = AnnouncementUnderstanding(run_id="r", announcement_id="a", project_name="项目",
        announcement_type="winning_announcement", package_mode="single", status="success",
        packages=[PackageUnderstanding(package_no="1", project_id="项目|1", package_evidence_text="1")])
    empty = plan_projects("r", understanding, [], True)
    assert all(n["purpose"] == "discover_objects" for n in empty.projects[0].needs
               if n["field"] in {"object_name", "brand", "spec_model", "quantity", "unit_price", "total_price"})
    sub = _sub("sub", "供应商")
    with_sub = plan_projects("r", understanding, [sub], True)
    for need in with_sub.projects[0].needs:
        if need["field"] in {"is_winner", "score"}:
            assert need["target_entity"] == "sub" and need["target_candidate_ids"] == ["sub"]


def test_receiver_does_not_accept_invented_targets_but_keeps_independent_candidates():
    context = {"file_id": "a", "page_no": 1, "source_segments": [{"source_ref": "p"}]}
    receiver = ExtractionReceiver([context], needs=[{"need_id": "n", "field": "brand", "purpose": "fill_field", "target_candidate_ids": []}])
    issues = receiver.accept({"candidates": [{"entity_type": "cob", "source_ref": "p", "fields": {"object_name": "设备"}}],
        "need_resolutions": [{"need_id": "n", "field": "brand", "target_candidate_id": "invented", "source_ref": "p"}]}, 0)
    assert issues and len(receiver.outputs["candidates"]) == 1 and not receiver.outputs["need_resolutions"]


def test_file_can_end_after_old_need_disappears_while_global_discovery_remains():
    before = plans({"need_id": "old", "field": "brand", "purpose": "fill_field", "target_candidate_ids": ["html"]})
    after = plans({"need_id": "discover", "field": "supplier_name", "purpose": "discover_objects", "state": "pending"})
    files, model = decisions(), Fixed(finish())
    assert not s04_attach.review_native_continuation(before, after, files, [source()], [], model)
    assert files.file_decisions[0].read_strategy == "skip"
    assert after.projects[0].needs[0]["state"] == "pending"
    payload = json.loads(model.inputs[0][2])
    assert payload["needs_before"][0]["need_id"] == "old" and payload["needs_after"][0]["need_id"] == "discover"


@pytest.mark.parametrize("ref", ["file:other:native:p1", "invented"])
def test_file_review_rejects_unreceived_or_another_files_evidence(ref):
    files = decisions()
    errors = s04_attach.review_native_continuation(plans(), plans(), files, [source()], [], Fixed(finish(ref)))
    assert errors and files.file_decisions[0].read_strategy == "target_pages"
    assert len(files.file_decisions[0].continuation_audit["attempts"]) == 2


@pytest.mark.parametrize("mode", ["empty", "unbound", "deep_requested"])
def test_missing_evidence_or_deep_request_never_skips_or_calls_review(mode):
    files = decisions()
    row = source()
    if mode == "unbound":
        row.package_no = None
    if mode == "deep_requested":
        files.file_decisions[0].screen["requires_deep_parse"] = True
    model = Fixed(finish())
    s04_attach.review_native_continuation(plans(), plans(), files, [] if mode == "empty" else [row], [], model)
    assert not model.inputs and files.file_decisions[0].read_strategy == "target_pages"


def test_other_packages_same_field_does_not_block_file_or_resolve_global_need():
    own = {"need_id": "own", "field": "brand", "purpose": "fill_field", "package_no": "1", "target_candidate_ids": ["html"]}
    other = {**own, "need_id": "other", "package_no": "2", "target_candidate_ids": ["html2"], "state": "pending"}
    snapshot = plans(own, other)
    files = decisions()
    ready = s04_attach.apply_native_resolutions(snapshot, files, [source()], [{"need_id": "own", "field": "brand",
        "target_candidate_id": "html", "source_ref": "file:a:native:p1"}])
    assert ready == {"own"} and files.file_decisions[0].read_strategy == "skip"
    assert snapshot.projects[0].needs[1]["state"] == "pending"


def test_replan_does_not_apply_old_resolution_to_new_targets():
    old = {"need_id": "n", "purpose": "fill_field", "package_no": "1", "target_candidate_ids": ["a"],
           "covered_target_candidate_ids": ["a"], "source_refs": ["p"], "state": "evidence_ready"}
    after = plans({**old, "target_candidate_ids": ["a", "new"], "covered_target_candidate_ids": [], "state": "pending"})
    s04_attach.carry_native_resolution_state(plans(old), after)
    assert after.projects[0].needs[0]["covered_target_candidate_ids"] == ["a"]
    assert after.projects[0].needs[0]["state"] == "pending"


def test_successfully_attempted_empty_file_needs_explicit_model_review_not_automatic_skip():
    receipt = {"source_refs": ["file:a:native:p1"], "attempts": [{"issues": []}], "unrepaired": False}
    answer = finish() | {"evidence_basis": "attempted_no_new_observation"}
    files, model = decisions(), Fixed(answer)
    s04_attach.review_native_continuation(plans(), plans(), files, [], [], model, reception=[receipt])
    assert len(model.inputs) == 1 and files.file_decisions[0].read_strategy == "skip"
    files = decisions()
    model = Fixed(answer | {"continuation": "deep_parse", "evidence_refs": []})
    s04_attach.review_native_continuation(plans(), plans(), files, [], [], model, reception=[receipt])
    assert files.file_decisions[0].read_strategy == "target_pages"


@pytest.mark.parametrize("rejection", ["failed", "rejected", "missing_fragment"])
def test_unsuccessful_or_rejected_attempt_cannot_end_empty_file(rejection):
    receipt = {"source_refs": ["file:a:native:p1"], "attempts": [{"issues": []}], "unrepaired": False}
    if rejection == "failed":
        receipt["unrepaired"] = True
    elif rejection == "rejected":
        receipt["rejected_items"] = [{"path": "candidates[0]", "raw": {"source_ref": "file:a:native:p1"}}]
    else:
        receipt["source_refs"] = ["file:a:native:p2"]
    files, model = decisions(), Fixed(finish() | {"evidence_basis": "attempted_no_new_observation"})
    s04_attach.review_native_continuation(plans(), plans(), files, [], [], model, reception=[receipt])
    assert not model.inputs and files.file_decisions[0].read_strategy == "target_pages"


def test_partial_recursive_archive_records_failure_and_keeps_members_without_ocr(tmp_path, monkeypatch):
    from prep import extract_attachments as extractor
    archive = tmp_path / "notice.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("nested.rar", b"Rar!\x1a\x07\x00bad")
        outer.writestr("readable.pdf", "<html>" + "采购内容" * 70 + "</html>")

    def partial(archive, dest, failures):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "member.pdf").write_text("<html>" + "成员内容" * 70, encoding="utf-8")
        with zipfile.ZipFile(dest / "usable_nested.zip", "w") as child:
            child.writestr("deep.pdf", "<html>" + "嵌套成员" * 70)
        failures.append({"archive": str(archive), "reason": "7z_exit_2:Data Error"})
        return False
    monkeypatch.setattr(extractor, "extract_with_7z", partial)
    output = tmp_path / "attachments"
    record = extractor.extract_announcement(archive, output, False)
    assert record["status"] == "partial"
    index = build_index("notice", output)
    assert index.extraction["failures"] and len(index.files) == 4
    assert len(record["retained_archives"]) == 1 and record["nested_extracted"] == 1
    files = s04_attach.triage_files("r", plans({"need_id": "n", "field": "object_name"}), index,
        Fixed({"kind": "bid_quote", "has_price_table": False, "confidence": 0.9, "found_fields": ["object_name"]}))
    rar = next(f for f in files.file_decisions if f.reason.startswith("archive_not_expanded"))
    assert rar.read_strategy == "unsupported" and files.failures
    monkeypatch.setattr(s04_attach, "_client", lambda: SimpleNamespace(parse=lambda *a, **kw: pytest.fail("no OCR")))
    parsed, _ = s04_attach.parse_pages("r", files, index, [])
    assert len(parsed) == 3 and all(p[0]["source"] == "text" for p in parsed.values())


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


def test_unresolved_amount_cannot_supply_file_completion_evidence():
    from ict.schemas import PackageAmountObservation
    files = decisions()
    amount = PackageAmountObservation(package_no="1", raw_text="100元", amount_yuan=100, file_id="a",
        source_ref="file:a:native:p1", binding={"status": "unresolved"})
    model = Fixed(finish())
    s04_attach.review_native_continuation(plans(), plans(), files, [], [amount], model)
    assert not model.inputs and files.file_decisions[0].read_strategy == "target_pages"


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


def test_pipeline_preserves_task_snapshot_and_keeps_native_candidates_without_ocr(tmp_path, monkeypatch):
    from ict.pipeline import run_announcement
    html_dir, attachments = tmp_path / "html", tmp_path / "attachments"
    html_dir.mkdir()
    (html_dir / "notice.html").write_text('<html><body><div class="vF_detail_content"><p>采购公告</p></div></body></html>', encoding="utf-8")
    (attachments / "notice").mkdir(parents=True)
    (attachments / "notice" / "attachment.pdf").write_text("<html>" + "设备 品牌 厂家 " * 70, encoding="utf-8")
    (attachments / "notice" / ".extract_complete").write_text(json.dumps({"status": "partial", "failures": [{"reason": "nested_archive_failed"}]}), encoding="utf-8")

    class Model:
        def complete(self, *, step, prompt_version, user, **kwargs):
            value = json.loads(user)
            if step == "understand_announcement":
                answer = {"project_name": "项目", "package_mode": "single", "packages": [{"package_no": "1", "package_kind": "unpartitioned", "package_name": "未分包"}]}
            elif step == "extract_html_candidates":
                answer = {"candidates": []}
            elif step == "screen_files":
                answer = {"kind": "bid_quote", "has_price_table": True, "confidence": 0.9,
                          "found_fields": ["object_name", "brand"], "evidence_refs": ["file:a001:native:p1"], "requires_deep_parse": False}
            elif step == "extract_attachment_candidates":
                assert value["native_reuse"]
                answer = {"candidates": [{"entity_type": "cob", "package_no": "1", "source_ref": "file:a001:native:p1",
                    "fields": {"object_name": "设备", "brand": "品牌", "product_supplier": "厂家", "quantity": 1, "unit_price": "100元", "total_price": "100元"}}]}
            elif step == "review_native_continuation":
                answer = finish("file:a001:native:p1")
            elif step == "merge_candidates":
                answer = {"deltas": []}
            else:
                raise AssertionError("unexpected step " + step)
            return LLMResult(json.dumps(answer), "fixed", "fixed", prompt_version, 0)
    monkeypatch.setattr(s04_attach, "_client", lambda: SimpleNamespace(parse=lambda *a, **kw: pytest.fail("must not upload")))
    run = tmp_path / "runs"
    report = run_announcement("notice", llm=Model(), html_dir=html_dir, attachments_root=attachments, runs_root=run)
    folder = run / "notice"
    native = json.loads((folder / "05c_native_extraction.json").read_text(encoding="utf-8"))
    decisions_out = json.loads((folder / "05_file_decisions.json").read_text(encoding="utf-8"))
    merged = json.loads((folder / "09_merged_projects.json").read_text(encoding="utf-8"))
    assert report.status != "failed" and native["candidates"] and merged["projects"][0]["cobs"]
    assert not native["needs_before"]["projects"][0]["cob_candidate_ids"]
    assert native["needs_after"]["projects"][0]["cob_candidate_ids"]
    assert decisions_out["file_decisions"][0]["read_strategy"] == "skip"
    assert (folder / "00_attachment_preparation.json").exists()
    assert report.status == "partial"
    assert any(f["failure_code"] == "document_parse_failed" for f in report.failure_summary)
    first_owner = json.loads((folder / "run_state.json").read_text(encoding="utf-8"))["parse_owner"]
    run_announcement("notice", llm=Model(), html_dir=html_dir, attachments_root=attachments, runs_root=tmp_path / "other-account-runs")
    second_owner = json.loads((tmp_path / "other-account-runs/notice/run_state.json").read_text(encoding="utf-8"))["parse_owner"]
    assert first_owner != second_owner  # Same business id must not share cancellation scope.
