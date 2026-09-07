from __future__ import annotations

import ast
import unittest

from seed_gen.scripts.extract_python_ref_tools import _function_record


def parse_function(source: str) -> ast.FunctionDef:
    node = ast.parse(source).body[0]
    if not isinstance(node, ast.FunctionDef):
        raise TypeError("Expected a function definition")
    return node


class PythonRefToolExtractionTests(unittest.TestCase):
    def test_google_docstring_sections(self):
        function = parse_function(
            '''
def test(self, structure: Structure | IStructure):
    """Keep a valid structure.

    Args:
        structure (Structure): Input structure to test.

    Returns:
        bool: True if the structure passes.
    """
'''
        )

        record = _function_record(function)

        self.assertEqual(record["description"], "Keep a valid structure.")
        self.assertEqual(
            record["input"],
            {
                "self": None,
                "structure": {
                    "type": "Structure | IStructure",
                    "description": "Input structure to test.",
                },
            },
        )
        self.assertEqual(
            record["output"],
            {"return": {"type": "bool", "description": "True if the structure passes."}},
        )

    def test_numpy_docstring_sections(self):
        function = parse_function(
            '''
def load(path: str, optional=None):
    """Load a record.

    Parameters
    ----------
    path : str
        Path to load.
    optional
        Optional setting.

    Returns
    -------
    result : Record
        Loaded record.
    """
'''
        )

        record = _function_record(function)

        self.assertEqual(record["description"], "Load a record.")
        self.assertEqual(
            record["input"],
            {
                "path": {"type": "str", "description": "Path to load."},
                "optional": {"type": "", "description": "Optional setting."},
            },
        )
        self.assertEqual(
            record["output"],
            {"return": {"type": "Record", "description": "Loaded record."}},
        )

    def test_missing_return_section_is_null(self):
        function = parse_function(
            '''
def save(value: int) -> None:
    """Save a value.

    Args:
        value: Value to save.
    """
'''
        )

        record = _function_record(function)

        self.assertIsNone(record["output"])
        self.assertEqual(record["ori_input"], "value: int")


if __name__ == "__main__":
    unittest.main()
