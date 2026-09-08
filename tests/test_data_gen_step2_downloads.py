from __future__ import annotations

from contextlib import contextmanager
import gzip
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from typing import Iterator
import zipfile

from env_gen.data_gen.analysis.collection_analysis import (
    _repository_url_stability,
    build_source_inventory,
    inspect_data_path,
    prepare_archive,
    validate_source_inventory,
)
from env_gen.data_gen.analysis.seed import canonical_json_sha256
from env_gen.data_gen.config import CollectionPolicy
from env_gen.data_gen.steps.common.constants import SOURCE_RESEARCH_PATH
from env_gen.data_gen.steps.common.download import (
    DownloadFailure,
    discard_download,
    download_raw_file,
    download_receipt_issues,
    load_download_ledger,
)
from env_gen.data_gen.steps.step1_research_scenario import save_scenario_research
from env_gen.data_gen.steps.step2_collect_data import (
    DataCollectionError,
    _build_collection_prompt,
    _finalize_agent_result,
    _prepare_collection,
    read_saved_source_research,
    run_data_collection,
    source_research_receipt_issues,
)
from tests.data_gen_test_helpers import (
    ROOT,
    prepare_step0,
    scenario_payload,
    write_json,
)


class _Handler(BaseHTTPRequestHandler):
    item_payload = json.dumps({
        "items": [
            {"item_id": "i1", "name": "Alpha", "category": "a"},
            {"item_id": "i2", "name": "Beta", "category": "b"},
        ]
    }).encode()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/missing.json":
            self.send_error(404)
            return
        if self.path in {"/items.json", "/same.json"}:
            body, content_type = self.item_payload, "application/json"
        elif self.path == "/invalid.json":
            body, content_type = b"not json", "application/json"
        elif self.path == "/items.csv":
            body, content_type = b"item_id,name\ni1,Alpha\ni2,Beta\n", "text/csv"
        elif self.path == "/events.json.gz":
            body = gzip.compress(b'{"event":"created"}\n{"event":"updated"}\n')
            content_type = "application/gzip"
        elif self.path == "/invalid.json.gz":
            body, content_type = b"not gzip", "application/gzip"
        elif self.path == "/project.zip":
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("project/a.txt", "A")
                archive.writestr("project/b.txt", "B")
            body, content_type = stream.getvalue(), "application/zip"
        elif self.path == "/unsafe.zip":
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("../escape.txt", "bad")
            body, content_type = stream.getvalue(), "application/zip"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def prepare_collection_run(
    run_dir: Path,
    *,
    policy: CollectionPolicy | None = None,
) -> tuple[dict, str]:
    seed, digest = prepare_step0(run_dir, policy=policy)
    save_scenario_research(run_dir, scenario_payload(seed, digest))
    _prepare_collection(run_dir)
    return seed, digest


def full_card(
    path: str = "raw/items.json",
    *,
    url: str = "https://example.test/items.json",
    status: str = "supported",
    role: str = "business_records",
    limitations: list[str] | str | None = None,
) -> dict:
    return {
        "path": path,
        "url": url,
        "source_id": "items",
        "role": role,
        "name": "Public catalog items",
        "summary": "Two real catalog item records with stable identifiers, names and categories.",
        "subjects": [
            {"subject_type": "entity", "subject_name": "Item", "status": status, "reason": "The file contains real item instances."},
            {"subject_type": "tool", "subject_name": "list_items", "status": status, "reason": "The records can be listed and filtered."},
            {"subject_type": "task", "subject_name": "Browse items by category", "status": status, "reason": "Categories and item identities are present."},
            {
                "subject_type": "task",
                "subject_name": "Find public items in one category and inspect the result.",
                "status": status,
                "reason": "The category task has direct records.",
            },
        ],
        "prepared_paths": [],
        "limitations": limitations or [],
    }


class DownloadLedgerTests(unittest.TestCase):
    def test_download_returns_only_coarse_file_facts(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            result = download_raw_file(
                run_dir,
                url=f"{base}/items.json",
                output="raw/items.json",
                expected_format="json",
                timeout_seconds=10,
                source_id="items",
            )
            self.assertEqual(result["record_count"], 2)
            self.assertEqual(result["file_count"], 1)
            self.assertEqual(result["format"], "json")
            self.assertNotIn("fields", result)
            self.assertTrue((run_dir / "workspace/raw/items.json").is_file())
            self.assertEqual(download_receipt_issues(run_dir), [])

    def test_same_request_is_not_downloaded_twice(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            first = download_raw_file(
                run_dir, url=f"{base}/items.json", output="raw/items.json",
                source_id="items", expected_format="json", timeout_seconds=10,
            )
            second = download_raw_file(
                run_dir, url=f"{base}/items.json", output="raw/other.json",
                source_id="other", expected_format="json", timeout_seconds=10,
            )
            self.assertEqual(second["status"], "already_downloaded")
            self.assertEqual(second["path"], first["path"])
            self.assertFalse((run_dir / "workspace/raw/other.json").exists())
            self.assertEqual(len(load_download_ledger(run_dir)["downloads"]), 1)

    def test_same_content_from_another_url_reuses_hash(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            download_raw_file(
                run_dir, url=f"{base}/items.json", output="raw/items.json",
                source_id="items", expected_format="json", timeout_seconds=10,
            )
            result = download_raw_file(
                run_dir, url=f"{base}/same.json", output="raw/same.json",
                source_id="same", expected_format="json", timeout_seconds=10,
            )
            self.assertEqual(result["status"], "duplicate_content")
            self.assertEqual(result["path"], "raw/items.json")
            self.assertFalse((run_dir / "workspace/raw/same.json").exists())
            self.assertEqual(len(load_download_ledger(run_dir)["downloads"][0]["urls"]), 2)

    def test_invalid_or_missing_file_records_failure(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            with self.assertRaises(DownloadFailure):
                download_raw_file(
                    run_dir, url=f"{base}/invalid.json", output="raw/invalid.json",
                    source_id="invalid", expected_format="json", timeout_seconds=10,
                )
            with self.assertRaises(DownloadFailure):
                download_raw_file(
                    run_dir, url=f"{base}/missing.json", output="raw/missing.json",
                    source_id="missing", expected_format="json", timeout_seconds=10,
                )
            self.assertEqual(len(load_download_ledger(run_dir)["failures"]), 2)

    def test_csv_count_and_semantic_discard(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            result = download_raw_file(
                run_dir, url=f"{base}/items.csv", output="raw/items.csv",
                source_id="items", expected_format="csv", timeout_seconds=10,
            )
            self.assertEqual(result["record_count"], 2)
            discarded = discard_download(run_dir, path="raw/items.csv", reason="wrong business topic")
            self.assertEqual(discarded["status"], "rejected")
            self.assertFalse((run_dir / "workspace/raw/items.csv").exists())

    def test_gzip_is_streamed_for_coarse_record_count(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            result = download_raw_file(
                run_dir, url=f"{base}/events.json.gz", output="raw/events.json.gz",
                source_id="events", expected_format="gz", timeout_seconds=10,
            )
            self.assertEqual(result["format"], "gzip")
            self.assertEqual(result["record_count"], 2)
            self.assertEqual(result["file_count"], 1)

    def test_invalid_gzip_records_download_failure(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            with self.assertRaises(DownloadFailure):
                download_raw_file(
                    run_dir, url=f"{base}/invalid.json.gz", output="raw/invalid.json.gz",
                    source_id="invalid_gzip", expected_format="gzip", timeout_seconds=10,
                )
            self.assertEqual(load_download_ledger(run_dir)["failures"][0]["code"], "invalid_file")


class CoarseInspectionTests(unittest.TestCase):
    def test_archive_is_counted_and_safely_extracted(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            downloaded = download_raw_file(
                run_dir, url=f"{base}/project.zip", output="raw/project.zip",
                source_id="project", expected_format="zip", timeout_seconds=10,
                role="task_domain_files",
            )
            self.assertEqual(downloaded["file_count"], 2)
            prepared = prepare_archive(run_dir, input_path="raw/project.zip")
            self.assertEqual(prepared["file_count"], 2)
            self.assertTrue(str(prepared["prepared_path"]).startswith("prepared/"))

    def test_unsafe_archive_is_rejected_during_extraction(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            download_raw_file(
                run_dir, url=f"{base}/unsafe.zip", output="raw/unsafe.zip",
                source_id="unsafe", expected_format="zip", timeout_seconds=10,
                role="task_domain_files",
            )
            with self.assertRaises(RuntimeError):
                prepare_archive(run_dir, input_path="raw/unsafe.zip")

    def test_inventory_contains_file_card_not_field_profile(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed, digest = prepare_collection_run(run_dir)
            write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
            write_json(run_dir / ".datagen/collection_result.json", {
                "schema_version": "1.0",
                "result": "ready",
                "summary": "The Agent accepted complete real catalog records.",
                "file_cards": [full_card(url=f"{base}/items.json")],
            })
            _finalize_agent_result(run_dir)
            research = read_saved_source_research(run_dir)
            inventory = build_source_inventory(
                run_dir,
                seed_global_id=seed["global_id"],
                seed_sha256=digest,
                source_research=research,
            )
            self.assertEqual(inventory["summary"]["structured_record_count"], 2)
            self.assertEqual(inventory["files"][0]["record_count"], 2)
            self.assertNotIn("shape", inventory["files"][0])
            schema = ROOT / "env_gen/data_gen/analysis/checkpoint_schemas/source_inventory.schema.json"
            self.assertEqual(validate_source_inventory(inventory, schema), [])

    def test_inventory_deduplicates_shared_source_limitations(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
            (run_dir / "workspace/raw/items.csv").write_bytes(
                b"item_id,name\ni1,Alpha\ni2,Beta\n"
            )
            limitation = "Both files share the same public-source limitation."
            write_json(run_dir / ".datagen/collection_result.json", {
                "schema_version": "1.0",
                "result": "ready",
                "summary": "Two source files share one limitation without duplicating source issues.",
                "file_cards": [
                    full_card(url=f"{base}/items.json", limitations=[limitation]),
                    full_card(
                        path="raw/items.csv",
                        url=f"{base}/items.csv",
                        limitations=[limitation],
                    ),
                ],
            })
            _decision, inventory = _finalize_agent_result(run_dir)
            source = next(item for item in inventory["sources"] if item["source_id"] == "items")
            self.assertEqual(source["issues"], [limitation])


class AgentCollectionResultTests(unittest.TestCase):
    def _finalize(self, run_dir: Path, base: str, *, status: str, result: str, role: str = "business_records") -> tuple[str, dict]:
        write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
        write_json(run_dir / ".datagen/collection_result.json", {
            "schema_version": "1.0",
            "result": result,
            "summary": "The Agent completed its own download and coverage loop.",
            "file_cards": [full_card(url=f"{base}/items.json", status=status, role=role)],
        })
        return _finalize_agent_result(run_dir)

    def test_agent_ready_result_is_finalized(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            decision, _inventory = self._finalize(run_dir, base, status="supported", result="ready")
            self.assertEqual(decision, "ready")
            self.assertEqual(source_research_receipt_issues(run_dir), [])
            self.assertEqual(read_saved_source_research(run_dir)["result"], "ready")
            self.assertEqual(download_receipt_issues(run_dir), [])

    def test_agent_partial_result_is_honored(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            decision, _inventory = self._finalize(run_dir, base, status="partial", result="partial")
            self.assertEqual(decision, "partial")
            profile = json.loads((run_dir / ".datagen/collection_profile.json").read_text())
            self.assertEqual(profile["decision"], "partial")
            self.assertEqual(profile["metrics"]["seed"]["overall"]["percent"], 0.0)
            self.assertEqual(profile["metrics"]["seed"]["tool"]["percent"], 0.0)

    def test_ready_below_coverage_floor_is_rejected(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            with self.assertRaisesRegex(RuntimeError, "低于最低验收线"):
                self._finalize(run_dir, base, status="partial", result="ready")

    def test_single_limitation_string_is_normalized(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
            write_json(run_dir / ".datagen/collection_result.json", {
                "schema_version": "1.0",
                "result": "partial",
                "summary": "The Agent kept one useful file with a documented limitation.",
                "file_cards": [full_card(
                    url=f"{base}/items.json",
                    limitations="Only public records are available.",
                )],
            })
            _finalize_agent_result(run_dir)
            profile = json.loads((run_dir / ".datagen/collection_profile.json").read_text())
            self.assertEqual(profile["file_cards"][0]["limitations"], ["Only public records are available."])

    def test_semantic_evidence_is_rejected_as_a_third_file_type(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            with self.assertRaisesRegex(RuntimeError, "role 无效"):
                self._finalize(
                    run_dir, base, status="supported", result="partial",
                    role="semantic_evidence",
                )

    def test_data_independent_tool_is_reported_and_excluded_from_denominator(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            seed_path = run_dir / ".datagen/selected_seed.json"
            seed = json.loads(seed_path.read_text())
            seed["init_ref_tools"][0]["description"] = (
                "This service explicitly always returns an empty collection."
            )
            write_json(seed_path, seed)
            write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
            card = full_card(url=f"{base}/items.json")
            card["subjects"] = [
                item for item in card["subjects"]
                if not (item["subject_type"] == "tool" and item["subject_name"] == "list_items")
            ]
            write_json(run_dir / ".datagen/collection_result.json", {
                "schema_version": "1.0",
                "result": "partial",
                "summary": "The listing protocol has no initial data dependency in this test.",
                "data_independent_tools": [{
                    "tool_name": "list_items",
                    "reason": "The source contract explicitly defines a fixed empty collection.",
                }],
                "file_cards": [card],
            })
            _finalize_agent_result(run_dir)
            profile = json.loads((run_dir / ".datagen/collection_profile.json").read_text())
            finding = next(
                item for item in profile["coverage"]
                if item["subject_type"] == "tool" and item["subject_name"] == "list_items"
            )
            self.assertEqual(finding["status"], "not_required")
            self.assertEqual(profile["metrics"]["seed"]["tool"], {
                "supported": 0,
                "total": 0,
                "not_required": 1,
                "listed_total": 1,
                "percent": 100.0,
            })
            self.assertEqual(profile["gaps"], [])

    def test_data_independent_tool_cannot_also_have_file_support(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            seed_path = run_dir / ".datagen/selected_seed.json"
            seed = json.loads(seed_path.read_text())
            seed["init_ref_tools"][0]["description"] = (
                "This service explicitly always returns an empty collection."
            )
            write_json(seed_path, seed)
            write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
            write_json(run_dir / ".datagen/collection_result.json", {
                "schema_version": "1.0",
                "result": "partial",
                "summary": "The result incorrectly classifies one tool in two different ways.",
                "data_independent_tools": [{
                    "tool_name": "list_items",
                    "reason": "The source contract explicitly defines a fixed empty collection.",
                }],
                "file_cards": [full_card(url=f"{base}/items.json")],
            })
            with self.assertRaisesRegex(RuntimeError, "同时登记文件支持和无需初始数据"):
                _finalize_agent_result(run_dir)

    def test_regular_query_tool_cannot_be_excluded_from_data_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            write_json(run_dir / ".datagen/collection_result.json", {
                "schema_version": "1.0",
                "result": "partial",
                "summary": "The result incorrectly excludes a normal record query tool.",
                "data_independent_tools": [{
                    "tool_name": "list_items",
                    "reason": "The Agent claims that this normal listing tool needs no records.",
                }],
                "file_cards": [],
            })
            with self.assertRaisesRegex(RuntimeError, "不能排除数据需求"):
                _finalize_agent_result(run_dir)

    def test_untracked_raw_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            write_json(run_dir / "workspace/raw/untracked.json", {"items": []})
            write_json(run_dir / ".datagen/collection_result.json", {
                "schema_version": "1.0",
                "result": "insufficient_data",
                "summary": "The Agent found no usable business data after searching.",
                "file_cards": [],
            })
            with self.assertRaisesRegex(RuntimeError, "workspace/raw 与文件卡不一致"):
                _finalize_agent_result(run_dir)

    def test_prompt_is_task_focused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_collection_run(run_dir)
            prompt = _build_collection_prompt(run_dir)
            self.assertIn("构建一个可以离线运行的真实业务环境", prompt)
            self.assertIn("来源平台提供的原始入口", prompt)
            self.assertIn("不视为已经核实的事实", prompt)
            self.assertIn("基于外部来源核实并扩展后的现实业务报告", prompt)
            self.assertIn("`data_directions` 用于选择数据", prompt)
            self.assertIn("`open_questions` 用于避免", prompt)
            self.assertIn("下载到 `workspace/raw/<source>/`", prompt)
            self.assertIn("不要在运行目录顶层另建 `raw/`", prompt)
            self.assertIn("文件卡中的 `path` 才省略 `workspace/` 前缀", prompt)
            self.assertIn("两种平级的数据形态", prompt)
            self.assertIn("`business_records`：后续会把内容拆成一条条记录", prompt)
            self.assertIn("`task_domain_files`：后续会保留文件名、目录和原始内容", prompt)
            self.assertIn("环境的真实核心数据", prompt)
            self.assertIn("能够实际支撑业务操作", prompt)
            self.assertIn("对象、状态、关系、标识和字段", prompt)
            self.assertIn("现实业务中实际产生、维护和使用", prompt)
            self.assertIn("尽可能同源或能够相互关联", prompt)
            self.assertIn("不能代替真实核心数据", prompt)
            opening = prompt.split("先读取两个输入：", 1)[0]
            self.assertNotIn("`supported`", opening)
            self.assertNotIn("`partial`", opening)
            self.assertIn("分类依据是后续如何使用，不是文件扩展名", prompt)
            self.assertIn("只需要其中一类还是两类都需要", prompt)
            self.assertIn("不要为了凑齐类型而下载", prompt)
            self.assertNotIn("`semantic_evidence`", prompt)
            self.assertIn("不要先搜完所有来源再统一整理", prompt)
            self.assertIn("同时支持最多清单项", prompt)
            self.assertIn("依次尝试最多 3 个真正可能提供同类业务对象的不同来源", prompt)
            self.assertIn("同一站点的不同 URL 不算多个来源", prompt)
            self.assertIn("预计提供的数据及失败原因", prompt)
            self.assertIn("任一来源成功", prompt)
            self.assertIn("后不必凑满 3 次", prompt)
            self.assertIn("目标网站有对应凭据时，从第一次请求就使用认证", prompt)
            self.assertIn("GitHub 使用 `gh api`", prompt)
            self.assertIn("必须先对同一端点做一次认证请求", prompt)
            self.assertIn("不得输出、复制或写入任何 Token", prompt)
            self.assertIn("一个受限端点不能作为停止整个采集的理由", prompt)
            self.assertIn("单文件 256 MiB", prompt)
            self.assertIn("Raw 合计 512 MiB", prompt)
            self.assertIn("workspace 合计 768 MiB", prompt)
            self.assertIn("Raw 文件最多 200 个", prompt)
            self.assertIn("`partial` 不计入覆盖率", prompt)
            self.assertIn("写操作不要求在真实网站上执行", prompt)
            self.assertIn("`data_independent_tools`", prompt)
            self.assertIn("它不算已覆盖，只从数据覆盖分母排除", prompt)
            self.assertIn("静态规则、分类、字典等可查询参考记录仍然属于初始数据", prompt)
            self.assertIn("禁止", prompt)
            self.assertIn("把提取出的少量文件重新打包后冒充上游完整归档", prompt)
            self.assertIn("至少覆盖 90%", prompt)
            self.assertIn("至少覆盖 75%", prompt)
            self.assertIn("低于任一最低线：继续采集", prompt)
            self.assertIn("达到两条最低线", prompt)
            self.assertNotIn("collectctl", prompt)
            self.assertNotIn("Step 1", prompt)
            self.assertNotIn("Step 2", prompt)
            self.assertNotIn("Step 3", prompt)


class CollectionLoopTests(unittest.TestCase):
    def test_one_agent_owns_download_loop_and_finishes(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed, digest = prepare_step0(run_dir)
            save_scenario_research(run_dir, scenario_payload(seed, digest))

            def runner(prompt: str, timeout: int, markers: tuple[Path, ...]) -> str:
                self.assertIn("按以下顺序重复执行", prompt)
                self.assertEqual(timeout, CollectionPolicy().source_collection_total_seconds)
                self.assertEqual(markers, ())
                write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
                write_json(run_dir / ".datagen/collection_result.json", {
                    "schema_version": "1.0",
                    "result": "ready",
                    "summary": "The Agent reached its coverage target and ended the loop.",
                    "file_cards": [full_card(url=f"{base}/items.json")],
                })
                return "done"

            decision, inventory, calls = run_data_collection(run_dir=run_dir, agent_runner=runner)
            self.assertEqual(decision, "ready")
            self.assertEqual(calls, 1)
            self.assertEqual(inventory["summary"]["file_count"], 1)
            self.assertTrue((run_dir / SOURCE_RESEARCH_PATH).is_file())

    def test_invalid_file_card_gets_one_focused_agent_repair(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            seed, digest = prepare_step0(run_dir)
            save_scenario_research(run_dir, scenario_payload(seed, digest))
            calls = 0

            def runner(prompt: str, timeout: int, markers: tuple[Path, ...]) -> str:
                nonlocal calls
                calls += 1
                self.assertEqual(markers, ())
                if calls == 1:
                    write_json(run_dir / "workspace/raw/items.json", json.loads(_Handler.item_payload))
                    invalid = full_card(path="raw/missing.json", url=f"{base}/items.json")
                    write_json(run_dir / ".datagen/collection_result.json", {
                        "schema_version": "1.0",
                        "result": "ready",
                        "summary": "The Agent completed collection but recorded one wrong path.",
                        "file_cards": [invalid],
                    })
                else:
                    self.assertIn("对应文件不存在", prompt)
                    self.assertIn("不再调查或下载新来源", prompt)
                    self.assertEqual(timeout, 180)
                    write_json(run_dir / ".datagen/collection_result.json", {
                        "schema_version": "1.0",
                        "result": "ready",
                        "summary": "The Agent repaired the existing file card path.",
                        "file_cards": [full_card(url=f"{base}/items.json")],
                    })
                    raise TimeoutError("The repair summary exceeded its deadline.")
                return "done"

            decision, inventory, agent_calls = run_data_collection(
                run_dir=run_dir,
                agent_runner=runner,
            )
            self.assertEqual(decision, "ready")
            self.assertEqual(agent_calls, 2)
            self.assertEqual(inventory["summary"]["file_count"], 1)

    def test_missing_agent_result_fails_after_one_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            policy = CollectionPolicy(source_collection_total_seconds=30)
            seed, digest = prepare_step0(run_dir, policy=policy)
            save_scenario_research(run_dir, scenario_payload(seed, digest))
            calls = 0

            def runner(_prompt: str, _timeout: int, _markers: tuple[Path, ...]) -> str:
                nonlocal calls
                calls += 1
                return "no result"

            with self.assertRaises(DataCollectionError):
                run_data_collection(run_dir=run_dir, agent_runner=runner)
            self.assertEqual(calls, 1)


class RepositoryStabilityTests(unittest.TestCase):
    def test_commit_and_branch_are_distinguished(self) -> None:
        sha = "a" * 40
        self.assertEqual(
            _repository_url_stability(f"https://raw.githubusercontent.com/o/r/{sha}/data.json"),
            "immutable_repository",
        )
        self.assertEqual(
            _repository_url_stability("https://raw.githubusercontent.com/o/r/main/data.json"),
            "mutable_repository",
        )
        self.assertIsNone(_repository_url_stability("https://example.test/items.json"))


if __name__ == "__main__":
    unittest.main()
