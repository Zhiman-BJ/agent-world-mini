"""Static adapters for release-shipped Gmsh bindings and KLayout typed stubs.

No target package is imported. Preserve native overload alternatives separately
instead of merging incompatible signatures into an invented callable.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from seed_gen.scripts.extract_python_ref_tools import (
    _compact_text,
    _description,
    _doc_sections,
    _function_record,
    _parse_output,
    _section_entries,
    _signature,
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


def _cython_docstrings(path: Path) -> dict[tuple[str, str, str], str]:
    """Collect class/function/property docstrings without importing Cython."""
    lines = path.read_text(encoding="utf-8").splitlines()
    docs: dict[tuple[str, str, str], str] = {}
    class_stack: list[tuple[int, str]] = []

    def indentation(line: str) -> int:
        return len(line) - len(line.lstrip())

    def body_start(start: int) -> int:
        depth = 0
        for index in range(start, len(lines)):
            code = lines[index].split("#", 1)[0]
            depth += code.count("(") - code.count(")")
            if depth <= 0 and code.rstrip().endswith(":"):
                return index + 1
        return start + 1

    def immediate_docstring(start: int) -> str:
        index = body_start(start)
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            return ""
        match = re.match(r"^[rRuUbBfF]*(\"\"\"|''')(.*)$", lines[index].strip())
        if not match:
            return ""
        quote, remainder = match.groups()
        if quote in remainder:
            return remainder.split(quote, 1)[0]
        content = [remainder]
        for line in lines[index + 1:]:
            if quote in line:
                content.append(line.split(quote, 1)[0])
                break
            content.append(line)
        return "\n".join(content)

    class_re = re.compile(r"^(?P<indent>\s*)(?:cdef\s+)?class\s+(?P<name>[A-Za-z_]\w*)\b")
    function_re = re.compile(r"^(?P<indent>\s*)(?:(?:cp|c)def\s+|def\s+)(?P<name>[A-Za-z_]\w*)\s*\(")
    property_re = re.compile(r"^(?P<indent>\s*)property\s+(?P<name>[A-Za-z_]\w*)\s*:")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = indentation(line)
        while class_stack and indent <= class_stack[-1][0]:
            class_stack.pop()
        class_match = class_re.match(line)
        if class_match:
            name = class_match.group("name")
            docs[("class", name, "")] = immediate_docstring(index)
            class_stack.append((indent, name))
            continue
        match = property_re.match(line) or function_re.match(line)
        if match:
            owner = class_stack[-1][1] if class_stack else ""
            kind = "method" if owner else "function"
            docs[(kind, owner, match.group("name"))] = immediate_docstring(index)
    return {key: value for key, value in docs.items() if value.strip()}


def _apply_native_doc(record: dict, docstring: str) -> None:
    """Merge native-source prose into a signature record from a typed stub."""
    description, sections = _doc_sections(docstring)
    record["description"] = description
    record["ori_description"] = _compact_text(docstring)
    args_section = sections.get("args") or sections.get("arguments") or sections.get("parameters")
    documented = _section_entries(*args_section) if args_section else {}
    for name, value in record.get("input", {}).items():
        if value is not None and name in documented:
            if not value.get("type"):
                value["type"] = documented[name]["type"]
            value["description"] = documented[name]["description"]
    output_section = sections.get("returns") or sections.get("yields")
    if output_section:
        record["output"] = _parse_output(*output_section)


def extract_cantera_stubs(root: Path) -> tuple[list[dict], list[str], dict]:
    """Combine release-shipped Cantera stubs with Cython implementation docs."""
    package = root / "interfaces/cython/cantera"
    records: list[dict] = []
    files: list[str] = []
    overloads: dict[str, list[str]] = {}
    documented_symbols = 0
    for stub in sorted(package.glob("*.pyi")):
        implementation = stub.with_suffix(".pyx")
        # Pure-Python modules are already represented by normal AST extraction.
        if not implementation.is_file():
            continue
        module = "cantera." + stub.stem
        docs = _cython_docstrings(implementation)
        tree = ast.parse(stub.read_text(encoding="utf-8"), filename=str(stub))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                groups: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        method.name == "__init__" or not method.name.startswith("_")
                    ):
                        groups.setdefault(method.name, []).append(method)
                methods = []
                for name, variants in groups.items():
                    representative = max(
                        variants,
                        key=lambda item: len(item.args.posonlyargs) + len(item.args.args) + len(item.args.kwonlyargs),
                    )
                    method_record = _function_record(representative)
                    doc = docs.get(("method", node.name, name), "")
                    if doc:
                        _apply_native_doc(method_record, doc)
                        documented_symbols += 1
                    methods.append(method_record)
                    if len(variants) > 1:
                        overloads[f"{module}.{node.name}.{name}"] = [_signature(item) for item in variants]
                class_doc = docs.get(("class", node.name, ""), "")
                if class_doc:
                    documented_symbols += 1
                records.append({
                    "name": node.name,
                    "type": "class",
                    "module": module,
                    "input": ", ".join(ast.unparse(base) for base in node.bases),
                    "description": _compact_text(class_doc),
                    "function": methods,
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                function_record = _function_record(node)
                doc = docs.get(("function", "", node.name), "")
                if doc:
                    _apply_native_doc(function_record, doc)
                    documented_symbols += 1
                records.append({**function_record, "type": "function", "module": module})
        files.extend([
            stub.relative_to(root).as_posix(),
            implementation.relative_to(root).as_posix(),
        ])
    if not files:
        raise ValueError("Cantera release has no paired .pyi/.pyx native sources")
    return records, files, {
        "strategy": "static_ast_and_release_cython_stubs",
        "native_function_count": sum(
            len(record.get("function", [])) if record["type"] == "class" else 1
            for record in records
        ),
        "documented_native_symbol_count": documented_symbols,
        "namespace_policy": "Public definitions from release-shipped cantera/*.pyi files paired with same-name .pyx implementation documentation; private definitions are excluded and pure-Python modules come from ordinary AST extraction.",
        "overload_policy": "Count each public callable name once; choose the variant with the most explicit parameters and preserve all typed signatures in native_overloads.",
        "docstring_adapter": "Typed stubs supply signatures and annotations; immediate Cython class/function/property docstrings supply descriptions and documented parameter/return text.",
        "native_overloads": overloads,
        "stub_limitations": "The release source was not compiled or imported. Runtime-generated exports, inherited methods, and Cython definitions absent from typed stubs are not enumerated.",
    }


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
    if adapter == "cantera":
        native, files, metadata = extract_cantera_stubs(root)
        return [*python_tools, *native], files, metadata
    raise ValueError(f"Unknown native adapter: {adapter}")
