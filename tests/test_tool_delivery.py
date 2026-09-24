from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from env_gen.tool_gen.delivery import publish


class ToolDeliveryTests(unittest.TestCase):
    def test_publishes_one_environment_as_a_self_contained_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "state").mkdir()
            (source / "state/records.sqlite").write_bytes(b"")
            (source / "environment.json").write_text(
                json.dumps({"environment_id": "support_workspace"}), encoding="utf-8"
            )
            (source / "validation.json").write_text(
                json.dumps({"valid": True}), encoding="utf-8"
            )
            software_root = source / "tool_generation/software"
            (software_root / "python/bin").mkdir(parents=True)
            (software_root / "python/bin/python").write_text("", encoding="utf-8")
            (source / "tool_generation").mkdir(exist_ok=True)
            (source / "tool_generation/software_environment.json").write_text(
                json.dumps(
                    {
                        "root": str(software_root),
                        "python": str(software_root / "python/bin/python"),
                        "prefix": str(software_root / "python"),
                        "plan": {"python": "3.11", "python_packages": ["numpy"]},
                    }
                ),
                encoding="utf-8",
            )
            tools = source / "tools.json"
            tools.write_text(
                json.dumps({"environment_id": "support_workspace", "tools": []}),
                encoding="utf-8",
            )
            validation = source / "tool_validation.json"
            validation.write_text(json.dumps({"reports": []}), encoding="utf-8")
            grounding = source / "tool_grounding.json"
            grounding.write_text(json.dumps({"tools": []}), encoding="utf-8")
            action_plan = source / "action_plan.json"
            action_plan.write_text(json.dumps({"actions": []}), encoding="utf-8")

            result = SimpleNamespace(
                package_root=source,
                environment_path=source / "environment.json",
                tools_path=tools,
                validation_path=validation,
                grounding_path=grounding,
                action_plan_path=action_plan,
            )
            delivery = publish(result, root / "delivery")

            package = root / "delivery/environments/source"
            self.assertEqual(delivery.package_root, package)
            self.assertTrue((package / "binding.json").is_file())
            self.assertTrue((package / "environment/environment.json").is_file())
            self.assertTrue((package / "tools/tools.json").is_file())
            self.assertTrue((package / "software/profile.json").is_file())
            self.assertTrue((package / "runtime/runtime.json").is_file())
            self.assertFalse((root / "delivery/tools").exists())
            self.assertFalse((root / "delivery/bindings").exists())
            mapping = json.loads(
                (package / "software/profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(mapping["python_path"].split("/", 3)[-1], "python/bin/python")
            profile_root = root / "delivery" / mapping["profile_path"]
            self.assertTrue((profile_root / "python/bin/python").is_file())

            binding = json.loads((package / "binding.json").read_text(encoding="utf-8"))
            self.assertEqual(binding["package_path"], "environments/source")
            self.assertEqual(
                binding["tools_path"], "environments/source/tools/tools.json"
            )
            self.assertEqual(
                binding["environment_path"], "environments/source/environment"
            )
            self.assertEqual(
                binding["software_mapping_path"],
                "environments/source/software/profile.json",
            )
            self.assertEqual(
                binding["runtime_path"],
                "environments/source/runtime/runtime.json",
            )
            runtime = json.loads(
                (package / "runtime/runtime.json").read_text(encoding="utf-8")
            )
            self.assertEqual(runtime["backend"], "python_profile")
            self.assertEqual(runtime["profile_id"], binding["software_profile"])

            (source / "tool_generation/container_runtime.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "backend": "docker",
                        "image": "agentworld/kicad:9.0",
                        "delivery_mount": "/delivery",
                        "code_mount": "/opt/agent-world",
                        "software_root": "/opt/tool-software",
                        "python_command": "python",
                    }
                ),
                encoding="utf-8",
            )
            docker_delivery = publish(result, root / "docker_delivery")
            docker_binding = json.loads(
                docker_delivery.binding_path.read_text(encoding="utf-8")
            )
            self.assertIsNone(docker_binding["software_profile"])
            self.assertIsNone(docker_binding["software_profile_path"])
            docker_mapping = json.loads(
                (docker_delivery.software_mapping_root / "profile.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNone(docker_mapping["profile_id"])
            docker_runtime = json.loads(
                (docker_delivery.runtime_root / "runtime.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(docker_runtime["image"], "agentworld/kicad:9.0")
            self.assertFalse((root / "docker_delivery/software_profiles").exists())


if __name__ == "__main__":
    unittest.main()
