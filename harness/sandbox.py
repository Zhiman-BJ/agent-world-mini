"""Canonical bubblewrap sandbox for generated ToolGen code.

The old TaskGen module re-exports these functions for compatibility.
"""

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


def _restrict_writes_with_landlock(writable_roots):
    # Deny filesystem mutation except below explicitly disposable roots.
    # This project runs on x86_64 Linux.  These syscall numbers are shared by
    # the generic and x86_64 syscall tables; failing to install the ruleset is
    # fatal so a read-only tool never silently runs writable.
    landlock_create_ruleset = 444
    landlock_add_rule = 445
    landlock_restrict_self = 446
    landlock_rule_path_beneath = 1
    landlock_access_fs_write_file = 1 << 1
    landlock_access_fs_remove_dir = 1 << 4
    landlock_access_fs_remove_file = 1 << 5
    landlock_access_fs_make_char = 1 << 6
    landlock_access_fs_make_dir = 1 << 7
    landlock_access_fs_make_reg = 1 << 8
    landlock_access_fs_make_sock = 1 << 9
    landlock_access_fs_make_fifo = 1 << 10
    landlock_access_fs_make_block = 1 << 11
    landlock_access_fs_make_sym = 1 << 12
    handled = (
        landlock_access_fs_write_file
        | landlock_access_fs_remove_dir
        | landlock_access_fs_remove_file
        | landlock_access_fs_make_char
        | landlock_access_fs_make_dir
        | landlock_access_fs_make_reg
        | landlock_access_fs_make_sock
        | landlock_access_fs_make_fifo
        | landlock_access_fs_make_block
        | landlock_access_fs_make_sym
    )

    class PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    libc = ctypes.CDLL(None, use_errno=True)
    ruleset_attributes = ctypes.c_uint64(handled)
    ruleset_fd = libc.syscall(
        landlock_create_ruleset,
        ctypes.byref(ruleset_attributes),
        ctypes.sizeof(ruleset_attributes),
        0,
    )
    if ruleset_fd < 0:
        error = ctypes.get_errno()
        raise OSError(error, "Landlock ruleset creation failed")
    try:
        for root in writable_roots:
            parent_fd = os.open(root, os.O_PATH | os.O_CLOEXEC)
            try:
                access = handled if os.path.isdir(root) else landlock_access_fs_write_file
                if root == '/dev/shm':
                    access = (landlock_access_fs_write_file
                              | landlock_access_fs_make_reg
                              | landlock_access_fs_remove_file)
                rule = PathBeneath(access, parent_fd)
                if libc.syscall(
                    landlock_add_rule,
                    ruleset_fd,
                    landlock_rule_path_beneath,
                    ctypes.byref(rule),
                    0,
                ) < 0:
                    error = ctypes.get_errno()
                    raise OSError(error, "Landlock path rule creation failed")
            finally:
                os.close(parent_fd)
        # Landlock requires no_new_privs before attaching an unprivileged
        # ruleset to the current process and all of its future children.
        if libc.prctl(38, 1, 0, 0, 0) < 0:
            error = ctypes.get_errno()
            raise OSError(error, "PR_SET_NO_NEW_PRIVS failed")
        if libc.syscall(landlock_restrict_self, ruleset_fd, 0) < 0:
            error = ctypes.get_errno()
            raise OSError(error, "Landlock restriction failed")
    finally:
        os.close(ruleset_fd)

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
    if not payload.get("memory_cgroup"):
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (write_limit, write_limit))
    process_limit = payload.get("process_limit")
    if process_limit is not None:
        process_limit = int(process_limit)
        resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))
    runtime = {}
    exec(payload["context_source"], runtime)
    namespace = {"json": json, "sqlite3": sqlite3}
    with quiet_native_stdout(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if payload.get("direct_path_guard") and payload.get("read_only"):
            _restrict_writes_with_landlock(payload.get("writable_roots", []))
        exec(payload["code"], namespace)
        run = namespace.get("run")
        if not callable(run):
            raise ValueError("internal.code 没有定义 run(arguments, context)")
        workspace_root = Path(payload.get("workspace_root", "/workspace"))
        context = (runtime["Context"](
                workspace_root, payload['environment'], payload.get("read_only", False),
                record_database=payload.get("record_database"),
                scope_roots=payload.get("scope_roots"),
            )
            if payload.get('environment', {}).get('schema_version') == '2.0'
            else SimpleNamespace(workspace_root=workspace_root))
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
# Exit handlers from native libraries may still write to fd 1 after the Python
# result has been emitted. Redirect them to the already-open diagnostic stream;
# opening /dev/null here would be rejected by a read-only Landlock ruleset.
os.dup2(2, 1)
"""
_MAX_SANDBOX_OUTPUT_BYTES = 16 * 1024 * 1024


def _context_source() -> str:
    """Inline the runtime context so isolated Python needs no project imports."""
    project = Path(__file__).resolve().parents[1]
    shared = project / "utils/record_store.py"
    resources = (project / "harness/resources.py").read_text(
        encoding="utf-8"
    ).replace("from __future__ import annotations", "")
    context = (project / "harness/state.py").read_text(
        encoding="utf-8"
    )
    context = context.replace(
        "from harness.resources import ResourceCatalog", ""
    )
    context = context.replace(
        "from utils.record_store import RecordStore, _validate, _json, _quote", ""
    )
    return shared.read_text(encoding="utf-8") + "\n" + resources + "\n" + context


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


def call_tool(
    code: str,
    arguments: dict[str, Any],
    workspace: Path,
    timeout: int,
    memory_limit: int,
    write_limit: int,
    environment: dict[str, Any] | None = None,
    software_root: Path | None = None,
    *,
    process_limit: int = 1024,
    software: dict[str, str] | None = None,
    read_only: bool = False,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from functools import partial
    if runtime and runtime.get("backend") == "docker" and _inside_docker_runtime():
        run_tool_fn = partial(run_tool_direct, software=software, read_only=read_only,
                              software_root=software_root)
    elif runtime and runtime.get("backend") == "docker":
        run_tool_fn = partial(run_tool_in_docker, runtime=runtime, read_only=read_only)
    else:
        run_tool_fn = partial(run_tool_in_sandbox, software=software, read_only=read_only)
        if software_root is not None:
            run_tool_fn = partial(run_tool_fn, software_root=software_root)
    if not environment or environment.get('schema_version') != '2.0':
        return run_tool_fn(
            code, arguments, workspace, timeout, memory_limit, write_limit, environment,
            process_limit=process_limit,
        )
    from .state import snapshot_state, state_diff
    workspace = workspace.resolve()
    try:
        _, _, error = _workspace_usage(workspace)
        if error:
            raise ValueError(error)
        before = snapshot_state(workspace, environment)
        with tempfile.TemporaryDirectory(prefix='.tool-state-', dir=workspace.parent) as temporary:
            candidate = Path(temporary) / 'state'
            shutil.copytree(workspace, candidate, symlinks=True)
            outcome = run_tool_fn(
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


def _container_procfs() -> bool:
    """Deployment flags for runtimes that provide their own private PID namespace."""
    return any(os.environ.get(name) == '1' for name in (
        'AGENT_WORLD_DOCKER_RUNTIME', 'AGENT_WORLD_KUBERNETES_RUNTIME',
    ))


def _sandbox_namespaces() -> list[str]:
    if _container_procfs():
        # Reused procfs and the worker must describe the same container PIDs.
        return ['--unshare-user', '--unshare-ipc', '--unshare-net',
                '--unshare-uts', '--unshare-cgroup']
    return ['--unshare-all']


def _interpreter_home_mounts(home: Path, base: Path) -> list[Path]:
    """Preserve a runtime alias together with its standard-library directory."""
    mounts = [home]
    if home.parent.resolve() == base.resolve():
        mounts.append(home.parent)
    return mounts


def run_tool_in_sandbox(
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
            mount_paths = [info['base'], info['prefix'], software['root']]
            config = venv / 'pyvenv.cfg'
            if config.is_file():
                for line in config.read_text(encoding='utf-8').splitlines():
                    key, separator, value = line.partition('=')
                    if separator and key.strip() == 'home':
                        home = Path(value.strip()).absolute()
                        if home.is_dir():
                            mount_paths.extend(str(path) for path in
                                               _interpreter_home_mounts(home, Path(info['base'])))
            # Keep aliases from pyvenv.cfg mounted at the path Python reads.
            runtime_mounts = sorted({Path(p).absolute() for p in mount_paths}, key=lambda p: len(p.parts))
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
    # Docker and non-hostPID Kubernetes Pods supply a private PID namespace.
    # A nested unprivileged user namespace cannot mount a fresh procfs on
    # this kernel, so reuse the
    # container's procfs there. Host execution keeps the stronger fresh procfs
    # mount. Network, user, IPC, UTS, cgroup and filesystem isolation remain
    # owned by bubblewrap in both modes.
    proc_mount = (
        ["--ro-bind", "/proc", "/proc"]
        if _container_procfs()
        else ["--proc", "/proc"]
    )
    command = [
        sandbox,
        *_sandbox_namespaces(),
        "--die-with-parent",
        "--new-session",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        *proc_mount,
        "--dev", "/dev",
        "--tmpfs", "/dev/shm",
        "--dir", "/etc",
        "--dir", "/tmp",
        "--dir", "/workspace",
        "--ro-bind" if read_only else "--bind", str(workspace), "/workspace",
        "--chdir", "/tmp" if read_only else "/workspace",
        "--clearenv",
        "--setenv", "HOME", "/tmp",
        "--setenv", "USER", "tool",
        "--setenv", "LOGNAME", "tool",
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
    # Read-only CPU topology supports native MPI hardware discovery.
    for topology in ('/sys/devices/system/cpu', '/sys/devices/system/node'):
        if Path(topology).is_dir():
            command.extend(['--ro-bind', topology, topology])
    # Bind a disposable directory from the workspace filesystem as /tmp.
    # This keeps standard-library tempfile + os.replace operations on one
    # device while still isolating caches from the business workspace.
    tmp_mount = tempfile.mkdtemp(prefix=".tool-tmp-", dir=workspace.parent)
    command.extend(["--bind", tmp_mount, "/tmp"])
    # Container images may omit the Kubernetes runAsUser from /etc/passwd.
    # Supply only this call's identity, without changing the container image.
    passwd = Path(tmp_mount) / 'passwd'
    group = Path(tmp_mount) / 'group'
    passwd.write_text(f'tool:x:{os.getuid()}:{os.getgid()}:tool:/tmp:/bin/sh\n', encoding='utf-8')
    group.write_text(f'tool:x:{os.getgid()}:\n', encoding='utf-8')
    command.extend(['--ro-bind', str(passwd), '/etc/passwd',
                    '--ro-bind', str(group), '/etc/group'])
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
        profile_environment = runtime_environment(Path(software['root']), {})
        for name in ('PATH', 'LD_LIBRARY_PATH', 'PETSC_DIR', 'SLEPC_DIR'):
            value = profile_environment.get(name)
            if value:
                command.extend(['--setenv', name, value])
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


def run_tool_direct(
    code, arguments, workspace, timeout, memory_limit, write_limit,
    environment=None, software_root=None, *, process_limit=1024,
    software=None, read_only=False, record_database=None, scope_roots=None,
):
    """Run validated delivery code in its runtime without nested bubblewrap.

    The caller owns transaction rollback and path authorization. This process
    boundary supplies time/resource limits and keeps tool stdout off MCP stdio.
    """
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        return {"kind": "exception", "result": None, "error": "工具工作目录不存在"}
    before_bytes, before_entries, workspace_error = _workspace_usage(workspace)
    if workspace_error:
        return {"kind": "exception", "result": None, "error": workspace_error}
    database = Path(record_database).resolve() if record_database else None
    before_database_bytes = database.stat().st_size if database and database.is_file() else 0
    roots = {
        str(key): str(Path(value).resolve())
        for key, value in (scope_roots or {}).items()
    }
    effective_software_root = (
        Path(software_root).expanduser().resolve()
        if software_root is not None
        else (
            Path(str(software["root"])).expanduser().resolve()
            if software and software.get("root")
            else None
        )
    )
    # Use the exact import environment of the already-isolated MCP server. The
    # delivery interpreter may rely on its configured user site for harness
    # dependencies such as jsonschema; ``-I`` would incorrectly hide those.
    # Do not resolve the executable symlink: resolving a venv/profile launcher
    # to its base interpreter drops that profile's site-packages.
    executable = (
        Path(str(software["python"])).expanduser().absolute()
        if software and software.get("python")
        else Path(sys.executable).absolute()
    )
    if not executable.is_file():
        return {
            "kind": "exception",
            "result": None,
            "error": f"工具 Python 不存在：{executable}",
        }
    command = [str(executable), "-c", _TOOL_WORKER]
    with tempfile.TemporaryDirectory(prefix="agent-world-tool-") as temporary:
        payload = json.dumps({
            "code": code,
            "arguments": arguments,
            "environment": environment or {},
            "context_source": _context_source(),
            "workspace_root": str(workspace),
            "record_database": str(database) if database else None,
            "scope_roots": roots,
            "read_only": read_only,
            "direct_path_guard": True,
            "writable_roots": [temporary, "/dev/null", "/dev/shm"],
            "memory_limit": memory_limit,
            "write_limit": write_limit,
            # RLIMIT_NPROC is counted across the real UID, not this tool process.
            # Applying a per-call value here makes thread creation fail whenever
            # unrelated processes for the same user already exceed that value.
            # Direct task execution relies on path authorization, transaction
            # rollback, timeout, memory, and write limits instead.
            "process_limit": None,
            "software_root": (
                str(effective_software_root) if effective_software_root else None
            ),
            "software_prefix": None,
            "software_import_paths": [],
        }, ensure_ascii=False)
        if software and software.get("root"):
            from env_gen.tool_gen.software import runtime_environment

            environment_variables = runtime_environment(Path(str(software["root"])), os.environ)
        else:
            environment_variables = dict(os.environ)
        environment_variables.update({
            "HOME": temporary,
            "USER": f"agent-world-{os.getuid()}",
            "LOGNAME": f"agent-world-{os.getuid()}",
            "TMPDIR": temporary,
            "XDG_CACHE_HOME": str(Path(temporary) / "cache"),
            "XDG_CONFIG_HOME": str(Path(temporary) / "config"),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OMP_THREAD_LIMIT": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "JAX_NUM_THREADS": "1",
            "TF_NUM_INTRAOP_THREADS": "1",
            "TF_NUM_INTEROP_THREADS": "1",
            "XLA_FLAGS": "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1",
        })
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
                        cwd=temporary if read_only else workspace,
                        env=environment_variables,
                    )
                except subprocess.TimeoutExpired:
                    return {"kind": "timeout", "result": None, "error": f"工具调用超过 {timeout} 秒"}
                except OSError as error:
                    return {"kind": "exception", "result": None, "error": f"启动工具进程失败：{error}"}
                stdout_value = _read_limited(stdout)
                stderr_value = _read_limited(stderr, 2000)
        except OSError as error:
            return {"kind": "exception", "result": None, "error": f"工具进程 I/O 失败：{error}"}
    if completed.returncode != 0:
        detail = (stderr_value or stdout_value).decode("utf-8", errors="replace").strip()[-2000:]
        return {"kind": "exception", "result": None, "error": f"工具进程异常退出：{detail}"}
    if len(stdout_value) > _MAX_SANDBOX_OUTPUT_BYTES:
        return {"kind": "exception", "result": None, "error": "工具进程输出超过 16 MiB"}
    after_bytes, after_entries, workspace_error = _workspace_usage(workspace)
    if workspace_error:
        return {"kind": "exception", "result": None, "error": workspace_error}
    after_database_bytes = database.stat().st_size if database and database.is_file() else 0
    if after_bytes + after_database_bytes > before_bytes + before_database_bytes + write_limit:
        return {"kind": "exception", "result": None, "error": f"任务状态总增长超过 {write_limit} 字节"}
    if after_entries > before_entries + max(1024, write_limit // 4096):
        return {"kind": "exception", "result": None, "error": "工作目录新增条目数量超过限制"}
    try:
        message = json.loads(stdout_value)
    except json.JSONDecodeError as error:
        return {"kind": "exception", "result": None, "error": f"工具进程返回无效 JSON：{error}"}
    if not isinstance(message, dict) or set(message) != {"result", "error"}:
        return {"kind": "exception", "result": None, "error": "工具进程返回结构非法"}
    if message["error"] is not None:
        return {"kind": "exception", "result": None, "error": message["error"]}
    return {"kind": None, "result": message["result"], "error": None}


_call_tool = call_tool
run_tool = run_tool_in_sandbox
workspace_usage = _workspace_usage
_run_tool = run_tool_in_sandbox
_workspace_usage = _workspace_usage
_read_limited = _read_limited

__all__ = ["call_tool", "run_tool", "run_tool_direct", "workspace_usage", "_call_tool", "_run_tool", "_workspace_usage", "_read_limited"]


def _inside_docker_runtime() -> bool:
    return os.environ.get("AGENT_WORLD_DOCKER_RUNTIME") == "1" and Path("/.dockerenv").is_file()


def run_tool_in_docker(
    code, arguments, workspace, timeout, memory_limit, write_limit,
    environment=None, *, process_limit=1024, runtime, read_only=False, record_database=None, scope_roots=None,
    software_root=None, software=None,
):
    if _inside_docker_runtime():
        return run_tool_direct(
            code, arguments, workspace, timeout, memory_limit, write_limit, environment,
            process_limit=process_limit, software_root=Path(runtime["software_root"]),
            read_only=read_only, record_database=record_database, scope_roots=scope_roots,
        )
    workspace = workspace.resolve()
    before_bytes, before_entries, workspace_error = _workspace_usage(workspace)
    if workspace_error:
        return {"kind": "exception", "result": None, "error": workspace_error}
    docker = shutil.which("docker")
    if docker is None:
        return {"kind": "exception", "result": None, "error": "找不到 Docker 命令"}

    database = Path(record_database).resolve() if record_database else None
    mounts = {}
    for scope, value in (scope_roots or {}).items():
        location = Path(value).resolve()
        if not location.is_relative_to(workspace):
            raise ValueError("Scope root 必须位于当前任务工作目录内")
        mounts[str(scope)] = str(Path("/workspace") / location.relative_to(workspace))
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
        "read_only": read_only,
        "record_database": "/records/records.sqlite" if database else None,
        "scope_roots": mounts,
    }, ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix=".tool-docker-", dir=workspace.parent) as control:
        cidfile = Path(control) / "container.cid"
        passwd = Path(control) / "passwd"
        passwd.write_text(
            f"root:x:0:0:root:/root:/bin/sh\n"
            f"tool:x:{os.getuid()}:{os.getgid()}:tool:/tmp:/bin/sh\n",
            encoding="utf-8",
        )
        database_mount = (["--tmpfs", f"/records:rw,size={write_limit}",
                           "--mount", f"type=bind,source={database},target=/records/records.sqlite"
                           + (",readonly" if read_only else "")] if database else [])
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
            "--workdir", "/tmp" if read_only else "/workspace",
            "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
            "--env", "XDG_CACHE_HOME=/tmp/cache",
            "--env", "XDG_CONFIG_HOME=/tmp/config",
            "--env", f"TOOLGEN_SOFTWARE_ROOT={runtime['software_root']}",
            "--env", "OPENBLAS_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=1",
            *database_mount,
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
