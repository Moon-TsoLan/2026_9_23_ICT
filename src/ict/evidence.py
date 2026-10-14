"""Stable, mechanically assigned source addresses. No content selection happens here."""
from __future__ import annotations


def html_rows(table) -> list[dict]:
    return [{"source_ref": f"html:t{table.table_index}:r{index}", "row_index": index,
             "cells": row} for index, row in enumerate(table.rows)]


def page_segments(file_id: str, page: dict) -> list[dict]:
    """Page text and table rows remain separate observations, not inferred duplicates."""
    root = f"file:{file_id}:p{page['page_no']}"
    out = [{"source_ref": root, "file_id": file_id, "page_no": page["page_no"],
            "text": page.get("text", ""), "table_index": None, "row_index": None}]
    for table_index, table in enumerate(page.get("tables") or []):
        for row_index, row in enumerate(table.get("rows") or []):
            out.append({"source_ref": f"{root}:t{table_index}:r{row_index}",
                        "file_id": file_id, "page_no": page["page_no"],
                        "table_index": table_index, "row_index": row_index,
                        "headers": table.get("headers") or [], "cells": row,
                        "text": " | ".join(str(cell or "") for cell in row)})
    return out


def resolve_context(entry: dict, contexts: list[dict]) -> dict | None:
    """Resolve an exact source; legacy file-only references work only when unique."""
    source_ref = entry.get("source_ref")
    if source_ref:
        for context in contexts:
            if (any(segment["source_ref"] == source_ref for segment in context.get("source_segments", []))
                    and (not entry.get("file_id") or entry["file_id"] == context["file_id"])
                    and (entry.get("page_no") is None or entry["page_no"] == context["page_no"])):
                return context
        return None
    matching = [context for context in contexts
                if (not entry.get("file_id") or entry["file_id"] == context["file_id"])
                and (entry.get("page_no") is None or entry["page_no"] == context["page_no"])]
    return matching[0] if len(matching) == 1 else None
