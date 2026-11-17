"""Full-context and page-local regressions. All model/OCR responses are fixed locally."""
import json
import threading
from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest

from ict.attachment_pages import split_page
from ict.html_context import parse_notice
from ict.llm import FakeLLMClient, LLMError, LLMResult, load_prompt
from ict.schemas import AttachmentIndex, IndexedFile, ProjectPlan, ProjectPlans, Project, Sub, MergedProjects
from ict.steps.s01_understand import read_announcement
from ict.steps.s04_attach import process_attachment_pages
from ict.supplier_policy import finalize_suppliers


class PageModel:
    def __init__(self, decisions=None, fields=None, bad=False):
        self.decisions, self.fields, self.bad = decisions or {}, fields, bad
        self.inputs = []

    def complete_with_images(self, *, step, prompt_version, images, user_text):
        payload, _ = json.JSONDecoder().raw_decode(user_text)
        self.inputs.append((step, payload))
        answer = {} if self.bad else {"action": self.decisions.get(payload["page_no"], "ocr"),
            "kind": "bid_quote", "confidence": 0.1, "reason": "根据当前页内容判断"}
        return LLMResult(json.dumps(answer), "fixed", "fixed", prompt_version, 1)

    def complete(self, *, step, prompt_version, user, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(user)
        self.inputs.append((step, payload))
        assert step == "extract_attachment_candidates"
        context = payload["page_contexts"][0]
        candidates = [] if self.fields is None else [{"entity_type": "cob", "package_no": "1",
            "source_ref": context["source_segments"][0]["source_ref"], "source_class": "bid_quote",
            "fields": self.fields, "column_units": {"unit_price": "单价(万元)"}}]
        return LLMResult(json.dumps({"candidates": candidates, "package_amounts": [],
            "kind": "bid_quote", "confidence": 0.8, "reason": "已读当前片段"}), "fixed", "fixed", prompt_version, 1)


def plans():
    return ProjectPlans(run_id="r", status="success", projects=[ProjectPlan(
        project_id="项目|1", package_no="1", status="success", needs=[
            {"package_no": "1", "field": "supplier_name", "reason": "maybe_more_objects", "target_entity": "sub"}])])


def index_for(tmp_path, suffix="pdf", pages=2, name=None):
    path = tmp_path / (name or "source." + suffix)
    if suffix == "pdf":
        with pymupdf.open() as document:
            for _ in range(pages):
                document.new_page()
            document.save(path)
    else:
        path.write_text("<html>原生文本</html>", encoding="utf-8")
    return AttachmentIndex(announcement_id="a", attachment_directory=str(tmp_path), files=[IndexedFile(
        file_id="f", display_name=path.name, relative_path=path.name, extension=path.suffix,
        fmt=suffix, readability="unknown", page_count=pages)])


def fake_pages(monkeypatch, count=2, images=True):
    def pages(entry, choose, stack):
        for number in range(1, count + 1):
            yield {"page_no": number, "text": "" if images else "原生页%d" % number,
                   "tables": [], "images": ["data:image/png;base64,AA=="] if images else [],
                   "source_type": "pdf", "coordinate_kind": "pdf_page"}
    monkeypatch.setattr("ict.attachment_pages.source_pages", pages)


def no_ocr(monkeypatch):
    monkeypatch.setattr("ict.steps.s04_attach._client", lambda: pytest.fail("不应创建OCR客户端"))


def test_two_branches_are_parallel_complete_and_body_identity_wins(tmp_path):
    path = tmp_path / "a.html"
    body = "前置正文" + "长正文" * 2200 + "末尾证据"
    path.write_text('<h2 class="tc">标题</h2><div class="table"><table><tr><td class="title">采购项目名称</td><td>概要名</td></tr></table></div>'
        '<div class="vF_detail_content"><div>' + body + '</div>' + ''.join(
            '<table><tr><td>表%d</td></tr><tr><td>末行%d</td></tr></table>' % (i, i) for i in range(16)) + '</div>', encoding="utf-8")
    barrier, captured = threading.Barrier(2), {}
    class Model:
        def complete(self, *, step, prompt_version, user, **kwargs):
            captured[step] = json.loads(user)["html"]
            barrier.wait(timeout=3)
            output = {"project_name": "概要名", "purchaser": "采购人"} if step.endswith("summary") else {
                "project_name": "正文名", "package_mode": "single", "packages": [{"source_package_no": "1"}]}
            return LLMResult(json.dumps(output), "fixed", "fixed", prompt_version, 1)
    notice = parse_notice(path)
    understanding, _, _ = read_announcement("r", notice, Model())
    assert body in captured["read_announcement_body"]
    assert "末行15" in captured["read_announcement_body"]
    assert "概要名" not in captured["read_announcement_body"]
    assert "末尾证据" not in captured["read_announcement_summary"]
    assert understanding.project_name == "正文名" and understanding.purchaser == "采购人"
    assert understanding.branch_audit["conflicts"][0]["selected"] == "body"


def test_same_number_units_bind_nested_candidates_once(tmp_path):
    path = tmp_path / "a.html"
    path.write_text('<div class="vF_detail_content"><p>第1标段甲；合同包1乙</p></div>', encoding="utf-8")
    body = {"project_name": "项目", "package_mode": "multi", "packages": [
        {"source_package_no": "1", "package_kind": kind, "candidates": [{"entity_type": "cob",
         "source_ref": "html:body", "source_class": "cob_detail", "fields": {"object_name": name}}]}
        for kind, name in [("lot", "甲"), ("contract_package", "乙")]]}
    model = FakeLLMClient({"read_announcement_summary": "{}", "read_announcement_body": json.dumps(body)})
    understanding, _, output = read_announcement("r", parse_notice(path), model)
    assert len({package.package_no for package in understanding.packages}) == 2
    assert [row.package_no for row in output.candidates] == [package.package_no for package in understanding.packages]
    assert {package.source_package_no for package in understanding.packages} == {"1"}


def test_invalid_body_source_is_repaired_once_without_other_calls(tmp_path):
    path = tmp_path / "a.html"
    path.write_text('<p>内容</p>', encoding="utf-8")
    body = {"project_name": "项目", "package_mode": "single", "packages": [{"source_package_no": "1"}],
            "candidates": [{"entity_type": "sub", "source_ref": "不存在", "fields": {"supplier_name": "甲"}}]}
    fixed = deepcopy(body)
    fixed["candidates"][0]["source_ref"] = "html:body"
    model = FakeLLMClient({"read_announcement_summary": "{}", "read_announcement_body": [json.dumps(body), json.dumps(fixed)]})
    result, _, observations = read_announcement("r", parse_notice(path), model)
    assert result.status == "success" and model.used["read_announcement_body"] == 2
    assert observations.candidates[0].package_no is None


def test_page_gate_reads_later_page_and_ocr_only_selected_page(tmp_path, monkeypatch):
    index = index_for(tmp_path)
    fake_pages(monkeypatch)
    calls = []
    class OCR:
        def parse(self, path, **kwargs):
            calls.append(kwargs["pages"])
            return {"pages": [{"page_no": 2, "markdown": "有效审查名单"}]}
    monkeypatch.setattr("ict.steps.s04_attach._client", OCR)
    model = PageModel({1: "skip", 2: "ocr"}, {"object_name": "设备"})
    original = plans()
    snapshot = original.model_dump()
    output, contexts, _ = process_attachment_pages("r", "项目", original, index, model, 1)
    assert calls == [[2]] and output.status == "success"
    assert len(output.candidates) == len(contexts) == 1 and output.candidates[0].source.page_no == 2
    assert original.model_dump() == snapshot
    assert [payload["page_no"] for step, payload in model.inputs if step == "inspect_attachment_page"] == [1, 2]
    assert all("need_resolutions" not in payload and "native_reuse" not in payload for _, payload in model.inputs)


def test_bad_page_answer_stays_failed_after_one_repair_and_never_ocr(tmp_path, monkeypatch):
    index = index_for(tmp_path, pages=1)
    fake_pages(monkeypatch, count=1)
    no_ocr(monkeypatch)
    model = PageModel(bad=True)
    result, _, _ = process_attachment_pages("r", "项目", plans(), index, model, 1)
    assert result.status == "partial" and result.page_quality[0]["status"] == "failed"
    assert len(model.inputs) == 2 and not result.candidates


def test_transport_failure_is_not_an_ocr_decision(tmp_path, monkeypatch):
    index = index_for(tmp_path, pages=1)
    fake_pages(monkeypatch, count=1)
    no_ocr(monkeypatch)
    class Broken(PageModel):
        def complete_with_images(self, **kwargs):
            raise LLMError("离线接口失败")
    output, _, _ = process_attachment_pages("r", "项目", plans(), index, Broken(), 1)
    assert output.status == "partial" and output.failures


def test_empty_native_extraction_is_success_and_never_reopens(tmp_path, monkeypatch):
    index = index_for(tmp_path, suffix="txt", pages=1)
    no_ocr(monkeypatch)
    model = PageModel()
    result, contexts, _ = process_attachment_pages("r", "项目", plans(), index, model, 1)
    assert result.status == "success" and not result.candidates and len(contexts) == 1
    assert [step for step, _ in model.inputs] == ["extract_attachment_candidates"]
    assert result.page_quality[0]["extraction"][0]["responses"]


def test_native_context_budget_marks_uncovered_without_ocr(tmp_path, monkeypatch):
    index = index_for(tmp_path, pages=31)
    fake_pages(monkeypatch, count=31, images=False)
    no_ocr(monkeypatch)
    model = PageModel()
    result, contexts, _ = process_attachment_pages("r", "项目", plans(), index, model, 1)
    assert len(contexts) == len(model.inputs) == 30
    assert result.status == "partial"
    assert result.page_quality[-1]["status"] == "uncovered"


def test_announcement_context_budget_is_shared_across_files(tmp_path, monkeypatch):
    indexes = [index_for(tmp_path, pages=30, name="source%d.pdf" % index) for index in range(4)]
    files = []
    for number, index in enumerate(indexes):
        index.files[0].file_id = "f%d" % number
        files.extend(index.files)
    indexes[0].files = files
    fake_pages(monkeypatch, count=30, images=False)
    no_ocr(monkeypatch)
    result, contexts, _ = process_attachment_pages("r", "项目", plans(), indexes[0], PageModel(), 1)
    assert len(contexts) == 100 and result.status == "partial"
    assert sum(row.get("status") == "uncovered" for row in result.page_quality) == 20


def test_native_pdf_has_no_image_gate_and_preserves_column_unit(tmp_path, monkeypatch):
    path = tmp_path / "native.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "native full content")
        document.save(path)
    index = AttachmentIndex(announcement_id="a", attachment_directory=str(tmp_path), files=[IndexedFile(
        file_id="f", display_name=path.name, relative_path=path.name, extension=".pdf", fmt="pdf", readability="unknown")])
    no_ocr(monkeypatch)
    model = PageModel(fields={"object_name": "设备", "unit_price": "1.25"})
    result, _, _ = process_attachment_pages("r", "项目", plans(), index, model, 1)
    from ict.steps.s07_normalize import normalize_candidates
    from ict.catalog import Catalog
    normalized = normalize_candidates("r", result.candidates, Catalog())
    assert normalized.candidates[0].fields["unit_price"].raw_value == "1.25"
    assert normalized.candidates[0].fields["unit_price"].normalized_value == 12500
    assert [step for step, _ in model.inputs] == ["extract_attachment_candidates"]


def test_complete_pipeline_native_attachment_and_final_policy(tmp_path, monkeypatch):
    from ict.pipeline import run_announcement
    (tmp_path / "a.html").write_text('<div class="vF_detail_content"><p>项目甲，中标人甲，成交20元，明细见附件</p></div>', encoding="utf-8")
    index = index_for(tmp_path, suffix="txt", pages=1)
    monkeypatch.setattr("ict.pipeline.load_index", lambda *args: index)
    no_ocr(monkeypatch)
    class Model(PageModel):
        def complete(self, *, step, prompt_version, user, **kwargs):
            if step.startswith("read_announcement_"):
                answer = {} if step.endswith("summary") else {"project_name": "项目甲", "package_mode": "single", "packages": [
                    {"source_package_no": "1", "candidates": [{"entity_type": "sub", "source_ref": "html:body", "fields":
                     {"supplier_name": "甲", "is_winner": True, "supplier_award_amount": "20元", "review_status": "unknown"}}]}]}
            elif step == "merge_candidates":
                answer = {"deltas": [], "quantity_accounting": []}
            else:
                return super().complete(step=step, prompt_version=prompt_version, user=user, **kwargs)
            return LLMResult(json.dumps(answer), "fixed", "fixed", prompt_version, 1)
    model = Model(fields={"object_name": "设备", "brand": "品牌", "spec_model": "定制", "quantity": 2, "unit": "台", "total_price": "20元"})
    report = run_announcement("a", llm=model, html_dir=tmp_path, runs_root=tmp_path / "runs")
    assert report.result["packages"][0]["cobs"][0]["spec_model"] == "定制"
    sub = report.result["packages"][0]["subs"][0]
    assert sub["bid_amount"] == 20 and sub["review_status"] == "passed"
    gap = json.loads((tmp_path / "runs/a/04_project_plans.json").read_text(encoding="utf-8"))
    assert not any(need["field"] in ("bid_amount", "review_status") for need in gap["projects"][0]["needs"])
    audit = json.loads((tmp_path / "runs/a/09_merged_projects.json").read_text(encoding="utf-8"))
    assert len([entry for entry in audit["field_bindings"] if entry.get("method") == "supplier_policy"]) == 2


def test_body_cannot_silently_create_a_missing_singleton(tmp_path):
    path = tmp_path / "a.html"
    path.write_text("<p>原文</p>", encoding="utf-8")
    model = FakeLLMClient({"read_announcement_summary": "{}", "read_announcement_body": json.dumps({
        "project_name": "项目", "package_mode": "single", "packages": []})})
    understanding, _, _ = read_announcement("r", parse_notice(path), model)
    assert understanding.status == "failed" and not understanding.packages


def test_announcement_amount_keeps_raw_cell_and_uses_header_unit(tmp_path):
    path = tmp_path / "a.html"
    path.write_text('<div class="table"><table><tr><td class="title">总成交金额（万元）</td><td>1.25</td></tr></table></div><p>正文项目</p>', encoding="utf-8")
    summary = {"summary_amount": {"raw_text": "1.25", "unit_header": "总成交金额（万元）", "amount_kind": "award", "source_ref": "html:summary"}}
    body = {"project_name": "项目", "package_mode": "single", "packages": [{"source_package_no": "1"}]}
    model = FakeLLMClient({"read_announcement_summary": json.dumps(summary), "read_announcement_body": json.dumps(body)})
    understanding, _, _ = read_announcement("r", parse_notice(path), model)
    assert understanding.summary_amount.raw_text == "1.25"
    assert understanding.summary_amount.amount_yuan == 12500
    assert understanding.summary_amount.binding["unit_header"] == "总成交金额（万元）"


@pytest.mark.parametrize("location", ["package", "candidate"])
def test_announcement_cannot_reintroduce_ambiguous_original_internal_number(location, tmp_path):
    path = tmp_path / "a.html"
    path.write_text("<p>项目正文</p>", encoding="utf-8")
    package = {"source_package_no": "1", "candidates": []}
    if location == "package":
        package["package_no"] = "1"
    else:
        package["candidates"] = [{"entity_type": "cob", "package_no": "1", "source_ref": "html:body", "fields": {"object_name": "设备"}}]
    body = {"project_name": "项目", "package_mode": "single", "packages": [package]}
    model = FakeLLMClient({"read_announcement_summary": "{}", "read_announcement_body": [json.dumps(body)]})
    result, _, _ = read_announcement("r", parse_notice(path), model)
    assert result.status == ("failed" if location == "package" else "partial") and model.used["read_announcement_body"] == 2


def test_row_source_role_and_quote_owner_reach_existing_candidate_interface(tmp_path, monkeypatch):
    index = index_for(tmp_path, suffix="txt", pages=1)
    no_ocr(monkeypatch)
    class Model(PageModel):
        def complete(self, *, step, prompt_version, user, **kwargs):
            context = json.loads(user)["page_contexts"][0]
            answer = {"candidates": [{"entity_type": "cob", "package_no": "1", "source_class": "tender_requirement",
                "source_ref": context["source_segments"][0]["source_ref"], "quote_supplier": "乙", "bidder_supplier": "乙",
                "winner_supplier": "甲", "fields": {"object_name": "组成", "spec_model": "定制"}}],
                "package_amounts": [], "kind": "bid_quote", "confidence": 0.8, "reason": "混合材料里的需求观测"}
            return LLMResult(json.dumps(answer), "fixed", "fixed", prompt_version, 1)
    result, _, _ = process_attachment_pages("r", "项目", plans(), index, Model(), 1)
    candidate = result.candidates[0]
    assert candidate.source_class == "tender_requirement"
    assert candidate.evidence.bidder_supplier == candidate.evidence.quote_supplier == "乙"
    assert candidate.evidence.winner_supplier == "甲"


def test_hard_ignore_never_reads_or_calls_model(tmp_path, monkeypatch):
    index = index_for(tmp_path, name="中小企业声明函.pdf")
    monkeypatch.setattr("ict.attachment_pages.source_pages", lambda *args: pytest.fail("不应读取硬忽略文件"))
    output, _, _ = process_attachment_pages("r", "项目", plans(), index, PageModel(), 1)
    assert output.page_quality[0]["reason"] == "rule_name_ignore:中小企业声明函"


def test_docx_keeps_later_tables_and_document_order(tmp_path):
    import docx
    from ict.documents import read_native
    document = docx.Document()
    document.add_paragraph("首段")
    for index in range(9):
        table = document.add_table(rows=2, cols=1)
        table.cell(0, 0).text = "名称"
        table.cell(1, 0).text = "表%d" % index
        document.add_paragraph("表后%d" % index)
    path = tmp_path / "wrong.doc"
    document.save(path)
    page = read_native(path, "docx")[0]
    text = " ".join(segment["text"] for context in split_page("f", path.name, page) for segment in context["source_segments"])
    assert text.index("表8") < text.index("表后8") and text.index("首段") < text.index("表0")


def test_xlsx_keeps_later_sheet_and_rows_beyond_old_preview(tmp_path):
    from openpyxl import Workbook
    from ict.documents import read_native
    book = Workbook()
    for index in range(4):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.append(["名称", "价格"])
        for row in range(410):
            sheet.append(["表%d行%d" % (index, row), row])
    path = tmp_path / "wrong.bin"
    book.save(path)
    pages = read_native(path, "xlsx")
    text = " ".join(segment["text"] for page in pages for context in split_page("f", path.name, page)
                    for segment in context["source_segments"])
    assert "表3行409" in text


def test_context_splitting_preserves_the_tail_and_source_locations():
    text = "长正文" * 6000 + "末尾证据"
    contexts = split_page("f", "a", {"page_no": 8, "text": text, "tables": [], "source_type": "pdf"})
    assert "".join(segment["text"] for context in contexts for segment in context["source_segments"]) == text
    assert len({segment["source_ref"] for context in contexts for segment in context["source_segments"]}) == len(contexts)
    assert all(sum(len(segment["text"]) for segment in context["source_segments"]) <= 6000 for context in contexts)


@pytest.mark.parametrize("bid,award,winner,review,expected_bid,expected_review", [
    (None, 20, True, "unknown", 20, "passed"), (10, 20, True, "unknown", 10, "passed"),
    (0, 20, True, "failed", 0, "failed"), (None, 20, False, "unknown", None, "passed"),
    (None, None, True, "passed", None, "passed")])
def test_final_supplier_policy(bid, award, winner, review, expected_bid, expected_review):
    sub = Sub(supplier_name="甲", is_winner=winner, bid_amount=bid, supplier_award_amount=award, review_status=review)
    merged = MergedProjects(run_id="r", status="success", projects=[Project(project_id="p", project_name="项目", package_no="1", subs=[sub])])
    finalize_suppliers(merged)
    assert (sub.bid_amount, sub.review_status) == (expected_bid, expected_review)
    count = len(merged.field_bindings)
    finalize_suppliers(merged)
    assert len(merged.field_bindings) == count


def test_unresolved_and_conflicting_sub_fields_are_not_defaulted():
    sub = Sub(supplier_name="甲", is_winner=True, supplier_award_amount=20)
    merged = MergedProjects(run_id="r", status="partial", projects=[Project(project_id="p", project_name="项目", package_no="1",
        subs=[sub], provenance={"sub_candidate_ids": ["s"]})], unresolved=[{"package_no": "1", "candidate_id": "s", "field": "bid_amount"}],
        conflicts=[{"package_no": "1", "supplier_name": "甲", "field": "review_status"}])
    finalize_suppliers(merged)
    assert sub.bid_amount is None and sub.review_status == "unknown"


def test_active_fields_have_no_retired_lifecycle_or_alias_chain():
    from ict.fields import STEP_FIELDS
    names = {name for incoming, outgoing in STEP_FIELDS.values() for name in incoming + outgoing}
    assert not names & {"need_resolutions", "requires_deep_parse", "reuse_status", "needs_before", "needs_after", "found_fields"}
    for version in STEP_FIELDS:
        assert load_prompt(version).count("字段含义") == 1
    assert "FIELD_SEMANTICS.update" not in Path("src/ict/fields.py").read_text(encoding="utf-8")


def test_page_gate_receives_the_same_readonly_known_observations(tmp_path, monkeypatch):
    index = index_for(tmp_path)
    fake_pages(monkeypatch)
    class OCR:
        def parse(self, path, **kwargs):
            return {"pages": [{"page_no": number, "markdown": "新增标的"} for number in kwargs["pages"]]}
    monkeypatch.setattr("ict.steps.s04_attach._client", OCR)
    known = [{"candidate_id": "c0", "entity_type": "cob", "package_no": "1",
              "fields": {"object_name": {"raw_value": "已有标的", "status": "present"}}, "source_refs": ["html:n1"]}]
    snapshot = deepcopy(known)
    model = PageModel(fields={"object_name": "新增标的", "product_supplier": "制造商"})
    output, _, _ = process_attachment_pages("r", "项目", plans(), index, model, 1, known_observations=known)
    inspections = [payload for step, payload in model.inputs if step == "inspect_attachment_page"]
    assert len(output.candidates) == 2 and known == snapshot
    assert all(payload["known_observations"] == snapshot for payload in inspections)
    assert all(set(payload) == {"project_name", "file_id", "file_name", "page_no", "package_registry", "summary_amount", "known_observations", "source_segments", "needs"}
               for payload in inspections)
    assert all(payload['project_name'] == '项目' for payload in inspections)


def test_rejected_supplier_does_not_get_passed_or_award_default_in_pipeline(tmp_path, monkeypatch):
    from ict.pipeline import run_announcement
    (tmp_path / "a.html").write_text('<p>甲的中标与审查原文</p>', encoding="utf-8")
    monkeypatch.setattr("ict.pipeline.load_index", lambda *args: None)
    no_ocr(monkeypatch)
    class Model(PageModel):
        def complete(self, *, step, prompt_version, user, **kwargs):
            if step.startswith("read_announcement_"):
                answer = {} if step.endswith("summary") else {"project_name": "项目", "package_mode": "single", "packages": [
                    {"source_package_no": "1", "candidates": [
                        {"entity_type": "sub", "source_ref": "html:body", "fields": {"supplier_name": "甲", "is_winner": True,
                         "supplier_award_amount": "20元", "review_status": "unknown"}},
                        {"entity_type": "sub", "source_ref": "missing", "fields": {"supplier_name": "甲", "review_status": "unknown"}},
                        {"entity_type": "cob", "source_class": "cob_detail", "source_ref": "html:body", "fields":
                            {"object_name": "设备", "brand": "品牌", "spec_model": "定制", "quantity": 1, "unit": "台", "total_price": "20元"}}]}]}
            elif step == "merge_candidates":
                answer = {"deltas": [], "quantity_accounting": []}
            else:
                return super().complete(step=step, prompt_version=prompt_version, user=user, **kwargs)
            return LLMResult(json.dumps(answer), "fixed", "fixed", prompt_version, 1)
    report = run_announcement("a", llm=Model(), html_dir=tmp_path, runs_root=tmp_path / "runs")
    assert report.status == "partial"
    sub = report.result["packages"][0]["subs"][0]
    assert sub["supplier_award_amount"] == 20 and sub["bid_amount"] is None and sub["review_status"] == "unknown"
    audit = json.loads((tmp_path / "runs/a/09_merged_projects.json").read_text(encoding="utf-8"))
    assert {row["field"] for row in audit["unresolved"] if row.get("reason") == "candidate_reception_failed"} == {"bid_amount", "review_status"}
    assert not any(row.get("method") == "supplier_policy" for row in audit["field_bindings"])


@pytest.mark.parametrize("bad_package_no", ["1", ["1"]])
def test_attachment_rejected_supplier_blocks_final_defaults(tmp_path, monkeypatch, bad_package_no):
    from ict.pipeline import run_announcement
    (tmp_path / "a.html").write_text('<p>中标人甲成交20元，明细见附件</p>', encoding="utf-8")
    index = index_for(tmp_path, suffix="txt", pages=1)
    monkeypatch.setattr("ict.pipeline.load_index", lambda *args: index)
    no_ocr(monkeypatch)
    class Model(PageModel):
        def complete(self, *, step, prompt_version, user, **kwargs):
            if step.startswith("read_announcement_"):
                answer = {} if step.endswith("summary") else {"project_name": "项目", "package_mode": "single", "packages": [
                    {"source_package_no": "1", "candidates": [{"entity_type": "sub", "source_ref": "html:body", "fields":
                     {"supplier_name": "甲", "is_winner": True, "supplier_award_amount": "20元", "review_status": "unknown"}}]}]}
            elif step == "extract_attachment_candidates":
                payload, _ = json.JSONDecoder().raw_decode(user)
                ref = payload["page_contexts"][0]["source_segments"][0]["source_ref"]
                answer = {"kind": "bid_quote", "confidence": 0.8, "reason": "当前片段", "package_amounts": [], "candidates": [
                    {"entity_type": "cob", "package_no": "1", "source_class": "bid_quote", "source_ref": ref,
                     "fields": {"object_name": "设备", "brand": "品牌", "spec_model": "定制", "quantity": 1, "unit": "台", "total_price": "20元"}},
                    {"entity_type": "sub", "package_no": bad_package_no, "source_ref": "missing", "fields": {"supplier_name": "甲"}}]}
            elif step == "merge_candidates":
                answer = {"deltas": [], "quantity_accounting": []}
            else:
                return super().complete(step=step, prompt_version=prompt_version, user=user, **kwargs)
            return LLMResult(json.dumps(answer), "fixed", "fixed", prompt_version, 1)
    report = run_announcement("a", llm=Model(), html_dir=tmp_path, runs_root=tmp_path / "runs")
    assert report.status == "partial"
    sub = report.result["packages"][0]["subs"][0]
    assert sub["supplier_award_amount"] == 20 and sub["bid_amount"] is None and sub["review_status"] == "unknown"
