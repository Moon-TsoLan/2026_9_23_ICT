"""Step 4b: one model call per file decides whether that file is worth parsing.

Measured on the gold corpus, so the order below is deliberate:

  native text peek     2-124 ms per file, zero API            ~96% of files
  text gate call       ~0.9 s, ~1.4k tokens in, 26 tokens out  free of GPU
  image gate call      ~1.1 s, ~1.1k tokens in, 27 tokens out  only for scans
  parse service        4.45 s per page on GPU                  only for files the gate keeps

The keep/drop call belongs to the model: a file is kept only when the model names at
least one field from needs (needs_hit non-empty). kind, header and confidence are
recorded for audit only: no threshold on them, and no word list picks the outcome. The
only rule that drops a file by itself is NAME_IGNORE, the two names fixed by the user.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ict.concurrency import LOCAL_GATE
from ict.config import SCREEN_TEXT_CHARS
from ict.llm import LLMError, complete_json
from ict.parse.census import FileEntry
from ict.parse.peek import Peek

KIND_PARSE = {"award_detail", "bid_quote", "winner_detail"}
KINDS = KIND_PARSE | {"tender_requirement", "qualification", "contract", "evaluation", "unrelated", "other"}
# User-specified hard ignores (instruction 2026-10-03). Exactly these two substrings and
# nothing else: the file is dropped before the vision gate and before any parsing.
NAME_IGNORE = re.compile("中小企业声明函|残疾人福利")


NAME_TARGET = re.compile("报价|明细|中标|得分|一览|分项|成交|清单|响应表|应答表")
# Logged as an input feature for the gates. These words never decide anything by themselves:
# the same words appear inside bundled attachments and inside package labels such as 合同包1.
NAME_JUNK = re.compile("声明|承诺|资格|证明|报酬|支付|须知|目录|扫描件|廉洁|告知书|回复")
NAME_AVERSE = re.compile("声明|承诺|通知|协议|资格|证明|报酬|支付|目录|须知|答复|扫描件|下载|廉洁|告知书")


@dataclass
class ScreenDecision:
    file_id: str
    decision: str                       # parse | skip
    kind: str
    confidence: float
    method: str                         # rule | text_gate | image_gate | forced
    reason: str
    name_rule: str = "neutral"
    header: str = ""
    found_fields: list[str] = field(default_factory=list)
    needs_hit: list[str] = field(default_factory=list)
    # Whose document this is, and whether the document itself is an award notice. The gate reads
    # the file name and the first 3000 characters anyway; these only carry that reading forward.
    # Step 8 uses them to tell the winner's quote from a losing bidder's quote.
    quote_supplier: str = ""
    is_award_notice: bool | None = None
    ms: int = 0
    tokens: dict = field(default_factory=dict)

    def short(self) -> dict:
        return {"file_id": self.file_id, "decision": self.decision, "kind": self.kind,
                "confidence": self.confidence, "method": self.method, "reason": self.reason,
                "name_rule": self.name_rule, "header": self.header, "found_fields": self.found_fields,
                "needs_hit": self.needs_hit, "quote_supplier": self.quote_supplier,
                "is_award_notice": self.is_award_notice, "ms": self.ms, "tokens": self.tokens}


def name_rule(name: str) -> str:
    """A hint for logs and for the model prompt, never a decision."""
    if NAME_JUNK.search(name) and not NAME_TARGET.search(name):
        return "junk"
    if NAME_AVERSE.search(name) and not NAME_TARGET.search(name):
        return "averse"
    if NAME_TARGET.search(name):
        return "target"
    return "neutral"


def picture_to_uri(raw: bytes, max_width: int = 1400, quality: int = 75) -> str:
    from PIL import Image

    image = Image.open(io.BytesIO(raw))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if image.width > max_width:
        image = image.resize((max_width, int(image.height * max_width / image.width)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def thumbnails(entry: FileEntry, pages: int = 2, dpi: int = 130, quality: int = 75) -> list[str]:
    """Pictures for the image gate: PDF page renders, or the media inside a thin docx."""
    # Rasterising pages and JPEG-encoding them is real CPU work on a two-core box, so it queues
    # behind the same local gate as the other native readers.
    with LOCAL_GATE:
        return _render_thumbnails(entry, pages, dpi, quality)


def _render_thumbnails(entry: FileEntry, pages: int, dpi: int, quality: int) -> list[str]:
    out: list[str] = []
    path = Path(entry.path)
    if entry.fmt == "docx":
        from ict.parse.peek import docx_media

        import zipfile

        names = docx_media(path)[:pages]
        try:
            with zipfile.ZipFile(path) as archive:
                for name in names:
                    out.append(picture_to_uri(archive.read(name)))
        except Exception:  # noqa: BLE001 - unreadable media means no image gate
            return []
        return out
    try:
        if entry.fmt == "pdf":
            import pymupdf

            document = pymupdf.open(str(path))
            try:
                total = document.page_count
                picks = [0] if total == 1 else sorted({0, max(1, total // 2)})[:pages]
                for index in picks:
                    pix = document[index].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
                    raw = pix.tobytes("jpg", jpg_quality=quality)
                    out.append("data:image/jpeg;base64," + base64.b64encode(raw).decode())
            finally:
                document.close()
        elif entry.fmt == "image":
            from PIL import Image

            with Image.open(path) as image:  # type: ignore[sound]
                image = image.convert("RGB")
                if image.width > 1400:
                    image = image.resize((1400, int(image.height * 1400 / image.width)))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality)
                out.append("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode())
    except Exception:  # noqa: BLE001 - the caller falls back to forced parsing
        return []
    return out


def _ask_text(llm, entry: FileEntry, peek: Peek, counter, needs: list[dict] | None = None) -> tuple[dict, int]:
    payload = {"file_name": entry.name, "format": entry.fmt, "pages": entry.pages,
               "table_headers": peek.table_headers[:3], "needs": needs or [],
               "content_view": peek.view[:SCREEN_TEXT_CHARS]}
    parsed = complete_json(
        llm, step="screen_files", prompt_version="screen-file-v1",
        user=json.dumps(payload, ensure_ascii=False), validate=_validate, counter=counter)
    return parsed, 0


def _ask_images(llm, entry: FileEntry, images: list[str], counter, needs: list[dict] | None = None) -> tuple[dict, int]:
    if llm is None:
        return {}, 0
    from ict.llm import parse_json_object

    try:
        result = llm.complete_with_images(step="screen_images", prompt_version="screen-page-v1",
                                          images=images, user_text=json.dumps({"file_name": entry.name,
                                                                               "needs": needs or []}, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - no vision on this model, or the call failed: keep the file
        return {}, 0
    try:
        return parse_json_object((result.text or "").strip()), 0
    except Exception:  # noqa: BLE001 - unparsable gate keeps the file, never drops it
        return {}, 0


def _validate(parsed: dict) -> str:
    kind = parsed.get("kind")
    if kind not in KINDS:
        return "kind 不在允许值内"
    if parsed.get("has_price_table") is None:
        return "需要 has_price_table"
    found = parsed.get("found_fields")
    if found is not None and not isinstance(found, list):
        return "found_fields 必须是数组"
    conf = parsed.get("confidence")
    if conf is None or not (0 <= float(conf) <= 1):
        return "confidence 必须在 0 到 1"
    notice = parsed.get("is_award_notice")
    if notice is not None and not isinstance(notice, bool):
        return "is_award_notice 必须是 true 或 false"
    supplier = parsed.get("quote_supplier")
    if supplier is not None and not isinstance(supplier, str):
        return "quote_supplier 必须是字符串"
    return ""


def _verdict(decision: ScreenDecision, parsed: dict, method: str, needs: list[dict] | None = None) -> ScreenDecision:
    wanted = [str(item.get("field")) for item in (needs or []) if isinstance(item, dict)]
    seen = [str(field) for field in (parsed.get("found_fields") or []) if field]
    decision.found_fields = seen
    decision.needs_hit = [field for field in wanted if field in seen]
    decision.method = method
    decision.kind = str(parsed.get("kind") or "unknown")
    decision.confidence = float(parsed.get("confidence") or 0)
    decision.header = str(parsed.get("header") or "")
    decision.quote_supplier = str(parsed.get("quote_supplier") or "").strip()[:120]
    noticed = parsed.get("is_award_notice")
    decision.is_award_notice = None if noticed is None else bool(noticed)
    if not wanted:
        # No target list means there is nothing to find: never drop on that basis.
        decision.decision, decision.reason = "parse", "%s_no_needs_keep" % method
        return decision
    decision.decision = "parse" if decision.needs_hit else "skip"
    decision.reason = "%s_found_%s" % (method, "+".join(decision.needs_hit[:3]) if decision.needs_hit else "none")
    return decision


def _try_image_gate(llm, entry: FileEntry, decision: ScreenDecision, counter, method: str,
                    needs: list[dict] | None = None) -> ScreenDecision | None:
    images = thumbnails(entry)
    if not images:
        return None
    parsed, _ = _ask_images(llm, entry, images, counter, needs)
    return _verdict(decision, parsed, method, needs) if parsed else None


def _rule_skip(decision: ScreenDecision, reason: str) -> ScreenDecision:
    decision.decision, decision.method = "skip", "rule"
    decision.kind, decision.confidence = "qualification", 0.95
    decision.reason = reason
    return decision


def gate_file(entry: FileEntry, peek: Peek, llm, counter=None, allow_images: bool = True,
            needs: list[dict] | None = None) -> ScreenDecision:
    """One file in, one keep-or-drop decision out.

    Two names only (see NAME_IGNORE) drop the file outright, per the user's instruction. Every
    other name is handed to the model as evidence and nothing else: a bundled attachment often
    mentions both a quote table and a declaration, and no word list can tell which dominates. Files we can read go to the text gate, files with no text layer
    go to the picture gate, and legacy containers are converted rather than guessed about.
    """
    decision = ScreenDecision(file_id=entry.file_id, decision="parse", kind="unknown",
                              confidence=0.0, method="forced", reason="", name_rule=name_rule(entry.name))

    ignored = NAME_IGNORE.search(entry.name)
    if ignored:
        decision.decision = "skip"
        decision.method = "rule_name_ignore"
        decision.confidence = 1.0
        decision.reason = "rule_name_ignore:" + ignored.group(0)
        return decision

    if entry.needs_normalisation:
        if allow_images and entry.fmt == "image":
            gated = _try_image_gate(llm, entry, decision, counter, "image_gate", needs)
            if gated is not None:
                return gated
        decision.reason = "needs_normalisation:" + entry.fmt + (":" + entry.note if entry.note else "")
        return decision

    if not peek.readable:
        if allow_images and entry.fmt == "pdf":
            gated = _try_image_gate(llm, entry, decision, counter, "scan_gate", needs)
            if gated is not None:
                return gated
        elif allow_images and entry.fmt == "docx" and peek.media_count:
            gated = _try_image_gate(llm, entry, decision, counter, "docx_media_gate", needs)
            if gated is not None:
                return gated
            decision.reason = "docx_media_unreadable"
            return decision
        decision.reason = peek.reason or "unreadable_keep"
        return decision

    if llm is None:
        decision.reason = "llm_unavailable_keep"
        return decision
    try:
        parsed, _ = _ask_text(llm, entry, peek, counter, needs)
    except (LLMError, ValueError):
        decision.reason = "text_gate_failed_keep"
        return decision
    return _verdict(decision, parsed, "text_gate", needs)


def gate_directory(entries: list[FileEntry], llm, counter=None, allow_images: bool = True,
                   needs: list[dict] | None = None) -> tuple[list[Peek], list[ScreenDecision]]:
    from ict.parse.peek import peek_entry

    peeks, decisions = [], []
    for entry in entries:
        peek = peek_entry(entry)
        peeks.append(peek)
        decisions.append(gate_file(entry, peek, llm, counter=counter, allow_images=allow_images, needs=needs))
    return peeks, decisions
