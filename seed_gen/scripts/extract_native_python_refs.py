"""Static adapters for release-shipped Gmsh bindings and KLayout typed stubs.

No target package is imported. Preserve native overload alternatives separately
instead of merging incompatible signatures into an invented callable.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from seed_gen.scripts.extract_python_ref_tools import (
    _compact_text, _description, _function_record, _signature,
)


def gmsh_function(node: ast.FunctionDef, namespace: str) -> dict:
    record = _function_record(node)
    doc = ast.get_docstring(node, clean=True) or ""
    # Generated bindings explicitly distinguish prose, Return, and Types.
    body = doc.partition("\n")[2].strip() if doc.startswith("gmsh.") else doc
    prose, _, types_text = body.partition("\nTypes:")
    types = dict(re.findall(r"^- `([^']+)': (.+)$", types_text, re.M))
    returned = re.search(r"(?:^|\n)Return ((?:`|an integer|a double).+?)(?:\n\n|$)", prose, re.S)
    record["description"] = _compact_text(prose[:returned.start()] if returned else prose)
    for name, value in record["input"].items():
        if value is not None:
            value["type"] = types.get(name, value["type"])
    if returned:
        names = re.findall(r"`([^']+)'", returned.group(1))
        record["output"] = {name: {"type": types.get(name, ""), "description": ""} for name in names}
        scalar = re.match(r"(?:an integer|a double)", returned.group(1))
        if scalar:
            record["output"] = {"return": {"type": scalar.group(0).split()[-1], "description": ""}, **record["output"]}
        if not record["output"]:
            raise ValueError(f"Unrecognized Gmsh return documentation: {node.name}")
    return {**record, "type": "function", "module": namespace}


def extract_gmsh(path: Path) -> list[dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    records = []

    def visit(nodes: list[ast.stmt], namespace: str) -> None:
        for node in nodes:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                if namespace != "gmsh" and not any(
                    isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list
                ):
                    raise ValueError(f"Non-static Gmsh namespace method: {namespace}.{node.name}")
                records.append(gmsh_function(node, namespace))
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                visit(node.body, namespace + "." + node.name)

    visit(tree.body, "gmsh")
    return records


def klayout_function(node: ast.FunctionDef) -> dict:
    record = _function_record(node)
    doc = ast.get_docstring(node, clean=True) or ""
    fields = list(re.finditer(r"(?m)^\s*@(param|return|returns)\b\s*", doc))
    if fields:
        record["description"] = _compact_text(doc[:fields[0].start()])
    for i, match in enumerate(fields):
        text = _compact_text(doc[match.end():fields[i + 1].start() if i + 1 < len(fields) else len(doc)])
        if match.group(1) == "param":
            name, _, description = text.partition(" ")
            if name in record["input"] and record["input"][name] is not None:
                record["input"][name]["description"] = description
        else:
            # Return type is not inferred from an annotation when docs omit it.
            record["output"] = {"return": {"type": "", "description": text}}
    return record


def extract_klayout_stub(path: Path, module: str) -> tuple[list[dict], dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    records, overloads = [], {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        groups: dict[str, list[ast.FunctionDef]] = {}
        for method in node.body:
            if isinstance(method, ast.FunctionDef) and (not method.name.startswith("_") or method.name == "__init__"):
                groups.setdefault(method.name, []).append(method)
        methods = []
        for name, variants in groups.items():
            # A genuine documented overload is the representative. Full variants
            # (including their docs) stay auditable under extraction metadata.
            representative = max(variants, key=lambda n: len(n.args.posonlyargs) + len(n.args.args) + len(n.args.kwonlyargs))
            methods.append(klayout_function(representative))
            if len(variants) > 1:
                overloads[f"{module}.{node.name}.{name}"] = {
                    "representative_signature": _signature(representative),
                    "variants": [klayout_function(v) for v in variants],
                }
        records.append({"name": node.name, "type": "class", "module": module,
                        "input": ", ".join(ast.unparse(base) for base in node.bases),
                        "description": _description(node), "function": methods})
    return records, overloads


def adapt_native(adapter: str, root: Path, python_tools: list[dict]) -> tuple[list[dict], list[str], dict]:
    if adapter == "gmsh":
        file = "api/gmsh.py"
        tools = extract_gmsh(root / file)
        return tools, [file], {
            "strategy": "static_generated_python_binding",
            "native_function_count": len(tools),
            "namespace_policy": "Gmsh staticmethod containers are API namespaces, recursively flattened as gmsh.model.mesh etc.; not instantiable tool classes. Snake-case aliases are excluded.",
            "docstring_adapter": "Generated Types fields supply types; Return fields supply named outputs; undocumented parameter/output descriptions stay empty.",
        }
    if adapter == "klayout":
        records, files, overloads = list(python_tools), [], {}
        for path in sorted((root / "src/pymod/distutils_src/klayout").glob("*core.pyi")):
            # Official package __init__.py files re-export each *core extension.
            module = "klayout." + path.stem.removesuffix("core")
            tools, variants = extract_klayout_stub(path, module)
            records.extend(tools)
            overloads.update(variants)
            files.append(path.relative_to(root).as_posix())
        if not files:
            raise ValueError("KLayout release has no shipped native stubs")
        return records, files, {
            "strategy": "static_ast_and_release_native_stubs",
            "native_function_count": sum(len(t["function"]) for t in records[len(python_tools):]),
            "namespace_policy": "Official *core.pyi classes mapped to public klayout.db/lay/lib/rdb/tl/pex modules, once per class; ordinary Python helper definitions also retained.",
            "overload_policy": "Count each method name once; choose the real overload with most explicit parameters (first on ties), preserve every overload below. Typed data attributes are not callable methods and are not counted.",
            "docstring_adapter": "Parse @param/@return fields from official native docstrings; return types without a documented return section remain empty/null.",
            "native_overloads": overloads,
            "stub_limitations": "Release-shipped stubs were not compared with a compiled runtime. Dynamic exports absent from stubs, Qt bindings outside the standalone package, and inherited methods are not enumerated.",
        }
    raise ValueError(f"Unknown native adapter: {adapter}")
