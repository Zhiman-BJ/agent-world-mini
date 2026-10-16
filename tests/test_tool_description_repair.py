"""Regression coverage for context-aware public tool description repair."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
import tempfile

PATH = Path(__file__).resolve().parents[1] / 'scripts/repair_tool_descriptions.py'
SPEC = importlib.util.spec_from_file_location('repair_tool_descriptions', PATH)
repair = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repair
SPEC.loader.exec_module(repair)


def tool():
    return {'name': 'inspect', 'description': 'Inspect workspace records and files.',
            'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'code': {'type': 'string'}}},
            'outputSchema': {'oneOf': [
                {'type': 'object', 'properties': {'success': {'const': True}, 'data': {'type': 'object', 'properties': {'code': {'type': 'integer'}}}}},
                {'type': 'object', 'properties': {'success': {'const': False}, 'error': {'type': 'object', 'properties': {k: {'type': 'string'} for k in ['code', 'path', 'message']}}}}
            ]}}


class DescriptionRepairTests(unittest.TestCase):
    def test_error_templates_require_failure_envelope(self):
        t = tool()
        repair.seed_descriptions({}, t)
        self.assertNotIn('description', t['inputSchema']['properties']['path'])
        self.assertNotIn('description', t['inputSchema']['properties']['code'])
        self.assertNotIn('description', t['outputSchema']['oneOf'][0]['properties']['data']['properties']['code'])
        for field in ['code', 'path', 'message']:
            self.assertEqual(t['outputSchema']['oneOf'][1]['properties']['error']['properties'][field]['description'], repair.RESULT_FIELD_DESCRIPTIONS[field])

    def test_business_success_and_error_objects_are_not_envelopes(self):
        t = tool()
        data = t['outputSchema']['oneOf'][0]['properties']['data']
        data['properties']['error'] = {'type': 'object', 'properties': {'code': {'type': 'string'}}}
        data['properties']['success'] = {'const': True}
        repair.seed_descriptions({}, t)
        self.assertNotIn('description', data['properties']['error'])
        self.assertNotIn('description', data['properties']['error']['properties']['code'])
        self.assertNotIn('description', data['properties']['success'])

    def test_environment_software_field_has_business_meaning(self):
        t = tool(); t['usageConditions'] = {'targetResources': ['dft_methods']}
        env = {'record_sets': [{'record_set_id': 'dft_methods', 'fields': {'code': {'description': '计算软件'}}}]}
        repair.seed_descriptions(env, t)
        self.assertNotIn('description', t['inputSchema']['properties']['code'])
        self.assertIn('计算软件', repair.build_prompt(env, [t], []))

    def test_array_items_do_not_inherit_error_template(self):
        t = tool()
        t['inputSchema']['properties']['path'] = {'type': 'array', 'items': {'type': 'string'}}
        repair.seed_descriptions({}, t)
        self.assertNotIn('description', t['inputSchema']['properties']['path']['items'])

    def test_seeded_and_existing_descriptions_are_reviewed(self):
        t = tool(); repair.seed_descriptions({}, t)
        t['inputSchema']['properties']['path']['description'] = '工作区中的目标路径。'
        batches = list(repair.chunk_tools({}, [tool()], [t], 100000))
        pointers = batches[0][1][0]['requested_schema_pointers']
        self.assertEqual(set(pointers), {target.pointer for target in repair.schema_targets(t)})
        self.assertIn('/inputSchema/properties/path', pointers)

    def filled(self):
        t = tool(); repair.seed_descriptions({}, t)
        for target in repair.schema_targets(t):
            target.node.setdefault('description', '当前业务对象的具体取值。')
        return {'tools': [t]}

    def test_validate_rejects_misplaced_error_template(self):
        document = self.filled(); repaired = copy.deepcopy(document)
        repaired['tools'][0]['inputSchema']['properties']['path']['description'] = repair.RESULT_FIELD_DESCRIPTIONS['path']
        with self.assertRaisesRegex(ValueError, '错用了结果信封'):
            repair.validate_repaired(document, repaired)

    def test_validate_rejects_inherited_error_template(self):
        document = self.filled(); t = document['tools'][0]
        t['inputSchema']['properties']['path'] = {'type': 'array', 'description': '配置路径段。', 'items': {'type': 'string', 'description': repair.RESULT_FIELD_DESCRIPTIONS['path'].rstrip('。') + '中的单个数组元素。'}}
        with self.assertRaisesRegex(ValueError, '错用了结果信封'):
            repair.validate_repaired(document, copy.deepcopy(document))

    def test_valid_description_only_repair(self):
        document = self.filled(); repaired = copy.deepcopy(document)
        repaired['tools'][0]['inputSchema']['properties']['path']['description'] = '工作区中要读取的目标文件路径。'
        self.assertEqual(repair.validate_repaired(document, repaired)['tools'], 1)

    def test_schema_and_internal_changes_are_rejected(self):
        for mutate in [lambda t: t['inputSchema']['properties']['path'].update(type='integer'), lambda t: t.update(internal={'code': 'changed'})]:
            document = self.filled(); repaired = copy.deepcopy(document); mutate(repaired['tools'][0])
            with self.assertRaisesRegex(ValueError, '不相等'):
                repair.validate_repaired(document, repaired)

    def test_portability_checked_even_when_baseline_has_same_pattern(self):
        document = self.filled()
        document['tools'][0]['inputSchema']['properties']['path']['pattern'] = '(?i).*'
        with self.assertRaisesRegex(ValueError, 'Schema 不兼容'):
            repair.validate_repaired(document, copy.deepcopy(document))

    def test_property_named_description_is_preserved(self):
        schema = {'description': 'root annotation', 'properties': {'description': {'type': 'string', 'description': 'business text'}}}
        self.assertEqual(repair.strip_schema_descriptions(schema), {'properties': {'description': {'type': 'string'}}})

    def test_large_tool_review_splits_without_omitting_fields(self):
        t = tool()
        for i in range(80):
            t['inputSchema']['properties'][f'field_{i}'] = {'type': 'string', 'description': '已填写且需要语义复核的业务说明。'}
        batches = list(repair.chunk_tools({}, [tool()], [t], 100000, 2000))
        pointers = [p for _, items in batches for item in items for p in item['requested_schema_pointers']]
        self.assertGreater(len(batches), 1)
        self.assertEqual(len(pointers), len(set(pointers)))
        self.assertEqual(set(pointers), {target.pointer for target in repair.schema_targets(t)})
        self.assertTrue(any(item['current_seeded_descriptions'] for _, items in batches for item in items))

    def test_model_response_with_misplaced_template_is_retried(self):
        item = {'name': 'inspect', 'requested_schema_pointers': ['/inputSchema/properties/path']}
        with self.assertRaisesRegex(ValueError, '错用了结果信封'):
            repair.validate_response({'tools': [{'name': 'inspect', 'tool_description': None, 'schema_descriptions': {'/inputSchema/properties/path': repair.RESULT_FIELD_DESCRIPTIONS['path']}}]}, [item])

    def test_business_data_and_numeric_error_are_not_seeded_as_envelopes(self):
        t = tool()
        t['inputSchema']['properties']['data'] = {'type': 'array', 'items': {'type': 'number'}}
        t['inputSchema']['properties']['error'] = {'type': 'array', 'items': {'type': 'number'}}
        t['outputSchema']['oneOf'][0]['properties']['data']['properties']['error'] = {'type': 'integer'}
        repair.seed_descriptions({}, t)
        for name in ['data', 'error']:
            self.assertNotIn('description', t['inputSchema']['properties'][name])
        self.assertNotIn('description', t['outputSchema']['oneOf'][0]['properties']['data']['properties']['error'])

    def test_model_cannot_return_unrequested_pointer(self):
        item = {'name': 'inspect', 'requested_schema_pointers': ['/inputSchema/properties/path']}
        with self.assertRaisesRegex(ValueError, 'extra='):
            repair.validate_response({'tools': [{'name': 'inspect', 'tool_description': None, 'schema_descriptions': {'/inputSchema/properties/path': '目标文件。', '/unrequested': 'extra'}}]}, [item])

    def test_model_contract_findings_block_description_publication(self):
        with self.assertRaisesRegex(ValueError, '同步修复Schema或实现'):
            repair.validate_response({'contract_findings': [{'name': 'sweep', 'issue': '原输入被覆盖'}], 'tools': []}, [])

    def test_semantic_review_covers_operational_constraints(self):
        prompt = repair.build_prompt({}, [tool()], [])
        for word in ['required', '空字符串', '升降序', '路径碰撞', 'contract_findings']:
            self.assertIn(word, prompt)

    def test_review_receipt_tracks_source_output_environment_and_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, environment = [root / name for name in ['tools.json', 'tools_repaired.json', 'environment.json']]
            for path in [source, output, environment]:
                path.write_text('{}')
            self.assertFalse(repair.review_receipt_matches(source, output, environment))
            receipt_path = output.with_name('tools_repaired.review.json')
            import json
            receipt_path.write_text(json.dumps(repair.review_receipt(source, output, environment)))
            self.assertTrue(repair.review_receipt_matches(source, output, environment))
            for path in [source, output, environment]:
                path.write_text('{"updated":true}')
                self.assertFalse(repair.review_receipt_matches(source, output, environment))
                path.write_text('{}')
            receipt = repair.review_receipt(source, output, environment)
            receipt['semantic_review_version'] -= 1
            receipt_path.write_text(json.dumps(receipt))
            self.assertFalse(repair.review_receipt_matches(source, output, environment))


if __name__ == '__main__':
    unittest.main()
