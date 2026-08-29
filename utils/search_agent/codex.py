from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

_CODEX_LLM_API_KEY_ENV = "AGENT_WORLD_LLM_API_KEY"


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

    def run_until_json_file(
        self,
        prompt: str,
        *,
        working_directory: Path,
        required_path: Path,
    ) -> str:
        """草稿成为完整且稳定的 JSON 后结束本次 Agent 调用。"""

        return self._run_process(
            prompt,
            working_directory=working_directory,
            stop_when=(required_path,),
            stable_json_path=required_path,
        )

    def _run_process(
        self,
        prompt: str,
        *,
        working_directory: Path,
        stop_when: tuple[Path, ...] = (),
        stable_json_path: Path | None = None,
    ) -> str:
        """启动 Codex；可选地在指定文件全部出现后终止子进程。"""

        executable = self.executable or shutil.which("codex")
        if not executable:
            raise RuntimeError("未安装 Codex CLI，或者无法从 PATH 找到 codex")
        if not prompt.strip():
            raise ValueError("Codex 提示词不能为空")

        working_directory = working_directory.resolve()
        if not working_directory.is_dir():
            raise ValueError(f"Codex 工作目录不存在：{working_directory}")

        with tempfile.TemporaryDirectory(prefix="agent-world-codex-") as temporary:
            final_message = Path(temporary) / "last_message.txt"
            stdout_log = Path(temporary) / "stdout.log"
            stderr_log = Path(temporary) / "stderr.log"
            environment = dict(os.environ)
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
            stable_since: float | None = None
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
                            if stable_json_path is None:
                                stopped_at_checkpoint = True
                                self._terminate_process_group(
                                    process,
                                    wait_for_paths=(stdout_log, stderr_log),
                                )
                                break
                            try:
                                json.loads(stable_json_path.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError):
                                stable_since = None
                            else:
                                stable_since = stable_since or time.monotonic()
                                if time.monotonic() - stable_since >= 1.0:
                                    stopped_at_checkpoint = True
                                    self._terminate_process_group(
                                        process,
                                        wait_for_paths=(stdout_log, stderr_log),
                                    )
                                    break
                        else:
                            stable_since = None
                        if time.monotonic() >= deadline:
                            self._terminate_process_group(
                                process,
                                wait_for_paths=(stdout_log, stderr_log),
                            )
                            raise RuntimeError(
                                f"Codex 智能体在 {self.timeout_seconds} 秒后超时"
                            )
                        time.sleep(0.25)
                    if process.poll() is None:
                        process.wait()
                stdout = stdout_log.read_text(encoding="utf-8", errors="replace")
                stderr = stderr_log.read_text(encoding="utf-8", errors="replace")
            except RuntimeError:
                raise
            except OSError as error:
                if process is not None and process.poll() is None:
                    self._terminate_process_group(
                        process,
                        wait_for_paths=(stdout_log, stderr_log),
                    )
                raise RuntimeError(f"启动 Codex CLI 失败：{error}") from error

            if not stopped_at_checkpoint and process.returncode != 0:
                detail = (stderr or stdout).strip()[-4000:]
                raise RuntimeError(
                    f"Codex 智能体执行失败，退出码为 {process.returncode}："
                    f"{detail or '没有诊断输出'}"
                )
            if not final_message.is_file():
                if stopped_at_checkpoint:
                    return "已到达文件提交点。"
                raise RuntimeError("Codex 智能体执行结束，但没有生成最终响应")

            response = final_message.read_text(encoding="utf-8").strip()
            if not response:
                raise RuntimeError("Codex 智能体生成了空的最终响应")
            return response

    @staticmethod
    def _terminate_process_group(
        process: subprocess.Popen[str],
        *,
        wait_for_paths: tuple[Path, ...] = (),
    ) -> None:
        """终止 Codex 及其子进程，避免网络命令或代码宿主残留。"""

        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            # Codex 的子进程退出后可能短暂保留重定向日志的文件句柄。
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    for path in wait_for_paths:
                        probe = path.with_name(path.name + ".release-probe")
                        path.replace(probe)
                        probe.replace(path)
                    break
                except PermissionError:
                    time.sleep(0.1)
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
