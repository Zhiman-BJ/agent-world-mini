"""Shared isolated Python/Profile/Docker tool execution."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import stat
import ssl
import subprocess
import sys
import tempfile
from typing import Any

_TOOL_WORKER = r"""
import contextlib
import ctypes
from copy import deepcopy
import errno
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import resource
import sys
import tempfile
from types import SimpleNamespace

# Keep the parent pipe reserved for the single JSON response. Some scientific
# libraries write directly to fd 1 and bypass Python's redirect_stdout.
_WIRE_FD = os.dup(1)
_ORIGINAL_REPLACE = os.replace

def _same_filesystem_replace(source, destination, *args, **kwargs):
    try:
        return _ORIGINAL_REPLACE(source, destination, *args, **kwargs)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        if args or kwargs:
            raise
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.is_dir():
            raise
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        handle, staged = tempfile.mkstemp(prefix='.replace-', dir=str(destination_path.parent))
        os.close(handle)
        try:
            shutil.copy2(source_path, staged)
            with open(staged, 'rb') as stream:
                os.fsync(stream.fileno())
            _ORIGINAL_REPLACE(staged, destination_path)
            os.unlink(source_path)
        except Exception:
            try:
                os.unlink(staged)
            except OSError:
                pass
            raise

os.replace = _same_filesystem_replace
os.rename = _same_filesystem_replace

@contextlib.contextmanager
def quiet_native_stdout():
    # C/Fortran libraries bypass Python's redirect_stdout; stdout is our JSON wire.
    original = os.dup(1)
    with tempfile.TemporaryFile() as sink:
        try:
            os.dup2(sink.fileno(), 1)
            yield
        finally:
            try:
                ctypes.CDLL(None).fflush(None)
            finally:
                os.dup2(original, 1)
                os.close(original)
            if sink.tell() > 16 * 1024 * 1024:
                raise ValueError('工具沙箱输出超过 16 MiB')

payload = json.load(sys.stdin)
if not payload.get('software_prefix'):
    sys.path.insert(0, '/dependencies')
else:
    sys.prefix = sys.exec_prefix = payload['software_prefix']
    sys.path.extend(payload['software_import_paths'])
try:
    memory_limit = int(payload["memory_limit"])
    write_limit = int(payload["write_limit"])
    if not payload.get('memory_cgroup'):
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (write_limit, write_limit))
    process_limit = int(payload["process_limit"])
    resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))
    os.chdir('/tmp')
    runtime = {}
    exec(payload["context_source"], runtime)
    namespace = {"json": json, "sqlite3": sqlite3}
    with quiet_native_stdout(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        exec(payload["code"], namespace)
        run = namespace.get("run")
        if not callable(run):
            raise ValueError("internal.code 没有定义 run(arguments, context)")
        context = (runtime["Context"](Path("/workspace"), payload['environment'])
            if payload.get('environment', {}).get('schema_version') == '2.0'
            else SimpleNamespace(workspace_root=Path('/workspace')))
        if payload.get('software_root'):
            context.software_root = Path(payload['software_root'])
        result = run(deepcopy(payload["arguments"]), context)
    json.dumps(result, ensure_ascii=False)
    response = {"result": result, "error": None}
except BaseException as error:
    response = {"result": None, "error": f"{type(error).__name__}: {error}"}
wire = json.dumps(response, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
os.write(_WIRE_FD, wire)
os.close(_WIRE_FD)
os._exit(0)
"""
_MAX_SANDBOX_OUTPUT_BYTES = 16 * 1024 * 1024


def _context_source() -> str:
    project = Path(__file__).resolve().parents[1]
    shared = project / 'utils/record_store.py'
    resources = (project / 'env_gen/tool_gen/resources.py').read_text(encoding='utf-8').replace(
        'from __future__ import annotations', ''
    )
    context = (project / 'task_gen/tool_graph/state_runtime.py').read_text(encoding='utf-8')
    context = context.replace('from env_gen.tool_gen.resources import ResourceCatalog', '')
    return shared.read_text(encoding='utf-8') + '\n' + resources + '\n' + context.replace(
        'from utils.record_store import RecordStore, _validate, _json, _quote', ''
    )


def _workspace_usage(root: Path) -> tuple[int, int, str | None]:
    """Return regular-file bytes/count and reject links or special filesystem nodes."""
    total = 0
    count = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in [*directories, *files]:
            path = parent / name
            mode = path.lstat().st_mode
            count += 1
            if stat.S_ISLNK(mode):
                return total, count, f"workspace 包含符号链接：{path.relative_to(root)}"
            if stat.S_ISREG(mode):
                total += path.stat().st_size
            elif not stat.S_ISDIR(mode):
                return total, count, f"workspace 包含特殊文件：{path.relative_to(root)}"
    return total, count, None


def _read_limited(stream: Any, limit: int = _MAX_SANDBOX_OUTPUT_BYTES) -> bytes:
    stream.seek(0)
    return stream.read(limit + 1)


def _call_tool(
    code: str,
    arguments: dict[str, Any],
    workspace: Path,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    environment: dict[str, Any] | None = None,
    software_root: Path | None = None,
    *, process_limit: int = 1024, software: dict[str, str] | None = None,
    runtime: dict[str, Any] | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    from functools import partial
    if runtime and runtime.get("backend") == "docker":
        run_tool = partial(_run_docker_tool, runtime=runtime, read_only=read_only)
    else:
        run_tool = partial(_run_tool, software=software, read_only=read_only)
    if software_root is not None and not (runtime and runtime.get("backend") == "docker"):
        run_tool = partial(run_tool, software_root=software_root)
    if not environment or environment.get('schema_version') != '2.0':
        return run_tool(
            code, arguments, workspace, timeout, memory_limit, write_limit, environment,
            process_limit=process_limit,
        )
    if read_only:
        return run_tool(
            code, arguments, workspace, timeout, memory_limit, write_limit, environment,
            process_limit=process_limit,
        )
    from task_gen.tool_graph.state_runtime import snapshot_state, state_diff
    workspace = workspace.resolve()
    try:
        _, _, error = _workspace_usage(workspace)
        if error:
            raise ValueError(error)
        before = snapshot_state(workspace, environment)
        with tempfile.TemporaryDirectory(prefix='.tool-state-', dir=workspace.parent) as temporary:
            candidate = Path(temporary) / 'state'
            shutil.copytree(workspace, candidate, symlinks=True)
            outcome = run_tool(
                code, arguments, candidate, timeout, memory_limit, write_limit, environment,
                process_limit=process_limit,
            )
            result = outcome.get('result')
            if outcome.get('error') or not isinstance(result, dict) or result.get('success') is not True:
                return outcome
            diff = state_diff(before, snapshot_state(candidate, environment))
            writable = {d['record_set_id'] for d in environment.get('record_sets', []) if d.get('access') == 'copy_on_write'}
            writable.update(d['scope_id'] for d in environment.get('filesystem_scopes', []) if d.get('access') == 'copy_on_write')
            forbidden = set(diff['changed_assets']) - writable
            if forbidden:
                raise PermissionError('工具修改了只读或未声明状态：' + ', '.join(sorted(forbidden)))
            previous = Path(temporary) / 'previous'
            workspace.rename(previous)
            try:
                candidate.rename(workspace)
            except Exception:
                previous.rename(workspace)
                raise
            return outcome
    except Exception as error:
        return {'kind': 'exception', 'result': None, 'error': f'{type(error).__name__}: {error}'}


def _run_tool(
    code, arguments, workspace, timeout, memory_limit, write_limit, environment=None, software_root=None,
    *, process_limit=1024, software=None, read_only=False,
):
    import jsonschema
    workspace = workspace.resolve()
    if not workspace.is_dir():
        return {"kind": "exception", "result": None, "error": "工具 workspace 不存在"}
    before_bytes, before_entries, workspace_error = _workspace_usage(workspace)
    if workspace_error:
        return {"kind": "exception", "result": None, "error": workspace_error}
    sandbox = shutil.which("bwrap")
    if sandbox is None:
        return {"kind": "exception", "result": None, "error": "未安装 bubblewrap，拒绝执行未隔离工具"}
    runtime_root = Path(sys.base_prefix).resolve()
    executable = Path(sys.executable).resolve()
    runtime_mounts = []
    software_prefix, software_imports = None, []
    if software:
        try:
            # The interpreter is trusted delivery infrastructure, not tool code.
            # -S avoids executing profile .pth/sitecustomize outside the sandbox.
            launcher = Path(software['python']).absolute()
            venv = launcher.parent.parent
            probe = subprocess.run([str(launcher), '-I', '-S', '-B', '-c',
                'import json,sys,sysconfig; prefix=sys.argv[1] or sys.prefix; '
                'paths=sysconfig.get_paths(scheme="posix_prefix", vars={"base":prefix,"platbase":prefix}); '
                'print(json.dumps({"base":sys.base_prefix,"prefix":prefix,"imports":[paths["purelib"],paths["platlib"]]}))',
                str(venv) if (venv / 'pyvenv.cfg').is_file() else ''],
                capture_output=True, text=True, check=True, timeout=min(timeout, 30))
            info = json.loads(probe.stdout)
            # Preserve the launcher's venv and the absolute paths in pyvenv.cfg.
            # Only declared software/runtime directories are visible, not their parents.
            runtime_executable = launcher
            runtime_mounts = sorted({Path(p).resolve() for p in
                [info['base'], info['prefix'], software['root']]}, key=lambda p: len(p.parts))
            # Managed Python installs may use an alias in the venv launcher.
            # Keep that interpreter directory visible at its configured path.
            config_path = venv / 'pyvenv.cfg'
            if config_path.is_file():
                for line in config_path.read_text().splitlines():
                    key, separator, value = line.partition('=')
                    if separator and key.strip() == 'home':
                        interpreter_root = Path(value.strip()).absolute().parent
                        if interpreter_root not in runtime_mounts:
                            runtime_mounts.append(interpreter_root)
                        break
            software_prefix = str(Path(info['prefix']).resolve())
            for value in dict.fromkeys(info['imports']):
                path = Path(value)
                if not path.resolve().is_relative_to(Path(software_prefix)):
                    raise ValueError('软件 Profile 引用了其环境之外的第三方依赖')
                software_imports.append(str(path))
        except Exception as error:
            return {'kind': 'exception', 'result': None, 'error': f'软件 Profile 启动失败：{error}'}
    else:
        try:
            runtime_executable = Path("/runtime") / executable.relative_to(runtime_root)
        except ValueError:
            return {"kind": "exception", "result": None, "error": "Python 解释器不在其运行时目录中"}
    command = [
        sandbox,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--dir", "/etc",
        "--ro-bind-try", "/etc/passwd", "/etc/passwd",
        "--proc", "/proc",
        "--dev", "/dev",
        "--dir", "/tmp",
        "--dir", "/workspace",
        "--ro-bind" if read_only else "--bind", str(workspace), "/workspace",
        "--chdir", "/workspace",
        "--clearenv",
        "--setenv", "HOME", "/tmp",
        "--setenv", "USER", "tool",
        "--setenv", "TMPDIR", "/tmp",
        # 库的缓存与自动生成配置不属于环境状态。
        "--setenv", "XDG_CACHE_HOME", "/tmp/cache",
        "--setenv", "XDG_CONFIG_HOME", "/tmp/config",
        # 多核主机上数值库的默认线程池会耗尽沙箱的地址空间预算。
        "--setenv", "OPENBLAS_NUM_THREADS", "1",
        "--setenv", "OMP_NUM_THREADS", "1",
        "--setenv", "OMP_THREAD_LIMIT", "1",
        "--setenv", "MKL_NUM_THREADS", "1",
        "--setenv", "NUMEXPR_NUM_THREADS", "1",
        "--setenv", "JAX_NUM_THREADS", "1",
        "--setenv", "TF_NUM_INTRAOP_THREADS", "1",
        "--setenv", "TF_NUM_INTEROP_THREADS", "1",
        "--setenv", "XLA_FLAGS", "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1",
    ]
    # Bind a disposable directory from the workspace filesystem as /tmp.
    # This keeps standard-library tempfile + os.replace operations on one
    # device while still isolating caches from the business workspace.
    tmp_mount = tempfile.mkdtemp(prefix=".tool-tmp-", dir=workspace.parent)
    command.extend(["--bind", tmp_mount, "/tmp"])
    if software_root is not None and not software:
        software_root = Path(software_root).expanduser().resolve()
        if not software_root.is_dir():
            return {"kind": "exception", "result": None, "error": "配置的软件目录不存在"}
        command.extend(["--ro-bind", str(software_root), "/software"])
        # 本机软件的动态库可能按 /usr/lib 的 RPATH 加载依赖。
        command.extend(["--ro-bind-try", "/usr/lib", "/usr/lib"])
    for name in ("LANG", "LC_ALL", "TZ"):
        if name in os.environ:
            command.extend(["--setenv", name, os.environ[name]])
    # Some scientific libraries construct TLS clients on import, even offline.
    # Only public trust roots are exposed; network isolation remains unchanged.
    ca_bundle = Path(ssl.get_default_verify_paths().cafile or '/etc/ssl/certs/ca-certificates.crt')
    if ca_bundle.is_file():
        command.extend(['--ro-bind', str(ca_bundle.resolve()), '/ca-certificates.crt',
                        '--setenv', 'SSL_CERT_FILE', '/ca-certificates.crt'])
    if software:
        from env_gen.tool_gen.software import runtime_environment

        for path in runtime_mounts:
            command.extend(['--ro-bind', str(path), str(path)])
        command.extend(['--ro-bind', str(Path(software['root']).resolve()), '/software'])
        prepared = runtime_environment(Path(software['root']), {})
        for name in ('PATH', 'LD_LIBRARY_PATH', 'PETSC_DIR', 'PETSC_ARCH', 'SLEPC_DIR'):
            if prepared.get(name):
                command.extend(['--setenv', name, prepared[name]])
        # CPU libraries otherwise size thread pools from the host CPU count,
        # which can exhaust this tool's memory/process limits on import alone.
        for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'OMP_THREAD_LIMIT', 'MKL_NUM_THREADS'):
            command.extend(['--setenv', name, '1'])
    else:
        command.extend(['--ro-bind', str(runtime_root), '/runtime',
                        '--ro-bind', str(Path(jsonschema.__file__).resolve().parent.parent), '/dependencies'])
    command.extend([str(runtime_executable), "-I", *(['-S', '-B'] if software else []), "-c", _TOOL_WORKER])
    payload = json.dumps({
        "code": code,
        "arguments": arguments,
        "environment": environment or {},
        "context_source": _context_source(),
        "memory_limit": memory_limit,
        "write_limit": write_limit,
        "process_limit": process_limit,
        "software_root": '/software' if software or software_root is not None else None,
        "software_prefix": software_prefix,
        "software_import_paths": software_imports,
    }, ensure_ascii=False)
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                completed = subprocess.run(
                    command,
                    input=payload.encode("utf-8"),
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {"kind": "timeout", "result": None, "error": f"工具调用超过 {timeout} 秒"}
            except OSError as error:
                return {"kind": "exception", "result": None, "error": f"启动工具沙箱失败：{error}"}
            stdout_value = _read_limited(stdout)
            stderr_value = _read_limited(stderr, 2000)
    finally:
        shutil.rmtree(tmp_mount, ignore_errors=True)
    if completed.returncode != 0:
        detail = (stderr_value or stdout_value).decode("utf-8", errors="replace").strip()[-2000:]
        return {"kind": "exception", "result": None, "error": f"工具沙箱异常退出：{detail}"}
    if len(stdout_value) > _MAX_SANDBOX_OUTPUT_BYTES:
        return {"kind": "exception", "result": None, "error": "工具沙箱输出超过 16 MiB"}
    after_bytes, after_entries, workspace_error = _workspace_usage(workspace)
    if workspace_error:
        return {"kind": "exception", "result": None, "error": workspace_error}
    if after_bytes > before_bytes + write_limit:
        return {
            "kind": "exception",
            "result": None,
            "error": f"workspace 文件总增长超过 {write_limit} 字节",
        }
    if after_entries > before_entries + max(1024, write_limit // 4096):
        return {"kind": "exception", "result": None, "error": "workspace 新增条目数量超过限制"}
    try:
        message = json.loads(stdout_value)
    except json.JSONDecodeError as error:
        return {"kind": "exception", "result": None, "error": f"工具沙箱返回无效 JSON：{error}"}
    if not isinstance(message, dict) or set(message) != {"result", "error"}:
        return {"kind": "exception", "result": None, "error": "工具沙箱返回结构非法"}
    if message["error"] is not None:
        return {"kind": "exception", "result": None, "error": message["error"]}
    return {"kind": None, "result": message["result"], "error": None}


def _run_docker_tool(
    code, arguments, workspace, timeout, memory_limit, write_limit,
    environment=None, *, process_limit=1024, runtime, read_only=False,
):
    workspace = workspace.resolve()
    before_bytes, before_entries, workspace_error = _workspace_usage(workspace)
    if workspace_error:
        return {"kind": "exception", "result": None, "error": workspace_error}
    docker = shutil.which("docker")
    if docker is None:
        return {"kind": "exception", "result": None, "error": "找不到 Docker 命令"}

    payload = json.dumps({
        "code": code,
        "arguments": arguments,
        "environment": environment or {},
        "context_source": _context_source(),
        "memory_limit": memory_limit,
        "write_limit": write_limit,
        "process_limit": process_limit,
        "software_root": runtime["software_root"],
        "software_prefix": None,
        "software_import_paths": [],
        "memory_cgroup": True,
    }, ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix=".tool-docker-", dir=workspace.parent) as control:
        cidfile = Path(control) / "container.cid"
        passwd = Path(control) / "passwd"
        passwd.write_text(
            f"root:x:0:0:root:/root:/bin/sh\n"
            f"tool:x:{os.getuid()}:{os.getgid()}:tool:/tmp:/bin/sh\n",
            encoding="utf-8",
        )
        command = [
            docker, "run", "--rm", "-i", "--runtime", "runc", "--cidfile", str(cidfile),
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--network", "none", "--read-only",
            "--memory", str(memory_limit), "--pids-limit", str(process_limit),
            "--tmpfs", f"/tmp:rw,exec,size={write_limit}",
            "--tmpfs", f"{runtime['software_root']}/cache:rw,exec,size=256m",
            "--mount", f"type=bind,source={passwd},target=/etc/passwd,readonly",
            "--mount", f"type=bind,source={workspace},target=/workspace"
                       + (",readonly" if read_only else ""),
            "--workdir", "/workspace",
            "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
            "--env", "XDG_CACHE_HOME=/tmp/cache",
            "--env", "XDG_CONFIG_HOME=/tmp/config",
            "--env", f"TOOLGEN_SOFTWARE_ROOT={runtime['software_root']}",
            "--env", "OPENBLAS_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=1",
            runtime["image"], runtime["python_command"], "-I", "-c", _TOOL_WORKER,
        ]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                completed = subprocess.run(
                    command, input=payload.encode("utf-8"), stdout=stdout,
                    stderr=stderr, timeout=timeout, check=False,
                )
            except subprocess.TimeoutExpired:
                if cidfile.is_file():
                    subprocess.run(
                        [docker, "rm", "-f", cidfile.read_text().strip()],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=15, check=False,
                    )
                return {"kind": "timeout", "result": None, "error": f"工具调用超过 {timeout} 秒"}
            except OSError as error:
                return {"kind": "exception", "result": None, "error": f"启动 Docker 工具失败：{error}"}
            stdout_value = _read_limited(stdout)
            stderr_value = _read_limited(stderr, 2000)
    if completed.returncode != 0:
        detail = (stderr_value or stdout_value).decode("utf-8", errors="replace").strip()[-2000:]
        return {"kind": "exception", "result": None, "error": f"Docker 工具异常退出：{detail}"}
    if len(stdout_value) > _MAX_SANDBOX_OUTPUT_BYTES:
        return {"kind": "exception", "result": None, "error": "工具沙箱输出超过 16 MiB"}
    after_bytes, after_entries, workspace_error = _workspace_usage(workspace)
    if workspace_error:
        return {"kind": "exception", "result": None, "error": workspace_error}
    if after_bytes > before_bytes + write_limit:
        return {"kind": "exception", "result": None, "error": f"workspace 文件总增长超过 {write_limit} 字节"}
    if after_entries > before_entries + max(1024, write_limit // 4096):
        return {"kind": "exception", "result": None, "error": "workspace 新增条目数量超过限制"}
    try:
        message = json.loads(stdout_value)
    except json.JSONDecodeError as error:
        return {"kind": "exception", "result": None, "error": f"Docker 工具返回无效 JSON：{error}"}
    if not isinstance(message, dict) or set(message) != {"result", "error"}:
        return {"kind": "exception", "result": None, "error": "Docker 工具返回结构非法"}
    if message["error"] is not None:
        return {"kind": "exception", "result": None, "error": message["error"]}
    return {"kind": None, "result": message["result"], "error": None}

call_tool = _call_tool
run_tool = run_tool_in_sandbox = _run_tool
workspace_usage = _workspace_usage
