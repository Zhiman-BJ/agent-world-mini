"""Rebuild the three MCP environments from traceable public source data.

This script intentionally does not reuse the old synthetic expansion functions.
It keeps only tool capabilities that are supported by the rebuilt workspaces and
publishes an environment only after data, schema, smoke, negative, and stateful
workflow checks pass.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator, ValidationError

import generate_mcp_complex_environments as legacy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PROJECT_ROOT / "schemas"
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "mcp_quality_3env_20260825"

FINSTAT_SOURCES = {
    "paypal": "https://raw.githubusercontent.com/simonmichael/hledger/master/examples/csv/other/paypal-custom.csv",
    "journal": "https://raw.githubusercontent.com/simonmichael/hledger/master/examples/home-page-example.journal",
    "ofx": "https://raw.githubusercontent.com/libofx/libofx/master/doc/ofx_sample_files/ofx_spec201_stmtrs_example.xml",
}

BUG_SOURCES = {
    "junit_always_fail": "https://raw.githubusercontent.com/jenkinsci/junit-plugin/master/src/test/resources/hudson/tasks/junit/gh-237/TEST-io.olamy.AlwaysFailTest.xml",
    "junit_flaky": "https://raw.githubusercontent.com/jenkinsci/junit-plugin/master/src/test/resources/hudson/tasks/junit/gh-237/TEST-io.olamy.FlakyTest.xml",
    "junit_timestamps": "https://raw.githubusercontent.com/jenkinsci/junit-plugin/master/src/test/resources/hudson/tasks/junit/junit-report-testsuite-various-timestamps.xml",
    "junit_nested": "https://raw.githubusercontent.com/jenkinsci/junit-plugin/master/src/test/resources/hudson/tasks/junit/junit-report-nested-testsuites.xml",
    "junit_error_details": "https://raw.githubusercontent.com/jenkinsci/junit-plugin/master/src/test/resources/hudson/tasks/junit/junit-report-errror-details.xml",
    "sarif_simple": "https://raw.githubusercontent.com/microsoft/sarif-tutorials/main/samples/1-Introduction/simple-example.sarif",
    "sarif_code_flow": "https://raw.githubusercontent.com/microsoft/sarif-tutorials/main/samples/3-Beyond-basics/bad-eval-with-code-flow.sarif",
    "sarif_result_stacks": "https://raw.githubusercontent.com/microsoft/sarif-tutorials/main/samples/ResultStacks.sarif",
    "gitlab_issues": "https://gitlab.com/api/v4/projects/278964/issues?state=all&per_page=12&order_by=updated_at&sort=desc",
}

AMI_ROW_OFFSETS = (0, 2000, 3000)
AMI_ROWS_URL = (
    "https://datasets-server.huggingface.co/rows"
    "?dataset=edinburghcstr%2Fami&config=ihm&split=test&offset={offset}&length=100"
)
WHISPER_AUDIO_URL = "https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, path: Path, *, attempts: int = 4) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json, application/json, */*",
                    "User-Agent": "agent-world-mini-quality-builder/1.0",
                },
            )
            with urlopen(request, timeout=120) as response:
                content = response.read()
            if not content:
                raise RuntimeError(f"empty response from {url}")
            path.write_bytes(content)
            return {
                "url": url,
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        except Exception as error:  # network failures are retried, then surfaced
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _source_record(
    source_id: str,
    download: dict[str, Any],
    workspace: Path,
    resource_id: str,
    source_type: str,
    license_note: str,
) -> dict[str, Any]:
    path = Path(download["path"])
    return {
        "source_id": source_id,
        "url": download["url"],
        "source_type": source_type,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "license_or_access_note": license_note,
        "resource_ids": [resource_id],
        "files": [
            {
                "path": path.relative_to(workspace).as_posix(),
                "sha256": download["sha256"],
                "size": download["size"],
            }
        ],
    }


def _reset_builder_output(root: Path, source_mcp: str) -> None:
    shutil.rmtree(root / "workspace", ignore_errors=True)
    shutil.rmtree(root / "provenance", ignore_errors=True)
    (root / "workspace").mkdir(parents=True)
    _write_json(root / "provenance" / "mcp_seed.json", legacy.seed_record(source_mcp))


def _replace_tool_code(environment: dict[str, Any], replacements: dict[str, str]) -> None:
    for item in environment["tools"]:
        code = item["internal"]["code"]
        for old, new in replacements.items():
            code = code.replace(old, new)
        item["internal"]["code"] = code


def _filter_tools(
    environment: dict[str, Any],
    smoke: dict[str, dict[str, Any]],
    removed: set[str],
) -> None:
    environment["tools"] = [item for item in environment["tools"] if item["name"] not in removed]
    for name in list(smoke):
        if name in removed:
            del smoke[name]


def _entity_schema_finstat() -> dict[str, Any]:
    return {
        "account": {
            "description": "An account used by the normalized double-entry ledger.",
            "fields": {"account_id": "string", "code": "string", "name": "string", "type": "string"},
        },
        "journal_entry": {
            "description": "A balanced entry derived from one published PayPal fixture transaction.",
            "fields": {
                "entry_id": "string",
                "date": "string",
                "description": "string",
                "source_reference": "string",
                "posted_by": "string",
            },
        },
        "bank_transaction": {
            "description": "A normalized row from the published PayPal transaction fixture.",
            "fields": {
                "transaction_id": "string",
                "date": "string",
                "description": "string",
                "amount_minor": "integer",
                "currency": "string",
                "reference": "string",
                "match_status": "string",
            },
        },
        "statement_note": {
            "description": "An audit note attached to a source transaction reference.",
            "fields": {"note_id": "string", "reference": "string", "body": "string", "actor": "string"},
        },
    }


def build_finstat(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    environment, smoke = legacy.finstat_environment(root)
    _reset_builder_output(root, "finstat/finstatai")
    workspace = root / "workspace"

    downloads = {
        "paypal": _download(FINSTAT_SOURCES["paypal"], workspace / "raw" / "hledger" / "paypal-custom.csv"),
        "journal": _download(FINSTAT_SOURCES["journal"], workspace / "raw" / "hledger" / "home-page-example.journal"),
        "ofx": _download(FINSTAT_SOURCES["ofx"], workspace / "raw" / "libofx" / "ofx_spec201_stmtrs_example.xml"),
    }

    with Path(downloads["paypal"]["path"]).open(encoding="utf-8", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    normalized: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    for row in source_rows:
        month, day, year = row["Date"].split("/")
        date = f"{year}-{month}-{day}"
        amount_minor = int(Decimal(row["Net"]) * 100)
        reference = row["Transaction ID"]
        description = row["Name"].strip() or row["Type"].strip()
        normalized.append(
            {
                "date": date,
                "description": description,
                "amount_minor": amount_minor,
                "currency": row["Currency"],
                "reference": reference,
                "source_status": row["Status"],
            }
        )
        documents.append(
            {
                "document_id": "paypal_" + reference.lower(),
                "document_type": row["Type"].strip().lower().replace(" ", "_"),
                "reference": reference,
                "counterparty": description,
                "issue_date": date,
                "total_minor": abs(amount_minor),
                "currency": row["Currency"],
            }
        )

    statement_rows = [{key: value for key, value in row.items() if key != "source_status"} for row in normalized]
    _write_csv(
        workspace / "derived" / "bank" / "paypal_2019_10.csv",
        statement_rows,
        ["date", "description", "amount_minor", "currency", "reference"],
    )
    closing = sum(row["amount_minor"] for row in normalized)
    _write_json(
        workspace / "derived" / "bank" / "statement_control.json",
        {"account_id": "1000", "period": "2019-10", "opening_balance_minor": 0, "closing_balance_minor": closing},
    )
    for document in documents:
        _write_json(workspace / "derived" / "documents" / f'{document["document_id"]}.json', document)

    accounts = [
        {"account_id": "1000", "code": "1000", "name": "PayPal Balance", "type": "asset"},
        {"account_id": "2100", "code": "2100", "name": "Bank Transfer Clearing", "type": "liability"},
        {"account_id": "4000", "code": "4000", "name": "Incoming Payments", "type": "revenue"},
        {"account_id": "5100", "code": "5100", "name": "Subscriptions and Donations", "type": "expense"},
    ]
    entries: list[dict[str, Any]] = []
    bank_transactions: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for index, row in enumerate(normalized, start=1):
        completed = row["source_status"] == "Completed"
        bank_transactions.append(
            {
                "transaction_id": f"paypal_txn_{index:03d}",
                "date": row["date"],
                "description": row["description"],
                "amount_minor": row["amount_minor"],
                "currency": row["currency"],
                "reference": row["reference"],
                "match_status": "matched" if completed else "unmatched",
            }
        )
        if not completed:
            reviews.append(
                {
                    "review_id": f"review_{len(reviews) + 1:03d}",
                    "kind": "pending_source_transaction",
                    "reference": row["reference"],
                    "amount_minor": abs(row["amount_minor"]),
                    "status": "open",
                    "reason": "The published source row has Status=Pending and is excluded from the posted ledger.",
                    "resolution": "",
                    "resolved_by": "",
                }
            )
            continue
        value = abs(row["amount_minor"])
        counter_account = "4000" if row["amount_minor"] > 0 else "5100"
        lines = (
            [
                {"account_id": "1000", "debit_minor": value, "credit_minor": 0},
                {"account_id": counter_account, "debit_minor": 0, "credit_minor": value},
            ]
            if row["amount_minor"] > 0
            else [
                {"account_id": counter_account, "debit_minor": value, "credit_minor": 0},
                {"account_id": "1000", "debit_minor": 0, "credit_minor": value},
            ]
        )
        entries.append(
            {
                "entry_id": f"je_{len(entries) + 1:04d}",
                "date": row["date"],
                "description": row["description"],
                "source_reference": row["reference"],
                "posted_by": "deterministic_paypal_import",
                "lines": lines,
            }
        )

    notes = [
        {
            "note_id": "note_001",
            "reference": reviews[0]["reference"],
            "body": "Source status is Pending; transaction was not posted to the ledger.",
            "actor": "deterministic_import",
        }
    ]
    ledger = {
        "workspace_id": "hledger_paypal_2019_10",
        "currency": "USD",
        "accounts": accounts,
        "journal_entries": entries,
        "bank_transactions": bank_transactions,
        "statement_notes": notes,
    }
    _write_json(workspace / "entities" / "ledger.json", ledger)
    _write_json(workspace / "entities" / "review_queue.json", {"items": reviews})
    _write_json(
        workspace / "entities" / "accounting_controls.json",
        {
            "coas": [
                {
                    "coa_id": "coa_paypal_fixture",
                    "name": "PayPal fixture chart of accounts",
                    "status": "active",
                    "accounting_basis": "cash",
                    "currency": "USD",
                }
            ],
            "posting_control": {"coa_id": "coa_paypal_fixture", "status": "released", "reason": "", "changed_by": "system"},
            "adjustment_schedules": [],
            "workspace_corrections": [],
        },
    )
    _write_json(workspace / "entities" / "document_catalog.json", {"documents": documents})
    _write_json(
        workspace / "derived" / "engagement.json",
        {
            "entity": "Published hledger PayPal fixture",
            "close_period": "2019-10",
            "currency": "USD",
            "objective": "Audit the deterministic import and resolve source rows that remain pending.",
            "materiality_minor": 1,
        },
    )
    _write_json(
        workspace / "derived" / "close_control.json",
        {"period": "2019-10", "materiality_minor": 1, "required_reports": ["trial_balance", "profit_and_loss"], "required_open_review_count": 0},
    )
    digest = hashlib.sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()
    _write_json(
        workspace / "snapshots" / "opening_2019_10.json",
        {
            "snapshot_id": "opening_2019_10",
            "period": "2019-10",
            "ledger_sha256": digest,
            "account_count": len(accounts),
            "journal_entry_count": len(entries),
            "created_by": "deterministic_import",
            "ledger": ledger,
        },
    )
    for directory in ("exports", "reports"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)

    _replace_tool_code(
        environment,
        {
            "raw/bank/operating_2026_07.csv": "derived/bank/paypal_2019_10.csv",
            "raw/bank/statement_control.json": "derived/bank/statement_control.json",
            'root / "raw" / "bank" / "operating_2026_07.csv"': 'root / "derived" / "bank" / "paypal_2019_10.csv"',
            'root / "raw" / "bank" / "statement_control.json"': 'root / "derived" / "bank" / "statement_control.json"',
            'root / "raw" / "documents"': 'root / "derived" / "documents"',
            'root / "raw" / "open_source" / "hledger_sample.journal"': 'root / "raw" / "hledger" / "home-page-example.journal"',
            'root / "raw" / "open_source" / "libofx_statement_sample.ofx"': 'root / "raw" / "libofx" / "ofx_spec201_stmtrs_example.xml"',
            "Northstar Components LLC": "Published hledger PayPal fixture",
            '"period": "2026-07"': '"period": "2019-10"',
        },
    )
    _filter_tools(environment, smoke, {"list_adjustment_schedules", "list_workspace_corrections", "get_workspace_correction"})
    environment.update(
        {
            "environment_id": "finstat_open_fixture_close_001",
            "name": "FinStat open accounting fixture audit workspace",
            "description": "A traceable accounting workspace deterministically derived from published hledger PayPal CSV, hledger journal, and libofx statement fixtures; pending source transactions remain explicit review items.",
        }
    )
    environment["resources"] = [
        legacy.resource("paypal_source", "Published PayPal CSV fixture", "Unmodified PayPal transaction fixture from the hledger repository.", "raw", "file", "raw/hledger/paypal-custom.csv", "csv", False),
        legacy.resource("hledger_source", "Published hledger journal fixture", "Unmodified double-entry journal fixture from the hledger repository.", "raw", "file", "raw/hledger/home-page-example.journal", "journal", False),
        legacy.resource("ofx_source", "Published libofx statement fixture", "Unmodified OFX checking statement fixture from the libofx repository.", "raw", "file", "raw/libofx/ofx_spec201_stmtrs_example.xml", "ofx", False),
        legacy.resource("engagement_context", "Import audit context", "Deterministic local audit scope for the published fixtures.", "derived", "file", "derived/engagement.json", "json", False, source_resources=["paypal_source", "hledger_source", "ofx_source"]),
        legacy.resource("bank_statement", "Normalized PayPal transactions", "Normalized cents and ISO dates derived from the published PayPal CSV.", "derived", "file", "derived/bank/paypal_2019_10.csv", "csv", False, source_resources=["paypal_source"]),
        legacy.resource("statement_control", "Statement control", "Opening balance, source-row total, and resulting closing balance.", "derived", "file", "derived/bank/statement_control.json", "json", False, source_resources=["paypal_source"]),
        legacy.resource("source_documents", "Normalized transaction documents", "One deterministic JSON projection for each published PayPal source row.", "derived", "file_collection", "derived/documents/*.json", "json", False, source_resources=["paypal_source"]),
        legacy.resource("ledger", "Double-entry import ledger", "Balanced entries for completed source rows plus normalized statement transactions and audit notes.", "entity", "file", "entities/ledger.json", "json", True, source_resources=["paypal_source"], entity_schema=_entity_schema_finstat()),
        legacy.resource("review_queue", "Pending transaction review queue", "Published source rows with Status=Pending, excluded from the posted ledger until reviewed.", "entity", "file", "entities/review_queue.json", "json", True, source_resources=["paypal_source", "ledger"], entity_schema={"review_item": {"description": "A source-backed import exception.", "fields": {"review_id": "string", "kind": "string", "reference": "string", "amount_minor": "integer", "status": "string", "reason": "string", "resolution": "string", "resolved_by": "string"}}}),
        legacy.resource("accounting_controls", "Chart and posting control", "Local mutable posting control for the imported fixture ledger.", "entity", "file", "entities/accounting_controls.json", "json", True, source_resources=["ledger"], entity_schema={"coa": {"description": "A chart-of-accounts view.", "fields": {"coa_id": "string", "name": "string", "status": "string", "accounting_basis": "string", "currency": "string"}}}),
        legacy.resource("document_catalog", "Transaction document catalog", "Entity index deterministically projected from the published PayPal CSV.", "entity", "file", "entities/document_catalog.json", "json", False, source_resources=["source_documents"], entity_schema={"source_document": {"description": "A normalized published transaction row.", "fields": {"document_id": "string", "document_type": "string", "reference": "string", "counterparty": "string", "issue_date": "string", "total_minor": "integer", "currency": "string"}}}),
        legacy.resource("close_control", "Import audit controls", "Acceptance thresholds for the deterministic fixture import.", "derived", "file", "derived/close_control.json", "json", False, source_resources=["engagement_context"]),
        legacy.resource("account_exports", "Account exports", "CSV files generated by tools.", "output", "directory", "exports/", "directory", True),
        legacy.resource("ledger_snapshots", "Ledger snapshots", "Immutable snapshots generated by tools.", "output", "directory", "snapshots/", "directory", True),
        legacy.resource("financial_reports", "Financial reports", "Reports generated by tools.", "output", "directory", "reports/", "directory", True),
    ]
    environment["rules"] = [
        {"description": "Every journal entry must balance and every line must reference a declared account.", "resources": ["ledger"]},
        {"description": "Every normalized transaction and document must retain the exact Transaction ID from paypal_source.", "resources": ["paypal_source", "bank_statement", "source_documents", "ledger", "document_catalog"]},
        {"description": "Rows whose published status is Pending must remain unmatched and represented in review_queue until explicitly resolved.", "resources": ["paypal_source", "ledger", "review_queue"]},
    ]
    smoke.update(
        {
            "generate_trial_balance": {"period": "2019-10"},
            "generate_profit_and_loss": {"period": "2019-10"},
            "generate_balance_sheet": {"as_of_date": "2019-10-31"},
            "create_coa_snapshot": {"snapshot_id": "audit_2019_10", "period": "2019-10", "actor": "auditor"},
            "open_workspace": {"workspace_tag": "hledger_paypal_2019_10"},
            "get_coa_snapshot": {"snapshot_id": "opening_2019_10"},
            "list_coa_accounts": {"coa_id": "coa_paypal_fixture"},
            "diagnose_coa": {"coa_id": "coa_paypal_fixture"},
            "hold_coa_posting": {"coa_id": "coa_paypal_fixture", "reason": "Investigate pending imports", "actor": "auditor"},
            "release_coa_posting": {"coa_id": "coa_paypal_fixture", "reason": "Review completed", "actor": "auditor"},
            "get_coa_posting_status": {"coa_id": "coa_paypal_fixture"},
            "export_workspace_documents": {"bundle_name": "paypal_source_evidence"},
        }
    )
    sources = [
        _source_record("hledger_paypal_csv", downloads["paypal"], workspace, "paypal_source", "official_repository", "GPL-3.0; fixture published by the hledger project."),
        _source_record("hledger_home_page_journal", downloads["journal"], workspace, "hledger_source", "official_repository", "GPL-3.0; fixture published by the hledger project."),
        _source_record("libofx_statement_example", downloads["ofx"], workspace, "ofx_source", "official_repository", "GPL-2.0; fixture published by the libofx project."),
    ]
    return environment, smoke, {"sources": sources, "source_rows": len(source_rows), "pending_rows": len(reviews)}


def _safe_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def _parse_junit(path: Path, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    root = ET.parse(path).getroot()
    cases: list[dict[str, Any]] = []
    suites: list[dict[str, Any]] = []
    seen_suites: set[str] = set()
    for case in root.iter("testcase"):
        classname = case.attrib.get("classname", "unclassified")
        name = case.attrib.get("name", "unnamed")
        case_id = _safe_id("TC", f"{classname}::{name}")
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        problem = failure if failure is not None else error
        status = "failed" if problem is not None else "skipped" if skipped is not None else "passed"
        message = ""
        if problem is not None:
            message = (problem.attrib.get("message") or (problem.text or "")).strip()[:2000]
        cases.append(
            {
                "test_case_id": case_id,
                "name": name,
                "component": classname,
                "status": status,
                "error": message,
                "trace_path": path.name if problem is not None else "",
            }
        )
        if classname not in seen_suites:
            seen_suites.add(classname)
            suites.append(
                {
                    "suite_id": _safe_id("SUITE", classname),
                    "name": classname,
                    "purpose": f"JUnit suite imported from {path.name}",
                }
            )
    passed = sum(item["status"] == "passed" for item in cases)
    failed = sum(item["status"] == "failed" for item in cases)
    return (
        {
            "run_id": run_id,
            "suite": path.stem,
            "commit": "published_fixture",
            "started_at": root.attrib.get("timestamp", "not_provided_by_source"),
            "status": "failed" if failed else "passed",
            "summary": {"passed": passed, "failed": failed},
            "tests": cases,
        },
        suites,
        cases,
    )


def build_bugagent(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    environment, smoke = legacy.bugagent_environment(root)
    _reset_builder_output(root, "bugagent/bugagent-mcp")
    workspace = root / "workspace"
    sources: list[dict[str, Any]] = []
    junit_downloads: list[dict[str, Any]] = []
    for source_id in ("junit_always_fail", "junit_flaky", "junit_timestamps", "junit_nested", "junit_error_details"):
        name = source_id.removeprefix("junit_") + ".xml"
        download = _download(BUG_SOURCES[source_id], workspace / "raw" / "junit" / name)
        junit_downloads.append(download)
        sources.append(_source_record(source_id, download, workspace, "junit_reports", "official_repository", "MIT; fixture published by the Jenkins JUnit Plugin project."))
    sarif_downloads: list[dict[str, Any]] = []
    for source_id in ("sarif_simple", "sarif_code_flow", "sarif_result_stacks"):
        name = source_id.removeprefix("sarif_") + ".sarif"
        download = _download(BUG_SOURCES[source_id], workspace / "raw" / "sarif" / name)
        sarif_downloads.append(download)
        sources.append(_source_record(source_id, download, workspace, "sarif_reports", "official_repository", "CC-BY-4.0; sample published by Microsoft SARIF Tutorials."))
    issue_download = _download(BUG_SOURCES["gitlab_issues"], workspace / "raw" / "gitlab" / "gitlab_issues.json")
    sources.append(_source_record("gitlab_issue_snapshot", issue_download, workspace, "public_issues", "official_api", "Public GitLab REST API response; issue text is retained for benchmark research use."))
    issue_payload = json.loads(Path(issue_download["path"]).read_text(encoding="utf-8"))
    issues = [item for item in issue_payload if isinstance(item, dict) and isinstance(item.get("iid"), int)]

    runs: list[dict[str, Any]] = []
    test_cases: dict[str, dict[str, Any]] = {}
    test_suites: dict[str, dict[str, Any]] = {}
    bugs: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for index, download in enumerate(junit_downloads, start=1):
        run_id = f"run_junit_{index:02d}"
        run, suites, cases = _parse_junit(Path(download["path"]), run_id)
        runs.append(run)
        _write_json(workspace / "derived" / "test_runs" / f"{run_id}.json", run)
        for suite in suites:
            test_suites[suite["suite_id"]] = suite
        for case in cases:
            test_cases[case["test_case_id"]] = {
                "test_case_id": case["test_case_id"],
                "name": case["name"],
                "component": case["component"],
            }
            if case["status"] == "failed":
                bug_id = _safe_id("BUG-JUNIT", f'{run_id}:{case["test_case_id"]}')
                bugs.append(
                    {
                        "bug_id": bug_id,
                        "title": f'JUnit failure: {case["name"]}',
                        "severity": "high",
                        "status": "open",
                        "component": case["component"],
                        "source_run_id": run_id,
                        "description": case["error"] or "JUnit fixture reports a failure without a message.",
                        "classification": "functional",
                    }
                )
                links.append({"bug_id": bug_id, "test_case_id": case["test_case_id"], "relation": "reported_by"})

    public_bug_ids: set[str] = set()
    for issue in issues:
        bug_id = f'GL-GITLAB-{issue["iid"]}'
        public_bug_ids.add(bug_id)
        labels = [str(value) for value in issue.get("labels", [])]
        text = (issue.get("title", "") + " " + (issue.get("description") or "")).lower()
        classification = "security" if any(term in text for term in ("security", "unsafe", "vulnerability")) else "functional"
        severity = "high" if any("bug" in label.lower() for label in labels) or classification == "security" else "medium"
        bugs.append(
            {
                "bug_id": bug_id,
                "title": issue.get("title") or f'GitLab issue {issue["iid"]}',
                "severity": severity,
                "status": issue.get("state", "open"),
                "component": labels[0] if labels else "gitlab",
                "source_run_id": "",
                "description": (issue.get("description") or "No description supplied.")[:4000],
                "classification": classification,
            }
        )

    # GitLab can require authentication for notes even when the issue list is
    # public. Keep initial local comments empty instead of inventing content.
    comments: list[dict[str, Any]] = []

    folders: dict[str, dict[str, Any]] = {}
    for item in test_cases.values():
        component = item["component"] or "unclassified"
        folder_id = _safe_id("FOLDER", component)
        folders[folder_id] = {"folder_id": folder_id, "name": component, "parent_id": ""}
    registry = {
        "bugs": bugs,
        "test_cases": sorted(test_cases.values(), key=lambda item: item["test_case_id"]),
        "test_suites": sorted(test_suites.values(), key=lambda item: item["suite_id"]),
        "test_case_folders": sorted(folders.values(), key=lambda item: item["folder_id"]),
        "links": links,
        "review_flags": [],
        "comments": comments,
    }
    _write_json(workspace / "entities" / "quality_registry.json", registry)
    security_target = workspace / "derived" / "security" / "scan.sarif"
    junit_reference = workspace / "derived" / "reference" / "jenkins_junit_error_details.xml"
    sarif_reference = workspace / "derived" / "reference" / "microsoft_sarif_tutorial.sarif"
    for target in (security_target, junit_reference, sarif_reference):
        target.parent.mkdir(parents=True, exist_ok=True)
    # The query tool exposes SARIF's explicit result level, so select the
    # tutorial sample that actually declares that optional field.
    shutil.copy2(sarif_downloads[0]["path"], security_target)
    shutil.copy2(junit_downloads[-1]["path"], junit_reference)
    shutil.copy2(sarif_downloads[0]["path"], sarif_reference)

    _replace_tool_code(
        environment,
        {
            'root / "raw" / "test_runs"': 'root / "derived" / "test_runs"',
            'root / "raw" / "security" / "scan.sarif"': 'root / "derived" / "security" / "scan.sarif"',
            'root / "raw" / "open_source" / "jenkins_junit_error_details.xml"': 'root / "derived" / "reference" / "jenkins_junit_error_details.xml"',
            'root / "raw" / "open_source" / "microsoft_sarif_tutorial.sarif"': 'root / "derived" / "reference" / "microsoft_sarif_tutorial.sarif"',
        },
    )
    removed = {
        "get_performance_results",
        "collect_compliance_evidence",
        "generate_release_readiness_report",
        "analyze_fix_area",
        "check_config_drift",
        "get_security_events",
    }
    _filter_tools(environment, smoke, removed)
    environment.update(
        {
            "environment_id": "bugagent_open_quality_triage_001",
            "name": "bugAgent open-source quality triage workspace",
            "description": "A quality triage workspace built from unmodified Jenkins JUnit fixtures, Microsoft SARIF samples, and a public GitLab issue snapshot; normalized runs and registry entities retain source identifiers.",
        }
    )
    entity_schema = next(item for item in environment["resources"] if item["resource_id"] == "quality_registry")["entity_schema"]
    environment["resources"] = [
        legacy.resource("junit_reports", "Jenkins JUnit fixtures", "Unmodified XML reports published by the Jenkins JUnit Plugin project.", "raw", "file_collection", "raw/junit/*.xml", "xml", False),
        legacy.resource("sarif_reports", "Microsoft SARIF samples", "Unmodified SARIF logs published by Microsoft SARIF Tutorials.", "raw", "file_collection", "raw/sarif/*.sarif", "sarif", False),
        legacy.resource("public_issues", "GitLab issue snapshot", "Unmodified public GitLab REST response containing current GitLab project issues.", "raw", "file", "raw/gitlab/gitlab_issues.json", "json", False),
        legacy.resource("test_runs", "Normalized JUnit runs", "Deterministic JSON projections of the published JUnit XML files.", "derived", "file_collection", "derived/test_runs/*.json", "json", False, source_resources=["junit_reports"]),
        legacy.resource("security_scan", "Selected SARIF scan", "A directly copied SARIF code-flow sample used by query tools.", "derived", "file", "derived/security/scan.sarif", "sarif", False, source_resources=["sarif_reports"]),
        legacy.resource("parser_references", "Parser reference files", "Exact copies selected for the dedicated JUnit and SARIF inspection tools.", "derived", "file_collection", "derived/reference/*", "mixed", False, source_resources=["junit_reports", "sarif_reports"]),
        legacy.resource("quality_registry", "Source-backed quality registry", "Issues, test cases, suites, folders, failure links, review flags, and local audit comments derived from public sources.", "entity", "file", "entities/quality_registry.json", "json", True, source_resources=["junit_reports", "public_issues"], entity_schema=entity_schema),
    ]
    environment["rules"] = [
        {"description": "Every JUnit-derived bug-test link must reference a failure in the same normalized test run.", "resources": ["junit_reports", "test_runs", "quality_registry"]},
        {"description": "Every GL-GITLAB bug must retain the numeric identifier returned by the GitLab API; local comments must reference an existing bug.", "resources": ["public_issues", "quality_registry"]},
        {"description": "SARIF query results must preserve ruleId, message, level, and artifact URI from the selected published log.", "resources": ["sarif_reports", "security_scan"]},
    ]
    first_failure_run = next(item["run_id"] for item in runs if item["summary"]["failed"])
    first_bug = bugs[0]["bug_id"]
    first_test = registry["test_cases"][0]["test_case_id"]
    first_component = registry["test_cases"][0]["component"]
    first_suite = registry["test_suites"][0]["suite_id"]
    first_folder = registry["test_case_folders"][0]["folder_id"]
    smoke.update(
        {
            "get_test_reports_failures": {"run_id": first_failure_run},
            "classify_bug": {"bug_id": first_bug, "classification": "functional", "component": "junit-fixture"},
            "link_test_case_to_bug": {"bug_id": first_bug, "test_case_id": first_test, "relation": "regression_for"},
            "add_comment": {"bug_id": first_bug, "author": "quality-reviewer", "body": "Reviewed against the published source fixture."},
            "get_bug_report": {"bug_id": first_bug},
            "update_bug_report": {"bug_id": first_bug, "status": "investigating"},
            "list_test_cases": {"component": first_component},
            "get_test_case": {"test_case_id": first_test},
            "list_test_case_links": {"bug_id": first_bug},
            "create_test_suite": {"suite_id": "suite_local_review", "name": "Local Review", "purpose": "Local follow-up tests"},
            "create_test_case_folder": {"folder_id": "folder_local_review", "name": "Local Review", "parent_id": first_folder},
            "mark_test_case_review_flags": {"test_case_id": first_test, "flag": "needs_coverage", "reason": "Review the published failure behavior.", "actor": "quality-reviewer"},
        }
    )
    # Ensure retained smoke entries never point at removed synthetic IDs.
    smoke["create_test_case"] = {"test_case_id": "TC-LOCAL-REVIEW", "name": "Local follow-up case", "component": first_component}
    smoke["list_test_suites"] = {}
    smoke["list_test_case_folders"] = {}
    smoke["list_test_case_review_candidates"] = {}
    _ = first_suite
    return environment, smoke, {
        "sources": sources,
        "junit_cases": len(test_cases),
        "junit_failures": len(links),
        "public_issues": len(issues),
        "public_issue_comments": len(comments),
    }


def _replace_json_strings(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, dict):
        return {
            _replace_json_strings(key, replacements): _replace_json_strings(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_json_strings(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
    return value


def build_happyscribe(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    environment, smoke = legacy.happyscribe_environment(root)
    _reset_builder_output(root, "happyscribe/happyscribe")
    workspace = root / "workspace"
    sources: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for offset in AMI_ROW_OFFSETS:
        url = AMI_ROWS_URL.format(offset=offset)
        download = _download(url, workspace / "raw" / "ami" / f"test_rows_{offset}_{offset + 99}.json")
        snapshots.append(download)
        sources.append(_source_record(f"ami_test_rows_{offset}_{offset + 99}", download, workspace, "ami_api_snapshots", "official_dataset", "CC-BY-4.0; AMI rows served by the Hugging Face datasets server."))
    audio = _download(WHISPER_AUDIO_URL, workspace / "raw" / "media" / "whisper_jfk.flac")
    sources.append(_source_record("whisper_jfk_audio", audio, workspace, "open_source_media", "official_repository", "MIT; audio fixture published by the OpenAI Whisper project."))

    transcripts: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    speakers: set[str] = set()
    for project_id, (offset, download) in enumerate(zip(AMI_ROW_OFFSETS, snapshots), start=501):
        payload = json.loads(Path(download["path"]).read_text(encoding="utf-8"))
        rows = [item["row"] for item in payload.get("rows", []) if isinstance(item, dict) and isinstance(item.get("row"), dict)]
        meeting_ids = sorted({str(item.get("meeting_id")) for item in rows})
        if len(meeting_ids) != 1:
            raise ValueError(f"AMI page at offset {offset} does not contain exactly one meeting: {meeting_ids}")
        meeting_id = meeting_ids[0]
        segments: list[dict[str, Any]] = []
        for item in sorted(rows, key=lambda row: float(row["begin_time"])):
            start = int(math.floor(float(item["begin_time"])))
            end = max(start + 1, int(math.ceil(float(item["end_time"]))))
            speaker = str(item["speaker_id"])
            speakers.add(speaker)
            segments.append(
                {
                    "speaker": speaker,
                    "start_seconds": start,
                    "end_seconds": end,
                    "text": str(item["text"]),
                    "evidence_tag": "ami_manual_transcript",
                }
            )
        transcript = {
            "transcription_id": f"ami_{meeting_id.lower()}_{offset}",
            "project_id": project_id,
            "title": f"AMI {meeting_id} transcript excerpt, source rows {offset}-{offset + len(rows) - 1}",
            "recorded_at": "not_provided_by_source",
            "language": "en",
            "folder_path": f"/AMI/test/{meeting_id}",
            "participants": sorted({item["speaker"] for item in segments}),
            "segments": segments,
            "action_items": [],
        }
        transcripts.append(transcript)
        projects.append(
            {
                "project_id": project_id,
                "name": f"AMI meeting {meeting_id}",
                "status": "active",
                "notes": f"Imported {len(rows)} manually transcribed utterances from test split source rows {offset}-{offset + len(rows) - 1}.",
                "updated_by": "deterministic_ami_import",
            }
        )
        _write_json(workspace / "derived" / "transcripts" / f'{transcript["transcription_id"]}.json', transcript)

    people: list[dict[str, Any]] = []
    person_by_speaker: dict[str, int] = {}
    for person_id, speaker in enumerate(sorted(speakers), start=301):
        person_by_speaker[speaker] = person_id
        people.append(
            {
                "person_id": person_id,
                "name": speaker,
                "email": "",
                "organization_id": 201,
                "role": "anonymized AMI corpus speaker",
            }
        )
    transcription_records = [
        {
            "transcription_id": item["transcription_id"],
            "project_id": item["project_id"],
            "title": item["title"],
            "recorded_at": item["recorded_at"],
            "language": item["language"],
            "folder_path": item["folder_path"],
            "duration_seconds": max(segment["end_seconds"] for segment in item["segments"]),
            "deleted": False,
        }
        for item in transcripts
    ]
    appearances = [
        {"person_id": person_by_speaker[speaker], "transcription_id": transcript["transcription_id"]}
        for transcript in transcripts
        for speaker in transcript["participants"]
    ]
    folders = [
        {"folder_id": 401 + index, "path": item["folder_path"], "location": "workspace", "transcription_count": 1}
        for index, item in enumerate(transcripts)
    ]
    catalog = {
        "projects": projects,
        "organizations": [
            {
                "organization_id": 201,
                "name": "AMI Meeting Corpus",
                "domain": "groups.inf.ed.ac.uk/ami",
                "industry": "multimodal meeting research dataset",
            }
        ],
        "people": people,
        "transcriptions": transcription_records,
        "appearances": appearances,
        "read_files": [],
        "folders": folders,
    }
    _write_json(workspace / "entities" / "meeting_catalog.json", catalog)
    word_counts = Counter(
        word.strip(".,!?;:'\"").lower()
        for transcript in transcripts
        for segment in transcript["segments"]
        for word in segment["text"].split()
        if len(word.strip(".,!?;:'\"")) >= 6
    )
    terms = [
        {"term": term, "preferred": term.upper(), "context": f"Appears {count} times in the imported AMI transcript excerpts."}
        for term, count in word_counts.most_common(12)
    ]
    _write_json(workspace / "derived" / "glossary.json", {"terms": terms})
    for directory in ("exports", "briefs"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)

    removed = {
        "extract_action_items",
        "list_calendar_events",
        "get_meeting_diagnostics",
        "search_helpdesk",
        "list_conversations",
        "get_conversation",
        "list_read_files",
    }
    _filter_tools(environment, smoke, removed)
    replacements = [
        ("generate_customer_brief", "generate_organization_brief"),
        ("get_company", "get_organization"),
        ("list_companies", "list_organizations"),
        ("company_count", "organization_count"),
        ("company_id", "organization_id"),
        ("companies", "organizations"),
        ("Company", "Organization"),
        ("company", "organization"),
        ("Customer Brief", "Organization Brief"),
        ("customer", "organization"),
        ('root / "raw" / "transcripts"', 'root / "derived" / "transcripts"'),
        ('root / "raw" / "open_source"', 'root / "raw" / "media"'),
        ("northstar_revenue_research", "ami_meeting_research"),
        ("Northstar Revenue Research", "AMI Meeting Research"),
    ]
    environment["tools"] = _replace_json_strings(environment["tools"], replacements)
    renamed_smoke: dict[str, dict[str, Any]] = {}
    for name, arguments in smoke.items():
        new_name = str(_replace_json_strings(name, replacements))
        renamed_smoke[new_name] = _replace_json_strings(arguments, replacements)
    smoke = renamed_smoke
    environment.update(
        {
            "environment_id": "happyscribe_ami_meeting_research_001",
            "name": "HappyScribe AMI meeting research workspace",
            "description": "A meeting research workspace built from 300 manually transcribed AMI utterances with real speaker labels and timestamps, plus an OpenAI Whisper audio fixture; no customer identities, meeting dates, or action items are invented.",
        }
    )
    meeting_schema = {
        "project": {"description": "A locally organized AMI meeting excerpt.", "fields": {"project_id": "integer", "name": "string", "status": "string", "notes": "string", "updated_by": "string"}},
        "organization": {"description": "The organization publishing the meeting corpus.", "fields": {"organization_id": "integer", "name": "string", "domain": "string", "industry": "string"}},
        "person": {"description": "An anonymized speaker label from AMI.", "fields": {"person_id": "integer", "name": "string", "email": "string", "organization_id": "integer", "role": "string"}},
        "transcription": {"description": "A source-backed AMI transcript excerpt.", "fields": {"transcription_id": "string", "project_id": "integer", "title": "string", "recorded_at": "string", "language": "string", "folder_path": "string", "duration_seconds": "integer", "deleted": "boolean"}},
        "appearance": {"description": "A speaker-to-transcript relation derived from source rows.", "fields": {"person_id": "integer", "transcription_id": "string"}},
        "read_file": {"description": "A local record of a transcript read operation.", "fields": {"transcription_id": "string", "last_read_at": "string", "read_by": "string"}},
        "folder": {"description": "A local folder organizing imported excerpts.", "fields": {"folder_id": "integer", "path": "string", "location": "string", "transcription_count": "integer"}},
    }
    environment["resources"] = [
        legacy.resource("ami_api_snapshots", "AMI transcript row snapshots", "Unmodified JSON pages from the Hugging Face datasets server for the CC-BY-4.0 AMI dataset.", "raw", "file_collection", "raw/ami/*.json", "json", False),
        legacy.resource("open_source_media", "Whisper speech fixture", "Unmodified JFK FLAC fixture from the OpenAI Whisper repository.", "raw", "file", "raw/media/whisper_jfk.flac", "flac", False),
        legacy.resource("transcript_files", "Normalized AMI transcript excerpts", "Deterministic grouping of AMI rows by meeting with source speaker IDs and timestamps.", "derived", "file_collection", "derived/transcripts/*.json", "json", False, source_resources=["ami_api_snapshots"]),
        legacy.resource("meeting_catalog", "AMI meeting catalog", "Projects, source organization, anonymized speakers, transcript metadata, appearances, read state, and folders.", "entity", "file", "entities/meeting_catalog.json", "json", True, source_resources=["transcript_files"], entity_schema=meeting_schema),
        legacy.resource("domain_glossary", "Corpus term frequency glossary", "Frequent long tokens computed from the imported transcript excerpts.", "derived", "file", "derived/glossary.json", "json", False, source_resources=["transcript_files"]),
        legacy.resource("subtitle_exports", "Subtitle exports", "WebVTT files generated from source timestamps.", "output", "directory", "exports/", "directory", True),
        legacy.resource("organization_briefs", "Meeting organization briefs", "Evidence-linked Markdown briefs generated from imported excerpts.", "output", "directory", "briefs/", "directory", True),
    ]
    environment["rules"] = [
        {"description": "Every transcript segment must preserve text, speaker_id, begin_time, and end_time from one AMI source row, modulo deterministic integer timestamp rounding.", "resources": ["ami_api_snapshots", "transcript_files"]},
        {"description": "Every appearance must reference an imported speaker label and transcript; missing source dates and emails remain explicit empty or not_provided values.", "resources": ["transcript_files", "meeting_catalog"]},
        {"description": "Generated briefs and VTT files may quote only text present in transcript_files.", "resources": ["transcript_files", "subtitle_exports", "organization_briefs"]},
    ]
    first_transcript = transcripts[0]
    longest_segment = max(first_transcript["segments"], key=lambda item: len(item["text"]))
    quote = longest_segment["text"]
    search_terms = " ".join(quote.lower().split()[:2])
    first_person = people[0]["person_id"]
    first_folder = folders[0]["folder_id"]
    first_project = projects[0]["project_id"]
    smoke.update(
        {
            "list_transcriptions": {"project_id": first_project},
            "search_transcriptions": {"query": search_terms},
            "get_transcription": {"transcription_id": first_transcript["transcription_id"]},
            "verify_quotes": {"transcription_id": first_transcript["transcription_id"], "quotes": [quote]},
            "list_people": {"organization_id": 201},
            "get_organization": {"organization_id": 201},
            "get_folder_hierarchy": {"root_path": "/AMI"},
            "update_project_notes": {"project_id": first_project, "notes": "Source excerpt reviewed against the AMI datasets-server snapshot.", "actor": "corpus-reviewer"},
            "export_transcription_vtt": {"transcription_id": first_transcript["transcription_id"], "file_name": "ami_excerpt"},
            "generate_organization_brief": {"organization_id": 201, "brief_name": "ami_corpus_brief"},
            "get_project": {"project_id": first_project},
            "list_organizations": {"search": "AMI"},
            "get_person": {"person_id": first_person},
            "rename_transcription": {"transcription_id": first_transcript["transcription_id"], "name": "Reviewed AMI excerpt"},
            "create_folder": {"name": "Reviewed", "parent_path": "/AMI/test"},
            "rename_folder": {"folder_id": first_folder, "name": "Reviewed-source"},
            "move_transcriptions": {"transcription_ids": [first_transcript["transcription_id"]], "folder_id": first_folder},
            "delete_transcriptions": {"transcription_ids": [first_transcript["transcription_id"]]},
        }
    )
    return environment, smoke, {
        "sources": sources,
        "transcript_excerpts": len(transcripts),
        "utterances": sum(len(item["segments"]) for item in transcripts),
        "speakers": len(speakers),
    }


def _tool_runner(tool: dict[str, Any], workspace: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(tool["inputSchema"]).validate(arguments)
    namespace: dict[str, Any] = {}
    exec(compile(tool["internal"]["code"], f'<tool:{tool["name"]}>', "exec"), namespace)
    result = namespace["run"](arguments, SimpleNamespace(workspace_root=workspace))
    Draft202012Validator(tool["outputSchema"]).validate(result)
    return result


def _negative_calls(slug: str, environment: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    by_name = {item["name"]: item for item in environment["tools"]}
    calls: dict[str, list[tuple[str, dict[str, Any]]]] = {
        "finstat": [
            ("resolve_review_item", {"review_id": "missing", "resolution": "not applicable", "actor": "auditor"}),
            ("reconcile_workspace_account", {"account_id": "missing"}),
            ("get_coa_snapshot", {"snapshot_id": "missing"}),
        ],
        "bugagent": [
            ("get_test_reports_failures", {"run_id": "missing"}),
            ("get_bug_report", {"bug_id": "missing"}),
            ("get_test_case", {"test_case_id": "missing"}),
        ],
        "happyscribe": [
            ("get_transcription", {"transcription_id": "missing"}),
            ("get_organization", {"organization_id": 999999}),
            ("get_person", {"person_id": 999999}),
        ],
    }
    reports: list[dict[str, Any]] = []
    for name, arguments in calls[slug]:
        result = _tool_runner(by_name[name], workspace, arguments)
        if result.get("success") is not False:
            raise ValueError(f"negative call unexpectedly succeeded: {slug}.{name}")
        reports.append({"tool": name, "arguments": arguments, "error": result["error"]})
    for tool in environment["tools"]:
        try:
            Draft202012Validator(tool["inputSchema"]).validate({"__unexpected__": True})
        except ValidationError:
            continue
        raise ValueError(f"input schema accepts undeclared arguments: {slug}.{tool['name']}")
    return reports


def _stateful_workflow(slug: str, environment: dict[str, Any], workspace: Path) -> dict[str, Any]:
    by_name = {item["name"]: item for item in environment["tools"]}
    if slug == "finstat":
        before = _tool_runner(by_name["list_review_items"], workspace, {"status": "open"})["data"]["count"]
        review_id = json.loads((workspace / "entities" / "review_queue.json").read_text())["items"][0]["review_id"]
        _tool_runner(by_name["resolve_review_item"], workspace, {"review_id": review_id, "resolution": "Confirmed pending in published source", "actor": "auditor"})
        after = _tool_runner(by_name["list_review_items"], workspace, {"status": "open"})["data"]["count"]
        if after != before - 1:
            raise ValueError("FinStat review mutation did not persist")
        return {"name": "resolve_then_list", "before": before, "after": after}
    if slug == "bugagent":
        created = _tool_runner(by_name["create_bug_report"], workspace, {"title": "Local validation finding", "severity": "medium", "component": "validator", "source_run_id": "run_junit_01", "description": "Created by the stateful contract test."})
        bug_id = created["data"]["bug_id"]
        _tool_runner(by_name["add_comment"], workspace, {"bug_id": bug_id, "author": "validator", "body": "Stateful comment persistence check."})
        detail = _tool_runner(by_name["get_bug_report"], workspace, {"bug_id": bug_id})["data"]
        if not detail["comments"]:
            raise ValueError("bugAgent comment mutation did not persist")
        return {"name": "create_comment_get", "bug_id": bug_id, "comment_count": len(detail["comments"])}
    first = json.loads((workspace / "entities" / "meeting_catalog.json").read_text())["transcriptions"][0]
    _tool_runner(by_name["rename_transcription"], workspace, {"transcription_id": first["transcription_id"], "name": "Statefully reviewed AMI excerpt"})
    listed = _tool_runner(by_name["list_transcriptions"], workspace, {"project_id": first["project_id"]})["data"]["items"]
    renamed = next(item for item in listed if item["transcription_id"] == first["transcription_id"])
    if renamed["title"] != "Statefully reviewed AMI excerpt":
        raise ValueError("HappyScribe rename mutation did not persist")
    return {"name": "rename_then_list", "transcription_id": first["transcription_id"], "title": renamed["title"]}


def _walk_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        records.append(value)
        for item in value.values():
            records.extend(_walk_records(item))
    elif isinstance(value, list):
        for item in value:
            records.extend(_walk_records(item))
    return records


def _data_audit(slug: str, environment: dict[str, Any], root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    workspace = root / "workspace"
    source_payload = {"schema_version": "1.0", "sources": metadata.pop("sources")}
    _write_json(root / "provenance" / "sources.json", source_payload)
    _write_json(
        root / "provenance" / "derivations.json",
        {
            "schema_version": "1.0",
            "method": "deterministic Python parsing; no model-generated business records",
            "script": "scripts/rebuild_mcp_test3_quality.py",
            "environment": slug,
            "metrics": metadata,
        },
    )
    for source in source_payload["sources"]:
        for file_item in source["files"]:
            path = workspace / file_item["path"]
            if not path.is_file() or _sha256(path) != file_item["sha256"]:
                raise ValueError(f"source hash validation failed: {file_item['path']}")
    suspicious: list[str] = []
    duplicate_records = 0
    for resource in environment["resources"]:
        if resource["data_type"] != "entity" or resource["storage_type"] != "file":
            continue
        payload = json.loads((workspace / resource["path"]).read_text(encoding="utf-8"))
        for collection_name, rows in payload.items():
            if not isinstance(rows, list):
                continue
            serialized = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows]
            duplicate_records += len(serialized) - len(set(serialized))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key, value in row.items():
                    if key.endswith("_id") and isinstance(value, str) and any(token in value.lower() for token in ("auto_", "batch_", "history_", "historical_")):
                        suspicious.append(f"{resource['resource_id']}.{collection_name}.{key}={value}")
    if suspicious:
        raise ValueError(f"template-like entity IDs remain: {suspicious[:5]}")
    if duplicate_records:
        raise ValueError(f"duplicate entity records remain: {duplicate_records}")

    if slug == "finstat":
        ledger = json.loads((workspace / "entities" / "ledger.json").read_text())
        account_ids = {item["account_id"] for item in ledger["accounts"]}
        for entry in ledger["journal_entries"]:
            if sum(line["debit_minor"] for line in entry["lines"]) != sum(line["credit_minor"] for line in entry["lines"]):
                raise ValueError(f"unbalanced entry: {entry['entry_id']}")
            if any(line["account_id"] not in account_ids for line in entry["lines"]):
                raise ValueError(f"unknown account in entry: {entry['entry_id']}")
    elif slug == "bugagent":
        registry = json.loads((workspace / "entities" / "quality_registry.json").read_text())
        bug_ids = {item["bug_id"] for item in registry["bugs"]}
        case_ids = {item["test_case_id"] for item in registry["test_cases"]}
        if any(item["bug_id"] not in bug_ids or item["test_case_id"] not in case_ids for item in registry["links"]):
            raise ValueError("broken bug-test relation")
        if any(item["bug_id"] not in bug_ids for item in registry["comments"]):
            raise ValueError("broken issue-comment relation")
    else:
        catalog = json.loads((workspace / "entities" / "meeting_catalog.json").read_text())
        person_ids = {item["person_id"] for item in catalog["people"]}
        transcript_ids = {item["transcription_id"] for item in catalog["transcriptions"]}
        if any(item["person_id"] not in person_ids or item["transcription_id"] not in transcript_ids for item in catalog["appearances"]):
            raise ValueError("broken speaker-transcript relation")
    return {
        "status": "passed",
        "source_count": len(source_payload["sources"]),
        "source_file_count": sum(len(item["files"]) for item in source_payload["sources"]),
        "source_bytes": sum(file_item["size"] for item in source_payload["sources"] for file_item in item["files"]),
        "duplicate_entity_records": duplicate_records,
        "template_like_entity_ids": len(suspicious),
        **metadata,
    }


def _validate_and_report(
    slug: str,
    source_mcp: str,
    environment: dict[str, Any],
    smoke: dict[str, dict[str, Any]],
    root: Path,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    validator = legacy.schema_validator()
    validator.validate(environment)
    legacy.validate_resources(environment, root)
    data_report = _data_audit(slug, environment, root, metadata)
    smoke_reports = legacy.validate_tools(environment, root, smoke)
    with tempfile.TemporaryDirectory(prefix=f"negative-{slug}-") as temporary:
        copied = Path(temporary) / "workspace"
        shutil.copytree(root / "workspace", copied)
        negative_reports = _negative_calls(slug, environment, copied)
    with tempfile.TemporaryDirectory(prefix=f"stateful-{slug}-") as temporary:
        copied = Path(temporary) / "workspace"
        shutil.copytree(root / "workspace", copied)
        stateful = _stateful_workflow(slug, environment, copied)
    tool_report = {
        "status": "passed",
        "tool_count": len(environment["tools"]),
        "schema_and_smoke_passed": len(smoke_reports),
        "negative_runtime_cases_passed": len(negative_reports),
        "unexpected_argument_rejections_passed": len(environment["tools"]),
        "stateful_workflow": stateful,
    }
    report = {
        "schema_version": "1.0",
        "status": "passed",
        "source_mcp": source_mcp,
        "schema_valid": True,
        "resource_valid": True,
        "data_quality": data_report,
        "tool_quality": tool_report,
        "tool_smoke_tests": smoke_reports,
        "tool_negative_tests": negative_reports,
    }
    _write_json(root / "environment.json", environment)
    _write_json(root / "validation.json", report)
    _write_json(root / "quality_report.json", {key: value for key, value in report.items() if not key.startswith("tool_") or key == "tool_quality"})
    return report


def main() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True)

    builders = (
        ("finstat", "finstat/finstatai", build_finstat),
        ("bugagent", "bugagent/bugagent-mcp", build_bugagent),
        ("happyscribe", "happyscribe/happyscribe", build_happyscribe),
    )
    index: list[dict[str, Any]] = []
    for slug, source_mcp, builder in builders:
        print(f"[quality] rebuilding {slug}", flush=True)
        root = OUTPUT_ROOT / slug
        environment, smoke, metadata = builder(root)
        report = _validate_and_report(slug, source_mcp, environment, smoke, root, metadata)
        index.append(
            {
                "environment_id": environment["environment_id"],
                "name": environment["name"],
                "source_mcp": source_mcp,
                "path": f"{slug}/environment.json",
                "resources": len(environment["resources"]),
                "tools": len(environment["tools"]),
                "quality_status": report["status"],
            }
        )
        print(f"[quality] {slug}: {len(environment['tools'])} tools passed", flush=True)
    _write_json(
        OUTPUT_ROOT / "index.json",
        {
            "schema_version": "1.0",
            "purpose": "source_grounded_quality_environments",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "environments": index,
        },
    )
    print(f"[quality] generated {len(index)} environments at {OUTPUT_ROOT}", flush=True)


if __name__ == "__main__":
    main()
