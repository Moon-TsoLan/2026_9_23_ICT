"""Project and candidate identifiers."""

from __future__ import annotations


def escape_project_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace("|", "\\|")


def make_project_id(project_name: str, package_no: str) -> str:
    return f"{escape_project_name(project_name)}|{package_no}"


def format_candidate_id(seq: int) -> str:
    return f"cand_{seq:06d}"


def format_file_id(seq: int) -> str:
    return f"a{seq:03d}"
