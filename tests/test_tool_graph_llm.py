"""Tool Graph LLM 适配层的行为检查。"""

from __future__ import annotations

from threading import Barrier, Lock
import unittest
from unittest.mock import patch

import task_gen.tool_graph.llm as llm


class FakeLLMClient:
    model = "test-model"
    client = object()

    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    @classmethod
    def from_environment(cls) -> "FakeLLMClient":
        return cls()

    def complete_messages(self, messages, *, on_delta=None, **_parameters):
        self.messages.append(messages)
        text = messages[-1]["content"]
        if on_delta:
            on_delta(text)
        return text, {"total_tokens": len(text)}


class ToolGraphLLMTest(unittest.TestCase):
    def test_codex_backend_uses_empty_read_only_workspace_and_preserves_order(self) -> None:
        calls: list[tuple[str, list[str]]] = []

        class FakeCodexClient:
            def __init__(self, **kwargs) -> None:
                self.model = kwargs.get("model")
                self.kwargs = kwargs

            def run(self, prompt, *, working_directory):
                calls.append((prompt, [path.name for path in working_directory.iterdir()]))
                return prompt.rsplit("<request>\n", 1)[-1]

        with (
            patch.object(llm, "CodexAgentClient", FakeCodexClient),
            patch.object(llm.LLMClient, "from_environment", side_effect=AssertionError("API client used")),
        ):
            results = llm.infer(
                ["first", "second"],
                llm_config={
                    "backend": "codex",
                    "model": "test-codex",
                    "max_concurrency": 1,
                    "timeout_seconds": 30,
                },
            )

        self.assertEqual([result.text for result in results], ["first", "second"])
        self.assertEqual([files for _prompt, files in calls], [[], []])
        self.assertTrue(all("不要使用工具或读取文件" in prompt for prompt, _files in calls))

    def test_parse_json_object_accepts_reasoning_wrapper_and_fence(self) -> None:
        self.assertEqual(
            llm.parse_json_object('<think>private reasoning</think>\n```json\n{"ok":true}\n```'),
            {"ok": True},
        )
        with self.assertRaises(ValueError):
            llm.parse_json_object('<think>x</think> [1, 2]')

    def test_infer_builds_multi_turn_messages_and_returns_metadata(self) -> None:
        client = FakeLLMClient()
        with patch.object(llm.LLMClient, "from_environment", return_value=client):
            try:
                result = llm.infer(
                    "继续",
                    system_prompt="系统",
                    history=[
                        {"role": "user", "content": "问题"},
                        {"role": "assistant", "content": "回答"},
                    ],
                    llm_config={
                        "model": "test-model",
                        "base_url": "https://example.test/v1",
                        "api_key_env": "TEST_KEY",
                    },
                )
            except Exception as error:
                self.fail(f"infer 的公开接口不可用：{error}")

        self.assertEqual(result.text, "继续")
        self.assertEqual(result.usage, {"total_tokens": 2})
        self.assertEqual(result.model, "test-model")
        self.assertEqual(
            client.messages,
            [[
                {"role": "system", "content": "系统"},
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "回答"},
                {"role": "user", "content": "继续"},
            ]],
        )

    def test_infer_runs_prompt_list_concurrently_and_preserves_order(self) -> None:
        barrier = Barrier(2, timeout=1)

        class ConcurrentClient(FakeLLMClient):
            def complete_messages(self, messages, **_parameters):
                barrier.wait()
                return messages[-1]["content"], {}

        with patch.object(
            llm.LLMClient,
            "from_environment",
            return_value=ConcurrentClient(),
        ):
            try:
                results = llm.infer(
                    ["first", "second"],
                    llm_config={"model": "test-model", "max_concurrency": 2},
                )
            except Exception as error:
                self.fail(f"prompt 数组没有并发执行：{error}")

        self.assertEqual([result.text for result in results], ["first", "second"])

    def test_batch_error_preserves_successful_results_by_position(self) -> None:
        class PartiallyFailingClient(FakeLLMClient):
            def complete_messages(self, messages, **_parameters):
                text = messages[-1]["content"]
                if text == "bad":
                    raise TimeoutError("timed out")
                return text, {}

        with patch.object(
            llm.LLMClient,
            "from_environment",
            return_value=PartiallyFailingClient(),
        ):
            with self.assertRaises(llm.BatchInferenceError) as caught:
                llm.infer(
                    ["first", "bad", "third"],
                    llm_config={"model": "test-model", "max_concurrency": 3},
                )

        outcomes = caught.exception.outcomes
        self.assertEqual(outcomes[0].text, "first")
        self.assertIsInstance(outcomes[1], TimeoutError)
        self.assertEqual(outcomes[2].text, "third")

    def test_infer_stream_callback_identifies_each_prompt(self) -> None:
        chunks: list[tuple[int, str]] = []
        lock = Lock()

        def collect(index: int, text: str) -> None:
            with lock:
                chunks.append((index, text))

        with patch.object(
            llm.LLMClient,
            "from_environment",
            return_value=FakeLLMClient(),
        ):
            try:
                llm.infer(
                    ["A", "B"],
                    llm_config={"model": "test-model", "max_concurrency": 2},
                    on_chunk=collect,
                )
            except Exception as error:
                self.fail(f"流式回调接口不可用：{error}")

        self.assertCountEqual(chunks, [(0, "A"), (1, "B")])

    def test_empty_config_keeps_environment_defaults(self) -> None:
        client = FakeLLMClient()
        with patch.object(
            llm.LLMClient,
            "from_environment",
            return_value=client,
        ):
            result = llm.infer("hello", llm_config={})

        self.assertEqual(result.model, "test-model")

    def test_result_usage_cannot_be_modified(self) -> None:
        result = llm.InferenceResult(
            text="hello",
            usage={"details": {"cached_tokens": 1}},
            model="test-model",
        )

        with self.assertRaises(TypeError):
            result.usage["total_tokens"] = 1
        with self.assertRaises(TypeError):
            result.usage["details"]["cached_tokens"] = 2


if __name__ == "__main__":
    unittest.main()
