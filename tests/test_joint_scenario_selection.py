"""Failure-mode tests for multi-package scenario selection and source fidelity."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seed_gen.scripts.build_joint_scenario_seeds import classification_rows, counts, select_joint, verify_raw_sources
from seed_gen.scripts.select_python_ref_tools import _canonical_sha256


def fun(module='a', name='solve'):
    return {'module': module, 'name': name, 'type': 'function', 'description': 'original docs', 'input': {}, 'output': None}


def klass():
    return {'module': 'a', 'name': 'Mesh', 'type': 'class', 'description': 'original class', 'input': '',
            'function': [{'name': n, 'description': 'original method ' + n, 'input': {}, 'output': None}
                         for n in ['__init__', 'edit', 'debug']]}


class JointSelectionTests(unittest.TestCase):
    def fixture(self):
        payloads = {name: [{'environment': {'basic_info': {'name': name, 'version': '1'}},
                           'init_ref_tools': ([klass(), fun()] if name == 'a' else [fun('b'), fun('b', 'read')])}]
                    for name in ['a', 'b']}
        profile = {'packages': [{'name': n, 'version': '1', 'raw_sha256': _canonical_sha256(p),
                                 'role': 'primary' if n == 'a' else 'complement', 'reason': 'role evidence'}
                                for n, p in payloads.items()],
                   'capabilities': [{'id': 'build'}, {'id': 'solve'}, {'id': 'io'}],
                   'symbols': [dict(module='a', name='Mesh', type='class', package='a', methods=['__init__', 'edit'], capability='build', reason='needed for state'),
                               dict(module='a', name='solve', type='function', package='a', capability='solve', reason='primary solver'),
                               dict(module='b', name='read', type='function', package='b', capability='io', reason='non-overlapping I/O')],
                   'tasks': [{'id': 'repair', 'description': 'repair state then solve', 'initial_state': 'broken input',
                              'fixture': 'fixed input', 'assertions': ['independent oracle'],
                              'steps': [{'tool_refs': ['a.Mesh.__init__', 'a.Mesh.edit', 'a.solve', 'b.read']}]}]}
        second = copy.deepcopy(profile['tasks'][0])
        second['id'] = 'refine'
        profile['tasks'].append(second)
        return profile, payloads

    def test_complement_subset_preserves_docs_and_audits_every_candidate(self):
        p, data = self.fixture()
        before = copy.deepcopy(data)
        tools, audit = select_joint(p, data)
        self.assertEqual(data, before)
        self.assertEqual(counts(tools), {'class': 1, 'function': 2, 'class_func': 2, 'all_func': 4})
        self.assertEqual(audit['candidate_count'], 4)
        self.assertFalse(audit['decisions'][0]['methods'][2]['selected'])
        self.assertFalse(next(d for d in audit['decisions'] if d['package'] == 'b' and d['name'] == 'solve')['selected'])
        self.assertEqual(tools[0]['function'], before['a'][0]['init_ref_tools'][0]['function'][:2])

    def test_candidate_hash_drift_fails(self):
        p, d = self.fixture()
        d['a'][0]['init_ref_tools'][1]['description'] = 'new source docs'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            select_joint(p, d)

    def test_reextraction_rejects_truncated_raw_even_with_matching_profile_hash(self):
        p, data = self.fixture()
        full = data['a']
        truncated = copy.deepcopy(full)
        truncated[0]['init_ref_tools'].pop()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'manifest.json').write_text(json.dumps([{'name': 'a', 'tag': '1', 'commit': 'fixed'}]), encoding='utf-8')
            (root / 'raw.json').write_text(json.dumps(truncated), encoding='utf-8')
            profile = {'packages': [{'name': 'a', 'version': '1', 'raw_path': 'raw.json',
                                     'raw_sha256': _canonical_sha256(truncated), 'release_manifest': 'manifest.json'}]}
            with patch('seed_gen.scripts.build_joint_scenario_seeds.Path.cwd', return_value=root), \
                 patch('seed_gen.scripts.extract_release_python_seeds.build_seed', return_value=full):
                with self.assertRaisesRegex(ValueError, 'no longer matches release source'):
                    verify_raw_sources(profile, root)

    def test_excluded_alternative_cannot_leak_into_tools(self):
        p, d = self.fixture()
        p['packages'][1]['role'] = 'excluded'
        with self.assertRaisesRegex(ValueError, 'Excluded alternative'):
            select_joint(p, d)

    def test_methods_and_constructor_cannot_be_lost_silently(self):
        p, d = self.fixture()
        p['symbols'][0]['methods'] = ['edit']
        with self.assertRaisesRegex(ValueError, 'Constructor removed'):
            select_joint(p, d)
        p['symbols'][0]['methods'] = ['__init__', 'invented']
        with self.assertRaisesRegex(ValueError, 'Unknown method'):
            select_joint(p, d)

    def test_uncovered_capability_rejected(self):
        p, d = self.fixture()
        p['capabilities'].append({'id': 'export'})
        with self.assertRaisesRegex(ValueError, 'Uncovered required'):
            select_joint(p, d)

    def test_unselected_task_method_rejected(self):
        p, d = self.fixture()
        p['tasks'][0]['steps'][0]['tool_refs'].append('a.Mesh.debug')
        with self.assertRaisesRegex(ValueError, 'unselected tools'):
            select_joint(p, d)

    def test_infrastructure_cannot_smuggle_unselected_api(self):
        p, d = self.fixture()
        p['runtime_infrastructure'] = [{'reference': 'a.Mesh.debug'}]
        p['tasks'][0]['steps'][0]['tool_refs'].append('a.Mesh.debug')
        with self.assertRaisesRegex(ValueError, 'cannot reintroduce'):
            select_joint(p, d)

    def test_derived_callable_requires_selected_factory_and_runtime_namespace(self):
        p, d = self.fixture()
        p['runtime_infrastructure'] = [{'reference': 'runtime.model', 'kind': 'derived_callable', 'derived_from': 'a.solve'}]
        p['tasks'][0]['steps'][0]['tool_refs'].append('runtime.model')
        select_joint(p, d)
        p['runtime_infrastructure'][0]['derived_from'] = 'b.solve'
        with self.assertRaisesRegex(ValueError, 'selected factory'):
            select_joint(p, d)

    def test_two_complete_task_recipes_required(self):
        p, d = self.fixture()
        p['tasks'].pop()
        with self.assertRaisesRegex(ValueError, 'two complete task'):
            select_joint(p, d)

    def test_duplicate_identity_and_bad_method_list_rejected(self):
        p, d = self.fixture()
        p['symbols'].append(copy.deepcopy(p['symbols'][1]))
        with self.assertRaisesRegex(ValueError, 'Duplicate selection'):
            select_joint(p, d)
        p, d = self.fixture()
        p['symbols'][0]['methods'].append('edit')
        with self.assertRaisesRegex(ValueError, 'unique method subset'):
            select_joint(p, d)

    def test_missing_documentation_requires_explicit_reason_without_rewriting(self):
        p, d = self.fixture()
        d['a'][0]['init_ref_tools'][1]['description'] = ''
        p['packages'][0]['raw_sha256'] = _canonical_sha256(d['a'])
        with self.assertRaisesRegex(ValueError, 'Empty source description'):
            select_joint(p, d)
        p['symbols'][1]['missing_description_reason'] = 'required source API has no docstring'
        tools, audit = select_joint(p, d)
        self.assertEqual(tools[1]['description'], '')
        self.assertEqual(len(audit['missing_source_descriptions']), 1)

    def test_classification_uses_l1_and_inherits_only_blank_l2(self):
        text = '# 02 器件与物理仿真\n\n| L2 | L3 | 应用场景 | Python Backend |\n| --- | --- | --- | --- |\n| 02.01 TCAD | **02.01.01 Poisson** | PN | A / B |\n| | **02.01.02 Quantum** | QW | C |\n'
        rows = classification_rows(text)
        self.assertEqual(rows['02.01.02']['domain']['level2'], '02.01 TCAD')
        self.assertEqual(rows['02.01.01']['domain']['level1'], '02 器件与物理仿真')
        self.assertEqual(rows['02.01.01']['application'], 'PN')


if __name__ == '__main__':
    unittest.main()
