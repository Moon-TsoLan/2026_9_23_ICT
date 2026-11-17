"""Actual nested page inputs and historical prompt isolation at the LLM boundary."""
import json
from pathlib import Path

from ict.config import COB_FIELDS, SUB_FIELDS
from ict.fields import FIELD_SEMANTICS, field_block
from ict.llm import LLMResult, load_prompt
from ict.parse.screen import inspect_page


def test_page_runtime_request_explains_all_supported_nested_business_fields():
    captured = {}
    class Capture:
        def complete_with_images(self, *, step, prompt_version, images, user_text):
            captured.update(version=prompt_version, prompt=load_prompt(prompt_version),
                            payload=json.loads(user_text), images=images)
            return LLMResult(json.dumps({'action':'skip','kind':'other','confidence':0.8,'reason':'当前页没有业务增量'}),
                             'fixed','test',prompt_version,1)
    known = [{'candidate_id':'c1','entity_type':'cob','package_no':'1',
              'fields':{'spec_model':{'raw_value':'服务范围描述','status':'present'}},'source_refs':['html:n1']}]
    context = {'project_name':'本案','file_id':'a1','file_name':'材料.pdf','page_no':1,
               'package_registry':[], 'known_observations':known,
               'source_segments':[{'source_ref':'file:a1:p1','text':'当前页'}], 'images':['fixed-image']}
    result, _ = inspect_page(Capture(), context, [{'field':'product_supplier','reason':'absent','package_no':'1'}])
    assert captured['version'] == 'screen-page-v9'
    assert captured['payload']['known_observations'] == known
    assert 'images' not in captured['payload'] and captured['images']==['fixed-image']
    for key in set(COB_FIELDS) | set(SUB_FIELDS):
        assert captured['prompt'].count('\n'+key+' '+FIELD_SEMANTICS[key]) == 1
    assert set(result)=={'action','kind','confidence','reason'}


def test_previous_prompt_field_blocks_remain_exact_snapshots():
    directory=Path('src/ict/prompts')
    snapshots=json.loads((directory/'archive/field-blocks-20261109-before-luna.json').read_text(encoding='utf-8'))
    for version, block in snapshots.items():
        assert field_block(version)==block
        assert load_prompt(version)==(directory/(version+'.md')).read_text(encoding='utf-8').rstrip()+'\n\n'+block+'\n'


def test_unassigned_amount_survives_receipt_and_reaches_binding_model_once(tmp_path):
    from ict.receiving import ExtractionReceiver
    from ict.observations import seal_amounts
    from ict.bindings import bind_packages
    from ict.schemas import HtmlTables
    from test_binding_evidence import fixture
    notice, understanding = fixture(tmp_path)
    contexts=[{'file_id':'a','file_name':'材料.pdf','page_no':2,'source_type':'pdf',
               'source_segments':[{'source_ref':'file:a:p2','text':'本采购单元成交金额200元'}]}]
    raw={'package_no':None,'raw_text':'200元','amount_kind':'award','source_ref':'file:a:p2'}
    receiver=ExtractionReceiver(contexts)
    assert not receiver.accept({'package_amounts':[raw]},0)
    amounts=seal_amounts(receiver.result([])['package_amounts'],contexts)
    assert amounts[0].package_no is None and amounts[0].amount_yuan==200
    class Binding:
        calls=0
        def complete(self, *, step, prompt_version, user, **kwargs):
            self.calls+=1
            payload=json.loads(user)
            observed=next(x for x in payload['amount_observations'] if x.get('amount_ref')=='amount:attachment:0')
            assert observed['package_no'] is None and observed['source_ref']=='file:a:p2'
            answer={'bindings':[], 'amount_bindings':[
                {'package_no':n,'amount_ref':'amount:package:'+n,'amount_kind':'award','reason':'保留公告明确金额'}
                for n in ('1','2')], 'observation_bindings':[
                {'amount_ref':'amount:attachment:0','package_no':'2','amount_kind':'award','reason':'原文对应采购单元2'}]}
            return LLMResult(json.dumps(answer),'fixed','test',prompt_version,1)
    model=Binding()
    audit, errors=bind_packages(understanding, [], notice, HtmlTables(run_id='x',status='success'),model,
                               attachment_pages=contexts, package_amounts=amounts)
    assert not errors and not audit['unresolved'] and model.calls==1
    assert amounts[0].package_no=='2' and amounts[0].binding['status']=='bound'


def test_inspection_receives_existing_amounts_without_changing_announcement_or_needs(tmp_path, monkeypatch):
    from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding, Amount
    from ict.steps.s04_attach import process_attachment_pages
    from test_front_half import fake_pages, index_for, PageModel, plans, no_ocr
    fake_pages(monkeypatch,count=1)
    no_ocr(monkeypatch)
    index=index_for(tmp_path,pages=1)
    original_plans=plans()
    announcement=AnnouncementUnderstanding(run_id='r',status='success',project_name='项目',package_mode='single',announcement_type='winning_announcement',
        summary_amount=Amount(raw_text='10万元',scope='announcement',amount_kind='award',source_ref='html:n1'),
        packages=[PackageUnderstanding(package_no='1',project_id='项目|1',package_evidence_text='包1',
            package_amount=Amount(raw_text='100000元',scope='package',amount_kind='award',source_ref='html:n2'),
            amount_alternatives=[Amount(raw_text='12万元',scope='package',amount_kind='budget',source_ref='html:n3')])])
    before=(announcement.model_dump(),original_plans.model_dump())
    model=PageModel({1:'skip'})
    process_attachment_pages('r','项目',original_plans,index,model,1,announcement=announcement)
    payload=model.inputs[0][1]
    assert payload['summary_amount']['raw_text']=='10万元'
    package=payload['package_registry'][0]
    assert package['package_amount']['source_ref']=='html:n2'
    assert package['amount_alternatives'][0]['amount_kind']=='budget'
    prompt=load_prompt('screen-page-v9')
    for key in set(package)|set(package['package_amount'])|set(payload['summary_amount']):
        assert '\n'+key+' ' in prompt
    assert (announcement.model_dump(),original_plans.model_dump())==before

