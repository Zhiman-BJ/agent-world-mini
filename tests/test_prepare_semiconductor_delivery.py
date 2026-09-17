from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_semiconductor_delivery import IMPORTS, _check_imports, prepare_delivery


class PrepareSemiconductorDeliveryTests(unittest.TestCase):
    def test_copies_and_corrects_launchers_from_generation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            upstream = Path(temporary) / "upstream"
            source = upstream / "delivery"
            destination = Path(temporary) / "corrected"
            packages = sorted(IMPORTS)
            for index, package in enumerate(packages):
                self._add_package(
                    source,
                    upstream,
                    package,
                    f"profile-{index}",
                    bad=index == 0,
                )
            payload = source / f"environments/{packages[0]}/environment/state/payload.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("unchanged\n", encoding="utf-8")

            def imports_ok(_launcher: Path, modules: list[str]) -> dict[str, object]:
                return {"imports": {module: "ok" for module in modules}}

            with patch(
                "scripts.prepare_semiconductor_delivery._check_imports",
                side_effect=imports_ok,
            ):
                manifest = prepare_delivery(source, destination)

            corrected = json.loads(
                (destination / f"environments/{packages[0]}/software/profile.json").read_text()
            )
            self.assertEqual(
                corrected["python_path"],
                "software_profiles/profiles/profile-0/python-3.11/bin/python",
            )
            config = destination / "software_profiles/profiles/profile-0/python-3.11/pyvenv.cfg"
            self.assertIn(
                f"home = {destination}/software_profiles/profiles/profile-0/interpreters/cpython/bin",
                config.read_text(),
            )
            self.assertEqual(payload.read_bytes(), (destination / payload.relative_to(source)).read_bytes())
            self.assertNotEqual(payload.stat().st_ino, (destination / payload.relative_to(source)).stat().st_ino)
            self.assertEqual(
                [item["python_path"]["changed"] for item in manifest["corrections"]],
                [True, False, False, False, False, False, False, False],
            )
            self.assertEqual(
                manifest["portability"]["destination_bound_profiles"],
                packages,
            )
            self.assertEqual(manifest["import_failures"], {})
            self.assertEqual(
                manifest,
                json.loads((destination / "delivery_corrections.json").read_text()),
            )

            with self.assertRaisesRegex(FileExistsError, "destination already exists"):
                prepare_delivery(source, destination)

            alias = Path(temporary) / "source-alias"
            alias.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "destination cannot be inside source delivery"):
                prepare_delivery(source, alias / "nested")
            self.assertFalse((source / "nested").exists())

    def test_rejects_incomplete_or_unknown_environment_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            upstream = Path(temporary) / "upstream"
            source = upstream / "delivery"
            self._add_package(source, upstream, "unknown", "profile", bad=False)

            with self.assertRaisesRegex(ValueError, "missing.*unexpected"):
                prepare_delivery(source, Path(temporary) / "corrected")

    def test_import_probe_disables_bytecode_and_redirects_home_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture.json"
            launcher = root / "launcher"
            launcher.write_text(
                f"""#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
Path(os.environ["TEST_CAPTURE"]).write_text(json.dumps({{
    "argv": sys.argv[1:],
    "home": os.environ.get("HOME"),
    "xdg_cache_home": os.environ.get("XDG_CACHE_HOME"),
}}))
raise SystemExit(subprocess.call([os.environ["TEST_PYTHON"], *sys.argv[1:]], env=os.environ))
""",
                encoding="utf-8",
            )
            launcher.chmod(0o755)

            with patch.dict(
                os.environ,
                {"TEST_CAPTURE": str(capture), "TEST_PYTHON": sys.executable},
            ):
                result = _check_imports(launcher, ["json"])

            recorded = json.loads(capture.read_text())
            self.assertEqual(result["imports"], {"json": "ok"})
            self.assertEqual(recorded["argv"][:2], ["-B", "-I"])
            self.assertNotEqual(recorded["home"], str(Path.home()))
            self.assertEqual(
                Path(recorded["home"]).parent,
                Path(recorded["xdg_cache_home"]).parent,
            )

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
