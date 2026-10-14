import json

from ict.bindings import bind_packages
from ict.evidence import resolve_context
from ict.html_context import parse_notice
from ict.llm import LLMResult
from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding, Amount, HtmlTables, ProjectPlans, ProjectPlan, FileDecisions, FileDecision
from ict.steps.s02_html import _append_candidates
from ict.steps.s04_attach import apply_native_resolutions
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
    candidates = []
    _append_candidates(candidates, rows, "cob", None, "项目", 100, 1, ["1", "2"],
                       table=table, table_index=table.table_index, column_mapping={"supplier_name": "供应商"})
    assert [row.evidence.row_index for row in candidates] == [0, 1]
    assert all(row.evidence.bidder_supplier == "同一公司" for row in candidates)
    assert all(row.fields["object_name"].raw_value is None for row in candidates)
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


def test_native_name_evidence_can_end_file_parsing_without_claiming_discovery_complete():
    need = {"need_id": "name", "field": "object_name", "purpose": "fill_field", "package_no": "1",
            "target_candidate_ids": ["html"], "state": "pending"}
    discovery = {"need_id": "discover", "field": "object_name", "purpose": "discover_objects", "package_no": "1", "state": "pending"}
    plans = ProjectPlans(run_id="x", status="success", projects=[ProjectPlan(project_id="项目|1", package_no="1",
                         needs=[need, discovery], has_attachment=True, needs_attachment=True, status="success")])
    source = _cob("native", "pdf", 20, {"object_name": "互联网接入服务"}, source_class="tender_requirement")
    from ict.schemas import CandidateEvidence
    source.evidence = CandidateEvidence(source_refs=["file:a:native:p3"])
    decisions = FileDecisions(run_id="x", status="success", file_decisions=[FileDecision(file_id="a",
        file_class="tender_requirement", priority=1, read_strategy="target_pages", reason="model",
        expected_fields=["object_name"], evidence_refs=["file:a:native:p3"], screen={"requires_deep_parse": False})])
    ready = apply_native_resolutions(plans, decisions, [source], [{"need_id": "name", "field": "object_name",
                                     "target_candidate_id": "html", "source_ref": "file:a:native:p3"}])
    assert ready == {"name"}
    assert plans.projects[0].needs[0]["state"] == "evidence_ready"
    assert plans.projects[0].needs[1]["state"] == "pending"
    assert decisions.file_decisions[0].read_strategy == "skip"


def test_a_screen_hit_without_a_matching_extracted_value_does_not_skip_parsing():
    plans = ProjectPlans(run_id="x", status="success", projects=[ProjectPlan(project_id="项目|1", package_no="1",
        needs=[{"need_id": "name", "field": "object_name", "purpose": "fill_field", "package_no": "1",
                "target_candidate_ids": ["html"]}], has_attachment=True, needs_attachment=True, status="success")])
    decisions = FileDecisions(run_id="x", status="success", file_decisions=[FileDecision(file_id="a", file_class="tender_requirement",
        priority=1, read_strategy="target_pages", reason="model", expected_fields=["object_name"],
        evidence_refs=["p3"], screen={"requires_deep_parse": False})])
    apply_native_resolutions(plans, decisions, [], [{"need_id": "name", "field": "object_name", "target_candidate_id": "html", "source_ref": "p3"}])
    assert decisions.file_decisions[0].read_strategy == "target_pages"


def test_native_coverage_requires_every_target_of_the_same_need():
    need = {"need_id": "names", "field": "object_name", "purpose": "fill_field", "package_no": "1",
            "target_candidate_ids": ["html1", "html2"], "state": "pending"}
    plans = ProjectPlans(run_id="x", status="success", projects=[ProjectPlan(project_id="项目|1", package_no="1",
        needs=[need], has_attachment=True, needs_attachment=True, status="success")])
    from ict.schemas import CandidateEvidence
    source = _cob("native", "pdf", 20, {"object_name": "服务"}, source_class="tender_requirement")
    source.evidence = CandidateEvidence(source_refs=["p3"])
    decisions = FileDecisions(run_id="x", status="success", file_decisions=[FileDecision(file_id="a",
        file_class="tender_requirement", priority=1, read_strategy="target_pages", reason="model",
        expected_fields=["object_name"], evidence_refs=["p3"], screen={"requires_deep_parse": False})])
    result = apply_native_resolutions(plans, decisions, [source], [{"need_id": "names", "field": "object_name",
        "target_candidate_id": "html1", "source_ref": "p3"}])
    assert not result and plans.projects[0].needs[0]["state"] == "pending"
    assert decisions.file_decisions[0].read_strategy == "target_pages"
