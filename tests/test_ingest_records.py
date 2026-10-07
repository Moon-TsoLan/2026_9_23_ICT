"""01 页那张表：一行一则公告，四种状态，最近一次尝试优先。"""
from __future__ import annotations

import importlib.util
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("server_ingest", ROOT / "server" / "ingest.py")
assert _spec and _spec.loader
ingest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ingest
_spec.loader.exec_module(ingest)


def job(job_id: str, updated_at: str, items: list[tuple[str, str, str | None]],
        created_at: str | None = None) -> dict:
    return {"job_id": job_id, "created_at": created_at or updated_at, "updated_at": updated_at,
            "items": [{"announcement_id": aid, "status": status, "error": error}
                      for aid, status, error in items]}


def write_run_state(root: Path, aid: str, created_at: str, done_steps: int = 3) -> None:
    directory = root / aid
    directory.mkdir(parents=True, exist_ok=True)
    steps = [{"step": f"s{i}", "status": "success" if i < done_steps else "pending"}
             for i in range(5)]
    (directory / "run_state.json").write_text(
        json.dumps({"created_at": created_at, "current_step": "s1", "steps": steps}),
        encoding="utf-8")


def done_row(aid: str, created_at: str, projects: int = 2, cobs: int = 5) -> dict:
    return {"announcement_id": aid, "title": f"{aid} 标题", "created_at": created_at,
            "projects": projects, "cobs": cobs}


def test_db_only_row_is_done() -> None:
    result = ingest.merge_records([], [done_row("t1", "2026-10-07T10:00:00+08:00")], 1, 20, roots=[])
    assert result["total"] == 1 and result["done_total"] == 1
    item = result["items"][0]
    assert item["status"] == "done" and item["projects"] == 2 and item["cobs"] == 5


def test_waiting_and_failed_come_from_jobs() -> None:
    jobs = [job("j1", "2026-10-07T11:00:00+08:00",
                [("t2", "waiting", None), ("t3", "failed", "RuntimeError: 提取失败")])]
    result = ingest.merge_records(jobs, [], 1, 20, roots=[])
    by_id = {item["announcement_id"]: item for item in result["items"]}
    assert by_id["t2"]["status"] == "waiting" and by_id["t2"]["job_id"] == "j1"
    assert by_id["t3"]["status"] == "failed" and "提取失败" in by_id["t3"]["error"]
    assert by_id["t3"]["projects"] is None      # 失败行不给旧的计数，免得读成"这轮成功了"
    assert result["done_total"] == 0


def test_latest_attempt_wins_over_older_job() -> None:
    jobs = [job("new", "2026-10-07T12:00:00+08:00", [("t1", "failed", "boom")]),
            job("old", "2026-10-07T09:00:00+08:00", [("t1", "waiting", None)])]
    result = ingest.merge_records(jobs, [done_row("t1", "2026-10-06T09:00:00+08:00")], 1, 20, roots=[])
    item = result["items"][0]
    assert item["status"] == "failed" and item["job_id"] == "new"


def test_failed_reattempt_hides_stale_counts() -> None:
    jobs = [job("j1", "2026-10-07T12:00:00+08:00", [("t1", "failed", "boom")])]
    result = ingest.merge_records(jobs, [done_row("t1", "2026-10-06T09:00:00+08:00")], 1, 20, roots=[])
    item = result["items"][0]
    assert item["status"] == "failed" and item["projects"] is None and item["cobs"] is None
    assert item["title"] == "t1 标题"          # 标题仍沿用库里的


def test_done_item_without_db_row_still_shows_done() -> None:
    jobs = [job("j1", "2026-10-07T12:00:00+08:00", [("t9", "done", None)])]
    result = ingest.merge_records(jobs, [], 1, 20, roots=[])
    assert result["items"][0]["status"] == "done"
    assert result["items"][0]["projects"] is None


def test_sorted_newest_first_and_paginated() -> None:
    rows = [done_row(f"t{i}", f"2026-10-0{i}T10:00:00+08:00") for i in range(1, 6)]
    result = ingest.merge_records([], rows, 2, 2, roots=[])
    assert result["total"] == 5 and result["done_total"] == 5
    assert [item["announcement_id"] for item in result["items"]] == ["t3", "t2"]


def test_stale_run_state_is_not_shown_for_waiting_item(tmp_path: Path) -> None:
    """重新上传后，磁盘上还留着上一轮的 run_state —— 不能当成"已完成"展示。"""
    write_run_state(tmp_path, "t1", "2026-10-07T10:00:00+08:00")
    jobs = [job("j1", "2026-10-07T11:00:00+08:00", [("t1", "waiting", None)],
                created_at="2026-10-07T11:00:00+08:00")]
    item = ingest.merge_records(jobs, [], 1, 20, roots=[tmp_path])["items"][0]
    assert item["run_started"] is False
    assert item["progress"] is None


def test_current_run_state_is_used(tmp_path: Path) -> None:
    write_run_state(tmp_path, "t1", "2026-10-07T11:00:05+08:00")
    jobs = [job("j1", "2026-10-07T11:00:05+08:00", [("t1", "running", None)],
                created_at="2026-10-07T11:00:00+08:00")]
    item = ingest.merge_records(jobs, [], 1, 20, roots=[tmp_path])["items"][0]
    assert item["run_started"] is True
    # 分母是整条流程的步数（12），不是"这条 run 已经登记了几步"（夹具里只有 5 步）
    assert item["progress"] == {"done": 3, "total": 12, "current_step": "s1", "status": None}


def test_finished_run_reads_full_marks(tmp_path: Path) -> None:
    """跑完的 run 一律满格：老的 run_state 可能只登记了 11 步。"""
    directory = tmp_path / "t1"
    directory.mkdir(parents=True)
    (directory / "run_state.json").write_text(
        json.dumps({"created_at": "2026-10-07T11:00:05+08:00", "current_step": "persist_and_report",
                    "status": "partial",
                    "steps": [{"step": "s0", "status": "success"}] * 11}), encoding="utf-8")
    jobs = [job("j1", "2026-10-07T11:00:05+08:00", [("t1", "running", None)],
                created_at="2026-10-07T11:00:00+08:00")]
    progress = ingest.merge_records(jobs, [], 1, 20, roots=[tmp_path])["items"][0]["progress"]
    assert progress["done"] == 12 and progress["total"] == 12


def test_job_discardable_only_before_anything_starts() -> None:
    waiting = [job("j1", "2026-10-07T11:00:00+08:00",
                   [("t1", "waiting", None), ("t2", "waiting", None)])]
    items = ingest.merge_records(waiting, [], 1, 20, roots=[])["items"]
    assert all(item["job_discardable"] for item in items)
    assert all(item["job_total"] == 2 for item in items)

    busy = [job("j2", "2026-10-07T12:00:00+08:00",
                [("t3", "waiting", None), ("t4", "running", None)])]
    items = ingest.merge_records(busy, [], 1, 20, roots=[])["items"]
    assert not any(item["job_discardable"] for item in items)
