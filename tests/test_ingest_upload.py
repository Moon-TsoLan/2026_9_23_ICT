"""上传侧：文件名配对、流式落盘、同名跳过、超限拒绝、开始前置检查。

不连数据库；租户目录指到临时目录，避免碰到真实的 var/。
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("server_ingest", ROOT / "server" / "ingest.py")
assert _spec and _spec.loader
ingest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ingest
_spec.loader.exec_module(ingest)


@pytest.fixture(autouse=True)
def _tmp_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ict.config.TENANTS_ROOT", tmp_path / "tenants")
    monkeypatch.setattr("ict.config.JOBS_ROOT", tmp_path / "jobs")


def _job(items: list[dict]) -> dict:
    return ingest.create_job(items, set())


def test_plan_pairs_pairs_by_name() -> None:
    plan = ingest.plan_pairs(["t1.html", "t1.zip", "t2.HTML", "t3.zip", "notes.txt"])
    assert plan.items == [
        {"announcement_id": "t1", "has_html": True, "has_zip": True},
        {"announcement_id": "t2", "has_html": True, "has_zip": False},
    ]
    assert plan.zip_only == [{"announcement_id": "t3", "filename": "t3.zip"}]
    assert plan.invalid == ["notes.txt"]


def test_plan_pairs_strips_directory_parts() -> None:
    plan = ingest.plan_pairs(["../../evil.html", "C:\\tmp\\t9.zip"])
    assert [item["announcement_id"] for item in plan.items] == ["evil"]
    assert plan.zip_only == [{"announcement_id": "t9", "filename": "t9.zip"}]


def test_upload_streams_and_skips_existing() -> None:
    job = _job([{"announcement_id": "t1", "has_zip": True}])
    body = b"<html>hi</html>"
    first = ingest.store_upload(job, "t1.html", io.BytesIO(body))
    assert first == {"announcement_id": "t1", "kind": "html", "skipped": False, "size": len(body)}
    again = ingest.store_upload(job, "t1.html", io.BytesIO(b"ignored"))
    assert again["skipped"] is True
    assert (ingest.incoming_dir(job["job_id"]) / "t1.html").read_bytes() == body


def test_upload_marks_zip_and_checks_before_start() -> None:
    job = _job([{"announcement_id": "t1", "has_zip": True}])
    with pytest.raises(ingest.UploadRejected) as missing:
        ingest.start_job(job)
    assert missing.value.status == 409

    ingest.store_upload(job, "t1.html", io.BytesIO(b"x"))
    ingest.store_upload(job, "t1.zip", io.BytesIO(b"y"))
    started = ingest.start_job(ingest.load_job(job["job_id"]))
    assert started["status"] == "queued"


def test_upload_rejects_oversize_and_cleans_part(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "MAX_ZIP_BYTES", 8)
    job = _job([{"announcement_id": "t1", "has_zip": True}])
    with pytest.raises(ingest.UploadRejected) as exc:
        ingest.store_upload(job, "t1.zip", io.BytesIO(b"0123456789"))
    assert exc.value.status == 413
    assert list(ingest.incoming_dir(job["job_id"]).glob("*")) == []


def test_upload_rejects_unknown_announcement_and_bad_extension() -> None:
    job = _job([{"announcement_id": "t1", "has_zip": False}])
    with pytest.raises(ingest.UploadRejected) as other:
        ingest.store_upload(job, "t2.html", io.BytesIO(b"x"))
    assert other.value.status == 422
    with pytest.raises(ingest.UploadRejected) as ext:
        ingest.store_upload(job, "t1.txt", io.BytesIO(b"x"))
    assert ext.value.status == 422


def test_job_list_is_paginated_and_delete_removes_record() -> None:
    job = _job([{"announcement_id": "t1", "has_zip": False}])
    page = ingest.list_jobs(1, 20)
    assert page["total"] == 1
    assert page["items"][0]["job_id"] == job["job_id"]
    assert page["items"][0]["status"] == "draft"
    ingest.delete_job(job)
    assert ingest.load_job(job["job_id"]) is None
    assert ingest.list_jobs(1, 20)["total"] == 0


def test_delete_allowed_while_nothing_started() -> None:
    """排队中但没有任何一项开跑 → 允许丢弃（这不是"取消提取"）。"""
    job = _job([{"announcement_id": "t1", "has_zip": False}])
    queued = ingest.load_job(job["job_id"])
    queued["status"] = "queued"
    ingest.save_job(queued)

    ingest.delete_job(ingest.load_job(job["job_id"]))
    assert ingest.load_job(job["job_id"]) is None
    assert not ingest.incoming_dir(job["job_id"]).exists()


def test_delete_refused_once_an_item_started() -> None:
    job = _job([{"announcement_id": "t1", "has_zip": False}])
    live = ingest.load_job(job["job_id"])
    live["status"] = "running"
    live["items"][0]["status"] = "running"
    ingest.save_job(live)

    with pytest.raises(ingest.UploadRejected) as exc:
        ingest.delete_job(ingest.load_job(job["job_id"]))
    assert exc.value.status == 409
    assert ingest.load_job(job["job_id"]) is not None
