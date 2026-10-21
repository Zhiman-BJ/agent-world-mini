"""Build a separate Kimi 0.43.0 distribution with audited compaction fixes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


UPSTREAM_COMMIT = "88a7d932f1cda95c78bf5488d34ff974a160fb08"
LOOP_ANCHOR = "\t\t\t\tlet requestAttempts = 0;\n\t\t\t\twhile (true) {"
METHOD_ANCHOR = "\t\tasync postProcessSummary(summary) {"
BLOCK_RATIO_ANCHOR = "\t\t\tconst blockRatio = Math.max(triggerRatio, DEFAULT_COMPACTION_CONFIG.blockRatio);"
SERIAL_BLOCK_RATIO = "\t\t\tconst blockRatio = triggerRatio;"
PRE_SHRINK = """\t\tpreShrinkHistoryToWindowBudget(history, instruction, compactionMaxOutputSize) {
\t\t\tconst effectiveMaxTokens = this.getEffectiveMaxContextTokens();
\t\t\tif (effectiveMaxTokens <= 0) return history;
\t\t\tconst outputReserve = compactionMaxOutputSize === void 0
\t\t\t\t? Math.floor(effectiveMaxTokens / 8)
\t\t\t\t: Math.min(compactionMaxOutputSize, Math.floor(effectiveMaxTokens / 8));
\t\t\tconst messageBudget = Math.floor((effectiveMaxTokens - outputReserve) * OVERFLOW_CONTEXT_SAFETY_RATIO) - this.requestTokens([]);
\t\t\tconst estimatedMessagesTokens = this.tokenCounting.estimateMessages(history) + this.tokenCounting.estimateMessage(createUserMessage(instruction));
\t\t\tif (messageBudget <= 0 || estimatedMessagesTokens <= messageBudget) return history;
\t\t\tconst preShrunk = takeRecentMessagesWithinTokenBudget(history, messageBudget, (message) => this.tokenCounting.estimateMessage(message));
\t\t\treturn preShrunk.length === 0 ? history : preShrunk;
\t\t}
"""


def patch_bundle(source: str, *, serialize_compaction: bool = False) -> str:
    if "preShrinkHistoryToWindowBudget" in source:
        raise ValueError("The Kimi bundle already contains pre-shrink logic")
    if source.count(LOOP_ANCHOR) != 1 or source.count(METHOD_ANCHOR) != 1:
        raise ValueError("Kimi bundle does not match the audited 0.43.0 layout")
    if serialize_compaction and source.count(BLOCK_RATIO_ANCHOR) != 1:
        raise ValueError("Kimi bundle does not match the audited compaction strategy")
    insertion = (
        "\t\t\t\tlet requestAttempts = 0;\n"
        "\t\t\t\tconst preShrunkHistory = this.preShrinkHistoryToWindowBudget(historyForModel, instruction, compactionMaxOutputSize);\n"
        "\t\t\t\tdroppedCount += historyForModel.length - preShrunkHistory.length;\n"
        "\t\t\t\thistoryForModel = preShrunkHistory;\n"
        "\t\t\t\twhile (true) {"
    )
    patched = source.replace(LOOP_ANCHOR, insertion, 1).replace(METHOD_ANCHOR, PRE_SHRINK + METHOD_ANCHOR, 1)
    if serialize_compaction:
        patched = patched.replace(BLOCK_RATIO_ANCHOR, SERIAL_BLOCK_RATIO, 1)
    return patched


def prepare_patch(source: Path, destination: Path, *, serialize_compaction: bool = False) -> Path:
    source, destination = source.resolve(), destination.absolute()
    package = json.loads((source / "package.json").read_text())
    if package.get("name") != "@moonshot-ai/kimi-code" or package.get("version") != "0.43.0":
        raise ValueError("This backport is restricted to Kimi Code 0.43.0")
    original = (source / "dist/main.mjs").read_bytes()
    patched = patch_bundle(original.decode(), serialize_compaction=serialize_compaction).encode()
    destination.mkdir(parents=True, exist_ok=False)
    for item in source.iterdir():
        if item.name == "dist":
            shutil.copytree(item, destination / item.name)
        else:
            (destination / item.name).symlink_to(item, target_is_directory=item.is_dir())
    (destination / "node_modules").symlink_to(source.parents[1], target_is_directory=True)
    executable = destination / "dist/main.mjs"
    executable.write_bytes(patched)
    changes = ["pre-shrink compaction history before its first request"]
    if serialize_compaction:
        changes.append("local fix: block at the compaction trigger, including after effective-window reduction")
    receipt = {"base_version": "0.43.0", "upstream_commit": UPSTREAM_COMMIT,
               "source": str(source), "source_sha256": hashlib.sha256(original).hexdigest(),
               "patched_sha256": hashlib.sha256(patched).hexdigest(),
               "changes": changes, "serialize_compaction": serialize_compaction,
               "original_distribution_unchanged": True}
    (destination / "patch_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--serialize-compaction", action="store_true",
                        help="Apply a local fix that waits for compaction before the next agent step")
    arguments = parser.parse_args()
    print(prepare_patch(arguments.source, arguments.destination,
                        serialize_compaction=arguments.serialize_compaction))
