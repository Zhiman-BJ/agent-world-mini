from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_API_KEYS_FILE = PROJECT_ROOT / "config" / "api_keys.env"
LEGACY_ENV_FILES = (
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / ".deepseek-harness.env",
)


def load_local_environment(
    target: MutableMapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    """Load local provider settings without overriding injected variables."""
    environment = os.environ if target is None else target
    for path in (LOCAL_API_KEYS_FILE, *LEGACY_ENV_FILES):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or not key or key.startswith("#"):
                continue
            parsed = value.strip().strip('"').strip("'")
            if parsed:
                environment.setdefault(key, parsed)
    return environment
