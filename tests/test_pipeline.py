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


def test_fake_llm_writes_run_files(tmp_path, monkeypatch):
    html_dir = tmp_path / 'html'
    html_dir.mkdir()
    (html_dir / 'demo001.html').write_text(HTML, encoding='utf-8')
    monkeypatch.setattr('ict.pipeline.load_index', lambda *args: None)
    body = {'project_name': '示例项目', 'package_mode': 'single', 'packages': [
        {'source_package_no': '1', 'candidates': [{'entity_type': 'cob', 'source_ref': 'html:body',
         'source_class': 'cob_detail', 'fields': {'object_name': '显示屏', 'brand': '创维', 'quantity': '2块', 'unit_price': '100'}}]}]}
    summary = {'project_name': '概要名称', 'purchaser': '示例单位', 'summary_amount': {'raw_text': '￥10万元', 'amount_kind': 'award', 'source_ref': 'html:summary'}}
    client = FakeLLMClient({'read_announcement_summary': json.dumps(summary), 'read_announcement_body': json.dumps(body)})
    report = run_announcement('demo001', llm=client, html_dir=html_dir, runs_root=tmp_path / 'runs')
    run_dir = tmp_path / 'runs/demo001'
    assert report.status in {'success', 'partial'}
    result = json.loads((run_dir / 'projects.json').read_text(encoding='utf-8'))
    assert result['project_name'] == '示例项目'
    assert result['packages'][0]['cobs'][0]['object_name'] == '显示屏'
    assert result['packages'][0]['package_total_amount'] == 100000
    assert 'provenance' not in result['packages'][0]
    state = json.loads((run_dir / 'run_state.json').read_text(encoding='utf-8'))
    assert not {'understand_html_tables','extract_html_candidates','extract_native_evidence','locate_pages','triage_files'} & {s['step'] for s in state['steps']}
