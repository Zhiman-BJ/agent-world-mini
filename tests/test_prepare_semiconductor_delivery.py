from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.prepare_semiconductor_delivery import prepare_delivery


class PrepareSemiconductorDeliveryTests(unittest.TestCase):
    def test_copies_and_corrects_launchers_from_generation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            upstream = Path(temporary) / "upstream"
            source = upstream / "delivery"
            destination = Path(temporary) / "corrected"
            self._add_package(source, upstream, "needs_fix", "profile-a", bad=True)
            self._add_package(source, upstream, "already_correct", "profile-b", bad=False)
            payload = source / "environments/needs_fix/environment/state/payload.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("unchanged\n", encoding="utf-8")

            manifest = prepare_delivery(source, destination, check_imports=False)

            corrected = json.loads(
                (destination / "environments/needs_fix/software/profile.json").read_text()
            )
            self.assertEqual(
                corrected["python_path"],
                "software_profiles/profiles/profile-a/python-3.11/bin/python",
            )
            config = destination / "software_profiles/profiles/profile-a/python-3.11/pyvenv.cfg"
            self.assertIn(
                f"home = {destination}/software_profiles/profiles/profile-a/interpreters/cpython/bin",
                config.read_text(),
            )
            self.assertEqual(payload.read_bytes(), (destination / payload.relative_to(source)).read_bytes())
            self.assertNotEqual(payload.stat().st_ino, (destination / payload.relative_to(source)).stat().st_ino)
            self.assertEqual(
                [item["python_path"]["changed"] for item in manifest["corrections"]],
                [False, True],
            )
            self.assertEqual(
                manifest["portability"]["destination_bound_profiles"],
                ["already_correct", "needs_fix"],
            )
            self.assertEqual(manifest["import_failures"], {})
            self.assertEqual(
                manifest,
                json.loads((destination / "delivery_corrections.json").read_text()),
            )

            with self.assertRaisesRegex(FileExistsError, "destination already exists"):
                prepare_delivery(source, destination, check_imports=False)

    @staticmethod
    def _add_package(
        source: Path,
        upstream: Path,
        package: str,
        profile: str,
        *,
        bad: bool,
    ) -> None:
        profile_root = source / f"software_profiles/profiles/{profile}"
        launcher = profile_root / "python-3.11/bin/python"
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(sys.executable)
        base = profile_root / "interpreters/cpython/bin/python3.11"
        base.parent.mkdir(parents=True)
        base.symlink_to(sys.executable)

        mapping = source / f"environments/{package}/software/profile.json"
        mapping.parent.mkdir(parents=True)
        mapping.write_text(
            json.dumps(
                {
                    "profile_id": profile,
                    "profile_path": f"software_profiles/profiles/{profile}",
                    "python_path": (
                        f"software_profiles/profiles/{profile}/interpreters/cpython/bin/python3.11"
                        if bad
                        else f"software_profiles/profiles/{profile}/python-3.11/bin/python"
                    ),
                    "requirements_path": f"software_profiles/profiles/{profile}/requirements.txt",
                }
            ),
            encoding="utf-8",
        )
        (profile_root / "requirements.txt").write_text("jsonschema\n", encoding="utf-8")

        metadata = upstream / f"environments/{package}/tool_generation/software_environment.json"
        metadata.parent.mkdir(parents=True)
        generation_root = metadata.parent / "software"
        generation_home = generation_root / "interpreters/cpython/bin"
        generation_home.mkdir(parents=True)
        generation_venv = generation_root / "python-3.11"
        generation_venv.mkdir(parents=True)
        config = f"home = {generation_home}\ninclude-system-site-packages = false\n"
        (generation_venv / "pyvenv.cfg").write_text(config, encoding="utf-8")
        (profile_root / "python-3.11/pyvenv.cfg").write_text(config, encoding="utf-8")
        metadata.write_text(
            json.dumps(
                {
                    "root": str(generation_root),
                    "python": str(generation_root / "python-3.11/bin/python"),
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
