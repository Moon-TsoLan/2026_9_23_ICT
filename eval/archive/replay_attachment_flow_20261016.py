"""Read-only regression replay of the paused failure round; never calls an endpoint.

The continuation answers below are explicit fixed stubs, not new model judgments.
Run from the repository root: python eval/replay_attachment_flow_20261016.py
"""
import json
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ict.llm import LLMResult, parse_json_object, ValidationIssue
from ict.schemas import FileDecisions, ProjectPlans, CandidateFile
from ict.steps.s02_html import _validate_table
from ict.steps.s04_attach import review_native_continuation

BASE = ROOT / "work/e2e-20261016"
RUNTIME = BASE / "runtime/tenants/7b02ec0855bd4e4b98684e05c67f7bc9"
OUT = ROOT / "work/attachment-flow-fixes-20261016"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class FixedReview:
    def __init__(self):
        self.calls = 0

    def complete(self, *, step, prompt_version, user, **kwargs):
        assert step == "review_native_continuation"
        self.calls += 1
        payload = json.loads(user)
        packages = {p["package_no"] for p in payload["package_registry"]}
        refs = []
        for item in payload["accepted_observations"]:
            if item.get("package_no") in packages:
                refs.extend(item.get("source_refs", []) or [item.get("source_ref")])
        refs = [ref for ref in refs if ref]
        basis = "accepted_observation"
        if not refs:
            refs = payload["reception_status"]["source_refs_successfully_attempted"]
            basis = "attempted_no_new_observation"
        answer = {"continuation": "finish", "needs_hit_ids": [], "evidence_refs": refs[:1],
                  "evidence_basis": basis,
                  "kind": "native_continuation", "confidence": 0.5,
                  "reason": "固定回答回归桩：验证已接收证据允许结束本文件，不代表新的内容判断"}
        return LLMResult(json.dumps(answer, ensure_ascii=False), "fixed-stub", "not-a-live-model", prompt_version, 0)


def main():
    watched = [BASE / "model_trace.jsonl"]
    for aid in ("t20260409_26380023", "t20260509_26533549"):
        watched.extend((RUNTIME / "runs" / aid).glob("*.json"))
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    requests, screens, checked, rejected = {}, [], 0, 0
    for line in (BASE / "model_trace.jsonl").open(encoding="utf-8"):
        event = json.loads(line)
        if event.get("event") == "request":
            requests[event["call_id"]] = event
            if event.get("step") == "screen_files":
                payload, _ = json.JSONDecoder().raw_decode(event["user"])
                screens.append(payload)
        elif event.get("event") == "response":
            request = requests.get(event["call_id"], {})
            if request.get("step") == "understand_html_tables":
                payload, _ = json.JSONDecoder().raw_decode(request["user"])
                value = parse_json_object(event["text"])
                checked += 1
                error = _validate_table(value, payload["package_candidates"])
                if isinstance(value.get("package_scope"), list):
                    assert isinstance(error, ValidationIssue) and error.path == "$.package_scope"
                    rejected += 1
    cases = []
    for aid in ("t20260409_26380023", "t20260509_26533549"):
        run = RUNTIME / "runs" / aid
        files = FileDecisions.model_validate(read(run / "05_file_decisions.json"))
        after = ProjectPlans.model_validate(read(run / "04_project_plans.json"))
        native = CandidateFile.model_validate(read(run / "05c_native_extraction.json"))
        index = read(RUNTIME / "attachments" / aid / "index.json")
        names = {f["display_name"] for f in index["files"]}
        snapshot = next(payload["needs"] for payload in screens if payload["file_name"] in names)
        before = after.model_copy(deep=True)
        for plan in before.projects:
            plan.needs = [dict(n) for n in snapshot if n.get("package_no") == plan.package_no]
        state = after.model_dump()
        model = FixedReview()
        failures = review_native_continuation(before, after, files, native.candidates, native.package_amounts, model,
                                             reception=native.reception)
        assert not failures and state == after.model_dump(), "file review must not change global need states"
        cases.append({"announcement_id": aid, "stub_review_calls": model.calls,
                      "global_needs_unchanged": True,
                      "files": [{"file_id": f.file_id, "read_strategy": f.read_strategy,
                                 "reuse_status": f.reuse_status, "continuation": f.continuation_audit.get("output")}
                                for f in files.file_decisions]})
    first = next(c for c in cases if c["announcement_id"] == "t20260409_26380023")
    assert next(f for f in first["files"] if f["file_id"] == "a002")["read_strategy"] == "skip"
    assert all(hashlib.sha256((ROOT / key).read_bytes()).hexdigest() == value for key, value in hashes.items())
    result = {"table_answers_checked": checked, "scope_arrays_with_precise_type_error": rejected,
              "cases": cases, "source_artifacts_unchanged": True, "evidence_sha256": hashes,
              "limits": "No live model/OCR/HTTP/database. Fixed continuation answers verify execution only, not new inference accuracy or runtime improvement."}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("cases", "evidence_sha256")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
