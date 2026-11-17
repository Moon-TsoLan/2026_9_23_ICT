"""One model decision per visible page; no file verdict or continuation review."""
import json
import re
from ict.llm import complete_json
from ict.config import FILE_CLASSES
from ict.fields import FRONT_ENUMS

NAME_IGNORE = re.compile("中小企业声明函|残疾人福利")


def inspect_page(llm, context, needs, counter=None):
    def validate(value):
        action = value.get("action")
        if action not in FRONT_ENUMS["action"]:
            return f"action原值={action!r}；允许值={FRONT_ENUMS['action']}"
        if action == "extract" and not any(segment["text"].strip() for segment in context["source_segments"]):
            return "没有原生文本的图像不能选 extract；需要内容时选择 ocr"
        if not isinstance(value.get("kind"), str) or value["kind"] not in FRONT_ENUMS["kind"]:
            return f"kind原值={value.get('kind')!r}；允许值={FRONT_ENUMS['kind']}"
        confidence = value.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            return "confidence 必须为0到1的数字，仅供审计"
        if not isinstance(value.get("reason"), str) or not value["reason"].strip():
            return "需要 reason"
        return ""

    notes = {}
    payload = {key: value for key, value in context.items() if key != "images"}
    payload["needs"] = needs
    try:
        answer = complete_json(llm, step="inspect_attachment_page", prompt_version="screen-page-v9",
            user=json.dumps(payload, ensure_ascii=False), images=context["images"], validate=validate,
            counter=counter, notes=notes)
    except (ValueError, NotImplementedError, RuntimeError) as exc:
        exc.page_audit = notes
        raise
    return answer, notes
