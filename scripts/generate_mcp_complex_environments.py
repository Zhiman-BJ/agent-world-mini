"""从复杂 MCP 种子构建三套可执行、可验证的文件型业务环境。"""

from __future__ import annotations

import csv
import copy
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from typing import Any
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "seed_gen" / "data" / "prepared_environments.json"
SCHEMA_DIR = PROJECT_ROOT / "schemas"
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "mcp_complex_3env_20260824"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def download_source(url: str, path: Path) -> dict[str, Any]:
    """下载开源原始样本并返回可审计的来源和文件摘要。"""

    request = Request(url, headers={"User-Agent": "agent-world-mini-environment-builder/1.0"})
    with urlopen(request, timeout=60) as response:
        content = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "file": path.name,
        "url": url,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def closed_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def array_of(item_schema: dict[str, Any], *, min_items: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if min_items is not None:
        schema["minItems"] = min_items
    return schema


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
NUMBER = {"type": "number"}
BOOLEAN = {"type": "boolean"}

ERROR_SCHEMA = closed_object(
    {
        "code": STRING,
        "path": STRING,
        "message": {"type": "string", "minLength": 1},
        "retryable": BOOLEAN,
    },
    ["code", "path", "message", "retryable"],
)


def normalize_entity_schema(entity_schema: dict[str, Any]) -> dict[str, Any]:
    """把脚本内便于书写的 fields 简写展开为正式实体记录 Schema。"""

    normalized: dict[str, Any] = {}
    for entity_name, definition in entity_schema.items():
        if not isinstance(definition, dict):
            continue
        fields = definition.get("fields")
        if isinstance(fields, dict):
            properties = {
                field: {
                    "type": field_type,
                    "description": f"{entity_name} 实体记录中的 {field} 业务字段。",
                }
                for field, field_type in fields.items()
            }
            normalized[entity_name] = {
                "description": definition.get("description", f"{entity_name} 实体记录。"),
                "fields": properties,
            }
        else:
            normalized[entity_name] = definition
    return normalized


def error_codes_from_code(code: str) -> list[str]:
    """从工具实现中提取实际返回的稳定业务错误码。"""

    values = re.findall(r"(?:_fail\(\s*['\"]|['\"]code['\"]\s*:\s*['\"])([a-z][a-z0-9_]*)", code)
    return sorted(set(values)) or ["internal_error"]


def result_schema(data_schema: dict[str, Any]) -> dict[str, Any]:
    error_schema = copy.deepcopy(ERROR_SCHEMA)
    return {
        "oneOf": [
            closed_object(
                {"success": {"type": "boolean", "const": True}, "data": data_schema},
                ["success", "data"],
            ),
            closed_object(
                {"success": {"type": "boolean", "const": False}, "error": error_schema},
                ["success", "error"],
            ),
        ]
    }


def tool(
    name: str,
    description: str,
    input_properties: dict[str, Any],
    required_inputs: list[str],
    data_schema: dict[str, Any],
    runtime_code: str,
) -> dict[str, Any]:
    code = dedent(runtime_code).strip() + f'\n\ndef run(arguments, context):\n    return _dispatch("{name}", arguments, context)\n'
    output_schema = result_schema(data_schema)
    output_schema["oneOf"][1]["properties"]["error"]["properties"]["code"]["enum"] = error_codes_from_code(code)
    input_properties = copy.deepcopy(input_properties)
    for key, value in input_properties.items():
        if isinstance(value, dict) and "description" not in value:
            value["description"] = f"工具参数 {key}。"
    return {
        "name": name,
        "description": description,
        "inputSchema": closed_object(input_properties, required_inputs),
        "outputSchema": output_schema,
        "internal": {"code": code},
    }


def seed_record(qualified_name: str) -> dict[str, Any]:
    entries = read_json(CATALOG_PATH)["environments"]
    item = next(entry for entry in entries if entry.get("qualifiedName") == qualified_name)
    return {
        "qualifiedName": item["qualifiedName"],
        "displayName": item.get("displayName"),
        "description": item.get("description"),
        "homepage": item.get("homepage"),
        "repository": item.get("repository"),
        "tools": [
            {
                "name": value.get("name"),
                "description": value.get("description"),
                "inputSchema": value.get("inputSchema"),
            }
            for value in item.get("tools", [])
        ],
        "dataDirections": item.get("dataDirections", []),
    }


def resource(
    resource_id: str,
    name: str,
    description: str,
    data_type: str,
    storage_type: str,
    path: str,
    format_name: str,
    writable: bool,
    *,
    source_resources: list[str] | None = None,
    entity_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "resource_id": resource_id,
        "name": name,
        "description": description,
        "data_type": data_type,
        "storage_type": storage_type,
        "path": path,
        "format": format_name,
        "writable": writable,
    }
    if source_resources is not None:
        value["source_resources"] = source_resources
    if entity_schema is not None:
        value["entity_schema"] = normalize_entity_schema(entity_schema)
    return value


# FinStat 环境中的每个工具使用同一套确定性账务内核，但只暴露各自的入口。
FINSTAT_RUNTIME = r'''
def _load(path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))

def _save(path, value):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _ok(data):
    return {"success": True, "data": data}

def _fail(code, path, message, retryable=False):
    return {"success": False, "error": {"code": code, "path": path, "message": message, "retryable": retryable}}

def _ledger(root):
    return _load(root / "entities" / "ledger.json")

def _account_map(ledger):
    return {item["account_id"]: item for item in ledger["accounts"]}

def _dispatch(operation, arguments, context):
    import csv
    import hashlib
    import json
    from decimal import Decimal

    root = context.workspace_root
    if operation == "list_source_documents":
        items = []
        statement = root / "raw" / "bank" / "operating_2026_07.csv"
        items.append({"path": statement.relative_to(root).as_posix(), "document_type": "bank_statement", "size": statement.stat().st_size})
        for path in sorted((root / "raw" / "documents").glob("*.json")):
            payload = _load(path)
            items.append({"path": path.relative_to(root).as_posix(), "document_type": payload["document_type"], "size": path.stat().st_size})
        return _ok({"items": items, "count": len(items)})

    if operation == "inspect_bank_statement":
        control = _load(root / "raw" / "bank" / "statement_control.json")
        with (root / "raw" / "bank" / "operating_2026_07.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        transaction_total = sum(Decimal(item["amount_minor"]) for item in rows)
        computed = Decimal(control["opening_balance_minor"]) + transaction_total
        return _ok({
            "account_id": control["account_id"],
            "period": control["period"],
            "opening_balance_minor": control["opening_balance_minor"],
            "closing_balance_minor": control["closing_balance_minor"],
            "transaction_total_minor": int(transaction_total),
            "computed_closing_balance_minor": int(computed),
            "control_matches": int(computed) == control["closing_balance_minor"],
            "transaction_count": len(rows),
        })

    if operation == "list_review_items":
        queue = _load(root / "entities" / "review_queue.json")
        status = arguments.get("status")
        items = [item for item in queue["items"] if status is None or item["status"] == status]
        return _ok({"items": items, "count": len(items)})

    if operation == "resolve_review_item":
        path = root / "entities" / "review_queue.json"
        queue = _load(path)
        item = next((value for value in queue["items"] if value["review_id"] == arguments["review_id"]), None)
        if item is None:
            return _fail("not_found", "$.review_id", "Review item not found.")
        if item["status"] == "resolved":
            return _fail("invalid_state", "$.review_id", "Review item is already resolved.")
        item["status"] = "resolved"
        item["resolution"] = arguments["resolution"]
        item["resolved_by"] = arguments["actor"]
        _save(path, queue)
        return _ok({"review_id": item["review_id"], "status": item["status"], "resolution": item["resolution"]})

    if operation == "list_workspace_account_transactions":
        ledger = _ledger(root)
        account_id = arguments["account_id"]
        if account_id not in _account_map(ledger):
            return _fail("not_found", "$.account_id", "Account not found.")
        items = []
        for entry in ledger["journal_entries"]:
            for line in entry["lines"]:
                if line["account_id"] == account_id:
                    items.append({
                        "entry_id": entry["entry_id"], "date": entry["date"], "description": entry["description"],
                        "debit_minor": line["debit_minor"], "credit_minor": line["credit_minor"], "source_reference": entry["source_reference"],
                    })
        return _ok({"account_id": account_id, "items": items, "count": len(items)})

    if operation == "reconcile_workspace_account":
        ledger = _ledger(root)
        control = _load(root / "raw" / "bank" / "statement_control.json")
        account_id = arguments["account_id"]
        if account_id != control["account_id"]:
            return _fail("not_found", "$.account_id", "No statement control exists for this account.")
        book_balance = 0
        matched_references = []
        for entry in ledger["journal_entries"]:
            for line in entry["lines"]:
                if line["account_id"] == account_id:
                    book_balance += line["debit_minor"] - line["credit_minor"]
                    matched_references.append(entry["source_reference"])
        difference = control["closing_balance_minor"] - book_balance
        return _ok({
            "account_id": account_id, "statement_balance_minor": control["closing_balance_minor"],
            "book_balance_minor": book_balance, "difference_minor": difference,
            "status": "reconciled" if difference == 0 else "needs_adjustment",
            "matched_references": matched_references,
        })

    if operation == "create_standard_journal_entry":
        path = root / "entities" / "ledger.json"
        ledger = _load(path)
        accounts = _account_map(ledger)
        lines = arguments["lines"]
        unknown = [line["account_id"] for line in lines if line["account_id"] not in accounts]
        if unknown:
            return _fail("not_found", "$.lines", "Unknown account: " + unknown[0])
        debit = sum(line["debit_minor"] for line in lines)
        credit = sum(line["credit_minor"] for line in lines)
        if debit <= 0 or debit != credit:
            return _fail("unbalanced_entry", "$.lines", "Journal entry debits and credits must be equal and positive.")
        entry_id = "je_" + str(len(ledger["journal_entries"]) + 1).zfill(4)
        entry = {
            "entry_id": entry_id, "date": arguments["date"], "description": arguments["description"],
            "source_reference": arguments["source_reference"], "posted_by": arguments["actor"], "lines": lines,
        }
        ledger["journal_entries"].append(entry)
        _save(path, ledger)
        return _ok({"entry_id": entry_id, "debit_total_minor": debit, "credit_total_minor": credit, "status": "posted"})

    if operation == "generate_trial_balance":
        ledger = _ledger(root)
        debit_by_account = {item["account_id"]: 0 for item in ledger["accounts"]}
        credit_by_account = {item["account_id"]: 0 for item in ledger["accounts"]}
        for entry in ledger["journal_entries"]:
            for line in entry["lines"]:
                debit_by_account[line["account_id"]] += line["debit_minor"]
                credit_by_account[line["account_id"]] += line["credit_minor"]
        items = []
        for account in ledger["accounts"]:
            account_id = account["account_id"]
            items.append({
                "account_id": account_id, "account_name": account["name"],
                "debit_minor": debit_by_account[account_id], "credit_minor": credit_by_account[account_id],
            })
        total_debit = sum(debit_by_account.values())
        total_credit = sum(credit_by_account.values())
        return _ok({"period": arguments["period"], "items": items, "total_debit_minor": total_debit, "total_credit_minor": total_credit, "balanced": total_debit == total_credit})

    if operation == "generate_profit_and_loss":
        ledger = _ledger(root)
        accounts = _account_map(ledger)
        revenue = 0
        expenses = 0
        lines = []
        totals = {account_id: {"debit": 0, "credit": 0} for account_id in accounts}
        for entry in ledger["journal_entries"]:
            if not entry["date"].startswith(arguments["period"]):
                continue
            for line in entry["lines"]:
                totals[line["account_id"]]["debit"] += line["debit_minor"]
                totals[line["account_id"]]["credit"] += line["credit_minor"]
        for account_id, values in totals.items():
            kind = accounts[account_id]["type"]
            if kind == "revenue":
                amount = values["credit"] - values["debit"]
                revenue += amount
                lines.append({"account_id": account_id, "account_name": accounts[account_id]["name"], "category": "revenue", "amount_minor": amount})
            elif kind == "expense":
                amount = values["debit"] - values["credit"]
                expenses += amount
                lines.append({"account_id": account_id, "account_name": accounts[account_id]["name"], "category": "expense", "amount_minor": amount})
        return _ok({"period": arguments["period"], "revenue_minor": revenue, "expense_minor": expenses, "net_income_minor": revenue - expenses, "lines": lines})

    if operation == "export_workspace_account_csv":
        ledger = _ledger(root)
        account_id = arguments["account_id"]
        if account_id not in _account_map(ledger):
            return _fail("not_found", "$.account_id", "Account not found.")
        relative = "exports/" + arguments["file_name"] + ".csv"
        output = root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for entry in ledger["journal_entries"]:
            for line in entry["lines"]:
                if line["account_id"] == account_id:
                    rows.append([entry["date"], entry["entry_id"], entry["description"], line["debit_minor"], line["credit_minor"], entry["source_reference"]])
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "entry_id", "description", "debit_minor", "credit_minor", "source_reference"])
            writer.writerows(rows)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        return _ok({"path": relative, "row_count": len(rows), "sha256": digest})

    if operation == "create_coa_snapshot":
        ledger = _ledger(root)
        payload = json.dumps(ledger, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        snapshot_id = arguments["snapshot_id"]
        relative = "snapshots/" + snapshot_id + ".json"
        snapshot = {
            "snapshot_id": snapshot_id, "period": arguments["period"], "ledger_sha256": digest,
            "account_count": len(ledger["accounts"]), "journal_entry_count": len(ledger["journal_entries"]),
            "created_by": arguments["actor"], "ledger": ledger,
        }
        _save(root / relative, snapshot)
        return _ok({"snapshot_id": snapshot_id, "path": relative, "ledger_sha256": digest, "journal_entry_count": len(ledger["journal_entries"])})

    if operation == "open_workspace":
        ledger = _ledger(root)
        if arguments["workspace_tag"] not in {ledger["workspace_id"], "Northstar Components LLC"}:
            return _fail("not_found", "$.workspace_tag", "Workspace not found.")
        return _ok({"workspace_id": ledger["workspace_id"], "name": "Northstar Components LLC", "currency": ledger["currency"], "status": "active"})

    if operation == "workspace_overview":
        ledger = _ledger(root)
        queue = _load(root / "entities" / "review_queue.json")
        documents = list((root / "raw" / "documents").glob("*.json"))
        return _ok({
            "workspace_id": ledger["workspace_id"], "period": "2026-07",
            "account_count": len(ledger["accounts"]), "journal_entry_count": len(ledger["journal_entries"]),
            "source_document_count": len(documents) + 1,
            "open_review_count": sum(1 for item in queue["items"] if item["status"] == "open"),
        })

    if operation == "list_workspace_accounts":
        ledger = _ledger(root)
        items = []
        for account in ledger["accounts"]:
            debit = 0
            credit = 0
            for entry in ledger["journal_entries"]:
                for line in entry["lines"]:
                    if line["account_id"] == account["account_id"]:
                        debit += line["debit_minor"]
                        credit += line["credit_minor"]
            items.append({**account, "debit_minor": debit, "credit_minor": credit})
        return _ok({"items": items, "count": len(items)})

    if operation == "diagnose_workspace":
        ledger = _ledger(root)
        queue = _load(root / "entities" / "review_queue.json")
        control = _load(root / "raw" / "bank" / "statement_control.json")
        cash_balance = 0
        unbalanced_entries = []
        for entry in ledger["journal_entries"]:
            debit = sum(line["debit_minor"] for line in entry["lines"])
            credit = sum(line["credit_minor"] for line in entry["lines"])
            if debit != credit:
                unbalanced_entries.append(entry["entry_id"])
            for line in entry["lines"]:
                if line["account_id"] == control["account_id"]:
                    cash_balance += line["debit_minor"] - line["credit_minor"]
        issues = []
        difference = control["closing_balance_minor"] - cash_balance
        if difference:
            issues.append({"code": "bank_difference", "severity": "high", "message": f"Bank-to-book difference is {difference} minor units."})
        open_count = sum(1 for item in queue["items"] if item["status"] == "open")
        if open_count:
            issues.append({"code": "open_reviews", "severity": "medium", "message": f"{open_count} review items remain open."})
        if unbalanced_entries:
            issues.append({"code": "unbalanced_entries", "severity": "critical", "message": "Unbalanced entries: " + ", ".join(unbalanced_entries)})
        return _ok({"status": "healthy" if not issues else "attention_required", "issues": issues, "issue_count": len(issues)})

    if operation == "add_statement_note":
        path = root / "entities" / "ledger.json"
        ledger = _load(path)
        note_id = "note_" + str(len(ledger.setdefault("statement_notes", [])) + 1).zfill(3)
        note = {"note_id": note_id, "reference": arguments["reference"], "body": arguments["body"], "actor": arguments["actor"]}
        ledger["statement_notes"].append(note)
        _save(path, ledger)
        return _ok(note)

    if operation == "verify_expectations":
        queue = _load(root / "entities" / "review_queue.json")
        ledger = _ledger(root)
        control = _load(root / "raw" / "bank" / "statement_control.json")
        cash_balance = sum(line["debit_minor"] - line["credit_minor"] for entry in ledger["journal_entries"] for line in entry["lines"] if line["account_id"] == control["account_id"])
        difference = abs(control["closing_balance_minor"] - cash_balance)
        open_count = sum(1 for item in queue["items"] if item["status"] == "open")
        checks = [
            {"name": "maximum_bank_difference_minor", "passed": difference <= arguments["maximum_bank_difference_minor"], "observed": difference, "expected": arguments["maximum_bank_difference_minor"]},
            {"name": "maximum_open_reviews", "passed": open_count <= arguments["maximum_open_reviews"], "observed": open_count, "expected": arguments["maximum_open_reviews"]},
        ]
        return _ok({"all_passed": all(item["passed"] for item in checks), "checks": checks})

    if operation == "generate_balance_sheet":
        ledger = _ledger(root)
        accounts = _account_map(ledger)
        balances = {account_id: 0 for account_id in accounts}
        for entry in ledger["journal_entries"]:
            for line in entry["lines"]:
                account = accounts[line["account_id"]]
                if account["type"] in {"liability", "equity"}:
                    balances[line["account_id"]] += line["credit_minor"] - line["debit_minor"]
                else:
                    balances[line["account_id"]] += line["debit_minor"] - line["credit_minor"]
        assets = sum(value for account_id, value in balances.items() if accounts[account_id]["type"] == "asset")
        liabilities = sum(value for account_id, value in balances.items() if accounts[account_id]["type"] == "liability")
        equity = sum(value for account_id, value in balances.items() if accounts[account_id]["type"] == "equity")
        current_income = sum(value for account_id, value in balances.items() if accounts[account_id]["type"] == "revenue") - sum(value for account_id, value in balances.items() if accounts[account_id]["type"] == "expense")
        items = [{"account_id": account_id, "account_name": accounts[account_id]["name"], "category": accounts[account_id]["type"], "amount_minor": value} for account_id, value in balances.items() if accounts[account_id]["type"] in {"asset", "liability", "equity"}]
        return _ok({"as_of_date": arguments["as_of_date"], "assets_minor": assets, "liabilities_minor": liabilities, "equity_minor": equity, "current_income_minor": current_income, "balanced": assets == liabilities + equity + current_income, "items": items})

    if operation == "list_coa_snapshots":
        items = []
        for path in sorted((root / "snapshots").glob("*.json")):
            snapshot = _load(path)
            items.append({"snapshot_id": snapshot["snapshot_id"], "period": snapshot["period"], "path": path.relative_to(root).as_posix(), "ledger_sha256": snapshot["ledger_sha256"]})
        return _ok({"items": items, "count": len(items)})

    if operation == "get_coa_snapshot":
        path = root / "snapshots" / (arguments["snapshot_id"] + ".json")
        if not path.exists():
            return _fail("not_found", "$.snapshot_id", "Snapshot not found.")
        snapshot = _load(path)
        return _ok({"snapshot_id": snapshot["snapshot_id"], "period": snapshot["period"], "ledger_sha256": snapshot["ledger_sha256"], "account_count": snapshot["account_count"], "journal_entry_count": snapshot["journal_entry_count"], "created_by": snapshot["created_by"]})

    if operation == "export_workspace_documents":
        import zipfile
        relative = "exports/" + arguments["bundle_name"] + ".zip"
        output = root / relative
        members = ["raw/bank/operating_2026_07.csv", "raw/bank/statement_control.json"]
        members.extend(path.relative_to(root).as_posix() for path in sorted((root / "raw" / "documents").glob("*.json")))
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member in members:
                archive.write(root / member, member)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        return _ok({"path": relative, "document_count": len(members), "sha256": digest, "members": members})

    if operation == "list_coas":
        controls = _load(root / "entities" / "accounting_controls.json")
        return _ok({"items": controls["coas"], "count": len(controls["coas"])})

    if operation == "list_coa_accounts":
        controls = _load(root / "entities" / "accounting_controls.json")
        if arguments["coa_id"] not in {item["coa_id"] for item in controls["coas"]}:
            return _fail("not_found", "$.coa_id", "Chart of accounts not found.")
        accounts = _ledger(root)["accounts"]
        return _ok({"coa_id": arguments["coa_id"], "items": accounts, "count": len(accounts)})

    if operation == "diagnose_coa":
        ledger = _ledger(root)
        account_ids = [item["account_id"] for item in ledger["accounts"]]
        codes = [item["code"] for item in ledger["accounts"]]
        issues = []
        if len(account_ids) != len(set(account_ids)):
            issues.append({"code": "duplicate_account_id", "message": "Account IDs are not unique."})
        if len(codes) != len(set(codes)):
            issues.append({"code": "duplicate_account_code", "message": "Account codes are not unique."})
        unknown = sorted({line["account_id"] for entry in ledger["journal_entries"] for line in entry["lines"] if line["account_id"] not in set(account_ids)})
        if unknown:
            issues.append({"code": "unknown_account_reference", "message": "Unknown accounts: " + ", ".join(unknown)})
        return _ok({"coa_id": arguments["coa_id"], "healthy": not issues, "issues": issues, "issue_count": len(issues)})

    if operation in {"hold_coa_posting", "release_coa_posting"}:
        path = root / "entities" / "accounting_controls.json"
        controls = _load(path)
        posting = controls["posting_control"]
        if posting["coa_id"] != arguments["coa_id"]:
            return _fail("not_found", "$.coa_id", "Chart of accounts not found.")
        if operation == "hold_coa_posting":
            posting.update({"status": "held", "reason": arguments["reason"], "changed_by": arguments["actor"]})
        else:
            posting.update({"status": "released", "reason": arguments["reason"], "changed_by": arguments["actor"]})
        _save(path, controls)
        return _ok(posting)

    if operation == "get_coa_posting_status":
        posting = _load(root / "entities" / "accounting_controls.json")["posting_control"]
        if posting["coa_id"] != arguments["coa_id"]:
            return _fail("not_found", "$.coa_id", "Chart of accounts not found.")
        return _ok(posting)

    if operation == "list_adjustment_schedules":
        items = _load(root / "entities" / "accounting_controls.json")["adjustment_schedules"]
        if arguments.get("status"):
            items = [item for item in items if item["status"] == arguments["status"]]
        return _ok({"items": items, "count": len(items)})

    if operation == "list_workspace_corrections":
        items = _load(root / "entities" / "accounting_controls.json")["workspace_corrections"]
        if arguments.get("status"):
            items = [item for item in items if item["status"] == arguments["status"]]
        return _ok({"items": items, "count": len(items)})

    if operation == "get_workspace_correction":
        items = _load(root / "entities" / "accounting_controls.json")["workspace_corrections"]
        item = next((value for value in items if value["correction_id"] == arguments["correction_id"]), None)
        if item is None:
            return _fail("not_found", "$.correction_id", "Workspace correction not found.")
        return _ok({"correction": item})

    if operation == "inspect_open_source_financial_samples":
        journal = (root / "raw" / "open_source" / "hledger_sample.journal").read_text(encoding="utf-8")
        ofx = (root / "raw" / "open_source" / "libofx_statement_sample.ofx").read_text(encoding="utf-8", errors="replace")
        transaction_headers = sum(1 for line in journal.splitlines() if line and not line[0].isspace() and not line.startswith(";") and line[0].isdigit())
        posting_lines = sum(1 for line in journal.splitlines() if line.startswith((" ", "\t")) and line.strip() and not line.lstrip().startswith(";"))
        ofx_transaction_count = ofx.upper().count("<STMTTRN>") + ofx.upper().count("<BUYMF>") + ofx.upper().count("<SELLMF>")
        return _ok({"journal_transaction_count": transaction_headers, "journal_posting_count": posting_lines, "ofx_transaction_count": ofx_transaction_count, "ofx_version": "2.1.1" if "OFX" in ofx.upper() else "unknown"})

    return _fail("unsupported_operation", "$", "Unsupported tool operation.")
'''


def finstat_environment(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = root / "workspace"
    write_json(root / "provenance" / "mcp_seed.json", seed_record("finstat/finstatai"))
    open_sources = [
        {
            **download_source(
                "https://raw.githubusercontent.com/simonmichael/hledger/master/examples/sample.journal",
                workspace / "raw" / "open_source" / "hledger_sample.journal",
            ),
            "project": "hledger",
            "repository": "https://github.com/simonmichael/hledger",
            "license": "GPL-3.0",
            "usage": "Reference syntax for a real plain-text double-entry journal.",
        },
        {
            **download_source(
                "https://raw.githubusercontent.com/libofx/libofx/master/doc/ofx_sample_files/ofx-2.1.1-sect-13.13.ofx",
                workspace / "raw" / "open_source" / "libofx_statement_sample.ofx",
            ),
            "project": "libofx",
            "repository": "https://github.com/libofx/libofx",
            "license": "GPL-2.0",
            "usage": "Reference OFX 2.1.1 statement payload for parser and import tasks.",
        },
    ]
    write_json(root / "provenance" / "open_source_provenance.json", {"sources": open_sources})
    write_json(
        workspace / "raw" / "engagement.json",
        {
            "entity": "Northstar Components LLC",
            "industry": "electronic component distribution",
            "close_period": "2026-07",
            "currency": "USD",
            "objective": "Complete the July close with traceable source evidence and resolve the bank-to-book difference.",
            "materiality_minor": 500,
        },
    )
    statement_rows = [
        {"date": "2026-07-03", "description": "ACME Logistics INV-1048", "amount_minor": -12000, "currency": "USD", "reference": "AP-1048"},
        {"date": "2026-07-05", "description": "Customer receipt Orion", "amount_minor": 48000, "currency": "USD", "reference": "AR-775"},
        {"date": "2026-07-09", "description": "AWS cloud services", "amount_minor": -3600, "currency": "USD", "reference": "RCPT-AWS-701"},
        {"date": "2026-07-10", "description": "AWS cloud services retry", "amount_minor": -3600, "currency": "USD", "reference": "RCPT-AWS-701-B"},
        {"date": "2026-07-15", "description": "Payroll batch JUL-A", "amount_minor": -42000, "currency": "USD", "reference": "PAY-JUL-A"},
        {"date": "2026-07-20", "description": "Customer receipt Vega", "amount_minor": 60000, "currency": "USD", "reference": "AR-781"},
        {"date": "2026-07-26", "description": "Monthly account fee", "amount_minor": -300, "currency": "USD", "reference": "BANK-FEE-07"},
        {"date": "2026-07-29", "description": "State sales tax payment", "amount_minor": -8000, "currency": "USD", "reference": "TAX-2026-07"},
    ]
    write_csv(
        workspace / "raw" / "bank" / "operating_2026_07.csv",
        statement_rows,
        ["date", "description", "amount_minor", "currency", "reference"],
    )
    write_json(
        workspace / "raw" / "bank" / "statement_control.json",
        {"account_id": "1000", "period": "2026-07", "opening_balance_minor": 125000, "closing_balance_minor": 163500},
    )
    documents = [
        {"document_id": "doc_ap_1048", "document_type": "vendor_invoice", "reference": "AP-1048", "counterparty": "ACME Logistics", "issue_date": "2026-07-01", "total_minor": 12000, "currency": "USD"},
        {"document_id": "doc_ar_775", "document_type": "customer_invoice", "reference": "AR-775", "counterparty": "Orion Devices", "issue_date": "2026-06-25", "total_minor": 48000, "currency": "USD"},
        {"document_id": "doc_aws_701", "document_type": "receipt", "reference": "RCPT-AWS-701", "counterparty": "AWS", "issue_date": "2026-07-09", "total_minor": 3600, "currency": "USD"},
        {"document_id": "doc_ar_781", "document_type": "customer_invoice", "reference": "AR-781", "counterparty": "Vega Microsystems", "issue_date": "2026-07-12", "total_minor": 60000, "currency": "USD"},
    ]
    for document in documents:
        write_json(workspace / "raw" / "documents" / f'{document["document_id"]}.json', document)

    accounts = [
        {"account_id": "1000", "code": "1000", "name": "Operating Cash", "type": "asset"},
        {"account_id": "1100", "code": "1100", "name": "Accounts Receivable", "type": "asset"},
        {"account_id": "2000", "code": "2000", "name": "Accounts Payable", "type": "liability"},
        {"account_id": "3000", "code": "3000", "name": "Opening Equity", "type": "equity"},
        {"account_id": "4000", "code": "4000", "name": "Product Revenue", "type": "revenue"},
        {"account_id": "5100", "code": "5100", "name": "Logistics Expense", "type": "expense"},
        {"account_id": "5200", "code": "5200", "name": "Cloud Services", "type": "expense"},
        {"account_id": "5300", "code": "5300", "name": "Payroll Expense", "type": "expense"},
        {"account_id": "6100", "code": "6100", "name": "Bank Fees", "type": "expense"},
        {"account_id": "6200", "code": "6200", "name": "Sales Tax Expense", "type": "expense"},
    ]
    entries = [
        {"entry_id": "je_0001", "date": "2026-07-01", "description": "Opening balance", "source_reference": "OPEN-2026-07", "posted_by": "migration", "lines": [{"account_id": "1000", "debit_minor": 125000, "credit_minor": 0}, {"account_id": "3000", "debit_minor": 0, "credit_minor": 125000}]},
        {"entry_id": "je_0002", "date": "2026-07-03", "description": "Logistics invoice paid", "source_reference": "AP-1048", "posted_by": "import", "lines": [{"account_id": "5100", "debit_minor": 12000, "credit_minor": 0}, {"account_id": "1000", "debit_minor": 0, "credit_minor": 12000}]},
        {"entry_id": "je_0003", "date": "2026-07-05", "description": "Orion invoice collected", "source_reference": "AR-775", "posted_by": "import", "lines": [{"account_id": "1000", "debit_minor": 48000, "credit_minor": 0}, {"account_id": "4000", "debit_minor": 0, "credit_minor": 48000}]},
        {"entry_id": "je_0004", "date": "2026-07-09", "description": "AWS cloud services", "source_reference": "RCPT-AWS-701", "posted_by": "import", "lines": [{"account_id": "5200", "debit_minor": 3600, "credit_minor": 0}, {"account_id": "1000", "debit_minor": 0, "credit_minor": 3600}]},
        {"entry_id": "je_0005", "date": "2026-07-10", "description": "AWS retry pending duplicate review", "source_reference": "RCPT-AWS-701-B", "posted_by": "import", "lines": [{"account_id": "5200", "debit_minor": 3600, "credit_minor": 0}, {"account_id": "1000", "debit_minor": 0, "credit_minor": 3600}]},
        {"entry_id": "je_0006", "date": "2026-07-15", "description": "July payroll batch A", "source_reference": "PAY-JUL-A", "posted_by": "import", "lines": [{"account_id": "5300", "debit_minor": 42000, "credit_minor": 0}, {"account_id": "1000", "debit_minor": 0, "credit_minor": 42000}]},
        {"entry_id": "je_0007", "date": "2026-07-20", "description": "Vega invoice collected", "source_reference": "AR-781", "posted_by": "import", "lines": [{"account_id": "1000", "debit_minor": 60000, "credit_minor": 0}, {"account_id": "4000", "debit_minor": 0, "credit_minor": 60000}]},
    ]
    ledger_value = {"workspace_id": "northstar_2026", "currency": "USD", "accounts": accounts, "journal_entries": entries}
    write_json(workspace / "entities" / "ledger.json", ledger_value)
    opening_digest = hashlib.sha256(json.dumps(ledger_value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    write_json(
        workspace / "snapshots" / "opening_2026_07.json",
        {"snapshot_id": "opening_2026_07", "period": "2026-07", "ledger_sha256": opening_digest, "account_count": len(accounts), "journal_entry_count": len(entries), "created_by": "migration", "ledger": ledger_value},
    )
    write_json(
        workspace / "entities" / "review_queue.json",
        {
            "items": [
                {"review_id": "review_001", "kind": "possible_duplicate", "reference": "RCPT-AWS-701-B", "amount_minor": 3600, "status": "open", "reason": "Same merchant and amount posted on consecutive days.", "resolution": "", "resolved_by": ""},
                {"review_id": "review_002", "kind": "missing_journal_entry", "reference": "BANK-FEE-07", "amount_minor": 300, "status": "open", "reason": "Statement transaction has no journal entry.", "resolution": "", "resolved_by": ""},
                {"review_id": "review_003", "kind": "missing_journal_entry", "reference": "TAX-2026-07", "amount_minor": 8000, "status": "open", "reason": "Tax payment has no journal entry.", "resolution": "", "resolved_by": ""},
            ]
        },
    )
    write_json(
        workspace / "entities" / "accounting_controls.json",
        {
            "coas": [{"coa_id": "coa_us_gaap_2026", "name": "Northstar US GAAP", "status": "active", "accounting_basis": "accrual", "currency": "USD"}],
            "posting_control": {"coa_id": "coa_us_gaap_2026", "status": "released", "reason": "", "changed_by": "system"},
            "adjustment_schedules": [
                {"schedule_id": "adj_cloud_2026", "name": "Annual cloud prepayment amortization", "account_id": "5200", "periods_remaining": 5, "amount_per_period_minor": 1200, "status": "active"},
                {"schedule_id": "adj_insurance_2026", "name": "Business insurance amortization", "account_id": "5100", "periods_remaining": 8, "amount_per_period_minor": 750, "status": "active"},
            ],
            "workspace_corrections": [
                {"correction_id": "corr_001", "kind": "source_reference", "target_id": "je_0004", "status": "adopted", "description": "Normalized AWS receipt reference after source review.", "actor": "controller"},
                {"correction_id": "corr_002", "kind": "classification", "target_id": "je_0005", "status": "proposed", "description": "Review possible duplicate cloud charge before close.", "actor": "assistant-controller"},
            ],
        },
    )
    write_json(
        workspace / "derived" / "close_control.json",
        {"period": "2026-07", "materiality_minor": 500, "required_reports": ["trial_balance", "profit_and_loss"], "required_open_review_count": 0},
    )
    for directory in ("exports", "snapshots", "reports"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)

    document_item = closed_object({"path": STRING, "document_type": STRING, "size": INTEGER}, ["path", "document_type", "size"])
    review_item = closed_object(
        {"review_id": STRING, "kind": STRING, "reference": STRING, "amount_minor": INTEGER, "status": STRING, "reason": STRING, "resolution": STRING, "resolved_by": STRING},
        ["review_id", "kind", "reference", "amount_minor", "status", "reason", "resolution", "resolved_by"],
    )
    transaction_item = closed_object(
        {"entry_id": STRING, "date": STRING, "description": STRING, "debit_minor": INTEGER, "credit_minor": INTEGER, "source_reference": STRING},
        ["entry_id", "date", "description", "debit_minor", "credit_minor", "source_reference"],
    )
    line_input = closed_object(
        {"account_id": STRING, "debit_minor": {"type": "integer", "minimum": 0}, "credit_minor": {"type": "integer", "minimum": 0}},
        ["account_id", "debit_minor", "credit_minor"],
    )
    trial_item = closed_object({"account_id": STRING, "account_name": STRING, "debit_minor": INTEGER, "credit_minor": INTEGER}, ["account_id", "account_name", "debit_minor", "credit_minor"])
    pnl_item = closed_object({"account_id": STRING, "account_name": STRING, "category": STRING, "amount_minor": INTEGER}, ["account_id", "account_name", "category", "amount_minor"])
    account_item = closed_object({"account_id": STRING, "code": STRING, "name": STRING, "type": STRING, "debit_minor": INTEGER, "credit_minor": INTEGER}, ["account_id", "code", "name", "type", "debit_minor", "credit_minor"])
    diagnostic_item = closed_object({"code": STRING, "severity": STRING, "message": STRING}, ["code", "severity", "message"])
    expectation_item = closed_object({"name": STRING, "passed": BOOLEAN, "observed": INTEGER, "expected": INTEGER}, ["name", "passed", "observed", "expected"])
    balance_sheet_item = closed_object({"account_id": STRING, "account_name": STRING, "category": STRING, "amount_minor": INTEGER}, ["account_id", "account_name", "category", "amount_minor"])
    snapshot_item = closed_object({"snapshot_id": STRING, "period": STRING, "path": STRING, "ledger_sha256": STRING}, ["snapshot_id", "period", "path", "ledger_sha256"])
    coa_item = closed_object({"coa_id": STRING, "name": STRING, "status": STRING, "accounting_basis": STRING, "currency": STRING}, ["coa_id", "name", "status", "accounting_basis", "currency"])
    coa_account_item = closed_object({"account_id": STRING, "code": STRING, "name": STRING, "type": STRING}, ["account_id", "code", "name", "type"])
    coa_issue_item = closed_object({"code": STRING, "message": STRING}, ["code", "message"])
    posting_control_schema = closed_object({"coa_id": STRING, "status": {"type": "string", "enum": ["held", "released"]}, "reason": STRING, "changed_by": STRING}, ["coa_id", "status", "reason", "changed_by"])
    adjustment_item = closed_object({"schedule_id": STRING, "name": STRING, "account_id": STRING, "periods_remaining": INTEGER, "amount_per_period_minor": INTEGER, "status": STRING}, ["schedule_id", "name", "account_id", "periods_remaining", "amount_per_period_minor", "status"])
    correction_item = closed_object({"correction_id": STRING, "kind": STRING, "target_id": STRING, "status": STRING, "description": STRING, "actor": STRING}, ["correction_id", "kind", "target_id", "status", "description", "actor"])
    tools = [
        tool("list_source_documents", "列出月结工作区中的银行对账单和业务凭证，并返回文件类型与大小；用于开始证据盘点，不修改账簿。", {}, [], closed_object({"items": array_of(document_item), "count": INTEGER}, ["items", "count"]), FINSTAT_RUNTIME),
        tool("inspect_bank_statement", "读取银行对账单及控制余额，核对期初余额、交易汇总和期末余额是否自洽。", {}, [], closed_object({"account_id": STRING, "period": STRING, "opening_balance_minor": INTEGER, "closing_balance_minor": INTEGER, "transaction_total_minor": INTEGER, "computed_closing_balance_minor": INTEGER, "control_matches": BOOLEAN, "transaction_count": INTEGER}, ["account_id", "period", "opening_balance_minor", "closing_balance_minor", "transaction_total_minor", "computed_closing_balance_minor", "control_matches", "transaction_count"]), FINSTAT_RUNTIME),
        tool("list_review_items", "查询需要人工判断的重复交易、缺失凭证和未入账事项；可按状态筛选。", {"status": {"type": "string", "enum": ["open", "resolved"]}}, [], closed_object({"items": array_of(review_item), "count": INTEGER}, ["items", "count"]), FINSTAT_RUNTIME),
        tool("resolve_review_item", "记录一项账务复核的处置结论和操作人；会修改复核队列，但不会自动生成会计分录。", {"review_id": STRING, "resolution": {"type": "string", "minLength": 3}, "actor": STRING}, ["review_id", "resolution", "actor"], closed_object({"review_id": STRING, "status": STRING, "resolution": STRING}, ["review_id", "status", "resolution"]), FINSTAT_RUNTIME),
        tool("list_workspace_account_transactions", "按科目返回已入账的分录行及来源引用，用于追溯余额构成。", {"account_id": STRING}, ["account_id"], closed_object({"account_id": STRING, "items": array_of(transaction_item), "count": INTEGER}, ["account_id", "items", "count"]), FINSTAT_RUNTIME),
        tool("reconcile_workspace_account", "比较银行控制余额和账簿余额，返回差额、状态及已经匹配的来源引用；不自动调整账簿。", {"account_id": STRING}, ["account_id"], closed_object({"account_id": STRING, "statement_balance_minor": INTEGER, "book_balance_minor": INTEGER, "difference_minor": INTEGER, "status": {"type": "string", "enum": ["reconciled", "needs_adjustment"]}, "matched_references": array_of(STRING)}, ["account_id", "statement_balance_minor", "book_balance_minor", "difference_minor", "status", "matched_references"]), FINSTAT_RUNTIME),
        tool("create_standard_journal_entry", "创建一条可追溯的双重记账分录；所有科目必须存在且借贷金额必须相等。", {"date": {"type": "string", "format": "date"}, "description": {"type": "string", "minLength": 3}, "source_reference": STRING, "actor": STRING, "lines": array_of(line_input, min_items=2)}, ["date", "description", "source_reference", "actor", "lines"], closed_object({"entry_id": STRING, "debit_total_minor": INTEGER, "credit_total_minor": INTEGER, "status": {"type": "string", "const": "posted"}}, ["entry_id", "debit_total_minor", "credit_total_minor", "status"]), FINSTAT_RUNTIME),
        tool("generate_trial_balance", "根据当前账簿生成指定期间的试算平衡结果，用于检查全部分录借贷总额是否相等。", {"period": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"}}, ["period"], closed_object({"period": STRING, "items": array_of(trial_item), "total_debit_minor": INTEGER, "total_credit_minor": INTEGER, "balanced": BOOLEAN}, ["period", "items", "total_debit_minor", "total_credit_minor", "balanced"]), FINSTAT_RUNTIME),
        tool("generate_profit_and_loss", "根据收入和费用科目生成指定月份的损益表，并给出净利润及科目明细。", {"period": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"}}, ["period"], closed_object({"period": STRING, "revenue_minor": INTEGER, "expense_minor": INTEGER, "net_income_minor": INTEGER, "lines": array_of(pnl_item)}, ["period", "revenue_minor", "expense_minor", "net_income_minor", "lines"]), FINSTAT_RUNTIME),
        tool("export_workspace_account_csv", "把一个账簿科目的分录明细导出为 CSV，并返回行数与 SHA-256，供审计交付。", {"account_id": STRING, "file_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}}, ["account_id", "file_name"], closed_object({"path": STRING, "row_count": INTEGER, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, ["path", "row_count", "sha256"]), FINSTAT_RUNTIME),
        tool("create_coa_snapshot", "为当前科目表和全部分录创建不可变 JSON 快照，并记录账簿摘要哈希。", {"snapshot_id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}, "period": STRING, "actor": STRING}, ["snapshot_id", "period", "actor"], closed_object({"snapshot_id": STRING, "path": STRING, "ledger_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "journal_entry_count": INTEGER}, ["snapshot_id", "path", "ledger_sha256", "journal_entry_count"]), FINSTAT_RUNTIME),
        tool("open_workspace", "按工作区 ID 或精确名称确认账务工作区存在且处于可用状态；这是恢复月结工作前的只读检查。", {"workspace_tag": STRING}, ["workspace_tag"], closed_object({"workspace_id": STRING, "name": STRING, "currency": STRING, "status": STRING}, ["workspace_id", "name", "currency", "status"]), FINSTAT_RUNTIME),
        tool("workspace_overview", "汇总工作区的科目、分录、来源文档和开放复核项数量，用于判断月结进度。", {}, [], closed_object({"workspace_id": STRING, "period": STRING, "account_count": INTEGER, "journal_entry_count": INTEGER, "source_document_count": INTEGER, "open_review_count": INTEGER}, ["workspace_id", "period", "account_count", "journal_entry_count", "source_document_count", "open_review_count"]), FINSTAT_RUNTIME),
        tool("list_workspace_accounts", "列出科目表并计算每个科目的累计借方和贷方，用于报表前的科目检查。", {}, [], closed_object({"items": array_of(account_item), "count": INTEGER}, ["items", "count"]), FINSTAT_RUNTIME),
        tool("diagnose_workspace", "诊断银行账差、开放复核项和不平衡分录，返回按严重度组织的问题，不自动修改账务状态。", {}, [], closed_object({"status": {"type": "string", "enum": ["healthy", "attention_required"]}, "issues": array_of(diagnostic_item), "issue_count": INTEGER}, ["status", "issues", "issue_count"]), FINSTAT_RUNTIME),
        tool("add_statement_note", "给银行流水引用追加审计备注和操作人，用于记录会计判断，不改变金额或过账状态。", {"reference": STRING, "body": {"type": "string", "minLength": 3}, "actor": STRING}, ["reference", "body", "actor"], closed_object({"note_id": STRING, "reference": STRING, "body": STRING, "actor": STRING}, ["note_id", "reference", "body", "actor"]), FINSTAT_RUNTIME),
        tool("verify_expectations", "检查当前银行账差和开放复核项是否满足调用方给出的关账期望，逐项返回观测值。", {"maximum_bank_difference_minor": {"type": "integer", "minimum": 0}, "maximum_open_reviews": {"type": "integer", "minimum": 0}}, ["maximum_bank_difference_minor", "maximum_open_reviews"], closed_object({"all_passed": BOOLEAN, "checks": array_of(expectation_item)}, ["all_passed", "checks"]), FINSTAT_RUNTIME),
        tool("generate_balance_sheet", "根据当前账簿生成指定日期的资产负债表，并把本期损益纳入平衡检查。", {"as_of_date": {"type": "string", "format": "date"}}, ["as_of_date"], closed_object({"as_of_date": STRING, "assets_minor": INTEGER, "liabilities_minor": INTEGER, "equity_minor": INTEGER, "current_income_minor": INTEGER, "balanced": BOOLEAN, "items": array_of(balance_sheet_item)}, ["as_of_date", "assets_minor", "liabilities_minor", "equity_minor", "current_income_minor", "balanced", "items"]), FINSTAT_RUNTIME),
        tool("list_coa_snapshots", "列出已经生成的账簿快照及期间、路径和摘要，便于选择审计基线。", {}, [], closed_object({"items": array_of(snapshot_item), "count": INTEGER}, ["items", "count"]), FINSTAT_RUNTIME),
        tool("get_coa_snapshot", "读取指定账簿快照的控制信息，不返回完整账簿正文。", {"snapshot_id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}}, ["snapshot_id"], closed_object({"snapshot_id": STRING, "period": STRING, "ledger_sha256": STRING, "account_count": INTEGER, "journal_entry_count": INTEGER, "created_by": STRING}, ["snapshot_id", "period", "ledger_sha256", "account_count", "journal_entry_count", "created_by"]), FINSTAT_RUNTIME),
        tool("export_workspace_documents", "把银行对账单、控制信息和全部业务凭证打包为 ZIP，并返回成员清单和 SHA-256。", {"bundle_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}}, ["bundle_name"], closed_object({"path": STRING, "document_count": INTEGER, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "members": array_of(STRING)}, ["path", "document_count", "sha256", "members"]), FINSTAT_RUNTIME),
        tool("list_coas", "列出工作区中可用的科目表报告视图及核算基础、币种和状态。", {}, [], closed_object({"items": array_of(coa_item), "count": INTEGER}, ["items", "count"]), FINSTAT_RUNTIME),
        tool("list_coa_accounts", "列出指定科目表视图中的全部科目定义。", {"coa_id": STRING}, ["coa_id"], closed_object({"coa_id": STRING, "items": array_of(coa_account_item), "count": INTEGER}, ["coa_id", "items", "count"]), FINSTAT_RUNTIME),
        tool("diagnose_coa", "检查科目 ID、科目代码和分录引用的一致性，返回结构性问题。", {"coa_id": STRING}, ["coa_id"], closed_object({"coa_id": STRING, "healthy": BOOLEAN, "issues": array_of(coa_issue_item), "issue_count": INTEGER}, ["coa_id", "healthy", "issues", "issue_count"]), FINSTAT_RUNTIME),
        tool("hold_coa_posting", "冻结指定科目表的后续过账并记录原因和操作人；用于调查重大异常。", {"coa_id": STRING, "reason": {"type": "string", "minLength": 3}, "actor": STRING}, ["coa_id", "reason", "actor"], posting_control_schema, FINSTAT_RUNTIME),
        tool("release_coa_posting", "解除指定科目表的过账冻结并记录理由和操作人。", {"coa_id": STRING, "reason": {"type": "string", "minLength": 3}, "actor": STRING}, ["coa_id", "reason", "actor"], posting_control_schema, FINSTAT_RUNTIME),
        tool("get_coa_posting_status", "读取指定科目表当前是否冻结以及最近一次状态变更理由。", {"coa_id": STRING}, ["coa_id"], posting_control_schema, FINSTAT_RUNTIME),
        tool("list_adjustment_schedules", "列出跨期间费用分摊计划，可按 active 或 completed 状态筛选。", {"status": {"type": "string", "enum": ["active", "completed"]}}, [], closed_object({"items": array_of(adjustment_item), "count": INTEGER}, ["items", "count"]), FINSTAT_RUNTIME),
        tool("list_workspace_corrections", "列出账簿来源引用和分类纠正声明，可按 proposed 或 adopted 状态筛选。", {"status": {"type": "string", "enum": ["proposed", "adopted"]}}, [], closed_object({"items": array_of(correction_item), "count": INTEGER}, ["items", "count"]), FINSTAT_RUNTIME),
        tool("get_workspace_correction", "读取一条纠正声明的目标、说明、状态和操作人。", {"correction_id": STRING}, ["correction_id"], closed_object({"correction": correction_item}, ["correction"]), FINSTAT_RUNTIME),
        tool("inspect_open_source_financial_samples", "实际解析 hledger journal 和 libofx OFX 开源文件，返回交易与分录规模而非只展示来源元数据。", {}, [], closed_object({"journal_transaction_count": INTEGER, "journal_posting_count": INTEGER, "ofx_transaction_count": INTEGER, "ofx_version": STRING}, ["journal_transaction_count", "journal_posting_count", "ofx_transaction_count", "ofx_version"]), FINSTAT_RUNTIME),
    ]
    environment = {
        "schema_version": "1.0",
        "environment_id": "finstat_month_end_close_001",
        "name": "FinStat 月末关账与审计工作区",
        "description": "面向电子元器件分销企业 2026 年 7 月关账的文件型财务环境，包含银行流水、发票与收据、可变双重记账账簿、复核队列、关账控制规则和审计导出目录。",
        "resources": [
            resource("engagement_context", "关账业务背景", "会计主体、关账期间、币种、目标和重要性阈值。", "raw", "file", "raw/engagement.json", "json", False),
            resource("bank_statement", "银行交易明细", "Operating Cash 账户的 2026 年 7 月银行流水。", "raw", "file", "raw/bank/operating_2026_07.csv", "csv", False),
            resource("statement_control", "银行对账单控制信息", "银行账户期初、期末余额和对账期间。", "raw", "file", "raw/bank/statement_control.json", "json", False),
            resource("source_documents", "业务凭证集合", "与银行流水关联的客户发票、供应商发票和费用收据。", "raw", "file_collection", "raw/documents/*.json", "json", False),
            resource("open_source_financial_samples", "开源财务格式样本", "从 hledger 和 libofx 官方开源仓库下载的原始 journal 与 OFX 文件。", "raw", "file_collection", "raw/open_source/*", "mixed", False),
            resource("ledger", "双重记账账簿", "科目表及已经过账的标准会计分录；工具可以追加平衡分录。", "entity", "file", "entities/ledger.json", "json", True, source_resources=["bank_statement", "source_documents"], entity_schema={"account": {"description": "总账科目。", "fields": {"account_id": "string", "code": "string", "name": "string", "type": "string"}}, "journal_entry": {"description": "借贷平衡且带来源引用的会计分录。", "fields": {"entry_id": "string", "date": "string", "description": "string", "source_reference": "string", "posted_by": "string"}}}),
            resource("review_queue", "财务复核队列", "重复交易、缺失分录等需要人工判断的问题及处置状态。", "entity", "file", "entities/review_queue.json", "json", True, source_resources=["bank_statement", "source_documents", "ledger"], entity_schema={"review_item": {"description": "一项需要会计人员判断的异常。", "fields": {"review_id": "string", "kind": "string", "reference": "string", "amount_minor": "integer", "status": "string", "reason": "string", "resolution": "string", "resolved_by": "string"}}}),
            resource("accounting_controls", "会计控制与纠正记录", "科目表视图、过账冻结状态、调整计划和可审计纠正声明。", "entity", "file", "entities/accounting_controls.json", "json", True, source_resources=["ledger", "review_queue"], entity_schema={"coa": {"description": "账簿上的独立报告科目表视图。", "fields": {"coa_id": "string", "name": "string", "status": "string", "accounting_basis": "string", "currency": "string"}}, "adjustment_schedule": {"description": "跨期间分摊的标准调整计划。", "fields": {"schedule_id": "string", "name": "string", "account_id": "string", "periods_remaining": "integer", "amount_per_period_minor": "integer", "status": "string"}}, "workspace_correction": {"description": "对账簿事实或分类提出的可审计纠正。", "fields": {"correction_id": "string", "kind": "string", "target_id": "string", "status": "string", "description": "string", "actor": "string"}}}),
            resource("close_control", "关账控制规则", "重要性阈值、必需报表和开放复核项要求。", "derived", "file", "derived/close_control.json", "json", False, source_resources=["engagement_context"]),
            resource("account_exports", "科目审计导出", "工具生成的科目 CSV 明细。", "output", "directory", "exports/", "directory", True),
            resource("ledger_snapshots", "账簿快照", "工具生成的带哈希账簿快照。", "output", "directory", "snapshots/", "directory", True),
            resource("financial_reports", "财务报告目录", "关账过程中生成的正式报告位置。", "output", "directory", "reports/", "directory", True),
        ],
        "rules": [
            {"description": "所有 journal_entry 分录行的借方合计必须等于贷方合计，且引用的 account_id 必须存在。", "resources": ["ledger"]},
            {"description": "银行账户只有在账簿余额等于银行期末余额且开放复核项为零时才能完成关账。", "resources": ["statement_control", "ledger", "review_queue", "close_control"]},
            {"description": "每条导出记录和账簿快照必须保留来源引用或账簿 SHA-256，支持从报表回溯原始凭证。", "resources": ["source_documents", "ledger", "account_exports", "ledger_snapshots"]},
        ],
        "tools": tools,
    }
    smoke = {
        "list_source_documents": {},
        "inspect_bank_statement": {},
        "list_review_items": {"status": "open"},
        "resolve_review_item": {"review_id": "review_002", "resolution": "Post bank fee adjustment.", "actor": "controller"},
        "list_workspace_account_transactions": {"account_id": "1000"},
        "reconcile_workspace_account": {"account_id": "1000"},
        "create_standard_journal_entry": {"date": "2026-07-26", "description": "Record monthly bank fee", "source_reference": "BANK-FEE-07", "actor": "controller", "lines": [{"account_id": "6100", "debit_minor": 300, "credit_minor": 0}, {"account_id": "1000", "debit_minor": 0, "credit_minor": 300}]},
        "generate_trial_balance": {"period": "2026-07"},
        "generate_profit_and_loss": {"period": "2026-07"},
        "export_workspace_account_csv": {"account_id": "1000", "file_name": "operating_cash_july"},
        "create_coa_snapshot": {"snapshot_id": "preclose_2026_07", "period": "2026-07", "actor": "controller"},
        "open_workspace": {"workspace_tag": "northstar_2026"},
        "workspace_overview": {},
        "list_workspace_accounts": {},
        "diagnose_workspace": {},
        "add_statement_note": {"reference": "RCPT-AWS-701-B", "body": "Second charge requires vendor confirmation.", "actor": "controller"},
        "verify_expectations": {"maximum_bank_difference_minor": 500, "maximum_open_reviews": 0},
        "generate_balance_sheet": {"as_of_date": "2026-07-31"},
        "list_coa_snapshots": {},
        "get_coa_snapshot": {"snapshot_id": "opening_2026_07"},
        "export_workspace_documents": {"bundle_name": "july_source_evidence"},
        "list_coas": {},
        "list_coa_accounts": {"coa_id": "coa_us_gaap_2026"},
        "diagnose_coa": {"coa_id": "coa_us_gaap_2026"},
        "hold_coa_posting": {"coa_id": "coa_us_gaap_2026", "reason": "Investigate bank difference.", "actor": "controller"},
        "release_coa_posting": {"coa_id": "coa_us_gaap_2026", "reason": "Review completed.", "actor": "controller"},
        "get_coa_posting_status": {"coa_id": "coa_us_gaap_2026"},
        "list_adjustment_schedules": {"status": "active"},
        "list_workspace_corrections": {"status": "proposed"},
        "get_workspace_correction": {"correction_id": "corr_002"},
        "inspect_open_source_financial_samples": {},
    }
    return environment, smoke


# bugAgent 环境运行时：把测试、缺陷、安全、性能和审计证据连接成发布门禁。
BUGAGENT_RUNTIME = r'''
def _load(path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))

def _save(path, value):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _ok(data):
    return {"success": True, "data": data}

def _fail(code, path, message, retryable=False):
    return {"success": False, "error": {"code": code, "path": path, "message": message, "retryable": retryable}}

def _registry(root):
    return _load(root / "entities" / "quality_registry.json")

def _sarif_findings(root):
    sarif = _load(root / "raw" / "security" / "scan.sarif")
    results = sarif["runs"][0]["results"]
    return [{"rule_id": item["ruleId"], "level": item["level"], "message": item["message"]["text"], "path": item["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]} for item in results]

def _performance(root):
    import csv
    import math
    with (root / "raw" / "performance" / "checkout_samples.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    latencies = sorted(int(item["latency_ms"]) for item in rows)
    failures = sum(1 for item in rows if item["status"] != "ok")
    p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)]
    return {"sample_count": len(rows), "p95_latency_ms": p95, "error_rate": failures / len(rows)}

def _dispatch(operation, arguments, context):
    import hashlib
    import json
    import zipfile

    root = context.workspace_root
    if operation == "list_test_runs":
        items = []
        for path in sorted((root / "raw" / "test_runs").glob("*.json")):
            run = _load(path)
            items.append({"run_id": run["run_id"], "suite": run["suite"], "commit": run["commit"], "started_at": run["started_at"], "passed": run["summary"]["passed"], "failed": run["summary"]["failed"], "status": run["status"]})
        return _ok({"items": items, "count": len(items)})

    if operation == "get_test_reports_failures":
        path = root / "raw" / "test_runs" / (arguments["run_id"] + ".json")
        if not path.exists():
            return _fail("not_found", "$.run_id", "Test run not found.")
        run = _load(path)
        items = [{"test_case_id": item["test_case_id"], "name": item["name"], "error": item["error"], "trace_path": item["trace_path"]} for item in run["tests"] if item["status"] == "failed"]
        return _ok({"run_id": run["run_id"], "items": items, "count": len(items)})

    if operation == "create_bug_report":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        bug_id = "BUG-" + str(100 + len(registry["bugs"]) + 1)
        bug = {"bug_id": bug_id, "title": arguments["title"], "severity": arguments["severity"], "status": "open", "component": arguments["component"], "source_run_id": arguments["source_run_id"], "description": arguments["description"], "classification": "unclassified"}
        registry["bugs"].append(bug)
        _save(path, registry)
        return _ok({"bug_id": bug_id, "status": "open", "severity": bug["severity"]})

    if operation == "classify_bug":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        bug = next((item for item in registry["bugs"] if item["bug_id"] == arguments["bug_id"]), None)
        if bug is None:
            return _fail("not_found", "$.bug_id", "Bug not found.")
        bug["classification"] = arguments["classification"]
        bug["component"] = arguments["component"]
        _save(path, registry)
        return _ok({"bug_id": bug["bug_id"], "classification": bug["classification"], "component": bug["component"]})

    if operation == "link_test_case_to_bug":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        bug_ids = {item["bug_id"] for item in registry["bugs"]}
        test_ids = {item["test_case_id"] for item in registry["test_cases"]}
        if arguments["bug_id"] not in bug_ids:
            return _fail("not_found", "$.bug_id", "Bug not found.")
        if arguments["test_case_id"] not in test_ids:
            return _fail("not_found", "$.test_case_id", "Test case not found.")
        link = {"bug_id": arguments["bug_id"], "test_case_id": arguments["test_case_id"], "relation": arguments["relation"]}
        if link not in registry["links"]:
            registry["links"].append(link)
            _save(path, registry)
        return _ok(link)

    if operation == "add_comment":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        if arguments["bug_id"] not in {item["bug_id"] for item in registry["bugs"]}:
            return _fail("not_found", "$.bug_id", "Bug not found.")
        comment_id = "comment_" + str(len(registry["comments"]) + 1).zfill(3)
        comment = {"comment_id": comment_id, "bug_id": arguments["bug_id"], "author": arguments["author"], "body": arguments["body"]}
        registry["comments"].append(comment)
        _save(path, registry)
        return _ok({"comment_id": comment_id, "bug_id": arguments["bug_id"]})

    if operation == "get_security_results":
        findings = _sarif_findings(root)
        minimum = arguments.get("minimum_level")
        ranks = {"note": 0, "warning": 1, "error": 2}
        if minimum is not None:
            findings = [item for item in findings if ranks[item["level"]] >= ranks[minimum]]
        counts = {"note": 0, "warning": 0, "error": 0}
        for item in findings:
            counts[item["level"]] += 1
        return _ok({"items": findings, "count": len(findings), "counts": counts})

    if operation == "get_performance_results":
        metrics = _performance(root)
        policy = _load(root / "derived" / "release_policy.json")
        metrics["latency_gate_passed"] = metrics["p95_latency_ms"] <= policy["max_p95_latency_ms"]
        metrics["error_rate_gate_passed"] = metrics["error_rate"] <= policy["max_error_rate"]
        return _ok(metrics)

    if operation == "collect_compliance_evidence":
        registry = _registry(root)
        bug = next((item for item in registry["bugs"] if item["bug_id"] == arguments["bug_id"]), None)
        if bug is None:
            return _fail("not_found", "$.bug_id", "Bug not found.")
        links = [item for item in registry["links"] if item["bug_id"] == bug["bug_id"]]
        members = ["manifest.json", "raw/security/scan.sarif", "raw/performance/checkout_samples.csv"]
        relative = "evidence/" + arguments["bundle_name"] + ".zip"
        output = root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = {"bug": bug, "test_case_links": links, "comments": [item for item in registry["comments"] if item["bug_id"] == bug["bug_id"]]}
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.write(root / "raw" / "security" / "scan.sarif", "raw/security/scan.sarif")
            archive.write(root / "raw" / "performance" / "checkout_samples.csv", "raw/performance/checkout_samples.csv")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        return _ok({"path": relative, "sha256": digest, "members": members})

    if operation == "generate_release_readiness_report":
        policy = _load(root / "derived" / "release_policy.json")
        run_paths = sorted((root / "raw" / "test_runs").glob("*.json"))
        run = _load(run_paths[-1])
        total = run["summary"]["passed"] + run["summary"]["failed"]
        pass_rate = run["summary"]["passed"] / total
        security = _sarif_findings(root)
        blocking_security_count = sum(1 for item in security if item["level"] == "error")
        performance = _performance(root)
        open_critical_bugs = sum(1 for item in _registry(root)["bugs"] if item["status"] == "open" and item["severity"] == "critical")
        checks = [
            {"name": "test_pass_rate", "passed": pass_rate >= policy["minimum_test_pass_rate"], "observed": round(pass_rate, 4), "threshold": policy["minimum_test_pass_rate"]},
            {"name": "blocking_security", "passed": blocking_security_count <= policy["max_blocking_security_findings"], "observed": blocking_security_count, "threshold": policy["max_blocking_security_findings"]},
            {"name": "p95_latency_ms", "passed": performance["p95_latency_ms"] <= policy["max_p95_latency_ms"], "observed": performance["p95_latency_ms"], "threshold": policy["max_p95_latency_ms"]},
            {"name": "open_critical_bugs", "passed": open_critical_bugs == 0, "observed": open_critical_bugs, "threshold": 0},
        ]
        decision = "go" if all(item["passed"] for item in checks) else "no_go"
        relative = "reports/" + arguments["report_name"] + ".md"
        output = root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Release Readiness", "", "Decision: **" + decision.upper() + "**", "", "| Gate | Observed | Threshold | Passed |", "|---|---:|---:|---|"]
        lines.extend("| {name} | {observed} | {threshold} | {passed} |".format(**item) for item in checks)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return _ok({"decision": decision, "checks": checks, "path": relative})

    if operation == "list_bug_reports":
        items = _registry(root)["bugs"]
        if arguments.get("status"):
            items = [item for item in items if item["status"] == arguments["status"]]
        if arguments.get("severity"):
            items = [item for item in items if item["severity"] == arguments["severity"]]
        return _ok({"items": items, "count": len(items)})

    if operation == "get_bug_report":
        registry = _registry(root)
        bug = next((item for item in registry["bugs"] if item["bug_id"] == arguments["bug_id"]), None)
        if bug is None:
            return _fail("not_found", "$.bug_id", "Bug not found.")
        return _ok({"bug": bug, "links": [item for item in registry["links"] if item["bug_id"] == bug["bug_id"]], "comments": [item for item in registry["comments"] if item["bug_id"] == bug["bug_id"]]})

    if operation == "update_bug_report":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        bug = next((item for item in registry["bugs"] if item["bug_id"] == arguments["bug_id"]), None)
        if bug is None:
            return _fail("not_found", "$.bug_id", "Bug not found.")
        changed = []
        for field in ("status", "severity", "description"):
            if field in arguments:
                bug[field] = arguments[field]
                changed.append(field)
        if not changed:
            return _fail("no_changes", "$", "At least one mutable field is required.")
        _save(path, registry)
        return _ok({"bug_id": bug["bug_id"], "status": bug["status"], "severity": bug["severity"], "changed_fields": changed})

    if operation == "get_stats":
        registry = _registry(root)
        by_status = {}
        by_severity = {}
        for bug in registry["bugs"]:
            by_status[bug["status"]] = by_status.get(bug["status"], 0) + 1
            by_severity[bug["severity"]] = by_severity.get(bug["severity"], 0) + 1
        return _ok({"bug_count": len(registry["bugs"]), "test_case_count": len(registry["test_cases"]), "link_count": len(registry["links"]), "comment_count": len(registry["comments"]), "bugs_by_status": by_status, "bugs_by_severity": by_severity})

    if operation == "analyze_fix_area":
        registry = _registry(root)
        bug = next((item for item in registry["bugs"] if item["bug_id"] == arguments["bug_id"]), None)
        if bug is None:
            return _fail("not_found", "$.bug_id", "Bug not found.")
        component_paths = {
            "checkout-api": ["services/purchase_orders/create.py", "services/purchase_orders/idempotency.py"],
            "authentication": ["web/auth/session.ts", "services/auth/refresh.py"],
            "auth-session": ["web/auth/session.ts", "services/auth/refresh.py"],
            "supplier-upload": ["services/suppliers/uploads.py", "web/suppliers/certificates.tsx"],
        }
        paths = component_paths.get(bug["component"], ["components/" + bug["component"]])
        related_tests = [item["test_case_id"] for item in registry["links"] if item["bug_id"] == bug["bug_id"]]
        return _ok({"bug_id": bug["bug_id"], "component": bug["component"], "candidate_paths": paths, "related_test_case_ids": related_tests, "risk": bug["severity"]})

    if operation == "list_test_cases":
        items = _registry(root)["test_cases"]
        if arguments.get("component"):
            items = [item for item in items if item["component"] == arguments["component"]]
        return _ok({"items": items, "count": len(items)})

    if operation == "get_test_case":
        registry = _registry(root)
        test_case = next((item for item in registry["test_cases"] if item["test_case_id"] == arguments["test_case_id"]), None)
        if test_case is None:
            return _fail("not_found", "$.test_case_id", "Test case not found.")
        executions = []
        for path in sorted((root / "raw" / "test_runs").glob("*.json")):
            run = _load(path)
            result = next((item for item in run["tests"] if item["test_case_id"] == test_case["test_case_id"]), None)
            if result:
                executions.append({"run_id": run["run_id"], "status": result["status"], "error": result["error"]})
        return _ok({"test_case": test_case, "executions": executions})

    if operation == "list_test_case_links":
        links = _registry(root)["links"]
        if arguments.get("bug_id"):
            links = [item for item in links if item["bug_id"] == arguments["bug_id"]]
        if arguments.get("test_case_id"):
            links = [item for item in links if item["test_case_id"] == arguments["test_case_id"]]
        return _ok({"items": links, "count": len(links)})

    if operation == "get_test_reports_overview":
        runs = [_load(path) for path in sorted((root / "raw" / "test_runs").glob("*.json"))]
        passed = sum(run["summary"]["passed"] for run in runs)
        failed = sum(run["summary"]["failed"] for run in runs)
        total = passed + failed
        return _ok({"run_count": len(runs), "passed": passed, "failed": failed, "pass_rate": passed / total if total else 0, "latest_commit": runs[-1]["commit"] if runs else ""})

    if operation == "check_config_drift":
        current = _load(root / "raw" / "config" / "runtime_config.json")
        baseline = _load(root / "derived" / "config_baseline.json")
        items = [{"field": key, "expected": json.dumps(baseline[key]), "actual": json.dumps(current.get(key)), "matches": current.get(key) == baseline[key]} for key in baseline]
        return _ok({"items": items, "drift_count": sum(1 for item in items if not item["matches"]), "compliant": all(item["matches"] for item in items)})

    if operation == "parse_junit_report":
        import xml.etree.ElementTree as ET
        path = root / "raw" / "open_source" / "jenkins_junit_error_details.xml"
        tree = ET.parse(path)
        suites = tree.getroot()
        suite_nodes = [suites] if suites.tag == "testsuite" else list(suites.iter("testsuite"))
        testcases = list(suites.iter("testcase"))
        failures = sum(1 for case in testcases if case.find("failure") is not None or case.find("error") is not None)
        skipped = sum(1 for case in testcases if case.find("skipped") is not None)
        return _ok({"file": path.relative_to(root).as_posix(), "suite_count": len(suite_nodes), "test_count": len(testcases), "failure_count": failures, "skipped_count": skipped})

    if operation == "compare_sarif_reference":
        reference = _load(root / "raw" / "open_source" / "microsoft_sarif_tutorial.sarif")
        current = _load(root / "raw" / "security" / "scan.sarif")
        return _ok({"reference_version": reference["version"], "current_version": current["version"], "reference_tool": reference["runs"][0]["tool"]["driver"]["name"], "current_tool": current["runs"][0]["tool"]["driver"]["name"], "compatible": reference["version"] == current["version"]})

    if operation == "list_comments":
        items = _registry(root)["comments"]
        if arguments.get("bug_id"):
            items = [item for item in items if item["bug_id"] == arguments["bug_id"]]
        return _ok({"items": items, "count": len(items)})

    if operation == "create_test_case":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        if any(item["test_case_id"] == arguments["test_case_id"] for item in registry["test_cases"]):
            return _fail("already_exists", "$.test_case_id", "Test case already exists.")
        item = {"test_case_id": arguments["test_case_id"], "name": arguments["name"], "component": arguments["component"]}
        registry["test_cases"].append(item)
        _save(path, registry)
        return _ok(item)

    if operation == "list_test_suites":
        items = _registry(root)["test_suites"]
        return _ok({"items": items, "count": len(items)})

    if operation == "create_test_suite":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        if any(item["suite_id"] == arguments["suite_id"] for item in registry["test_suites"]):
            return _fail("already_exists", "$.suite_id", "Test suite already exists.")
        item = {"suite_id": arguments["suite_id"], "name": arguments["name"], "purpose": arguments["purpose"]}
        registry["test_suites"].append(item)
        _save(path, registry)
        return _ok(item)

    if operation == "list_test_case_folders":
        items = _registry(root)["test_case_folders"]
        return _ok({"items": items, "count": len(items)})

    if operation == "create_test_case_folder":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        if any(item["folder_id"] == arguments["folder_id"] for item in registry["test_case_folders"]):
            return _fail("already_exists", "$.folder_id", "Test case folder already exists.")
        parent_id = arguments.get("parent_id", "")
        if parent_id and parent_id not in {item["folder_id"] for item in registry["test_case_folders"]}:
            return _fail("not_found", "$.parent_id", "Parent folder not found.")
        item = {"folder_id": arguments["folder_id"], "name": arguments["name"], "parent_id": parent_id}
        registry["test_case_folders"].append(item)
        _save(path, registry)
        return _ok(item)

    if operation == "list_test_case_review_candidates":
        registry = _registry(root)
        linked = {item["test_case_id"] for item in registry["links"]}
        failed = {}
        for run_path in (root / "raw" / "test_runs").glob("*.json"):
            for result in _load(run_path)["tests"]:
                if result["status"] == "failed":
                    failed[result["test_case_id"]] = result["error"]
        items = [{"test_case_id": item["test_case_id"], "name": item["name"], "reason": "Failed without linked bug: " + failed[item["test_case_id"]]} for item in registry["test_cases"] if item["test_case_id"] in failed and item["test_case_id"] not in linked]
        return _ok({"items": items, "count": len(items)})

    if operation == "mark_test_case_review_flags":
        path = root / "entities" / "quality_registry.json"
        registry = _load(path)
        known = {item["test_case_id"] for item in registry["test_cases"]}
        if arguments["test_case_id"] not in known:
            return _fail("not_found", "$.test_case_id", "Test case not found.")
        item = {"test_case_id": arguments["test_case_id"], "flag": arguments["flag"], "reason": arguments["reason"], "actor": arguments["actor"]}
        registry["review_flags"] = [value for value in registry["review_flags"] if value["test_case_id"] != arguments["test_case_id"]]
        registry["review_flags"].append(item)
        _save(path, registry)
        return _ok(item)

    if operation == "get_security_events":
        items = _load(root / "raw" / "security" / "events.json")["events"]
        if arguments.get("minimum_severity"):
            ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            items = [item for item in items if ranks[item["severity"]] >= ranks[arguments["minimum_severity"]]]
        if arguments.get("category"):
            items = [item for item in items if item["category"] == arguments["category"]]
        return _ok({"items": items, "count": len(items)})

    return _fail("unsupported_operation", "$", "Unsupported tool operation.")
'''


def bugagent_environment(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = root / "workspace"
    write_json(root / "provenance" / "mcp_seed.json", seed_record("bugagent/bugagent-mcp"))
    open_sources = [
        {
            **download_source(
                "https://raw.githubusercontent.com/jenkinsci/junit-plugin/master/src/test/resources/hudson/tasks/junit/junit-report-errror-details.xml",
                workspace / "raw" / "open_source" / "jenkins_junit_error_details.xml",
            ),
            "project": "Jenkins JUnit Plugin",
            "repository": "https://github.com/jenkinsci/junit-plugin",
            "license": "MIT",
            "usage": "Real JUnit XML parser fixture with error details.",
        },
        {
            **download_source(
                "https://raw.githubusercontent.com/microsoft/sarif-tutorials/main/samples/1-Introduction/simple-example.sarif",
                workspace / "raw" / "open_source" / "microsoft_sarif_tutorial.sarif",
            ),
            "project": "Microsoft SARIF Tutorials",
            "repository": "https://github.com/microsoft/sarif-tutorials",
            "license": "CC-BY-4.0",
            "usage": "Reference SARIF 2.1.0 payload for compatibility checks.",
        },
    ]
    write_json(root / "provenance" / "open_source_provenance.json", {"sources": open_sources})
    write_json(
        workspace / "raw" / "release_candidate.json",
        {"product": "Northstar Supplier Portal", "release": "2026.08.0-rc2", "commit": "9f31c2a", "target_date": "2026-08-28", "owner": "Platform QA", "scope": ["checkout", "authentication", "supplier onboarding"]},
    )
    tests = [
        {"test_case_id": "TC-LOGIN-01", "name": "Valid user login", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-LOGIN-02", "name": "Expired session refresh", "status": "failed", "error": "Refresh request loops after 401 response", "trace_path": "raw/traces/expired_session_trace.json"},
        {"test_case_id": "TC-CHECKOUT-04", "name": "Submit purchase order", "status": "failed", "error": "Duplicate purchase order created after timeout retry", "trace_path": "raw/traces/checkout_retry_trace.json"},
        {"test_case_id": "TC-CHECKOUT-05", "name": "Reject invalid tax ID", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-SUPPLIER-03", "name": "Upload compliance certificate", "status": "failed", "error": "PDF larger than 8 MB returns generic 500", "trace_path": "raw/traces/upload_trace.json"},
        {"test_case_id": "TC-SUPPLIER-04", "name": "Approve supplier profile", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-RBAC-01", "name": "Viewer cannot approve supplier", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-RBAC-02", "name": "Admin audit log", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-API-01", "name": "Idempotency key replay", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-API-02", "name": "Rate limit response", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-UI-01", "name": "Keyboard checkout navigation", "status": "passed", "error": "", "trace_path": ""},
        {"test_case_id": "TC-UI-02", "name": "Mobile supplier form", "status": "passed", "error": "", "trace_path": ""},
    ]
    write_json(workspace / "raw" / "test_runs" / "run_rc2.json", {"run_id": "run_rc2", "suite": "release-regression", "commit": "9f31c2a", "started_at": "2026-08-23T09:30:00Z", "status": "failed", "summary": {"passed": 9, "failed": 3}, "tests": tests})
    for name, payload in {
        "expired_session_trace.json": {"test_case_id": "TC-LOGIN-02", "requests": [{"method": "POST", "url": "/session/refresh", "status": 401}, {"method": "POST", "url": "/session/refresh", "status": 401}], "console": ["refresh retry 1", "refresh retry 2"]},
        "checkout_retry_trace.json": {"test_case_id": "TC-CHECKOUT-04", "requests": [{"method": "POST", "url": "/purchase-orders", "status": 504}, {"method": "POST", "url": "/purchase-orders", "status": 201}], "database_ids": ["PO-8814", "PO-8815"]},
        "upload_trace.json": {"test_case_id": "TC-SUPPLIER-03", "requests": [{"method": "POST", "url": "/supplier/certificates", "status": 500, "content_length": 9437184}], "console": ["Unexpected response 500"]},
    }.items():
        write_json(workspace / "raw" / "traces" / name, payload)
    sarif = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": {"name": "Semgrep", "version": "1.130.0"}}, "results": [
            {"ruleId": "python.lang.security.audit.sql-injection", "level": "error", "message": {"text": "User-controlled sort field reaches a raw SQL order clause."}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "services/suppliers/query.py"}}}]},
            {"ruleId": "javascript.browser.security.insecure-cookie", "level": "warning", "message": {"text": "Session cookie is missing SameSite=Strict."}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "web/auth/session.ts"}}}]},
            {"ruleId": "generic.secrets.security.detected-private-key", "level": "note", "message": {"text": "Test fixture resembles a private key but is non-production."}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "tests/fixtures/key.txt"}}}]},
        ]}],
    }
    write_json(workspace / "raw" / "security" / "scan.sarif", sarif)
    write_json(
        workspace / "raw" / "security" / "events.json",
        {
            "events": [
                {"event_id": "sec_evt_001", "occurred_at": "2026-08-22T18:14:00Z", "category": "authentication", "severity": "medium", "actor": "supplier-admin", "action": "repeated_refresh_failure", "resource": "/session/refresh"},
                {"event_id": "sec_evt_002", "occurred_at": "2026-08-23T07:51:00Z", "category": "authorization", "severity": "high", "actor": "viewer-role", "action": "supplier_approval_denied", "resource": "/suppliers/VEGA/approve"},
                {"event_id": "sec_evt_003", "occurred_at": "2026-08-23T08:02:00Z", "category": "data_access", "severity": "low", "actor": "qa-automation", "action": "test_fixture_export", "resource": "/audit/export"},
            ]
        },
    )
    performance_rows = [
        {"sample_id": f"sample_{index:02d}", "endpoint": "/checkout", "latency_ms": latency, "status": status}
        for index, (latency, status) in enumerate([(420, "ok"), (510, "ok"), (560, "ok"), (610, "ok"), (680, "ok"), (720, "ok"), (790, "ok"), (860, "ok"), (940, "ok"), (1020, "ok"), (1180, "ok"), (1470, "timeout")], 1)
    ]
    write_csv(workspace / "raw" / "performance" / "checkout_samples.csv", performance_rows, ["sample_id", "endpoint", "latency_ms", "status"])
    write_json(
        workspace / "entities" / "quality_registry.json",
        {
            "bugs": [
                {"bug_id": "BUG-101", "title": "Checkout retry creates duplicate purchase order", "severity": "critical", "status": "open", "component": "checkout-api", "source_run_id": "run_rc2", "description": "A timeout retry creates two purchase orders with different IDs.", "classification": "data_integrity"},
                {"bug_id": "BUG-102", "title": "Expired session refresh loop", "severity": "high", "status": "open", "component": "authentication", "source_run_id": "run_rc2", "description": "Client retries refresh indefinitely after an expired token.", "classification": "functional"},
            ],
            "test_cases": [{"test_case_id": item["test_case_id"], "name": item["name"], "component": item["test_case_id"].split("-")[1].lower()} for item in tests],
            "links": [{"bug_id": "BUG-101", "test_case_id": "TC-CHECKOUT-04", "relation": "reproduced_by"}, {"bug_id": "BUG-102", "test_case_id": "TC-LOGIN-02", "relation": "reproduced_by"}],
            "comments": [{"comment_id": "comment_001", "bug_id": "BUG-101", "author": "qa-lead", "body": "Reproduced twice against rc2 with the same idempotency key."}],
            "test_suites": [{"suite_id": "suite_release", "name": "Release Regression", "purpose": "Required release candidate coverage"}, {"suite_id": "suite_security", "name": "Security Regression", "purpose": "Authentication and authorization controls"}],
            "test_case_folders": [{"folder_id": "folder_checkout", "name": "Checkout", "parent_id": ""}, {"folder_id": "folder_identity", "name": "Identity", "parent_id": ""}, {"folder_id": "folder_supplier", "name": "Supplier Onboarding", "parent_id": ""}],
            "review_flags": [],
        },
    )
    write_json(workspace / "derived" / "release_policy.json", {"minimum_test_pass_rate": 0.95, "max_blocking_security_findings": 0, "max_p95_latency_ms": 1200, "max_error_rate": 0.02})
    write_json(workspace / "raw" / "config" / "runtime_config.json", {"environment": "production", "session_cookie_samesite": "Lax", "audit_retention_days": 90, "purchase_order_idempotency": False})
    write_json(workspace / "derived" / "config_baseline.json", {"environment": "production", "session_cookie_samesite": "Strict", "audit_retention_days": 180, "purchase_order_idempotency": True})
    for directory in ("evidence", "reports"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)

    test_run_item = closed_object({"run_id": STRING, "suite": STRING, "commit": STRING, "started_at": STRING, "passed": INTEGER, "failed": INTEGER, "status": STRING}, ["run_id", "suite", "commit", "started_at", "passed", "failed", "status"])
    failure_item = closed_object({"test_case_id": STRING, "name": STRING, "error": STRING, "trace_path": STRING}, ["test_case_id", "name", "error", "trace_path"])
    finding_item = closed_object({"rule_id": STRING, "level": STRING, "message": STRING, "path": STRING}, ["rule_id", "level", "message", "path"])
    counts_schema = closed_object({"note": INTEGER, "warning": INTEGER, "error": INTEGER}, ["note", "warning", "error"])
    gate_item = closed_object({"name": STRING, "passed": BOOLEAN, "observed": NUMBER, "threshold": NUMBER}, ["name", "passed", "observed", "threshold"])
    bug_item = closed_object({"bug_id": STRING, "title": STRING, "severity": STRING, "status": STRING, "component": STRING, "source_run_id": STRING, "description": STRING, "classification": STRING}, ["bug_id", "title", "severity", "status", "component", "source_run_id", "description", "classification"])
    link_item = closed_object({"bug_id": STRING, "test_case_id": STRING, "relation": STRING}, ["bug_id", "test_case_id", "relation"])
    comment_item = closed_object({"comment_id": STRING, "bug_id": STRING, "author": STRING, "body": STRING}, ["comment_id", "bug_id", "author", "body"])
    test_case_item = closed_object({"test_case_id": STRING, "name": STRING, "component": STRING}, ["test_case_id", "name", "component"])
    execution_item = closed_object({"run_id": STRING, "status": STRING, "error": STRING}, ["run_id", "status", "error"])
    drift_item = closed_object({"field": STRING, "expected": STRING, "actual": STRING, "matches": BOOLEAN}, ["field", "expected", "actual", "matches"])
    suite_item = closed_object({"suite_id": STRING, "name": STRING, "purpose": STRING}, ["suite_id", "name", "purpose"])
    test_folder_item = closed_object({"folder_id": STRING, "name": STRING, "parent_id": STRING}, ["folder_id", "name", "parent_id"])
    review_candidate_item = closed_object({"test_case_id": STRING, "name": STRING, "reason": STRING}, ["test_case_id", "name", "reason"])
    review_flag_item = closed_object({"test_case_id": STRING, "flag": STRING, "reason": STRING, "actor": STRING}, ["test_case_id", "flag", "reason", "actor"])
    security_event_item = closed_object({"event_id": STRING, "occurred_at": STRING, "category": STRING, "severity": STRING, "actor": STRING, "action": STRING, "resource": STRING}, ["event_id", "occurred_at", "category", "severity", "actor", "action", "resource"])
    tools = [
        tool("list_test_runs", "列出发布候选版本的测试运行及通过、失败统计，用于确定后续应检查的运行。", {}, [], closed_object({"items": array_of(test_run_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("get_test_reports_failures", "读取指定测试运行中的失败用例、错误信息和对应证据轨迹路径。", {"run_id": STRING}, ["run_id"], closed_object({"run_id": STRING, "items": array_of(failure_item), "count": INTEGER}, ["run_id", "items", "count"]), BUGAGENT_RUNTIME),
        tool("create_bug_report", "从测试运行创建可跟踪的缺陷记录；会写入质量登记簿，但不会自动关联测试用例。", {"title": {"type": "string", "minLength": 5}, "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]}, "component": STRING, "source_run_id": STRING, "description": {"type": "string", "minLength": 10}}, ["title", "severity", "component", "source_run_id", "description"], closed_object({"bug_id": STRING, "status": STRING, "severity": STRING}, ["bug_id", "status", "severity"]), BUGAGENT_RUNTIME),
        tool("classify_bug", "更新缺陷的故障分类和责任组件，用于路由修复与发布风险汇总。", {"bug_id": STRING, "classification": {"type": "string", "enum": ["functional", "data_integrity", "security", "performance", "usability"]}, "component": STRING}, ["bug_id", "classification", "component"], closed_object({"bug_id": STRING, "classification": STRING, "component": STRING}, ["bug_id", "classification", "component"]), BUGAGENT_RUNTIME),
        tool("link_test_case_to_bug", "把复现用例或回归用例关联到缺陷，重复关联不会创建第二条记录。", {"bug_id": STRING, "test_case_id": STRING, "relation": {"type": "string", "enum": ["reproduced_by", "regression_for", "related"]}}, ["bug_id", "test_case_id", "relation"], closed_object({"bug_id": STRING, "test_case_id": STRING, "relation": STRING}, ["bug_id", "test_case_id", "relation"]), BUGAGENT_RUNTIME),
        tool("add_comment", "给缺陷追加带作者的审计评论，用于记录复现结论、修复说明或风险接受意见。", {"bug_id": STRING, "author": STRING, "body": {"type": "string", "minLength": 3}}, ["bug_id", "author", "body"], closed_object({"comment_id": STRING, "bug_id": STRING}, ["comment_id", "bug_id"]), BUGAGENT_RUNTIME),
        tool("get_security_results", "解析 SARIF 安全扫描文件，可按最低级别过滤，并返回各级别数量。", {"minimum_level": {"type": "string", "enum": ["note", "warning", "error"]}}, [], closed_object({"items": array_of(finding_item), "count": INTEGER, "counts": counts_schema}, ["items", "count", "counts"]), BUGAGENT_RUNTIME),
        tool("get_performance_results", "从性能采样 CSV 计算结账接口的 P95 延迟和错误率，并按发布策略给出门禁结果。", {}, [], closed_object({"sample_count": INTEGER, "p95_latency_ms": INTEGER, "error_rate": NUMBER, "latency_gate_passed": BOOLEAN, "error_rate_gate_passed": BOOLEAN}, ["sample_count", "p95_latency_ms", "error_rate", "latency_gate_passed", "error_rate_gate_passed"]), BUGAGENT_RUNTIME),
        tool("collect_compliance_evidence", "把指定缺陷、用例关联、安全扫描和性能样本打包为 ZIP 证据包，并返回成员列表和摘要。", {"bug_id": STRING, "bundle_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}}, ["bug_id", "bundle_name"], closed_object({"path": STRING, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "members": array_of(STRING)}, ["path", "sha256", "members"]), BUGAGENT_RUNTIME),
        tool("generate_release_readiness_report", "综合测试、安全、性能和开放严重缺陷生成发布准入 Markdown 报告；任一门禁失败时结论为 no_go。", {"report_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}}, ["report_name"], closed_object({"decision": {"type": "string", "enum": ["go", "no_go"]}, "checks": array_of(gate_item), "path": STRING}, ["decision", "checks", "path"]), BUGAGENT_RUNTIME),
        tool("list_bug_reports", "按状态或严重度浏览缺陷登记簿，用于发布风险盘点和后续详情查询。", {"status": {"type": "string", "enum": ["open", "investigating", "fixed", "closed"]}, "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]}}, [], closed_object({"items": array_of(bug_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("get_bug_report", "读取一个缺陷的完整记录、测试关联和审计评论，形成可追溯上下文。", {"bug_id": STRING}, ["bug_id"], closed_object({"bug": bug_item, "links": array_of(link_item), "comments": array_of(comment_item)}, ["bug", "links", "comments"]), BUGAGENT_RUNTIME),
        tool("update_bug_report", "更新缺陷状态、严重度或说明；至少提供一个要修改的字段。", {"bug_id": STRING, "status": {"type": "string", "enum": ["open", "investigating", "fixed", "closed"]}, "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]}, "description": {"type": "string", "minLength": 10}}, ["bug_id"], closed_object({"bug_id": STRING, "status": STRING, "severity": STRING, "changed_fields": array_of(STRING)}, ["bug_id", "status", "severity", "changed_fields"]), BUGAGENT_RUNTIME),
        tool("get_stats", "汇总缺陷、测试用例、关联和评论数量，并按状态和严重度统计缺陷分布。", {}, [], closed_object({"bug_count": INTEGER, "test_case_count": INTEGER, "link_count": INTEGER, "comment_count": INTEGER, "bugs_by_status": {"type": "object", "additionalProperties": INTEGER}, "bugs_by_severity": {"type": "object", "additionalProperties": INTEGER}}, ["bug_count", "test_case_count", "link_count", "comment_count", "bugs_by_status", "bugs_by_severity"]), BUGAGENT_RUNTIME),
        tool("analyze_fix_area", "根据缺陷组件、严重度和已关联测试，给出候选修复文件及回归用例范围。", {"bug_id": STRING}, ["bug_id"], closed_object({"bug_id": STRING, "component": STRING, "candidate_paths": array_of(STRING), "related_test_case_ids": array_of(STRING), "risk": STRING}, ["bug_id", "component", "candidate_paths", "related_test_case_ids", "risk"]), BUGAGENT_RUNTIME),
        tool("list_test_cases", "列出质量登记簿中的测试用例，可按责任组件筛选。", {"component": STRING}, [], closed_object({"items": array_of(test_case_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("get_test_case", "读取测试用例及它在所有本地测试运行中的执行结果。", {"test_case_id": STRING}, ["test_case_id"], closed_object({"test_case": test_case_item, "executions": array_of(execution_item)}, ["test_case", "executions"]), BUGAGENT_RUNTIME),
        tool("list_test_case_links", "查询缺陷和测试用例之间的复现、回归或相关关系，可按任一端过滤。", {"bug_id": STRING, "test_case_id": STRING}, [], closed_object({"items": array_of(link_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("get_test_reports_overview", "汇总全部测试运行的通过、失败、通过率和最近提交。", {}, [], closed_object({"run_count": INTEGER, "passed": INTEGER, "failed": INTEGER, "pass_rate": NUMBER, "latest_commit": STRING}, ["run_count", "passed", "failed", "pass_rate", "latest_commit"]), BUGAGENT_RUNTIME),
        tool("check_config_drift", "逐项比较候选环境实际配置与生产基线，返回漂移数量和合规结论。", {}, [], closed_object({"items": array_of(drift_item), "drift_count": INTEGER, "compliant": BOOLEAN}, ["items", "drift_count", "compliant"]), BUGAGENT_RUNTIME),
        tool("parse_junit_report", "解析从 Jenkins JUnit Plugin 开源仓库下载的真实 XML 样本，统计测试套件、用例、失败和跳过数量。", {}, [], closed_object({"file": STRING, "suite_count": INTEGER, "test_count": INTEGER, "failure_count": INTEGER, "skipped_count": INTEGER}, ["file", "suite_count", "test_count", "failure_count", "skipped_count"]), BUGAGENT_RUNTIME),
        tool("compare_sarif_reference", "比较当前安全扫描与 Microsoft 开源 SARIF 样例的版本和生成工具，确认格式兼容性。", {}, [], closed_object({"reference_version": STRING, "current_version": STRING, "reference_tool": STRING, "current_tool": STRING, "compatible": BOOLEAN}, ["reference_version", "current_version", "reference_tool", "current_tool", "compatible"]), BUGAGENT_RUNTIME),
        tool("list_comments", "查询全部缺陷审计评论，或只返回指定缺陷的评论。", {"bug_id": STRING}, [], closed_object({"items": array_of(comment_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("create_test_case", "在质量登记簿中创建新的稳定测试用例定义；重复 ID 会被拒绝。", {"test_case_id": {"type": "string", "pattern": "^[A-Z0-9-]+$"}, "name": {"type": "string", "minLength": 3}, "component": STRING}, ["test_case_id", "name", "component"], test_case_item, BUGAGENT_RUNTIME),
        tool("list_test_suites", "列出按发布或安全目标组织的测试套件。", {}, [], closed_object({"items": array_of(suite_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("create_test_suite", "创建新的测试套件及其验证目的；重复 ID 会被拒绝。", {"suite_id": {"type": "string", "pattern": "^[a-z0-9_]+$"}, "name": {"type": "string", "minLength": 3}, "purpose": {"type": "string", "minLength": 5}}, ["suite_id", "name", "purpose"], suite_item, BUGAGENT_RUNTIME),
        tool("list_test_case_folders", "列出用于组织测试用例的文件夹及其父目录关系。", {}, [], closed_object({"items": array_of(test_folder_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("create_test_case_folder", "创建测试用例文件夹，可挂在现有父目录下；不存在的父目录会被拒绝。", {"folder_id": {"type": "string", "pattern": "^[a-z0-9_]+$"}, "name": {"type": "string", "minLength": 1}, "parent_id": STRING}, ["folder_id", "name"], test_folder_item, BUGAGENT_RUNTIME),
        tool("list_test_case_review_candidates", "找出测试运行中失败但尚未关联缺陷的用例，作为测试资产复核候选。", {}, [], closed_object({"items": array_of(review_candidate_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
        tool("mark_test_case_review_flags", "给测试用例设置复核标记、原因和操作人，用于补充覆盖或重构自动化。", {"test_case_id": STRING, "flag": {"type": "string", "enum": ["needs_bug_link", "flaky", "needs_coverage", "obsolete"]}, "reason": {"type": "string", "minLength": 3}, "actor": STRING}, ["test_case_id", "flag", "reason", "actor"], review_flag_item, BUGAGENT_RUNTIME),
        tool("get_security_events", "按最低严重度和事件类别查询候选版本期间的认证、授权和数据访问审计事件。", {"minimum_severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]}, "category": {"type": "string", "enum": ["authentication", "authorization", "data_access"]}}, [], closed_object({"items": array_of(security_event_item), "count": INTEGER}, ["items", "count"]), BUGAGENT_RUNTIME),
    ]
    environment = {
        "schema_version": "1.0",
        "environment_id": "bugagent_release_quality_gate_001",
        "name": "bugAgent 发布质量门禁工作区",
        "description": "面向供应商门户 rc2 发布候选版本的质量工程环境，组合测试运行、Playwright 网络轨迹、SARIF 安全结果、性能样本、缺陷登记簿、发布策略以及可交付审计证据。",
        "resources": [
            resource("release_candidate", "发布候选信息", "产品、版本、提交、计划日期和测试范围。", "raw", "file", "raw/release_candidate.json", "json", False),
            resource("test_runs", "测试运行结果", "发布回归运行、用例状态、失败信息和证据轨迹引用。", "raw", "file_collection", "raw/test_runs/*.json", "json", False),
            resource("playwright_traces", "浏览器测试轨迹", "失败用例的网络请求、控制台和业务对象证据。", "raw", "file_collection", "raw/traces/*.json", "json", False),
            resource("security_scan", "安全扫描结果", "Semgrep 输出的 SARIF 2.1.0 静态安全发现。", "raw", "file", "raw/security/scan.sarif", "sarif", False),
            resource("security_events", "安全审计事件", "发布候选期间的认证、授权和数据访问事件。", "raw", "file", "raw/security/events.json", "json", False),
            resource("performance_samples", "性能采样", "结账接口逐请求延迟和结果状态。", "raw", "file", "raw/performance/checkout_samples.csv", "csv", False),
            resource("runtime_config", "候选版本运行配置", "发布候选环境实际启用的安全和业务控制配置。", "raw", "file", "raw/config/runtime_config.json", "json", False),
            resource("open_source_quality_samples", "开源质量格式样本", "从 Jenkins 和 Microsoft 官方开源仓库下载的 JUnit XML 与 SARIF 文件。", "raw", "file_collection", "raw/open_source/*", "mixed", False),
            resource("quality_registry", "质量登记簿", "缺陷、测试用例、套件、文件夹、关联、复核标记和审计评论，可由工具增补。", "entity", "file", "entities/quality_registry.json", "json", True, source_resources=["test_runs", "playwright_traces"], entity_schema={"bug": {"description": "影响发布质量的缺陷。", "fields": {"bug_id": "string", "title": "string", "severity": "string", "status": "string", "component": "string", "source_run_id": "string", "description": "string", "classification": "string"}}, "test_case": {"description": "可重复执行的质量验证用例。", "fields": {"test_case_id": "string", "name": "string", "component": "string"}}, "test_suite": {"description": "按验证目标组织的测试套件。", "fields": {"suite_id": "string", "name": "string", "purpose": "string"}}, "test_case_folder": {"description": "组织测试资产的目录。", "fields": {"folder_id": "string", "name": "string", "parent_id": "string"}}, "bug_test_link": {"description": "缺陷与测试用例的可追溯关系。", "fields": {"bug_id": "string", "test_case_id": "string", "relation": "string"}}, "test_case_review_flag": {"description": "需要补充或重构测试用例的复核标记。", "fields": {"test_case_id": "string", "flag": "string", "reason": "string", "actor": "string"}}, "comment": {"description": "缺陷审计评论。", "fields": {"comment_id": "string", "bug_id": "string", "author": "string", "body": "string"}}}),
            resource("release_policy", "发布门禁策略", "测试通过率、安全发现、性能和错误率阈值。", "derived", "file", "derived/release_policy.json", "json", False, source_resources=["release_candidate"]),
            resource("config_baseline", "生产配置基线", "发布前必须满足的安全、留存和幂等配置。", "derived", "file", "derived/config_baseline.json", "json", False, source_resources=["release_candidate"]),
            resource("compliance_evidence", "合规证据包", "工具生成的缺陷与扫描证据 ZIP 包。", "output", "directory", "evidence/", "directory", True),
            resource("release_reports", "发布准入报告", "工具生成的 Markdown 发布结论及门禁明细。", "output", "directory", "reports/", "directory", True),
        ],
        "rules": [
            {"description": "quality_registry 中的 bug-test 关联必须同时引用存在的 bug_id 和 test_case_id。", "resources": ["quality_registry"]},
            {"description": "测试通过率、安全阻断项、P95 延迟、错误率或开放 critical 缺陷任一不满足策略时不得给出 go 结论。", "resources": ["test_runs", "security_scan", "performance_samples", "quality_registry", "release_policy", "release_reports"]},
            {"description": "合规证据包必须包含清单、原始扫描结果和性能样本，并返回文件 SHA-256。", "resources": ["quality_registry", "security_scan", "performance_samples", "compliance_evidence"]},
        ],
        "tools": tools,
    }
    smoke = {
        "list_test_runs": {},
        "get_test_reports_failures": {"run_id": "run_rc2"},
        "create_bug_report": {"title": "Large certificate upload returns 500", "severity": "high", "component": "supplier-upload", "source_run_id": "run_rc2", "description": "Uploading a valid 9 MB compliance PDF returns an internal server error."},
        "classify_bug": {"bug_id": "BUG-102", "classification": "functional", "component": "auth-session"},
        "link_test_case_to_bug": {"bug_id": "BUG-102", "test_case_id": "TC-LOGIN-02", "relation": "regression_for"},
        "add_comment": {"bug_id": "BUG-101", "author": "release-manager", "body": "Blocks release until idempotency behavior is corrected."},
        "get_security_results": {"minimum_level": "warning"},
        "get_performance_results": {},
        "collect_compliance_evidence": {"bug_id": "BUG-101", "bundle_name": "bug_101_evidence"},
        "generate_release_readiness_report": {"report_name": "rc2_release_gate"},
        "list_bug_reports": {"status": "open"},
        "get_bug_report": {"bug_id": "BUG-101"},
        "update_bug_report": {"bug_id": "BUG-102", "status": "investigating"},
        "get_stats": {},
        "analyze_fix_area": {"bug_id": "BUG-101"},
        "list_test_cases": {"component": "checkout"},
        "get_test_case": {"test_case_id": "TC-CHECKOUT-04"},
        "list_test_case_links": {"bug_id": "BUG-101"},
        "get_test_reports_overview": {},
        "check_config_drift": {},
        "parse_junit_report": {},
        "compare_sarif_reference": {},
        "list_comments": {"bug_id": "BUG-101"},
        "create_test_case": {"test_case_id": "TC-SEC-09", "name": "Reject unsafe sort field", "component": "supplier"},
        "list_test_suites": {},
        "create_test_suite": {"suite_id": "suite_accessibility", "name": "Accessibility Regression", "purpose": "Keyboard and screen reader release coverage"},
        "list_test_case_folders": {},
        "create_test_case_folder": {"folder_id": "folder_security", "name": "Security", "parent_id": "folder_identity"},
        "list_test_case_review_candidates": {},
        "mark_test_case_review_flags": {"test_case_id": "TC-SUPPLIER-03", "flag": "needs_bug_link", "reason": "Failure has no linked defect.", "actor": "qa-lead"},
        "get_security_events": {"minimum_severity": "medium"},
    }
    return environment, smoke


# HappyScribe 环境运行时：在多场转写中执行检索、引用核验、行动项和客户简报产出。
HAPPYSCRIBE_RUNTIME = r'''
def _load(path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))

def _save(path, value):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _ok(data):
    return {"success": True, "data": data}

def _fail(code, path, message, retryable=False):
    return {"success": False, "error": {"code": code, "path": path, "message": message, "retryable": retryable}}

def _catalog(root):
    return _load(root / "entities" / "meeting_catalog.json")

def _transcripts(root):
    return [_load(path) for path in sorted((root / "raw" / "transcripts").glob("*.json"))]

def _find_transcript(root, transcription_id):
    return next((item for item in _transcripts(root) if item["transcription_id"] == transcription_id), None)

def _stamp(seconds):
    value = int(seconds)
    return f"{value // 3600:02d}:{(value % 3600) // 60:02d}:{value % 60:02d}.000"

def _dispatch(operation, arguments, context):
    import json

    root = context.workspace_root
    catalog = _catalog(root)
    if operation == "list_transcriptions":
        items = catalog["transcriptions"]
        if not arguments.get("include_deleted", False):
            items = [item for item in items if not item.get("deleted", False)]
        if arguments.get("project_id") is not None:
            items = [item for item in items if item["project_id"] == arguments["project_id"]]
        if arguments.get("folder_path") is not None:
            items = [item for item in items if item["folder_path"] == arguments["folder_path"]]
        return _ok({"items": items, "count": len(items)})

    if operation == "search_transcriptions":
        terms = [term.lower() for term in arguments["query"].split() if term]
        items = []
        for transcript in _transcripts(root):
            for segment in transcript["segments"]:
                text = segment["text"]
                if all(term in text.lower() for term in terms):
                    items.append({"transcription_id": transcript["transcription_id"], "title": transcript["title"], "speaker": segment["speaker"], "start_seconds": segment["start_seconds"], "excerpt": text})
        return _ok({"items": items, "count": len(items)})

    if operation == "get_transcription":
        transcript = _find_transcript(root, arguments["transcription_id"])
        if transcript is None:
            return _fail("not_found", "$.transcription_id", "Transcription not found.")
        return _ok({"transcription": transcript})

    if operation == "verify_quotes":
        transcript = _find_transcript(root, arguments["transcription_id"])
        if transcript is None:
            return _fail("not_found", "$.transcription_id", "Transcription not found.")
        items = []
        for quote in arguments["quotes"]:
            match = next((segment for segment in transcript["segments"] if quote.strip().lower() in segment["text"].lower()), None)
            items.append({"quote": quote, "verified": match is not None, "speaker": match["speaker"] if match else "", "start_seconds": match["start_seconds"] if match else 0})
        return _ok({"transcription_id": transcript["transcription_id"], "items": items, "all_verified": all(item["verified"] for item in items)})

    if operation == "list_people":
        items = catalog["people"]
        if arguments.get("company_id") is not None:
            items = [item for item in items if item["company_id"] == arguments["company_id"]]
        if arguments.get("search"):
            query = arguments["search"].lower()
            items = [item for item in items if query in item["name"].lower() or query in item["email"].lower()]
        return _ok({"items": items, "count": len(items)})

    if operation == "get_company":
        company = next((item for item in catalog["companies"] if item["company_id"] == arguments["company_id"]), None)
        if company is None:
            return _fail("not_found", "$.company_id", "Company not found.")
        people = [item for item in catalog["people"] if item["company_id"] == company["company_id"]]
        transcript_ids = sorted({item["transcription_id"] for item in catalog["appearances"] if item["person_id"] in {person["person_id"] for person in people}})
        return _ok({"company": company, "people": people, "transcription_ids": transcript_ids})

    if operation == "get_folder_hierarchy":
        items = catalog["folders"]
        if arguments.get("root_path"):
            items = [item for item in items if item["path"].startswith(arguments["root_path"])]
        return _ok({"items": items, "count": len(items)})

    if operation == "extract_action_items":
        transcript = _find_transcript(root, arguments["transcription_id"])
        if transcript is None:
            return _fail("not_found", "$.transcription_id", "Transcription not found.")
        items = [{"action_id": item["action_id"], "owner": item["owner"], "due_date": item["due_date"], "description": item["description"], "evidence_start_seconds": item["evidence_start_seconds"]} for item in transcript["action_items"]]
        relative = "action_items/" + transcript["transcription_id"] + ".json"
        _save(root / relative, {"transcription_id": transcript["transcription_id"], "items": items})
        return _ok({"transcription_id": transcript["transcription_id"], "items": items, "count": len(items), "path": relative})

    if operation == "update_project_notes":
        path = root / "entities" / "meeting_catalog.json"
        project = next((item for item in catalog["projects"] if item["project_id"] == arguments["project_id"]), None)
        if project is None:
            return _fail("not_found", "$.project_id", "Project not found.")
        project["notes"] = arguments["notes"]
        project["updated_by"] = arguments["actor"]
        _save(path, catalog)
        return _ok({"project_id": project["project_id"], "notes": project["notes"], "updated_by": project["updated_by"]})

    if operation == "export_transcription_vtt":
        transcript = _find_transcript(root, arguments["transcription_id"])
        if transcript is None:
            return _fail("not_found", "$.transcription_id", "Transcription not found.")
        relative = "exports/" + arguments["file_name"] + ".vtt"
        output = root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        lines = ["WEBVTT", ""]
        for index, segment in enumerate(transcript["segments"], 1):
            lines.extend([str(index), _stamp(segment["start_seconds"]) + " --> " + _stamp(segment["end_seconds"]), segment["speaker"] + ": " + segment["text"], ""])
        output.write_text("\n".join(lines), encoding="utf-8")
        return _ok({"path": relative, "cue_count": len(transcript["segments"]), "language": transcript["language"]})

    if operation == "generate_customer_brief":
        company = next((item for item in catalog["companies"] if item["company_id"] == arguments["company_id"]), None)
        if company is None:
            return _fail("not_found", "$.company_id", "Company not found.")
        people = [item for item in catalog["people"] if item["company_id"] == company["company_id"]]
        person_names = {item["name"] for item in people}
        transcripts = [item for item in _transcripts(root) if any(name in item["participants"] for name in person_names)]
        evidence = []
        action_items = []
        for transcript in transcripts:
            for segment in transcript["segments"]:
                if segment.get("evidence_tag"):
                    evidence.append({"transcription_id": transcript["transcription_id"], "speaker": segment["speaker"], "start_seconds": segment["start_seconds"], "quote": segment["text"]})
            action_items.extend(transcript["action_items"])
        relative = "briefs/" + arguments["brief_name"] + ".md"
        output = root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Customer Brief: " + company["name"], "", "Industry: " + company["industry"], "", "## Evidence-backed findings"]
        lines.extend("- [{transcription_id} @ {start_seconds}s] {speaker}: {quote}".format(**item) for item in evidence)
        lines.extend(["", "## Open actions"])
        lines.extend("- {description} (owner: {owner}, due: {due_date})".format(**item) for item in action_items)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return _ok({"company_id": company["company_id"], "path": relative, "transcription_count": len(transcripts), "evidence_count": len(evidence), "action_item_count": len(action_items)})

    if operation == "list_projects":
        items = catalog["projects"]
        if arguments.get("status"):
            items = [item for item in items if item["status"] == arguments["status"]]
        return _ok({"items": items, "count": len(items)})

    if operation == "get_project":
        project = next((item for item in catalog["projects"] if item["project_id"] == arguments["project_id"]), None)
        if project is None:
            return _fail("not_found", "$.project_id", "Project not found.")
        transcriptions = [item for item in catalog["transcriptions"] if item["project_id"] == project["project_id"] and not item.get("deleted", False)]
        return _ok({"project": project, "transcriptions": transcriptions, "transcription_count": len(transcriptions)})

    if operation == "list_companies":
        items = catalog["companies"]
        if arguments.get("search"):
            query = arguments["search"].lower()
            items = [item for item in items if query in item["name"].lower() or query in item["domain"].lower()]
        return _ok({"items": items, "count": len(items)})

    if operation == "get_person":
        person = next((item for item in catalog["people"] if item["person_id"] == arguments["person_id"]), None)
        if person is None:
            return _fail("not_found", "$.person_id", "Person not found.")
        company = next(item for item in catalog["companies"] if item["company_id"] == person["company_id"])
        transcript_ids = [item["transcription_id"] for item in catalog["appearances"] if item["person_id"] == person["person_id"]]
        return _ok({"person": person, "company": company, "transcription_ids": transcript_ids})

    if operation == "get_workspace":
        active_transcriptions = sum(1 for item in catalog["transcriptions"] if not item.get("deleted", False))
        return _ok({"workspace_id": "northstar_revenue_research", "name": "Northstar Revenue Research", "project_count": len(catalog["projects"]), "company_count": len(catalog["companies"]), "people_count": len(catalog["people"]), "transcription_count": active_transcriptions})

    if operation == "list_calendar_events":
        events = _load(root / "raw" / "calendar_events.json")["events"]
        if arguments.get("start_time_after"):
            events = [item for item in events if item["start"] >= arguments["start_time_after"]]
        if arguments.get("start_time_before"):
            events = [item for item in events if item["start"] < arguments["start_time_before"]]
        return _ok({"items": events, "count": len(events)})

    if operation == "list_glossaries":
        glossary = _load(root / "derived" / "glossary.json")
        return _ok({"items": [{"glossary_id": 701, "name": "Identity and customer terminology", "term_count": len(glossary["terms"])}], "count": 1})

    if operation == "get_glossary":
        if arguments["glossary_id"] != 701:
            return _fail("not_found", "$.glossary_id", "Glossary not found.")
        glossary = _load(root / "derived" / "glossary.json")
        return _ok({"glossary_id": 701, "name": "Identity and customer terminology", "terms": glossary["terms"]})

    if operation == "rename_transcription":
        catalog_path = root / "entities" / "meeting_catalog.json"
        item = next((value for value in catalog["transcriptions"] if value["transcription_id"] == arguments["transcription_id"]), None)
        transcript = _find_transcript(root, arguments["transcription_id"])
        if item is None or transcript is None:
            return _fail("not_found", "$.transcription_id", "Transcription not found.")
        item["title"] = arguments["name"]
        _save(catalog_path, catalog)
        return _ok({"transcription_id": item["transcription_id"], "name": item["title"]})

    if operation == "inspect_media_asset":
        import hashlib
        path = root / "raw" / "open_source" / "whisper_jfk.flac"
        payload = path.read_bytes()
        if payload[:4] != b"fLaC":
            return _fail("invalid_format", "$", "Media asset is not FLAC.")
        streaminfo = payload[8:42]
        packed = int.from_bytes(streaminfo[10:18], "big")
        sample_rate = (packed >> 44) & 0xFFFFF
        channels = ((packed >> 41) & 0x7) + 1
        bits_per_sample = ((packed >> 36) & 0x1F) + 1
        total_samples = packed & 0xFFFFFFFFF
        duration = total_samples / sample_rate if sample_rate else 0
        return _ok({"path": path.relative_to(root).as_posix(), "format": "flac", "size": len(payload), "sample_rate_hz": sample_rate, "channels": channels, "bits_per_sample": bits_per_sample, "duration_seconds": duration, "sha256": hashlib.sha256(payload).hexdigest()})

    if operation == "create_folder":
        path = root / "entities" / "meeting_catalog.json"
        parent = arguments.get("parent_path", "")
        full_path = (parent.rstrip("/") + "/" + arguments["name"]).replace("//", "/")
        if not full_path.startswith("/"):
            full_path = "/" + full_path
        if any(item["path"] == full_path for item in catalog["folders"]):
            return _fail("already_exists", "$.name", "Folder already exists.")
        folder_id = max(item["folder_id"] for item in catalog["folders"]) + 1
        folder = {"folder_id": folder_id, "path": full_path, "location": "workspace", "transcription_count": 0}
        catalog["folders"].append(folder)
        _save(path, catalog)
        return _ok(folder)

    if operation == "rename_folder":
        path = root / "entities" / "meeting_catalog.json"
        folder = next((item for item in catalog["folders"] if item["folder_id"] == arguments["folder_id"]), None)
        if folder is None:
            return _fail("not_found", "$.folder_id", "Folder not found.")
        old_path = folder["path"]
        new_path = old_path.rsplit("/", 1)[0] + "/" + arguments["name"]
        for item in catalog["folders"]:
            if item["path"] == old_path or item["path"].startswith(old_path + "/"):
                item["path"] = new_path + item["path"][len(old_path):]
        for item in catalog["transcriptions"]:
            if item["folder_path"] == old_path or item["folder_path"].startswith(old_path + "/"):
                item["folder_path"] = new_path + item["folder_path"][len(old_path):]
        _save(path, catalog)
        return _ok({"folder_id": folder["folder_id"], "old_path": old_path, "new_path": new_path})

    if operation == "move_transcriptions":
        path = root / "entities" / "meeting_catalog.json"
        folder = next((item for item in catalog["folders"] if item["folder_id"] == arguments["folder_id"]), None)
        if folder is None:
            return _fail("not_found", "$.folder_id", "Destination folder not found.")
        items = [item for item in catalog["transcriptions"] if item["transcription_id"] in arguments["transcription_ids"]]
        if len(items) != len(set(arguments["transcription_ids"])):
            return _fail("not_found", "$.transcription_ids", "One or more transcriptions were not found.")
        for item in items:
            item["folder_path"] = folder["path"]
        for item in catalog["folders"]:
            item["transcription_count"] = sum(1 for transcript in catalog["transcriptions"] if transcript["folder_path"] == item["path"] and not transcript.get("deleted", False))
        _save(path, catalog)
        return _ok({"moved_count": len(items), "folder_id": folder["folder_id"], "folder_path": folder["path"]})

    if operation == "delete_transcriptions":
        path = root / "entities" / "meeting_catalog.json"
        items = [item for item in catalog["transcriptions"] if item["transcription_id"] in arguments["transcription_ids"]]
        if len(items) != len(set(arguments["transcription_ids"])):
            return _fail("not_found", "$.transcription_ids", "One or more transcriptions were not found.")
        for item in items:
            item["deleted"] = True
        _save(path, catalog)
        return _ok({"deleted_count": len(items), "transcription_ids": [item["transcription_id"] for item in items], "deletion_type": "soft"})

    if operation == "get_meeting_diagnostics":
        events = _load(root / "raw" / "calendar_events.json")["events"]
        event = next((item for item in events if item["event_id"] == arguments["calendar_event_id"]), None)
        if event is None:
            return _fail("not_found", "$.calendar_event_id", "Calendar event not found.")
        transcript = _find_transcript(root, event["transcription_id"])
        issues = []
        if event["recording_status"] != "recorded":
            issues.append("meeting_not_recorded")
        if transcript is None:
            issues.append("transcription_missing")
        return _ok({"event_id": event["event_id"], "title": event["title"], "recording_status": event["recording_status"], "transcription_id": event["transcription_id"], "transcription_available": transcript is not None, "segment_count": len(transcript["segments"]) if transcript else 0, "issues": issues})

    if operation == "search_helpdesk":
        articles = _load(root / "raw" / "helpdesk_articles.json")["articles"]
        terms = [term.lower() for term in arguments["query"].split()]
        scored = []
        for article in articles:
            haystack = (article["title"] + " " + article["content"]).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append({"article_id": article["article_id"], "title": article["title"], "url": article["url"], "excerpt": article["content"], "score": score})
        scored.sort(key=lambda item: (-item["score"], item["article_id"]))
        limit = arguments.get("limit", 5)
        return _ok({"items": scored[:limit], "count": min(len(scored), limit)})

    if operation == "list_conversations":
        items = _load(root / "raw" / "conversations.json")["conversations"]
        if arguments.get("project_id") is not None:
            items = [item for item in items if item["project_id"] == arguments["project_id"]]
        summaries = [{"conversation_id": item["conversation_id"], "project_id": item["project_id"], "created_at": item["created_at"], "summary": item["summary"], "outcome": item["outcome"]} for item in items]
        return _ok({"items": summaries, "count": len(summaries)})

    if operation == "get_conversation":
        items = _load(root / "raw" / "conversations.json")["conversations"]
        conversation = next((item for item in items if item["conversation_id"] == arguments["conversation_id"]), None)
        if conversation is None:
            return _fail("not_found", "$.conversation_id", "Conversation not found.")
        return _ok({"conversation": conversation})

    if operation == "list_read_files":
        items = catalog["read_files"]
        return _ok({"items": items, "count": len(items)})

    return _fail("unsupported_operation", "$", "Unsupported tool operation.")
'''


def happyscribe_environment(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = root / "workspace"
    write_json(root / "provenance" / "mcp_seed.json", seed_record("happyscribe/happyscribe"))
    open_sources = [
        {
            **download_source(
                "https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac",
                workspace / "raw" / "open_source" / "whisper_jfk.flac",
            ),
            "project": "OpenAI Whisper",
            "repository": "https://github.com/openai/whisper",
            "license": "MIT",
            "usage": "Real FLAC speech fixture for media metadata and transcription pipeline tests.",
        }
    ]
    write_json(root / "provenance" / "open_source_provenance.json", {"sources": open_sources})
    write_json(
        workspace / "raw" / "research_brief.json",
        {"project_id": 501, "project_name": "Vega Microsystems Renewal", "objective": "Prepare an evidence-backed renewal brief covering adoption blockers, security requirements, owners, and deadlines.", "customer_company_id": 201, "review_date": "2026-08-27"},
    )
    transcripts = [
        {
            "transcription_id": "tr_discovery_0812", "project_id": 501, "title": "Vega discovery call", "recorded_at": "2026-08-12T14:00:00Z", "language": "en-US", "folder_path": "/Customers/Vega/Renewal", "participants": ["Maya Chen", "Daniel Ortiz", "Priya Shah"],
            "segments": [
                {"speaker": "Maya Chen", "start_seconds": 18, "end_seconds": 42, "text": "Our operations team loses about six hours each week reconciling supplier status across spreadsheets.", "evidence_tag": "pain_point"},
                {"speaker": "Daniel Ortiz", "start_seconds": 43, "end_seconds": 66, "text": "The automation pilot reduced the reconciliation work to about ninety minutes, but adoption is uneven between regions.", "evidence_tag": "business_value"},
                {"speaker": "Priya Shah", "start_seconds": 72, "end_seconds": 101, "text": "Before renewal, security needs written confirmation of SSO enforcement and a ninety-day audit log retention period.", "evidence_tag": "requirement"},
                {"speaker": "Daniel Ortiz", "start_seconds": 108, "end_seconds": 125, "text": "I will send the SSO configuration guide by August twentieth.", "evidence_tag": "commitment"},
            ],
            "action_items": [{"action_id": "act_001", "owner": "Daniel Ortiz", "due_date": "2026-08-20", "description": "Send SSO configuration guide to Vega security.", "evidence_start_seconds": 108}],
        },
        {
            "transcription_id": "tr_security_0818", "project_id": 501, "title": "Vega security review", "recorded_at": "2026-08-18T16:30:00Z", "language": "en-US", "folder_path": "/Customers/Vega/Renewal", "participants": ["Priya Shah", "Leo Martins", "Daniel Ortiz"],
            "segments": [
                {"speaker": "Priya Shah", "start_seconds": 15, "end_seconds": 39, "text": "The remaining blocker is SCIM deprovisioning evidence for contractors who leave a supplier project.", "evidence_tag": "blocker"},
                {"speaker": "Leo Martins", "start_seconds": 45, "end_seconds": 73, "text": "The production tenant keeps audit events for one hundred and eighty days, which exceeds the ninety-day requirement.", "evidence_tag": "security_control"},
                {"speaker": "Daniel Ortiz", "start_seconds": 82, "end_seconds": 104, "text": "I will provide a screen recording of the SCIM offboarding flow by August twenty-fifth.", "evidence_tag": "commitment"},
            ],
            "action_items": [{"action_id": "act_002", "owner": "Daniel Ortiz", "due_date": "2026-08-25", "description": "Provide SCIM offboarding evidence recording.", "evidence_start_seconds": 82}],
        },
        {
            "transcription_id": "tr_internal_0821", "project_id": 501, "title": "Internal renewal strategy", "recorded_at": "2026-08-21T09:00:00Z", "language": "en-US", "folder_path": "/Internal/Revenue/Renewals", "participants": ["Daniel Ortiz", "Aisha Bell"],
            "segments": [
                {"speaker": "Aisha Bell", "start_seconds": 12, "end_seconds": 37, "text": "Usage expanded from two regions to five, so the renewal proposal should use the enterprise tier rather than the original team plan.", "evidence_tag": "commercial_signal"},
                {"speaker": "Daniel Ortiz", "start_seconds": 44, "end_seconds": 68, "text": "I will confirm active operator counts with Maya before pricing is finalized.", "evidence_tag": "commitment"},
            ],
            "action_items": [{"action_id": "act_003", "owner": "Daniel Ortiz", "due_date": "2026-08-24", "description": "Confirm active operator count with Maya.", "evidence_start_seconds": 44}],
        },
    ]
    for transcript in transcripts:
        write_json(workspace / "raw" / "transcripts" / f'{transcript["transcription_id"]}.json', transcript)
    write_json(
        workspace / "raw" / "calendar_events.json",
        {"events": [{"event_id": 901, "title": "Vega discovery call", "start": "2026-08-12T14:00:00Z", "recording_status": "recorded", "transcription_id": "tr_discovery_0812"}, {"event_id": 902, "title": "Vega security review", "start": "2026-08-18T16:30:00Z", "recording_status": "recorded", "transcription_id": "tr_security_0818"}]},
    )
    write_json(
        workspace / "raw" / "helpdesk_articles.json",
        {
            "articles": [
                {"article_id": "help_export_vtt", "title": "Export subtitles as WebVTT", "url": "https://help.happyscribe.com/export-subtitles", "content": "Open the transcription export menu, choose WebVTT, confirm the language and download the generated VTT file."},
                {"article_id": "help_retention", "title": "File retention and deletion", "url": "https://help.happyscribe.com/file-retention", "content": "Deleted files move to trash before permanent deletion. Workspace administrators control retention according to the account plan."},
                {"article_id": "help_glossary", "title": "Use glossaries for consistent terminology", "url": "https://help.happyscribe.com/glossaries", "content": "Glossaries define preferred spellings, translations and context for names and domain terminology."},
            ]
        },
    )
    write_json(
        workspace / "raw" / "conversations.json",
        {
            "conversations": [
                {"conversation_id": 801, "project_id": 501, "created_at": "2026-08-19T10:00:00Z", "summary": "Reviewed Vega security blockers and evidence deadlines.", "outcome": "bumpy_success", "messages": [{"role": "user", "content": "What remains before the Vega renewal?"}, {"role": "assistant", "content": "SCIM offboarding evidence and active operator counts remain open."}]},
                {"conversation_id": 802, "project_id": 501, "created_at": "2026-08-22T11:30:00Z", "summary": "Prepared evidence-backed renewal points.", "outcome": "success", "messages": [{"role": "user", "content": "Summarize measurable value."}, {"role": "assistant", "content": "Weekly reconciliation work fell from six hours to about ninety minutes."}]},
            ]
        },
    )
    write_json(
        workspace / "entities" / "meeting_catalog.json",
        {
            "projects": [{"project_id": 501, "name": "Vega Microsystems Renewal", "status": "active", "notes": "Security evidence and operator count remain open.", "updated_by": "revops"}],
            "companies": [{"company_id": 201, "name": "Vega Microsystems", "domain": "vega.example", "industry": "semiconductor manufacturing"}, {"company_id": 202, "name": "Northstar Systems", "domain": "northstar.example", "industry": "enterprise software"}],
            "people": [{"person_id": 301, "name": "Maya Chen", "email": "maya@vega.example", "company_id": 201, "role": "VP Operations"}, {"person_id": 302, "name": "Priya Shah", "email": "priya@vega.example", "company_id": 201, "role": "Security Lead"}, {"person_id": 303, "name": "Daniel Ortiz", "email": "daniel@northstar.example", "company_id": 202, "role": "Account Executive"}, {"person_id": 304, "name": "Leo Martins", "email": "leo@northstar.example", "company_id": 202, "role": "Security Engineer"}, {"person_id": 305, "name": "Aisha Bell", "email": "aisha@northstar.example", "company_id": 202, "role": "Revenue Operations"}],
            "transcriptions": [{"transcription_id": item["transcription_id"], "project_id": item["project_id"], "title": item["title"], "recorded_at": item["recorded_at"], "language": item["language"], "folder_path": item["folder_path"], "duration_seconds": item["segments"][-1]["end_seconds"], "deleted": False} for item in transcripts],
            "appearances": [{"person_id": person["person_id"], "transcription_id": transcript["transcription_id"]} for transcript in transcripts for person in [{"person_id": next(item["person_id"] for item in [{"person_id": 301, "name": "Maya Chen"}, {"person_id": 302, "name": "Priya Shah"}, {"person_id": 303, "name": "Daniel Ortiz"}, {"person_id": 304, "name": "Leo Martins"}, {"person_id": 305, "name": "Aisha Bell"}] if item["name"] == name)} for name in transcript["participants"]]],
            "folders": [{"folder_id": 401, "path": "/Customers", "location": "workspace", "transcription_count": 2}, {"folder_id": 402, "path": "/Customers/Vega", "location": "workspace", "transcription_count": 2}, {"folder_id": 403, "path": "/Customers/Vega/Renewal", "location": "workspace", "transcription_count": 2}, {"folder_id": 404, "path": "/Internal/Revenue/Renewals", "location": "workspace", "transcription_count": 1}],
            "read_files": [{"transcription_id": "tr_discovery_0812", "last_read_at": "2026-08-22T09:10:00Z", "read_by": "revops-agent"}, {"transcription_id": "tr_security_0818", "last_read_at": "2026-08-22T09:18:00Z", "read_by": "revops-agent"}],
        },
    )
    write_json(workspace / "derived" / "glossary.json", {"terms": [{"term": "SCIM", "preferred": "SCIM", "context": "identity lifecycle provisioning"}, {"term": "SSO", "preferred": "SSO", "context": "single sign-on"}, {"term": "Vega Microsystems", "preferred": "Vega Microsystems", "context": "customer company"}]})
    for directory in ("action_items", "exports", "briefs"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)

    transcription_item = closed_object({"transcription_id": STRING, "project_id": INTEGER, "title": STRING, "recorded_at": STRING, "language": STRING, "folder_path": STRING, "duration_seconds": INTEGER, "deleted": BOOLEAN}, ["transcription_id", "project_id", "title", "recorded_at", "language", "folder_path", "duration_seconds", "deleted"])
    search_item = closed_object({"transcription_id": STRING, "title": STRING, "speaker": STRING, "start_seconds": INTEGER, "excerpt": STRING}, ["transcription_id", "title", "speaker", "start_seconds", "excerpt"])
    segment_schema = closed_object({"speaker": STRING, "start_seconds": INTEGER, "end_seconds": INTEGER, "text": STRING, "evidence_tag": STRING}, ["speaker", "start_seconds", "end_seconds", "text", "evidence_tag"])
    action_schema = closed_object({"action_id": STRING, "owner": STRING, "due_date": STRING, "description": STRING, "evidence_start_seconds": INTEGER}, ["action_id", "owner", "due_date", "description", "evidence_start_seconds"])
    transcript_schema = closed_object({"transcription_id": STRING, "project_id": INTEGER, "title": STRING, "recorded_at": STRING, "language": STRING, "folder_path": STRING, "participants": array_of(STRING), "segments": array_of(segment_schema), "action_items": array_of(action_schema)}, ["transcription_id", "project_id", "title", "recorded_at", "language", "folder_path", "participants", "segments", "action_items"])
    quote_schema = closed_object({"quote": STRING, "verified": BOOLEAN, "speaker": STRING, "start_seconds": INTEGER}, ["quote", "verified", "speaker", "start_seconds"])
    person_schema = closed_object({"person_id": INTEGER, "name": STRING, "email": STRING, "company_id": INTEGER, "role": STRING}, ["person_id", "name", "email", "company_id", "role"])
    company_schema = closed_object({"company_id": INTEGER, "name": STRING, "domain": STRING, "industry": STRING}, ["company_id", "name", "domain", "industry"])
    folder_schema = closed_object({"folder_id": INTEGER, "path": STRING, "location": STRING, "transcription_count": INTEGER}, ["folder_id", "path", "location", "transcription_count"])
    project_schema = closed_object({"project_id": INTEGER, "name": STRING, "status": STRING, "notes": STRING, "updated_by": STRING}, ["project_id", "name", "status", "notes", "updated_by"])
    event_schema = closed_object({"event_id": INTEGER, "title": STRING, "start": STRING, "recording_status": STRING, "transcription_id": STRING}, ["event_id", "title", "start", "recording_status", "transcription_id"])
    term_schema = closed_object({"term": STRING, "preferred": STRING, "context": STRING}, ["term", "preferred", "context"])
    glossary_item = closed_object({"glossary_id": INTEGER, "name": STRING, "term_count": INTEGER}, ["glossary_id", "name", "term_count"])
    helpdesk_item = closed_object({"article_id": STRING, "title": STRING, "url": STRING, "excerpt": STRING, "score": INTEGER}, ["article_id", "title", "url", "excerpt", "score"])
    conversation_summary = closed_object({"conversation_id": INTEGER, "project_id": INTEGER, "created_at": STRING, "summary": STRING, "outcome": STRING}, ["conversation_id", "project_id", "created_at", "summary", "outcome"])
    message_schema = closed_object({"role": {"type": "string", "enum": ["user", "assistant"]}, "content": STRING}, ["role", "content"])
    conversation_schema = closed_object({"conversation_id": INTEGER, "project_id": INTEGER, "created_at": STRING, "summary": STRING, "outcome": STRING, "messages": array_of(message_schema)}, ["conversation_id", "project_id", "created_at", "summary", "outcome", "messages"])
    read_file_item = closed_object({"transcription_id": STRING, "last_read_at": STRING, "read_by": STRING}, ["transcription_id", "last_read_at", "read_by"])
    tools = [
        tool("list_transcriptions", "按项目或文件夹列出可访问转写及其时间、语言和时长，用于先确定研究范围；默认隐藏软删除记录。", {"project_id": INTEGER, "folder_path": STRING, "include_deleted": BOOLEAN}, [], closed_object({"items": array_of(transcription_item), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("search_transcriptions", "在所有转写片段中执行多词检索，返回说话人、时间戳和原文摘录。", {"query": {"type": "string", "minLength": 2}}, ["query"], closed_object({"items": array_of(search_item), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("get_transcription", "读取一场会议的完整片段、参与者和已标注行动项，供上下文复核。", {"transcription_id": STRING}, ["transcription_id"], closed_object({"transcription": transcript_schema}, ["transcription"]), HAPPYSCRIBE_RUNTIME),
        tool("verify_quotes", "逐条核验候选引用是否真实出现在指定转写中，并返回说话人和时间戳；不得用它改写原文。", {"transcription_id": STRING, "quotes": array_of({"type": "string", "minLength": 3}, min_items=1)}, ["transcription_id", "quotes"], closed_object({"transcription_id": STRING, "items": array_of(quote_schema), "all_verified": BOOLEAN}, ["transcription_id", "items", "all_verified"]), HAPPYSCRIBE_RUNTIME),
        tool("list_people", "查询会议知识图谱中的人员，可按公司或姓名、邮箱片段过滤。", {"company_id": INTEGER, "search": STRING}, [], closed_object({"items": array_of(person_schema), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("get_company", "读取公司档案、已识别人员以及他们出现过的转写 ID，用于跨会议汇总。", {"company_id": INTEGER}, ["company_id"], closed_object({"company": company_schema, "people": array_of(person_schema), "transcription_ids": array_of(STRING)}, ["company", "people", "transcription_ids"]), HAPPYSCRIBE_RUNTIME),
        tool("get_folder_hierarchy", "查看会议文件夹层级和各目录中的转写数量，可从指定根路径开始。", {"root_path": STRING}, [], closed_object({"items": array_of(folder_schema), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("extract_action_items", "从指定转写中提取带负责人、截止日和证据时间戳的行动项，并写入独立 JSON 文件。", {"transcription_id": STRING}, ["transcription_id"], closed_object({"transcription_id": STRING, "items": array_of(action_schema), "count": INTEGER, "path": STRING}, ["transcription_id", "items", "count", "path"]), HAPPYSCRIBE_RUNTIME),
        tool("update_project_notes", "用经过核验的结论整体替换项目持久备注，并记录更新人；调用前应先完成引用核验。", {"project_id": INTEGER, "notes": {"type": "string", "minLength": 10}, "actor": STRING}, ["project_id", "notes", "actor"], closed_object({"project_id": INTEGER, "notes": STRING, "updated_by": STRING}, ["project_id", "notes", "updated_by"]), HAPPYSCRIBE_RUNTIME),
        tool("export_transcription_vtt", "把一场转写按原始时间戳导出为 WebVTT 字幕文件。", {"transcription_id": STRING, "file_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}}, ["transcription_id", "file_name"], closed_object({"path": STRING, "cue_count": INTEGER, "language": STRING}, ["path", "cue_count", "language"]), HAPPYSCRIBE_RUNTIME),
        tool("generate_customer_brief", "组合该客户人员参与的多场会议，生成包含可回溯证据引用和开放行动项的 Markdown 客户简报。", {"company_id": INTEGER, "brief_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}}, ["company_id", "brief_name"], closed_object({"company_id": INTEGER, "path": STRING, "transcription_count": INTEGER, "evidence_count": INTEGER, "action_item_count": INTEGER}, ["company_id", "path", "transcription_count", "evidence_count", "action_item_count"]), HAPPYSCRIBE_RUNTIME),
        tool("list_projects", "列出会议知识工作区中的项目，可按 active 或 archived 状态筛选。", {"status": {"type": "string", "enum": ["active", "archived"]}}, [], closed_object({"items": array_of(project_schema), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("get_project", "读取项目持久备注及其当前有效转写目录，供跨会话继续研究。", {"project_id": INTEGER}, ["project_id"], closed_object({"project": project_schema, "transcriptions": array_of(transcription_item), "transcription_count": INTEGER}, ["project", "transcriptions", "transcription_count"]), HAPPYSCRIBE_RUNTIME),
        tool("list_companies", "列出会议知识图谱中的公司，可按名称或域名片段检索。", {"search": STRING}, [], closed_object({"items": array_of(company_schema), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("get_person", "读取人员、所属公司和出现过的全部转写 ID，用于人物视角的会议追溯。", {"person_id": INTEGER}, ["person_id"], closed_object({"person": person_schema, "company": company_schema, "transcription_ids": array_of(STRING)}, ["person", "company", "transcription_ids"]), HAPPYSCRIBE_RUNTIME),
        tool("get_workspace", "汇总当前会议工作区中的项目、公司、人员和有效转写数量。", {}, [], closed_object({"workspace_id": STRING, "name": STRING, "project_count": INTEGER, "company_count": INTEGER, "people_count": INTEGER, "transcription_count": INTEGER}, ["workspace_id", "name", "project_count", "company_count", "people_count", "transcription_count"]), HAPPYSCRIBE_RUNTIME),
        tool("list_calendar_events", "按开始时间范围列出会议日历事件、录制状态及关联转写。", {"start_time_after": STRING, "start_time_before": STRING}, [], closed_object({"items": array_of(event_schema), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("list_glossaries", "列出当前工作区可用于统一专有名词的术语表及词条数量。", {}, [], closed_object({"items": array_of(glossary_item), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("get_glossary", "读取指定术语表中的首选写法和上下文说明。", {"glossary_id": INTEGER}, ["glossary_id"], closed_object({"glossary_id": INTEGER, "name": STRING, "terms": array_of(term_schema)}, ["glossary_id", "name", "terms"]), HAPPYSCRIBE_RUNTIME),
        tool("rename_transcription", "修改转写目录中的显示名称，同时保持原始转写文件不变以保留证据。", {"transcription_id": STRING, "name": {"type": "string", "minLength": 3}}, ["transcription_id", "name"], closed_object({"transcription_id": STRING, "name": STRING}, ["transcription_id", "name"]), HAPPYSCRIBE_RUNTIME),
        tool("inspect_media_asset", "读取从 Whisper 开源仓库下载的真实 FLAC 文件头，返回音频参数、时长和 SHA-256。", {}, [], closed_object({"path": STRING, "format": {"type": "string", "const": "flac"}, "size": INTEGER, "sample_rate_hz": INTEGER, "channels": INTEGER, "bits_per_sample": INTEGER, "duration_seconds": NUMBER, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, ["path", "format", "size", "sample_rate_hz", "channels", "bits_per_sample", "duration_seconds", "sha256"]), HAPPYSCRIBE_RUNTIME),
        tool("create_folder", "在会议目录中创建新的工作区文件夹；重名时拒绝创建。", {"name": {"type": "string", "minLength": 1, "pattern": "^[^/]+$"}, "parent_path": STRING}, ["name"], folder_schema, HAPPYSCRIBE_RUNTIME),
        tool("rename_folder", "重命名文件夹，并同步更新其子目录和转写目录路径；不移动原始转写文件。", {"folder_id": INTEGER, "name": {"type": "string", "minLength": 1, "pattern": "^[^/]+$"}}, ["folder_id", "name"], closed_object({"folder_id": INTEGER, "old_path": STRING, "new_path": STRING}, ["folder_id", "old_path", "new_path"]), HAPPYSCRIBE_RUNTIME),
        tool("move_transcriptions", "把一组转写目录记录原子移动到指定文件夹；任一 ID 不存在时不执行。", {"transcription_ids": array_of(STRING, min_items=1), "folder_id": INTEGER}, ["transcription_ids", "folder_id"], closed_object({"moved_count": INTEGER, "folder_id": INTEGER, "folder_path": STRING}, ["moved_count", "folder_id", "folder_path"]), HAPPYSCRIBE_RUNTIME),
        tool("delete_transcriptions", "软删除一组转写目录记录，原始转写证据文件不会被永久删除。", {"transcription_ids": array_of(STRING, min_items=1)}, ["transcription_ids"], closed_object({"deleted_count": INTEGER, "transcription_ids": array_of(STRING), "deletion_type": {"type": "string", "const": "soft"}}, ["deleted_count", "transcription_ids", "deletion_type"]), HAPPYSCRIBE_RUNTIME),
        tool("get_meeting_diagnostics", "诊断日历会议是否完成录制、是否存在关联转写以及转写片段数量。", {"calendar_event_id": INTEGER}, ["calendar_event_id"], closed_object({"event_id": INTEGER, "title": STRING, "recording_status": STRING, "transcription_id": STRING, "transcription_available": BOOLEAN, "segment_count": INTEGER, "issues": array_of(STRING)}, ["event_id", "title", "recording_status", "transcription_id", "transcription_available", "segment_count", "issues"]), HAPPYSCRIBE_RUNTIME),
        tool("search_helpdesk", "检索本地帮助中心文章，回答字幕导出、文件保留和术语表等操作问题。", {"query": {"type": "string", "minLength": 2}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"], closed_object({"items": array_of(helpdesk_item), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("list_conversations", "列出历史 AI 研究对话的摘要和结果，可按项目筛选。", {"project_id": INTEGER}, [], closed_object({"items": array_of(conversation_summary), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
        tool("get_conversation", "读取一段历史研究对话的完整消息，用于复用已有分析并检查结论来源。", {"conversation_id": INTEGER}, ["conversation_id"], closed_object({"conversation": conversation_schema}, ["conversation"]), HAPPYSCRIBE_RUNTIME),
        tool("list_read_files", "列出近期被研究流程读取的转写、读取时间和执行主体。", {}, [], closed_object({"items": array_of(read_file_item), "count": INTEGER}, ["items", "count"]), HAPPYSCRIBE_RUNTIME),
    ]
    environment = {
        "schema_version": "1.0",
        "environment_id": "happyscribe_customer_research_001",
        "name": "HappyScribe 客户续约研究工作区",
        "description": "面向半导体客户续约准备的会议知识环境，包含三场跨团队转写、日历事件、项目与人物公司关系、术语表，以及可核验行动项、字幕和客户简报输出。",
        "resources": [
            resource("research_brief", "续约研究目标", "指定客户、项目、复核日期和证据型简报目标。", "raw", "file", "raw/research_brief.json", "json", False),
            resource("transcript_files", "会议转写集合", "包含说话人、逐段时间戳、原文、证据标签和行动项的会议记录。", "raw", "file_collection", "raw/transcripts/*.json", "json", False),
            resource("calendar_events", "会议日历事件", "已安排会议与转写之间的关联及录制状态。", "raw", "file", "raw/calendar_events.json", "json", False),
            resource("helpdesk_articles", "帮助中心文章", "字幕导出、文件保留和术语表使用的本地帮助文档快照。", "raw", "file", "raw/helpdesk_articles.json", "json", False),
            resource("conversation_history", "历史研究对话", "项目内既有 AI 研究对话、摘要、结果和消息记录。", "raw", "file", "raw/conversations.json", "json", False),
            resource("open_source_media", "开源语音样本", "从 Whisper 官方 MIT 仓库下载的真实 FLAC 语音测试文件。", "raw", "file_collection", "raw/open_source/*", "flac", False),
            resource("meeting_catalog", "会议知识目录", "项目、公司、人员、转写元数据、出席关系、读取记录和文件夹层级，可更新项目备注。", "entity", "file", "entities/meeting_catalog.json", "json", True, source_resources=["transcript_files", "calendar_events"], entity_schema={"project": {"description": "持续积累会议知识的客户项目。", "fields": {"project_id": "integer", "name": "string", "status": "string", "notes": "string", "updated_by": "string"}}, "company": {"description": "会议参与者所属公司。", "fields": {"company_id": "integer", "name": "string", "domain": "string", "industry": "string"}}, "person": {"description": "可跨会议识别的参与人。", "fields": {"person_id": "integer", "name": "string", "email": "string", "company_id": "integer", "role": "string"}}, "transcription": {"description": "一场转写文件的目录记录。", "fields": {"transcription_id": "string", "project_id": "integer", "title": "string", "recorded_at": "string", "language": "string", "folder_path": "string", "duration_seconds": "integer", "deleted": "boolean"}}, "appearance": {"description": "人员出现在某场转写中的关系。", "fields": {"person_id": "integer", "transcription_id": "string"}}, "read_file": {"description": "转写最近一次被研究流程读取的记录。", "fields": {"transcription_id": "string", "last_read_at": "string", "read_by": "string"}}, "folder": {"description": "用于组织转写的文件夹。", "fields": {"folder_id": "integer", "path": "string", "location": "string", "transcription_count": "integer"}}}),
            resource("domain_glossary", "领域术语表", "转写和简报中必须保持一致的身份与安全术语。", "derived", "file", "derived/glossary.json", "json", False, source_resources=["transcript_files"]),
            resource("action_item_outputs", "行动项输出", "工具从指定转写生成的可审计行动项 JSON。", "output", "directory", "action_items/", "directory", True),
            resource("subtitle_exports", "字幕导出", "工具生成的 WebVTT 字幕文件。", "output", "directory", "exports/", "directory", True),
            resource("customer_briefs", "客户简报", "组合多场会议生成的证据型 Markdown 简报。", "output", "directory", "briefs/", "directory", True),
        ],
        "rules": [
            {"description": "meeting_catalog 中的 appearance 必须引用存在的 person_id 和 transcription_id，人员 company_id 必须引用存在公司。", "resources": ["meeting_catalog"]},
            {"description": "客户简报中的事实性结论必须来自 transcript_files 中带说话人和时间戳的原文，不得把项目备注当作原始证据。", "resources": ["transcript_files", "meeting_catalog", "customer_briefs"]},
            {"description": "行动项必须同时包含负责人、截止日期、描述和 evidence_start_seconds，支持回到原始转写复核。", "resources": ["transcript_files", "action_item_outputs"]},
        ],
        "tools": tools,
    }
    smoke = {
        "list_transcriptions": {"project_id": 501},
        "search_transcriptions": {"query": "security needs"},
        "get_transcription": {"transcription_id": "tr_discovery_0812"},
        "verify_quotes": {"transcription_id": "tr_security_0818", "quotes": ["remaining blocker is SCIM deprovisioning evidence"]},
        "list_people": {"company_id": 201},
        "get_company": {"company_id": 201},
        "get_folder_hierarchy": {"root_path": "/Customers"},
        "extract_action_items": {"transcription_id": "tr_security_0818"},
        "update_project_notes": {"project_id": 501, "notes": "SCIM offboarding evidence remains due on 2026-08-25; audit retention exceeds requirement.", "actor": "revops"},
        "export_transcription_vtt": {"transcription_id": "tr_discovery_0812", "file_name": "vega_discovery"},
        "generate_customer_brief": {"company_id": 201, "brief_name": "vega_renewal_brief"},
        "list_projects": {"status": "active"},
        "get_project": {"project_id": 501},
        "list_companies": {"search": "Vega"},
        "get_person": {"person_id": 302},
        "get_workspace": {},
        "list_calendar_events": {"start_time_after": "2026-08-01T00:00:00Z", "start_time_before": "2026-09-01T00:00:00Z"},
        "list_glossaries": {},
        "get_glossary": {"glossary_id": 701},
        "rename_transcription": {"transcription_id": "tr_internal_0821", "name": "Internal Vega renewal strategy"},
        "inspect_media_asset": {},
        "create_folder": {"name": "Evidence", "parent_path": "/Customers/Vega"},
        "rename_folder": {"folder_id": 403, "name": "Renewal-2026"},
        "move_transcriptions": {"transcription_ids": ["tr_internal_0821"], "folder_id": 403},
        "delete_transcriptions": {"transcription_ids": ["tr_internal_0821"]},
        "get_meeting_diagnostics": {"calendar_event_id": 902},
        "search_helpdesk": {"query": "export WebVTT", "limit": 3},
        "list_conversations": {"project_id": 501},
        "get_conversation": {"conversation_id": 802},
        "list_read_files": {},
    }
    return environment, smoke


def expand_finstat_environment(root: Path, environment: dict[str, Any]) -> None:
    """扩充 FinStat 的基线数据，同时保留原有可复核异常。"""

    workspace = root / "workspace"
    statement_path = workspace / "raw" / "bank" / "operating_2026_07.csv"
    with statement_path.open(encoding="utf-8", newline="") as handle:
        existing_rows = list(csv.DictReader(handle))
    amount_pattern = [-1850, 7200, -4300, 12500, -980, -2400, 8800, -6100, 3200, -2750]
    extra_rows: list[dict[str, Any]] = []
    for index in range(1, 61):
        day = (index % 27) + 1
        amount = amount_pattern[(index - 1) % len(amount_pattern)]
        direction = "Customer receipt" if amount > 0 else "Vendor payment"
        extra_rows.append(
            {
                "date": f"2026-07-{day:02d}",
                "description": f"{direction} batch {index:03d}",
                "amount_minor": amount,
                "currency": "USD",
                "reference": f"AUTO-{index:03d}",
            }
        )
    all_rows = existing_rows + extra_rows
    write_csv(statement_path, all_rows, ["date", "description", "amount_minor", "currency", "reference"])
    control_path = workspace / "raw" / "bank" / "statement_control.json"
    control = read_json(control_path)
    control["closing_balance_minor"] = control["opening_balance_minor"] + sum(int(item["amount_minor"]) for item in all_rows)
    write_json(control_path, control)

    ledger_path = workspace / "entities" / "ledger.json"
    ledger = read_json(ledger_path)
    ledger.setdefault("bank_transactions", [])
    for index, row in enumerate(all_rows, 1):
        ledger["bank_transactions"].append(
            {
                "transaction_id": f"bank_{index:04d}",
                "date": row["date"],
                "description": row["description"],
                "amount_minor": int(row["amount_minor"]),
                "currency": row["currency"],
                "reference": row["reference"],
                "match_status": "matched" if index <= 8 or (index > 8 and index % 4 != 0) else "unmatched",
            }
        )
    next_entry = len(ledger["journal_entries"]) + 1
    queue_path = workspace / "entities" / "review_queue.json"
    queue = read_json(queue_path)
    next_review = len(queue["items"]) + 1
    for index, row in enumerate(extra_rows, 1):
        amount = int(row["amount_minor"])
        reference = row["reference"]
        if index % 4 != 0:
            expense_account = "5200" if index % 3 else "5100"
            debit_account = "4000" if amount > 0 else expense_account
            credit_account = "1000" if amount > 0 else "1000"
            value = abs(amount)
            lines = (
                [{"account_id": "1000", "debit_minor": value, "credit_minor": 0}, {"account_id": debit_account, "debit_minor": 0, "credit_minor": value}]
                if amount > 0
                else [{"account_id": expense_account, "debit_minor": value, "credit_minor": 0}, {"account_id": credit_account, "debit_minor": 0, "credit_minor": value}]
            )
            ledger["journal_entries"].append(
                {
                    "entry_id": f"je_{next_entry:04d}",
                    "date": row["date"],
                    "description": row["description"],
                    "source_reference": reference,
                    "posted_by": "import-batch-2026-07",
                    "lines": lines,
                }
            )
            next_entry += 1
        else:
            queue["items"].append(
                {
                    "review_id": f"review_{next_review:03d}",
                    "kind": "unmatched_bank_transaction",
                    "reference": reference,
                    "amount_minor": abs(amount),
                    "status": "open",
                    "reason": "Bank transaction requires source-document matching before posting.",
                    "resolution": "",
                    "resolved_by": "",
                }
            )
            next_review += 1
    write_json(ledger_path, ledger)
    write_json(queue_path, queue)

    documents = []
    for path in sorted((workspace / "raw" / "documents").glob("*.json")):
        documents.append(read_json(path))
    for index, row in enumerate(extra_rows, 1):
        document = {
            "document_id": f"doc_auto_{index:03d}",
            "document_type": "customer_invoice" if int(row["amount_minor"]) > 0 else "vendor_invoice",
            "reference": row["reference"],
            "counterparty": ["Helios Circuits", "Kestrel Freight", "Orion Devices", "Vega Microsystems"][index % 4],
            "issue_date": row["date"],
            "total_minor": abs(int(row["amount_minor"])),
            "currency": "USD",
        }
        documents.append(document)
        write_json(workspace / "raw" / "documents" / f'{document["document_id"]}.json', document)
    write_json(workspace / "entities" / "document_catalog.json", {"documents": documents})

    ledger_resource = next(item for item in environment["resources"] if item["resource_id"] == "ledger")
    ledger_resource["entity_schema"]["bank_transaction"] = {
        "description": "银行对账单中的一条可匹配或待复核交易。",
        "fields": {"transaction_id": "string", "date": "string", "description": "string", "amount_minor": "integer", "currency": "string", "reference": "string", "match_status": "string"},
    }
    if not any(item["resource_id"] == "document_catalog" for item in environment["resources"]):
        environment["resources"].append(
            resource(
                "document_catalog",
                "规范化凭证目录",
                "从原始发票和收据集合整理出的可追溯凭证实体。",
                "entity",
                "file",
                "entities/document_catalog.json",
                "json",
                False,
                source_resources=["source_documents"],
                entity_schema={"source_document": {"description": "一份与银行交易关联的原始业务凭证目录记录。", "fields": {"document_id": "string", "document_type": "string", "reference": "string", "counterparty": "string", "issue_date": "string", "total_minor": "integer", "currency": "string"}}},
            )
        )
        environment["rules"].append({"description": "document_catalog.reference 必须唯一，并且应能回到 source_documents 中的一份原始文件。", "resources": ["document_catalog", "source_documents"]})


def expand_bugagent_environment(root: Path, environment: dict[str, Any]) -> None:
    """增加历史测试运行、缺陷、审计事件和性能样本。"""

    workspace = root / "workspace"
    registry_path = workspace / "entities" / "quality_registry.json"
    registry = read_json(registry_path)
    component_names = ["checkout", "identity", "supplier", "billing", "audit"]
    for run_index in range(1, 5):
        tests = []
        for case_index in range(1, 21):
            component = component_names[(case_index - 1) % len(component_names)]
            status = "failed" if case_index in {4, 13, 19} else ("skipped" if case_index == 20 else "passed")
            test_id = f"TC-HIST{run_index:02d}-{case_index:02d}"
            error = "Historical regression detected in " + component if status == "failed" else ""
            tests.append({"test_case_id": test_id, "name": f"Historical {component} scenario {case_index:02d}", "status": status, "error": error, "trace_path": ""})
            registry["test_cases"].append({"test_case_id": test_id, "name": tests[-1]["name"], "component": component})
        passed = sum(1 for item in tests if item["status"] == "passed")
        failed = sum(1 for item in tests if item["status"] == "failed")
        write_json(
            workspace / "raw" / "test_runs" / f"run_history_{run_index:02d}.json",
            {"run_id": f"run_history_{run_index:02d}", "suite": "historical-regression", "commit": f"hist{run_index:02d}c7", "started_at": f"2026-08-{10 + run_index:02d}T09:00:00Z", "status": "failed" if failed else "passed", "summary": {"passed": passed, "failed": failed}, "tests": tests},
        )
        for case_index in (4, 13, 19):
            bug_id = f"BUG-{200 + run_index * 10 + case_index}"
            severity = ["medium", "high", "low"][(case_index // 4) % 3]
            component = component_names[(case_index - 1) % len(component_names)]
            registry["bugs"].append({"bug_id": bug_id, "title": f"Historical {component} regression {run_index}-{case_index}", "severity": severity, "status": "closed" if run_index < 3 else "fixed", "component": component, "source_run_id": f"run_history_{run_index:02d}", "description": f"Regression found in historical run {run_index} and tracked for the {component} area.", "classification": "functional"})
            registry["links"].append({"bug_id": bug_id, "test_case_id": f"TC-HIST{run_index:02d}-{case_index:02d}", "relation": "reproduced_by"})
            registry["comments"].append({"comment_id": f"comment_hist_{run_index:02d}_{case_index:02d}", "bug_id": bug_id, "author": "historical-qa", "body": "Closed with regression evidence retained in the historical run."})
    registry["test_suites"].extend(
        {"suite_id": f"suite_history_{index:02d}", "name": f"Historical Release {index:02d}", "purpose": "Archived release candidate coverage"}
        for index in range(1, 5)
    )
    registry["test_case_folders"].extend(
        {"folder_id": f"folder_history_{index:02d}", "name": f"Archived Release {index:02d}", "parent_id": ""}
        for index in range(1, 5)
    )
    for index in range(1, 13):
        registry["review_flags"].append({"test_case_id": f"TC-HIST{((index - 1) % 4) + 1:02d}-{((index - 1) % 20) + 1:02d}", "flag": "needs_coverage", "reason": "Historical failure should have an explicit regression assertion.", "actor": "qa-archive"})
    write_json(registry_path, registry)

    events_path = workspace / "raw" / "security" / "events.json"
    events = read_json(events_path)
    for index in range(4, 34):
        category = ["authentication", "authorization", "data_access"][index % 3]
        severity = ["low", "medium", "high"][index % 3]
        events["events"].append({"event_id": f"sec_evt_{index:03d}", "occurred_at": f"2026-08-{(index % 23) + 1:02d}T{index % 24:02d}:15:00Z", "category": category, "severity": severity, "actor": f"service-{index % 5}", "action": f"historical_{category}_event", "resource": f"/api/{category}/{index}"})
    write_json(events_path, events)

    sarif_path = workspace / "raw" / "security" / "scan.sarif"
    sarif = read_json(sarif_path)
    results = sarif["runs"][0]["results"]
    for index in range(4, 19):
        results.append({"ruleId": f"historical.rule-{index:02d}", "level": ["note", "warning", "error"][index % 3], "message": {"text": f"Historical scanner finding {index:02d}."}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": f"services/historical/module_{index:02d}.py"}}}]})
    write_json(sarif_path, sarif)

    performance_path = workspace / "raw" / "performance" / "checkout_samples.csv"
    with performance_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for index in range(13, 113):
        latency = 390 + ((index * 137) % 1150)
        status = "timeout" if index % 17 == 0 else "ok"
        rows.append({"sample_id": f"sample_{index:03d}", "endpoint": ["/checkout", "/suppliers", "/session/refresh"][index % 3], "latency_ms": latency, "status": status})
    write_csv(performance_path, rows, ["sample_id", "endpoint", "latency_ms", "status"])


def expand_happyscribe_environment(root: Path, environment: dict[str, Any]) -> None:
    """增加多项目、多客户和多场会议，使跨会议检索具备足够组合空间。"""

    workspace = root / "workspace"
    catalog_path = workspace / "entities" / "meeting_catalog.json"
    catalog = read_json(catalog_path)
    catalog["projects"].extend(
        {"project_id": project_id, "name": name, "status": "active", "notes": "Evidence collection in progress.", "updated_by": "seed-loader"}
        for project_id, name in [(502, "Orion Devices Expansion"), (503, "Helios Circuits Renewal"), (504, "Kestrel Freight Onboarding"), (505, "Northstar Platform Adoption")]
    )
    company_specs = [
        (203, "Orion Devices", "orion.example", "semiconductor design"),
        (204, "Helios Circuits", "helios.example", "electronics manufacturing"),
        (205, "Kestrel Freight", "kestrel.example", "logistics"),
        (206, "Lumen Robotics", "lumen.example", "industrial robotics"),
        (207, "Asteria Cloud", "asteria.example", "cloud infrastructure"),
        (208, "Nova Instruments", "nova.example", "test equipment"),
    ]
    catalog["companies"].extend({"company_id": cid, "name": name, "domain": domain, "industry": industry} for cid, name, domain, industry in company_specs)
    next_person_id = max(item["person_id"] for item in catalog["people"]) + 1
    person_by_company: dict[int, list[dict[str, Any]]] = {}
    for company_id, name, _domain, _industry in company_specs + [(201, "Vega Microsystems", "vega.example", "semiconductor manufacturing"), (202, "Northstar Systems", "northstar.example", "enterprise software")]:
        people = []
        for role in ("Operations Lead", "Security Lead"):
            person = {"person_id": next_person_id, "name": f"{name.split()[0]} {role.split()[0]}-{next_person_id}", "email": f"contact{next_person_id}@{name.split()[0].lower()}.example", "company_id": company_id, "role": role}
            catalog["people"].append(person)
            people.append(person)
            next_person_id += 1
        person_by_company[company_id] = people

    base_transcript_count = len(catalog["transcriptions"])
    for index in range(1, 13):
        company_id = 201 + ((index - 1) % 8)
        project_id = 501 + ((index - 1) % 5)
        company = next(item for item in catalog["companies"] if item["company_id"] == company_id)
        customer_people = person_by_company[company_id]
        account_person = next(item for item in catalog["people"] if item["person_id"] == 303)
        customer_person = customer_people[(index - 1) % len(customer_people)]
        transcription_id = f"tr_batch_{index:03d}"
        folder_path = f"/Customers/{company['name'].split()[0]}/Renewal-2026"
        participants = [customer_person["name"], account_person["name"]]
        segments = [
            {"speaker": customer_person["name"], "start_seconds": 12, "end_seconds": 32, "text": f"{company['name']} needs a clearer operating review and owner for the next phase.", "evidence_tag": "requirement"},
            {"speaker": account_person["name"], "start_seconds": 35, "end_seconds": 55, "text": "The implementation team can provide a measurable adoption report before the renewal checkpoint.", "evidence_tag": "commitment"},
            {"speaker": customer_person["name"], "start_seconds": 60, "end_seconds": 80, "text": "Security and audit evidence must be available to the customer review group.", "evidence_tag": "security_requirement"},
            {"speaker": account_person["name"], "start_seconds": 84, "end_seconds": 106, "text": f"We will schedule the {company['name']} follow-up with operations and security stakeholders.", "evidence_tag": "next_step"},
            {"speaker": customer_person["name"], "start_seconds": 110, "end_seconds": 130, "text": "The current workflow is useful, but regional adoption still needs a consistent playbook.", "evidence_tag": "risk"},
            {"speaker": account_person["name"], "start_seconds": 135, "end_seconds": 152, "text": "I will circulate a written summary with owners and due dates.", "evidence_tag": "commitment"},
        ]
        transcript = {"transcription_id": transcription_id, "project_id": project_id, "title": f"{company['name']} renewal working session {index:02d}", "recorded_at": f"2026-08-{(index % 24) + 1:02d}T{10 + index % 8:02d}:00:00Z", "language": "en-US", "folder_path": folder_path, "participants": participants, "segments": segments, "action_items": [{"action_id": f"act_batch_{index:03d}_a", "owner": account_person["name"], "due_date": f"2026-09-{(index % 20) + 1:02d}", "description": f"Send operating and security evidence for {company['name']}.", "evidence_start_seconds": 35}, {"action_id": f"act_batch_{index:03d}_b", "owner": customer_person["name"], "due_date": f"2026-09-{(index % 20) + 5:02d}", "description": "Confirm regional stakeholder list and review date.", "evidence_start_seconds": 110}]}
        write_json(workspace / "raw" / "transcripts" / f"{transcription_id}.json", transcript)
        catalog["transcriptions"].append({"transcription_id": transcription_id, "project_id": project_id, "title": transcript["title"], "recorded_at": transcript["recorded_at"], "language": "en-US", "folder_path": folder_path, "duration_seconds": 152, "deleted": False})
        for person in customer_people + [account_person]:
            catalog["appearances"].append({"person_id": person["person_id"], "transcription_id": transcription_id})
        catalog["read_files"].append({"transcription_id": transcription_id, "last_read_at": "2026-08-24T10:00:00Z", "read_by": "research-loader"})
        event_id = 1000 + index
        calendar = read_json(workspace / "raw" / "calendar_events.json")
        calendar["events"].append({"event_id": event_id, "title": transcript["title"], "start": transcript["recorded_at"], "recording_status": "recorded", "transcription_id": transcription_id})
        write_json(workspace / "raw" / "calendar_events.json", calendar)

    existing_folder_paths = {item["path"] for item in catalog["folders"]}
    for company_id, company_name, _domain, _industry in company_specs:
        folder_path = f"/Customers/{company_name.split()[0]}/Renewal-2026"
        if folder_path not in existing_folder_paths:
            folder_id = max(item["folder_id"] for item in catalog["folders"]) + 1
            catalog["folders"].append({"folder_id": folder_id, "path": folder_path, "location": "workspace", "transcription_count": sum(1 for item in catalog["transcriptions"] if item["folder_path"] == folder_path)})
            existing_folder_paths.add(folder_path)
    conversations_path = workspace / "raw" / "conversations.json"
    conversations = read_json(conversations_path)
    for index in range(3, 11):
        project_id = 501 + ((index - 1) % 5)
        conversations["conversations"].append({"conversation_id": 800 + index, "project_id": project_id, "created_at": f"2026-08-{index + 10:02d}T11:00:00Z", "summary": "Reviewed cross-meeting evidence, owners and customer follow-up risks.", "outcome": "success" if index % 3 else "bumpy_success", "messages": [{"role": "user", "content": "What evidence is still open?"}, {"role": "assistant", "content": "The latest meeting action items and security evidence require owner confirmation."}]})
    write_json(conversations_path, conversations)
    write_json(catalog_path, catalog)


def schema_validator() -> Draft202012Validator:
    validation_dir = SCHEMA_DIR / "validation"
    environment_schema = read_json(validation_dir / "environment.schema.json")
    tool_schema = read_json(validation_dir / "tool.schema.json")
    complete_schema = read_json(validation_dir / "complete_environment.schema.json")
    registry = Registry().with_resources(
        (
            (environment_schema["$id"], Resource.from_contents(environment_schema)),
            (tool_schema["$id"], Resource.from_contents(tool_schema)),
            (complete_schema["$id"], Resource.from_contents(complete_schema)),
        )
    )
    return Draft202012Validator(complete_schema, registry=registry)


def validate_resources(environment: dict[str, Any], root: Path) -> None:
    workspace = root / "workspace"
    resource_ids = {item["resource_id"] for item in environment["resources"]}
    if len(resource_ids) != len(environment["resources"]):
        raise ValueError("resource_id must be unique")
    for item in environment["resources"]:
        unknown = set(item.get("source_resources", [])) - resource_ids
        if unknown:
            raise ValueError(f"unknown source_resources: {sorted(unknown)}")
        if item["storage_type"] == "file_collection":
            if not [path for path in workspace.glob(item["path"]) if path.is_file()]:
                raise ValueError(f"file_collection is empty: {item['path']}")
        elif not (workspace / item["path"]).exists():
            raise ValueError(f"resource path does not exist: {item['path']}")
    for rule in environment["rules"]:
        unknown = set(rule["resources"]) - resource_ids
        if unknown:
            raise ValueError(f"rule references unknown resources: {sorted(unknown)}")


def validate_tools(environment: dict[str, Any], root: Path, smoke_calls: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    workspace = root / "workspace"
    reports: list[dict[str, Any]] = []
    names = [item["name"] for item in environment["tools"]]
    if len(names) != len(set(names)):
        raise ValueError("tool names must be unique")
    if set(names) != set(smoke_calls):
        raise ValueError("every tool must have exactly one smoke call")
    for item in environment["tools"]:
        namespace: dict[str, Any] = {}
        compiled = compile(item["internal"]["code"], f"<tool:{item['name']}>", "exec")
        exec(compiled, namespace)
        run = namespace.get("run")
        if not callable(run):
            raise ValueError(f"tool has no callable run: {item['name']}")
        with tempfile.TemporaryDirectory(prefix=f"validate-{item['name']}-") as temporary:
            copied = Path(temporary) / "workspace"
            shutil.copytree(workspace, copied)
            arguments = smoke_calls[item["name"]]
            Draft202012Validator(item["inputSchema"]).validate(arguments)
            result = run(arguments, SimpleNamespace(workspace_root=copied))
            Draft202012Validator(item["outputSchema"]).validate(result)
            reports.append({"tool": item["name"], "arguments": arguments, "result": result})
    return reports


def main() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True)
    validator = schema_validator()
    builders = (
        ("finstat", "finstat/finstatai", finstat_environment),
        ("bugagent", "bugagent/bugagent-mcp", bugagent_environment),
        ("happyscribe", "happyscribe/happyscribe", happyscribe_environment),
    )
    index: list[dict[str, Any]] = []
    for slug, source_mcp, builder in builders:
        root = OUTPUT_ROOT / slug
        environment, smoke_calls = builder(root)
        if slug == "finstat":
            expand_finstat_environment(root, environment)
        elif slug == "bugagent":
            expand_bugagent_environment(root, environment)
        elif slug == "happyscribe":
            expand_happyscribe_environment(root, environment)
        validator.validate(environment)
        validate_resources(environment, root)
        reports = validate_tools(environment, root, smoke_calls)
        write_json(root / "environment.json", environment)
        write_json(root / "validation.json", {"source_mcp": source_mcp, "schema_valid": True, "resource_valid": True, "tool_smoke_tests": reports})
        index.append({
            "environment_id": environment["environment_id"],
            "name": environment["name"],
            "source_mcp": source_mcp,
            "path": f"{slug}/environment.json",
            "provenance": f"{slug}/provenance/",
            "resources": len(environment["resources"]),
            "tools": len(environment["tools"]),
        })
    write_json(
        OUTPUT_ROOT / "index.json",
        {
            "schema_version": "1.0",
            "purpose": "complex_business_environments",
            "environments": index,
        },
    )
    print(f"generated {len(index)} complex environments at {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
