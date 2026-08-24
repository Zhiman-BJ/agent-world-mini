# Agent-World Mini 工具契约数据结构 v1.0

## 1. 权威结构

```json
{
  "toolContractVersion": "1.0",
  "toolDefinitions": [
    {
      "public": {
        "name": "cancel_appointment",
        "description": "Cancel an existing cancellable appointment and release its reserved slot. Validation failure performs no state change.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "appointment_id": {
              "type": "string",
              "format": "uuid",
              "description": "Appointment selected from the task or a previous tool result."
            },
            "reason": {
              "type": "string",
              "minLength": 1,
              "maxLength": 500
            }
          },
          "required": ["appointment_id", "reason"],
          "additionalProperties": false
        },
        "outputSchema": {
          "oneOf": [
            {
              "type": "object",
              "properties": {
                "success": {"type": "boolean", "const": true},
                "message": {"type": "string"},
                "data": {
                  "type": "object",
                  "properties": {
                    "appointment_id": {"type": "string", "format": "uuid"},
                    "status": {"type": "string", "const": "cancelled"},
                    "released_slot_id": {"type": "string", "format": "uuid"}
                  },
                  "required": ["appointment_id", "status", "released_slot_id"],
                  "additionalProperties": false
                }
              },
              "required": ["success", "message", "data"],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "success": {"type": "boolean", "const": false},
                "error": {
                  "type": "object",
                  "properties": {
                    "code": {
                      "type": "string",
                      "enum": ["not_found", "invalid_state", "conflict"]
                    },
                    "path": {"type": "string"},
                    "message": {"type": "string", "minLength": 1},
                    "retryable": {"type": "boolean"}
                  },
                  "required": ["code", "path", "message", "retryable"],
                  "additionalProperties": false
                }
              },
              "required": ["success", "error"],
              "additionalProperties": false
            }
          ]
        }
      },
      "internal": null
    }
  ]
}
```

## 2. 顶层字段

| 字段 | 含义 |
| --- | --- |
| `toolContractVersion` | 契约版本；生成器、Runtime 和任务生成器必须按同一版本读取 |
| `toolDefinitions` | 环境中所有工具的权威定义 |
| `public` | Agent 可以看到和调用的协议 |
| `internal` | Agent 不可见的运行和验证信息，待工具生成及执行代码确定后再定义 |

给 Agent 的公开工具由下面的投影产生：

```python
tools = [item["public"] for item in toolDefinitions]
```

公开结果严格只有：

```text
name / description / inputSchema / outputSchema
```

## 3. `public`

| 字段 | 含义 |
| --- | --- |
| `name` | 环境内唯一且稳定的工具名，使用小写 `snake_case` 和“业务动词 + 业务对象” |
| `description` | 说明做什么、何时使用、关键限制和主要副作用，不包含实现代码 |
| `inputSchema` | Agent 参数的完整 JSON Schema |
| `outputSchema` | 工具成功结果和业务失败结果的完整 JSON Schema |

### 3.1 `inputSchema` 如何组织

#### 根对象

一次工具调用的 `arguments` 永远是一个对象，因此 `inputSchema` 根节点固定为：

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

四个字段分别表示：

| 字段 | 含义 |
| --- | --- |
| `type: object` | 参数整体是一个 JSON object |
| `properties` | 允许出现的参数名，以及每个参数自己的 Schema |
| `required` | 调用时必须出现的参数名；可以是空数组 |
| `additionalProperties: false` | 拒绝 `properties` 没有声明的额外参数 |

没有参数的工具也必须传 `{}`，其 Schema 使用空 `properties`、空 `required` 和 `additionalProperties: false`。

#### 固定结构对象

键名和结构预先确定的对象必须完整展开，而且每一层对象都要单独声明自己的 `properties/required/additionalProperties`：

```json
{
  "type": "object",
  "properties": {
    "appointment_id": {
      "type": "string",
      "format": "uuid"
    },
    "contact": {
      "type": "object",
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "email": {"type": "string", "format": "email"},
        "phone": {"type": "string"}
      },
      "required": ["name", "email"],
      "additionalProperties": false
    }
  },
  "required": ["appointment_id", "contact"],
  "additionalProperties": false
}
```

这里：

- `appointment_id` 和整个 `contact` 对象必须出现；
- `contact.name`、`contact.email` 必须出现；
- `contact.phone` 可以不出现；
- 根对象和 `contact` 都不能出现未声明字段。

可选和可空是两件不同的事：

| 写法 | 含义 |
| --- | --- |
| 字段在 `required`，Schema 不含 `null` | 必须出现且不能为 `null` |
| 字段不在 `required`，Schema 不含 `null` | 可以省略；出现时不能为 `null` |
| 字段在 `required`，Schema 使用 `anyOf[..., null]` | 必须出现，但值可以为 `null` |
| 字段不在 `required`，Schema 使用 `anyOf[..., null]` | 可以省略，也可以显式传 `null` |

#### 动态字典

只有键名在运行时才能确定时才使用动态字典，例如“成员 UUID 到工作量分数”的映射：

```json
{
  "type": "object",
  "propertyNames": {
    "type": "string",
    "format": "uuid"
  },
  "additionalProperties": {
    "type": "number",
    "minimum": 0
  },
  "minProperties": 1
}
```

这里 `propertyNames` 约束每个动态 key，`additionalProperties` 的 Schema 约束每个动态 value。动态字典不得只写 `{"type":"object"}` 或 `additionalProperties: true`。

#### 数组

数组必须用 `items` 完整定义每一个元素。字符串 ID 数组：

```json
{
  "type": "array",
  "items": {
    "type": "string",
    "format": "uuid"
  },
  "minItems": 1,
  "maxItems": 100,
  "uniqueItems": true
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `items` | 数组中每个元素都必须遵守的 Schema |
| `minItems` | 最少元素数量；需要非空数组时设为 `1` |
| `maxItems` | 最多元素数量，用于限制调用规模 |
| `uniqueItems: true` | 元素不能重复；只在业务上表示集合时使用 |

对象数组必须把元素对象完整展开：

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "member_id": {"type": "string", "format": "uuid"},
      "role": {
        "type": "string",
        "enum": ["owner", "participant"]
      }
    },
    "required": ["member_id", "role"],
    "additionalProperties": false
  },
  "minItems": 1,
  "uniqueItems": true
}
```

数组字段是否必须出现由父对象的 `required` 决定；数组出现后能否为空由数组自己的 `minItems` 决定。数组元素没有“可选”概念，元素是否允许 `null` 由 `items` 决定：

```json
{
  "type": "array",
  "items": {
    "anyOf": [
      {"type": "string"},
      {"type": "null"}
    ]
  }
}
```

“整个数组可空”需要把 `anyOf` 写在数组外层：

```json
{
  "anyOf": [
    {
      "type": "array",
      "items": {"type": "string"}
    },
    {"type": "null"}
  ]
}
```

公开工具默认使用同构数组，即所有元素遵守同一个 `items`。不要用数组位置表达不同含义；需要固定的多个不同字段时改用带名称的固定对象。

#### 其他数据类型

| 数据 | Schema 写法 |
| --- | --- |
| 普通 ID | `{"type":"string"}` |
| UUID | `{"type":"string","format":"uuid"}` |
| 时间点 | `{"type":"string","format":"date-time"}`，必须带 offset 或 `Z` |
| 枚举 | 基础 `type` 加 `enum` |
| 精确金额 | 优先最小货币单位 `integer`，否则使用带十进制正则的 string |

选择器和简单业务参数直接放在根对象。只有参数本身是固定组合对象时才嵌套。不得要求 Agent 传入新 ID、系统时间、审计字段或派生字段。

### 3.2 `outputSchema` 如何组织

统一结果：

```text
成功：success=true + message + data
失败：success=false + error
```

`data` 内出现的固定对象、动态字典和数组，递归使用 3.1 的同一套对象与数组契约。

| 工具类型 | `data` 应存什么 |
| --- | --- |
| 搜索/列表 | `items`；需要时增加 `count`、`next_cursor` |
| 获取详情 | 一个明确命名的业务对象 |
| 计算/比较 | 关联 ID、计算值、单位和必要解释字段 |
| 创建 | 环境生成的新 ID，以及下一步需要的最小状态字段 |
| 更新/状态迁移 | 被修改实体 ID、新状态和关键联动结果 ID |
| 文件操作 | 相对 `path`、`media_type`、`size`、`checksum` |

空搜索返回成功和 `items: []`。按 ID 获取不存在的记录返回 `not_found`。

| `error` 字段 | 含义 |
| --- | --- |
| `code` | 稳定错误码；每个工具使用实际可能返回值的 `enum` |
| `path` | 对应参数的 JSONPath；不能定位到参数时使用 `$` |
| `message` | 简短错误说明，不包含 traceback 或隐藏状态 |
| `retryable` | 外部条件改变后是否值得重试 |

## 4. `internal`（待定）

本版不规定内部运行和验证字段，权威结构中暂时只保留：

```json
"internal": null
```

这个字段后续可能同时保存两类信息：

| 信息 | 使用者 | 用途 |
| --- | --- | --- |
| 运行信息 | Runtime、Validator | 绑定工具实现，并检查调用和状态边界 |
| 验证信息 | 工具生成器、Validator、ToolGraph、TaskGen | 保存能力依据、参数来源、依赖和测试样例 |

具体子字段必须等以下代码确定后再反推：

```text
工具生成器如何表达 builtin 与 Python 工具
Runtime 如何绑定和执行 handler
ToolGraph 如何建立数据依赖和状态依赖
Validator 如何检查只读、写入范围和失败回滚
TaskGen 实际需要哪些内部语义
```

在这些消费者确定前，不预先固定任何 `internal` 子字段，也不把当前 `ToolSpec` 字段直接沿用为新契约。

## 5. 已确定的调用边界

```text
输入通过 inputSchema
返回通过 outputSchema
success=false 时状态、文件和事件全部回滚
调用后完整状态必须通过状态 Schema、主键、外键和业务不变量检查
相同初始状态、参数和 rollout seed 必须得到相同结果
```
