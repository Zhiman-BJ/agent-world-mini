import json
from pathlib import Path
import tempfile
import unittest

from task_gen.tool_graph.codex import CodexAgentClient


class CodexSearchTest(unittest.TestCase):
    def test_search_switch_overrides_cli_cached_search_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'codex'
            fake.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.stdin.read()
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(json.dumps(sys.argv[1:]))
''')
            fake.chmod(0o755)
            for enabled, mode in ((False, 'disabled'), (True, 'live')):
                with self.subTest(enabled=enabled):
                    client = CodexAgentClient(executable=str(fake), enable_web_search=enabled)
                    args = json.loads(client.run('probe', working_directory=root))
                    self.assertIn(f'web_search="{mode}"', args)


if __name__ == '__main__':
    unittest.main()
