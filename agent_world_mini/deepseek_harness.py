from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import ResearchBundle
from .themes import ThemeSeed


def _load_project_env(environment: dict[str, str]) -> None:
    for env_file in (Path(".env"), Path(".deepseek-harness.env")):
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            key = key.strip()
            if separator and key and not key.startswith("#"):
                environment.setdefault(key, value.strip().strip('"').strip("'"))


def _research_prompt(seed: ThemeSeed, output_file: Path) -> str:
    seed_context = {
        "theme": seed.seed_label,
        "source_url": seed.source_url,
        "description": seed.source_description,
        "documented_capabilities": list(seed.candidate_operations),
        "data_directions": list(seed.data_directions),
    }
    prompt = f"""You are the Research Agent for an Agent-World environment.

Research this prepared environment package:
{json.dumps(seed_context, ensure_ascii=False, indent=2)}

Use PowerShell and HTTP requests to inspect the starting page, search pages when needed, and query real public APIs or official structured sources. The documented capabilities are clues, not a final tool list. All needed catalog context is above; do not inspect the parent repository, previous runs, or other environment entries. Never use model memory as a data source and never invent records or relationships. Stay on this environment; if a source fails, try another source for the same theme or report the limitation, but do not switch themes.

Build a small but varied, connected sample of the real workflow. Prefer several useful entity types and evidenced relationships over many repeated leaf records. Keep useful small JSON, CSV, or text resources with their real source URLs, but do not download binaries, PDFs, archives, model weights, or bulk datasets. Stop when the main workflow is represented and further requests would mostly add duplicates.

Write exactly one UTF-8 JSON file to:
{output_file}

It must contain theme, adapter="deepseek_harness_research_agent", retrieved_at, sources, records, theme_metadata, and complexification. Each record must contain entity_type, a stable entity_id, attributes, and a source_url that supports its facts. Express relations with *_id values that exactly match another record's entity_id; use *_link records for many-to-many relations. When a small real data file is useful, include it in resources as resource_id, name, media_type, source_url, and parsed JSON or text content. You may describe useful local mutable entities in theme_metadata.environment_blueprint, but do not create final tool specs, graphs, chains, or tasks. Set theme_metadata.theme_id to {json.dumps(seed.theme_id)} and research_agent to "deepseek_harness". Do not modify project code. Keep IDs and relation targets consistent while building the file; after writing it, finish because the Python pipeline will parse it. If public data is genuinely insufficient, report the problem instead of fabricating data."""
    # dsh is launched through Windows CMD; embedded newlines terminate the
    # positional task argument. Keep the full research brief in one argument.
    return " ".join(prompt.split())


class DeepSeekHarnessResearchAgent:
    def __init__(self, timeout_seconds: int = 1800):
        self.timeout_seconds = timeout_seconds

    def gather(self, seed: ThemeSeed, output_file: Path) -> ResearchBundle:
        executable = shutil.which("dsh")
        if not executable:
            raise RuntimeError("DeepSeek Harness is not installed. Run: npm install -g @deepseek-ai/dsh")

        environment = dict(os.environ)
        _load_project_env(environment)
        if not environment.get("DEEPSEEK_API_KEY"):
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        if not environment.get("DEEPSEEK_BASE_URL"):
            raise RuntimeError("DEEPSEEK_BASE_URL is not set")
        environment["DSH_PERMISSION_MODE"] = "danger-full-access"
        environment.setdefault("DSH_TELEMETRY_MODE", "DISABLED")
        environment.setdefault("NO_COLOR", "1")

        output_file = output_file.resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        patch_template = Path(__file__).resolve().parent.parent / "deepseek_harness.patch.yml"
        with tempfile.TemporaryDirectory(prefix="agent-world-deepseek-") as temporary:
            workspace = Path(temporary)
            harness_output = workspace / "research_bundle.json"
            patch_file = workspace / "deepseek_harness.patch.yml"
            patch_file.write_text(
                patch_template.read_text(encoding="utf-8").replace(
                    "__DEEPSEEK_BASE_URL__", json.dumps(environment["DEEPSEEK_BASE_URL"])
                ),
                encoding="utf-8",
            )
            command = [
                executable,
                "--profile",
                "headless",
                "--patch",
                str(patch_file),
                _research_prompt(seed, Path("research_bundle.json")),
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=workspace,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"DeepSeek Harness research timed out after {self.timeout_seconds} seconds") from error
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()[-2000:]
                raise RuntimeError(f"DeepSeek Harness failed with exit code {result.returncode}: {detail}")
            if not harness_output.is_file():
                detail = result.stdout.strip()[-1000:]
                raise RuntimeError(f"DeepSeek Harness finished without creating research_bundle.json: {detail}")

            try:
                payload = json.loads(harness_output.read_text(encoding="utf-8"))
                bundle = ResearchBundle.from_dict(payload)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise RuntimeError(f"DeepSeek Harness produced an invalid research bundle: {error}") from error

        bundle.adapter = "deepseek_harness_research_agent"
        bundle.retrieved_at = datetime.now(timezone.utc).isoformat()
        bundle.theme_metadata["research_agent"] = "deepseek_harness"
        output_file.write_text(json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return bundle
