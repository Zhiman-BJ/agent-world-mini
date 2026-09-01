"""实体记录处理的兼容门面。

具体职责已经拆到四个模块：

* ``record_primitives``：字段、类型和命名的基础规则；
* ``record_sources``：读取 raw/entity 文件及嵌套响应；
* ``record_relations``：实体归一化、关系闭合和别名合并；
* ``record_compiler``：从已确定的记录生成环境元数据。

旧代码仍可从本模块导入函数，因此拆分不会改变 DataGen 或 Validator 的调用协议。
"""

from __future__ import annotations

from .record_primitives import *
from .record_sources import *
from .record_relations import *
from .record_compiler import *


__all__ = [name for name in globals() if not name.startswith("__")]
