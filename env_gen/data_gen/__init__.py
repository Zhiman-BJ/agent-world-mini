"""DataGen 公共入口。"""

from .config import (
    CollectionPolicy,
    DEFAULT_OSS_OUTPUT_ROOT,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_RESEARCH_MODEL,
    DEFAULT_SCHEMA_EXAMPLE,
    DEFAULT_SEED_VALIDATION_SCHEMA,
    DataGenConfig,
)
from .analysis.quality import RichnessPolicy
from .analysis.environment_quality import EnvironmentQualityPolicy
from .run_pipeline import (
    DataGenerationError,
    DataGenerationResult,
    InsufficientPublicDataError,
    run_pipeline,
)

__all__ = [
    "CollectionPolicy",
    "DEFAULT_OSS_OUTPUT_ROOT",
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_RESEARCH_MODEL",
    "DEFAULT_SCHEMA_EXAMPLE",
    "DEFAULT_SEED_VALIDATION_SCHEMA",
    "DataGenerationError",
    "DataGenerationResult",
    "DataGenConfig",
    "EnvironmentQualityPolicy",
    "InsufficientPublicDataError",
    "RichnessPolicy",
    "run_pipeline",
]
