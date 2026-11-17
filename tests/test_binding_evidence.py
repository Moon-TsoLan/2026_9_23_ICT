import json

from ict.bindings import bind_packages
from ict.evidence import resolve_context
from ict.html_context import parse_notice
from ict.llm import LLMResult
from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding, Amount, HtmlTables, ProjectPlans, ProjectPlan, FileDecisions, FileDecision
from test_rules import _cob


class Binder:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def complete(self, *, step, prompt_version, user, thinking=False):
        self.calls += 1
        return LLMResult(json.dumps(self.answer, ensure_ascii=False), "fake", "fixed", prompt_version, 0)


def fixture(tmp_path):
    path = tmp_path / "notice.html"
    path.write_text('<html><body><table><tr><th>标的名称</th><th>供应商</th><th>包号</th></tr>'
                    '<tr><td></td><td>同一公司</td><td>2</td></tr>'
                    '<tr><td></td><td>同一公司</td><td>1</td></tr></table></body></html>', encoding="utf-8")
    notice = parse_notice(path)
    understanding = AnnouncementUnderstanding(run_id="x", status="success", project_name="项目", package_mode="multi",
        announcement_type="winning_announcement", packages=[PackageUnderstanding(package_no=no, project_id=f"项目|{no}",
        package_evidence_text=no, package_amount=Amount(raw_text=str(amount), amount_yuan=amount, scope="package"))
        for no, amount in [("1", 100), ("2", 200)]])
    return notice, understanding


def test_unnamed_rows_keep_their_supplier_and_distinct_source_refs(tmp_path):
    notice, _ = fixture(tmp_path)
    table = notice.tables[0]
    rows = [{"fields": {"quantity": 1, "total_price": 100}, "source_refs": [f"html:t{table.table_index}:r0"]},
            {"fields": {"quantity": 2, "total_price": 200}, "source_refs": [f"html:t{table.table_index}:r1"]}]
    from ict.observations import seal_observations
    segments = [item for item in notice.source_contexts.values() if item.get('table_index') == 0
                and item.get('row_index') in (1, 2)]
    rows = [{'entity_type': 'cob', 'source_ref': segment['source_ref'], 'source_class': 'cob_detail',
             'bidder_supplier': '同一公司', 'fields': {'quantity': index + 1, 'total_price': (index + 1) * 100}}
            for index, segment in enumerate(segments)]
    candidates, _ = seal_observations(rows, [{'file_id':None, 'page_no':None, 'source_type':'html', 'source_segments':segments}], '项目', 1)
    assert [row.evidence.row_index for row in candidates] == [1, 2]
    assert all(row.evidence.bidder_supplier == '同一公司' for row in candidates)
    assert all(row.fields['object_name'].raw_value is None for row in candidates)
    assert candidates[0].evidence.source_refs != candidates[1].evidence.source_refs


def test_known_wrong_assignments_and_amounts_can_be_corrected_together(tmp_path):
    notice, understanding = fixture(tmp_path)
    candidates = [_cob("a", "html", 100, {"object_name": "服务甲"}), _cob("b", "html", 100, {"object_name": "服务乙"})]
    model = Binder({"bindings": [{"candidate_id": "a", "package_no": "2", "source_refs": ["html:t0:r0"], "reason": "行内包2"},
                                  {"candidate_id": "b", "package_no": "1", "source_refs": ["html:t0:r1"], "reason": "行内包1"}],
                    "amount_bindings": [{"package_no": "1", "amount_ref": "amount:package:2", "amount_kind": "award", "reason": "包1原文"},
                                        {"package_no": "2", "amount_ref": "amount:package:1", "amount_kind": "award", "reason": "包2原文"}]})
    audit, errors = bind_packages(understanding, candidates, notice, HtmlTables(run_id="x", status="success"), model)
    assert not errors and not audit["unresolved"]
    assert [row.package_no for row in candidates] == ["2", "1"]
    assert [package.package_amount.amount_yuan for package in understanding.packages] == [200, 100]


def test_incomplete_binding_answer_does_not_partially_modify_candidates(tmp_path):
    notice, understanding = fixture(tmp_path)
    candidates = [_cob("a", "html", 100, {"object_name": "服务甲"}), _cob("b", "html", 100, {"object_name": "服务乙"})]
    model = Binder({"bindings": [{"candidate_id": "a", "package_no": "2", "reason": "只返回一条"}], "amount_bindings": []})
    _, errors = bind_packages(understanding, candidates, notice, HtmlTables(run_id="x", status="success"), model)
    assert errors and model.calls == 2
    assert all(row.package_no == "1" for row in candidates)


def test_context_selection_never_guesses_the_first_page_of_a_file():
    contexts = [{"file_id": "file", "page_no": page, "source_segments": [{"source_ref": f"p:{page}"}]}
                for page in (3, 37)]
    assert resolve_context({"file_id": "file"}, contexts) is None
    assert resolve_context({"file_id": "file", "page_no": 37}, contexts)["page_no"] == 37
    assert resolve_context({"source_ref": "p:3"}, contexts)["page_no"] == 3
    assert resolve_context({"source_ref": "invented"}, contexts) is None
    assert resolve_context({"source_ref": "p:3", "page_no": 37}, contexts) is None
