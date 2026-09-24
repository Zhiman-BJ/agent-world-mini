from __future__ import annotations

import unittest

from env_gen.tool_gen.docker_runtime import dockerfile, image_name


class DockerRuntimeTests(unittest.TestCase):
    def test_renders_explicit_professional_software_recipe(self) -> None:
        plan = {
            "python": "3.11",
            "common_modules": [],
            "python_packages": ["numpy==2.1.0"],
            "node_packages": [],
            "container": {
                "base_image": "python:3.11-slim-bookworm",
                "apt_packages": ["ngspice", "libglu1-mesa"],
                "environment": {"QT_QPA_PLATFORM": "offscreen"},
            },
        }
        recipe = dockerfile(plan)
        self.assertIn("FROM python:3.11-slim-bookworm", recipe)
        self.assertIn("libglu1-mesa ngspice", recipe)
        self.assertIn('ENV QT_QPA_PLATFORM="offscreen"', recipe)
        self.assertIn("TOOLGEN_SOFTWARE_ROOT=/opt/tool-software", recipe)
        self.assertTrue(image_name(plan).startswith("agentworld/tool-runtime:"))

    def test_does_not_guess_container_packages(self) -> None:
        with self.assertRaisesRegex(ValueError, "container 配置"):
            dockerfile({"python_packages": ["numpy"]})

    def test_image_identity_ignores_non_runtime_purpose_text(self) -> None:
        first = {
            "python": "3.11",
            "python_packages": [{"name": "numpy", "version": "2.1.0", "purpose": "A"}],
            "container": {"apt_packages": []},
        }
        second = {
            "python": "3.11",
            "python_packages": [{"name": "numpy", "version": "2.1.0", "purpose": "B"}],
            "container": {"apt_packages": []},
        }
        self.assertEqual(image_name(first), image_name(second))


if __name__ == "__main__":
    unittest.main()
