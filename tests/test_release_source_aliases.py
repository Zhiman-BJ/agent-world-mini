"""Case-only source aliases must preserve exact release identity."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from seed_gen.scripts.extract_release_python_seeds import build_seed


class SourceAliasTest(unittest.TestCase):
    def test_identical_blob_alias_and_mismatch(self):
        # Emulate a case-insensitive checkout independently of the host OS.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'pkg'
            (root/'pkg').mkdir(parents=True)
            data = b'def action(value):\n    """Return a value."""\n    return value\n'
            (root/'pkg'/'plotBands.py').write_bytes(data)
            def fake_git(directory, *args):
                if args == ('rev-parse', 'HEAD') or args == ('rev-parse', 'v1^{commit}'):
                    return 'commit'
                if args[0] == 'rev-parse' and ':' in args[1]:
                    return 'blob'
                if args[0] == 'hash-object':
                    return 'blob'
                return ''
            spec = dict(name='pkg', index=1, directory='pkg', modules=['pkg'], tag='v1', commit='commit',
                        repository='https://example.com/pkg', documentation='https://example.com/docs', pypi='',
                        pypi_version='1', checked_on='2026-09-17', release_published_at='', github_prerelease=False,
                        description='fixture', notes=[], identical_source_aliases={'pkg/plotbands.py': 'pkg/plotBands.py'})
            # File-hash reads for the alias use the same bytes as on Windows.
            original_read = Path.read_bytes
            def read_bytes(path):
                return data if path.as_posix().endswith('/pkg/plotbands.py') else original_read(path)
            with patch('seed_gen.scripts.extract_release_python_seeds.git', side_effect=fake_git), patch.object(Path, 'read_bytes', read_bytes):
                payload = build_seed(spec, Path(temp))[0]
                self.assertEqual({t['module'] for t in payload['init_ref_tools']}, {'pkg.plotBands', 'pkg.plotbands'})
                self.assertEqual(payload['environment']['nums']['function'], 2)
                hashes = payload['others']['python_source_extraction']['source_file_sha256']
                self.assertEqual(set(hashes), {'pkg/plotBands.py', 'pkg/plotbands.py'})
                self.assertEqual(set(hashes.values()), {hashlib.sha256(data).hexdigest()})
            def mismatching_git(directory, *args):
                if args == ('rev-parse', 'commit:pkg/plotbands.py'):
                    return 'different_blob'
                return fake_git(directory, *args)
            with patch('seed_gen.scripts.extract_release_python_seeds.git', side_effect=mismatching_git):
                with self.assertRaisesRegex(ValueError, 'Non-identical source alias'):
                    build_seed(spec, Path(temp))


if __name__ == '__main__':
    unittest.main()
