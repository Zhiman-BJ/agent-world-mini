"""Extract source-level Python APIs into ``init_ref_tools`` seed records."""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


_ARG_SECTION_NAMES = {"args", "arguments", "parameters"}
_OUTPUT_SECTION_NAMES = {"returns", "yields"}
_OTHER_SECTION_NAMES = {
    "attributes",
    "examples",
    "note",
    "notes",
    "raises",
    "references",
    "see also",
    "warnings",
    "warning",
}
_SECTION_NAMES = _ARG_SECTION_NAMES | _OUTPUT_SECTION_NAMES | _OTHER_SECTION_NAMES
_SECTION_LINE_RE = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z ]*)(?P<colon>:)?\s*$")
_GOOGLE_ARG_RE = re.compile(
    r"^\s*(?P<names>\*{0,2}[A-Za-z_]\w*(?:\s*,\s*\*{0,2}[A-Za-z_]\w*)*)"
    r"\s*(?:\((?P<type>[^)]*)\))?\s*:\s*(?P<description>.*)\s*$"
)
_NUMPY_ARG_RE = re.compile(
    r"^\s*(?P<names>\*{0,2}[A-Za-z_]\w*(?:\s*,\s*\*{0,2}[A-Za-z_]\w*)*)"
    r"\s*(?::\s*(?P<type>.*?))?\s*$"
)


def _compact_text(text: str | list[str]) -> str:
    """Collapse docstring whitespace while retaining paragraph boundaries."""
    raw = text if isinstance(text, str) else "\n".join(text)
    paragraphs = re.split(r"\n\s*\n", raw.strip()) if raw.strip() else []
    return "  ".join(" ".join(paragraph.split()) for paragraph in paragraphs)


def _description(node: ast.AST) -> str:
    """Return a compact, readable form of a definition's complete docstring."""
    return _compact_text(ast.get_docstring(node, clean=True) or "")


def _section_heading(lines: list[str], index: int) -> tuple[str, bool] | None:
    """Recognize Google- or NumPy-style section headings."""
    match = _SECTION_LINE_RE.match(lines[index])
    if not match:
        return None
    name = " ".join(match.group("name").lower().split())
    if name not in _SECTION_NAMES:
        return None
    if match.group("colon"):
        return name, False
    if index + 1 < len(lines) and re.fullmatch(r"\s*[-=]{3,}\s*", lines[index + 1]):
        return name, True
    return None


def _doc_sections(docstring: str) -> tuple[str, dict[str, tuple[list[str], bool]]]:
    """Split a docstring into its summary and named sections."""
    lines = docstring.splitlines()
    headings: list[tuple[int, str, bool]] = []
    for index in range(len(lines)):
        heading = _section_heading(lines, index)
        if heading is not None:
            headings.append((index, *heading))

    if not headings:
        return _compact_text(docstring), {}

    summary = _compact_text(lines[: headings[0][0]])
    sections: dict[str, tuple[list[str], bool]] = {}
    for position, (start, name, numpy_style) in enumerate(headings):
        body_start = start + (2 if numpy_style else 1)
        body_end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        sections[name] = (lines[body_start:body_end], numpy_style)
    return summary, sections


def _section_entries(lines: list[str], numpy_style: bool) -> dict[str, dict[str, str]]:
    """Parse argument entries from a Google- or NumPy-style section."""
    nonempty = [(index, line) for index, line in enumerate(lines) if line.strip()]
    if not nonempty:
        return {}

    # Google docstrings normally indent entries by four spaces, whereas NumPy
    # entries are usually unindented.  Use the first entry's indentation as the
    # section baseline; both projects contain a handful of mixed-style blocks.
    first_indent = len(nonempty[0][1]) - len(nonempty[0][1].lstrip())

    parsed: dict[str, dict[str, str]] = {}
    current_names: list[str] = []
    current_type = ""
    current_lines: list[str] = []

    def flush() -> None:
        if not current_names:
            return
        info = {"type": current_type.strip(), "description": _compact_text(current_lines)}
        for name in current_names:
            parsed[name.lstrip("*")] = dict(info)

    for line in lines:
        if not line.strip():
            if current_names:
                current_lines.append("")
            continue
        indent = len(line) - len(line.lstrip())
        google_match = _GOOGLE_ARG_RE.match(line)
        # Some NumPy-style ``Parameters`` blocks use ``name (type): desc``.
        # Recognize that mixed form only when an explicit parenthesized type is
        # present; otherwise ``name : type`` remains a normal NumPy entry.
        mixed_google = numpy_style and google_match is not None and google_match.group("type") is not None
        match = google_match if not numpy_style or mixed_google else _NUMPY_ARG_RE.match(line)
        is_entry = bool(match) and (not numpy_style or indent <= first_indent)
        if is_entry:
            flush()
            names = [part.strip() for part in match.group("names").split(",")]
            current_names = names
            current_type = (match.group("type") or "").strip()
            if numpy_style and not mixed_google:
                current_lines = []
            else:
                current_lines = [match.group("description")]
            continue
        if current_names:
            current_lines.append(line.strip())
    flush()
    return parsed


def _parse_output(lines: list[str], numpy_style: bool) -> dict[str, dict[str, str]] | None:
    """Parse the first return-value type and description."""
    content = [line for line in lines if line.strip()]
    if not content:
        return None

    if numpy_style:
        first = content[0].strip()
        if ":" in first:
            left, right = (part.strip() for part in first.split(":", 1))
            right_is_type = bool(
                re.fullmatch(r"[A-Za-z_][\w.\[\], |]*", right)
                and not re.match(r"^(?:a|an|the)\b", right, flags=re.IGNORECASE)
            )
            if re.fullmatch(r"[A-Za-z_]\w*", left) and right_is_type:
                output_type = right
                description = _compact_text(content[1:])
            else:
                output_type = left
                description = _compact_text([right, *content[1:]])
        else:
            output_type = first
            description = _compact_text(content[1:])
    else:
        first = content[0].strip()
        if ":" in first:
            output_type, first_description = first.split(":", 1)
            description = _compact_text([first_description, *content[1:]])
        else:
            output_type = ""
            description = _compact_text(content)
    return {"return": {"type": output_type.strip(), "description": description}}


def _annotation_types(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str | None]:
    """Map source parameter names to annotation-derived display types."""
    arguments = node.args
    result: dict[str, str | None] = {}
    positional = [*arguments.posonlyargs, *arguments.args]
    for argument in positional:
        result[argument.arg] = ast.unparse(argument.annotation) if argument.annotation is not None else ""
    if arguments.vararg is not None:
        result[arguments.vararg.arg] = (
            ast.unparse(arguments.vararg.annotation) if arguments.vararg.annotation is not None else f"*{arguments.vararg.arg}"
        )
    for argument in arguments.kwonlyargs:
        result[argument.arg] = ast.unparse(argument.annotation) if argument.annotation is not None else ""
    if arguments.kwarg is not None:
        result[arguments.kwarg.arg] = (
            ast.unparse(arguments.kwarg.annotation) if arguments.kwarg.annotation is not None else f"**{arguments.kwarg.arg}"
        )
    return result


def _input_details(node: ast.FunctionDef | ast.AsyncFunctionDef, args_section: tuple[list[str], bool] | None) -> dict[str, Any]:
    """Build the parameter mapping required by the seed format."""
    doc_args = _section_entries(*args_section) if args_section else {}
    annotation_types = _annotation_types(node)
    arguments = node.args
    all_arguments = [*arguments.posonlyargs, *arguments.args]
    if arguments.vararg is not None:
        all_arguments.append(arguments.vararg)
    all_arguments.extend(arguments.kwonlyargs)
    if arguments.kwarg is not None:
        all_arguments.append(arguments.kwarg)

    result: dict[str, Any] = {}
    for argument in all_arguments:
        name = argument.arg
        if name in {"self", "cls"}:
            result[name] = None
            continue
        doc_info = doc_args.get(name, {})
        result[name] = {
            "type": annotation_types.get(name) or doc_info.get("type", ""),
            "description": doc_info.get("description", ""),
        }
    return result


def _decorator_name(decorator: ast.expr) -> str:
    """Return the final component of a decorator expression."""
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return ""


def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_decorator_name(decorator) == "overload" for decorator in node.decorator_list)


def _format_argument(argument: ast.arg, default: ast.expr | None = None) -> str:
    rendered = argument.arg
    if argument.annotation is not None:
        rendered += f": {ast.unparse(argument.annotation)}"
    if default is not None:
        rendered += f" = {ast.unparse(default)}"
    return rendered


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render only the contents of a function's source parameter list."""
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(arguments.defaults)) + list(arguments.defaults)
    rendered = [_format_argument(argument, default) for argument, default in zip(positional, defaults, strict=True)]

    if arguments.posonlyargs:
        rendered.insert(len(arguments.posonlyargs), "/")

    if arguments.vararg is not None:
        rendered.append(f"*{_format_argument(arguments.vararg)}")
    elif arguments.kwonlyargs:
        rendered.append("*")

    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        rendered.append(_format_argument(argument, default))

    if arguments.kwarg is not None:
        rendered.append(f"**{_format_argument(arguments.kwarg)}")

    return ", ".join(rendered)


def _public_methods(class_node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Select source-defined public methods, including the constructor."""
    selected: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    order: list[str] = []
    for node in class_node.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "__init__" and node.name.startswith("_"):
            continue
        if node.name not in selected:
            order.append(node.name)
        if node.name not in selected or not _is_overload(node):
            selected[node.name] = node
    return [selected[name] for name in order]


def _public_module_functions(
    nodes: list[ast.stmt],
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Select one implementation for each public module-level function."""
    selected: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        if node.name not in selected or not _is_overload(node):
            selected[node.name] = node
    return selected


def _function_record(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    docstring = ast.get_docstring(node, clean=True) or ""
    description, sections = _doc_sections(docstring)
    output_section = sections.get("returns") or sections.get("yields")
    return {
        "name": node.name,
        "description": description,
        "input": _input_details(node, sections.get("args") or sections.get("arguments") or sections.get("parameters")),
        "output": _parse_output(*output_section) if output_section else None,
        "ori_input": _signature(node),
        "ori_description": _description(node),
    }


def extract_file(source_path: Path, module: str) -> list[dict[str, Any]]:
    """Extract public top-level definitions from one Python source file."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    records: list[dict[str, Any]] = []
    selected_functions = _public_module_functions(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            records.append(
                {
                    "name": node.name,
                    "type": "class",
                    "module": module,
                    "input": ", ".join(ast.unparse(base) for base in node.bases),
                    "description": _description(node),
                    "function": [_function_record(method) for method in _public_methods(node)],
                }
            )
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and selected_functions.get(node.name) is node
        ):
            docstring = ast.get_docstring(node, clean=True) or ""
            description, sections = _doc_sections(docstring)
            output_section = sections.get("returns") or sections.get("yields")
            records.append(
                {
                    "name": node.name,
                    "type": "function",
                    "module": module,
                    "description": description,
                    "input": _input_details(
                        node,
                        sections.get("args") or sections.get("arguments") or sections.get("parameters"),
                    ),
                    "output": _parse_output(*output_section) if output_section else None,
                    "ori_input": _signature(node),
                    "ori_description": _description(node),
                }
            )
    return records


def _module_paths(source_root: Path, module: str) -> list[Path]:
    relative = Path(*module.split("."))
    module_file = source_root / relative.with_suffix(".py")
    package_dir = source_root / relative
    if module_file.is_file():
        return [module_file]
    if package_dir.is_dir():
        return sorted(path for path in package_dir.rglob("*.py") if "__pycache__" not in path.parts)
    raise FileNotFoundError(f"Cannot resolve module {module!r} below {source_root}")


def extract_modules(source_root: Path, modules: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract definitions from module files in deterministic path order."""
    paths: dict[Path, str] = {}
    for requested_module in modules:
        for source_path in _module_paths(source_root, requested_module):
            relative = source_path.relative_to(source_root).with_suffix("")
            module = ".".join(relative.parts)
            paths[source_path] = module.removesuffix(".__init__")

    tools: list[dict[str, Any]] = []
    source_files: list[str] = []
    for source_path, module in sorted(paths.items(), key=lambda item: item[1]):
        source_files.append(source_path.relative_to(source_root).as_posix())
        tools.extend(extract_file(source_path, module))
    return tools, source_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="Directory containing the top-level package")
    parser.add_argument("--module", action="append", required=True, dest="modules", help="Module or package to extract")
    parser.add_argument("--template", type=Path, required=True, help="Seed JSON whose first record will be updated")
    parser.add_argument("--template-index", type=int, default=0, help="Zero-based seed record selected from the template")
    parser.add_argument("--output", type=Path, required=True, help="Destination JSON path")
    parser.add_argument("--global-id", help="Override the seed global_id")
    parser.add_argument("--source", help="Override environment.basic_info.source")
    parser.add_argument("--name", help="Override environment.basic_info.name")
    parser.add_argument("--url", action="append", help="Override environment.basic_info.url (repeatable)")
    parser.add_argument("--description", help="Override environment.description")
    parser.add_argument("--domain-level1", help="Override environment.domain.level1")
    parser.add_argument("--source-version", help="Record the source package version in extraction metadata")
    parser.add_argument("--source-commit", help="Record the source commit in extraction metadata")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = json.loads(args.template.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) for item in payload):
        raise ValueError("Expected the template to contain at least one seed object")
    try:
        seed = payload[args.template_index]
    except IndexError as exc:
        raise ValueError(f"Template index {args.template_index} is outside the {len(payload)} records") from exc
    # Each generated artifact describes one package, selected from the shared
    # multi-package example/template.
    payload = [seed]

    tools, source_files = extract_modules(args.source_root.resolve(), args.modules)
    if not tools:
        raise ValueError("Extraction produced no reference tools")
    if args.global_id:
        seed["global_id"] = args.global_id
    environment = seed.setdefault("environment", {})
    basic_info = environment.setdefault("basic_info", {})
    if args.source:
        basic_info["source"] = args.source
    if args.name:
        basic_info["name"] = args.name
    if args.url:
        basic_info["url"] = args.url
    if args.source_version:
        basic_info["version"] = args.source_version
    if args.description is not None:
        environment["description"] = args.description
    if args.domain_level1:
        environment.setdefault("domain", {})["level1"] = args.domain_level1
    seed["init_ref_tools"] = tools
    others = seed.get("others")
    if not isinstance(others, dict):
        others = {}
        seed["others"] = others
    others.pop("..", None)
    others["python_source_extraction"] = {
        "strategy": "static_ast",
        "requested_modules": args.modules,
        "source_files": source_files,
        "selection": "public module classes/functions; __init__ and public source-defined class methods",
        "excluded": "private module definitions, private methods, non-constructor magic methods, imported/inherited callables",
        "docstring_mapping": {
            "description": "summary before Args/Arguments/Parameters",
            "input": "signature parameters; descriptions from Args/Arguments/Parameters and types from annotations with docstring fallback",
            "output": "return object parsed from Returns/Yields; null when no non-empty section exists",
            "ori_input": "original AST-rendered parameter list",
            "ori_description": "complete normalized source docstring",
        },
    }
    if args.source_version:
        others["python_source_extraction"]["source_version"] = args.source_version
    if args.source_commit:
        others["python_source_extraction"]["source_commit"] = args.source_commit
    seed.setdefault("init_ref_tasks", [])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(tools)} init_ref_tools to {args.output}")


if __name__ == "__main__":
    main()
