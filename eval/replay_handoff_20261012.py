"""Offline execution audit using the original 50-case requests/responses. No client or OCR.

This reuses the original merge inputs, not a new extraction run or an accuracy evaluation.
Run: python -B eval/replay_handoff_20261012.py --output work/handoff-fixes-20261012/replay.json
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ict.llm import parse_json_object, FakeLLMClient, complete_json
from ict.merge_protocol import compile_plan, materialize
from ict.receiving import ExtractionReceiver
from ict.schemas import CandidateFile, MergeEvidence
from ict.steps.s08_merge import Group, _Context, _ownership, _value, _rank, _sum_bundle, _detail_ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("work/e2e-20261011"))
    parser.add_argument("--output", type=Path, default=Path("work/handoff-fixes-20261012/replay.json"))
    args = parser.parse_args()
    calls = {}
    for name in ("model_trace.jsonl", "resume_model_trace.jsonl"):
        path = args.root / name
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                calls.setdefault(item["call_id"], {})[item["event"]] = item
    runs = args.root / "runtime/tenants/default/runs"
    names = {}
    for directory in runs.iterdir():
        path = directory / "01_announcement_understanding.json"
        if path.exists():
            names.setdefault(json.loads(path.read_text(encoding="utf-8"))["project_name"], []).append(directory)
    report = {"scope": "saved-input execution only; no model/OCR/API or accuracy scoring",
              "merge_replays": [], "independent_reception": {}}
    for call_id, pair in calls.items():
        request, response = pair.get("request", {}), pair.get("response", {})
        if request.get("step") != "merge_candidates" or not response.get("text"):
            continue
        payload, end = json.JSONDecoder().raw_decode(request["user"])
        possible = names.get(payload["project_name"], [])
        if len(possible) != 1:
            raise RuntimeError(f"Cannot uniquely locate saved run: {payload['project_name']}")
        run = possible[0]
        row = {"announcement_id": run.name, "package_no": payload["package_no"], "call_id": call_id,
               "repair_response": bool(request["user"][end:].strip())}
        normalized = CandidateFile.model_validate_json((run / "08_normalized_candidates.json").read_text(encoding="utf-8"))
        evidence = MergeEvidence.model_validate_json((run / "08b_merge_evidence.json").read_text(encoding="utf-8"))
        by_id = {c.candidate_id: c for c in normalized.candidates if c.candidate_id in
                 {item["candidate_id"] for item in payload["candidates"]}}
        groups = [Group(group_id=g["group_id"], members=[by_id[cid] for cid in g["candidate_ids"]])
                  for g in payload["baseline_groups"]]
        ctx = _Context(package_no=payload["package_no"], project_name=payload["project_name"],
                       winners=set(payload.get("winner_suppliers", [])), detail_ids=_detail_ids(list(by_id.values()), evidence))
        from ict.catalog import Catalog
        catalog = Catalog()
        try:
            answer = parse_json_object(response["text"])
            plan = compile_plan(groups, by_id, answer, lambda c: _ownership(c, ctx), _value, _sum_bundle,
                payload.get("package_total_amount") if payload.get("package_amount_bindable") else None,
                {item: catalog.match(None, item) for item in payload.get("announcement_categories", [])})
            outputs, bindings, unresolved, conflicts = materialize(plan, lambda c: _ownership(c, ctx), _value,
                                                                   lambda c: _rank(c, ctx), payload["package_no"])
            row.update(accepted=True, cobs=[o["values"] for o in outputs], unresolved=unresolved,
                       field_bindings=bindings, conflicts=conflicts)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            row.update(accepted=False, error_type=type(exc).__name__, error=str(exc))
        report["merge_replays"].append(row)
    first_id, second_id = "8c8de353e75f4fe29545cbbb7054b9c5", "f3095ae1c7c4491aa5e9526150609bc2"
    payload = json.loads(calls[first_id]["request"]["user"])
    model = FakeLLMClient({"extract_attachment_candidates": [calls[c]["response"]["text"] for c in (first_id, second_id)]})
    received = complete_json(model, step="extract_attachment_candidates", prompt_version="attachment-extract-v3",
        user=calls[first_id]["request"]["user"], validate=lambda data: "" if isinstance(data.get("candidates"), list) else "candidates required",
        receiver=ExtractionReceiver(payload["page_contexts"]))
    report["independent_reception"]["t20260206_26157725"] = received
    first = {}
    for row in report["merge_replays"]:
        first.setdefault((row["announcement_id"], row["package_no"]), row)
    report["summary"] = {"merge_responses": len(report["merge_replays"]),
                         "accepted": sum(row["accepted"] for row in report["merge_replays"]),
                         "packages_with_saved_merge_calls": len(first),
                         "accepted_first_responses": sum(row["accepted"] for row in first.values()),
                         "native_names_retained": len(received["candidates"]),
                         "native_need_links_retained": len(received["need_resolutions"])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
