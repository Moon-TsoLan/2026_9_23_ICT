"""Amount, price, and quantity parsing. Neighbor cells never change this cell's unit."""

from __future__ import annotations

import re
from typing import Any

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# A percent sign makes the text a rate rather than an amount. This is the only semantic
# guard in this module: "折扣率：96.60%" once became a package total of 96.6 yuan. Every
# other wording judgement (预算/限价/保证金/折扣) stays with the model, per AGENTS.md.
_PERCENT = re.compile(r"[%％]")
_QTY = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:[\(（]\s*([^)）]+)\s*[\)）]|([^\d\s,，;；]+))?\s*$"
)


def parse_amount(raw: str | None) -> tuple[float | None, bool]:
    """Return yuan and whether the text itself said 万元. A percentage is not an amount."""
    if raw is None:
        return None, False
    text = str(raw).strip()
    if not text:
        return None, False
    wan = "万元" in text
    if _PERCENT.search(text):
        return None, wan
    negative = text.startswith("(") or text.startswith("（") or text.startswith("-")
    match = _NUMBER.search(text.replace(",", "").replace("，", ""))
    if not match:
        return None, wan
    value = float(match.group())
    if negative and value > 0:
        value = -value
    if wan:
        # Amounts are quoted to the cent at most, so six decimals is pure float cleanup:
        # 0.28 * 10000 lands on 2800.0000000000005 otherwise.
        value = round(value * 10000, 6)
    return value, wan


def parse_price_cell(raw: Any, column_unit: str | None = None) -> dict[str, Any]:
    """Normalize one price cell. column_unit is this column's own header, not a neighbor."""
    text = "" if raw is None else str(raw).strip()
    unit_text = text
    if column_unit and "万元" in column_unit and "万元" not in text and "元" not in text:
        unit_text = f"{text}万元"
    ambiguous = ("万" in unit_text) and ("万元" not in unit_text)
    value, wan = parse_amount(unit_text)
    if ambiguous:
        value = None
    return {
        "value": value,
        "multiplied_by_10000": wan and value is not None,
        "ambiguous": ambiguous,
        "invalid": value is None and bool(text) and text not in {"无", "详见附件", "-", "/"},
    }


def parse_quantity(raw: Any) -> tuple[float | None, str | None, bool]:
    """Split '52块' or '1(套)'. Combined cells fail closed."""
    if raw is None:
        return None, None, False
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw), None, True
    text = str(raw).strip()
    if not text or text in {"无", "-", "/"}:
        return None, None, False
    if any(sep in text for sep in (";", "；", "、")):
        return None, None, False
    match = _QTY.match(text.replace(",", "").replace("，", ""))
    if not match:
        return None, None, False
    quantity = float(match.group(1))
    unit = match.group(2) or match.group(3)
    return quantity, (unit.strip() if unit else None), True
