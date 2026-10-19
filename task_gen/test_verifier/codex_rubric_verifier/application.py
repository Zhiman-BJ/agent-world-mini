"""Reuse the Codex runtime, never a tools-generated rubric package."""
from ..codex_tool_verifier.application import apply as apply_codex
from .generation import validate_package


def apply(verifier_dir, evidence_dir, output_dir, config=None):
    validate_package(verifier_dir)
    return apply_codex(verifier_dir, evidence_dir, output_dir,
                       dict(config or {}, static_tools_enabled=False))
