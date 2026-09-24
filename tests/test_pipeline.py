import json
from pathlib import Path

from ict.llm import FakeLLMClient
from ict.pipeline import run_announcement

HTML = """<!doctype html><html><head>
<meta name="ArticleTitle" content="示例项目中标公告" />
<title>示例项目中标公告</title></head><body>
<div class="vF_detail_header"><h2 class="tc">示例项目中标公告</h2></div>
<div class='table'><table><tr><td class='title'>采购项目名称</td><td>示例项目</td></tr>
<tr><td class='title'>采购单位</td><td>示例单位</td></tr>
<tr><td class='title'>总中标金额</td><td>￥10 万元</td></tr></table></div>
<div class="vF_detail_content">
<p><strong>一、项目编号：DEMO001</strong></p>
<table><tr><th>货物名称</th><th>品牌</th><th>数量</th><th>单价(元)</th></tr>
<tr><td>显示屏</td><td>创维</td><td>2块</td><td>100</td></tr></table>
</div></body></html>
"""


def test_fake_llm_writes_run_files(monkeypatch):
    root = Path("work/test_pipeline")
    html_dir = root / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    (html_dir / "demo001.html").write_text(HTML, encoding="utf-8")
    monkeypatch.setattr("ict.pipeline.ATTACHMENTS_ROOT", root / "missing")
    monkeypatch.setattr("ict.state.RUNS_ROOT", root / "runs")
    client = FakeLLMClient(
        {
            "understand_announcement": json.dumps(
                {
                    "project_name": "示例项目",
                    "purchaser": "示例单位",
                    "source_project_no": "DEMO001",
                    "announcement_type": "winning_announcement",
                    "package_mode": "single",
                    "packages": [
                        {
                            "package_no": "1",
                            "title": "示例项目",
                            "package_evidence_text": "示例项目中标公告",
                            "package_amount": None,
                        }
                    ],
                    "summary_amount": {"raw_text": "￥10 万元", "amount_yuan": 1, "scope": "announcement"},
                },
                ensure_ascii=False,
            ),
            "understand_html_tables": json.dumps(
                {
                    "table_role": "cob_detail",
                    "package_scope": "1",
                    "row_grain": "cob",
                    "column_mapping": {"object_name": "货物名称", "brand": "品牌", "quantity": "数量", "unit_price": "单价(元)"},
                    "issues": [],
                    "confidence": 0.9,
                }
            ),
            "extract_html_candidates": json.dumps(
                {
                    "candidates": [
                        {
                            "entity_type": "cob",
                            "package_no": "1",
                            "fields": {"object_name": "显示屏", "brand": "创维", "quantity": "2块", "unit_price": "100"},
                            "issues": [],
                        }
                    ]
                }
            ),
        }
    )
    report = run_announcement("demo001", llm=client, html_dir=html_dir)
    run_dir = root / "runs" / "demo001"
    assert report.status in {"success", "partial"}
    assert (run_dir / "09_merged_projects.json").exists()
    assert (run_dir / "10_run_report.json").exists()
    projects = json.loads((run_dir / "projects.json").read_text(encoding="utf-8"))
    assert projects[0]["cobs"][0]["object_name"] == "显示屏"
    assert projects[0]["package_total_amount"] == 100000
    assert "provenance" not in projects[0]
