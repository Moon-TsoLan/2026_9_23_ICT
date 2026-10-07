"""入库键：project_id = <项目编号>|<包号>[|rN]，轮次按公告日期排。

库里已有同键行时，新到的更早公告要把它们往后挤一位（renames），靠外键
ON UPDATE CASCADE 带子表一起改名。这里只测纯函数，不连数据库。
"""
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


def _plan(runs: list[Path], existing: list[tuple] = ()):
    return load_runs.plan_rounds(load_runs.target_entries(runs), existing)


def test_single_announcement_keeps_plain_id(tmp_path: Path) -> None:
    run = _run(tmp_path, "t20260809_27099966",
               [{"project_id": "ignored|1", "package_no": "1",
                 "source_project_no": "440001-2026-27404", "project_name": "肇庆学院"}])
    assert _plan([run]).inserts == {
        ("t20260809_27099966", 0): ("440001-2026-27404|1", 1, "440001-2026-27404|1")}


def test_reused_key_gets_rounds_ordered_by_date(tmp_path: Path) -> None:
    first = _run(tmp_path, "t20260206_26154814",
                 [{"project_id": "a|1", "package_no": "1",
                   "source_project_no": "GZGK25D382A1284Z", "project_name": "口腔医学"}])
    second = _run(tmp_path, "t20260311_26256690",
                  [{"project_id": "b|1", "package_no": "1",
                    "source_project_no": "GZGK25D382A1284Z", "project_name": "口腔医学(二次)"}])
    plan = _plan([second, first])  # 传入顺序不影响结果
    assert plan.inserts[("t20260206_26154814", 0)] == ("GZGK25D382A1284Z|1", 1, "GZGK25D382A1284Z|1")
    assert plan.inserts[("t20260311_26256690", 0)] == ("GZGK25D382A1284Z|1|r2", 2, "GZGK25D382A1284Z|1")
    assert plan.renames == []


def test_different_project_numbers_do_not_merge(tmp_path: Path) -> None:
    first = _run(tmp_path, "t20260612_26744962",
                 [{"project_id": "a|1", "package_no": "1",
                   "source_project_no": "SDGP370000000202602003242", "project_name": "省级新闻媒体"}])
    second = _run(tmp_path, "t20260810_27107242",
                  [{"project_id": "b|1", "package_no": "1",
                    "source_project_no": "SDGP370000000202602003242-2", "project_name": "省级新闻媒体"}])
    plan = _plan([first, second])
    assert plan.inserts[("t20260612_26744962", 0)][1] == 1
    assert plan.inserts[("t20260810_27107242", 0)][1] == 1
    assert plan.inserts[("t20260612_26744962", 0)][2] != plan.inserts[("t20260810_27107242", 0)][2]


def test_missing_project_no_falls_back_to_project_name(tmp_path: Path) -> None:
    run = _run(tmp_path, "t20260610_26721304",
               [{"project_id": "x|1", "package_no": "1",
                 "source_project_no": None, "project_name": "安徽省突发事件预警短信"}])
    assert _plan([run]).inserts == {
        ("t20260610_26721304", 0): ("安徽省突发事件预警短信|1", 1, "安徽省突发事件预警短信|1")}


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
    plan = _plan([first, second])
    assert plan.inserts[("t20260511_26545663", 0)] == ("441901-2026-01878|1", 1, "441901-2026-01878|1")
    assert plan.inserts[("t20260604_26688741", 0)] == ("441901-2026-01878|1|r2", 2, "441901-2026-01878|1")
    assert plan.inserts[("t20260604_26688741", 1)] == ("441901-2026-01878|2", 1, "441901-2026-01878|2")


def test_later_announcement_appends_after_existing(tmp_path: Path) -> None:
    run = _run(tmp_path, "t20260311_26256690",
               [{"project_id": "b|1", "package_no": "1",
                 "source_project_no": "GZGK", "project_name": "口腔医学(二次)"}])
    existing = [("GZGK|1", "t20260206_26154814", "GZGK|1", 1)]
    plan = _plan([run], existing)
    assert plan.inserts[("t20260311_26256690", 0)] == ("GZGK|1|r2", 2, "GZGK|1")
    assert plan.renames == []          # 后到且更晚：已有的第 1 轮不动


def test_earlier_announcement_pushes_existing_back(tmp_path: Path) -> None:
    """库里有 3 月的第 1 轮，来了 2 月的公告 —— 新公告拿第 1 轮，已有行改名到 |r2。"""
    run = _run(tmp_path, "t20260206_26154814",
               [{"project_id": "a|1", "package_no": "1",
                 "source_project_no": "GZGK", "project_name": "口腔医学"}])
    existing = [("GZGK|1", "t20260311_26256690", "GZGK|1", 1)]
    plan = _plan([run], existing)
    assert plan.inserts[("t20260206_26154814", 0)] == ("GZGK|1", 1, "GZGK|1")
    assert plan.renames == [("GZGK|1", "GZGK|1|r2", 2)]
