"""Step 3：由 LLM 逐步生成参数，并在隔离子进程中真实执行候选工具链。

本文件把 Step 2 产生的 ``chain`` 变成可核验的执行轨迹。每条链从相同的
只读源 workspace 开始；候选链可并发，但任意两条链不共享可写目录。

输入与配置
==========

``stage_input`` 包含 ``config``、``run_dir``、``environment`` 和 ``tasks``。
``run_dir`` 是 run_io 为本次运行创建的独立目录。每项 task 必须包含
Step 2 分配的唯一非空 ``task_id`` 和非空 ``chain``；chain 中的工具名必须
存在于 ``environment.tools``。执行参数为：

* ``config.execution.max_concurrency``：并发执行的候选链数，默认 ``4``；
* ``config.execution.retry_count``：首次失败后的重试次数，默认 ``3``，
  即每条链最多尝试 ``4`` 次；
* ``config.execution.tool_timeout_seconds``：单次工具调用硬超时，默认 ``300`` 秒；
* ``config.execution.tool_result_max_bytes``：传给后续 LLM 的单条工具结果上限，
  默认 ``65536``（64 KiB）；原始结果仍完整保存在执行轨迹中；
* ``config.execution.tool_max_write_bytes``：单次工具调用允许写出的最大字节数，
  默认 ``268435456``（256 MiB）；
* ``config.execution.tool_max_memory_bytes``：单次工具调用的地址空间上限，
  默认 ``2147483648``（2 GiB）。

后两项是信任边界的一部分，理由见下方“信任边界”。

启动任何并发任务前，先一次性校验全部 ``task_id`` 唯一、chain 合法，并确认所有
``run_dir/tasks/<task_id>`` 均不存在。任一冲突都终止整个 Step 3；不得执行
一部分候选后才发现目录冲突，也不得覆盖或复用历史任务目录。

LLM 任务意图与逐步填参
=====================

每个候选先根据环境公开信息、全部工具公开定义和完整 chain 生成一次自然语言
``task_intent``。它用于统一整条链的目标、目标选择方式和需要主动创作的参数，
只是本阶段内部的软计划，不写入 Bundle；后续真实工具结果与它冲突时以真实结果为准。

随后对 chain 中的每个工具按顺序生成参数。LLM 只能看到：

* 环境名称、描述、``resources`` 和 ``rules``；
* ``task_intent``、完整 chain、已执行部分、当前工具和后续部分；
* 当前工具的 ``name``、``description`` 和 ``inputSchema``；
* 本次尝试已成功调用的 ``arguments`` 和公开 ``result``；
* 重试时，上一次尝试的失败工具、失败参数、错误原因和已完成调用。

LLM 不得看到 ``tools[].internal``，也不得直接读取或接收整个 workspace 内容。
公开环境明确列出的资源和前序真实 result 可以作为既有事实使用；ID、文件、数据库
记录、查询结果、余额和状态等未观察事实不得编造。标题、说明、评论、署名、严重级别、
报告名称等任务创作值可以由 LLM 决定，只需合理、方向一致且不与已知事实冲突。
LLM 固定返回 ``{"arguments": {...}}``，解析必须使用
:func:`tool_graph.llm.parse_json_object`，再用当前工具的 ``inputSchema``
本地校验。不再使用同名字段匹配、枚举首值或类型默认值机械填参。

workspace 生命周期
==================

每个候选使用 ``run_dir/tasks/<task_id>/``：

``initial/``
    从 ``config.environment_dir/workspace`` 完整递归复制得到，创建后保持不变。
    它就是该任务的 ``initial_state``：一个与环境源 workspace 同构的目录路径。

``final/``
    每次整链尝试前从 ``initial/`` 重新复制；工具只在这里执行。失败后删除，重试时
    再从 ``initial/`` 创建，不能在已污染状态上继续。成功后原地保留为
    ``final_state``，其目录结构与 ``initial_state`` 同构。

源 workspace 永远只读。若 ``tasks/<task_id>`` 已存在必须报错，不覆盖旧状态。
只有完整成功的候选保留 ``initial/`` 和 ``final/``；全部失败后删除任务目录，
但在 Bundle 中保留所有尝试记录。保存的路径一律相对 ``run_dir``，因此固定形如
``tasks/<task_id>/initial`` 和 ``tasks/<task_id>/final``，不包含 run_dir 自身名称。

真实执行与硬超时
==================

1. 每次工具调用都在独立进程组中的子进程里加载 ``internal.code`` 并执行
   ``run(arguments, context)``，context 的 ``workspace_root`` 指向当前 final 目录。
   ``context`` 必须是暴露 ``workspace_root`` **属性**的对象（参考环境的工具代码
   使用 ``context.workspace_root``，传 dict 会让全部工具立即失败）。
   ``workspace_root`` 必须是该 final 目录的绝对 ``Path``。
2. 超过 ``tool_timeout_seconds`` 后，父进程必须终止整个工具进程组，包括工具自行
   创建的子孙进程；宽限后仍存活则强制 kill，并回收直接子进程。不得只让等待超时，
   也不得只杀直接子进程而把子孙进程留在后台。
3. 工具返回值必须是 JSON-native object，通过 ``outputSchema`` 校验，且
   ``success is True``。异常退出、超时、无结果、不可序列化、Schema 失败或
   ``success=false`` 都立即结束本次尝试，不执行后续工具。
4. 工具代码抛出的裸异常必须捕获为该次尝试的 ``failure_kind="exception"``，
   ``error`` 记录异常类型和消息。工具代码不保证在所有输入下都规规矩矩返回
   ``{"success": false, ...}``；参考环境中的工具会直接抛 ``FileNotFoundError``、
   ``KeyError`` 等。子进程崩溃不得冒泡成候选级异常或中止其他候选。

信任边界
========

``internal.code`` 由上游环境生成流程产出，不是本流水线编写或审计的代码，
但 Step 3 会把它整体加载进子进程执行。它可以自由 ``import``（参考环境中已出现
``zipfile``、``hashlib``、``csv``），因此按“不可信但需要真实文件系统副作用”处理。
当前使用 Linux ``bubblewrap`` 建立文件系统、网络和 PID namespace 隔离；它是本阶段
执行不可信工具的必要运行条件，缺失时拒绝执行，不降级为宿主进程内执行。

必须施加的约束：

1. **文件系统与网络**：只读挂载当前 Python 运行时，只把 final workspace 挂载为
   可写；宿主其他路径不可见，网络 namespace 不与宿主共享。
2. **workspace 结构**：调用前后都只允许普通文件和目录；符号链接、FIFO、socket、
   device 等特殊条目立即判失败，避免后续宿主复制跟随链接。
3. **源状态保护**：每个候选开始时记录源 workspace 内容签名，每次整链尝试后复核；
   若被修改，本候选立即失败。
4. **资源上限**：子进程设置单文件、地址空间和进程数限制；调用后额外检查 workspace
   文件总增长和新增条目数。stdout/stderr 写入受限临时文件并定长读取，避免父进程
   无界累积输出。上限值来自配置，超时由父进程强制终止整个 namespace。
5. **环境变量**：清空继承环境，只恢复 locale 和时区，并把 HOME/TMPDIR 指向沙箱
   内路径；不向工具代码透传 API key 等凭据。

必须知道的既有事实（不是要求，而是实现时会遇到的情况）：

* 同一环境内**所有工具携带的 ``internal.code`` 完全相同**，只有尾部
  ``run()`` 传入的 operation 名不同。三个参考环境各自只有一份唯一的
  ``_dispatch`` 体，被复制到每个工具上。
* 该共享 ``_dispatch`` 处理的 operation 多于环境实际拥有的工具数
  （参考环境中分别多出 6、3、7 个），这些**孤儿分支**没有对应工具，
  其中若干会读取 workspace 中并不存在的路径。当前它们不可达，因为
  ``run()`` 只会传入本工具自己的 operation。
* 因此 Step 3 执行的代码面比流水线校验的公开契约面大得多：逐工具校验的是
  ``inputSchema``/``outputSchema``，实际加载的是整份模板含死分支。上游一旦
  改动模板，本阶段的行为面随之变化，而 Step 0–5 没有任何检查会察觉。
  这是上述约束按"不可信代码"处理的直接理由，不要因为孤儿分支当前不可达
  就省略越界写入核对。

重试与并发
==========

任务意图以及逐工具填参的 LLM/JSON 错误会按配置重试；参数 Schema 错误只在当前工具
位置重新生成参数，不重复执行已经成功的前缀；耗尽参数重试次数后结束候选。工具异常、
业务失败和输出 Schema 错误会删除
``final`` 并从 ``initial`` 重跑整条 chain；已成功前缀的参数可以复用，失败工具及后续
参数重新生成。硬超时、内存/文件限制和源 workspace 越界修改不盲目重试。

候选链并发执行，但输出 ``tasks`` 顺序必须与输入一致。一个候选失败
不中止其他候选；候选级异常必须转换为该候选的失败 execution。

输出
====

保留 task 已有字段并新增 ``execution``：

.. code-block:: python

    {
        "success": bool,
        "tool_calls": [
            {"tool": str, "arguments": dict, "result": dict},
        ],
        "initial_state": str | None,
        "final_state": str | None,
        "error": str | None,
        "attempts": [
            {
                "attempt": int,
                "success": bool,
                "tool_calls": list[dict],
                "failed_tool": str | None,
                "failed_arguments": dict | None,
                "failure_kind": str | None,
                "failed_result": dict | None,
                "error": str | None,
            },
        ],
    }

``initial_state`` 和 ``final_state`` 都是相对本次 ``run_dir`` 的 workspace
目录路径，不是文件内容快照或资源数组。``attempts`` 保存每次实际尝试。
``failure_kind`` 使用 ``llm``、``input_schema``、``timeout``、``exception``、
``business`` 或 ``output_schema``，成功尝试为 ``None``；``failed_result`` 只在工具
已经返回 JSON object 但仍判失败时保存原始结果，否则为 ``None``。
顶层 ``tool_calls`` 在成功时保存最终完整轨迹；全部失败时保存最后一次
在失败前完成的调用。``error`` 成功时为 ``None``，失败时为最后错误。
全部失败时删除任务目录，两个 state 字段均为 ``None``。

完成条件
========

每项输入 task 恰好对应一项输出 task。每条链要么具有真实执行成功的完整
轨迹以及同构的 ``initial_state``、``final_state`` workspace 路径，要么具有
最多四次完整的失败记录。
本阶段不修改 chain、不生成任务文本、不丢弃失败候选，也不留下仍在运行的工具子进程。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

from jsonschema import validators

from .contracts import ExecuteChainsInput, ExecuteChainsOutput
from .llm import infer, parse_json_object


def execute_chains(stage_input: ExecuteChainsInput) -> ExecuteChainsOutput:
    """并发执行候选链，以干净初态重试，并记录成功轨迹或失败历史。"""
    config = stage_input["config"]
    run_dir = stage_input["run_dir"].resolve()
    source = (config.environment_dir / "workspace").resolve()
    if not source.is_dir():
        raise ValueError(f"源 workspace 不存在：{source}")
    tools = _tools(stage_input["environment"])
    tasks = stage_input["tasks"]
    if not isinstance(tasks, list):
        raise ValueError("tasks 必须是 array")

    task_root = run_dir / "tasks"
    task_root.mkdir(parents=True, exist_ok=True)
    ids: list[str] = []
    for index, candidate in enumerate(tasks):
        if not isinstance(candidate, dict):
            raise ValueError(f"tasks[{index}] 必须是 object")
        task_id = candidate.get("task_id")
        chain = candidate.get("chain")
        if not isinstance(task_id, str) or not task_id or "/" in task_id or task_id in {".", ".."}:
            raise ValueError(f"tasks[{index}].task_id 非法")
        if not isinstance(chain, list) or not chain or any(name not in tools for name in chain):
            raise ValueError(f"tasks[{index}].chain 非法或包含未知工具")
        ids.append(task_id)
        if (task_root / task_id).exists():
            raise ValueError(f"任务目录已存在：{task_id}")
    if len(ids) != len(set(ids)):
        raise ValueError("task_id 必须唯一")

    concurrency = _integer(config.execution, "max_concurrency", 4, minimum=1)
    retries = _integer(config.execution, "retry_count", 3)
    timeout = _integer(config.execution, "tool_timeout_seconds", 300, minimum=1)
    result_limit = _integer(config.execution, "tool_result_max_bytes", 65536, minimum=1)
    memory_limit = _integer(config.execution, "tool_max_memory_bytes", 2 * 1024 * 1024 * 1024, minimum=1)
    write_limit = _integer(config.execution, "tool_max_write_bytes", 256 * 1024 * 1024, minimum=1)
    with ThreadPoolExecutor(max_workers=min(concurrency, len(tasks)) or 1) as executor:
        output = list(executor.map(
            lambda candidate: _execute_candidate(
                candidate,
                stage_input["environment"],
                tools,
                config.llm,
                source,
                task_root,
                retries,
                timeout,
                result_limit,
                memory_limit,
                write_limit,
            ),
            tasks,
        ))
    return {"tasks": output}


def _tools(environment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = environment.get("tools")
    if not isinstance(values, list) or not values:
        raise ValueError("environment.tools 必须是非空数组")
    result: dict[str, dict[str, Any]] = {}
    for tool in values:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("工具缺少合法 name")
        name = tool["name"]
        if name in result:
            raise ValueError(f"工具名重复：{name}")
        if not isinstance(tool.get("inputSchema"), dict) or not isinstance(tool.get("outputSchema"), dict):
            raise ValueError(f"工具 {name} 缺少输入输出 Schema")
        internal = tool.get("internal")
        if not isinstance(internal, dict) or not isinstance(internal.get("code"), str):
            raise ValueError(f"工具 {name} 缺少 internal.code")
        result[name] = tool
    return result


def _integer(config: dict[str, Any], name: str, default: int, *, minimum: int = 0) -> int:
    value = config.get(name, default)
    if type(value) is not int or value < minimum:
        raise ValueError(f"execution.{name} 必须是大于等于 {minimum} 的整数")
    return value


def _execute_candidate(
    candidate: dict[str, Any],
    environment: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    llm_config: dict[str, Any],
    source: Path,
    tasks_root: Path,
    retries: int,
    timeout: int,
    result_limit: int,
    memory_limit: int,
    write_limit: int,
) -> dict[str, Any]:
    result = deepcopy(candidate)
    task_id = candidate["task_id"]
    chain = candidate["chain"]
    root = tasks_root / task_id
    attempts: list[dict[str, Any]] = []
    previous_failure: dict[str, Any] | None = None
    argument_cache: dict[int, dict[str, Any]] = {}
    source_signature = _workspace_signature(source)
    try:
        task_intent, intent_failure = _intent_with_retry(
            task_id, chain, environment, tools, llm_config, retries,
        )
        if intent_failure is not None:
            attempts.append({
                "attempt": 1,
                "success": False,
                "tool_calls": [],
                **intent_failure,
            })
            result["execution"] = {
                "success": False,
                "tool_calls": [],
                "initial_state": None,
                "final_state": None,
                "error": intent_failure["error"],
                "attempts": attempts,
            }
            return result

        root.mkdir()
        initial = root / "initial"
        shutil.copytree(source, initial)
        for number in range(1, retries + 2):
            final = root / "final"
            shutil.copytree(initial, final)
            calls: list[dict[str, Any]] = []
            failure: dict[str, Any] | None = None
            for position, tool_name in enumerate(chain):
                tool = tools[tool_name]
                if position in argument_cache:
                    arguments, parameter_failure = deepcopy(argument_cache[position]), None
                else:
                    arguments, parameter_failure = _arguments_with_retry(
                        task_id,
                        chain,
                        position,
                        tool,
                        environment,
                        calls,
                        previous_failure,
                        llm_config,
                        retries,
                        result_limit,
                        task_intent,
                    )
                if parameter_failure is not None:
                    failure = parameter_failure
                    break

                outcome = _call_tool(tool["internal"]["code"], arguments, final, timeout, memory_limit, write_limit)
                if outcome["kind"] is not None:
                    failure = _failure(tool_name, arguments, outcome["kind"], outcome.get("result"), outcome["error"])
                    break
                tool_result = outcome["result"]
                if not isinstance(tool_result, dict):
                    failure = _failure(tool_name, arguments, "output_schema", tool_result, "工具返回值必须是 object")
                    break
                if tool_result.get("success") is not True:
                    failure = _failure(tool_name, arguments, "business", tool_result, _business_error(tool_result))
                    break
                schema_error = _schema_error(tool["outputSchema"], tool_result)
                if schema_error is not None:
                    failure = _failure(tool_name, arguments, "output_schema", tool_result, schema_error)
                    break
                calls.append({"tool": tool_name, "arguments": arguments, "result": tool_result})
                argument_cache[position] = deepcopy(arguments)

            if _workspace_signature(source) != source_signature:
                failure = _failure(
                    failure["failed_tool"] if failure else chain[-1],
                    failure["failed_arguments"] if failure else None,
                    "exception",
                    None,
                    "源 workspace 在执行期间被修改",
                )

            attempt_record = {
                "attempt": number,
                "success": failure is None,
                "tool_calls": calls,
                "failed_tool": failure["failed_tool"] if failure else None,
                "failed_arguments": failure["failed_arguments"] if failure else None,
                "failure_kind": failure["failure_kind"] if failure else None,
                "failed_result": failure["failed_result"] if failure else None,
                "error": failure["error"] if failure else None,
            }
            attempts.append(attempt_record)
            if failure is None:
                result["execution"] = {
                    "success": True,
                    "tool_calls": calls,
                    "initial_state": f"tasks/{task_id}/initial",
                    "final_state": f"tasks/{task_id}/final",
                    "error": None,
                    "attempts": attempts,
                }
                return result
            if not _retryable(failure):
                shutil.rmtree(final, ignore_errors=True)
                break
            previous_failure = attempt_record
            shutil.rmtree(final, ignore_errors=True)
        last = attempts[-1]
        shutil.rmtree(root, ignore_errors=True)
        result["execution"] = {
            "success": False,
            "tool_calls": last["tool_calls"],
            "initial_state": None,
            "final_state": None,
            "error": last["error"],
            "attempts": attempts,
        }
        return result
    except Exception as error:
        attempts.append({
            "attempt": len(attempts) + 1,
            "success": False,
            "tool_calls": [],
            "failed_tool": None,
            "failed_arguments": None,
            "failure_kind": "exception",
            "failed_result": None,
            "error": f"{type(error).__name__}: {error}",
        })
        shutil.rmtree(root, ignore_errors=True)
        result["execution"] = {
            "success": False,
            "tool_calls": attempts[-1]["tool_calls"],
            "initial_state": None,
            "final_state": None,
            "error": attempts[-1]["error"],
            "attempts": attempts,
        }
        return result


def _intent_with_retry(
    task_id: str,
    chain: list[str],
    environment: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    llm_config: dict[str, Any],
    retries: int,
) -> tuple[str | None, dict[str, Any] | None]:
    last_failure: dict[str, Any] | None = None
    for _ in range(retries + 1):
        try:
            return _generate_intent(task_id, chain, environment, tools, llm_config), None
        except Exception as error:
            last_failure = _failure(None, None, "llm", None, f"{type(error).__name__}: {error}")
    return None, last_failure


def _generate_intent(
    task_id: str,
    chain: list[str],
    environment: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    llm_config: dict[str, Any],
) -> str:
    prompt = json.dumps({
        "task": (
            "先根据完整调用链推测一个从头到尾方向一致、合理可执行的任务意图，供后续逐工具填参参考。"
            "可以决定目标选择策略以及标题、说明、评论、署名、严重级别等任务创作值；"
            "不能把未观察到的 ID、文件、数据库记录、查询结果、余额或状态写成既有事实。"
            "公开环境明确列出的资源可以直接选择。此意图只是软计划，后续真实工具结果优先。"
            "只返回 JSON：{\"task_intent\":\"非空自然语言说明\"}。"
        ),
        "task_id": task_id,
        "environment": _public_environment(environment),
        "tools": [
            {key: tool[key] for key in ("name", "description", "inputSchema", "outputSchema")}
            for tool in tools.values()
        ],
        "chain": chain,
    }, ensure_ascii=False)
    payload = parse_json_object(infer(prompt, llm_config=llm_config).text)
    intent = payload.get("task_intent")
    if set(payload) != {"task_intent"} or not isinstance(intent, str) or not intent.strip():
        raise ValueError("LLM 必须只返回非空字符串字段 task_intent")
    return intent.strip()


def _arguments_with_retry(
    task_id: str,
    chain: list[str],
    position: int,
    tool: dict[str, Any],
    environment: dict[str, Any],
    calls: list[dict[str, Any]],
    previous_failure: dict[str, Any] | None,
    llm_config: dict[str, Any],
    retries: int,
    result_limit: int,
    task_intent: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    last_failure: dict[str, Any] | None = None
    for _ in range(retries + 1):
        try:
            arguments = _generate_arguments(
                task_id, chain, position, tool, environment, calls,
                last_failure or previous_failure, llm_config,
                result_limit,
                task_intent,
            )
            schema_error = _schema_error(tool["inputSchema"], arguments)
            if schema_error is not None:
                raise _ArgumentError(arguments, schema_error)
            return arguments, None
        except _ArgumentError as error:
            last_failure = _failure(tool["name"], error.arguments, "input_schema", None, error.message)
        except Exception as error:
            last_failure = _failure(tool["name"], None, "llm", None, f"{type(error).__name__}: {error}")
    return None, last_failure


class _ArgumentError(ValueError):
    def __init__(self, arguments: dict[str, Any], message: str) -> None:
        super().__init__(message)
        self.arguments = arguments
        self.message = message


def _generate_arguments(
    task_id: str,
    chain: list[str],
    position: int,
    tool: dict[str, Any],
    environment: dict[str, Any],
    calls: list[dict[str, Any]],
    previous_failure: dict[str, Any] | None,
    llm_config: dict[str, Any],
    result_limit: int,
    task_intent: str,
) -> dict[str, Any]:
    prompt = json.dumps({
        "task": (
            "依据整条链的任务意图和真实执行进度，为当前工具生成方向一致的合理参数。"
            "可以从公开环境或前序真实 result 中选择目标，也可以生成标题、说明、评论、署名、"
            "严重级别、报告名称等任务创作值，只要与任务意图和已知事实一致。"
            "新建交易的日期、金额和分录内容，以及任务要求新增的其他业务值，也属于任务创作值；"
            "它们应合理、内部一致并满足 Schema。表示既有事实的动态 ID、文件路径、数据库记录、"
            "查询结果、余额和状态，必须由公开环境"
            "明确给出或来自前序真实 result；若有工具负责读取这些事实，绝不能编造其返回内容。"
            "不得使用 <id>、example、1000、coa_main 等占位符或猜测值；必须先检查 completed_calls。"
            "如果当前工具的必填事实无法从公开环境或 completed_calls 获得，返回空 arguments，"
            "让本地 Schema 校验明确拒绝该链，不要伪造一个看似合理的参数。"
            "任务意图与真实结果冲突时以真实结果为准。只返回 JSON："
            "{\"arguments\":{...}}。"
        ),
        "task_id": task_id,
        "task_intent": task_intent,
        "environment": _public_environment(environment),
        "chain": chain,
        "completed_chain": chain[:position],
        "position": position,
        "current_tool": {
            key: tool[key]
            for key in ("name", "description", "inputSchema")
        },
        "remaining_chain": chain[position + 1:],
        "completed_calls": _bounded_calls(calls, result_limit),
        "previous_failure": previous_failure,
    }, ensure_ascii=False)
    payload = parse_json_object(infer(prompt, llm_config=llm_config).text)
    if set(payload) != {"arguments"} or not isinstance(payload["arguments"], dict):
        raise ValueError("LLM 必须只返回 object 字段 arguments")
    return payload["arguments"]


def _public_environment(environment: dict[str, Any]) -> dict[str, Any]:
    return {
        key: environment.get(key)
        for key in ("environment_id", "name", "description", "resources", "rules")
    }


def _bounded_calls(calls: list[dict[str, Any]], limit: int = 65536) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for call in calls:
        item = deepcopy(call)
        encoded = json.dumps(item.get("result"), ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > limit:
            compact = _truncate_value(item["result"], max(16, limit // 8))
            item["result"] = {"_truncated": True, "data": compact}
        bounded.append(item)
    return bounded


def _truncate_value(value: Any, string_limit: int) -> Any:
    """保留小字段和标识值，裁掉大文本/大数组，避免参数 prompt 爆炸。"""
    if isinstance(value, str):
        return value if len(value.encode("utf-8")) <= string_limit else "[已裁剪]"
    if isinstance(value, list):
        result: list[Any] = []
        size = 2
        for item in value:
            compact = _truncate_value(item, string_limit)
            item_size = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
            if size + item_size > string_limit:
                break
            result.append(compact)
            size += item_size
        return result
    if isinstance(value, dict):
        return {str(key): _truncate_value(item, string_limit) for key, item in value.items()}
    return value


def _schema_error(schema: dict[str, Any], value: Any) -> str | None:
    validator = validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    return errors[0].message if errors else None


def _failure(tool: str | None, arguments: dict[str, Any] | None, kind: str, result: Any, error: str) -> dict[str, Any]:
    return {
        "failed_tool": tool,
        "failed_arguments": arguments,
        "failure_kind": kind,
        "failed_result": result,
        "error": error,
    }


def _business_error(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return "工具返回 success=false"


def _retryable(failure: dict[str, Any]) -> bool:
    if failure["failure_kind"] in {"llm", "input_schema", "timeout"}:
        return False
    error = str(failure.get("error") or "")
    return not any(marker in error for marker in ("MemoryError", "File too large", "源 workspace"))


def _workspace_signature(root: Path) -> tuple[tuple[str, int, str], ...]:
    """生成 workspace 内容签名；用于发现工具越界修改源 workspace。"""
    entries: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            entries.append((relative, -1, os.readlink(path)))
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((relative, path.stat().st_mode, digest))
        elif path.is_dir():
            entries.append((relative, path.stat().st_mode, ""))
    return tuple(entries)


_TOOL_WORKER = r"""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import resource
import sys
from types import SimpleNamespace

payload = json.load(sys.stdin)
try:
    memory_limit = int(payload["memory_limit"])
    write_limit = int(payload["write_limit"])
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (write_limit, write_limit))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    namespace = {"json": json}
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        exec(payload["code"], namespace)
        run = namespace.get("run")
        if not callable(run):
            raise ValueError("internal.code 没有定义 run(arguments, context)")
        result = run(
            deepcopy(payload["arguments"]),
            SimpleNamespace(workspace_root=Path("/workspace")),
        )
    json.dumps(result, ensure_ascii=False)
    response = {"result": result, "error": None}
except BaseException as error:
    response = {"result": None, "error": f"{type(error).__name__}: {error}"}
json.dump(response, sys.stdout, ensure_ascii=False)
"""
_MAX_SANDBOX_OUTPUT_BYTES = 16 * 1024 * 1024


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
) -> dict[str, Any]:
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
    try:
        runtime_executable = Path("/runtime") / executable.relative_to(runtime_root)
    except ValueError:
        return {"kind": "exception", "result": None, "error": "Python 解释器不在其运行时目录中"}
    command = [
        sandbox,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind", str(runtime_root), "/runtime",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", "/workspace",
        "--bind", str(workspace), "/workspace",
        "--chdir", "/workspace",
        "--clearenv",
        "--setenv", "HOME", "/workspace",
        "--setenv", "TMPDIR", "/tmp",
    ]
    for name in ("LANG", "LC_ALL", "TZ"):
        if name in os.environ:
            command.extend(["--setenv", name, os.environ[name]])
    command.extend([str(runtime_executable), "-I", "-c", _TOOL_WORKER])
    payload = json.dumps({
        "code": code,
        "arguments": arguments,
        "memory_limit": memory_limit,
        "write_limit": write_limit,
    }, ensure_ascii=False)
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
