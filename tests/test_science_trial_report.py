import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import report_three_search_trial as report


class RecoveryReportTests(unittest.TestCase):
    def test_skipped_composition_cannot_overwrite_completed_execution_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'reports').mkdir()
            roots = []
            for name, date, success in [('runtime_retry', '20260918_01', True),
                                        ('compose_retry', '20260918_02', False)]:
                runs = root / name
                roots.append(runs)
                run = runs / 'example' / date
                (run / 'intermediate').mkdir(parents=True)
                (run / 'run.json').write_text(json.dumps({'status': 'completed'}))
                task = {'task_id': 'task1', 'task_text': 'Recovered task',
                        'execution': {'success': success, 'tool_calls': []},
                        'validation': {'passed': success, 'errors': []}}
                (run / 'intermediate/step_5_bundle.json').write_text(json.dumps({'tasks': [task]}))
            with patch.object(report, 'ROOT', root):
                report.main(roots, report_name='result.md')
            latest = (root / 'reports/result.md').read_text().split('## 各轮完整记录')[0]
            self.assertIn('### example / task1', latest)
            self.assertIn('Recovered task', latest)
