"""Paths and limits fixed by the main-route contract."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_HTML = REPO_ROOT / "data" / "赛题五基准测试数据" / "赛题五.基准测试数据_html"
CATALOG_PATH = REPO_ROOT / "data" / "procurement_catalog_2022.json"
WORK_ROOT = REPO_ROOT / "work"
ATTACHMENTS_ROOT = WORK_ROOT / "attachments"
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


SCREEN_TEXT_CHARS = 3000
SCREEN_THUMB_DPI = 130
SCREEN_THUMB_PAGES = 2
PARSE_PAGE_PAD = 1
PARSE_MAX_PAGES_PER_RUN = 8
PARSE_MAX_PAGES_PER_FILE = 40

# Step 5 and 6 knobs, named so tuning never means editing a call site.
PAGE_CONTEXT_CHARS = 6000          # 第 6 步每页送进模型的正文上限
PAGE_CONTEXT_TABLES = 6            # 第 6 步每页送进模型的表格张数上限
PAGE_LOCATE_LIMIT = 80             # 第 5 步每个文件送进模型的页数上限
DEFAULT_FILE_CLASS = "bid_quote"   # 4b 没能给出文件类型时的兜底类
DEFAULT_SOURCE_PRIORITY = 90       # 兜底类对应的来源优先级
MERGE_THINKING = True              # 第 8 步的同一性判断开思考：内容由模型判，就给它推理空间


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


def parse_settings() -> dict:
    """Parsing service endpoint: heavy work stays off the application machine."""
    load_local_env()
    return {
        "url": os.environ.get("ICT_PARSE_URL", "http://127.0.0.1:6008").rstrip("/"),
        "token": os.environ.get("ICT_PARSE_TOKEN", ""),
        "timeout": float(os.environ.get("ICT_PARSE_TIMEOUT", "600")),
        "run_workers": int(os.environ.get("ICT_PARSE_RUN_WORKERS", "3")),
    }


FILE_CLASSES = {
    "award_detail", "bid_quote", "winner_detail", "tender_requirement", "qualification",
    "contract", "evaluation", "unrelated", "unknown",
}
CLASS_PRIORITY = {
    "award_detail": 100, "bid_quote": 90, "winner_detail": 80, "evaluation": 70,
    "tender_requirement": 40, "qualification": 30, "contract": 30, "unrelated": 30, "unknown": 30,
}
PARSE_RUN_PAGES = 12


# Data-driven name filter, to be filled from a full-corpus study in the optimisation phase.
# Empty means: the model gate decides every file. See eval/主路线流程与过拟合风险.md 2.4.
SCREEN_DROP_KINDS: set[str] = set()
