"""Account primitives and tenant-aware filesystem/worker behavior; no live models."""
from pathlib import Path

import pytest

from server import auth, ingest
from server.tenant import TENANT, scoped_read, tenant_scope, validate_tenant


def test_password_salted_hash_and_wrong_password():
    stored = auth.hash_password("比赛账号密码123")
    assert "比赛账号密码123" not in stored
    assert auth.verify_password("比赛账号密码123", stored)
    assert not auth.verify_password("不正确密码", stored)
    assert stored != auth.hash_password("比赛账号密码123")
    assert not auth.verify_password("123456", "bad-format")


def test_business_read_denies_missing_account_and_scopes_nested_cte():
    with pytest.raises(RuntimeError):
        scoped_read("SELECT * FROM announcement")
    with tenant_scope("alice"):
        query, params = scoped_read("WITH mine AS (SELECT * FROM bid) SELECT * FROM mine", (1,))
        assert params == ("alice", "alice", "alice", 1)
        assert "public.announcement WHERE tenant_id=%s" in query
        assert "public.supplier WHERE tenant_id=%s" in query
        assert "v_cob_record AS" in query
        assert "mine AS" in query and query.count("WITH ") == 1
        with pytest.raises(ValueError):
            scoped_read("DELETE FROM announcement")
    assert TENANT.get() is None


@pytest.mark.parametrize("tenant", ["../alice", "a/b", "", "a\\b"])
def test_invalid_tenant_cannot_escape_data_root(tenant):
    with pytest.raises(ValueError):
        validate_tenant(tenant)


def test_jobs_and_runs_are_account_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr("ict.config.TENANTS_ROOT", tmp_path / "tenants")
    monkeypatch.setattr("ict.config.JOBS_ROOT", tmp_path / "jobs")
    monkeypatch.setattr(ingest, "disk_error", lambda path: None)
    with tenant_scope("alice"):
        job = ingest.create_job([{"announcement_id": "same_id", "has_zip": False}], set())
        (ingest.incoming_dir(job["job_id"]) / "same_id.html").write_text("Alice")
        ingest.start_job(job)
        run = ingest.runs_roots()[0] / "same_id"
        run.mkdir(parents=True)
        (run / "run_state.json").write_text('{"status":"alice"}')
    with tenant_scope("bob"):
        assert ingest.load_job(job["job_id"]) is None
        assert ingest.load_all_jobs() == []
        assert ingest.find_run_state("same_id") is None
        assert ingest.load_job("../alice") is None
        other = ingest.create_job([{"announcement_id": "same_id", "has_zip": False}], set())
        assert other["tenant_id"] == "bob"
    assert {j["tenant_id"] for j in ingest.load_all_jobs(all_tenants=True)} == {"alice", "bob"}


def test_single_worker_claims_accounts_and_preserves_owner(tmp_path, monkeypatch):
    from ict import worker
    monkeypatch.setattr("ict.config.TENANTS_ROOT", tmp_path / "tenants")
    monkeypatch.setattr("ict.config.JOBS_ROOT", tmp_path / "jobs")
    monkeypatch.setattr(ingest, "disk_error", lambda path: None)
    for tenant in ["alice", "bob"]:
        with tenant_scope(tenant):
            job = ingest.create_job([{"announcement_id": "same_id", "has_zip": False}], set())
            (ingest.incoming_dir(job["job_id"]) / "same_id.html").write_text(tenant)
            ingest.start_job(job)
    claimed = worker.claim_items()
    assert len(claimed) == 2
    calls = []
    from types import SimpleNamespace
    def fake_run(aid, **kwargs):
        calls.append((kwargs["html_dir"].read_text() if kwargs["html_dir"].is_file() else
                      (kwargs["html_dir"] / f"{aid}.html").read_text(), kwargs["runs_root"].parent.name))
        return SimpleNamespace(status="success")
    monkeypatch.setattr(worker, "run_announcement", fake_run)
    monkeypatch.setattr(worker, "_load_to_db", lambda run, tenant: None)
    for job, item in claimed:
        worker.process_item(job, item, verbose=False)
        assert ingest.load_job(job["job_id"], job["tenant_id"])["status"] == "done"
    assert set(calls) == {("alice", "alice"), ("bob", "bob")}
