"""工作进程的编排：领取、成功清理、失败保留、重启自愈。

把 run_announcement 与入库换成假的，只验流程与状态机，不连库、不调模型。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("ict_worker", ROOT / "src" / "ict" / "worker.py")
assert _spec and _spec.loader
worker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = worker
_spec.loader.exec_module(worker)


@pytest.fixture(autouse=True)
def _tmp_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ict.config.TENANTS_ROOT", tmp_path / "tenants")
    monkeypatch.setattr("ict.config.JOBS_ROOT", tmp_path / "jobs")


def _job_with_files(items: list[tuple[str, bool]]) -> dict:
    job = worker.ingest.create_job(
        [{"announcement_id": aid, "has_zip": has_zip} for aid, has_zip in items], set())
    incoming = worker.ingest.incoming_dir(job["job_id"])
    for aid, has_zip in items:
        (incoming / f"{aid}.html").write_text("<html></html>", encoding="utf-8")
        if has_zip:
            (incoming / f"{aid}.zip").write_bytes(b"PK")
    return job


def test_claim_then_success_cleans_originals(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job_with_files([("t1", True)])
    worker.ingest.start_job(job)

    claimed = worker.claim_items()
    assert len(claimed) == 1
    assert claimed[0][1]["status"] == "running"
    assert worker.ingest.load_job(job["job_id"])["status"] == "running"

    loaded: list[tuple[str, str]] = []
    monkeypatch.setattr(worker, "run_announcement",
                        lambda aid, **kwargs: SimpleNamespace(status="success", failure_summary=[]))
    monkeypatch.setattr(worker, "_load_to_db", lambda run_dir, tenant: loaded.append((run_dir.name, tenant)))

    worker.process_item(*claimed[0], verbose=False)

    saved = worker.ingest.load_job(job["job_id"])
    assert saved["items"][0]["status"] == "done"
    assert saved["status"] == "done"
    assert loaded == [("t1", "default")]
    incoming = worker.ingest.incoming_dir(job["job_id"])
    assert not (incoming / "t1.html").exists()
    assert not (incoming / "t1.zip").exists()


def test_failed_extraction_keeps_originals(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job_with_files([("t1", True)])
    worker.ingest.start_job(job)
    claimed = worker.claim_items()

    monkeypatch.setattr(worker, "run_announcement",
                        lambda aid, **kwargs: SimpleNamespace(status="failed",
                                                              failure_summary=[{"failure_code": "x"}]))
    called: list[str] = []
    monkeypatch.setattr(worker, "_load_to_db", lambda *a: called.append("db"))

    worker.process_item(*claimed[0], verbose=False)

    saved = worker.ingest.load_job(job["job_id"])
    assert saved["items"][0]["status"] == "failed"
    assert saved["status"] == "failed"
    assert called == []                      # 提取失败就不入库
    incoming = worker.ingest.incoming_dir(job["job_id"])
    assert (incoming / "t1.html").exists()   # 原件保留，等用户重传
    assert (incoming / "t1.zip").exists()


def test_heal_marks_running_as_interrupted() -> None:
    job = _job_with_files([("t1", False)])
    worker.ingest.start_job(job)
    worker.claim_items()

    assert worker.heal_interrupted(verbose=False) == 1
    saved = worker.ingest.load_job(job["job_id"])
    assert saved["items"][0]["status"] == "failed"
    assert "中断" in saved["items"][0]["error"]
    assert saved["status"] == "failed"


def test_close_job_mixed_result_is_failed() -> None:
    job = _job_with_files([("t1", False), ("t2", False)])
    job["items"][0]["status"] = "done"
    job["items"][1]["status"] = "failed"
    worker._close_job(job)
    assert job["status"] == "failed"
