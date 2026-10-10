#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Any
from urllib import error, request

ROOT = Path("/data1/agent_world/trajectory/semiconductor_kimi_k3_20260930")
MODEL = "kimi-k3"
SYSTEM_PROMPT = """你是训练轨迹的环境描述清洗器。根据每条任务的环境元数据和任务正文，生成与该任务相关但不泄漏解题路径的简洁环境说明。

要求：
1. 每条说明使用中文纯文本，2至4句，80至260个汉字左右。
2. 说明环境中可核验的信息类型、可执行操作的大致边界，以及真实/合成、离线/在线或发布/未签核等关键限制。
3. 根据任务关注点选择相关能力，不要照抄通用环境介绍。
4. 不得复述任务要求，不得给出解题步骤、工具名称、调用顺序、答案或结论。
5. 不得罗列表名、字段名、文件路径、数据库关系、具体批次/器件ID、日期、阈值或结果数值。
6. 不得出现Markdown、record_sets、relationships、filesystem_scopes或MCP前缀。
7. 严格返回JSON数组，元素格式为 {"case":"原case","environment_description":"说明"}，不得输出数组以外文本。"""


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def accepted_rows() -> dict[str, dict[str, Any]]:
    manifest = load(ROOT / "manifest.json")
    rows = list(manifest["accepted"])
    passed = {row["case"] for row in manifest["recovery_verifier"]["pass"]}
    rows.extend(row for row in manifest["pending_verifier"] if row["case"] in passed)
    indexed = {row["case"]: row for row in rows}
    if len(indexed) != 481:
        raise ValueError(f"expected 481 accepted trajectories, got {len(indexed)}")
    return indexed


def model_item(case: str, row: dict[str, Any]) -> dict[str, Any]:
    trajectory = load(Path(row["source"]) / "trajectory.json")
    environment = trajectory["environment"]
    return {
        "case": case,
        "environment": {
            "name": environment.get("name"),
            "summary": environment.get("summary"),
            "description": environment.get("description"),
        },
        "task": trajectory["task"].get("task_text"),
    }


def parse_json_array(content: str) -> list[dict[str, Any]]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            raise
        value = json.loads(content[start:end + 1])
    if not isinstance(value, list):
        raise ValueError("model output is not a JSON array")
    return value


def validate(items: list[dict[str, Any]], expected: set[str]) -> dict[str, str]:
    from distill.clean import validate_generated_environment_context

    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("case"), str):
            raise ValueError("model output item has no case")
        case = item["case"]
        if case in result:
            raise ValueError(f"duplicate model output case: {case}")
        result[case] = validate_generated_environment_context(item.get("environment_description"))
    if set(result) != expected:
        raise ValueError(f"model output cases differ: missing={sorted(expected-set(result))}, extra={sorted(set(result)-expected)}")
    return result


def retryable(status: int, body: str) -> bool:
    lowered = body.lower()
    return status in {408, 409, 429} or status >= 500 or (
        status == 400 and "internal maas component" in lowered
    )


def call_batch(
    batch_index: int, items: list[dict[str, Any]], output: Path, base_url: str, api_key: str,
) -> dict[str, str]:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
        ],
        "reasoning_effort": "high",
        "max_completion_tokens": 6000,
        "stream": False,
    }
    batch_dir = output / "model_io"
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / f"{batch_index:04d}.request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    url = base_url.rstrip("/") + "/chat/completions"
    last_error = None
    for attempt in range(1, 7):
        req = request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=600) as response:
                body = response.read().decode("utf-8")
            decoded = json.loads(body)
            (batch_dir / f"{batch_index:04d}.response.json").write_text(
                json.dumps(decoded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            content = decoded["choices"][0]["message"].get("content") or ""
            return validate(parse_json_array(content), {item["case"] for item in items})
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {body[:1000]}")
            if not retryable(exc.code, body) or attempt == 6:
                raise last_error
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            last_error = exc
            if attempt == 6:
                raise
        delay = min(60.0, 2 ** (attempt - 1)) + random.random()
        time.sleep(delay)
    raise RuntimeError(str(last_error))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()
    base_url = os.environ.get("KIMI_BASE_URL")
    api_key = os.environ.get("KIMI_API_KEY")
    if not base_url or not api_key:
        raise SystemExit("KIMI_BASE_URL and KIMI_API_KEY are required")
    rows = accepted_rows()
    if args.selection:
        selected = load(args.selection)
        cases = [row["case"] for row in selected.get("samples", selected.get("records", []))]
    else:
        cases = sorted(rows)
    items = [model_item(case, rows[case]) for case in cases]
    batches = [items[i:i + args.batch_size] for i in range(0, len(items), args.batch_size)]
    args.output.mkdir(parents=True, exist_ok=True)
    collected = {}
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(call_batch, index, batch, args.output, base_url, api_key): index
            for index, batch in enumerate(batches, 1)
        }
        for future in as_completed(futures):
            result = future.result()
            with lock:
                collected.update(result)
                snapshot = {
                    "schema_version": "agent_world.task_environment_descriptions.v1",
                    "provenance": "model_generated",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "model": MODEL,
                    "reasoning_effort": "high",
                    "count": len(collected),
                    "descriptions": dict(sorted(collected.items())),
                }
                temporary = args.output / ".descriptions.json.tmp"
                temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temporary.replace(args.output / "descriptions.json")
                print(f"generated {len(collected)}/{len(items)}", flush=True)


if __name__ == "__main__":
    main()
