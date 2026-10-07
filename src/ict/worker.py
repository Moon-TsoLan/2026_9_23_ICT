"""数据接入的工作进程：取 queued 任务 → 解压 → 提取 → 入库 → 删原件。

    python -m ict.worker              常驻，轮询任务
    python -m ict.worker --once       处理一轮就退出（测试/手动跑用）
    python -m ict.worker --threads 2  同时处理的公告数

只跑**一个**进程：LLM 与解析的闸门、任务领取都在进程内，多开会让并发翻倍。
线程数 = 同时处理的公告数（部署基线 4）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 直接 `python src/ict/worker.py` 跑时，Python 会把脚本目录放在 sys.path 最前，
# 于是本目录下的 http.py 会顶掉标准库 http（httpx 会因此炸）。把它摘掉。
_HERE = str(Path(__file__).resolve().parent)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))            # server / db / prep 三个目录
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import psycopg                                                    # noqa: E402
import db.load_runs as load_runs                                  # noqa: E402
import prep.extract_attachments as attachments                    # noqa: E402
from ict.config import DEFAULT_TENANT, concurrency_settings, tenant_paths  # noqa: E402
from ict.pipeline import run_announcement                         # noqa: E402
from server import ingest                                         # noqa: E402

# 任务元数据在同一批里被多个线程改，写文件要串行；入库也要串行（轮次重排要看库内现状）。
_SAVE_LOCK = threading.Lock()
_DB_LOCK = threading.Lock()


def _save(job: dict) -> None:
    with _SAVE_LOCK:
        ingest.save_job(job)


def _step(job: dict, item: dict, name: str) -> None:
    item["current_step"] = name
    _save(job)


def _close_job(job: dict) -> None:
    """所有项都结束了就收口任务状态。"""
    if any(item["status"] in ("waiting", "running") for item in job["items"]):
        return
    job["status"] = "done" if all(item["status"] == "done" for item in job["items"]) else "failed"


def lock_path() -> Path:
    return tenant_paths(DEFAULT_TENANT).jobs / ".worker.lock"


def _pid_alive(pid: int) -> bool:
    """进程是否还在。Windows 上不能用 os.kill(pid, 0) —— 那会真的把它杀掉。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(force: bool = False) -> None:
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        try:
            owner = int(path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            owner = 0
        if owner and _pid_alive(owner):
            raise SystemExit("已经有工作进程在跑（pid=%d，锁文件 %s）。确认它已退出就删掉该文件，或加 --force。"
                             % (owner, path))
        # Stop-Process 是强杀，finally 不会执行 —— 上次留下的锁不该拦住这次启动
        print("[worker] 清掉上次残留的锁（pid=%s 已不在）" % (owner or "?"))
    path.write_text(str(os.getpid()), encoding="utf-8")


def release_lock() -> None:
    lock_path().unlink(missing_ok=True)


def heal_interrupted(verbose: bool = True) -> int:
    """上次进程被杀留下的 running：改成 failed，别让界面永远转圈。"""
    fixed = 0
    for job in ingest.load_all_jobs():
        changed = False
        for item in job["items"]:
            if item["status"] == "running":
                item["status"] = "failed"
                item["current_step"] = None
                item["error"] = "处理中断（工作进程重启）"
                fixed += 1
                changed = True
        if changed:
            _close_job(job)
            _save(job)
    if fixed and verbose:
        print("[worker] 修复 %d 个中断的提取项" % fixed)
    return fixed


def claim_items() -> list[tuple[dict, dict]]:
    """把 queued 任务里 waiting 的项认领成本进程的 running。"""
    claimed: list[tuple[dict, dict]] = []
    for job in ingest.load_all_jobs():
        if job["status"] not in ("queued", "running"):
            continue
        waiting = [item for item in job["items"] if item["status"] == "waiting"]
        if not waiting:
            continue
        for item in waiting:
            item["status"] = "running"
            item["error"] = None
        job["status"] = "running"
        _save(job)
        claimed.extend((job, item) for item in waiting)
    return claimed


def _load_to_db(run_dir: Path, tenant: str) -> None:
    with _DB_LOCK:                     # 轮次重排要读库内现状，同一时刻只能有一笔
        with psycopg.connect(load_runs.dsn_default()) as conn:
            with conn.cursor() as cur:
                load_runs.load_batch(cur, [run_dir], tenant)


def process_item(job: dict, item: dict, verbose: bool = True) -> None:
    aid = item["announcement_id"]
    tenant = job.get("tenant_id") or DEFAULT_TENANT
    paths = tenant_paths(tenant)
    incoming = paths.incoming / job["job_id"]
    html_path = incoming / f"{aid}.html"
    zip_path = incoming / f"{aid}.zip"
    started = time.perf_counter()
    try:
        _step(job, item, "解压附件")
        if zip_path.exists():
            attachments.extract_announcement(zip_path, paths.attachments, force=True)

        _step(job, item, "提取")
        report = run_announcement(aid, html_dir=incoming, attachments_root=paths.attachments,
                                  runs_root=paths.runs)
        if report.status == "failed":
            raise RuntimeError("提取失败：%s" % (report.failure_summary or "未给出原因"))

        _step(job, item, "入库")
        _load_to_db(paths.runs / aid, tenant)

        # 入库成功才删原件（顺序不能颠倒）
        _step(job, item, "清理原件")
        html_path.unlink(missing_ok=True)
        zip_path.unlink(missing_ok=True)
        shutil.rmtree(paths.attachments / aid, ignore_errors=True)

        item["status"] = "done"
        item["error"] = None
    except Exception as exc:            # 单则失败不拖垮整批
        item["status"] = "failed"
        item["error"] = "%s: %s" % (type(exc).__name__, exc)
        if verbose:
            traceback.print_exc()
    finally:
        item["current_step"] = None
        _close_job(job)
        _save(job)
        if verbose:
            print("[worker] %s %s %s（%.1fs）"
                  % (aid, item["status"], item.get("error") or "", time.perf_counter() - started))


def run_loop(threads: int, poll: float, once: bool, heal: bool, verbose: bool) -> None:
    if heal:
        heal_interrupted(verbose)
    while True:
        claimed = claim_items()
        if claimed:
            if verbose:
                print("[worker] 领取 %d 个提取项" % len(claimed))
            if threads <= 1:
                for job, item in claimed:
                    process_item(job, item, verbose)
            else:
                with ThreadPoolExecutor(max_workers=min(threads, len(claimed)),
                                        thread_name_prefix="ict-worker") as pool:
                    list(pool.map(lambda pair: process_item(pair[0], pair[1], verbose), claimed))
        if once:
            return
        time.sleep(poll)


def main() -> None:
    parser = argparse.ArgumentParser(description="ICT 数据接入工作进程")
    parser.add_argument("--threads", type=int, default=None,
                        help="同时处理的公告数（默认取 ICT_ANNOUNCEMENT_WORKERS）")
    parser.add_argument("--poll", type=float, default=3.0, help="轮询间隔（秒）")
    parser.add_argument("--once", action="store_true", help="只处理一轮就退出")
    parser.add_argument("--force", action="store_true", help="忽略已存在的 worker 锁")
    parser.add_argument("--no-heal", action="store_true", help="不修复上次中断的提取项")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    threads = args.threads or int(concurrency_settings()["announcement_workers"])
    verbose = not args.quiet
    acquire_lock(args.force)
    try:
        if verbose:
            paths = tenant_paths()
            print("[worker] threads=%d poll=%.1fs data_root=%s" % (threads, args.poll, paths.root))
            print("[worker] 任务目录 %s" % paths.jobs)
            if not any(paths.jobs.glob("*.json")):
                print("[worker] 注意：这个任务目录下还没有任何任务。"
                      "如果后端用的不是同一个 ICT_DATA_ROOT，两边就对不上，"
                      "任务会一直停在“等待中”。")
        run_loop(threads, args.poll, args.once, not args.no_heal, verbose)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
