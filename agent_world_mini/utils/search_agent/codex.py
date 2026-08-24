from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_CODEX_LLM_API_KEY_ENV = "AGENT_WORLD_LLM_API_KEY"


def _toml_string(value: str) -> str:
    """把字符串编码成可安全传给 ``codex -c`` 的 TOML 字符串。"""

    # JSON 字符串和 TOML 基础字符串的转义规则在这里兼容，避免手工拼接引号。
    return json.dumps(value, ensure_ascii=False)


class CodexAgentClient:
    """对本机 ``codex exec`` 命令的最小 Python 封装。

    默认直接使用本机 ``~/.codex/config.toml``。也可以在初始化时单独传入
    model、base_url 和 api_key，只覆盖本次 Codex 子进程。
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
            environment = dict(os.environ)
            environment.setdefault("NO_COLOR", "1")

            command = [executable]
            if self.enable_web_search:
                # --search 是 Codex 顶层参数，必须写在 exec 子命令之前。
                command.append("--search")
            command.append("exec")
            command.extend(self._llm_arguments(environment))
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

            try:
                completed = subprocess.run(
                    command,
                    cwd=working_directory,
                    env=environment,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"Codex 智能体在 {self.timeout_seconds} 秒后超时"
                ) from error
            except OSError as error:
                raise RuntimeError(f"启动 Codex CLI 失败：{error}") from error

            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-4000:]
                raise RuntimeError(
                    f"Codex 智能体执行失败，退出码为 {completed.returncode}："
                    f"{detail or '没有诊断输出'}"
                )
            if not final_message.is_file():
                raise RuntimeError("Codex 智能体执行结束，但没有生成最终响应")

            response = final_message.read_text(encoding="utf-8").strip()
            if not response:
                raise RuntimeError("Codex 智能体生成了空的最终响应")
            return response
