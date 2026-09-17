"""Prevent omitted freeform scenes and drift in normalized classifications."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from seed_gen.scripts.prepare_scenario_inventory import SOURCE, normalize
from seed_gen.scripts.build_joint_scenario_seeds import build_profile, classification_rows


class ScenarioInventoryTests(unittest.TestCase):
    def test_all_current_classifications_including_freeform_are_accounted_for(self):
        rows = normalize(SOURCE.read_text(encoding='utf-8'))
        self.assertEqual(len(rows), 160)
        self.assertEqual(sum(r['collection_scope'] == 'collect' for r in rows), 138)
        self.assertEqual(sum(r['normalization'] == 'explicit_editorial_supplement' for r in rows), 20)
        self.assertEqual(next(r for r in rows if r['scenario_id']=='08.02.05')['domain']['level2'], '08.02 Quality / RCA')

    def test_heading_hierarchy_and_alternative_application_headers(self):
        text = '# 06 Fab\n## 06.02 FDC\n| L3 | 工程任务 | 新包 |\n| --- | --- | --- |\n| **06.02.01 Features** | trace features | tsfresh |\n'
        row = normalize(text)[0]
        self.assertEqual(row['application'], 'trace features')
        self.assertEqual(row['domain']['level2'], '06.02 FDC')

    def test_missing_boundary_is_an_error_not_a_silently_skipped_scene(self):
        text = '# 03 Design\n| L2 | L3 | 场景 | 包 |\n| --- | --- | --- | --- |\n| 03.01 Layout | 03.01.01 PCell | | gdsfactory |\n'
        with self.assertRaisesRegex(ValueError, 'Unresolved boundary'):
            normalize(text)

    def test_unknown_freeform_scene_needs_explicit_supplement(self):
        with self.assertRaises(KeyError):
            normalize('# 06 Fab\n## 06.09 New\n06.09.01 Unknown\n')

    def test_normalized_rows_are_all_compatible_with_existing_builder(self):
        text = Path('seed_gen/scenario_collection/classification.normalized.md').read_text(encoding='utf-8')
        self.assertEqual(len(classification_rows(text)), 160)

    def test_changed_normalized_application_rejected_even_with_new_local_hash(self):
        original = '# 01 Materials\n| L2 | L3 | 实际应用场景 | 代表包 |\n| --- | --- | --- | --- |\n| 01.01 Structure | 01.01.01 Crystal | build crystal | pymatgen |\n'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'original.md').write_text(original,encoding='utf-8')
            (root/'normalized.md').write_text(original.replace('build crystal','unrelated task'),encoding='utf-8')
            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            profile = {'scenario_id':'01.01.01','classification_source':'normalized.md',
                       'classification_sha256':sha(root/'normalized.md'),
                       'classification_upstream':{'path':'original.md','sha256':sha(root/'original.md')}}
            with self.assertRaisesRegex(ValueError, 'does not match'):
                build_profile(profile,root=root)


if __name__ == '__main__':
    unittest.main()
