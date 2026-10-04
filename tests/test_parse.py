"""Tests for the screening and parsing layer. No server, no model, nothing outside tmp."""

import io
import json
import zipfile
from pathlib import Path

import pymupdf
from PIL import Image

from ict.llm import LLMResult
from ict.parse.census import FileEntry, sniff
from ict.parse.client import as_pages, contiguous_runs, html_to_rows, pages_to_spec
from ict.parse.peek import docx_media, peek_entry
from ict.parse.screen import gate_file, name_rule


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


def _entry(tmp_path: Path, name: str, payload: bytes, pages: int | None = None) -> FileEntry:
    path = tmp_path / name
    path.write_bytes(payload)
    fmt, note = sniff(path)
    return FileEntry(file_id="a001", name=name, relative_path=name, path=path, size_bytes=len(payload),
                     declared_ext=Path(name).suffix.lower(), fmt=fmt, note=note, digest="d", pages=pages)


def _docx_with_media(path: Path) -> None:
    document = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p/></w:body></w:document>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<?xml version='1.0'?>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/media/image1.png", PNG)


def _blank_pdf(path: Path) -> None:
    document = pymupdf.open()
    document.new_page(width=595, height=842)
    document.save(str(path))
    document.close()


class Recorder:
    """Stand-in model client: answers both gates and records what it was shown."""

    def __init__(self, answer: dict, images_ok: bool = True):
        self.answer = answer
        self.images_ok = images_ok
        self.seen_images = None
        self.text_calls = 0
        self.payload = None

    def complete(self, *, step, prompt_version, user):
        self.text_calls += 1
        self.payload = json.loads(user)
        return LLMResult(json.dumps(self.answer, ensure_ascii=False), "recorder", "test", prompt_version, 1)

    def complete_with_images(self, *, step, prompt_version, images, user_text):
        if not self.images_ok:
            raise NotImplementedError("no vision")
        self.seen_images = images
        return LLMResult(json.dumps(self.answer, ensure_ascii=False), "recorder", "test", prompt_version, 1)


def test_sniff_trusts_bytes_over_extension(tmp_path):
    ole = _entry(tmp_path, "fake.docx", b"\xd0\xcf\x11\xe0\x00" * 4)
    assert (ole.fmt, ole.note) == ("ole", "legacy_office")
    assert ole.needs_normalisation is True and ole.native_readable is False
    assert _entry(tmp_path, "fake2.docx", b"PK\x03\x04junk").fmt == "zip"
    assert _entry(tmp_path, "x.bin", b"%PDF-1.7\n").fmt == "pdf"


def test_a_junk_looking_name_never_drops_a_legacy_file(tmp_path):
    """Junk-looking names that are NOT in NAME_IGNORE stay model-decided: the OLE file is
    converted rather than dropped on suspicion."""
    entry = _entry(tmp_path, "供应商资格证明材料（包1）.docx", b"\xd0\xcf\x11\xe0\x00" * 4)
    decision = gate_file(entry, peek_entry(entry), llm=None)
    assert decision.decision == "parse"
    assert decision.reason.startswith("needs_normalisation:ole")
    assert decision.name_rule == "junk"


def test_a_picture_still_reaches_the_picture_gate_even_with_a_junk_name(tmp_path):
    entry = _entry(tmp_path, "资格证明文件扫描件.png", PNG)
    recorder = Recorder({"kind": "qualification", "has_price_table": False, "header": "",
                         "found_fields": [], "confidence": 0.95})
    decision = gate_file(entry, peek_entry(entry), recorder, needs=NEEDS)
    assert decision.method == "image_gate"
    assert decision.decision == "skip"
    assert decision.reason == "image_gate_found_none"
    assert recorder.seen_images and recorder.text_calls == 0


def test_picture_gate_failure_keeps_the_file(tmp_path):
    entry = _entry(tmp_path, "报价表.png", PNG)
    decision = gate_file(entry, peek_entry(entry), Recorder({}, images_ok=False))
    assert decision.decision == "parse"
    assert decision.reason.startswith("needs_normalisation:image")


def test_scan_page_goes_to_the_page_gate(tmp_path):
    pdf = tmp_path / "合同包1：开标记录表.pdf"
    _blank_pdf(pdf)
    entry = FileEntry(file_id="a006", name=pdf.name, relative_path=pdf.name, path=pdf,
                      size_bytes=pdf.stat().st_size, declared_ext=".pdf", fmt="pdf", note="", digest="d", pages=1)
    peek = peek_entry(entry)
    assert peek.readable is False
    recorder = Recorder({"kind": "award_detail", "has_price_table": True, "header": "供应商|总报价", "confidence": 0.9})
    decision = gate_file(entry, peek, recorder)
    assert decision.method == "scan_gate" and decision.decision == "parse"
    assert decision.name_rule != "junk"          # 合同 in a package label must not read as junk


def test_thin_docx_with_pictures_uses_media_not_a_conversion(tmp_path):
    path = tmp_path / "材料汇编（包1）.docx"
    _docx_with_media(path)
    entry = FileEntry(file_id="a001", name=path.name, relative_path=path.name, path=path,
                      size_bytes=path.stat().st_size, declared_ext=".docx", fmt="docx", note="", digest="d", pages=None)
    assert [n.split("/")[-1] for n in docx_media(path)] == ["image1.png"]
    peek = peek_entry(entry)
    assert peek.readable is False and peek.media_count == 1
    recorder = Recorder({"kind": "other", "has_price_table": False, "header": "",
                         "found_fields": [], "confidence": 0.9})
    decision = gate_file(entry, peek, recorder, needs=[{"field": "spec_model", "reason": "coverage_low",
                                                        "package_no": "1"}])
    assert decision.method == "docx_media_gate" and decision.decision == "skip"
    assert decision.reason == "docx_media_gate_found_none"
    assert recorder.text_calls == 0


def test_readable_file_goes_to_the_text_gate_with_name_as_input(tmp_path):
    entry = _entry(tmp_path, "包1报价明细表.pdf", b"%PDF-1.7\n")
    entry.note = ""
    peek = peek_entry(entry)
    peek.readable = True
    peek.view = "序号 货物名称 单价 数量 总价"
    peek.price_hints = 3
    recorder = Recorder({"kind": "bid_quote", "has_price_table": True, "header": "序号|货物名称|单价", "confidence": 0.95})
    decision = gate_file(entry, peek, recorder)
    assert decision.method == "text_gate" and decision.decision == "parse"
    assert recorder.payload["file_name"] == entry.name


NEEDS = [{"field": "unit_price", "reason": "points_to_attachment", "package_no": "B"}]


def _readable_entry(tmp_path, name):
    entry = _entry(tmp_path, name, b"%PDF-1.7\n")
    return entry


def test_a_seen_needed_field_keeps_the_file(tmp_path):
    recorder = Recorder({"kind": "bid_quote", "has_price_table": True, "header": "x|y",
                         "found_fields": ["unit_price"], "confidence": 0.9})
    entry = _readable_entry(tmp_path, "包1报价表.pdf")
    peek = peek_entry(entry)
    peek.readable, peek.view = True, "序号 单价 总价"
    decision = gate_file(entry, peek, recorder, needs=NEEDS)
    assert decision.decision == "parse"
    assert decision.needs_hit == ["unit_price"]
    assert decision.reason.startswith("text_gate_found_")


def test_nothing_seen_drops_the_file_even_at_high_confidence(tmp_path):
    """No confidence threshold any more: the self-reported number is not a measurement."""
    recorder = Recorder({"kind": "other", "has_price_table": False, "header": "",
                         "found_fields": [], "confidence": 0.99})
    entry = _readable_entry(tmp_path, "开标记录.pdf")
    peek = peek_entry(entry)
    peek.readable, peek.view = True, "一些文字"
    decision = gate_file(entry, peek, recorder, needs=NEEDS)
    assert decision.decision == "skip"
    assert decision.reason == "text_gate_found_none"


def test_empty_needs_never_drops_a_file(tmp_path):
    recorder = Recorder({"kind": "other", "has_price_table": False, "header": "",
                         "found_fields": [], "confidence": 0.9})
    entry = _readable_entry(tmp_path, "任意文件.pdf")
    peek = peek_entry(entry)
    peek.readable, peek.view = True, "一些文字"
    decision = gate_file(entry, peek, recorder, needs=[])
    assert decision.decision == "parse"
    assert decision.reason == "text_gate_no_needs_keep"


def test_compound_name_is_reported_as_target_not_junk():
    assert name_rule("报价一览表、二次报价表、业绩一览表、中小企业声明函、支付表.pdf") == "target"
    assert name_rule("残疾人福利性单位声明函（包1）.docx") == "junk"


def test_pages_spec_is_compact():
    assert pages_to_spec([2, 3, 4, 7]) == "2-4,7"
    assert pages_to_spec(None) == ""
    assert pages_to_spec([9, 1, 2]) == "1-2,9"


def test_runs_are_padded_and_capped():
    covered = {page for run in contiguous_runs([5, 6, 20], pad=1, max_run=8) for page in run}
    assert covered == {4, 5, 6, 7, 19, 20, 21}
    long = contiguous_runs(list(range(1, 40)), pad=1, max_run=8)
    assert all(len(run) <= 8 for run in long)
    assert set().union(*[set(run) for run in long]) >= set(range(1, 40))


def test_service_pages_become_the_shape_steps_five_and_six_read():
    response = {"pages": [{"page_no": 5,
                           "markdown": "分项报价表\n<table><tr><th>货物名称</th><th>单价</th></tr>"
                                       "<tr><td>打印机</td><td>1500</td></tr></table>",
                           "blocks": []}],
                "tables": [], "meta": {}}
    pages = as_pages(response)
    assert pages[0]["page_no"] == 5 and pages[0]["source"] == "vl"
    assert pages[0]["tables"][0]["headers"] == ["货物名称", "单价"]
    assert pages[0]["tables"][0]["rows"] == [["打印机", "1500"]]
    assert "<table>" not in pages[0]["text"] and "分项报价表" in pages[0]["text"]


def test_table_only_page_survives_page_selection(tmp_path):
    from ict.schemas import AttachmentIndex, FileDecision, FileDecisions, IndexedFile, ProjectPlan, ProjectPlans
    from ict.steps.s04_attach import locate_pages

    table_page = {"page_no": 2, "text": "", "tables": [{"table_index": 0, "headers": ["供应商", "总报价"],
                                                       "rows": [["甲公司", "1239629.63"]]}],
                  "chars": 0, "source": "vl"}
    empty_page = {"page_no": 3, "text": "", "tables": [], "chars": 0, "source": "vl"}
    index = AttachmentIndex(announcement_id="x", attachment_directory=str(tmp_path),
                            files=[IndexedFile(file_id="a001", display_name="合同包1：开标记录表.pdf",
                                               relative_path="a.pdf", extension=".pdf",
                                               readability="text_extractable", fmt="pdf")])
    locator = Recorder({})
    decisions = FileDecisions(run_id="r", status="success", file_decisions=[
        FileDecision(file_id="a001", file_class="award_detail", priority=90, read_strategy="target_pages", reason="x")])
    plans = ProjectPlans(run_id="r", status="success", projects=[
        ProjectPlan(project_id="项目|1", package_no="1", has_attachment=True, needs_attachment=True,
                    field_stats={}, missing_fields=[], suspects=[], search_queries=["报价"], status="success")])
    locate_pages("r", plans, decisions, {"a001": [table_page, empty_page]}, index, locator)
    pages = locator.payload["files"][0]["pages"]
    assert [entry["page_no"] for entry in pages] == [2]
    assert pages[0]["table_rows"] == 1 and "甲公司" in pages[0]["row_sample"]
    assert locator.payload["files"][0]["display_name"] == "合同包1：开标记录表.pdf"


def test_search_terms_never_reach_the_model(tmp_path):
    """H1: 第 5 步只看 needs 与页面内容，公告检索词不再进 payload。"""
    from ict.schemas import AttachmentIndex, FileDecision, FileDecisions, IndexedFile, ProjectPlan, ProjectPlans
    from ict.steps.s04_attach import locate_pages

    page = {"page_no": 1, "text": "分项报价表 打印机 1500",
            "tables": [{"table_index": 0, "headers": ["货物名称", "单价"], "rows": [["打印机", "1500"]]}],
            "chars": 12, "source": "vl"}
    index = AttachmentIndex(announcement_id="x", attachment_directory=str(tmp_path),
                            files=[IndexedFile(file_id="a001", display_name="分项报价表.pdf",
                                               relative_path="a.pdf", extension=".pdf",
                                               readability="text_extractable", fmt="pdf")])
    decisions = FileDecisions(run_id="r", status="success", file_decisions=[
        FileDecision(file_id="a001", file_class="bid_quote", priority=90, expected_fields=["unit_price"],
                     possible_packages=["1"], read_strategy="target_pages", reason="x")])
    plans = ProjectPlans(run_id="r", status="success", projects=[
        ProjectPlan(project_id="项目|1", package_no="1", has_attachment=True, needs_attachment=True,
                    field_stats={}, missing_fields=[], suspects=[],
                    needs=[{"field": "unit_price", "package_no": "1", "reason": "maybe_more_objects"}],
                    search_queries=["打印机"], status="success")])
    locator = Recorder({})
    locate_pages("r", plans, decisions, {"a001": [page]}, index, locator)
    assert "search_queries" not in locator.payload
    assert locator.payload["needs"][0]["reason"] == "maybe_more_objects"
    entry = locator.payload["files"][0]
    assert entry["found_fields"] == ["unit_price"] and entry["possible_packages"] == ["1"]


def test_screen_findings_and_package_hint_travel_downstream(tmp_path):
    """H3/H4/H5: 目检看到的字段与文件名包号都要送到第 5、6 步。"""
    from ict.schemas import (AttachmentIndex, IndexedFile, PageDecision, PageDecisions,
                             ProjectPlan, ProjectPlans)
    from ict.steps.s04_attach import extract_attachment_candidates, triage_files

    pdf = tmp_path / "合同包2：报价明细.pdf"
    _blank_pdf(pdf)
    index = AttachmentIndex(announcement_id="x", attachment_directory=str(tmp_path),
                            files=[IndexedFile(file_id="a001", display_name=pdf.name,
                                               relative_path=pdf.name, extension=".pdf",
                                               readability="low_text", fmt="pdf", pages=1)])
    plans = ProjectPlans(run_id="r", status="success", projects=[
        ProjectPlan(project_id="项目|2", package_no="2", has_attachment=True, needs_attachment=True,
                    field_stats={}, missing_fields=[], suspects=[],
                    needs=[{"field": "unit_price", "package_no": "2", "reason": "absent"}], status="success"),
        ProjectPlan(project_id="项目|3", package_no="3", has_attachment=True, needs_attachment=True,
                    field_stats={}, missing_fields=[], suspects=[], status="success")])
    answer = {"kind": "bid_quote", "has_price_table": True, "header": "货物名称|单价",
              "found_fields": ["unit_price"], "confidence": 0.9}
    recorder = Recorder(answer)
    files = triage_files("r", plans, index, recorder)
    decision = files.file_decisions[0]
    assert decision.read_strategy == "target_pages"
    assert decision.expected_fields == ["unit_price"]
    assert decision.possible_packages == ["2"]

    pages = PageDecisions(run_id="r", status="success", page_decisions=[
        PageDecision(file_id="a001", page_no=1, relevance=0.9, package_scope="2",
                     extraction_mode="text_and_table", reason="x")])
    parsed = {"a001": [{"page_no": 1, "text": "打印机 1500", "tables":
                        [{"table_index": 0, "headers": ["货物名称", "单价"], "rows": [["打印机", "1500"]]}],
                        "chars": 9, "source": "vl"}]}
    out = Recorder(answer)
    extract_attachment_candidates("r", "项目", plans, pages, parsed, out, 1,
                                  file_classes={"a001": "bid_quote"},
                                  file_names={"a001": pdf.name},
                                  file_found_fields={"a001": ["unit_price"]})
    context = out.payload["page_contexts"][0]
    assert context["found_fields"] == ["unit_price"]
    assert context["file_name"] == pdf.name


def _extract_with(answer, tmp_path):
    """Run step 6 with a fixed model answer and hand back the recorded package amounts."""
    from ict.schemas import PageDecision, PageDecisions, ProjectPlan, ProjectPlans
    from ict.steps.s04_attach import extract_attachment_candidates

    plans = ProjectPlans(run_id="r", status="success", projects=[
        ProjectPlan(project_id="项目|B", package_no="B", has_attachment=True, needs_attachment=True,
                    field_stats={}, missing_fields=[], suspects=[], status="success")])
    pages = PageDecisions(run_id="r", status="success", page_decisions=[
        PageDecision(file_id="a003", page_no=7, relevance=0.9, package_scope="B",
                     extraction_mode="text_and_table", reason="x")])
    parsed = {"a003": [{"page_no": 7, "text": "B包中标金额：¥1,930,000.00", "tables":
                        [{"table_index": 0, "headers": ["货物名称", "总价"], "rows": [["HIS", "400000"]]}],
                        "chars": 20, "source": "vl"}]}
    got = []
    client = Recorder(answer)
    cands, failures, _, _ = extract_attachment_candidates(
        "r", "项目", plans, pages, parsed, client, 1,
        file_classes={"a003": "bid_quote"}, file_names={"a003": "B包分项报价表.pdf"},
        package_amounts=got)
    return cands, failures, got


def test_step6_records_attachment_package_amounts(tmp_path):
    """A1: 模型抄下来的包级金额有地方落了，带页号与文件名。"""
    answer = {"candidates": [{"entity_type": "cob", "package_no": "B", "file_id": "a003",
                              "fields": {"object_name": "HIS子系统升级", "total_price": "400000.00"}}],
              "package_amounts": [{"package_no": "B", "raw_text": "¥1,930,000.00"}]}
    cands, failures, got = _extract_with(answer, tmp_path)
    assert len(cands) == 1 and not failures
    assert len(got) == 1
    item = got[0]
    assert (item.package_no, item.amount_yuan, item.page_no, item.file_name) == ("B", 1930000.0, 7, "B包分项报价表.pdf")
    assert item.raw_text == "¥1,930,000.00"


def test_step6_keeps_other_package_numbers_as_records(tmp_path):
    """公告没有 Z 包：观测照样留痕（便于复看模型抄了什么），第 8 步查不到它就不会用。"""
    answer = {"candidates": [], "package_amounts": [{"package_no": "Z", "raw_text": "100万"}]}
    _, failures, got = _extract_with(answer, tmp_path)
    assert len(got) == 1 and got[0].package_no == "Z" and not failures


def test_step6_keeps_an_unparseable_amount_visible(tmp_path):
    answer = {"candidates": [], "package_amounts": [{"package_no": "B", "raw_text": "金额见附页"}]}
    _, failures, got = _extract_with(answer, tmp_path)
    assert len(got) == 1 and got[0].amount_yuan is None


def test_step6_rejects_a_malformed_package_amount(tmp_path):
    answer = {"candidates": [], "package_amounts": [{"package_no": "B"}]}
    _, failures, got = _extract_with(answer, tmp_path)
    assert got == [] and [f.failure_code for f in failures] == ["llm_schema_invalid"]


def test_html_table_rows_drop_empty_lines():
    headers, rows = html_to_rows("<table><tr><td>a</td><td></td></tr><tr><td></td><td></td></tr></table>")
    assert headers == ["a", ""] and rows == []


def test_only_the_two_named_words_are_hard_ignored(tmp_path):
    """User rule 2026-10-03: exactly 中小企业声明函 and 残疾人福利, nothing else."""
    from ict.parse.screen import NAME_IGNORE

    for name in ("中小企业声明函（包1）.pdf", "B包中小企业声明函.pdf", "残疾人福利性单位声明函（包1）.docx"):
        assert NAME_IGNORE.search(name), name
    for name in ("监狱企业的证明文件（包1）.docx", "供应商承诺书.pdf", "中小企业信用声明.pdf"):
        assert not NAME_IGNORE.search(name), name

    entry = _entry(tmp_path, "中小企业声明函（包1）.pdf", b"\xd0\xcf\x11\xe0\x00" * 4)
    decision = gate_file(entry, peek_entry(entry), llm=Recorder({}))
    assert decision.decision == "skip"
    assert decision.method == "rule_name_ignore"
    assert decision.reason.endswith("中小企业声明函")


def test_ignored_files_never_reach_the_model(tmp_path):
    recorder = Recorder({"kind": "bid_quote", "has_price_table": True, "header": "x|y", "confidence": 0.9})
    entry = _entry(tmp_path, "残疾人福利性单位声明函.png", PNG)
    decision = gate_file(entry, peek_entry(entry), recorder)
    assert decision.decision == "skip" and decision.method == "rule_name_ignore"
    assert recorder.text_calls == 0 and recorder.seen_images is None

def test_field_definitions_reach_the_prompt_once():
    """One source, one occurrence, and actually present in the assembled prompt."""
    from ict.fields import FIELD_SEMANTICS, STEP_FIELDS
    from ict.llm import load_prompt

    assert "字段含义" not in (Path("src/ict/prompts") / "screen-file-v1.md").read_text(encoding="utf-8")
    for version, (incoming, outgoing) in STEP_FIELDS.items():
        prompt = load_prompt(version)
        assert "字段含义" in prompt, version
        for name in set(incoming) | set(outgoing):
            from ict.fields import IO_NOTES
            if name in FIELD_SEMANTICS or name in IO_NOTES:
                line_head = name + " "
                assert prompt.count("\n" + line_head) <= 1, (version, name)
                assert line_head in prompt, (version, name)


def test_step_fields_names_are_known():
    from ict.fields import FIELD_SEMANTICS, IO_NOTES, STEP_FIELDS

    unknown = []
    for version, (incoming, outgoing) in STEP_FIELDS.items():
        for name in set(incoming) | set(outgoing):
            if name not in FIELD_SEMANTICS and name not in IO_NOTES:
                unknown.append((version, name))
    assert not unknown, unknown