# 把 Codex 子智能体用作 Research Agent

## 一句话说明

Codex 子智能体负责“去哪里找数据、选哪些数据、怎样把真实关系整理出来”；现有 Python pipeline 负责“根据数据生成工具、验证工具、构图、采样和生成任务”。两段通过一个 `research_bundle.json` 连接。

```text
准备好的主题或人工指定主题
  -> Codex 子智能体网络研究
  -> research_bundle.json
  -> python --research-bundle
  -> 工具生成与执行验证
  -> 工具图与候选任务
  -> 语义审查
  -> 可选 5-run
```

Codex 比普通一次性模型调用更有耐心，也能在搜索过程中判断“目前缺的是关系还是更多同类记录”；后面的 pipeline 仍保持统一，不需要让 Codex 手工仿造整套输出。

## 实际怎么运行

### 第一步：让 Codex 做研究

在 Codex 中打开仓库，把根目录 [CODEX_PROMPT.md](../CODEX_PROMPT.md) 的完整提示词发给主智能体，并写明需要的主题。例如：

```text
请为以下三个主题使用 Codex 子智能体作为 Research Agent：
1. 临床基因组证据研究
2. 航天任务与发射工程
3. 汽车功率半导体选型
```

主智能体给每个独立主题分配一个子智能体。每个子智能体只交付自己的研究文件：

```text
runs/codex-genomics/research_bundle.json
runs/codex-aerospace/research_bundle.json
runs/codex-semiconductor/research_bundle.json
```

### 第二步：运行 pipeline 后半段

以基因环境为例：

```powershell
python -m agent_world_mini `
  --research-bundle runs/codex-genomics/research_bundle.json `
  --slug codex-genomics
```

输入文件和输出目录可以相同。pipeline 会先完整读取研究文件，再在 `runs/codex-genomics/` 中写入工具、图、任务和摘要。

需要 5-run 时：

```powershell
python -m agent_world_mini `
  --research-bundle runs/codex-genomics/research_bundle.json `
  --slug codex-genomics-verified `
  --verify-five-runs
```

也可以直接要求主 Codex 在子智能体全部完成后逐个执行这些命令并汇总结果。需要明确的是：Python 进程不会自己创建 Codex 子智能体；调度发生在当前 Codex 会话里，文件交接以后才进入 Python pipeline。

## Research Agent 到底要交什么

最小的 `research_bundle.json` 如下：

```json
{
  "theme": "Clinical genomics evidence research",
  "adapter": "codex_research_agent",
  "retrieved_at": "2026-08-13T12:00:00Z",
  "sources": [
    {
      "name": "NCBI Gene BRCA1",
      "url": "https://eutils.ncbi.nlm.nih.gov/...",
      "access_note": "Public NCBI API"
    }
  ],
  "records": [
    {
      "entity_type": "gene",
      "entity_id": "gene:672",
      "attributes": {"name": "BRCA1"},
      "source_url": "https://eutils.ncbi.nlm.nih.gov/..."
    },
    {
      "entity_type": "variant",
      "entity_id": "variant:example",
      "attributes": {"name": "Example variant", "gene_id": "gene:672"},
      "source_url": "https://www.ncbi.nlm.nih.gov/clinvar/..."
    }
  ],
  "theme_metadata": {
    "theme_id": "codex-genomics",
    "source_type": "codex_research_bundle"
  },
  "complexification": []
}
```

关键只有四点：

1. 每条记录有稳定且唯一的 `entity_id`。
2. 每条记录的事实都能由自己的 `source_url` 支持。
3. 关系使用 `*_id` 字段，并且值等于另一条记录的 `entity_id`。
4. 多对多关系使用以 `_link` 结尾的实体，例如 `variant_publication_link`，其中保存 `variant_id` 和 `publication_id`。

`derived_datasets` 和 `state_contract` 可以省略，pipeline 会根据记录自动生成。Codex 不需要提前设计工具，也不需要填写任务。

## 研究做到什么程度就停

Research Agent 不是数据下载器。它的目标是得到一个小而连贯的环境样本。

应该继续研究的情况：

- 只有基因和变异，还缺临床解释或疾病关系。
- 只有航天任务和很多图片，还缺航天器、仪器或发射关系。
- 数据分成多个孤立部分，而公开来源中存在真实连接。

应该停止的情况：

- 工作流中的主要实体和关系已经覆盖。
- 新搜索只能继续增加同一父节点下的文件、图片或相似零件。
- 下一个来源需要下载大型权重、序列、影像、PDF 或整站数据。
- 为了继续加深，只能猜测关系或拼接不相关来源。

环境不需要固定记录数。判断依据是数据是否支持多样且自然的工作，而不是是否达到某个数字。

## 主智能体最后要检查什么

Codex 子智能体完成研究后，主智能体先检查 bundle，再运行 pipeline：

- 来源是否真实，记录中的 `source_url` 是否能支持该记录。
- ID 有没有重复，不同父实体下的同名文件是否被错误合并。
- `*_id` 是否指向真实存在的记录。
- 是否为了数量堆了大量同类叶子。
- 是否存在没有证据的跨主题关系。

pipeline 完成后还要检查任务文字是否与 `reference_calls` 的实际对象一致。参考链能够重放，仍不代表题目一定自然或模型一定能稳定解出。

## 当前边界

`--research-bundle` 已经把 Codex 研究结果接进 Python pipeline，但 Codex 子智能体的创建仍由 Codex 应用负责。这个仓库没有 `--spawn-codex-agents` 参数，也没有把桌面子智能体当作远程 API 调用。
