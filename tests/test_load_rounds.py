"""入库键：project_id = <项目编号>|<包号>[|rN]，轮次只在同键被多则公告复用时 > 1。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("ict_load_runs", ROOT / "db" / "load_runs.py")
assert _spec and _spec.loader
load_runs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = load_runs
_spec.loader.exec_module(load_runs)


def _run(root: Path, aid: str, projects: list[dict]) -> Path:
    run = root / aid
    run.mkdir(parents=True)
    (run / "10_run_report.json").write_text(
        json.dumps({"announcement_id": aid, "status": "success"}), encoding="utf-8")
    (run / "09_merged_projects.json").write_text(
        json.dumps({"projects": projects}), encoding="utf-8")
    return run


def test_single_announcement_keeps_plain_id(tmp_path: Path) -> None:
    run = _run(tmp_path, "t20260809_27099966",
               [{"project_id": "ignored|1", "package_no": "1",
                 "source_project_no": "440001-2026-27404", "project_name": "肇庆学院"}])
    plan = load_runs.plan_rounds([run])
    assert plan[("t20260809_27099966", 0)] == ("440001-2026-27404|1", 1, "440001-2026-27404|1")


def test_reused_key_gets_rounds_ordered_by_date(tmp_path: Path) -> None:
    first = _run(tmp_path, "t20260206_26154814",
                 [{"project_id": "a|1", "package_no": "1",
                   "source_project_no": "GZGK25D382A1284Z", "project_name": "口腔医学"}])
    second = _run(tmp_path, "t20260311_26256690",
                  [{"project_id": "b|1", "package_no": "1",
                    "source_project_no": "GZGK25D382A1284Z", "project_name": "口腔医学(二次)"}])
    plan = load_runs.plan_rounds([second, first])  # 传入顺序不影响结果
    assert plan[("t20260206_26154814", 0)] == ("GZGK25D382A1284Z|1", 1, "GZGK25D382A1284Z|1")
    assert plan[("t20260311_26256690", 0)] == ("GZGK25D382A1284Z|1|r2", 2, "GZGK25D382A1284Z|1")


def test_different_project_numbers_do_not_merge(tmp_path: Path) -> None:
    first = _run(tmp_path, "t20260612_26744962",
                 [{"project_id": "a|1", "package_no": "1",
                   "source_project_no": "SDGP370000000202602003242", "project_name": "省级新闻媒体"}])
    second = _run(tmp_path, "t20260810_27107242",
                  [{"project_id": "b|1", "package_no": "1",
                    "source_project_no": "SDGP370000000202602003242-2", "project_name": "省级新闻媒体"}])
    plan = load_runs.plan_rounds([first, second])
    assert plan[("t20260612_26744962", 0)][1] == 1
    assert plan[("t20260810_27107242", 0)][1] == 1
    assert plan[("t20260612_26744962", 0)][2] != plan[("t20260810_27107242", 0)][2]


def test_missing_project_no_falls_back_to_project_name(tmp_path: Path) -> None:
    run = _run(tmp_path, "t20260610_26721304",
               [{"project_id": "x|1", "package_no": "1",
                 "source_project_no": None, "project_name": "安徽省突发事件预警短信"}])
    plan = load_runs.plan_rounds([run])
    assert plan[("t20260610_26721304", 0)] == ("安徽省突发事件预警短信|1", 1, "安徽省突发事件预警短信|1")


def test_renumbered_retender_keeps_both_packages(tmp_path: Path) -> None:
    """第二批实训室：原公告包1、二次公告包1+包2 —— 包1 撞键分成两轮，包2 保持第 1 轮。"""
    first = _run(tmp_path, "t20260511_26545663",
                 [{"project_id": "p1", "package_no": "1",
                   "source_project_no": "441901-2026-01878", "project_name": "第二批实训室"}])
    second = _run(tmp_path, "t20260604_26688741",
                  [{"project_id": "q1", "package_no": "1",
                    "source_project_no": "441901-2026-01878", "project_name": "第二批实训室(二次)"},
                   {"project_id": "q2", "package_no": "2",
                    "source_project_no": "441901-2026-01878", "project_name": "第二批实训室(二次)"}])
    plan = load_runs.plan_rounds([first, second])
    assert plan[("t20260511_26545663", 0)] == ("441901-2026-01878|1", 1, "441901-2026-01878|1")
    assert plan[("t20260604_26688741", 0)] == ("441901-2026-01878|1|r2", 2, "441901-2026-01878|1")
    assert plan[("t20260604_26688741", 1)] == ("441901-2026-01878|2", 1, "441901-2026-01878|2")
