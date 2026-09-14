from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from seed_gen.catalog import _seed_from_detail
from seed_gen.scripts.agent_score_mcp_seeds import DIMENSIONS, build_prompt, export_screening, recover_logs, run, validate_result, write_json
from seed_gen.scripts.finalize_agent_seed_scores import audit_records, finalize


def make_seed(name="demo/server", tools=None):
    return _seed_from_detail({"qualifiedName": name, "description": "A demo environment", "tools": tools or []}, 1)


def valid_review(tool_names=()):
    return {
        "decision": "limited_candidate",
        "dimensions": {key: {"score": 3, "reason": "Evidence from the supplied seed."} for key in DIMENSIONS},
        "entity_evidence": "The supplied tool parameters identify records.",
        "evidence_tools": list(tool_names),
        "proposed_task_chain": list(tool_names),
        "chain_dependencies": "The identifier returned by one step is used by the next step.",
        "proposed_assertion": "The final record matches the requested state.",
        "missing_evidence": "Output and reset behavior require runtime confirmation.",
        "recommendation": "Run a small isolated environment first.",
    }


class AgentSeedScoringTests(unittest.TestCase):
    def test_checkpoint_retries_transient_windows_lock(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text('{"old": true}', encoding="utf-8")
            original_replace = Path.replace
            attempts = []

            def replace_after_lock(temporary, target):
                attempts.append(target)
                if len(attempts) < 3:
                    self.assertEqual(json.loads(path.read_text()), {"old": True})
                    raise PermissionError("transient Windows lock")
                return original_replace(temporary, target)

            with patch.object(Path, "replace", replace_after_lock), patch("seed_gen.scripts.agent_score_mcp_seeds.time.sleep"):
                write_json(path, {"new": True})
            self.assertEqual(len(attempts), 3)
            self.assertEqual(json.loads(path.read_text()), {"new": True})

    def test_checkpoint_permanent_lock_preserves_previous_data(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text('{"old": true}', encoding="utf-8")
            with patch.object(Path, "replace", side_effect=PermissionError("locked")) as replace, \
                 patch("seed_gen.scripts.agent_score_mcp_seeds.time.sleep"):
                with self.assertRaises(PermissionError):
                    write_json(path, {"new": True})
            self.assertEqual(replace.call_count, 20)
            self.assertEqual(json.loads(path.read_text()), {"old": True})

    def test_strict_routing_detects_mismatched_category(self):
        seed = make_seed("demo/server", [{"name": "search", "description": "Find records"}])
        review = valid_review(["search"])
        with self.assertRaisesRegex(ValueError, "limited_candidate requires"):
            validate_result(review, seed, strict_routing=True)
        review["decision"] = "specialized_candidate"
        self.assertEqual(validate_result(review, seed, strict_routing=True)["total_score"], 60)
        for item in review["dimensions"].values():
            item["score"] = 4
        with self.assertRaisesRegex(ValueError, "specialized_candidate requires"):
            validate_result(review, seed, strict_routing=True)

    def test_audit_rejects_incorrect_totals_and_incomplete_coverage(self):
        seed = make_seed("demo/server", [{"name": "search", "description": "Find records"}])
        review = valid_review(["search"])
        review["decision"] = "specialized_candidate"
        row = {"global_id": seed["global_id"], "name": "demo/server", "source_index": 1, "tool_count": 1,
               **validate_result(review, seed), "total_score": 70}
        errors, missing = audit_records([seed], {seed["global_id"]: row})
        self.assertEqual(len(errors), 1)
        self.assertIn("total_score differs", errors[0]["error"])
        self.assertEqual(missing, [])
        self.assertEqual(audit_records([seed], {})[1], [seed["global_id"]])

    def test_finalize_empty_seed_and_export_end_to_end(self):
        seed = make_seed()
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text(json.dumps([seed]), encoding="utf-8")
            out = Path(directory) / "out"
            args = type("Args", (), {"source": source, "output_dir": out, "limit": 0,
                "resume": False, "model": "test", "workers": 1, "retries": 0, "checkpoint_every": 1,
                "timeout_seconds": 1, "reasoning_effort": "low"})()
            run(args)
            result = finalize(source, out)
            self.assertTrue(result["passed"])
            self.assertTrue((out / "final_summary.md").exists())
            self.assertEqual(json.loads((out / "screened/needs_tool_evidence.json").read_text(encoding="utf-8")), [seed])

    def test_recovery_requires_matching_source_and_valid_tool_references(self):
        seed = make_seed("demo/server", [{"name": "search", "description": "Find records"}])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for number, prompt, review in (
                (1, build_prompt(seed).replace("Find records", "Changed description"), valid_review(["search"])),
                (2, build_prompt(seed), valid_review(["invented_tool"])),
                (3, build_prompt(seed), valid_review(["search"])),
            ):
                log = root / "agent_logs" / f"run_{number}"
                log.mkdir(parents=True)
                (log / "stderr.log").write_text(prompt, encoding="utf-8")
                (log / "last_message.txt").write_text(json.dumps(review), encoding="utf-8")
            existing = {}
            result = recover_logs(root, [seed], existing)
            self.assertEqual(result["recovered_count"], 1)
            self.assertEqual(len(result["invalid_logs"]), 2)
            self.assertEqual(existing[seed["global_id"]]["total_score"], 60)
            self.assertEqual(recover_logs(root, [seed], existing)["recovered_count"], 0)

    def test_export_preserves_source_and_separates_unscored(self):
        seed = make_seed("demo/server", [{"name": "search", "description": "Find records"}])
        pending = make_seed("demo/pending")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            export_screening(root, [seed, pending], {seed["global_id"]: valid_review(["search"])})
            self.assertEqual(json.loads((root / "screened/limited_candidate.json").read_text(encoding="utf-8")), [seed])
            self.assertEqual(json.loads((root / "screened/pending_ids.json").read_text(encoding="utf-8")), [pending["global_id"]])

    def test_prompt_contains_only_compact_tool_evidence(self):
        seed = make_seed("demo/server", [{"name": "search", "description": "Find records", "inputSchema": {"properties": {"query": {"type": "string"}}}}])
        prompt = build_prompt(seed)
        self.assertIn("search", prompt)
        self.assertIn("input_fields", prompt)
        self.assertIn("只输出一个 JSON object", prompt)
        self.assertNotIn("tools: [", prompt)

    def test_validation_enforces_real_tools_and_threshold(self):
        seed = make_seed("demo/server", [{"name": "search", "description": "Find records"}])
        result = validate_result(valid_review(["search"]), seed)
        self.assertEqual(result["total_score"], 60)
        self.assertFalse(result["runtime_verified"])
        bad = valid_review(["missing"])
        with self.assertRaisesRegex(ValueError, "outside the seed"):
            validate_result(bad, seed)
        high = valid_review(["search"])
        high["decision"] = "priority_candidate"
        with self.assertRaisesRegex(ValueError, "threshold"):
            validate_result(high, seed)

    def test_empty_seed_is_deterministic_without_agent_call(self):
        seed = make_seed()
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text(json.dumps([seed]), encoding="utf-8")
            args = type("Args", (), {"source": source, "output_dir": Path(directory) / "out", "limit": 1,
                "resume": False, "model": "test", "workers": 1, "retries": 0, "checkpoint_every": 1,
                "timeout_seconds": 1, "reasoning_effort": "minimal"})()
            with patch("seed_gen.scripts.agent_score_mcp_seeds.CodexAgentClient") as client:
                result = run(args)
            client.assert_not_called()
            self.assertEqual(result["decision_counts"], {"needs_tool_evidence": 1})
            output = json.loads((args.output_dir / "agent_scores.json").read_text(encoding="utf-8"))
            self.assertEqual(output["records"][0]["assessment_status"], "deterministic_missing_tools")

    def test_resume_skips_completed_records(self):
        seed = make_seed("demo/server", [{"name": "search", "description": "Find records"}])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps([seed]), encoding="utf-8")
            out = root / "out"
            out.mkdir()
            completed = {seed["global_id"]: {"global_id": seed["global_id"], "name": "demo/server", "source_index": 1, "tool_count": 1, **valid_review(["search"]), "total_score": 60, "score_version": "mcp_seed_rubric_v1", "assessment_status": "agent_static_review", "runtime_verified": False}}
            (out / "agent_scores.progress.json").write_text(json.dumps(completed), encoding="utf-8")
            args = type("Args", (), {"source": source, "output_dir": out, "limit": 1, "resume": True,
                "model": "test", "workers": 1, "retries": 0, "checkpoint_every": 1,
                "timeout_seconds": 1, "reasoning_effort": "minimal"})()
            with patch("seed_gen.scripts.agent_score_mcp_seeds.CodexAgentClient") as client:
                result = run(args)
            client.assert_not_called()
            self.assertEqual(result["completed"], 1)


if __name__ == "__main__":
    unittest.main()
