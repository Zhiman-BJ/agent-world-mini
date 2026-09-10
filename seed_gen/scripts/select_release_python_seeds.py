"""Apply reviewed release profiles; keep full indexes in ori_all."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seed_gen.scripts.select_python_ref_tools import select_seed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("seed_gen/pypi_release_sources.json"))
    parser.add_argument("--check", action="store_true", help="Compare existing outputs and reports without writing")
    args = parser.parse_args()
    specs = json.loads(args.manifest.read_text(encoding="utf-8"))
    artifacts = []
    for spec in specs:
        filename = f"{spec['name']}_{spec['tag']}.json"
        source = Path("seed_gen/pypi_outputs/ori_all") / filename
        profile = Path("seed_gen/pypi_selection_profiles") / filename
        selected, report = select_seed(
            json.loads(source.read_text(encoding="utf-8")),
            json.loads(profile.read_text(encoding="utf-8")),
            input_label=source.as_posix(), profile_label=profile.as_posix(),
        )
        artifacts.extend([
            (Path("seed_gen/pypi_outputs") / filename, selected),
            (Path("seed_gen/pypi_outputs/selection_reports") / filename, report),
        ])
        print(f"{spec['name']}: {selected[0]['environment']['nums']}")
    for path, data in artifacts:
        if args.check:
            if json.loads(path.read_text(encoding="utf-8")) != data:
                raise ValueError(f"Selection differs from {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
