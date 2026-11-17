"""Actual prompt contracts and independently received real/synthetic observations."""
from copy import deepcopy
import json
from pathlib import Path

from ict.fields import FIELD_OBSERVATION_SHAPE, FRONT_ENUMS, OUTPUT_EXAMPLES, OUTPUT_SHAPES, STEP_FIELDS
from ict.html_context import parse_notice
from ict.llm import FakeLLMClient, LLMError, LLMResult, load_prompt
from ict.steps.s01_understand import read_announcement


def candidate(name="设备", **fields):
    return {"entity_type": "cob", "source_ref": "html:body", "source_class": "cob_detail",
            "fields": {"object_name": name, **fields}}


def body():
    return {"project_name": "项目", "package_mode": "single", "packages": [
        {"source_package_no": "1", "package_kind": "contract_package", "package_name": "采购包",
         "package_amount": {"raw_text": "1.25", "amount_kind": "成交金额", "unit_header": "金额(万元)",
                            "source_ref": "html:body"}, "candidates": [candidate(unit_price="1.25")]}]}


def run(tmp_path, responses, summary="{}"):
    path = tmp_path / "a.html"
    path.write_text('<div class="vF_detail_content"><p>设备及供应商的完整原文</p></div>', "utf-8")
    model = FakeLLMClient({"read_announcement_summary": summary,
                          "read_announcement_body": [json.dumps(row, ensure_ascii=False) for row in responses]})
    return read_announcement("r", parse_notice(path), model), model


def test_actual_prompt_describes_every_nested_shape_and_existing_enum():
    structures = {
        "announcement-summary-v2": [OUTPUT_EXAMPLES["announcement-summary-v2"], OUTPUT_SHAPES["amount"]],
        "announcement-body-v6": [OUTPUT_EXAMPLES["announcement-body-v6"], *(OUTPUT_SHAPES[key]
                                 for key in ("candidate", "package", "amount", "table")), FIELD_OBSERVATION_SHAPE],
        "attachment-extract-v10": [OUTPUT_EXAMPLES["attachment-extract-v10"], OUTPUT_SHAPES["candidate"],
                                  OUTPUT_SHAPES["attachment_amount"], FIELD_OBSERVATION_SHAPE],
        "screen-page-v9": [OUTPUT_EXAMPLES["screen-page-v9"]],
    }
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()
    for version, shapes in structures.items():
        incoming, outgoing = STEP_FIELDS[version]
        declared = set(incoming + outgoing)
        prompt = load_prompt(version)
        for shape in shapes:
            assert keys(shape) <= declared, (version, keys(shape) - declared)
        for key in declared:
            assert "\n" + key + " " in prompt
            if key in FRONT_ENUMS:
                assert key + "允许值：" + json.dumps(FRONT_ENUMS[key], ensure_ascii=False) in prompt


def test_bad_amount_is_isolated_and_precise_feedback_survives(tmp_path):
    bad = body()
    (notice, _, rows), model = run(tmp_path, [bad, bad])
    assert notice.status == "partial" and len(rows.candidates) == 1
    assert notice.packages[0].package_amount is None
    audit = rows.reception[1]
    assert audit["unrepaired"] and len(audit["rejected_items"]) == 2
    issue = audit["attempts"][0]["issues"][0]
    assert issue["path"] == "packages[0].package_amount.amount_kind"
    assert "成交金额" in issue["message"] and all(value in issue["message"] for value in FRONT_ENUMS["amount_kind"])
    assert model.used["read_announcement_body"] == 2


def test_corrected_amount_preserves_candidates_and_header_unit(tmp_path):
    bad = body()
    fixed = deepcopy(bad)
    fixed["packages"][0]["package_amount"]["amount_kind"] = "award"
    (notice, _, rows), _ = run(tmp_path, [bad, fixed])
    assert notice.status == "success" and len(rows.candidates) == 1
    assert notice.packages[0].package_amount.raw_text == "1.25"
    assert notice.packages[0].package_amount.amount_yuan == 12500
    assert not rows.reception[1]["unrepaired"]


def test_second_transport_failure_retains_first_valid_observations(tmp_path):
    path = tmp_path / "a.html"
    path.write_text("<p>原文</p>", "utf-8")
    class Broken(FakeLLMClient):
        def complete(self, *, step, **kwargs):
            if step == "read_announcement_body" and self.used.get(step):
                raise LLMError("transport stopped")
            return super().complete(step=step, **kwargs)
    model = Broken({"read_announcement_summary": "{}", "read_announcement_body": [json.dumps(body())]})
    notice, _, rows = read_announcement("r", parse_notice(path), model)
    assert notice.status == "partial" and len(rows.candidates) == 1
    assert notice.branch_audit["body"]["attempts"][-1]["issues"][0]["category"] == "transport"


def test_bad_candidate_and_bad_table_do_not_discard_other_candidates(tmp_path):
    answer = body()
    answer["packages"][0]["package_amount"] = None
    answer["packages"][0]["candidates"] += [candidate("坏引用") | {"source_ref": "missing"}, candidate("合法后续")]
    answer["tables"] = [{"table_index": 999, "table_role": "other", "row_grain": "other"}]
    (notice, tables, rows), _ = run(tmp_path, [answer, answer])
    assert notice.status == tables.status == "partial"
    assert [row.fields["object_name"].raw_value for row in rows.candidates] == ["设备", "合法后续"]
    assert not tables.tables


def test_same_source_can_supply_two_independent_objects(tmp_path):
    answer = body()
    answer["packages"][0]["candidates"] = [candidate("光跳纤架", quantity="4", unit_price="319300"),
                                           candidate("光熔纤柜", quantity="5", unit_price="60480")]
    fixed = deepcopy(answer)
    fixed["packages"][0]["package_amount"]["amount_kind"] = "award"
    (notice, _, rows), _ = run(tmp_path, [answer, fixed])
    assert notice.status == "success" and len(rows.candidates) == 2
    assert [row.fields["quantity"].raw_value for row in rows.candidates] == ["4", "5"]
    assert [row.fields["unit_price"].raw_value for row in rows.candidates] == ["319300", "60480"]


def test_repair_may_not_reorder_same_number_different_kind_directory(tmp_path):
    answer = body()
    answer["package_mode"] = "multi"
    second = deepcopy(answer["packages"][0])
    second["package_kind"] = "lot"
    second["candidates"] = [candidate("另一单元")]
    answer["packages"].append(second)
    reordered = deepcopy(answer)
    reordered["packages"].reverse()
    (notice, _, rows), _ = run(tmp_path, [answer, reordered])
    assert notice.status == "partial" and len({unit.package_no for unit in notice.packages}) == 2
    assert [row.package_no for row in rows.candidates] == [unit.package_no for unit in notice.packages]
    assert rows.reception[1]["attempts"][-1]["issues"][0]["path"] == "packages"


def test_omitting_bad_amount_is_not_a_successful_repair(tmp_path):
    answer = body()
    omitted = deepcopy(answer)
    omitted["packages"][0].pop("package_amount")
    (notice, _, rows), _ = run(tmp_path, [answer, omitted])
    assert notice.status == "partial" and rows.reception[1]["unresolved_items"]


def test_independent_repair_conflict_keeps_both_amounts_without_selecting(tmp_path):
    answer = body()
    answer["packages"][0]["package_amount"]["amount_kind"] = "award"
    answer["candidates"] = [candidate() | {"source_ref": "missing"}]
    fixed = deepcopy(answer)
    fixed["packages"][0]["package_amount"]["raw_text"] = "2.5"
    fixed["candidates"] = [candidate()]
    (notice, _, rows), _ = run(tmp_path, [answer, fixed])
    assert notice.status == "partial" and notice.packages[0].package_amount is None
    assert [row.raw_text for row in notice.packages[0].amount_alternatives] == ["1.25", "2.5"]
    assert rows.reception[1]["conflicts"]


def test_real_failed_answer_preserves_its_four_candidates(tmp_path):
    fixture = json.loads(Path("tests/fixtures/announcement_bad_amount_20261108.json").read_text("utf-8"))
    path = tmp_path / "real.html"
    path.write_text(fixture["html"], "utf-8")
    notice = parse_notice(path)
    model = FakeLLMClient({"read_announcement_summary": fixture["summary"],
                          "read_announcement_body": [fixture["body"], fixture["body"]]})
    result, _, rows = read_announcement("r", notice, model)
    assert result.status == "partial" and len(rows.candidates) == 4 and len(result.packages) == 2
    assert all(unit.package_amount is None for unit in result.packages)
    assert all(row.evidence.source_refs and row.evidence.row_text for row in rows.candidates)


def test_real_output_limit_failure_never_fabricates_directory(tmp_path):
    fixture = json.loads(Path("tests/fixtures/announcement_output_limit_20261108.json").read_text("utf-8"))
    path = tmp_path / "real.html"
    path.write_text(fixture["html"], "utf-8")
    model = FakeLLMClient({"read_announcement_summary": fixture["summary"],
                          "read_announcement_body": [fixture["body"], fixture["body"]]})
    result, _, rows = read_announcement("r", parse_notice(path), model)
    assert result.status == "failed" and result.project_name and not result.packages and not rows.candidates
    assert len(result.branch_audit["body"]["responses"]) == 2
    assert model.used["read_announcement_body"] == 2


def test_ordinary_scalar_is_wrapped_by_program_and_special_state_remains(tmp_path):
    answer = body()
    answer["packages"][0]["package_amount"] = None
    answer["packages"][0]["candidates"] = [candidate("设备", unit_price="1.25",
        spec_model={"raw_value": "另见附件规格", "status": "points_to_attachment"})]
    (notice, _, rows), _ = run(tmp_path, [answer])
    field = rows.candidates[0].fields["unit_price"]
    assert notice.status == "success" and field.raw_value == "1.25" and field.normalized_value is None
    assert field.status == "present" and rows.candidates[0].fields["spec_model"].status == "points_to_attachment"


def test_body_capacity_is_fixed_on_both_attempts_and_not_added_downstream(tmp_path, monkeypatch):
    from ict.llm import OpenAICompatibleClient
    requests = []
    answer = body()
    class Response:
        status_code = 200
        def __init__(self, value):
            self.value = value
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(self.value)}, "finish_reason": "stop"}], "usage": {}}
    def post(self, step, request):
        requests.append((step, deepcopy(request)))
        if step == "read_announcement_body":
            value = deepcopy(answer)
            if len([row for row in requests if row[0] == step]) == 2:
                value["packages"][0]["package_amount"]["amount_kind"] = "award"
            return Response(value)
        return Response({})
    monkeypatch.setattr(OpenAICompatibleClient, "_post", post)
    path = tmp_path / "a.html"
    path.write_text("<p>原文</p>", "utf-8")
    client = OpenAICompatibleClient("unused", "unused", "test")
    result, _, rows = read_announcement("r", parse_notice(path), client)
    assert result.status == "success" and len(rows.candidates) == 1
    body_requests = [request for step, request in requests if step == "read_announcement_body"]
    assert [request["max_tokens"] for request in body_requests] == [16384, 16384]
    assert 'packages[0].package_amount.amount_kind' in body_requests[1]["messages"][1]["content"]
    assert 'award' in body_requests[1]["messages"][1]["content"]
    client.complete(step="merge_candidates", prompt_version="merge-objects-v8", user="{}", thinking=True)
    assert "max_tokens" not in requests[-1][1]
    assert all("max_tokens" not in request for step, request in requests if step == "read_announcement_summary")


def test_page_contract_separates_readonly_input_from_four_output_fields():
    prompt = load_prompt("screen-page-v9")
    assert "输入字段（只读上下文，不回传）：" in prompt
    assert "输出顶层仅允许：action、kind、confidence、reason" in prompt
    assert "结构与类型：顶层为JSON对象；packages/candidates" not in prompt
    assert set(OUTPUT_EXAMPLES["screen-page-v9"]) == {"action", "kind", "confidence", "reason"}


def test_unknown_body_identity_fills_from_summary_with_honest_selection(tmp_path):
    answer = body()
    answer["announcement_type"] = "unknown"
    answer["packages"][0]["package_amount"] = None
    (notice, _, _), _ = run(tmp_path, [answer], summary=json.dumps({"announcement_type": "winning_announcement"}))
    assert notice.announcement_type == "winning_announcement"
    entry = next(row for row in notice.branch_audit["conflicts"] if row["field"] == "announcement_type")
    assert entry["selected"] == "summary"


def test_attachment_receiver_keeps_omitted_bad_item_unresolved():
    from ict.receiving import ExtractionReceiver
    receiver = ExtractionReceiver([])
    issues = receiver.accept({"candidates": [{"entity_type": "sub", "source_ref": "missing", "fields": {"supplier_name": "甲"}}]}, 0)
    assert issues
    assert not receiver.accept({"candidates": []}, 1)
    result = receiver.result([{"attempt": 1, "issues": [issue.as_dict() for issue in issues]}, {"attempt": 2, "issues": []}])
    assert result["reception"]["unrepaired"]
    assert result["reception"]["unresolved_items"][0]["path"] == "candidates[0]"


def test_attachment_enum_errors_use_the_shared_values_and_exact_paths():
    from ict.receiving import ExtractionReceiver
    contexts = [{"file_id": "f", "page_no": 1, "source_segments": [{"source_ref": "r", "text": "金额"}]}]
    receiver = ExtractionReceiver(contexts)
    issues = receiver.accept({"package_amounts": [{"source_ref": "r", "package_no": "1", "raw_text": "1元", "amount_kind": "成交金额"}]}, 0)
    assert issues[0].path == "package_amounts[0].amount_kind"
    assert all(value in issues[0].message for value in FRONT_ENUMS["amount_kind"])


def test_corrected_candidate_enum_clears_its_leaf_error(tmp_path):
    answer = body()
    answer["packages"][0]["candidates"][0]["source_class"] = "invalid"
    fixed = deepcopy(answer)
    fixed["packages"][0]["candidates"][0]["source_class"] = "cob_detail"
    fixed["packages"][0]["package_amount"]["amount_kind"] = "award"
    (notice, _, rows), _ = run(tmp_path, [answer, fixed])
    assert notice.status == "success" and len(rows.candidates) == 1
    assert not rows.reception[1]["unresolved_items"]


def test_corrected_attachment_amount_enum_clears_its_leaf_error():
    from ict.receiving import ExtractionReceiver
    receiver = ExtractionReceiver([{"file_id": "f", "page_no": 1, "source_segments": [{"source_ref": "r", "text": "金额"}]}])
    amount = {"source_ref": "r", "package_no": "1", "raw_text": "1元", "amount_kind": "成交金额"}
    first = receiver.accept({"package_amounts": [amount]}, 0)
    assert first
    assert not receiver.accept({"package_amounts": [amount | {"amount_kind": "award"}]}, 1)
    result = receiver.result([{"attempt": 1, "issues": [issue.as_dict() for issue in first]}, {"attempt": 2, "issues": []}])
    assert not result["reception"]["unrepaired"] and len(result["package_amounts"]) == 1
