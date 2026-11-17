"""工作进程的编排：领取、成功清理、失败保留、重启自愈。

把 announcement_chain 与入库换成假的，只验流程与状态机，不连库、不调模型。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
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


def _answered(report):
    yield from ()
    return report


def test_claim_then_success_cleans_originals(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job_with_files([("t1", True)])
    worker.ingest.start_job(job)

    claimed = worker.claim_items()
    assert len(claimed) == 1
    assert claimed[0][1]["status"] == "running"
    assert worker.ingest.load_job(job["job_id"])["status"] == "running"

    loaded: list[tuple[str, str]] = []
    monkeypatch.setattr(worker, "announcement_chain",
                        lambda aid, **kwargs: _answered(SimpleNamespace(status="success", failure_summary=[])))
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

    monkeypatch.setattr(worker, "announcement_chain",
                        lambda aid, **kwargs: _answered(SimpleNamespace(status="failed",
                                                              failure_summary=[{"failure_code": "x"}])))
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


@pytest.mark.parametrize("fail_merge", [False, True])
def test_worker_yields_merge_then_resumes_without_early_cleanup(monkeypatch, fail_merge):
    job = _job_with_files([("a", False), ("b", False), ("c", False), ("d", False)])
    worker.ingest.start_job(job)
    incoming = worker.ingest.incoming_dir(job["job_id"])
    b_started = threading.Event()
    loaded, observed = [], []
    lock = threading.Lock()
    live, peak = 0, 0

    def fake_chain(aid, **kwargs):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(live, peak)
        try:
            if aid == "b":
                assert (incoming / "a.html").exists()
                assert "a" not in loaded
                b_started.set()
            def merge():
                if aid == "a":
                    assert b_started.wait(3), "a kept the only normal case slot while merging"
                    saved = worker.ingest.load_job(job["job_id"])
                    observed.append(saved["items"][0]["status"])
                    if fail_merge:
                        raise RuntimeError("merge failed")
                return aid
            assert (yield merge) == aid
            return SimpleNamespace(status="success")
        finally:
            with lock:
                live -= 1

    monkeypatch.setattr(worker, "announcement_chain", fake_chain)
    monkeypatch.setattr(worker, "_load_to_db", lambda path, tenant: loaded.append(path.name))
    monkeypatch.setattr(worker, "concurrency_settings", lambda: {"merge_workers": 1, "resume_workers": 1})
    worker.run_loop(threads=1, poll=0.01, once=True, heal=False, verbose=False)
    saved = worker.ingest.load_job(job["job_id"])
    assert observed == ["running"]
    assert peak == 2 and live == 0  # one normal slot + one merge slot
    assert [item["status"] for item in saved["items"]] == [
        "failed" if fail_merge else "done", "done", "done", "done"]
    assert saved["status"] == ("failed" if fail_merge else "done")
    assert set(loaded) == ({"b", "c", "d"} if fail_merge else {"a", "b", "c", "d"})
    assert (incoming / "a.html").exists() == fail_merge
    assert not (incoming / "b.html").exists()


def test_worker_picks_up_new_job_during_another_jobs_merge(monkeypatch):
    first = _job_with_files([("a", False)])
    worker.ingest.start_job(first)
    b_started = threading.Event()
    created = []

    def fake_chain(aid, **kwargs):
        if aid == "b":
            b_started.set()
        def merge():
            if aid == "a":
                second = _job_with_files([("b", False)])
                worker.ingest.start_job(second)
                created.append(second["job_id"])
                assert b_started.wait(3), "worker waited for the old batch instead of claiming new work"
            return aid
        yield merge
        return SimpleNamespace(status="success")

    monkeypatch.setattr(worker, "announcement_chain", fake_chain)
    monkeypatch.setattr(worker, "_load_to_db", lambda *args: None)
    monkeypatch.setattr(worker, "concurrency_settings", lambda: {"merge_workers": 1, "resume_workers": 1})
    worker.run_loop(threads=1, poll=0.01, once=True, heal=False, verbose=False)
    assert created and worker.ingest.load_job(created[0])["status"] == "done"
    assert worker.ingest.load_job(first["job_id"])["status"] == "done"


def test_worker_claims_only_immediately_startable_fronts(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    job = _job_with_files([(str(i), False) for i in range(5)])
    worker.ingest.start_job(job)
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()

    def chain(aid, **kwargs):
        if int(aid) < 2:
            entered[int(aid)].set()
            assert release.wait(5)
        yield lambda: aid
        return SimpleNamespace(status="success")

    monkeypatch.setattr(worker, "announcement_chain", chain)
    monkeypatch.setattr(worker, "_load_to_db", lambda *args: None)
    monkeypatch.setattr(worker, "concurrency_settings", lambda: {"merge_workers": 12, "resume_workers": 1})
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(worker.run_loop, 2, .01, True, False, False)
        try:
            assert all(e.wait(2) for e in entered)
            with worker._SAVE_LOCK:
                saved = worker.ingest.load_job(job["job_id"])
            assert [i["status"] for i in saved["items"]] == ["running", "running", "waiting", "waiting", "waiting"]
        finally:
            release.set()
            future.result(timeout=5)
    assert worker.ingest.load_job(job["job_id"])["status"] == "done"
