import unittest

from task_gen.tool_graph.step_2_chain_sample import _apply_chain_edits


class ChainEditsTest(unittest.TestCase):
    def test_sequential_positions_and_atomic_failure(self):
        original = ["a", "b", "a"]
        edits = [
            {"op": "insert", "position": 2, "tools": ["c"], "reason": "Locate"},
            {"op": "replace", "position": 3, "expected_tool": "b", "tools": ["a", "c"], "reason": "Replace"},
            {"op": "delete", "position": 5, "expected_tool": "a", "reason": "Duplicate"},
            {"op": "insert", "position": 5, "tools": ["b"], "reason": "Verify"},
        ]
        self.assertEqual(_apply_chain_edits(original, edits, set("abc")), ["a", "c", "a", "c", "b"])
        for invalid in (
            {"op": "delete", "position": 2, "expected_tool": "b", "reason": "Stale index"},
            {"op": "insert", "position": True, "tools": ["a"], "reason": "Invalid position"},
            {"op": "insert", "position": 99, "tools": ["a"], "reason": "Out of range"},
            {"op": "replace", "position": 1, "expected_tool": "a", "tools": ["unknown"], "reason": "Unknown"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _apply_chain_edits(original, edits[:1] + [invalid], set("abc"))
            self.assertEqual(original, ["a", "b", "a"])
        with self.assertRaises(ValueError):
            _apply_chain_edits(["a"], [{"op": "delete", "position": 1, "expected_tool": "a", "reason": "Empty"}], {"a"})
