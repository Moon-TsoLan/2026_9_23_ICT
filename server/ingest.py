"""上传 → 建任务 → 落盘。后端只收文件与记状态，**不跑提取**（那是工作进程的事）。

目录（见 doc/全链路改造计划.md §2）：

    <ICT_DATA_ROOT>/tenants/<tenant>/incoming/<job_id>/   上传原件
    <ICT_DATA_ROOT>/jobs/<tenant>/<job_id>.json           任务元数据（全量保留）

任务状态：draft（正在上传）→ queued（等提取）→ running → done / failed。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ict.config import DATA_ROOT, DEFAULT_TENANT, tenant_paths  # noqa: E402
from ict.state import STEP_TOTAL  # noqa: E402

TZ = timezone(timedelta(hours=8))
ID_RE = re.compile(r"^[0-9A-Za-z_][0-9A-Za-z_\-]{0,80}$")
HTML_SUFFIX = ".html"
ZIP_SUFFIX = ".zip"

# 上限（见 doc/全链路改造计划.md §7）：实测 zip 最大 337MB，留一倍余量。
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 512 * 1024 * 1024
MIN_FREE_BYTES = 20 * 1024 ** 3
MIN_FREE_RATIO = 0.15
CHUNK_BYTES = 1024 * 1024


class UploadRejected(Exception):
    """带 HTTP 状态码的拒绝：调用方直接转成 HTTPException。"""

    def __init__(self, status: int, message: str, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


@dataclass
class PairPlan:
    items: list[dict]        # [{announcement_id, has_html, has_zip}]，只含有 html 的公告
    zip_only: list[dict]     # [{announcement_id, filename}]，没有配到 html 的 zip（按决定直接丢弃）
    invalid: list[str]       # 扩展名不对或公告号不合法的文件名


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def plan_pairs(filenames: list[str]) -> PairPlan:
    """只按文件名配对：<公告号>.html ↔ <公告号>.zip，扩展名大小写不敏感。"""
    html: dict[str, str] = {}
    zips: dict[str, str] = {}
    invalid: list[str] = []
    for raw in filenames:
        name = Path(str(raw).replace("\\", "/")).name       # 去掉任何目录成分，防路径穿越
        stem, dot, ext = name.rpartition(".")
        ext = ("." + ext).lower() if dot else ""
        if ext not in (HTML_SUFFIX, ZIP_SUFFIX) or not ID_RE.match(stem):
            invalid.append(name)
            continue
        (html if ext == HTML_SUFFIX else zips)[stem] = name
    items = [{"announcement_id": aid, "has_html": True, "has_zip": aid in zips}
             for aid in sorted(html)]
    zip_only = [{"announcement_id": aid, "filename": zips[aid]}
                for aid in sorted(zips) if aid not in html]
    return PairPlan(items=items, zip_only=zip_only, invalid=invalid)


def job_path(job_id: str) -> Path:
    return tenant_paths(DEFAULT_TENANT).jobs / f"{job_id}.json"


def incoming_dir(job_id: str) -> Path:
    return tenant_paths(DEFAULT_TENANT).incoming / job_id


def load_job(job_id: str) -> dict | None:
    path = job_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_all_jobs() -> list[dict]:
    """全部任务，新→旧。工作进程靠它找 queued。"""
    directory = tenant_paths(DEFAULT_TENANT).jobs
    if not directory.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"), reverse=True)]


def runs_roots(tenant_id: str = DEFAULT_TENANT) -> list[Path]:
    """提取产物只在租户目录下：`tenants/<t>/runs`（工作进程写这里）。

    不再回落到 work/runs：那是开发期批量跑批的产物，正式链路接通后一切都在租户目录里。
    查找顺序只有这一处定义，`/api/runs/{id}` 与列表的 run_started 判断共用它。
    """
    return [tenant_paths(tenant_id).runs]


def find_run_state(announcement_id: str, extra_roots: list[Path] | None = None,
                   tenant_id: str = DEFAULT_TENANT) -> Path | None:
    for root in list(extra_roots or []) + runs_roots(tenant_id):
        path = root / announcement_id / "run_state.json"
        if path.exists():
            return path
    return None


def run_state_of(announcement_id: str, extra_roots: list[Path] | None = None,
                 tenant_id: str = DEFAULT_TENANT) -> dict | None:
    """读某个公告的 run_state.json；没有就是还没跑过。"""
    path = find_run_state(announcement_id, extra_roots, tenant_id)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _progress(announcement_id: str, extra_roots: list[Path] | None,
              tenant_id: str) -> dict | None:
    """从 run_state.json 读提取进度；没有就是还没跑。

    分母是整条流程的步数（`STEP_TOTAL`，12 步），不是"这条 run 已经登记了几步"。
    跑到第 9 步时 `steps` 里只有 9 条，拿它当分母会显示成"第 8/9 步"，用户看不出还剩多少。
    """
    state = run_state_of(announcement_id, extra_roots, tenant_id)
    if state is None:
        return None
    steps = state.get("steps") or []
    finished = [step for step in steps
                if step.get("status") in ("success", "partial", "failed", "skipped")]
    # 已经跑完的 run 一律满格：老 run_state 可能只登记了 11 步（附件链路整条跳过的那种）
    done = STEP_TOTAL if state.get("status") in ("success", "partial", "failed") else len(finished)
    return {"done": done, "total": STEP_TOTAL,
            "current_step": state.get("current_step"), "status": state.get("status")}


def run_is_current(announcement_id: str, since: str | None, extra_roots: list[Path] | None = None,
                   tenant_id: str = DEFAULT_TENANT) -> bool:
    """磁盘上那份 run_state 是不是「这次尝试」产生的。

    重新上传一则提取过的公告时，旧 run_state 还在原地 —— 不比对时间的话，
    界面上"等待中"的行展开会显示上一轮的完整步骤。
    `since` 是本次任务的创建时间；run_state.created_at 晚于它才算本次。
    """
    state = run_state_of(announcement_id, extra_roots, tenant_id)
    if state is None:
        return False
    if not since:
        return True
    return (state.get("created_at") or "") >= since


def job_detail(job: dict) -> dict:
    """任务详情：给每一项附上提取进度（前端画进度条用）。"""
    detail = json.loads(json.dumps(job, ensure_ascii=False))   # 深拷贝，别动内存里那份
    tenant = job.get("tenant_id") or DEFAULT_TENANT
    for item in detail["items"]:
        item["progress"] = _progress(item["announcement_id"], None, tenant)
    return detail


def save_job(job: dict) -> None:
    job["updated_at"] = now()
    path = job_path(job["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def new_job_id() -> str:
    return datetime.now(TZ).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def disk_error(path: Path) -> str | None:
    # 目标目录可能还没建，往上找到第一个存在的祖先再问磁盘。
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    if usage.free < MIN_FREE_BYTES or usage.free < usage.total * MIN_FREE_RATIO:
        return "磁盘余量不足：剩 %.1f GB（要求 ≥ %.0f GB 且 ≥ %.0f%%）" % (
            usage.free / 1e9, MIN_FREE_BYTES / 1e9, MIN_FREE_RATIO * 100)
    return None


def create_job(items: list[dict], overwrite: set[str]) -> dict:
    """建任务并落元数据。items 来自确认过的 precheck 结果。"""
    reason = disk_error(tenant_paths(DEFAULT_TENANT).root)
    if reason:
        raise UploadRejected(507, reason)
    job_id = new_job_id()
    job = {
        "job_id": job_id,
        "tenant_id": DEFAULT_TENANT,
        "created_at": now(),
        "updated_at": now(),
        "status": "draft",
        "items": [
            {
                "announcement_id": item["announcement_id"],
                "has_zip": bool(item.get("has_zip")),
                "overwrite": item["announcement_id"] in overwrite,
                "status": "waiting",
                "current_step": None,
                "error": None,
            }
            for item in items
        ],
    }
    incoming_dir(job_id).mkdir(parents=True, exist_ok=True)
    save_job(job)
    return job


def _item_of(job: dict, announcement_id: str) -> dict | None:
    for item in job["items"]:
        if item["announcement_id"] == announcement_id:
            return item
    return None


def store_upload(job: dict, filename: str | None, stream) -> dict:
    """流式接收一个文件。同名已存在直接跳过（等于断点续传）。"""
    if job["status"] != "draft":
        raise UploadRejected(409, "该任务已经开始提取，不能再上传文件")
    name = Path(str(filename or "").replace("\\", "/")).name
    stem, dot, ext = name.rpartition(".")
    ext = ("." + ext).lower() if dot else ""
    if ext not in (HTML_SUFFIX, ZIP_SUFFIX):
        raise UploadRejected(422, "只接收 .html 与 .zip 文件：%s" % name)
    if not ID_RE.match(stem):
        raise UploadRejected(422, "公告号不合法：%s" % stem)
    item = _item_of(job, stem)
    if item is None:
        raise UploadRejected(422, "该公告不在本次任务里：%s" % stem)

    limit = MAX_HTML_BYTES if ext == HTML_SUFFIX else MAX_ZIP_BYTES
    target = incoming_dir(job["job_id"]) / f"{stem}{ext}"
    if target.exists():
        return {"announcement_id": stem, "kind": ext.lstrip("."), "skipped": True,
                "size": target.stat().st_size}

    reason = disk_error(target)
    if reason:
        raise UploadRejected(507, reason)

    part = target.with_name(target.name + ".part")
    size = 0
    try:
        with part.open("wb") as handle:
            while True:
                chunk = stream.read(CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UploadRejected(413, "文件超过上限（%.0f MB）：%s"
                                         % (limit / 1024 ** 2, name))
                handle.write(chunk)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    if ext == ZIP_SUFFIX:
        item["has_zip"] = True
    save_job(job)
    return {"announcement_id": stem, "kind": ext.lstrip("."), "skipped": False, "size": size}


def missing_files(job: dict) -> list[str]:
    """列出还缺的上传文件。"""
    missing: list[str] = []
    directory = incoming_dir(job["job_id"])
    for item in job["items"]:
        aid = item["announcement_id"]
        if not (directory / f"{aid}{HTML_SUFFIX}").exists():
            missing.append(f"{aid}.html")
        if item["has_zip"] and not (directory / f"{aid}{ZIP_SUFFIX}").exists():
            missing.append(f"{aid}.zip")
    return missing


def start_job(job: dict) -> dict:
    if job["status"] != "draft":
        raise UploadRejected(409, "任务当前状态是 %s，不能重复开始" % job["status"])
    missing = missing_files(job)
    if missing:
        raise UploadRejected(409, "还有 %d 个文件没传完" % len(missing),
                             missing=missing[:20])
    job["status"] = "queued"
    save_job(job)
    return job


def list_jobs(page: int, page_size: int) -> dict:
    directory = tenant_paths(DEFAULT_TENANT).jobs
    files = sorted(directory.glob("*.json"), reverse=True) if directory.exists() else []
    total = len(files)
    start = (page - 1) * page_size
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in files[start:start + page_size]]
    return {"total": total, "page": page, "page_size": page_size,
            "items": [summarize(job) for job in jobs]}


def summarize(job: dict) -> dict:
    """列表里只给一眼能读的字段（用户可见状态只有 4 种）。"""
    counts: dict[str, int] = {}
    for item in job["items"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "job_id": job["job_id"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "status": job["status"],
        "total": len(job["items"]),
        "counts": counts,
    }


def merge_records(jobs: list[dict], done_rows: list[dict], page: int, page_size: int,
                  roots: list[Path] | None = None, tenant_id: str = DEFAULT_TENANT) -> dict:
    """把「进行中的任务项」与「已入库公告」合成一张表：**一行一则公告**，四种状态。

    - 库里有 → 已完成（带项目/标的数）
    - 任务项 waiting / running / failed → 等待中 / 处理中 / 失败（**最近一次尝试优先**：
      同一公告既有旧数据又在重跑，按新的那条显示）
    - 任务项 done 但库里没有（例如库被清过）→ 也算已完成，只是没有计数

    `jobs` 必须从新到旧，这样同一公告只取最新那次尝试。

    `roots` 只给测试用（额外优先查找的运行目录）；线上传 None，走 `runs_roots()` 的
    租户目录 + work/runs 回落。
    """
    rows: dict[str, dict] = {}
    for row in done_rows:
        aid = row["announcement_id"]
        rows[aid] = {
            "announcement_id": aid,
            "title": row.get("title") or aid,
            "status": "done",
            "job_id": None,
            "job_total": None,
            "job_discardable": False,
            "error": None,
            # 已完成的行不用 run_started 门控：它的记录在不在，交给 /api/runs 去答
            # （查得到就展示步骤，查不到就是"没有处理记录"）。
            "run_started": True,
            "progress": None,
            "projects": row.get("projects"),
            "cobs": row.get("cobs"),
            "updated_at": row.get("created_at"),
        }

    seen: set[str] = set()
    for job in jobs:
        for item in job.get("items") or []:
            aid = item["announcement_id"]
            if aid in seen:
                continue
            seen.add(aid)
            state = item.get("status")
            if state == "done":
                if aid not in rows:
                    rows[aid] = {"announcement_id": aid, "title": aid, "status": "done",
                                 "job_id": job["job_id"], "job_total": len(job.get("items") or []),
                                 "job_discardable": False,
                                 "error": None, "progress": None,
                                 "run_started": run_is_current(
                                     aid, job.get("created_at"), roots, tenant_id),
                                 "projects": None, "cobs": None,
                                 "updated_at": job.get("updated_at")}
                continue
            rows[aid] = {
                "announcement_id": aid,
                "title": (rows.get(aid) or {}).get("title") or aid,
                "status": state,                      # waiting / running / failed
                "job_id": job["job_id"],
                "job_total": len(job.get("items") or []),
                # 整条任务都还没开跑才允许丢弃（后端也会再判一次）
                "job_discardable": all(i.get("status") == "waiting" for i in job.get("items") or []),
                "error": item.get("error"),
                "run_started": run_is_current(aid, job.get("created_at"), roots, tenant_id),
                "progress": _progress(aid, roots, tenant_id)
                            if (state == "running"
                                and run_is_current(aid, job.get("created_at"), roots, tenant_id))
                            else None,
                "projects": None,
                "cobs": None,
                "updated_at": job.get("updated_at"),
            }

    ordered = sorted(rows.values(), key=lambda row: row.get("updated_at") or "", reverse=True)
    total = len(ordered)
    done_total = sum(1 for row in ordered if row["status"] == "done")
    start = max(0, (page - 1) * page_size)
    return {"total": total, "done_total": done_total, "page": page, "page_size": page_size,
            "items": ordered[start:start + page_size]}


def delete_job(job: dict) -> None:
    """删掉一条任务记录 + 它上传的原件。

    只在**没有任何一项开始跑**时允许（draft，或 queued 但各项都还是 waiting）。
    只要有一项 running / done / failed，就拒绝 —— 那是"取消提取"，我们没有这东西。
    """
    if any(item["status"] != "waiting" for item in job["items"]):
        raise UploadRejected(409, "任务已经开始提取，不能丢弃")
    shutil.rmtree(incoming_dir(job["job_id"]), ignore_errors=True)
    job_path(job["job_id"]).unlink(missing_ok=True)
