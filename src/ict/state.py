"""Run directory and run_state.json registry."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ict.config import RUNS_ROOT
from ict.schemas import RunState, RunStep

TZ = timezone(timedelta(hours=8))
STEP_FILES = {
    "understand_announcement": "01_announcement_understanding.json",
    "understand_html_tables": "02_html_tables.json",
    "extract_html_candidates": "03_html_candidates.json",
    "plan_project_gaps": "04_project_plans.json",
    "triage_files": "05_file_decisions.json",
    "parse_pages": "05b_parsed_pages.json",
    "locate_pages": "06_page_decisions.json",
    "extract_attachment_candidates": "07_attachment_extraction.json",
    "normalize_candidates": "08_normalized_candidates.json",
    # Written next to the normalized candidates but not a registered step: it is the read-only
    # context step 8 was given, kept so a merge decision can be replayed by hand.
    "merge_evidence": "08b_merge_evidence.json",
    "merge_candidates": "09_merged_projects.json",
    "repair_packages": "09_merged_projects.json",
    "persist_and_report": "10_run_report.json",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def dump(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(dump(payload), ensure_ascii=False, indent=2), encoding="utf-8")


class RunStore:
    def __init__(self, announcement_id: str, persist: bool = True) -> None:
        self.announcement_id = announcement_id
        self.run_id = f"run_{announcement_id}"
        self.persist = persist
        self.directory = RUNS_ROOT / announcement_id
        if persist:
            self.directory.mkdir(parents=True, exist_ok=True)
        created = now_iso()
        self.state = RunState(
            run_id=self.run_id,
            announcement_id=announcement_id,
            created_at=created,
            updated_at=created,
            status="running",
            current_step="understand_announcement",
            counts={
                "projects": 0,
                "files": 0,
                "selected_files": 0,
                "selected_pages": 0,
                "html_candidates": 0,
                "attachment_candidates": 0,
                "parsed_pages": 0,
                "normalized_candidates": 0,
                "final_cobs": 0,
                "final_subs": 0,
            },
        )
        self.save()

    def begin(self, step: str) -> None:
        self.state.current_step = step
        self.state.updated_at = now_iso()
        self.state.steps.append(
            RunStep(step=step, status="running", output_file=STEP_FILES.get(step), started_at=now_iso())
        )
        self.save()

    def finish(self, step: str, status: str, failure_code: str | None = None, failure_message: str | None = None) -> None:
        for item in reversed(self.state.steps):
            if item.step == step and item.status == "running":
                item.status = status
                item.ended_at = now_iso()
                item.failure_code = failure_code
                item.failure_message = failure_message
                break
        self.state.updated_at = now_iso()
        self.save()

    def write_output(self, step: str, payload) -> Path | None:
        if not self.persist:
            return None
        path = self.directory / STEP_FILES[step]
        write_json(path, payload)
        return path

    def save(self) -> None:
        if not self.persist:
            return
        write_json(self.directory / "run_state.json", self.state)
