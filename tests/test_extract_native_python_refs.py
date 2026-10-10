from __future__ import annotations

import ast
import unittest
from pathlib import Path

from seed_gen.scripts.extract_native_python_refs import (
    extract_cantera_stubs,
    extract_gmsh,
    extract_klayout_stub,
    gmsh_function,
    klayout_function,
)
from seed_gen.scripts.extract_gdstk_refs import extract_gdstk_stubs


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

    @unittest.skipUnless(
        Path("seed_pypi_raw/cantera/interfaces/cython/cantera/thermo.pyi").is_file(),
        "release checkout unavailable",
    )
    def test_cantera_release_pairs_typed_stubs_with_cython_docs(self):
        tools, files, metadata = extract_cantera_stubs(Path("seed_pypi_raw/cantera"))
        index = {(tool["module"], tool["name"]): tool for tool in tools}
        thermo = index["cantera.thermo", "ThermoPhase"]
        species = index["cantera.thermo", "Species"]
        from_dict = next(method for method in species["function"] if method["name"] == "from_dict")

        self.assertIn("thermodynamic state", thermo["description"].lower())
        self.assertEqual(from_dict["input"]["data"]["type"], "_SpeciesInput")
        self.assertIn("YAML representation", from_dict["input"]["data"]["description"])
        self.assertIn("interfaces/cython/cantera/thermo.pyi", files)
        self.assertIn("cantera.thermo.ThermoPhase.species", metadata["native_overloads"])
        self.assertGreater(metadata["documented_native_symbol_count"], 0)

    @unittest.skipUnless(Path('seed_pypi_raw/l1_design_lab/gdstk/gdstk/_gdstk.pyi').is_file(), 'release checkout unavailable')
    def test_gdstk_real_exports_cover_stub_omissions_and_ignore_comments(self):
        tools, files, metadata = extract_gdstk_stubs(Path('seed_pypi_raw/l1_design_lab/gdstk'))
        classes = {tool['name']: tool for tool in tools if tool['type'] == 'class'}
        self.assertEqual(len(classes), 12)
        self.assertEqual(sum(tool['type'] == 'function' for tool in tools), 21)
        self.assertEqual(metadata['native_exports_missing_from_stubs'], ['gdstk.Cell.remap', 'gdstk.Library.remap'])
        remap = next(m for m in classes['Cell']['function'] if m['name'] == 'remap')
        self.assertIn('Dictionary mapping existing', remap['input']['layer_type_map']['description'])
        self.assertEqual(remap['ori_input'], 'self, layer_type_map')
        # The C++ copy binding is commented out, and must not become a tool.
        self.assertNotIn('copy', [m['name'] for m in classes['Repetition']['function']])
        self.assertIn('gdstk.Cell.name', metadata['native_data_attributes'])
        self.assertNotIn('name', [m['name'] for m in classes['Cell']['function']])
        self.assertIn('python/cell_object.cpp', files)

    @unittest.skipUnless(Path('seed_pypi_raw/l1_design_lab/gdstk/gdstk/_gdstk.pyi').is_file(), 'release checkout unavailable')
    def test_gdstk_exact_shared_docs_and_no_invented_returns(self):
        tools, _, metadata = extract_gdstk_stubs(Path('seed_pypi_raw/l1_design_lab/gdstk'))
        index = {tool['name']: tool for tool in tools}
        rectangle = index['rectangle']
        self.assertEqual(rectangle['description'], 'Create a rectangle.')
        self.assertEqual(rectangle['input']['corner1']['type'], 'tuple[float, float] | complex')
        self.assertEqual(rectangle['input']['corner1']['description'], 'First rectangle corner.')
        self.assertIsNone(rectangle['output'])  # Native arrow and stub annotation are not Returns.
        get_property = next(m for m in index['Cell']['function'] if m['name'] == 'get_property')
        self.assertEqual(metadata['native_symbol_sources']['gdstk.Cell.get_property']['doc_variable'], 'object_get_property_doc')
        self.assertEqual(get_property['output']['return']['type'], 'list or None')
        self.assertIn('List of property values.', get_property['output']['return']['description'])


if __name__ == "__main__":
    unittest.main()
