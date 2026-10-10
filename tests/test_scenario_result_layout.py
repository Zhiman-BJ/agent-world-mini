"""Guard publication against duplicate scenes, content loss and snapshot rewrites."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seed_gen.scripts import build_joint_scenario_seeds as builder
from seed_gen.scripts.merge_scenario_results import collect_partitions


def seed(sid, *, index=None):
    return {'global_id': 'semiconductor_scenario_' + sid.replace('.', '_'),
            'schema_version': 'scenario-1.1',
            'environment': {'basic_info': {'name': sid, 'source': 'deep_research',
                                           'index': index if index is not None else int(sid.replace('.', ''))},
                            'domain': {'level1': '半导体 ' + sid[:2], 'level3': sid + ' 场景'},
                            'nums': {'class': 0, 'function': 0, 'class_func': 0, 'all_func': 0}},
            'init_ref_tools': [], 'init_ref_tasks': ['原任务'], 'others': {'pypi_package': []}}


class ScenarioResultLayoutTests(unittest.TestCase):
    def test_merge_preserves_metadata_and_original_partitions(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            records = [seed('01.01.01'), seed('02.01.01')]
            before = {}
            for record in records:
                path = out / ('semiconductor_scenario_' + record['environment']['domain']['level3'][:2] + '.json')
                path.write_text(json.dumps([record], ensure_ascii=False), encoding='utf-8')
                before[path] = path.read_bytes()
            self.assertEqual(collect_partitions(out), records)
            self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_rejects_wrong_partition_duplicates_and_incorrect_counts(self):
        original = seed('01.01.01')
        duplicate_global = seed('01.01.02')
        duplicate_global['global_id'] = original['global_id']
        bad_count = copy.deepcopy(original)
        bad_count['environment']['nums']['all_func'] = 1
        duplicate_index = seed('01.01.02', index=original['environment']['basic_info']['index'])
        cases = [([seed('02.01.01')], 'wrong L1'), ([original, original], 'Duplicate L3'),
                 ([original, duplicate_global], 'Duplicate global'),
                 ([original, duplicate_index], 'Duplicate scenario index'),
                 ([bad_count], 'Count mismatch')]
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for payload, error in cases:
                with self.subTest(error=error):
                    (out/'semiconductor_scenario_01.json').write_text(json.dumps(payload), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, error):
                        collect_partitions(out)

    def test_builder_keeps_preserved_snapshot_bytes_and_separates_audits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profiles, out, audits = root/'profiles', root/'final', root/'local_reports'
            profiles.mkdir()
            out.mkdir()
            fresh, preserved_seed = seed('01.01.01'), seed('02.01.01')
            (profiles/'01.01.01.json').write_text(json.dumps({'scenario_id': '01.01.01', 'index': 1}), encoding='utf-8')
            preserved = out/'semiconductor_scenario_02.json'
            preserved.write_text(json.dumps([preserved_seed], ensure_ascii=False), encoding='utf-8')
            before = preserved.read_bytes()
            report = {'nums': fresh['environment']['nums'], 'candidate_count': 0,
                      'missing_source_descriptions': [], 'construction_gaps': [],
                      'pilot_task_gate_passed': True, 'runtime_reports': []}
            argv = ['build', '--profiles', str(profiles), '--output-dir', str(out),
                    '--scene-dir', str(out/'l3'), '--reports-dir', str(audits),
                    '--include-seeds', str(preserved), '--group-by-l1',
                    '--merged-name', 'semiconductor_scenario_collection.json']
            with patch.object(builder, 'build_profile', return_value=([fresh], report)), patch('sys.argv', argv):
                builder.main()
            self.assertEqual(preserved.read_bytes(), before)
            self.assertEqual(builder.read(out/'semiconductor_scenario_collection.json'), [fresh, preserved_seed])
            self.assertEqual(builder.read(out/'l3/02.01.01.json'), [preserved_seed])
            self.assertFalse((out/'01.01.01.json').exists())
            self.assertFalse((out/'reports').exists())
            self.assertEqual([s['scenario_id'] for s in builder.read(audits/'summary.json')], ['01.01.01'])
            with patch.object(builder, 'build_profile', return_value=([fresh], report)), patch('sys.argv', argv+['--check']):
                builder.main()


if __name__ == '__main__':
    unittest.main()
