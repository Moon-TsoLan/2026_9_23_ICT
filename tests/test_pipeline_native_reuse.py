"""Pipeline integration with fixed model answers; OCR access is forbidden in this test."""
import json

from ict.llm import LLMResult
from ict.pipeline import run_announcement
from ict.schemas import AttachmentIndex, IndexedFile, FileDecisions, FileDecision
from test_pipeline import HTML


class NativeModel:
    def complete(self, *, step, prompt_version, user, thinking=False):
        payload, _ = json.JSONDecoder().raw_decode(user)
        if step == "understand_announcement":
            result = {"project_name": "示例项目", "purchaser": "示例单位", "package_mode": "single",
                      "announcement_type": "winning_announcement", "packages": [{"package_no": "1",
                      "package_evidence_text": "公告", "package_amount": {"raw_text": "100元", "scope": "package", "amount_kind": "award"}}]}
        elif step == "bind_packages":
            result = {"bindings": [{"candidate_id": row["candidate_id"], "package_no": "1",
                                    "source_refs": row.get("evidence", {}).get("source_refs", []),
                                    "reason": "唯一包且证据对应"} for row in payload["candidates"]],
                      "amount_bindings": [{"package_no": "1", "amount_ref": "amount:package:1",
                                           "amount_kind": "award", "reason": "公告明确成交金额"}],
                      "observation_bindings": [{"amount_ref": row["amount_ref"], "package_no": "1",
                                                "amount_kind": "budget", "reason": "需求预算"}
                                               for row in payload["amount_observations"]
                                               if row["amount_ref"].startswith("amount:attachment:")]}
        elif step == "understand_html_tables":
            result = {"table_role": "cob_detail", "package_scope": "1", "row_grain": "cob",
                      "column_mapping": {"object_name": "货物名称"}, "confidence": 1}
        elif step == "extract_html_candidates":
            result = {"candidates": [{"entity_type": "sub", "fields": {"supplier_name": "供应公司", "is_winner": "是"}}]} if payload.get("body_sections") else {
                "candidates": [{"entity_type": "cob", "fields": {"brand": "测试品牌", "quantity": 1,
                                 "unit": "项", "unit_price": 100, "total_price": 100}, "source_refs": ["html:t0:r0"]}]}
        elif step == "extract_attachment_candidates":
            need = next(item for item in payload["current_package"]["needs"]
                        if item["field"] == "object_name" and item["purpose"] == "fill_field")
            result = {"candidates": [{"entity_type": "cob", "package_no": "1", "source_ref": "file:a:native:p3",
                                      "fields": {"object_name": "互联网接入服务"}}],
                      "package_amounts": [{"package_no": "1", "raw_text": "120万元", "amount_kind": "budget",
                                           "source_ref": "file:a:native:p3"}],
                      "need_resolutions": [{"need_id": need["need_id"], "field": "object_name",
                                             "target_candidate_id": need["target_candidate_ids"][0],
                                             "source_ref": "file:a:native:p3"}]}
        elif step == "merge_candidates":
            native = next(row for row in payload["candidates"] if row.get("file_class") == "tender_requirement")
            html = next(row for row in payload["candidates"] if row["candidate_id"] != native["candidate_id"])
            result = {"deltas": [{"op": "exclude", "candidate_id": native["candidate_id"], "kind": "not_an_object",
                                   "reason": "需求概述只供名称"},
                                  {"op": "name_from", "group_id": html["group_id"],
                                   "candidate_id": native["candidate_id"], "reason": "明确对应整体服务"}]}
        else:
            raise AssertionError(f"unexpected model call: {step}")
        return LLMResult(json.dumps(result, ensure_ascii=False), "fake", "fixed", prompt_version, 0)


def test_native_overview_names_html_without_ocr_or_budget_leak(monkeypatch, tmp_path):
    (tmp_path / "demo.html").write_text(HTML, encoding="utf-8")
    index = AttachmentIndex(announcement_id="demo", attachment_directory="unused", files=[IndexedFile(
        file_id="a", display_name="招标文件.pdf", relative_path="a.pdf", extension="pdf", readability="text_extractable")])
    monkeypatch.setattr("ict.pipeline.load_index", lambda *args: index)

    def triage(run_id, *args):
        return FileDecisions(run_id=run_id, status="success", file_decisions=[FileDecision(
            file_id="a", file_class="tender_requirement", priority=20, read_strategy="target_pages", reason="model",
            expected_fields=["object_name"], evidence_refs=["file:a:native:p3"],
            native_segments=[{"source_ref": "file:a:native:p3", "file_id": "a", "page_no": 3,
                              "text": "包1：互联网接入服务，预算120万元"}], screen={"requires_deep_parse": False})])

    def parse(run_id, decisions, index, failures):
        assert all(item.read_strategy == "skip" for item in decisions.file_decisions)
        return {}, []

    monkeypatch.setattr("ict.pipeline.triage_files", triage)
    monkeypatch.setattr("ict.pipeline.parse_pages", parse)
    report = run_announcement("demo", llm=NativeModel(), html_dir=tmp_path, runs_root=tmp_path / "runs")
    merged = json.loads((tmp_path / "runs/demo/09_merged_projects.json").read_text(encoding="utf-8"))
    row = merged["projects"][0]["cobs"][0]
    assert row["object_name"] == "互联网接入服务"
    assert (row["total_price"], row["quantity"]) == (100, 1)
    assert merged["projects"][0]["package_total_amount"] == 100
    extraction = json.loads((tmp_path / "runs/demo/07_attachment_extraction.json").read_text(encoding="utf-8"))
    assert extraction["status"] == "success" and len(extraction["candidates"]) == 1
    assert extraction["package_amounts"][0]["amount_kind"] == "budget"
    assert report.counts["parsed_pages"] == 0


def test_attempted_empty_native_extraction_is_partial_and_keeps_discovered_evidence(monkeypatch, tmp_path):
    class EmptyNativeModel(NativeModel):
        def complete(self, *, step, prompt_version, user, thinking=False):
            if step == "extract_attachment_candidates":
                result = {"candidates": [], "package_amounts": [], "need_resolutions": []}
            elif step == "merge_candidates":
                payload, _ = json.JSONDecoder().raw_decode(user)
                assert payload["evidence_context"]["attachment_pages"]["a:3"]["native_segments"]
                result = {"deltas": [], "quantity_accounting": []}
            else:
                return super().complete(step=step, prompt_version=prompt_version, user=user, thinking=thinking)
            return LLMResult(json.dumps(result), "fake", "fixed", prompt_version, 0)

    (tmp_path / "demo.html").write_text(HTML, encoding="utf-8")
    index = AttachmentIndex(announcement_id="demo", attachment_directory="unused", files=[IndexedFile(
        file_id="a", display_name="招标文件.pdf", relative_path="a.pdf", extension="pdf", readability="text_extractable")])
    monkeypatch.setattr("ict.pipeline.load_index", lambda *args: index)
    monkeypatch.setattr("ict.pipeline.triage_files", lambda run_id, *args: FileDecisions(
        run_id=run_id, status="success", file_decisions=[FileDecision(file_id="a", file_class="tender_requirement",
            priority=20, read_strategy="target_pages", reason="model", expected_fields=["object_name"],
            evidence_refs=["file:a:native:p3"], native_segments=[{"source_ref": "file:a:native:p3", "file_id": "a",
            "page_no": 3, "text": "包1互联网接入服务"}], screen={"requires_deep_parse": True})]))
    monkeypatch.setattr("ict.pipeline.parse_pages", lambda *args: ({}, []))
    report = run_announcement("demo", llm=EmptyNativeModel(), html_dir=tmp_path, runs_root=tmp_path / "runs")
    native = json.loads((tmp_path / "runs/demo/05c_native_extraction.json").read_text(encoding="utf-8"))
    assert native["status"] == "partial" and native["outcome"] == "attempted_empty"
    assert native["reception"] and report.counts["native_extraction_attempts"] == 1
