from __future__ import annotations

import ast
import unittest
from pathlib import Path

from seed_gen.scripts.extract_native_python_refs import (
    extract_gmsh, extract_klayout_stub, gmsh_function, klayout_function,
)


class NativeDocTests(unittest.TestCase):
    def test_gmsh_named_and_scalar_results(self):
        node = ast.parse('''
def sample(dim=2):
    """gmsh.model.sample(dim)

    Query entity tags.

    Return an integer, `tags'.

    Types:
    - `dim': integer
    - `tags': vector of integers
    """
''').body[0]
        record = gmsh_function(node, "gmsh.model")
        self.assertEqual(record["description"], "Query entity tags.")
        self.assertEqual(record["input"]["dim"], {"type": "integer", "description": ""})
        self.assertEqual(record["output"], {
            "return": {"type": "integer", "description": ""},
            "tags": {"type": "vector of integers", "description": ""},
        })
        self.assertIn("Return an integer", record["ori_description"])

    def test_klayout_native_doc_fields_and_undocumented_return(self):
        node = ast.parse('''
def enlarge(self, distance: int) -> Box:
    """@brief Enlarge a box.

    @param distance Amount in database units.
        Continued explanation.
    @return The enlarged box.
    """
''').body[0]
        record = klayout_function(node)
        self.assertEqual(record["description"], "@brief Enlarge a box.")
        self.assertEqual(record["input"]["distance"]["description"], "Amount in database units. Continued explanation.")
        self.assertEqual(record["output"], {"return": {"type": "", "description": "The enlarged box."}})
        bare = ast.parse('def copy(self) -> Box:\n    """Copy this box."""').body[0]
        self.assertIsNone(klayout_function(bare)["output"])

    @unittest.skipUnless(Path("seed_pypi_raw/gmsh/api/gmsh.py").is_file(), "release checkout unavailable")
    def test_gmsh_release_exposes_nested_core_mesh_functions(self):
        tools = extract_gmsh(Path("seed_pypi_raw/gmsh/api/gmsh.py"))
        index = {(t["module"], t["name"]): t for t in tools}
        self.assertEqual(len(index), len(tools))
        self.assertIn(("gmsh.model.mesh", "generate"), index)
        self.assertIn(("gmsh.model.mesh.field", "setAsBackgroundMesh"), index)
        self.assertIn(("gmsh.model.occ", "fragment"), index)
        self.assertNotIn(("gmsh.model.mesh", "get_nodes"), index)
        self.assertEqual(index["gmsh", "isInitialized"]["output"]["return"]["type"], "integer")

    @unittest.skipUnless(Path("seed_pypi_raw/klayout/src/pymod/distutils_src/klayout/dbcore.pyi").is_file(), "release checkout unavailable")
    def test_klayout_release_preserves_constructor_overloads(self):
        tools, overloads = extract_klayout_stub(
            Path("seed_pypi_raw/klayout/src/pymod/distutils_src/klayout/dbcore.pyi"), "klayout.db"
        )
        box = next(t for t in tools if t["name"] == "Box")
        methods = [m["name"] for m in box["function"]]
        self.assertEqual(methods.count("__init__"), 1)
        signatures = overloads["klayout.db.Box.__init__"]
        self.assertGreater(len(signatures["variants"]), 1)
        self.assertIn(signatures["representative_signature"], [v["ori_input"] for v in signatures["variants"]])
        self.assertNotIn("__add__", methods)


if __name__ == "__main__":
    unittest.main()
