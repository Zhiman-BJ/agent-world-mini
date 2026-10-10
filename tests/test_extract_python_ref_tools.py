from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from seed_gen.scripts.extract_python_ref_tools import _extract_source_records, _function_record, extract_file, extract_modules


def parse_function(source: str) -> ast.FunctionDef:
    node = ast.parse(source).body[0]
    if not isinstance(node, ast.FunctionDef):
        raise TypeError("Expected a function definition")
    return node


class PythonRefToolExtractionTests(unittest.TestCase):
    def test_parser_cache_reads_current_content_and_returns_isolated_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'cached.py'
            path.write_text('def run(value=1):\n    """Original."""\n', encoding='utf-8')
            _extract_source_records.cache_clear()
            first = extract_file(path, 'one')
            first[0]['description'] = 'consumer mutation'
            first[0]['input']['value']['type'] = 'mutated'
            second = extract_file(path, 'one')
            self.assertEqual(second[0]['description'], 'Original.')
            self.assertEqual(second[0]['input']['value']['type'], '')
            self.assertEqual(_extract_source_records.cache_info().hits, 1)
            self.assertEqual(extract_file(path, 'two')[0]['module'], 'two')
            path.write_text('def run(value=2):\n    """Updated!."""\n', encoding='utf-8')
            changed = extract_file(path, 'one')[0]
            self.assertEqual(changed['description'], 'Updated!.')
            self.assertEqual(changed['ori_input'], 'value = 2')
            _extract_source_records.cache_clear()

    def test_utf8_bom_is_legal_source_without_modifying_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'driver.py'
            original = b'\xef\xbb\xbfdef measure():\n    """Read voltage."""\n    return 1.0\n'
            path.write_bytes(original)
            record = extract_file(path, 'driver')[0]
            self.assertEqual(record['name'], 'measure')
            self.assertEqual(record['description'], 'Read voltage.')
            self.assertEqual(path.read_bytes(), original)

    def test_callable_protocol_is_opt_in_and_keeps_source_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'parameters.py'
            path.write_text('''class Material:
    def __init__(self): pass
    def __call__(self, T=300):
        """Evaluate material.

        Args:
            T (float): Temperature in K.
        Returns:
            float: Energy in eV.
        """
        return T
    def _internal(self): pass
    def __repr__(self): return "Material"
''', encoding='utf-8')
            default = extract_file(path, 'parameters')[0]
            self.assertEqual([m['name'] for m in default['function']], ['__init__'])
            extended, files = extract_modules(Path(temp), ['parameters'], include_call_protocol=True)
            self.assertEqual(files, ['parameters.py'])
            self.assertEqual([m['name'] for m in extended[0]['function']], ['__init__', '__call__'])
            method = extended[0]['function'][1]
            self.assertEqual(method['ori_input'], 'self, T = 300')
            self.assertEqual(method['input']['T']['description'], 'Temperature in K.')
            self.assertEqual(method['output']['return']['description'], 'Energy in eV.')

    def test_sphinx_fields_keep_summary_types_and_multiline_descriptions(self):
        function = parse_function('''
def evaluate(layer, T=298):
    """Evaluate a layer.

    :param Layer layer: Material layer with
        a continuation line.
    :param T: Temperature in kelvin.
    :type T: float
    :returns: Calculated result.
    :rtype: dict
    :raises ValueError: Invalid layer.
    """
''')
        record = _function_record(function)
        self.assertEqual(record["description"], "Evaluate a layer.")
        self.assertEqual(record["input"]["layer"], {
            "type": "Layer", "description": "Material layer with a continuation line.",
        })
        self.assertEqual(record["input"]["T"], {"type": "float", "description": "Temperature in kelvin."})
        self.assertEqual(record["output"], {"return": {"type": "dict", "description": "Calculated result."}})
        self.assertIn(":raises ValueError:", record["ori_description"])

    def test_sphinx_missing_type_and_return_stay_empty(self):
        function = parse_function('''
def update(value, undocumented):
    """Update a value.

    :param value: New value.
    """
''')
        record = _function_record(function)
        self.assertEqual(record["input"]["value"], {"type": "", "description": "New value."})
        self.assertEqual(record["input"]["undocumented"], {"type": "", "description": ""})
        self.assertIsNone(record["output"])

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

    def test_duplicate_class_name_uses_last_runtime_definition(self):
        source = '''
class Duplicate:
    """First definition."""

class Duplicate:
    """Runtime definition."""
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text(source, encoding="utf-8")
            records = extract_file(path, "sample")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "Duplicate")
        self.assertEqual(records[0]["description"], "Runtime definition.")


if __name__ == "__main__":
    unittest.main()
