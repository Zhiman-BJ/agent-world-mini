#!/usr/bin/env python3
"""Copy a semiconductor delivery and repair published virtualenv metadata."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from typing import Any


IMPORTS = {
    "semiconductor_calibration_deembedding_1": ["jsonschema", "numpy", "skrf"],
    "semiconductor_circuit_level_s_matrix_1": ["jsonschema", "numpy", "sax", "gdsfactory"],
    "semiconductor_cloud_fdtd_1": ["jsonschema", "numpy", "tidy3d", "gdsfactory", "gdstk", "yaml"],
    "semiconductor_design_optimization_1": ["jsonschema", "pyopus", "kiutils"],
    "semiconductor_diffusion_reaction_pde_1": ["jsonschema", "numpy", "fipy", "gmsh", "skfmm"],
    "semiconductor_eigenmode_1": ["jsonschema", "femwell", "gplugins", "gdsfactory", "meshwell"],
    "semiconductor_eme_propagation_1": [
        "jsonschema", "emepy", "nbformat", "nbclient", "nbconvert", "matplotlib", "torch", "sklearn"
    ],
    "semiconductor_generic_fem_pde_1": [
        "jsonschema", "skfem", "meshio", "femwell", "gmsh", "meshwell", "shapely", "matplotlib"
    ],
}

PROBE = r'''import importlib, json, sys
result = {"prefix": sys.prefix, "base_prefix": sys.base_prefix, "imports": {}}
for name in json.loads(sys.argv[1]):
    try:
        importlib.import_module(name)
    except Exception as error:
        result["imports"][name] = f"{type(error).__name__}: {error}"
    else:
        result["imports"][name] = "ok"
print("__IMPORT_RESULT__" + json.dumps(result, sort_keys=True))
'''


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _relative(value: Any, label: str) -> Path:
    path = PurePosixPath(value) if isinstance(value, str) else PurePosixPath()
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a path inside the delivery: {value!r}")
    return Path(*path.parts)


def _tree_size(root: Path) -> int:
    return sum(path.stat(follow_symlinks=False).st_size for path in root.rglob("*") if not path.is_symlink())


def _plan(source: Path) -> list[dict[str, Any]]:
    plans = []
    generation_environments = source.parent / "environments"
    mapping_paths = sorted(source.glob("environments/*/software/profile.json"))
    found = {path.parents[1].name for path in mapping_paths}
    missing = sorted(set(IMPORTS) - found)
    unexpected = sorted(found - set(IMPORTS))
    if missing or unexpected:
        raise ValueError(
            f"delivery environment set mismatch: missing={missing}, unexpected={unexpected}"
        )
    for mapping_path in mapping_paths:
        package = mapping_path.parents[1].name
        mapping = _read_json(mapping_path)
        profile_path = _relative(mapping.get("profile_path"), f"{package} profile_path")
        metadata_path = generation_environments / package / "tool_generation/software_environment.json"
        metadata = _read_json(metadata_path)
        generation_root = Path(metadata.get("root", ""))
        generation_python = Path(metadata.get("python", ""))
        if not generation_root.is_absolute() or not generation_python.is_absolute():
            raise ValueError(f"generation software paths must be absolute: {metadata_path}")
        try:
            launcher_relative = generation_python.relative_to(generation_root)
        except ValueError as error:
            raise ValueError(f"generation Python is outside its software root: {metadata_path}") from error
        expected_python_path = (profile_path / launcher_relative).as_posix()
        if not (source / expected_python_path).is_file():
            raise FileNotFoundError(f"copied profile lacks recorded launcher: {source / expected_python_path}")
        generation_config = generation_python.parent.parent / "pyvenv.cfg"
        profile_config = source / profile_path / launcher_relative.parent.parent / "pyvenv.cfg"
        if not generation_config.is_file() or generation_config.read_bytes() != profile_config.read_bytes():
            raise ValueError(f"generation and delivered pyvenv.cfg differ: {package}")
        plans.append(
            {
                "environment_id": package,
                "mapping_path": mapping_path.relative_to(source),
                "metadata_path": metadata_path,
                "profile_id": mapping.get("profile_id"),
                "profile_path": profile_path,
                "before_python_path": mapping.get("python_path"),
                "after_python_path": expected_python_path,
                "launcher_relative": launcher_relative,
                "generation_root": generation_root,
                "generation_config": generation_config,
            }
        )
    return plans


def _rewrite_venv_config(destination: Path, plan: dict[str, Any]) -> dict[str, Any]:
    profile_root = destination / plan["profile_path"]
    config_path = profile_root / plan["launcher_relative"].parent.parent / "pyvenv.cfg"
    lines = config_path.read_text(encoding="utf-8").splitlines()
    updates: dict[str, dict[str, str]] = {}
    output = []
    for line in lines:
        key, separator, value = line.partition(" = ")
        if separator and key in {"home", "executable", "base-executable"}:
            original = Path(value)
            try:
                relative = original.relative_to(plan["generation_root"])
            except ValueError:
                pass
            else:
                replacement = profile_root / relative
                if not replacement.exists():
                    raise FileNotFoundError(f"bundled virtualenv base path is absent: {replacement}")
                updates[key] = {"before": value, "after": str(replacement)}
                line = f"{key} = {replacement}"
        output.append(line)
    if updates:
        config_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    home = next((line.partition(" = ")[2] for line in output if line.startswith("home = ")), None)
    external = home is not None and not Path(home).is_relative_to(profile_root)
    return {
        "path": config_path.relative_to(destination).as_posix(),
        "updates": updates,
        "external_base_dependency": home if external else None,
    }


def _check_imports(launcher: Path, modules: list[str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="semiconductor-import-") as temporary:
        (Path(temporary) / "home").mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(Path(temporary) / "home"),
                "TMPDIR": temporary,
                "XDG_CACHE_HOME": str(Path(temporary) / "cache"),
                "XDG_CONFIG_HOME": str(Path(temporary) / "xdg"),
                "MPLCONFIGDIR": str(Path(temporary) / "matplotlib"),
                "TIDY3D_BASE_DIR": str(Path(temporary) / "tidy3d"),
            }
        )
        result = subprocess.run(
            [str(launcher), "-B", "-I", "-c", PROBE, json.dumps(modules)],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
            env=environment,
        )
    marker = "__IMPORT_RESULT__"
    line = next((line for line in reversed(result.stdout.splitlines()) if line.startswith(marker)), None)
    if result.returncode or line is None:
        return {
            "imports": {name: "probe failed" for name in modules},
            "probe_error": (result.stderr or result.stdout).strip()[-2000:],
        }
    return json.loads(line.removeprefix(marker))


def prepare_delivery(source: Path, destination: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    destination = destination.expanduser()
    if not source.is_dir():
        raise NotADirectoryError(f"source delivery does not exist: {source}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    destination = destination.resolve()
    if destination.is_relative_to(source):
        raise ValueError("destination cannot be inside source delivery")
    plans = _plan(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = _tree_size(source)
    free_bytes = shutil.disk_usage(destination.parent).free
    if free_bytes < source_bytes:
        raise OSError(f"insufficient free space: need {source_bytes} bytes, have {free_bytes}")

    try:
        subprocess.run(["cp", "-a", "--reflink=auto", str(source), str(destination)], check=True)
        corrections = []
        for plan in plans:
            mapping_path = destination / plan["mapping_path"]
            mapping = _read_json(mapping_path)
            mapping["python_path"] = plan["after_python_path"]
            if plan["before_python_path"] != plan["after_python_path"]:
                mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            venv_config = _rewrite_venv_config(destination, plan)
            launcher = destination / plan["after_python_path"]
            import_check = _check_imports(launcher, IMPORTS[plan["environment_id"]])
            corrections.append(
                {
                    "environment_id": plan["environment_id"],
                    "profile_id": plan["profile_id"],
                    "mapping_path": plan["mapping_path"].as_posix(),
                    "generation_metadata": str(plan["metadata_path"]),
                    "python_path": {
                        "before": plan["before_python_path"],
                        "after": plan["after_python_path"],
                        "changed": plan["before_python_path"] != plan["after_python_path"],
                    },
                    "pyvenv_config": venv_config,
                    "import_check": import_check,
                }
            )
        manifest = {
            "schema_version": "1.0",
            "source_delivery": str(source),
            "destination_delivery": str(destination),
            "copy_method": "cp -a --reflink=auto",
            "capacity_check": {"source_bytes": source_bytes, "free_bytes_before_copy": free_bytes},
            "corrections": corrections,
            "import_failures": {
                item["environment_id"]: {
                    name: status
                    for name, status in item["import_check"]["imports"].items()
                    if status != "ok"
                }
                for item in corrections
                if item["import_check"]
                and any(status != "ok" for status in item["import_check"]["imports"].values())
            },
            "portability": {
                "destination_bound_profiles": [
                    item["environment_id"] for item in corrections if item["pyvenv_config"]["updates"]
                ],
                "external_base_dependencies": {
                    item["environment_id"]: item["pyvenv_config"]["external_base_dependency"]
                    for item in corrections
                    if item["pyvenv_config"]["external_base_dependency"]
                },
                "note": "Destination-bound pyvenv.cfg paths require rerunning this script after relocation.",
            },
        }
        (destination / "delivery_corrections.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manifest
    except BaseException:
        if destination.exists():
            shutil.rmtree(destination)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    manifest = prepare_delivery(arguments.source, arguments.destination)
    print(
        json.dumps(
            {"destination": manifest["destination_delivery"], "import_failures": manifest["import_failures"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
