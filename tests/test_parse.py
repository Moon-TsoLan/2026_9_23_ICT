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
    """Names are evidence for the model, not a veto: conversion cost is accepted instead."""
    entry = _entry(tmp_path, "残疾人福利性单位声明函（包1）.docx", b"\xd0\xcf\x11\xe0\x00" * 4)
    decision = gate_file(entry, peek_entry(entry), llm=None)
    assert decision.decision == "parse"
    assert decision.reason.startswith("needs_normalisation:ole")
    assert decision.name_rule == "junk"


def test_a_picture_still_reaches_the_picture_gate_even_with_a_junk_name(tmp_path):
    entry = _entry(tmp_path, "中小企业声明函.png", PNG)
    recorder = Recorder({"kind": "qualification", "has_price_table": False, "header": "", "confidence": 0.95})
    decision = gate_file(entry, peek_entry(entry), recorder)
    assert decision.method == "image_gate"
    assert decision.decision == "skip"
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
    recorder = Recorder({"kind": "other", "has_price_table": False, "header": "", "confidence": 0.9})
    decision = gate_file(entry, peek, recorder)
    assert decision.method == "docx_media_gate" and decision.decision == "skip"
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


def test_low_confidence_verdict_keeps_the_file(tmp_path):
    entry = _entry(tmp_path, "材料.pdf", b"%PDF-1.7\n")
    peek = peek_entry(entry)
    peek.readable = True
    peek.view = "一些文字"
    recorder = Recorder({"kind": "other", "has_price_table": False, "header": "", "confidence": 0.4})
    decision = gate_file(entry, peek, recorder)
    assert decision.decision == "parse"
    assert decision.reason == "low_confidence_keep"


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


def test_html_table_rows_drop_empty_lines():
    headers, rows = html_to_rows("<table><tr><td>a</td><td></td></tr><tr><td></td><td></td></tr></table>")
    assert headers == ["a", ""] and rows == []
