"""Load the published ToolGen binding, tools, state and runtime receipts."""
from __future__ import annotations

from .contracts import EnvironmentLoadInput, EnvironmentLoadOutput


def load_environment(stage_input: EnvironmentLoadInput) -> EnvironmentLoadOutput:
    """Load one published ToolGen delivery through its binding contract.

    The new delivery is the only supported input.  Runtime paths stay private
    to later execution stages and are never part of the public environment.
    """
    environment_dir = stage_input["config"].environment_dir
    binding = environment_dir / "binding.json"
    if not binding.is_file():
        raise ValueError(f"新版环境必须提供 binding.json：{binding}")

    from harness.delivery import load_delivery

    delivery = load_delivery(binding)
    environment = {
        **delivery.package.environment,
        "tools": list(delivery.package.tools),
    }
    state = delivery.package.package_root / "state"
    return {
        "environment": environment,
        "runtime": {
            "binding_path": str(delivery.binding_path),
            "initial_state": str(state),
            "runtime": delivery.runtime,
            "software": (
                {"root": str(delivery.software_root), "python": str(delivery.python_path)}
                if delivery.software_root else None
            ),
        },
    }
