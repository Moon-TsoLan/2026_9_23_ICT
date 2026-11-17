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
        self.thinking_seen = None
        self.text_calls = 0
        self.payload = None

    def complete(self, *, step, prompt_version, user, thinking=False):
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


NEEDS = [{"field": "unit_price", "reason": "points_to_attachment", "package_no": "B"}]


def _readable_entry(tmp_path, name):
    entry = _entry(tmp_path, name, b"%PDF-1.7\n")
    return entry


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


def test_html_table_rows_drop_empty_lines():
    headers, rows = html_to_rows("<table><tr><td>a</td><td></td></tr><tr><td></td><td></td></tr></table>")
    assert headers == ["a", ""] and rows == []


def test_field_definitions_reach_the_prompt_once():
    """One source, one occurrence, and actually present in the assembled prompt."""
    from ict.fields import FIELD_SEMANTICS, STEP_FIELDS
    from ict.llm import load_prompt

    assert "字段含义" not in (Path("src/ict/prompts") / "screen-file-v1.md").read_text(encoding="utf-8")
    for version, (incoming, outgoing) in STEP_FIELDS.items():
        prompt = load_prompt(version)
        assert "字段含义" in prompt, version
        for name in set(incoming) | set(outgoing):
            if name in FIELD_SEMANTICS:
                line_head = name + " "
                assert prompt.count("\n" + line_head) <= 1, (version, name)
                assert line_head in prompt, (version, name)


def test_step_fields_names_are_known():
    from ict.fields import FIELD_SEMANTICS, STEP_FIELDS

    unknown = []
    for version, (incoming, outgoing) in STEP_FIELDS.items():
        for name in set(incoming) | set(outgoing):
            if name not in FIELD_SEMANTICS:
                unknown.append((version, name))
    assert not unknown, unknown
