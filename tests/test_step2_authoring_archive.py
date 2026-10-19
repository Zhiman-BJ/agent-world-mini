from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from task_gen.program.step_2_solution_generate import _archive_authoring_quality_artifacts
from task_gen.program.utils.io import read_json, write_json


class Step2AuthoringArchiveTests(unittest.TestCase):
    def archive(self, root: Path) -> Path:
        _archive_authoring_quality_artifacts(
            authoring=root / "authoring", output_dir=root / "output", round_index=1,
            review_root=root / "reviews",
        )
        return root / "output/step2_authoring/round_01"

    def test_malformed_draft_is_preserved_without_becoming_a_submission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authoring = root / "authoring"
            authoring.mkdir()
            content = b'{"solution_code": "unfinished\n'
            (authoring / "candidate.draft.json").write_bytes(content)

            archive = self.archive(root)

            self.assertEqual((archive / "files/candidate.draft.json").read_bytes(), content)
            manifest = read_json(archive / "archive_manifest.json")
            self.assertIs(manifest["diagnostic_only"], True)
            self.assertIs(manifest["approval_evidence"], False)
            self.assertEqual(manifest["files"], [{
                "path": "files/candidate.draft.json", "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }])
            self.assertFalse((authoring / "candidates.json").exists())
            self.assertFalse((root / "output/step2_candidates").exists())
            self.assertIsNone(read_json(root / "output/step2_preflight/round_01.json")["receipt"])

    def test_last_requested_root_candidate_is_preserved_with_original_feedback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authoring = root / "authoring"
            authoring.mkdir()
            write_json(authoring / "revision.json", {"candidates": [{"task_public": "draft"}]})
            request = {"request_id": "last", "candidate": "revision.json"}
            write_json(authoring / "candidate_check_request.json", request)
            result = {"stage": "execution", "passed": False, "errors": [{"code": "timeout"}]}
            write_json(authoring / "candidate_check_result.json", result)

            archive = self.archive(root)

            self.assertEqual(read_json(archive / "files/revision.json"), {"candidates": [{"task_public": "draft"}]})
            self.assertEqual(read_json(archive / "files/candidate_check_request.json"), request)
            preflight = read_json(root / "output/step2_preflight/round_01.json")
            self.assertEqual(preflight["last_result"], result)
            self.assertEqual(preflight["history"], [])

    def test_outside_paths_and_candidate_symlinks_are_not_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authoring = root / "authoring"
            authoring.mkdir()
            outside = root / "outside.json"
            outside.write_text("outside", encoding="utf-8")
            (authoring / "candidate.draft.json").symlink_to(outside)
            write_json(authoring / "candidate_check_request.json", {"candidate": "../outside.json"})

            archive = self.archive(root)

            self.assertFalse((archive / "files/candidate.draft.json").exists())
            self.assertFalse((archive / "files/outside.json").exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside")
            self.assertEqual([item["path"] for item in read_json(archive / "archive_manifest.json")["files"]],
                             ["files/candidate_check_request.json"])

    def test_malformed_request_does_not_prevent_raw_draft_archiving(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authoring = root / "authoring"
            authoring.mkdir()
            content = '{"candidate": "draft.json"'
            (authoring / "candidate_check_request.json").write_text(content, encoding="utf-8")
            write_json(authoring / "candidate.draft.json", {"candidates": []})

            archive = self.archive(root)

            self.assertEqual((archive / "files/candidate_check_request.json").read_text(encoding="utf-8"), content)
            self.assertEqual(read_json(archive / "files/candidate.draft.json"), {"candidates": []})
            with self.assertRaises(json.JSONDecodeError):
                json.loads((archive / "files/candidate_check_request.json").read_text(encoding="utf-8"))

    def test_custom_candidate_cannot_collide_with_archive_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authoring = root / "authoring"
            authoring.mkdir()
            content = {"candidates": [{"task_public": "draft"}]}
            write_json(authoring / "archive_manifest.json", content)
            write_json(authoring / "candidate_check_request.json", {"candidate": "archive_manifest.json"})

            archive = self.archive(root)

            self.assertEqual(read_json(archive / "files/archive_manifest.json"), content)
            self.assertIs(read_json(archive / "archive_manifest.json")["diagnostic_only"], True)


if __name__ == "__main__":
    unittest.main()
