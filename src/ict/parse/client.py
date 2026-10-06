"""HTTP client for the page-parsing service.

The service returns page structure as JSON; nothing is written to disk on either side.
`as_pages()` converts that response into the shape Steps 5 and 6 already consume, so the
rest of the pipeline does not change when the old markdown-twin reader goes away.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from ict.http import shared_client

TABLE_BLOCK = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)


class ParseError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ParseClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 600.0,
                 connect_timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # How long one document may take is `timeout`; how long we wait to reach the box at all is
        # this. Keeping them equal let one unreachable endpoint block the whole parse queue for
        # ten minutes per file.
        self.connect_timeout = connect_timeout

    def health(self) -> dict:
        response = shared_client("parse").get(self.base_url + "/health", timeout=20)
        response.raise_for_status()
        return response.json()

    def parse(self, path: Path, pages: list[int] | None = None, merge_tables: bool = True,
              return_blocks: bool = True, max_pages: int = 40) -> dict:
        path = Path(path)
        spec = pages_to_spec(pages)
        data = {"merge_tables": str(merge_tables).lower(), "return_blocks": str(return_blocks).lower(),
                "max_pages": str(max_pages)}
        if self.token:
            data["x_parse_token"] = self.token
        if spec:
            data["pages"] = spec
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, "application/octet-stream")}
            try:
                response = shared_client("parse").post(self.base_url + "/parse", files=files,
                                                       data=data,
                                                       timeout=httpx.Timeout(self.timeout,
                                                                             connect=self.connect_timeout))
            except Exception as exc:  # noqa: BLE001
                raise ParseError("parse_unreachable:" + type(exc).__name__) from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("failure"))[:180]
            except Exception:  # noqa: BLE001
                detail = response.text[:180]
            raise ParseError("parse_http_%d:%s" % (response.status_code, detail))
        body = response.json()
        if not body.get("ok"):
            raise ParseError("parse_refused:" + str(body.get("failure"))[:180])
        return body

    def parse_runs(self, path: Path, runs: list[list[int]], workers: int = 3, **kwargs) -> dict:
        """One request per contiguous run, executed concurrently by worker processes remotely."""
        runs = [sorted(set(run)) for run in runs if run]
        if not runs:
            return {"pages": [], "tables": [], "meta": {"parsed_pages": []}}
        if len(runs) == 1:
            return self.parse(path, pages=runs[0], **kwargs)
        collected: list[dict] = []
        tables: list[dict] = []
        warnings: list[str] = []
        started = time.perf_counter()
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(runs)))) as pool:
            futures = {pool.submit(self.parse, path, pages=run, **kwargs): run for run in runs}
            for future in futures:
                try:
                    collected.append(future.result())
                except ParseError as exc:
                    errors.append(str(exc.reason)[:120])
        pages_map: dict[int, dict] = {}
        for response in collected:
            for page in response.get("pages") or []:
                pages_map[int(page["page_no"])] = page
            tables.extend(response.get("tables") or [])
            warnings.extend((response.get("meta") or {}).get("warnings") or [])
        pages = [pages_map[key] for key in sorted(pages_map)]
        if errors:
            warnings.extend("run_failed:" + error for error in errors)
        return {"pages": pages, "tables": sorted(tables, key=lambda item: (item["page_no"], item["index"])),
                "meta": {"parsed_pages": [page["page_no"] for page in pages], "runs": len(runs),
                         "ms": int((time.perf_counter() - started) * 1000), "warnings": warnings}}


def pages_to_spec(pages: list[int] | None) -> str:
    if not pages:
        return ""
    ordered = sorted(set(int(page) for page in pages))
    parts: list[str] = []
    index = 0
    while index < len(ordered):
        low = ordered[index]
        high = low
        while index + 1 < len(ordered) and ordered[index + 1] == high + 1:
            index += 1
            high = ordered[index]
        parts.append(str(low) if low == high else "%d-%d" % (low, high))
        index += 1
    return ",".join(parts)


def contiguous_runs(pages: list[int], pad: int = 1, max_run: int = 8) -> list[list[int]]:
    """Group pages into padded contiguous runs so a split table stays in one request."""
    ordered = sorted(set(int(page) for page in pages))
    if not ordered:
        return []
    groups: list[list[int]] = []
    current = [ordered[0]]
    for page in ordered[1:]:
        if page - current[-1] <= 1 + 2 * pad:
            current.append(page)
        else:
            groups.append(current)
            current = [page]
    groups.append(current)
    runs: list[list[int]] = []
    for group in groups:
        low = max(1, group[0] - pad)
        high = group[-1] + pad
        size = high - low + 1
        step = max_run
        start = low
        while start <= high:
            stop = min(high, start + step - 1)
            runs.append(list(range(start, stop + 1)))
            if stop >= high:
                break
            start = stop + 1 - pad
    merged: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    for run in runs:
        key = (run[0], run[-1])
        if key in seen:
            continue
        seen.add(key)
        merged.append(run)
    return merged


def html_to_rows(html: str) -> tuple[list[str], list[list[str]]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows = [[cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            for row in soup.find_all("tr")]
    rows = [row for row in rows if any(cell for cell in row)]
    headers = rows[0] if rows else []
    return headers, rows[1:]


def as_pages(response: dict) -> list[dict]:
    """Convert the service JSON into the page shape Steps 5 and 6 already read."""
    pages: list[dict] = []
    for page in response.get("pages") or []:
        markdown = page.get("markdown") or ""
        tables = []
        for index, html in enumerate(TABLE_BLOCK.findall(markdown)):
            headers, rows = html_to_rows(html)
            tables.append({"table_index": index, "headers": headers, "rows": rows})
        if not tables:
            for index, item in enumerate(page.get("blocks") or []):
                if item.get("label") == "table" and "<t" in (item.get("content") or "").lower():
                    headers, rows = html_to_rows(item["content"])
                    tables.append({"table_index": index, "headers": headers, "rows": rows})
        text = TABLE_BLOCK.sub(" ", markdown)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        pages.append({"page_no": int(page["page_no"]), "text": text, "tables": tables,
                      "chars": len(text), "source": "vl", "raw_chars": len(markdown)})
    pages.sort(key=lambda item: item["page_no"])
    return pages


def merged_table_rows(pages: list[dict]) -> dict[int, list[dict[str, Any]]]:
    """Group tables by page so a caller can see which tables are adjacent continuations."""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for page in pages:
        grouped.setdefault(page["page_no"], []).extend(page["tables"])
    return grouped
