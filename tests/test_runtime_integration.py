"""Cross-entry backend and publication contracts."""
from copy import deepcopy
import os
from pathlib import Path
import sqlite3
import shlex
import subprocess
from unittest.mock import patch

import pytest

from harness.execution import call_environment_tool
from harness.sandbox import run_tool_in_docker


def tool_definition(code, write=False):
    return {
        "name": "probe", "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object", "properties": {"success": {"const": True}, "data": {"type": "object"}}, "required": ["success", "data"]},
        "usageConditions": {"targetResources": ["reports"], "sideEffects": ["write"] if write else []},
        "internal": {"code": code},
    }


def environment():
    return {"schema_version": "2.0", "record_sets": [],
            "filesystem_scopes": [{"scope_id": "reports", "access": "copy_on_write"}]}


def test_task_adapter_keeps_docker_and_workspace_parameters(tmp_path):
    from task_gen.tool_runtime import call_environment_tool as adapter
    runtime = {"backend": "docker", "image": "fixture", "python_command": "python", "software_root": "/opt/tool-software"}
    db = tmp_path / "private/records.sqlite"
    with patch("task_gen.tool_runtime._execute", return_value={"error": None}) as execute:
        adapter("probe", {}, {}, tmp_path, runtime=runtime, binding_path=tmp_path / "binding.json",
                record_database=db, scope_mounts={"reports": "."}, timeout=10, memory_limit=1024, write_limit=1024)
    assert execute.call_args.kwargs["runtime"] == runtime
    assert execute.call_args.kwargs["record_database"] == db
    assert execute.call_args.kwargs["scope_mounts"] == {"reports": "."}


def test_docker_workspace_dispatch_keeps_private_database_and_scopes(tmp_path):
    db = tmp_path / "private/records.sqlite"
    db.parent.mkdir()
    sqlite3.connect(db).close()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = tool_definition("def run(arguments, context): return {}")
    runtime = {"backend": "docker"}
    with patch("harness.sandbox.run_tool_in_docker", return_value={"kind": None, "error": None, "result": {"success": True, "data": {}}}) as runner:
        record = call_environment_tool("probe", {}, {"probe": tool}, workspace, runtime=runtime,
            environment=environment(), record_database=db, scope_mounts={"reports": "."},
            timeout=10, memory_limit=1024**3, write_limit=1024**2)
    assert record["error"] is None
    assert runner.call_args.kwargs["record_database"] == db
    assert runner.call_args.kwargs["scope_roots"] == {"reports": workspace}
    assert runner.call_args.kwargs["read_only"] is True


@pytest.mark.skipif(not os.environ.get("TOOLGEN_DOCKER_TEST_IMAGE"), reason="Set an existing test image")
def test_real_docker_shared_workspace_read_write_and_rollback(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.txt").write_text("original")
    db = tmp_path / "private/records.sqlite"
    db.parent.mkdir()
    sqlite3.connect(db).close()
    runtime = {"backend": "docker", "image": os.environ["TOOLGEN_DOCKER_TEST_IMAGE"],
               "python_command": "python", "software_root": "/opt/tool-software"}
    tool = tool_definition('def run(arguments, context):\n return {"success": True, "data": {"text": (context.scope_root("reports") / "report.txt").read_text()}}')
    options = dict(environment=environment(), record_database=db, scope_mounts={"reports": "."},
                   runtime=runtime, timeout=30, memory_limit=1024**3, write_limit=1024**2)
    result = call_environment_tool("probe", {}, {"probe": tool}, workspace, **options)
    assert result["error"] is None, result
    assert result["result"]["data"]["text"] == "original"
    tool = tool_definition('def run(arguments, context):\n (context.scope_root("reports") / "report.txt").write_text("changed")\n return {"success": True, "data": {}}', True)
    assert call_environment_tool("probe", {}, {"probe": tool}, workspace, **options)["error"] is None
    assert (workspace / "report.txt").read_text() == "changed"
    bad = deepcopy(tool)
    bad["internal"]["code"] = bad["internal"]["code"].replace('"changed"', '"bad"').replace('"success": True', '"success": False')
    assert call_environment_tool("probe", {}, {"probe": bad}, workspace, **options)["error"]
    assert (workspace / "report.txt").read_text() == "changed"


def test_software_documentation_exclusion_keeps_runtime_links_checked():
    from env_gen.tool_gen.delivery import _software_build_artifact
    assert _software_build_artifact(Path("native/prefix/usr/share/doc/pkg/readme"))
    assert not _software_build_artifact(Path("native/prefix/usr/lib/libblas.so"))


def test_apt_mirror_preserves_security_and_default_recipe():
    from env_gen.tool_gen.docker_runtime import dockerfile, image_name
    plan = {"python": "3.11", "python_packages": [], "container": {"base_image": "python:3.11-slim-bookworm", "apt_packages": ["klayout"]}}
    original = dockerfile(plan)
    changed = deepcopy(plan)
    changed["container"]["apt_mirror"] = "https://mirrors.ustc.edu.cn"
    rendered = dockerfile(changed)
    assert "mirrors.ustc.edu.cn/debian" in rendered
    sed_line = next(line for line in rendered.splitlines() if "sed -i -E" in line)
    expression = shlex.split(sed_line.split("sed -i -E", 1)[1])[0]
    sources = "https://deb.debian.org/debian bookworm main\nhttps://deb.debian.org/debian-security bookworm-security main\n"
    rewritten = subprocess.run(["sed", "-E", expression], input=sources, text=True,
                               capture_output=True, check=True).stdout
    assert rewritten == sources.replace("https://deb.debian.org/debian ", "https://mirrors.ustc.edu.cn/debian ")
    assert image_name(plan) != image_name(changed)
    assert dockerfile(plan) == original
    changed["container"]["apt_mirror"] = "https://mirror.example;bad"
    with pytest.raises(ValueError):
        dockerfile(changed)
