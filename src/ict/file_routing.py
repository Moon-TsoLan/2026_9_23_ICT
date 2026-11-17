"""Local attachment reads and two-pass file routing. No network calls."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pymupdf
from PIL import Image

from ict.attachment_pages import office_pdf
from ict.documents import read_native
from ict.parse.census import sniff


@dataclass
class Content:
    number: int
    text: str
    has_image: bool = False
    kind: str = "pdf_page"
    tables: list = field(default_factory=list)
    sheet: str | None = None


@dataclass
class Document:
    path: Path
    parts: list[Content] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def read_file(path: Path) -> Document:
    doc = Document(path)
    try:
        fmt, note = sniff(path)
        if fmt == "pdf":
            with pymupdf.open(path) as pdf:
                if pdf.needs_pass:
                    raise ValueError("encrypted_pdf")
                for index in range(len(pdf)):
                    try:
                        page = pdf[index]
                        text = page.get_text(sort=True)
                        images = bool(page.get_images() or page.get_image_info())
                        if text.strip() or images or page.get_drawings():
                            doc.parts.append(Content(index + 1, text, images or not text.strip()))
                    except (RuntimeError, ValueError, OSError) as exc:
                        doc.errors.append(f"page {index + 1}: {exc}")
        elif fmt == "ole":
            with TemporaryDirectory() as tmp:
                converted = read_file(office_pdf(path, tmp))
                doc.parts, doc.errors = converted.parts, converted.errors
        elif fmt == "image":
            with Image.open(path) as img:
                for index in range(getattr(img, "n_frames", 1)):
                    img.seek(index)
                    img.load()
                    doc.parts.append(Content(index + 1, "", True, "image_frame"))
        elif fmt in {"docx", "xlsx", "text"} or path.suffix.lower() == ".txt":
            if fmt == "text":
                from bs4 import BeautifulSoup
                from ict.documents import read_text
                raw = read_text(path)
                if raw.lstrip().startswith("<"):
                    raw = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
                    if re.search("系统正在维护|验证码|访问受限|访问被拒绝|Access Denied", raw, re.I):
                        raise ValueError("download_error_page")
                    if not raw.strip():
                        raise ValueError("empty_document")
                    doc.parts.append(Content(1, raw, kind="native_segment"))
                    return doc
            rows = read_native(path, "text" if fmt == "unknown" else fmt)
            spreadsheet_images = False
            if fmt == "xlsx":
                with ZipFile(path) as book:
                    spreadsheet_images = any(k.startswith("xl/media/") for k in book.namelist())
            for row in rows:
                images = spreadsheet_images or any(b.get("images") for b in row.get("blocks", []))
                if row["text"].strip() or images:
                    doc.parts.append(Content(row["page_no"], row["text"], images,
                                             row.get("coordinate_kind", "native_segment"),
                                             row["tables"], row.get("sheet_name")))
        else:
            raise ValueError(note if note in {"broken_zip", "unreadable"} else "unsupported_or_unexpanded:" + fmt)
    except Exception as exc:
        # One failed attachment must not discard other members of the same announcement.
        doc.errors.append(f"{type(exc).__name__}: {exc}")
    if not doc.parts and not doc.errors:
        doc.errors.append("empty_document")
    return doc


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


TENDER = re.compile(r"(?:公开招标|招标|竞争性磋商|磋商|谈判|采购|询价)文件")
QUOTE = re.compile(r"分项报价|报价明细|最后报价|最终报价|开标一览|开标.{0,2}报价.{0,2}一览")
REVIEW = re.compile(r"评审情况表|评审报告|得分排序表|评审结果|中标结果公告|成交结果公告|中标公告|成交公告|中标通知书")
ENTITY = re.compile(r"[\u3400-\u9fffA-Za-z0-9()·]{2,}(?:公司|研究院|事务所|合作社|服务中心|经营部|商行|厂)")
PROVIDER = re.compile(r"(?:制造商|生产厂|承建(?:\(承接\))?企业|承接企业)(?:为|是|名称[:：])\(?[^。;；\n]{0,90}?(?:公司|厂|研究院|合作社|事务所)")
MONEY = re.compile(r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+(?:万)?元|(?:总价|合计)\(元\)[:：]?\d+)")
PLACEHOLDER = re.compile(r"\{供应商响应\}|\{供应商[^{}]*响应\}|由供应商填写|自行填写|供应商自行编写")
AUXILIARY = re.compile(r"声明函|劳务报酬|专家费|资格证明|业绩一览|承诺函|履约业绩")


def classify(doc: Document, ocr: dict[int, str] | None = None) -> dict:
    """Classify a whole file. The caller can supply recognized text for a second pass."""
    def decision(route, reason, part=None):
        return {"route": route, "reason": reason, "part": part,
                "errors": list(doc.errors)}

    if not doc.parts:
        reason = ("unsupported_or_unexpanded" if any("unsupported_or_unexpanded:" in e for e in doc.errors)
                  else "source_unreadable")
        return decision("unavailable", reason)
    name = compact(doc.path.stem)
    parts = [(p, compact(p.text + "\n" + (ocr or {}).get(p.number, ""))) for p in doc.parts]
    text = "\n".join(s for _, s in parts)
    cover = parts[0][1]
    tender_cover = TENDER.search(name) or TENDER.search(cover)
    solicitation = bool(tender_cover and re.search(r"投标人须知|供应商须知|评标办法|采购需求|资格审查", text))
    reference = None
    for part, section in parts:
        if not section:
            continue
        # A solicitation can reproduce a filled illustrative table. Its invitation and
        # form headings must not qualify as results simply because they mention prices.
        result_title = REVIEW.search(section) or REVIEW.search(name)
        if result_title and ENTITY.search(section) and re.search(r"中标人|成交供应商|中标供应商|中标金额|中标成交|成交金额|评审总得分|候选人|符合性审查", section):
            if not solicitation or REVIEW.search(name):
                return decision("direct", "filled_award_or_review", part.number)
        if PROVIDER.search(section) and re.search(r"标的|采购活动|属于|产品", section) and not solicitation:
            return decision("direct", "named_provider", part.number)
        quote = QUOTE.search(name) or QUOTE.search(section)
        if quote and not solicitation and not PLACEHOLDER.search(section):
            if MONEY.search(section) and re.search(r"合价|总价|响应报价|单价|最后报价|小写|合计", section):
                if not re.search("投标人须知|包最高限价", section) and (
                    ENTITY.search(section) or ENTITY.search(name) or re.search("品牌|规格型号|人民币大写|分项名称|产品名称", section)
                    or re.search(r"投标人名称[^。\n:：]*[:：]" + ENTITY.pattern, text)
                ):
                    return decision("direct", "filled_quotation", part.number)
        if (PLACEHOLDER.search(section) or "招标工程量清单" in section or
                ("响应报价" in section and "{供应商" in section and "响应}" in section)):
            reference = part.number
    if solicitation or reference is not None:
        return decision("reference", "procurement_document_or_form", reference)
    if any(p.has_image and not (ocr or {}).get(p.number, "").strip() for p, _ in parts):
        return decision("needs_ocr" if ocr is None else "undetermined", "unread_image_content")
    if AUXILIARY.search(text) and (AUXILIARY.search(name) or AUXILIARY.search(cover)):
        return decision("irrelevant", "ancillary_document")
    return decision("undetermined", "insufficient_evidence")

