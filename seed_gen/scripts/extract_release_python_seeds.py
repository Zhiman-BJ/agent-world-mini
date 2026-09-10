"""Generate version-pinned semiconductor package seeds without importing packages.

Run from the repository root:
    python -m seed_gen.scripts.extract_release_python_seeds
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import subprocess
from pathlib import Path

from seed_gen.scripts.extract_python_ref_tools import (
    _compact_text,
    _doc_sections,
    _parse_output,
    _section_entries,
    extract_modules,
)


def extract_devsim_native(root: Path) -> tuple[list[dict], list[str]]:
    """Read the exact C-extension export table and its embedded Python docs."""
    files = ["src/pythonapi/CommandTable.cc", "src/pythonapi/DevsimDoc.cc"]
    names = re.findall(r"^DS_FUNCTION_TABLE\(\s*(\w+)\s*,", (root / files[0]).read_text(encoding="utf-8"), re.M)
    docs = dict(re.findall(
        r'static const char (\w+)_doc\[\]\s*=\s*R"\((.*?)\)";',
        (root / files[1]).read_text(encoding="utf-8"), re.S,
    ))
    if not names or len(names) != len(set(names)):
        raise ValueError("DEVSIM export table is empty or contains duplicates")
    tools = []
    for name in names:
        if name not in docs:
            raise ValueError(f"Missing DEVSIM embedded documentation block: {name}")
        raw_doc = inspect.cleandoc(docs[name])
        first, _, remainder = raw_doc.partition("\n")
        signature_match = re.fullmatch(rf"devsim\.{name}\s*\((.*)\)\s*", first)
        if not signature_match:
            raise ValueError(f"Unrecognized DEVSIM documented signature: {first}")
        signature = signature_match.group(1)
        description, sections = _doc_sections(inspect.cleandoc(remainder))
        parameters = _section_entries(*sections["parameters"]) if "parameters" in sections else {}
        args = [part.strip() for part in signature.split(",") if part.strip()]
        if any(not re.fullmatch(r"\w+", arg) for arg in args):
            raise ValueError(f"Unrecognized DEVSIM parameter syntax: {signature}")
        # Native signatures are documentation signatures, not Python def stubs.
        inputs = {arg: parameters.get(arg, {"type": "", "description": ""}) for arg in args}
        output_section = sections.get("returns") or sections.get("yields")
        tools.append({
            "name": name, "type": "function", "module": "devsim",
            "description": description, "input": inputs,
            "output": _parse_output(*output_section) if output_section else None,
            "ori_input": signature, "ori_description": _compact_text(raw_doc),
        })
    return tools, files


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()


def build_seed(spec: dict, raw_root: Path) -> list[dict]:
    root = raw_root / spec["directory"]
    commit = git(root, "rev-parse", "HEAD")
    if commit != spec["commit"] or git(root, "rev-parse", f"{spec['tag']}^{{commit}}") != commit:
        raise ValueError(f"Release commit mismatch: {root}")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError(f"Tracked source modifications in {root}")
    tools, files = extract_modules(root, spec["modules"])
    native_count = 0
    if spec["name"] == "devsim":
        for tool in tools:
            tool["module"] = "devsim." + tool["module"]
        native, native_files = extract_devsim_native(root)
        native_count = len(native)
        tools = native + tools
        files += native_files
    classes = [tool for tool in tools if tool["type"] == "class"]
    function_count = sum(tool["type"] == "function" for tool in tools)
    method_count = sum(len(tool["function"]) for tool in classes)
    urls = [spec["repository"]]
    if spec["pypi"]:
        urls.append(spec["pypi"])
    urls.append(spec["documentation"])
    return [{
        "global_id": f"pypi_{spec['name']}_{spec['index']}",
        "schema_version": "1.1",
        "environment": {
            "basic_info": {"source": "pypi", "url": urls, "name": spec["name"],
                           "version": spec["tag"], "index": spec["index"]},
            "description": spec["description"],
            "domain": {"level1": "semiconductor", "level2": None, "level3": None},
            "nums": {"class": len(classes), "function": function_count,
                     "class_func": method_count, "all_func": function_count + method_count},
        },
        "init_ref_tools": tools,
        "init_ref_tasks": [],
        "others": {
            "source_metadata": {
                **{key: spec[key] for key in ("repository", "documentation", "pypi", "pypi_version",
                                             "tag", "commit", "release_published_at", "checked_on",
                                             "github_prerelease", "notes")},
                "release_url": f"{spec['repository']}/releases/tag/{spec['tag']}",
                "release_api": f"https://api.github.com/repos/{spec['repository'].removeprefix('https://github.com/')}/releases/latest",
                "selection_rule": "Latest official GitHub Release returned at checked_on; checkout its tag, not the default branch",
                "source_directory": root.as_posix(),
                "submodule_status": git(root, "submodule", "status").splitlines(),
            },
            "python_source_extraction": {
                "strategy": "static_ast_and_native_export_docs" if native_count else "static_ast",
                "requested_modules": spec["modules"], "source_files": files,
                "source_file_sha256": {file: hashlib.sha256((root / file).read_bytes()).hexdigest() for file in files},
                "source_version": spec["tag"], "source_commit": commit,
                "native_function_count": native_count,
                "selection": "public module classes/functions; __init__ and public source-defined class methods (including properties)",
                "excluded": "private definitions, non-constructor magic methods, imported/inherited APIs, nested definitions, external dependencies; tests/examples outside package directories",
                "docstring_mapping": {
                    "description": "summary before Google/NumPy sections or reStructuredText fields",
                    "input": "signature parameters; annotation types with docstring fallback; Google/NumPy/reStructuredText parameter descriptions",
                    "output": "Returns/Yields or reStructuredText return/rtype; null if undocumented",
                    "ori_input": "AST-rendered parameters, or DEVSIM native documented signature parameters",
                    "ori_description": "complete normalized source docstring or native embedded documentation",
                },
                "missing_values": "Undocumented text is an empty string; missing return sections and self/cls entries are null; missing lists are empty",
                "runtime_verified": False,
            },
        },
    }]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("seed_gen/pypi_release_sources.json"))
    parser.add_argument("--raw-root", type=Path, default=Path("seed_pypi_raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("seed_gen/pypi_outputs/ori_all"))
    parser.add_argument("--check", action="store_true", help="Compare existing JSON to fresh source extraction without writing")
    args = parser.parse_args()
    specs = json.loads(args.manifest.read_text(encoding="utf-8"))
    # Build all four before writing so an extraction failure cannot leave half a batch.
    payloads = [(spec, build_seed(spec, args.raw_root)) for spec in specs]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for spec, payload in payloads:
        destination = args.output_dir / f"{spec['name']}_{spec['tag']}.json"
        if args.check:
            if json.loads(destination.read_text(encoding="utf-8")) != payload:
                raise ValueError(f"Source extraction differs from {destination}")
        else:
            destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{destination}: {payload[0]['environment']['nums']}")


if __name__ == "__main__":
    main()
