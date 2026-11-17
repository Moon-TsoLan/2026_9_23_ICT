"""Run current prompt examples through the existing merge executor, without API calls."""
import json
from copy import deepcopy
from pathlib import Path

from ict.fields import MERGE_OPERATION_EXAMPLES, field_block
from ict.llm import load_prompt
from test_merge_protocol_v4 import Model
from test_rules import _cob, _merge


def example(op):
    return deepcopy(next(row for row in MERGE_OPERATION_EXAMPLES if row['op'] == op))


def test_previous_merge_prompt_keeps_its_effective_field_snapshot():
    directory = Path('src/ict/prompts')
    saved = json.loads((directory / 'archive/field-blocks-20261110-before-merge-v9.json').read_text(encoding='utf-8'))
    assert field_block('merge-objects-v8') == saved['merge-objects-v8']
    assert load_prompt('merge-objects-v8') == (directory / 'merge-objects-v8.md').read_text(encoding='utf-8').rstrip() + '\n\n' + saved['merge-objects-v8'] + '\n'


def test_current_fields_from_example_reaches_executor_and_supplies_category():
    rows = [
        _cob('cand_000001', 'html', 100, {'object_name': '屏幕', 'total_price': 100}),
        _cob('cand_000006', 'pdf', 90, {'object_name': '屏幕', 'category_name': '液晶显示器'}, source_class='award_detail'),
    ]

    class Capture(Model):
        def complete(self, *, step, prompt_version, user, thinking=False):
            if step == 'merge_candidates':
                assert prompt_version == 'merge-objects-v9'
                assert json.dumps(example('fields_from'), ensure_ascii=False) in load_prompt(prompt_version)
            return super().complete(step=step, prompt_version=prompt_version, user=user, thinking=thinking)

    merged = _merge(rows, llm=Capture([example('fields_from')]))
    assert merged.projects[0].cobs[0].category_name == '液晶显示器'
    assert not any(row.get('action') == 'merge_model_fallback' for row in merged.repairs)


def test_quantity_and_unit_example_obeys_existing_source_qualification():
    allowed = _cob('cand_000001', 'html', 100, {'object_name': '设备', 'quantity': 1, 'unit': '台'})
    merged = _merge([allowed], llm=Model([example('price_from')]))
    assert (merged.projects[0].cobs[0].quantity, merged.projects[0].cobs[0].unit) == (1, '台')
    restricted = _cob('cand_000001', 'pdf', 20, {'object_name': '设备', 'quantity': 1, 'unit': '台'}, source_class='tender_requirement')
    rejected = _merge([restricted], llm=Model([example('price_from')]))
    assert any('没有供价资格' in row.get('detail', '') for row in rejected.repairs)
