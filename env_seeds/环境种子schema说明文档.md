# 环境种子schema说明文档

## 1. 环境种子数据示例

```json
{
  "catalog": "smithery",
  "prepared": 140,
  "agent_organized": 140,
  "environments": [
    {
      "id": "3a787567-19f8-4eaa-837a-1f0ce01dcca0",
      "qualifiedName": "theagenttimes/news",
      "namespace": "theagenttimes",
      "slug": "news",
      "displayName": "Agent News",
      "description": "Agent News is a research and verification service for the AI-agent economy. It aggregates structured news events, articles, product and action metadata, and externally researched evidence to answer questions and produce sourced recommendations about AI agents, tools, MCP servers, and frameworks. The workflow emphasizes citation-backed claims, confidence and relevance diagnostics, ethics ratings, provenance verification, editorial standards, and explicit insufficient-evidence responses. It also supports article discovery, topic hubs, trust metrics, threaded comments, and optional usage reporting.",
      "iconUrl": "https://api.smithery.ai/servers/theagenttimes/news/icon",
      "verified": true,
      "useCount": 43392,
      "remote": true,
      "isDeployed": true,
      "unlisted": false,
      "inactive": false,
      "createdAt": "2026-02-09T15:49:42.877Z",
      "homepage": "https://theagenttimes.com",
      "bySmithery": false,
      "owner": "org_01KNBV6FPFZCF0K6T282SPGTTE",
      "score": null,
      "deploymentUrl": "https://news--theagenttimes.run.tools",
      "connections": [
        {
          "type": "http",
          "deploymentUrl": "https://news--theagenttimes.run.tools",
          "configSchema": {}
        }
      ],
      "tools": [
        {
          "name": "tat_search",
          "description": "Search The Agent Times agent-news layer across structured events, articles, and agent-action/product metadata. Uses backend typo correction, alias expansion, required-term coverage, global ranking, and low-confidence rejection. Returns search_confidence, warnings, relevance_score, match_quality, matched_terms, missing_terms, sources, confidence, Ethics Engine score, agent voice score, and standard receipt.",
          "inputSchema": {
            "type": "object",
            "properties": {
              "tag": {
                "type": "string",
                "description": "Optional tag filter"
              },
              "sort": {
                "enum": [
                  "relevance",
                  "newest"
                ],
                "type": "string",
                "description": "Article sort order"
              },
              "limit": {
                "type": "integer",
                "description": "Number of results (max 20, default 10)"
              },
              "query": {
                "type": "string",
                "description": "Short entity-rich English search query for agent-news, articles, products, actions, or events"
              },
              "topic": {
                "type": "string",
                "description": "Optional topic filter"
              },
              "intent": {
                "type": "string",
                "description": "Optional intent filter"
              },
              "section": {
                "type": "string",
                "description": "Optional article section filter"
              },
              "urgency": {
                "enum": [
                  "low",
                  "medium",
                  "high",
                  "critical"
                ],
                "type": "string",
                "description": "Optional event urgency filter"
              },
              "actionability": {
                "enum": [
                  "informational",
                  "monitor",
                  "act_now"
                ],
                "type": "string",
                "description": "Optional actionability filter"
              },
              "include_events": {
                "type": "boolean",
                "description": "Include agent event matches (default true)"
              },
              "include_articles": {
                "type": "boolean",
                "description": "Include article matches (default true)"
              },
              "include_products": {
                "type": "boolean",
                "description": "Include agent-action/product metadata matches (default true)"
              }
            }
          },
          "outputSchema": {
            "type": "object",
            "properties": {
              "text": {
                "type": "string",
                "description": "Present when the tool returns a text-only response."
              }
            }
          }
        },
        {
          "name": "tat_ask",
          "..": "..."
        }
      ],
      "dataDirections": [
        "Official product documentation, release notes, changelogs, and vendor announcements for AI-agent platforms, tools, MCP servers, and frameworks",
        "Open-source repositories, commit histories, issue trackers, package registries, licenses, and security advisories",
        "Research papers, preprints, benchmarks, model cards, datasets, and institutional lab publications",
        "Technology-news articles, interviews, opinion pieces, newsletters, and trade-publication coverage",
        "Company websites, press releases, funding announcements, investor disclosures, and acquisition records",
        "Public pricing pages, product catalogs, integration directories, API references, and marketplace listings",
        "Government regulations, legislative proposals, regulator guidance, enforcement actions, and public-policy consultations",
        "Infrastructure status pages, incident reports, vulnerability databases, and reliability disclosures",
        "Public labor-market data, job postings, workforce reports, and studies of automation's employment effects",
        "Public commerce, sales, marketing, advertising-technology, and engineering case studies involving AI agents",
        "Cryptographically signed publication records, timestamps, authorship metadata, source links, and other provenance evidence",
        "Public discussion threads, article comments, endorsements, and attributed expert or agent commentary",
        "Editorial policies, codes of conduct, correction records, trust reports, and source-quality methodologies"
      ],
      "organizationStatus": "agent_organized"
    }
  ]
}
```

### 字段简要说明

```json
{
  "catalog 目录数据来源": "smithery",
  "prepared 已准备环境条目数": 140,
  "agent_organized LLM整理成功条目数": 140,
  "environments 已准备的环境种子列表,本文件仅展示其中一个条目": [
    {
      "id Smithery内部服务记录UUID": "3a787567-19f8-4eaa-837a-1f0ce01dcca0",
      "qualifiedName Smithery唯一服务名,也是页面URL后的ID": "theagenttimes/news",
      "namespace 服务命名空间,通常是qualifiedName斜杠前部分": "theagenttimes",
      "slug 命名空间内的服务短名,通常是qualifiedName斜杠后部分": "news",
      "displayName 网站展示名": "Agent News",
      "description LLM整理后的业务与能力简介": "Agent News is a research and verification service for the AI-agent economy. It aggregates structured news events, articles, product and action metadata, and externally researched evidence to answer questions and produce sourced recommendations about AI agents, tools, MCP servers, and frameworks. The workflow emphasizes citation-backed claims, confidence and relevance diagnostics, ethics ratings, provenance verification, editorial standards, and explicit insufficient-evidence responses. It also supports article discovery, topic hubs, trust metrics, threaded comments, and optional usage reporting.",
      "iconUrl 服务图标地址": "https://api.smithery.ai/servers/theagenttimes/news/icon",
      "verified 是否经过Smithery验证": true,
      "useCount Smithery记录的使用次数快照": 43392,
      "remote 是否为远程URL型MCP": true,
      "isDeployed 是否已在Smithery目录中部署": true,
      "unlisted 是否不在公开列表展示": false,
      "inactive 是否被标记为非活跃": false,
      "createdAt Smithery目录记录创建时间,UTC": "2026-02-09T15:49:42.877Z",
      "homepage 服务或项目主页": "https://theagenttimes.com",
      "bySmithery 是否由Smithery官方发布或维护": false,
      "owner Smithery记录的所有者组织ID": "org_01KNBV6FPFZCF0K6T282SPGTTE",
      "score Smithery返回的内部评分,null表示未提供": null,
      "deploymentUrl Smithery托管的MCP访问地址": "https://news--theagenttimes.run.tools",
      "connections 可用连接方式与连接配置": [
        {
          "type 连接协议类型": "http",
          "deploymentUrl 当前连接的服务地址": "https://news--theagenttimes.run.tools",
          "configSchema 建立连接所需配置的JSON Schema,空对象表示未声明额外配置": {}
        }
      ],
      "tools Smithery记录的工具定义,仅作为后续环境能力线索": [
        {
          "name 工具调用名": "tat_search",
          "description 工具能力与调用关系说明": "Search The Agent Times agent-news layer across structured events, articles, and agent-action/product metadata. Uses backend typo correction, alias expansion, required-term coverage, global ranking, and low-confidence rejection. Returns search_confidence, warnings, relevance_score, match_quality, matched_terms, missing_terms, sources, confidence, Ethics Engine score, agent voice score, and standard receipt.",
          "inputSchema 工具输入参数的JSON Schema": {
            "type 输入根节点类型": "object",
            "properties 可传入的参数定义": {
              "tag 可选标签过滤条件": {
                "type 参数类型": "string",
                "description 参数说明": "Optional tag filter"
              },
              "sort 文章排序方式": {
                "enum 可选值": [
                  "relevance",
                  "newest"
                ],
                "type 参数类型": "string",
                "description 参数说明": "Article sort order"
              },
              "limit 返回结果数量": {
                "type 参数类型": "integer",
                "description 参数说明": "Number of results (max 20, default 10)"
              },
              "query 短而实体信息明确的英文搜索词": {
                "type 参数类型": "string",
                "description 参数说明": "Short entity-rich English search query for agent-news, articles, products, actions, or events"
              },
              "topic 可选主题过滤条件": {
                "type 参数类型": "string",
                "description 参数说明": "Optional topic filter"
              },
              "intent 可选意图过滤条件": {
                "type 参数类型": "string",
                "description 参数说明": "Optional intent filter"
              },
              "section 可选文章栏目过滤条件": {
                "type 参数类型": "string",
                "description 参数说明": "Optional article section filter"
              },
              "urgency 事件紧急程度过滤条件": {
                "enum 可选值": [
                  "low",
                  "medium",
                  "high",
                  "critical"
                ],
                "type 参数类型": "string",
                "description 参数说明": "Optional event urgency filter"
              },
              "actionability 事件可行动程度过滤条件": {
                "enum 可选值": [
                  "informational",
                  "monitor",
                  "act_now"
                ],
                "type 参数类型": "string",
                "description 参数说明": "Optional actionability filter"
              },
              "include_events 是否包含智能体事件匹配": {
                "type 参数类型": "boolean",
                "description 参数说明": "Include agent event matches (default true)"
              },
              "include_articles 是否包含文章匹配": {
                "type 参数类型": "boolean",
                "description 参数说明": "Include article matches (default true)"
              },
              "include_products 是否包含智能体行动或产品元数据匹配": {
                "type 参数类型": "boolean",
                "description 参数说明": "Include agent-action/product metadata matches (default true)"
              }
            }
          },
          "outputSchema 工具输出结果的JSON Schema": {
            "type 输出根节点类型": "object",
            "properties 可能返回的字段定义": {
              "text 文本类返回内容": {
                "type 字段类型": "string",
                "description 字段说明": "Present when the tool returns a text-only response."
              }
            }
          }
        },
        {
          "name 工具调用名": "tat_ask",
          "省略说明 其余工具字段与tat_search使用相同的注释规则": "..."
        }
      ],
      "dataDirections LLM建议后续研究的真实公开数据方向": [
        "Official product documentation, release notes, changelogs, and vendor announcements for AI-agent platforms, tools, MCP servers, and frameworks",
        "Open-source repositories, commit histories, issue trackers, package registries, licenses, and security advisories",
        "Research papers, preprints, benchmarks, model cards, datasets, and institutional lab publications",
        "Technology-news articles, interviews, opinion pieces, newsletters, and trade-publication coverage",
        "Company websites, press releases, funding announcements, investor disclosures, and acquisition records",
        "Public pricing pages, product catalogs, integration directories, API references, and marketplace listings",
        "Government regulations, legislative proposals, regulator guidance, enforcement actions, and public-policy consultations",
        "Infrastructure status pages, incident reports, vulnerability databases, and reliability disclosures",
        "Public labor-market data, job postings, workforce reports, and studies of automation's employment effects",
        "Public commerce, sales, marketing, advertising-technology, and engineering case studies involving AI agents",
        "Cryptographically signed publication records, timestamps, authorship metadata, source links, and other provenance evidence",
        "Public discussion threads, article comments, endorsements, and attributed expert or agent commentary",
        "Editorial policies, codes of conduct, correction records, trust reports, and source-quality methodologies"
      ],
      "organizationStatus 条目整理状态,agent_organized表示LLM整理成功": "agent_organized"
    }
  ]
}

```

## 2. 权威结构与适用边界

环境种子文件是“环境候选目录”，不是最终环境，也不是
[`AgentWorld 工具契约.md`](AgentWorld%20工具契约.md) 中的公开工具契约。其职责是保存
Smithery 服务元数据、已记录的 MCP 工具能力线索，以及供后续研究 Agent 使用的数据方向。

权威顶层结构固定为：

```json
{
  "catalog": "smithery",
  "prepared": 140,
  "agent_organized": 140,
  "environments": []
}
```

`agent_world_mini/catalog.py` 中存在两个不同边界：

| 边界 | 权威函数 | 约束 |
| --- | --- | --- |
| 制备边界 | `prepare_smithery_catalog` | 生成上面的四个顶层字段，并为每个条目写入 `organizationStatus` |
| 消费边界 | `load_prepared_catalog` | 文件必须存在；顶层 `environments` 必须是数组；跳过 `agent_failed` 和 `raw_catalog_record` |

当前消费代码没有调用独立的 JSON Schema 校验器。下面的 Schema 因而用于生成器、人工审查和后续验证器对齐；运行时真正的最低要求仍由第 6 节说明。

## 3. 推荐 JSON Schema

该 Schema 固定本项目依赖的字段，同时允许 Smithery 在环境、连接和工具对象上增加透传字段。新增的上游字段不得自动升级为本项目依赖。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://agent-world-mini.local/schemas/prepared-environment-catalog-v1.json",
  "title": "Agent-World Mini prepared environment catalog",
  "type": "object",
  "required": ["catalog", "prepared", "agent_organized", "environments"],
  "properties": {
    "catalog": {
      "const": "smithery"
    },
    "prepared": {
      "type": "integer",
      "minimum": 0
    },
    "agent_organized": {
      "type": "integer",
      "minimum": 0
    },
    "environments": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/environment"
      }
    }
  },
  "additionalProperties": false,
  "$defs": {
    "environment": {
      "type": "object",
      "required": [
        "qualifiedName",
        "description",
        "tools",
        "organizationStatus"
      ],
      "properties": {
        "id": {"type": "string"},
        "qualifiedName": {"type": "string", "minLength": 1},
        "namespace": {"type": "string"},
        "slug": {"type": "string"},
        "displayName": {"type": "string"},
        "description": {"type": "string"},
        "iconUrl": {"type": "string"},
        "verified": {"type": "boolean"},
        "useCount": {"type": "integer", "minimum": 0},
        "remote": {"type": "boolean"},
        "isDeployed": {"type": "boolean"},
        "unlisted": {"type": "boolean"},
        "inactive": {"type": "boolean"},
        "createdAt": {"type": "string", "format": "date-time"},
        "homepage": {"type": "string"},
        "repository": {"type": "string"},
        "bySmithery": {"type": "boolean"},
        "owner": {"type": "string"},
        "score": {"type": ["number", "null"]},
        "deploymentUrl": {"type": "string"},
        "connections": {
          "type": "array",
          "items": {"$ref": "#/$defs/connection"}
        },
        "tools": {
          "type": "array",
          "minItems": 1,
          "items": {"$ref": "#/$defs/documentedTool"}
        },
        "toolNames": {
          "type": "array",
          "items": {"type": "string"}
        },
        "prompts": {
          "type": "array",
          "items": {"type": "object"}
        },
        "resources": {
          "type": "array",
          "items": {"type": "object"}
        },
        "dataDirections": {
          "type": "array",
          "items": {"type": "string", "minLength": 1}
        },
        "organizationStatus": {
          "enum": [
            "agent_organized",
            "agent_failed",
            "raw_catalog_record"
          ]
        }
      },
      "additionalProperties": true
    },
    "connection": {
      "type": "object",
      "required": ["type"],
      "properties": {
        "type": {"type": "string", "minLength": 1},
        "deploymentUrl": {"type": "string"},
        "bundleUrl": {"type": "string"},
        "runtime": {"type": "string"},
        "configSchema": {"type": "object"}
      },
      "additionalProperties": true
    },
    "documentedTool": {
      "type": "object",
      "required": ["name"],
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"}
      },
      "additionalProperties": true
    }
  }
}
```

这里没有把 `inputSchema` 和 `outputSchema` 收紧为最终工具契约，原因是它们是 Smithery 返回的原始能力描述。当前产物中的部分工具没有 `outputSchema`，且这些 Schema 不保证满足最终环境对 `required`、`additionalProperties`、统一成功/失败结果或状态回滚的要求。

## 4. 顶层字段

| 字段 | 生产规则 | 一致性要求 |
| --- | --- | --- |
| `catalog` | 当前固定为 `smithery` | 标识目录来源，不表示业务数据来源 |
| `prepared` | 写入 `environments` 的详细条目数 | 必须等于 `environments.length` |
| `agent_organized` | `organizationStatus == agent_organized` 的条目数 | 不得大于 `prepared` |
| `environments` | 通过初筛、详情读取和工具存在性检查后的条目 | 必须是数组；可以为空 |

`prepared` 和 `agent_organized` 是汇总字段，不参与环境选择。修改条目后必须重新计算，不能只改计数。

## 5. 环境条目

### 5.1 字段分层

| 分层 | 字段 | 后续用途 |
| --- | --- | --- |
| 稳定标识 | `qualifiedName` | 生成 `theme_id` 和 Smithery `source_url`；是消费阶段唯一硬依赖字段 |
| 展示与研究 | `displayName`、`description`、`dataDirections` | 形成环境主题、研究提示和真实数据搜索方向 |
| 能力线索 | `tools`、兼容回退字段 `toolNames` | 形成候选操作名，并把原始工具描述带入研究阶段 |
| 目录元数据 | `homepage`、`repository`、`useCount`、`verified` | 作为 `catalog_metadata` 保存，帮助追溯和判断来源 |
| 连接元数据 | `connections`、`deploymentUrl` | 描述原 MCP 的接入方式；当前不用于运行生成后的本地工具 |
| 状态 | `organizationStatus` | 决定条目是否进入环境候选池 |
| 上游透传 | `id`、`namespace`、`slug`、`iconUrl`、`createdAt`、`owner`、`score`、`prompts`、`resources` 等 | 保留目录快照；当前生成流程不读取 |

`qualifiedName` 必须保持 Smithery 原值，不应从 `displayName`、`namespace` 或 `slug` 猜测。`displayName` 缺失时，消费代码回退到 `qualifiedName`。

### 5.2 `description` 与 `dataDirections`

启用 LLM 整理时，`_organize_environment` 要求返回：

```json
{
  "business_description": "brief description of the real service or workflow",
  "data_directions": [
    "types of real public data likely to support this environment"
  ]
}
```

整理成功后：

- `business_description` 覆盖条目的 `description`；空值则回退到原目录描述；
- 非空 `data_directions` 被转为字符串数组并写入 `dataDirections`；
- MCP 工具只提供能力线索，不要求后续环境逐个复刻这些工具；
- `dataDirections` 是研究方向，不是已经采集、验证或获准使用的数据。

### 5.3 `connections`

当前产物每个环境有一个连接。已观察到两种形态：

```json
{
  "type": "http",
  "deploymentUrl": "https://example.run.tools",
  "configSchema": {}
}
```

```json
{
  "type": "stdio",
  "bundleUrl": "https://example/server.mcpb",
  "runtime": "node",
  "configSchema": {
    "type": "object",
    "required": [],
    "properties": {}
  }
}
```

连接字段由 Smithery 详情接口透传。后续生成器不得因为存在 `deploymentUrl` 就绕过访问控制，也不得把 `configSchema` 中的密钥或配置写入生成产物。

### 5.4 `tools`

`_read_server_detail` 只保留对象类型且具有非空 `name` 的工具，并要求每个环境至少有一个这样的工具。单个条目的工具结构为：

```json
{
  "name": "search_records",
  "description": "Search documented records.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  },
  "outputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

对后续环境生成的约束是：

- `name` 进入 `candidate_operations`，只表示可参考的业务动作；
- 完整工具对象进入 `documented_tools`，用于保留来源能力说明；
- 不得直接把这里的工具复制为最终 `toolDefinitions[].public`；
- 最终工具必须根据新环境的数据模型重新设计，并满足工具契约中的输入、输出、错误、状态和回滚约束；
- `outputSchema` 在目录条目中允许缺失，缺失不等同于工具无返回值。

## 6. 整理状态与最低读取契约

| `organizationStatus` | 产生条件 | `load_prepared_catalog` 行为 |
| --- | --- | --- |
| `agent_organized` | LLM 返回并成功解析整理结果 | 接受 |
| `agent_failed` | LLM 调用、解析或结果读取失败 | 跳过 |
| `raw_catalog_record` | LLM 未启用 | 跳过 |

按照当前实现，一个可用条目至少必须满足：

```text
顶层 environments 是数组
条目是 JSON object
organizationStatus 不得是 agent_failed 或 raw_catalog_record
qualifiedName 存在且可转成非空字符串
tools 若存在则应为数组；dataDirections 若存在则应为数组
```

代码目前会接受缺失或未知的 `organizationStatus`，也不会验证汇总计数和字段类型。这是读取实现的宽松行为，不是推荐的数据契约。新产物必须显式写入三种已知状态之一，并在进入批量生成前完成第 9 节检查。

## 7. 到 `ThemeSeed` 的确定性投影

`theme_from_catalog` 将一个环境条目投影为内部 `ThemeSeed`：

| `ThemeSeed` 字段 | 来源或规则 |
| --- | --- |
| `theme_id` | `smithery-` + 小写 `qualifiedName`，将 `/` 替换为 `-`，主体最多 64 个字符 |
| `seed_label` | `displayName`；缺失或为空时使用 `qualifiedName` |
| `source_type` | 固定为 `smithery_mcp` |
| `source_url` | `https://smithery.ai/servers/{qualifiedName}` |
| `license_or_access_note` | 固定提示：MCP 页面只作主题证据，实际数据源仍需记录许可 |
| `coarse_route_label` | 固定为 `unclassified` |
| `adapter` | 固定为 `generic_web` |
| `candidate_operations` | `tools[*].name`；没有工具名时回退到 `toolNames` |
| `source_description` | `description`，缺失时为空字符串 |
| `documented_tools` | `tools` 中所有对象条目 |
| `data_directions` | `dataDirections` 中的非空值，统一转成字符串 |
| `catalog_metadata` | 仅保留非空的 `qualifiedName`、`homepage`、`repository`、`useCount`、`verified` |

因此，`id`、`slug`、`deploymentUrl`、`connections`、`prompts` 和 `resources` 不会进入 `ThemeSeed`，不能依赖它们影响后续环境结构。

## 8. 制备、选择与去重

目录制备顺序如下：

```text
请求 verified=true 的 Smithery 分页目录
  -> 跳过 inactive / unlisted / 描述少于 40 字符的条目
  -> 按规范化展示名去除目录内重复项
  -> 读取详情并要求至少一个具名工具
  -> 可选 LLM 整理 description 和 dataDirections
  -> 写入 prepared_environments.json
```

批量生成不会重新访问 Smithery。`select_prepared_themes` 读取本地文件，用 `selection_seed` 对候选项洗牌，然后与内置主题和 `output_root/*/theme_registry.json` 中的既有主题比较：

- URL 比较会小写 scheme/host，移除 query/fragment 和末尾 `/`；
- 名称比较会转小写、只保留字母数字词，并忽略 `mcp/server/tool/tools/official/integration`；
- 规范化名称相同，或字符串相似度不低于 `0.9`，视为重复；
- 每选中一个条目，立即把其 URL 和名称加入本轮已见集合。

`selection_seed` 只控制选择顺序，不修改环境内容。相同 seed 的结果还取决于目录内容、内置主题和输出目录中已有的成功环境。

## 9. 生成前验证清单

在把新目录交给 `--batch-size` 前，至少验证：

```text
1. 文件是 UTF-8 编码的合法 JSON object。
2. catalog == "smithery"。
3. prepared == environments.length。
4. agent_organized 等于 organizationStatus == "agent_organized" 的条目数。
5. qualifiedName 非空且在 environments 内唯一。
6. 每个 agent_organized 条目至少有一个具名工具。
7. description 是字符串，dataDirections 是字符串数组。
8. tools/inputSchema/outputSchema 保持从详情接口取得的 JSON 结构，未被字符串化。
9. agent_failed 和 raw_catalog_record 不计入可生成候选数。
10. 使用 --batch-size N --dry-run --selection-seed S 做本地选择与去重检查。
```

示例命令：

```powershell
python -m agent_world_mini --batch-size 5 --dry-run --selection-seed 7 `
  --prepared-catalog agent_world_mini/prepared_environments.json
```

`--dry-run` 只验证加载、投影、随机选择和既有环境去重，不验证真实数据研究、工具生成、运行时状态或任务合成。

## 10. 当前产物快照

对仓库现有 `agent_world_mini/prepared_environments.json` 的静态统计为：

| 项目 | 当前值 |
| --- | ---: |
| 环境条目 | 140 |
| `agent_organized` 条目 | 140 |
| 具名工具 | 2,833 |
| 带 `outputSchema` 的工具 | 1,813 |
| HTTP 连接 | 138 |
| stdio 连接 | 2 |
| 单环境工具数 | 1 至 290 |
| 单环境 `dataDirections` 数 | 6 至 30 |

这些数字是当前文件的快照，不属于固定 Schema。后续重新制备目录后，应重新统计并更新本节，不能把数量变化直接视为格式错误。
