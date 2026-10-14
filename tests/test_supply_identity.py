from server.supply import supply_graph, supply_id
from server.app import _build_search


def test_self_supply_has_one_company_node_and_two_roles():
    projects = [{"project_id": "p", "project_name": "保险", "package_no": "1", "package_total_amount": 100}]
    winners = [{"project_id": "p", "name": "保险公司"}]
    supplies = [{"project_id": "p", "subject": "保险公司", "identity_kind": "company"}]
    nodes, edges = supply_graph("采购单位", projects, winners, supplies,
                                [{"subject": "保险公司", "identity_kind": "company", "coop_times": 1}])
    companies = [node for node in nodes if node["id"] == "sup:保险公司"]
    assert len(companies) == 1
    assert set(companies[0]["roles"]) == {"中标供应商", "产品供应商"}
    assert any(edge["role"] == "win" and edge["b"] == companies[0]["id"] for edge in edges)
    assert any(edge["role"] == "supply" and edge["b"] == "buyer:采购单位" for edge in edges)


def test_brand_proxy_and_company_with_same_name_remain_distinct():
    assert supply_id("同名", "company") != supply_id("同名", "brand")
    nodes, _ = supply_graph("采购单位", [], [{"project_id": "p", "name": "同名"}],
                            [{"project_id": "p", "subject": "同名", "identity_kind": "brand"}], [])
    assert {node["id"] for node in nodes} >= {"sup:同名", "brand:同名"}


def test_brand_search_is_literal_and_keywords_include_supplier():
    where, params, _ = _build_search(kw="供应方", brand="品牌")
    assert "product_supplier ILIKE" in where
    assert "brand ILIKE" in where and "coalesce(nullif(brand, ''), product_supplier)" not in where
    assert params.count("%供应方%") == 7


def test_supply_edges_aggregate_projects_without_duplicate_lines():
    supplies = [{"project_id": pid, "subject": "企业", "identity_kind": "company"} for pid in ("p1", "p2", "p1")]
    _, edges = supply_graph("采购单位", [], [], supplies, [])
    assert len(edges) == 1
    assert edges[0]["weight"] == 2 and edges[0]["project_ids"] == ["p1", "p2"]
