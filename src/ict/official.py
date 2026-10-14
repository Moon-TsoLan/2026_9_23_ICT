"""Official three-sheet exchange. Amounts are yuan until this output boundary."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from ict.packages import package_label


HEADERS = {
    "总表": ["序号", "URL", "UUID", "标段/合同包", "项目名称", "采购单位", "中标供应商", "中标金额"],
    "标的表": ["序号", "UUID", "标段/合同包", "产品服务名称", "品目", "品牌", "规格型号", "数量", "单价", "总价", "类别",
               "单位", "产品供应商", "品目编码", "品目ABC"],
    "投标人表": ["序号", "UUID", "标段/合同包", "名称", "类别", "通过审查", "报价（万元）", "得分", "未中标原因"],
}
OPTIONAL = {"序号", "URL", "单位", "产品供应商", "品目编码", "品目ABC", "得分", "未中标原因"}


def money_wan(value):
    if value is None or value == "":
        return None
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("金额必须为有限数值")
    return float((number / Decimal(10000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def bidder_category(sub):
    if sub.get("review_status") == "failed":
        return "其他"
    return "中标人" if sub.get("is_winner") else "参与人"


def review_label(sub):
    if sub.get("review_status") == "passed":
        return "通过"
    if sub.get("review_status") == "failed":
        reason = sub.get("review_failure_reason")
        return "未通过" + ("，" + reason if reason else "")
    return None


def official_rows(results):
    sheets = {name: [] for name in HEADERS}
    for result in results:
        aid = result["announcement_id"]
        for package in result.get("packages", []):
            label = package_label(package)
            winners = [s for s in package.get("subs", []) if s.get("is_winner") and s.get("review_status") != "failed"]
            for winner in winners or [None]:
                amount = winner.get("supplier_award_amount") if winner else None
                if winner and amount is None and len(winners) == 1:
                    amount = package.get("package_total_amount")
                sheets["总表"].append([None, result.get("url"), aid, label,
                    package.get("project_name") or result.get("project_name"), package.get("purchaser") or result.get("purchaser"),
                    winner.get("supplier_name") if winner else None, money_wan(amount)])
            for cob in package.get("cobs", []):
                sheets["标的表"].append([None, aid, label, cob.get("object_name"), cob.get("category_name"), cob.get("brand"),
                    cob.get("spec_model"), cob.get("quantity"), money_wan(cob.get("unit_price")), money_wan(cob.get("total_price")),
                    None, cob.get("unit"), cob.get("product_supplier"), cob.get("category_code"), cob.get("category_type")])
            for sub in package.get("subs", []):
                sheets["投标人表"].append([None, aid, label, sub.get("supplier_name"), bidder_category(sub), review_label(sub),
                    money_wan(sub.get("bid_amount")), sub.get("score"), None])
    for rows in sheets.values():
        for index, row in enumerate(rows, 1):
            row[0] = index
    return sheets


def export_workbook(results):
    book = Workbook()
    book.remove(book.active)
    for name, rows in official_rows(results).items():
        sheet = book.create_sheet(name)
        headers = HEADERS[name]
        sheet.append(headers)
        for cell in sheet[1]:
            optional = cell.value in OPTIONAL or name == "标的表" and cell.value == "类别"
            cell.fill = PatternFill("solid", fgColor="FFA6A6A6" if optional else "FF1F4E79")
            cell.font = Font(name="等线", color="FFFFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            sheet.column_dimensions[cell.column_letter].width = 22 if cell.value not in ("产品服务名称", "规格型号", "名称") else 38
        sheet.row_dimensions[1].height = 30
        for row in rows:
            sheet.append(row)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                # All extracted strings are literal data, including leading '=' in source text.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                header = headers[cell.column - 1]
                if header in ("中标金额", "单价", "总价", "报价（万元）", "得分"):
                    cell.number_format = "0.00"
                elif header == "数量":
                    cell.number_format = "0.########"
        sheet.freeze_panes = "D2"
        sheet.auto_filter.ref = sheet.dimensions
    buf = BytesIO()
    book.save(buf)
    return buf.getvalue()


def read_gold_workbook(path):
    """Read official gold by headers, ignoring header colors and template explanation/example rows.

    Preserve each data cell's fill independently. Callers apply the user's inference exemption.
    """
    book = load_workbook(path, data_only=True)
    output = {}
    for name, required in HEADERS.items():
        sheet = book[name]
        headers = [cell.value for cell in sheet[1]]
        blue = [h for h in required if h not in OPTIONAL and not (name == "标的表" and h == "类别")]
        if any(h not in headers for h in blue):
            raise ValueError(f"{name} 缺少官方字段")
        records = []
        for row in sheet.iter_rows(min_row=2):
            values = {str(h): row[i].value for i, h in enumerate(headers) if h is not None}
            if values.get("序号") in ("填写说明", "填写示例", "说明", "示例"):
                continue
            if isinstance(values.get("序号"), str) and "区分后面的填写说明" in values["序号"]:
                continue
            if not values.get("UUID"):
                continue
            cells = {str(h): {"value": row[i].value, "inferred": _inference_fill(row[i])}
                     for i, h in enumerate(headers) if h is not None}
            records.append({"row": row[0].row, "values": values, "cells": cells})
        output[name] = records
    return output


def _inference_fill(cell):
    if cell.fill.patternType != "solid":
        return False
    color = cell.fill.fgColor
    if color.type == "rgb" and color.rgb[-6:].upper() == "FFFFFF":
        return False
    if color.type == "theme" and color.theme == 0 and cell.fill.fgColor.tint == 0:
        return False
    return True


def score_gold_cell(gold, extracted):
    """User policy: colored cells accept their value or blank; plain blanks reject over-extraction."""
    expected = gold["value"]
    empty = extracted is None or extracted == ""
    expected_empty = expected is None or expected == ""
    if gold.get("inferred") and empty:
        return True
    if expected_empty or empty:
        return expected_empty and empty
    try:
        left, right = Decimal(str(expected)), Decimal(str(extracted))
        return left.is_finite() and right.is_finite() and left == right
    except InvalidOperation:
        import unicodedata
        norm = lambda value: "".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()
        return norm(expected) == norm(extracted)
