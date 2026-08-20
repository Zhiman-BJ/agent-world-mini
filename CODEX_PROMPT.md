# Codex Research Agent 提示词

把下面整段交给在本仓库根目录工作的主 Codex，替换尖括号中的内容。

---

请把 Codex 子智能体作为 Agent-World Mini pipeline 的 Research Agent，为下面的主题准备真实研究数据：

`<主题列表，例如：临床基因组证据研究、航天任务工程、半导体器件选型>`

每个主题使用一个独立子智能体并行研究。如果当前会话不支持子智能体，请明确说明，不要假装已经调用。子智能体只负责研究阶段和 `research_bundle.json`，后续工具生成、验证、构图和任务生成必须交回现有 Python pipeline，不要手工生成这些文件。

开始前请阅读 `README.md`、`docs/USAGE_ZH.md` 和 `docs/CODEX_WORKFLOW_ZH.md`，理解 `research_bundle.json` 的格式以及 pipeline 的数据关系约定。

每个 Research Agent 按以下原则工作：

1. 先理解主题对应的真实工作流，再选择需要的实体和关系。优先官方 API、官方数据集和权威结构化来源。
2. 只保存真实抓取到、可以追溯的事实。每条记录必须有 `source_url`，禁止用模型常识补值或生成虚构记录。
3. 数据扩展的目标是改善实体多样性、关系覆盖和自然可达深度。继续增加同一父节点下的相似文件、图片、零件或记录，不算进展。
4. 可以保留任务真正会使用的少量 JSON、CSV 或文本文件；不下载模型权重、基因组大文件、遥感影像、PDF、压缩包、二进制文件或整站文件列表。
5. 不预先规定必须挖多少条。主要工作流已经连通，而继续搜索只会增加同类叶子时就停止；数据不足以构成环境时要如实说明。
6. 不为合成一张连通图而添加没有来源支持的关系。真实的多个连通分量可以保留并解释。
7. `entity_id` 必须稳定且在同类实体中唯一。子实体的 ID 要包含父级范围，避免不同仓库或不同产品下的同名项被合并。
8. 一对多关系在子记录中使用 `*_id`，值必须精确等于另一条记录的 `entity_id`。多对多关系使用以 `_link` 结尾的连接实体并保存两端 ID。
9. 每个来源请求和遇到的问题都要记录。某个来源返回海量重复内容、临时失败或不可访问时，可以换真实来源，不得静默降级成编造数据。

每个子智能体只写自己的目录：

```text
runs/codex-<slug>/research_bundle.json
```

不要修改共享 pipeline 代码，也不要生成 `tool_specs.json`、`tool_graph.json`、`walk_synthesis.json` 或 `tasks.json`。临时研究脚本放在 `tmp/`。

每个 `research_bundle.json` 至少包含：

```json
{
  "theme": "环境名称",
  "adapter": "codex_research_agent",
  "retrieved_at": "ISO 8601 时间",
  "sources": [
    {"name": "来源名称", "url": "真实 URL", "access_note": "访问或许可说明"}
  ],
  "records": [
    {
      "entity_type": "实体类型",
      "entity_id": "稳定 ID",
      "attributes": {"name": "真实名称", "parent_id": "另一个真实 entity_id"},
      "source_url": "支持这条记录的真实 URL"
    }
  ],
  "resources": [
    {
      "resource_id": "稳定资源 ID",
      "name": "data.json",
      "media_type": "application/json",
      "source_url": "真实下载 URL",
      "content": {"实际下载并解析的内容": true}
    }
  ],
  "theme_metadata": {
    "theme_id": "codex-<slug>",
    "source_type": "codex_research_bundle",
    "environment_blueprint": {
      "mutable_entities": [
        {
          "entity_type": "本地可操作对象",
          "fields": {"status": {"type": "string", "example": "queued", "update_example": "complete"}},
          "operations": ["create", "read", "update", "delete"],
          "update_fields": ["status"]
        }
      ],
      "python_tools": []
    }
  },
  "complexification": [
    {"round": 1, "reason": "补充了什么关系", "result": "增加了哪些真实实体"}
  ]
}
```

`resources` 只放实际下载的小文件；`environment_blueprint` 只描述用户在本地沙箱中管理的任务、集合、报告等对象，不能把模型编造的事实放进去。两者没有合适内容时都可以省略。最终工具、实现、图和任务仍由 pipeline 生成。`derived_datasets` 和 `state_contract` 不需要子智能体填写。

子智能体完成后，主智能体必须先独立抽查来源、记录和 ID 关系。确认无明显问题后，为每个环境执行：

```powershell
python -m agent_world_mini `
  --research-bundle runs/codex-<slug>/research_bundle.json `
  --slug codex-<slug>
```

这条命令才负责生成其余环境文件。除非我明确要求，否则先不运行 `--verify-five-runs`。

pipeline 跑完后，主智能体继续检查：工具是否全部实际验证、候选链是否执行和去重、任务文字是否与 `reference_calls` 操作的对象一致、长链是否来自真实关系而不是分号拼题或反复查询。

最后用易懂的中文汇报：每个 Research Agent 找了哪些来源、形成了什么数据结构、为什么停止继续挖掘、pipeline 生成了多少工具和任务、任务长度如何、还有哪些问题。明确区分数据真实、工具可运行、参考链可重放和模型能稳定求解。

---
