"""Real SQL regression for bounded label payloads; dedicated database only."""
import json
import os
from copy import deepcopy
from uuid import uuid4

import pytest

from server.results import annotate_units
from server.tenant import scoped_read, tenant_scope


@pytest.fixture
def storage():
    dsn = os.environ.get("ICT_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("独立账号测试库未指定")
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(dsn, row_factory=dict_row)
    try:
        assert conn.info.dbname.startswith("ict_account_check_")
        tenant = "label_" + uuid4().hex

        def put(raw, *, owner=tenant, project_id="db-p", number="1"):
            conn.execute(
                "INSERT INTO announcement (tenant_id,announcement_id,raw_json) VALUES (%s,%s,%s)",
                (owner, "same_notice", json.dumps(raw, ensure_ascii=False)))
            conn.execute("""INSERT INTO project
                (project_id,package_key,package_no,announcement_id,project_name,tenant_id)
                VALUES (%s,%s,%s,'same_notice','测试项目',%s)""",
                (project_id, project_id, number, owner))

        captured = []

        def query(sql, params):
            statement, values = scoped_read(sql, params)
            result = conn.execute(statement, values).fetchall()
            captured.extend(result)
            return result

        with tenant_scope(tenant):
            yield put, query, captured
    finally:
        conn.rollback()
        conn.close()


def legacy_query(query):
    def original(_sql, params):
        return query("""SELECT p.project_id,p.package_no,a.raw_json FROM project p
                        JOIN announcement a USING (announcement_id)
                        WHERE p.project_id=ANY(%s)""", params)
    return original


@pytest.mark.parametrize("raw", [
    {},
    {"official_result": None},
    {"package_bindings": {"effective_understanding": {"packages": [
        {"package_no": "1", "package_kind": "contract_package", "package_name": "设备包"}]}}},
    {"official_result": {}, "package_bindings": {"effective_understanding": {"packages": [
        {"package_no": "1", "package_kind": "unpartitioned"}]}}},
    {"official_result": {"schema_version": "announcement-v2"},
     "package_bindings": {"effective_understanding": {"packages": [
         {"package_no": "1", "package_name": "不可回填"}]}}},
    {"project_map": {"db-p": "lot:1"}, "official_result": {"packages": [
        {"package_no": "1", "package_kind": "contract_package", "package_name": "设备包"},
        {"package_no": "lot:1", "package_kind": "lot", "package_name": "第一标段", "source_package_no": "1"}]}},
    {"official_result": {"packages": [
        {"package_no": "1", "package_kind": "unpartitioned"},
        {"package_no": "1", "package_kind": "lot", "package_name": "后一个不能覆盖"}]}},
])
def test_projection_preserves_existing_labels_and_fallbacks(storage, raw):
    put, query, _ = storage
    put(raw)
    rows = [{"project_id": "db-p", "package_no": "1"}]
    expected = annotate_units(deepcopy(rows), legacy_query(query))
    assert annotate_units(deepcopy(rows), query) == expected


def test_large_audit_and_objects_do_not_enter_api_label_payload(storage):
    put, query, captured = storage
    put({"audit": "审计" * 500_000, "official_result": {"packages": [
        {"package_no": "1", "package_kind": "unpartitioned",
         "cobs": [{"spec_model": "大型明细" * 100_000}], "subs": [{"supplier_name": "公司"}]}]}})
    rows = annotate_units([{"project_id": "db-p", "package_no": "1"}], query)
    assert rows[0]["package_label"] == "未分包"
    assert len(json.dumps(captured, ensure_ascii=False).encode()) < 1024
    unit = captured[0]["raw_json"]["official_result"]["packages"][0]
    assert set(unit) == {"package_no", "package_kind", "package_name", "source_package_no"}


def test_projection_keeps_tenant_boundary_with_same_announcement_id(storage):
    put, query, _ = storage
    put({"official_result": {"packages": [{"package_no": "1", "package_name": "本账号"}]}})
    put({"official_result": {"packages": [{"package_no": "1", "package_name": "其它账号"}]}},
        owner="other_" + uuid4().hex, project_id="other-p")
    rows = [{"project_id": "db-p", "package_no": "1"}, {"project_id": "other-p", "package_no": "1"}]
    result = annotate_units(rows, query)
    assert result[0]["package_label"] == "本账号"
    assert "package_label" not in result[1]
