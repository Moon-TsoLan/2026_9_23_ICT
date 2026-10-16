"""Fixed database answers; never opens a database or extraction/OCR endpoint."""
import pytest
from fastapi import HTTPException
from server import explore


def project(number):
    return {"project_id": str(number), "project_name": "项目", "package_no": str(number),
            "package_label": "设备采购包", "package_total_amount": 100, "purchaser": "采购单位"}


def database(monkeypatch, *, companies=None, projects=(), parties=(), total=None, roles=()):
    calls = []

    def query(sql, params=()):
        calls.append((sql, params))
        if "max(created_at)" in sql:
            return [{"n": 50, "ts": None}]
        if "explore:company" in sql:
            return companies or []
        if "explore:project_parties" in sql:
            return list(parties)
        if "explore:project */" in sql:
            return list(projects)
        if "explore:total" in sql:
            return [{"n": len(projects) if total is None else total}]
        if "explore:projects" in sql:
            offset, limit = params[-1], params[-2]
            return list(projects)[offset:offset + limit]
        if "explore:roles" in sql:
            return list(roles)
        raise AssertionError(sql)
    monkeypatch.setattr(explore, "q", query)
    return calls


def test_low_frequency_supplier_loads_without_overview_membership(monkeypatch):
    calls = database(monkeypatch, companies=[{"name": "低频公司", "bids": 1, "has_win": True}],
                     projects=[project(1)], roles=[{"project_id": "1", "role": "win"}])
    result = explore.neighborhood("sup:低频公司", expected_version=explore.data_version())
    assert result["node"]["kind"] == "winner"
    assert result["graph"]["edges"] == [{"a": "proj:1", "b": "sup:低频公司", "role": "win", "weight": 1}]
    assert result["pagination"]["total"] == 1
    assert not any("overview" in sql or "min_bids" in sql for sql, _ in calls)


def test_neighborhood_pagination_does_not_claim_complete(monkeypatch):
    database(monkeypatch, companies=[{"name": "公司", "bids": 3, "has_win": True}],
             projects=[project(i) for i in range(3)], roles=[{"project_id": "1", "role": "bid"}])
    result = explore.neighborhood("sup:公司", offset=1, limit=1)
    assert [n["id"] for n in result["graph"]["nodes"]] == ["sup:公司", "proj:1"]
    assert result["pagination"] == {"offset": 1, "limit": 1, "total": 3, "has_more": True}


def test_company_identity_reuses_supplier_and_keeps_multiple_roles(monkeypatch):
    database(monkeypatch, companies=[{"name": "注册全称", "bids": 1, "has_win": True}], projects=[project(1)],
             roles=[{"project_id": "1", "role": "win"}, {"project_id": "1", "role": "supply"}])
    result = explore.neighborhood("vendor:原文别名")
    assert result["node"]["id"] == "sup:注册全称"
    assert {e["role"] for e in result["graph"]["edges"]} == {"win", "supply"}


@pytest.mark.parametrize("prefix,identity", [("vendor", "company"), ("brand", "brand")])
def test_product_supplier_and_brand_are_distinct_identities(monkeypatch, prefix, identity):
    database(monkeypatch, projects=[project(1)])
    result = explore.neighborhood(prefix + ":同名")
    assert result["node"]["id"] == prefix + ":同名"
    assert result["node"]["identity_kind"] == identity
    assert result["graph"]["edges"][0]["role"] == "supply"


def test_project_loads_long_tail_parties_and_paginate_identity_not_roles(monkeypatch):
    rows = [{"id": "sup:A", "name": "A", "identity_kind": "company", "kind": "winner", "role": r}
            for r in ("win", "supply")]
    rows += [{"id": "sup:B", "name": "B", "identity_kind": "company", "kind": "bidder", "role": "bid"}]
    database(monkeypatch, projects=[project(1)], parties=rows)
    first = explore.neighborhood("proj:1", limit=1)
    assert first["pagination"]["total"] == 2 and first["pagination"]["has_more"]
    assert {e["role"] for e in first["graph"]["edges"]} == {"win", "supply", "buy"}
    second = explore.neighborhood("proj:1", limit=1, offset=1)
    assert any(n["id"] == "sup:B" for n in second["graph"]["nodes"])
    assert not second["pagination"]["has_more"]


def test_unknown_subject_is_not_returned_as_fabricated_node(monkeypatch):
    database(monkeypatch)
    with pytest.raises(HTTPException) as error:
        explore.neighborhood("sup:不存在")
    assert error.value.status_code == 404


def test_version_mismatch_prevents_loading(monkeypatch):
    calls = database(monkeypatch)
    with pytest.raises(HTTPException) as error:
        explore.neighborhood("sup:A", expected_version="49:")
    assert error.value.status_code == 409 and len(calls) == 1


def test_version_change_during_query_rejects_whole_result(monkeypatch):
    monkeypatch.setattr(explore, "q", lambda sql, params=():
        [{"n": 1}] if "explore:total" in sql else [] if "explore:company" in sql else [project(1)])
    versions = iter(["old", "new"])
    monkeypatch.setattr(explore, "data_version", lambda: next(versions))
    with pytest.raises(HTTPException) as error:
        explore.neighborhood("vendor:公司")
    assert error.value.status_code == 409
