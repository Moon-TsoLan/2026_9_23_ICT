"""Paths and limits fixed by the main-route contract."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_HTML = REPO_ROOT / "data" / "赛题五基准测试数据" / "赛题五.基准测试数据_html"
CATALOG_PATH = REPO_ROOT / "data" / "procurement_catalog_2022.json"
WORK_ROOT = REPO_ROOT / "work"
ATTACHMENTS_ROOT = WORK_ROOT / "attachments"
ATTACHMENTS_MD_ROOT = WORK_ROOT / "attachments-md"
RUNS_ROOT = WORK_ROOT / "runs"

LOW_TEXT_CHARS_PER_PAGE = 80
MAX_FILES_PER_PROJECT = 20
MAX_PAGES_PER_FILE = 30
MAX_PAGES_PER_ANNOUNCEMENT = 100
FILE_PROBE_PAGES = 3
FILE_TEXT_HEAD = 500
PAGE_TEXT_HEAD = 300
HTML_ROW_CHUNK = 40

COB_FIELDS = (
    "object_name",
    "category_code",
    "category_name",
    "category_type",
    "brand",
    "product_supplier",
    "spec_model",
    "unit_price",
    "quantity",
    "unit",
    "total_price",
)
SUB_FIELDS = ("supplier_name", "score", "is_winner")
OFFICIAL_SEVEN = (
    "object_name",
    "category_name",
    "brand",
    "spec_model",
    "unit_price",
    "quantity",
    "total_price",
)
SOURCE_PRIORITY = {
    "award_detail": 100,
    "bid_quote": 90,
    "winner": 80,
    "cob_detail": 70,
    "sub_score": 70,
    "cob_summary": 60,
    "tender_requirement": 40,
    "other": 30,
}
FILE_CLASS_PRIORITY = ("award_detail", "bid_quote")


def load_local_env() -> None:
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def llm_settings() -> dict[str, str]:
    load_local_env()
    return {
        "base_url": os.environ.get("ICT_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        "api_key": os.environ.get("ICT_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": os.environ.get("ICT_LLM_MODEL", "deepseek-flash"),
    }
