"""Expose the frozen Direct rubric verifier through the original verifier API."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .verifier import (
    VerificationError, _write, prepare_verification_workspace,
    serialized_state_preparation,
)

LEGACY_FIELDS = (
    "task_id", "requirements", "non_blocking_issues", "summary", "failure_causes", "outcome",
)


def _bundle(root: Path, evidence: dict, config: dict) -> Path:
    from task_gen.test_verifier.program_verifier.environment_runtime import prepare_runtime

    with serialized_state_preparation():
        prepare_verification_workspace(root, **evidence)
        request = json.loads((root / "request.json").read_text())
        program = {
            "task_id": request["task_id"], "task_text": request["task_text"],
            "environment": request["environment"], "tools": request["tools"],
            "initial_state": request["initial"],
            "actual": {"final_state": "evidence/actual/final", "answer": "evidence/actual/answer.txt",
                       "calls": "evidence/actual/tool_calls.jsonl", "execution": "evidence/actual/execution.json"},
        }
        if request["reference"]:
            program["reference"] = {"final_state": "evidence/reference/final",
                                    "initial_state": request["reference_initial"],
                                    "answer": "evidence/reference/answer.txt",
                                    "calls": "evidence/reference/tool_calls.jsonl"}
        _write(root / "program_request.json", program)
        prepare_runtime(SimpleNamespace(runtime=config.get("runtime")), root)
    return root


def _frozen_package(verifier: Path, task: dict) -> dict:
    from task_gen.test_verifier.direct_codex_rubric.contracts import validate_package
    from task_gen.test_verifier.program_verifier.generation.model import snapshot

    manifest = json.loads((verifier.parent / "frozen_manifest.json").read_text())
    provenance = json.loads((verifier.parent / "generation/result.json").read_text())
    digest = hashlib.sha256(task["task_text"].encode()).hexdigest()
    if (provenance.get("status") != "generated"
            or provenance.get("method") != "direct_codex_rubric"
            or provenance.get("task_id") != task["task_id"]
            or provenance.get("task_sha256") != digest):
        raise VerificationError("Frozen Direct verifier does not match this exact task")
    if snapshot(verifier) != manifest:
        raise VerificationError("Frozen Direct verifier changed after generation")
    validate_package(verifier, task["task_text"])
    return manifest


def verify_execution(*, run_dir: Path, config: dict, **evidence) -> dict:
    from task_gen.test_verifier.direct_codex_rubric.application import investigate_prepared
    from task_gen.test_verifier.direct_codex_rubric.generation import generate
    from task_gen.test_verifier.program_verifier.generation.model import snapshot

    # Preserve the original flat Codex settings; the experimental engines use a
    # nested mapping. Both spellings are accepted, flat values take precedence.
    cfg = dict(config)
    codex = dict(cfg.get("codex", {}))
    for key in ("codex_home", "model", "reasoning_effort", "timeout_seconds", "attempts",
                "record_usage", "orchestration_only", "submission_preflight", "runtime_paths"):
        if key in cfg:
            codex[key] = cfg[key]
    cfg["codex"] = codex
    codex.setdefault("attempts", 3)
    cfg["fallback_enabled"] = False
    root = Path(run_dir).resolve()
    # Keep the old request/evidence paths and use a separate, candidate-blind
    # bundle for design and native final-review tests.
    _bundle(root, evidence, cfg)
    if cfg.get("verifier_dir"):
        verifier = Path(cfg["verifier_dir"]).expanduser().resolve()
    else:
        blind = {
            "task": {k: evidence["task"][k] for k in ("task_id", "task_text")},
            "environment": evidence["environment"], "initial_state": evidence["initial_state"],
            "actual_state": evidence["initial_state"], "calls": [], "answer": "",
            "execution": {"reason": "rubric_design_initial_only"},
        }
        design_evidence = _bundle(root / "direct/generation_input", blind, cfg)
        generated = generate(design_evidence, root / "direct/generation", cfg)
        if generated.get("status") != "generated":
            raise VerificationError(f"Direct rubric generation failed: {generated.get('error', generated)}")
        verifier = Path(generated["verifier_dir"]).resolve()
    manifest = _frozen_package(verifier, evidence["task"])
    _write(root / "direct/verifier_source.json", {"verifier_dir": str(verifier), "manifest": manifest})
    result = investigate_prepared(verifier, root, root,
                                  dict(cfg, legacy_output=True, readonly_paths=[str(root / "direct")]))
    if snapshot(verifier) != manifest:
        (root / "verdict.json").unlink(missing_ok=True)
        raise VerificationError("Frozen Direct verifier changed during investigation")
    _write(root / "direct_result.json", dict(result, mode="direct_codex_rubric", status="completed"))
    verdict = {key: result[key] for key in LEGACY_FIELDS}
    _write(root / "verdict.json", verdict)
    return verdict
