from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

_CODEX_LLM_API_KEY_ENV = "AGENT_WORLD_LLM_API_KEY"
_PARENT_SESSION_ENV = (
    "CODEX_THREAD_ID",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_PERMISSION_PROFILE",
)


class CodexLaunchError(RuntimeError):
    """Codex CLI 缺失或无法启动；重试同一配置不会恢复。"""

    retryable = False


class CodexTimeoutError(TimeoutError):
    """单次 Agent 会话超时；现场可能已有进展，可以启动续跑轮次。"""

    retryable = True


class CodexProcessError(RuntimeError):
    """Codex CLI 已启动，但以非零状态退出。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def _process_failure_is_retryable(detail: str) -> bool:
    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            "429",
            "rate limit",
            "temporarily unavailable",
            "service unavailable",
            "connection reset",
            "connection refused",
            "timed out",
            "timeout",
            "overloaded",
            "502",
            "503",
            "504",
        )
    )


def _toml_string(value: str) -> str:
    """把字符串编码成可安全传给 ``codex -c`` 的 TOML 字符串。"""

    # JSON 字符串和 TOML 基础字符串的转义规则在这里兼容，避免手工拼接引号。
    return json.dumps(value, ensure_ascii=False)


class CodexAgentClient:
    """对本机 ``codex exec`` 命令的最小 Python 封装。

    默认直接使用本机 ``~/.codex/config.toml``。也可以在初始化时单独传入
    model、base_url 和 api_key，只覆盖本次 Codex 子进程。需要 Agent 用
    命令下载真实文件时，同时启用 ``network_access``；网页搜索和命令行
    网络访问是两个独立权限。
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        executable: str | None = None,
        timeout_seconds: int = 1800,
        sandbox: str = "workspace-write",
        enable_web_search: bool = False,
        network_access: bool = False,
        reasoning_effort: str | None = None,
        disabled_mcp_servers: tuple[str, ...] = (),
        log_directory: Path | None = None,
    ):
        if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError(f"不支持的 Codex sandbox 模式：{sandbox}")
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.sandbox = sandbox
        self.enable_web_search = enable_web_search
        self.network_access = network_access
        self.reasoning_effort = reasoning_effort
        self.disabled_mcp_servers = tuple(disabled_mcp_servers)
        self.log_directory = log_directory
        self._run_counter = 0

    def _llm_arguments(self, environment: dict[str, str]) -> list[str]:
        """生成本次 Codex 调用的模型参数，并把密钥只放入子进程环境。"""

        arguments: list[str] = []
        if self.model:
            arguments.extend(["--model", self.model])

        base_url = (self.base_url or "").rstrip("/")
        if not base_url:
            # 没有显式地址时，provider 和认证沿用本机 Codex 配置。
            return arguments

        provider = "agent_world_llm"
        arguments.extend(
            [
                "--config",
                f"model_provider={_toml_string(provider)}",
                "--config",
                f"model_providers.{provider}.name={_toml_string('Agent World LLM')}",
                "--config",
                f"model_providers.{provider}.base_url={_toml_string(base_url)}",
                "--config",
                f"model_providers.{provider}.wire_api={_toml_string('responses')}",
                "--config",
                f"model_providers.{provider}.requires_openai_auth=false",
            ]
        )
        if self.api_key:
            environment[_CODEX_LLM_API_KEY_ENV] = self.api_key
            arguments.extend(
                [
                    "--config",
                    f"model_providers.{provider}.env_key="
                    f"{_toml_string(_CODEX_LLM_API_KEY_ENV)}",
                ]
            )
        return arguments

    def run(self, prompt: str, *, working_directory: Path) -> str:
        """执行一次不保留会话的 Codex 调用，并返回最终响应。"""

        return self._run_process(prompt, working_directory=working_directory)

    def run_until_files(
        self,
        prompt: str,
        *,
        working_directory: Path,
        required_paths: tuple[Path, ...],
    ) -> str:
        """文件提交点出现后立即结束 Agent，避免等待无关的最终总结。"""

        if not required_paths:
            return self.run(prompt, working_directory=working_directory)
        return self._run_process(
            prompt,
            working_directory=working_directory,
            stop_when=required_paths,
        )

    def _run_process(
        self,
        prompt: str,
        *,
        working_directory: Path,
        stop_when: tuple[Path, ...] = (),
    ) -> str:
        """启动 Codex；可选地在指定文件全部出现后终止子进程。"""

        executable = self.executable or shutil.which("codex")
        if not executable:
            raise CodexLaunchError("未安装 Codex CLI，或者无法从 PATH 找到 codex")
        if not prompt.strip():
            raise ValueError("Codex 提示词不能为空")

        working_directory = working_directory.resolve()
        if not working_directory.is_dir():
            raise ValueError(f"Codex 工作目录不存在：{working_directory}")

        if self.log_directory is None:
            directory_context = tempfile.TemporaryDirectory(
                prefix="agent-world-codex-"
            )
        else:
            log_root = self.log_directory.resolve()
            while True:
                self._run_counter += 1
                run_log_dir = log_root / f"run_{self._run_counter:02d}"
                try:
                    run_log_dir.mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    continue
                break
            directory_context = nullcontext(str(run_log_dir))

        with directory_context as run_log_directory:
            final_message = Path(run_log_directory) / "last_message.txt"
            stdout_log = Path(run_log_directory) / "stdout.log"
            stderr_log = Path(run_log_directory) / "stderr.log"
            environment = dict(os.environ)
            # DataGen 启动的是独立研究 Agent，不是当前 IDE/Codex 会话的子回合。
            # 继承这些标识会让 ``codex exec`` 误绑定父线程或父权限配置，出现
            # 未执行任何工具就结束的情况。保留 CODEX_HOME 以继续使用认证和
            # provider 配置，但显式移除父会话身份。
            for name in _PARENT_SESSION_ENV:
                environment.pop(name, None)
            environment.setdefault("NO_COLOR", "1")

            command = [executable]
            if self.enable_web_search:
                # --search 是 Codex 顶层参数，必须写在 exec 子命令之前。
                command.append("--search")
            command.append("exec")
            for server_name in self.disabled_mcp_servers:
                # Codex 当前没有通用的 --no-mcp 参数；将指定用户 MCP 标记为
                # disabled，避免失效的远程服务阻塞本次 Agent 调用。
                command.extend(
                    ["--config", f"mcp_servers.{server_name}.enabled=false"]
                )
            if self.reasoning_effort:
                if self.reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
                    raise ValueError(
                        "reasoning_effort 必须是 minimal、low、medium、high 或 xhigh"
                    )
                command.extend(
                    ["--config", f"model_reasoning_effort={self.reasoning_effort}"]
                )
            command.extend(self._llm_arguments(environment))
            if self.sandbox == "workspace-write" and self.network_access:
                # 搜索工具只能帮助 Agent 找来源；下载真实 API 响应和公开文件还
                # 需要允许沙箱中的 curl、git 等命令访问网络。
                command.extend(
                    ["--config", "sandbox_workspace_write.network_access=true"]
                )
            command.extend(
                [
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--sandbox",
                    self.sandbox,
                    "--cd",
                    str(working_directory),
                    "--output-last-message",
                    str(final_message),
                    "-",  # 从 stdin 读取提示词，避免把提示词拼接成 shell 命令。
                ]
            )

            process: subprocess.Popen[str] | None = None
            stopped_at_checkpoint = False
            try:
                with stdout_log.open("w", encoding="utf-8") as stdout_stream, stderr_log.open(
                    "w", encoding="utf-8"
                ) as stderr_stream:
                    process = subprocess.Popen(
                        command,
                        cwd=working_directory,
                        env=environment,
                        stdin=subprocess.PIPE,
                        stdout=stdout_stream,
                        stderr=stderr_stream,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        start_new_session=True,
                    )
                    assert process.stdin is not None
                    process.stdin.write(prompt)
                    process.stdin.close()
                    deadline = time.monotonic() + self.timeout_seconds
                    while process.poll() is None:
                        if stop_when and all(path.resolve().is_file() for path in stop_when):
                            stopped_at_checkpoint = True
                            self._terminate_process_group(process)
                            break
                        if time.monotonic() >= deadline:
                            self._terminate_process_group(process)
                            raise CodexTimeoutError(
                                f"Codex 智能体在 {self.timeout_seconds} 秒后超时"
                            )
                        time.sleep(0.25)
                    if process.poll() is None:
                        process.wait()
                stdout = stdout_log.read_text(encoding="utf-8", errors="replace")
                stderr = stderr_log.read_text(encoding="utf-8", errors="replace")
            except (CodexTimeoutError, CodexLaunchError, CodexProcessError):
                raise
            except OSError as error:
                if process is not None and process.poll() is None:
                    self._terminate_process_group(process)
                raise CodexLaunchError(f"启动 Codex CLI 失败：{error}") from error
            except BaseException:
                if process is not None and process.poll() is None:
                    self._terminate_process_group(process)
                raise

            if not stopped_at_checkpoint and process.returncode != 0:
                detail = (stderr or stdout).strip()[-4000:]
                detail = detail or "没有诊断输出"
                raise CodexProcessError(
                    f"Codex 智能体执行失败，退出码为 {process.returncode}：{detail}",
                    retryable=_process_failure_is_retryable(detail),
                )
            if not final_message.is_file():
                if stopped_at_checkpoint:
                    return "已到达文件提交点。"
                raise CodexProcessError(
                    "Codex 智能体执行结束，但没有生成最终响应",
                    retryable=False,
                )

            response = final_message.read_text(encoding="utf-8").strip()
            if not response:
                raise CodexProcessError(
                    "Codex 智能体生成了空的最终响应",
                    retryable=False,
                )
            return response

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        """终止 Codex 及其子进程，避免网络命令或代码宿主残留。"""

        if process.poll() is not None:
            return
        try:
            # ``start_new_session=True`` 使子进程 PID 同时是进程组 ID。
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            # 非 Unix 环境或进程组已经退出时，至少终止 Popen 对象本身。
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            process.wait(timeout=3)


__all__ = [
    "CodexAgentClient",
    "CodexLaunchError",
    "CodexProcessError",
    "CodexTimeoutError",
]
