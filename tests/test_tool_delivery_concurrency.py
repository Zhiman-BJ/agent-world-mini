from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from env_gen.tool_gen.delivery import _publish_software_profile


class ToolDeliveryConcurrencyTests(unittest.TestCase):
    def test_same_software_profile_can_be_published_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "package.bin").write_bytes(b"profile")
            requirements = root / "requirements.txt"
            requirements.write_text("numpy==2.3.0\n", encoding="utf-8")
            destination = root / "profiles/profile-a"

            with ThreadPoolExecutor(max_workers=4) as executor:
                results = [
                    executor.submit(
                        _publish_software_profile, source, destination, requirements
                    )
                    for _ in range(4)
                ]
                for result in results:
                    result.result()

            self.assertEqual((destination / "package.bin").read_bytes(), b"profile")
            self.assertEqual(
                (destination / "requirements.txt").read_text(encoding="utf-8"),
                "numpy==2.3.0\n",
            )
