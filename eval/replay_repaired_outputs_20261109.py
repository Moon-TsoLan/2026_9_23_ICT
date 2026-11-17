"""Verify final error/audit wiring against all 50 live outputs, with zero network."""
from collections import defaultdict
from pathlib import Path
import json
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "eval")]
from evaluate_front_half_20261108 import read, write, forbid_ocr, sha
from ict.llm import LLMClient, LLMError, LLMResult, load_prompt, OpenAICompatibleClient
from ict.replay import request_key
from ict.pipeline import run_announcement

BASE = ROOT / "work/front-half-repair-20261108"
LIVE = BASE / "live-capacity"
OUT = BASE / "offline-final"


class ExactReplay(LLMClient):
    def __init__(self, aid):
        self.records = defaultdict(list)
        self.lock = threading.Lock()
        self.used = 0
        for path in sorted((LIVE / "calls/announcements" / aid).glob("*.json")):
            record = read(path)
            assert record["kind"] == "text"
            key = request_key(record["step"], record["prompt_version"], record["user"], record["thinking"])
            self.records[key].append(record)

    def complete(self, *, step, prompt_version, user, thinking=False):
        key = request_key(step, prompt_version, user, thinking)
        with self.lock:
            assert self.records[key], f"Unrecorded request: {step}/{prompt_version}"
            record = self.records[key].pop(0)
            self.used += 1
        if "error" in record:
            raise LLMError(record["error"])
        return LLMResult(**record["result"])


def main():
    forbid_ocr()
    def no_network(*args, **kwargs):
        raise AssertionError("Offline verification must never call the API")
    OpenAICompatibleClient._send_once = no_network
    saved = read(LIVE / "effective_prompts.json")["prompts"]
    unchanged = {version: load_prompt(version) == text for version, text in saved.items() if version != "screen-page-v6"}
    assert all(unchanged.values())
    manifest = read(LIVE / "manifest.json")
    results = []
    for aid in manifest["ids"]:
        client = ExactReplay(aid)
        source = Path(manifest["html"][aid])
        run_announcement(aid, llm=client, html_dir=source.parent,
                         attachments_root=OUT / "empty_attachments", runs_root=OUT / "runs")
        expected, got = read(LIVE / "runs" / aid / "projects.json"), read(OUT / "runs" / aid / "projects.json")
        equal = expected == got
        unused = sum(len(items) for items in client.records.values())
        results.append({"announcement_id": aid, "equal_public_result": equal, "matched_requests": client.used, "unused_requests": unused})
        assert equal and unused == 0, aid
        if len(results) % 10 == 0:
            print(json.dumps({"replayed": len(results), "equal": sum(row["equal_public_result"] for row in results)}), flush=True)
    write(OUT / "verification.json", {"cases": results, "all_equal": all(row["equal_public_result"] for row in results),
        "unchanged_effective_prompts": unchanged, "network_calls": 0, "ocr_calls": 0,
        "final_source_sha256": {str(path.relative_to(ROOT)): sha(path) for path in
            list((ROOT / "src/ict").rglob("*.py")) + list((ROOT / "src/ict/prompts").rglob("*.md"))}})


if __name__ == "__main__":
    main()
