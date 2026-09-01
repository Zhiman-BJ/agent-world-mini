"""DataGen 的确定性数据分析函数。

这里的函数只描述文件中可以直接观察或验证的事实。业务语义最终由环境描述
Agent 声明，再由校验阶段核对；分析结果不能直接冒充最终环境契约。
"""

from .operation_candidates import build_operation_candidates, infer_closed_relations
from .entity_profiling import profile_entity_groups, profile_workspace_files
from .composition_estimation import build_composition_estimate

__all__ = [
    "build_composition_estimate",
    "build_operation_candidates",
    "infer_closed_relations",
    "profile_entity_groups",
    "profile_workspace_files",
]
