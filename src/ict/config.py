"""Paths and limits fixed by the main-route contract."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_HTML = REPO_ROOT / "data" / "赛题五基准测试数据" / "赛题五.基准测试数据_html"
CATALOG_PATH = REPO_ROOT / "data" / "procurement_catalog_2022.json"
WORK_ROOT = REPO_ROOT / "work"
ATTACHMENTS_ROOT = WORK_ROOT / "attachments"
RUNS_ROOT = WORK_ROOT / "runs"

# 运行数据根目录。开发与测试继续用 work/（上面的常量）；上线由进程环境变量
# ICT_DATA_ROOT 指到一个独立盘。和 ICT_LLM_MODE 同一原则：运行位置不放进 .env，
# 免得一份随手改的文件把数据写错地方。
DATA_ROOT = Path(os.environ.get("ICT_DATA_ROOT") or (REPO_ROOT / "var"))
TENANTS_ROOT = DATA_ROOT / "tenants"
JOBS_ROOT = DATA_ROOT / "jobs"
DEFAULT_TENANT = "default"


@dataclass(frozen=True)
class TenantPaths:
    """一个租户的全部运行目录。现在只有一个租户，tenant_id 恒为 default。"""

    tenant_id: str
    root: Path
    incoming: Path          # 上传原件（html + zip）
    attachments: Path       # 解压产物，入库成功后删
    runs: Path              # 提取产物 01–10，保留（01 页要读）
    jobs: Path              # 任务元数据


def tenant_paths(tenant_id: str = DEFAULT_TENANT) -> TenantPaths:
    root = TENANTS_ROOT / tenant_id
    return TenantPaths(
        tenant_id=tenant_id,
        root=root,
        incoming=root / "incoming",
        attachments=root / "attachments",
        runs=root / "runs",
        jobs=JOBS_ROOT / tenant_id,
    )

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
        # `.env` carries endpoints, keys and the tunnel. It must not be able to put the model layer
        # into record/replay, or leave a cassette path behind: those are runtime modes of a test
        # harness, and a stale line in a file would silently fake every answer. Process environment
        # only, so it cannot persist by accident.
        if key.strip() in ENV_FILE_EXCLUDED:
            continue
        os.environ.setdefault(key.strip(), value.strip())


# Read only from the process environment, never from `.env`.
ENV_FILE_EXCLUDED = {"ICT_LLM_MODE", "ICT_CASSETTE", "ICT_REPLAY_LATENCY_MS"}


def llm_settings() -> dict[str, str]:
    load_local_env()
    return {
        "base_url": os.environ.get("ICT_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        "api_key": os.environ.get("ICT_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": os.environ.get("ICT_LLM_MODEL", "deepseek-flash"),
        "timeout": os.environ.get("ICT_LLM_TIMEOUT", "180"),
        # Connecting is a different failure from waiting for an answer: a dead endpoint must not
        # hold a worker (or the GPU queue) for the whole read timeout.
        "connect_timeout": os.environ.get("ICT_LLM_CONNECT_TIMEOUT", "15"),
    }


def parse_settings() -> dict:
    """Parsing service endpoint: heavy work stays off the application machine."""
    load_local_env()
    return {
        "url": os.environ.get("ICT_PARSE_URL", "http://127.0.0.1:6008").rstrip("/"),
        "token": os.environ.get("ICT_PARSE_TOKEN", ""),
        "timeout": float(os.environ.get("ICT_PARSE_TIMEOUT", "600")),
        # 600s is how long one document may take to parse; it is not how long we wait to reach the
        # box. A dead or hung endpoint must fail in seconds, or it blocks the single-document queue.
        "connect_timeout": float(os.environ.get("ICT_PARSE_CONNECT_TIMEOUT", "10")),
        "run_workers": int(os.environ.get("ICT_PARSE_RUN_WORKERS", "3")),
    }


def concurrency_settings() -> dict:
    """Concurrency knobs, defaulted for the 2-core / 4 GB deployment box.

    These bound how many calls may be in flight at once; no judgement reads them. Every one of
    them has the same effect when set to 1 as the serial pipeline had before them.
    """
    load_local_env()
    return {
        # Announcements processed at the same time. Memory, not CPU, is what limits this: each one
        # in flight holds its own parsed pages.
        "announcement_workers": int(os.environ.get("ICT_ANNOUNCEMENT_WORKERS", "4")),
        # Chat calls in flight across every step and every announcement.
        "llm_max_concurrency": int(os.environ.get("ICT_LLM_MAX_CONCURRENCY", "8")),
        "llm_retry_attempts": int(os.environ.get("ICT_LLM_RETRY_ATTEMPTS", "3")),
        "llm_retry_wait": float(os.environ.get("ICT_LLM_RETRY_WAIT", "1")),
        # Work the two cores actually spend time on: sha256, page profiling, thumbnails.
        "local_workers": int(os.environ.get("ICT_LOCAL_HEAVY_WORKERS", "2")),
        # Documents in flight on the GPU box. The original contract was 1; measured on a real
        # 10-announcement set (2026-10-06), 3 is 42% faster end to end than 1, and 3 vs 2 buys
        # another 8% at the same CPU cost. See eval/主路线实现细则.md §2.1 for the table.
        "parse_max_inflight": int(os.environ.get("ICT_PARSE_MAX_INFLIGHT", "3")),
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
