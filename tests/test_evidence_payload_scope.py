"""Evidence reduction preserves referenced originals, units and independent amounts."""
import json

from ict.bindings import bind_packages
from ict.schemas import HtmlTables, MergeEvidence, PackageAmountObservation
from test_binding_evidence import Binder, fixture
from test_merge_protocol_v4 import Model
from test_rules import _cob, _merge


def test_binding_keeps_full_referenced_text_and_amount_context_once(tmp_path):
    notice, understanding = fixture(tmp_path)
    text = '完整采购行及上下文' * 1000
    row = _cob('a', 'pdf', 90, {'object_name': '设备'}, source_refs=['file:a001:p1'], row_text=text,
               column_units={'total_price': '成交金额（万元）'})
    amount = PackageAmountObservation(package_no=None, raw_text='100元', source_ref='file:a001:p3')
    pages = [{'source_segments': [{'source_ref': 'file:a001:p1', 'text': text}]},
             {'source_segments': [{'source_ref': 'file:a001:p2', 'text': '未形成任何观测的完整片段' * 1000}]},
             {'source_segments': [{'source_ref': 'file:a001:p3', 'text': '独立金额原文100元'}]}]

    class Capture(Binder):
        def complete(self, **kwargs):
            self.payload = json.loads(kwargs['user'])
            return super().complete(**kwargs)

    model = Capture({'bindings': [{'candidate_id': 'a', 'package_no': '1', 'source_refs': ['file:a001:p1'], 'reason': '原文包1'}],
                     'amount_bindings': [{'package_no': no, 'amount_ref': None, 'amount_kind': 'unknown', 'reason': '不采用'} for no in ['1', '2']],
                     'observation_bindings': [{'amount_ref': 'amount:attachment:0', 'package_no': '1', 'amount_kind': 'award', 'reason': '原文包1'}]})
    _, errors = bind_packages(understanding, [row], notice, HtmlTables(run_id='x', status='success'), model,
                              attachment_pages=pages, package_amounts=[amount])
    assert not errors
    sources = {item['source_ref']: item for item in model.payload['source_rows']}
    assert sources['file:a001:p1']['text'] == text
    assert sources['file:a001:p3']['text'] == '独立金额原文100元'
    assert 'file:a001:p2' not in sources
    assert len(sources) == len(model.payload['source_rows'])
    evidence = model.payload['candidates'][0]['evidence']
    assert 'row_text' not in evidence
    assert evidence['source_refs'] == ['file:a001:p1']
    assert evidence['column_units']['total_price'] == '成交金额（万元）'
    assert 'body_sections' not in model.payload


def test_merge_page_scope_preserves_candidate_and_independent_amount_pages():
    row = _cob('a', 'pdf', 90, {'object_name': '屏幕', 'total_price': 100},
               source_refs=['file:a001:p7'], row_text='完整行原文' * 2000, source_class='award_detail')
    row.source.page_no = 7
    pages = {'a001:7': {'text_head': '候选本页'}, 'a002:9': {'text_head': '金额本页'},
             'a003:1': {'text_head': '另一采购包页面'}}
    evidence = MergeEvidence(attachment_pages=pages, source_contexts={'file:a001:p7': {'text': '完整行原文' * 2000, 'file_id': 'a001', 'page_no': 7}}, amount_observations={
        '1': [{'source_ref': 'file:a002:p9', 'file_id': 'a002', 'page_no': 9}],
        '2': [{'source_ref': 'file:a003:p1', 'file_id': 'a003', 'page_no': 1}]})
    model = Model()
    _merge([row], evidence=evidence, llm=model)
    payload = json.loads(next(user for step, user in model.requests if step == 'merge_candidates'))
    assert set(payload['evidence_context']['attachment_pages']) == {'a001:7', 'a002:9'}
    assert 'row_text' not in payload['candidates'][0]
    assert payload['evidence_context']['source_contexts']['file:a001:p7']['text'] == '完整行原文' * 2000
    assert evidence.attachment_pages == pages
