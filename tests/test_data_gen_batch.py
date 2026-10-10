from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from env_gen.data_gen.batch import run_batch
from tests.data_gen_test_helpers import ROOT, sample_seed
from utils.search_agent.codex import CodexProcessError


def _seed(global_id: str, name: str, index: int) -> dict:
    seed = sample_seed()
    seed["global_id"] = global_id
    seed["environment"]["basic_info"]["name"] = name
    seed["environment"]["basic_info"]["index"] = index
    return seed


def _result(output_root: Path, global_id: str):
    return SimpleNamespace(
        output_dir=output_root / "rich" / global_id,
        quality_tier="rich",
        integration_tier="complete",
        elapsed_seconds=1.25,
    )


def test_batch_retries_transient_failure_and_continues_after_permanent_failure(tmp_path):
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(json.dumps([
        _seed("demo_permanent_1", "permanent", 1),
        _seed("demo_transient_2", "transient", 2),
        {"global_id": "broken_seed_3"},
    ]), encoding="utf-8")
    output_root = tmp_path / "output"
    calls: list[str] = []

    def runner(config):
        calls.append(config.global_id)
        if config.global_id == "demo_permanent_1":
            raise ValueError("deterministic failure")
        if calls.count("demo_transient_2") == 1:
            raise CodexProcessError("upstream 503", retryable=True)
        return _result(output_root, config.global_id)

    report = run_batch(
        seed_path=seed_path,
        output_root=output_root,
        schema_path=ROOT / "schemas/environment.schema.json",
        validation_schema_path=ROOT / "schemas/validation/env_seeds.schema.json",
        concurrency=1,
        max_attempts=2,
        retry_delay_seconds=0,
        pipeline_runner=runner,
    )

    assert report["summary"] == {
        "total": 3,
        "pending": 0,
        "completed": 1,
        "skipped_existing": 0,
        "invalid": 1,
        "failed": 1,
    }
    by_id = {item["global_id"]: item for item in report["results"]}
    assert len(by_id["demo_permanent_1"]["attempts"]) == 1
    assert by_id["demo_permanent_1"]["attempts"][0]["retryable"] is False
    assert len(by_id["demo_transient_2"]["attempts"]) == 2
    assert by_id["demo_transient_2"]["status"] == "completed"
    assert by_id["broken_seed_3"]["status"] == "invalid"
    assert Path(report["report_path"]).is_file()


def test_batch_skips_existing_environment_without_calling_pipeline(tmp_path):
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(
        json.dumps([_seed("demo_catalog_1", "catalog", 1)]),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"
    (output_root / "rich/demo_catalog_1").mkdir(parents=True)

    def runner(_config):
        raise AssertionError("existing environment must be skipped")

    report = run_batch(
        seed_path=seed_path,
        output_root=output_root,
        schema_path=ROOT / "schemas/environment.schema.json",
        validation_schema_path=ROOT / "schemas/validation/env_seeds.schema.json",
        pipeline_runner=runner,
    )

    assert report["summary"]["skipped_existing"] == 1
    assert report["summary"]["failed"] == 0
