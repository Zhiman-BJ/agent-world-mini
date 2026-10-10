from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from env_gen.tool_gen.delivery import _publish_software_profile, publish


class SoftwareDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "profiles/profile"
        self.requirements = self.root / "requirements.txt"

    def file(self, relative, content="runtime"):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def link(self, relative, target):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        return path

    def publish(self):
        _publish_software_profile(self.source, self.destination, self.requirements)

    def test_build_artifacts_and_system_documentation_are_excluded(self):
        self.file("python/lib/solver.so")
        self.file("python/lib/site-packages/library/cache/data.json")
        self.link("cache/conda/lib.so", "missing.so")
        self.link("system/usr/share/doc/libgfortran5", "gcc-12-base")
        self.file("python-3.12-uv-failed/partial")
        self.publish()
        self.assertTrue((self.destination / "python/lib/solver.so").is_file())
        self.assertTrue((self.destination / "python/lib/site-packages/library/cache/data.json").is_file())
        for name in ("cache", "system/usr/share/doc", "python-3.12-uv-failed"):
            self.assertFalse((self.destination / name).exists())

    def test_internal_relative_and_absolute_links_survive_relocation(self):
        target = self.file("python/lib/libsolver.so.1")
        self.link("python/lib/libsolver.so", "libsolver.so.1")
        self.link("python/lib/absolute.so", target)
        self.publish()
        self.source.rename(self.root / "source-moved")
        for name in ("libsolver.so", "absolute.so"):
            link = self.destination / "python/lib" / name
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.read_text(), "runtime")
            self.assertFalse(link.readlink().is_absolute())

    def test_conda_package_cache_is_excluded_and_runtime_is_preserved(self):
        self.link("conda-pkgs/_openmp_mutex/lib/libgomp.so.1", "libomp.so")
        self.file("python-3.11/lib/libgomp.so.1.0.0", "installed OpenMP")
        self.link("python-3.11/lib/libgomp.so.1", "libgomp.so.1.0.0")
        self.file("python-3.11/lib/site-packages/library/conda-pkgs/data.json")
        self.publish()
        self.assertFalse((self.destination / "conda-pkgs").exists())
        self.assertEqual((self.destination / "python-3.11/lib/libgomp.so.1").read_text(), "installed OpenMP")
        self.assertTrue((self.destination / "python-3.11/lib/site-packages/library/conda-pkgs/data.json").is_file())

    def test_runtime_reference_into_conda_cache_is_materialized(self):
        target = self.file("conda-pkgs/solver/lib/libsolver.so", "solver runtime")
        self.link("python/lib/libsolver.so", target)
        self.publish()
        shutil.rmtree(self.source / "conda-pkgs")
        self.assertEqual((self.destination / "python/lib/libsolver.so").read_text(), "solver runtime")
        self.assertFalse((self.destination / "conda-pkgs").exists())

    def test_shared_external_interpreter_keeps_same_server_mapping(self):
        target = self.root / "shared/python"
        target.parent.mkdir()
        target.write_text("shared interpreter")
        self.link("python/bin/python", "../../../shared/python")
        self.publish()
        link = self.destination / "python/bin/python"
        self.assertEqual(link.resolve(), target)
        self.assertEqual(link.read_text(), "shared interpreter")

    def test_required_cache_target_is_materialized(self):
        target = self.file("cache/packages/libsolver.so.1")
        self.link("python/lib/libsolver.so", target)
        self.publish()
        shutil.rmtree(self.source / "cache")
        self.assertEqual((self.destination / "python/lib/libsolver.so").read_text(), "runtime")
        self.assertFalse((self.destination / "cache").exists())

    def test_broken_runtime_link_prevents_profile_publication(self):
        self.link("python/lib/libsolver.so", "missing.so")
        with self.assertRaisesRegex(FileNotFoundError, "软件运行依赖链接失效"):
            self.publish()
        self.assertFalse(self.destination.exists())

    def test_existing_profile_is_reused(self):
        self.file("python/lib/solver.so", "first")
        self.publish()
        self.file("python/lib/solver.so", "second")
        self.publish()
        self.assertEqual((self.destination / "python/lib/solver.so").read_text(), "first")


class EnvironmentPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name, content in {
            "environment.json": {"environment_id": "example"},
            "validation.json": {"valid": True},
            "tools.json": {"tools": []},
            "tool_validation.json": {"reports": []},
            "tool_grounding.json": {"tools": []},
            "action_plan.json": {"actions": []},
        }.items():
            (self.source / name).write_text(json.dumps(content))
        self.software = self.source / "tool_generation/software"
        (self.software / "python/bin").mkdir(parents=True)
        (self.software / "python/bin/python").write_text("interpreter")
        (self.source / "tool_generation/software_environment.json").write_text(json.dumps({
            "root": str(self.software), "python": str(self.software / "python/bin/python"),
            "prefix": str(self.software / "python"), "plan": {"python": "3.11"},
        }))
        self.result = SimpleNamespace(
            package_root=self.source, environment_path=self.source / "environment.json",
            tools_path=self.source / "tools.json", validation_path=self.source / "tool_validation.json",
            grounding_path=self.source / "tool_grounding.json", action_plan_path=self.source / "action_plan.json",
        )
        self.output = self.root / "delivery"
        self.package = self.output / "environments/source"

    def test_failed_software_publication_leaves_no_environment_package(self):
        (self.software / "python/bin/missing").symlink_to("missing-target")
        with self.assertRaises(FileNotFoundError):
            publish(self.result, self.output)
        self.assertFalse(self.package.exists())

    def test_failed_update_preserves_previous_delivery(self):
        publish(self.result, self.output)
        before = (self.package / "tools/tools.json").read_bytes()
        self.result.tools_path = self.source / "missing-tools.json"
        with self.assertRaises(FileNotFoundError):
            publish(self.result, self.output)
        self.assertEqual((self.package / "tools/tools.json").read_bytes(), before)
        self.assertTrue((self.package / "binding.json").is_file())

    def test_package_rename_failure_restores_previous_delivery(self):
        publish(self.result, self.output)
        before = (self.package / "tools/tools.json").read_bytes()
        original = Path.rename

        def fail_new_package(path, target):
            if path.name == "package":
                raise OSError("simulated publication failure")
            return original(path, target)

        with patch.object(Path, "rename", fail_new_package):
            with self.assertRaisesRegex(OSError, "simulated publication failure"):
                publish(self.result, self.output)
        self.assertEqual((self.package / "tools/tools.json").read_bytes(), before)
        self.assertTrue((self.package / "binding.json").is_file())

    def test_successful_update_publishes_tools_and_binding_together(self):
        publish(self.result, self.output)
        self.result.tools_path.write_text(json.dumps({"tools": [{"name": "updated"}]}))
        publish(self.result, self.output)
        self.assertEqual(json.loads((self.package / "tools/tools.json").read_text())["tools"][0]["name"], "updated")
        self.assertTrue((self.package / "binding.json").is_file())


if __name__ == "__main__":
    unittest.main()
