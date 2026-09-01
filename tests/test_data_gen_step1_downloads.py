from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

from env_gen.data_gen.steps.collection.commands.download_raw import (
    DownloadFailure,
    _validate_download_file,
    cleanup_download_temporaries,
    download_raw_batch,
    download_raw_file,
    download_receipt_issues,
)
from env_gen.data_gen.steps.collection.commands.save_source_plan import (
    save_source_plan_payload,
)

from tests.data_gen_test_helpers import prepare_run, source_plan_payload


class _Handler(BaseHTTPRequestHandler):
    payload = json.dumps(
        {
            "items": [
                {"item_id": "i1", "name": "Alpha", "category": "a"},
                {"item_id": "i2", "name": "Beta", "category": "b"},
            ]
        }
    ).encode()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/missing.json":
            self.send_error(404)
            return
        if self.path in {"/items.json", "/same.json"}:
            body = self.payload
            content_type = "application/json"
        elif self.path == "/items.csv":
            body = b"item_id,name\ni1,Alpha\ni2,Beta\n"
            content_type = "text/csv"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def server() -> Iterator[str]:
    instance = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}"
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


class DownloadCommandTests(unittest.TestCase):
    def test_cleans_only_interrupted_download_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            raw = run_dir / "workspace/raw/calendar"
            raw.mkdir(parents=True)
            (raw / ".calendar.json.part-123").write_bytes(b"partial")
            (raw / ".calendar.json.headers-123").write_text("HTTP/2 200\n")
            (raw / ".keep.json").write_text("{}")

            removed = cleanup_download_temporaries(run_dir)

            self.assertEqual(removed, [
                "calendar/.calendar.json.headers-123",
                "calendar/.calendar.json.part-123",
            ])
            self.assertTrue((raw / ".keep.json").is_file())

    def test_validates_sqlite_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.sqlite"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE items (item_id TEXT NOT NULL)")
                connection.execute("INSERT INTO items VALUES ('i1')")
            _validate_download_file(path, "sqlite")

    def test_downloads_registered_exact_url_and_writes_receipt(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            url = f"{base}/items.json"
            prepare_run(run_dir, url=url)
            result = download_raw_file(
                run_dir,
                url=url,
                output="raw/items.json",
                expected_format="json",
                timeout_seconds=10,
                source_id="items",
            )
            self.assertEqual(result["status"], "downloaded")
            self.assertTrue((run_dir / "workspace/raw/items.json").is_file())
            self.assertEqual(download_receipt_issues(run_dir), [])

    def test_same_host_unregistered_url_is_rejected(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prepare_run(run_dir, url=f"{base}/items.json")
            with self.assertRaisesRegex(RuntimeError, "精确 URL"):
                download_raw_file(
                    run_dir,
                    url=f"{base}/items.csv",
                    output="raw/items.csv",
                    expected_format="csv",
                    timeout_seconds=10,
                    source_id="items",
                )

    def test_existing_url_is_reused_without_second_file(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            url = f"{base}/items.json"
            prepare_run(run_dir, url=url)
            first = download_raw_file(
                run_dir, url=url, output="raw/items.json",
                expected_format="json", timeout_seconds=10, source_id="items",
            )
            second = download_raw_file(
                run_dir, url=url, output="raw/other-name.json",
                expected_format="json", timeout_seconds=10, source_id="items",
            )
            self.assertEqual(first["status"], "downloaded")
            self.assertEqual(second["status"], "reused")
            self.assertEqual(second["path"], "raw/items.json")
            self.assertFalse((run_dir / "workspace/raw/other-name.json").exists())

    def test_identical_content_from_two_registered_urls_is_deduplicated(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            first_url = f"{base}/items.json"
            second_url = f"{base}/same.json"
            seed, digest = prepare_run(run_dir, url=first_url)
            plan = source_plan_payload(seed, digest, url=first_url)
            plan["sources"][0]["registered_urls"].append(second_url)
            save_source_plan_payload(run_dir, plan)
            download_raw_file(
                run_dir, url=first_url, output="raw/items.json",
                expected_format="json", timeout_seconds=10, source_id="items",
            )
            second = download_raw_file(
                run_dir, url=second_url, output="raw/same.json",
                expected_format="json", timeout_seconds=10, source_id="items",
            )
            self.assertEqual(second["status"], "reused")
            self.assertEqual(second["path"], "raw/items.json")

    def test_batch_downloads_multiple_registered_urls(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            json_url = f"{base}/items.json"
            csv_url = f"{base}/items.csv"
            seed, digest = prepare_run(run_dir, url=json_url)
            plan = source_plan_payload(seed, digest, url=json_url)
            plan["sources"][0]["registered_urls"].append(csv_url)
            save_source_plan_payload(run_dir, plan)
            result = download_raw_batch(
                run_dir,
                items=[
                    {"url": json_url, "output": "raw/items.json", "format": "json", "source_id": "items"},
                    {"url": csv_url, "output": "raw/items.csv", "format": "csv", "source_id": "items"},
                ],
                max_workers=2,
            )
            self.assertEqual(result["succeeded"], 2)

    def test_not_found_is_terminal_for_exact_url(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            url = f"{base}/missing.json"
            prepare_run(run_dir, url=url)
            with self.assertRaises(DownloadFailure) as first:
                download_raw_file(
                    run_dir, url=url, output="raw/missing.json",
                    expected_format="json", timeout_seconds=10, source_id="items",
                )
            self.assertEqual(first.exception.code, "not_found")
            with self.assertRaises(DownloadFailure) as second:
                download_raw_file(
                    run_dir, url=url, output="raw/missing-again.json",
                    expected_format="json", timeout_seconds=10, source_id="items",
                )
            self.assertEqual(second.exception.code, "not_found")
            self.assertIn("未再次访问网络", str(second.exception))

    def test_modified_downloaded_raw_is_detected(self) -> None:
        with server() as base, tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            url = f"{base}/items.json"
            prepare_run(run_dir, url=url)
            download_raw_file(
                run_dir, url=url, output="raw/items.json",
                expected_format="json", timeout_seconds=10, source_id="items",
            )
            (run_dir / "workspace/raw/items.json").write_text("{}", encoding="utf-8")
            codes = {item["code"] for item in download_receipt_issues(run_dir)}
            self.assertIn("downloaded_raw_modified", codes)


if __name__ == "__main__":
    unittest.main()
