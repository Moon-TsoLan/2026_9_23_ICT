"""Reusable entry point: announcement HTML plus its attachment folder in, entities out.

    result = extract_announcement(html_bytes, Path("/tmp/upload/job-77"), announcement_id="job-77")
    # result["projects"] -> write to Postgres, result["report"] -> one run row, upload deleted

`persist=False` keeps the whole run in memory: no run directory, no step files, no markdown.
`persist=True` also writes the eleven step files for development review.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from ict.pipeline import run_announcement


def extract_announcement(html_source: Path | bytes, attachment_dir: Path | None = None,
                         announcement_id: str | None = None, persist: bool = True) -> dict:
    """html_source is a path or raw bytes; attachment_dir holds already unpacked files."""

    scratch = Path(tempfile.mkdtemp(prefix="ict-run-"))
    try:
        if isinstance(html_source, bytes):
            announcement_id = announcement_id or f"upload-{uuid.uuid4().hex[:12]}"
            html_dir = scratch
            (scratch / f"{announcement_id}.html").write_bytes(html_source)
        else:
            source = Path(html_source)
            announcement_id = announcement_id or source.stem
            html_dir = source.parent

        attachments_root = None
        if attachment_dir is not None:
            attachments_root = scratch / "attachments"
            shutil.copytree(attachment_dir, attachments_root / announcement_id, dirs_exist_ok=True)

        report = run_announcement(announcement_id, html_dir=html_dir,
                                  attachments_root=attachments_root, persist=persist)
        return {"report": report, "projects": report.projects, "announcement_id": announcement_id}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
