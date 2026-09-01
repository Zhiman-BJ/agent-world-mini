const state = { data: null, selected: 0, resourceFilter: "all", nonEntityFilter: "all" };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const formatNumber = (value) => new Intl.NumberFormat("zh-CN").format(Number(value || 0));
const formatBytes = (value) => {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));
const jsonText = (value) => JSON.stringify(value, null, 2);
const currentEnvironment = () => state.data?.environments?.[state.selected];

const typeCopy = {
  raw: {
    index: "01",
    title: "Raw 原始证据",
    short: "来源原貌",
    description: "实际下载的 API 响应、网页或数据文件。保留原始结构，用来审计来源、重新抽取实体，不直接承诺统一字段。",
  },
  entity: {
    index: "02",
    title: "Entity 规范实体",
    short: "工具直接读取",
    description: "从 Raw 确定性整理出的业务记录。主键稳定、同类字段一致，是搜索、筛选、比较和关系遍历的主要对象。",
  },
  derived: {
    index: "03",
    title: "Derived 派生数据",
    short: "确定性计算",
    description: "根据已有业务数据计算的统计、索引或聚合结果。它不是生成脚本，也不能加入原始来源中没有的业务事实。",
  },
  output: {
    index: "04",
    title: "Output 工具输出",
    short: "后续可写",
    description: "留给后续工具写报告、导出文件和中间结果的目录。它不参与当前实体数量和丰富度计算。",
  },
};

async function loadSnapshot() {
  const response = await fetch(`data/environments.json?cache=${Date.now()}`);
  if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
  state.data = await response.json();
  state.selected = Math.max(0, Math.min(state.selected, state.data.environments.length - 1));
  renderNavigation();
  renderEnvironment();
  const generatedAt = new Date(state.data.generated_at);
  $("#snapshot-time").textContent = Number.isNaN(generatedAt.getTime())
    ? "快照已载入"
    : `数据快照 ${generatedAt.toLocaleString("zh-CN", { hour12: false })}`;
  $("#source-root").textContent = `源目录：${state.data.source_root}`;
}

function renderNavigation() {
  const environments = state.data?.environments || [];
  $("#environment-count").textContent = environments.length;
  $("#environment-nav").innerHTML = environments.map((environment, index) => `
    <button class="environment-button ${index === state.selected ? "active" : ""}" data-index="${index}">
      <span class="env-title">${escapeHtml(environment.name)}</span>
      <span class="env-id">${escapeHtml(environment.environment_id)}</span>
      <span class="env-summary">
        <i class="quality-dot ${environment.quality_tier === "rich" ? "good" : "warn"}"></i>
        ${formatNumber(environment.metrics.entity_record_count)} 条实体记录
      </span>
    </button>
  `).join("");
  $$(".environment-button").forEach((button) => {
    button.addEventListener("click", () => {
      state.selected = Number(button.dataset.index);
      state.resourceFilter = "all";
      state.nonEntityFilter = "all";
      renderNavigation();
      renderEnvironment();
    });
  });
}

function metric(label, value, detail, tone) {
  return `
    <div class="metric-item ${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>`;
}

function renderEnvironment() {
  const environment = currentEnvironment();
  if (!environment) return;
  const { metrics, validation } = environment;
  $("#environment-id").textContent = environment.environment_id;
  $("#environment-name").textContent = environment.name;
  $("#environment-description").textContent = environment.description;
  $("#quality-badge").textContent = String(environment.quality_tier || "unknown").toUpperCase();
  $("#quality-badge").classList.toggle("bad", environment.quality_tier !== "rich");
  $("#validation-caption").textContent = validation.valid
    ? "最终契约与文件事实一致"
    : `${(validation.errors || []).length} 项验证错误`;
  $("#validation-caption").classList.toggle("bad", !validation.valid);

  $("#metric-grid").innerHTML = [
    metric("规范实体记录", formatNumber(metrics.entity_record_count), `${metrics.entity_type_count} 种可调用业务对象`, "entity"),
    metric("Raw 原始文件", formatNumber(metrics.raw_file_count), `${formatBytes(metrics.raw_bytes)} 可审计来源数据`, "raw"),
    metric("来源数据面", formatNumber(metrics.surface_count), `${formatNumber(metrics.raw_record_count)} 条采集声明`, "source"),
    metric("闭合关系", formatNumber(metrics.closed_relation_count), `${metrics.relation_gap_count} 个未闭合候选`, "relation"),
    metric("能力原子", formatNumber(metrics.capability_count), `${metrics.operation_family_count} 类可实现操作`, "capability"),
    metric("估算任务实例", formatNumber(metrics.task_instance_count), `${formatNumber(metrics.chain_shape_count)} 种多步链`, "task"),
  ].join("");

  renderDataJourney(environment);
  renderLineage(environment);
  renderSurfaces(environment);
  renderNonEntityFilter();
  renderNonEntityFiles(environment);
  renderEntities(environment);
  renderRelations(environment);
  renderCapabilities(environment);
  renderResourceFilter();
  renderResources(environment);
  renderWarnings(environment);
}

function renderDataJourney(environment) {
  const counts = environment.metrics.resource_type_counts || {};
  const fileCounts = Object.fromEntries(
    Object.keys(typeCopy).map((type) => [
      type,
      (environment.resources || [])
        .filter((resource) => resource.data_type === type)
        .reduce((total, resource) => total + Number(resource.file_count || 0), 0),
    ]),
  );
  $("#data-journey").innerHTML = Object.entries(typeCopy).map(([type, copy], index) => `
    <article class="journey-stage ${type}">
      <div class="journey-index">${copy.index}</div>
      <div class="journey-copy">
        <div class="journey-title-row">
          <h4>${copy.title}</h4>
          <span>${formatNumber(counts[type] || 0)} 个资源</span>
        </div>
        <p>${copy.description}</p>
        <small>${formatNumber(fileCounts[type] || 0)} 个当前文件 · ${copy.short}</small>
      </div>
      ${index < 3 ? '<span class="journey-arrow" aria-hidden="true">→</span>' : ""}
    </article>
  `).join("");
}

function renderLineage(environment) {
  const resources = environment.resources || [];
  const edges = environment.lineage_edges || [];
  $("#lineage-meta").textContent = `${resources.length} 个资源 / ${edges.length} 条转换边`;
  $("#lineage-board").innerHTML = Object.keys(typeCopy).map((type) => {
    const items = resources.filter((resource) => resource.data_type === type);
    return `
      <section class="lineage-column ${type}">
        <header><span>${typeCopy[type].index}</span><div><strong>${typeCopy[type].title}</strong><small>${items.length} 个资源</small></div></header>
        <div class="lineage-resources">
          ${items.length ? items.map((resource) => `
            <div class="lineage-resource">
              <strong>${escapeHtml(resource.name || resource.resource_id)}</strong>
              <code>${escapeHtml(resource.resource_id)}</code>
              ${resource.source_resources?.length
                ? `<small>来自 ${resource.source_resources.map(escapeHtml).join("、")}</small>`
                : resource.source_urls?.length
                  ? `<small>${resource.source_urls.length} 个外部来源</small>`
                  : ""}
            </div>
          `).join("") : '<div class="empty-inline">当前没有这一层资源</div>'}
        </div>
      </section>`;
  }).join("");
}

function surfaceProgress(surface) {
  const collected = Number(surface.records_collected || 0);
  const total = Number(surface.reported_total || 0);
  if (!total) return "";
  const percentage = Math.min(100, Math.round(collected * 100 / total));
  return `<span class="progress"><i style="width:${percentage}%"></i></span><small>${percentage}% of reported total</small>`;
}

function renderSurfaces(environment) {
  const surfaces = environment.surfaces || [];
  $("#surface-meta").textContent = `${surfaces.length} 个数据面 / ${environment.metrics.raw_file_count} 个 Raw 文件`;
  $("#surface-list").innerHTML = surfaces.length ? surfaces.map((surface) => `
    <details class="surface-item">
      <summary>
        <div class="surface-identity">
          <span class="surface-status ${escapeHtml(surface.collection_status)}"></span>
          <div>
            <strong>${escapeHtml(surface.name || surface.surface_id)}</strong>
            <code>${escapeHtml(surface.surface_id)}</code>
          </div>
        </div>
        <div class="surface-numbers">
          <span><strong>${formatNumber(surface.records_collected)}</strong><small>采集记录</small></span>
          <span><strong>${formatNumber(surface.raw_file_count)}</strong><small>Raw 文件</small></span>
          <span><strong>${formatNumber(surface.pages_collected || 0)}</strong><small>分页/分层</small></span>
        </div>
        <span class="surface-priority ${surface.priority === "extension" ? "extension" : ""}">${escapeHtml(surface.priority)}</span>
      </summary>
      <div class="surface-detail">
        <dl>
          <div><dt>真实来源</dt><dd><a href="${escapeHtml(surface.url)}" target="_blank" rel="noreferrer">${escapeHtml(surface.url)}</a></dd></div>
          <div><dt>数据形态</dt><dd>${escapeHtml(surface.kind)} · ${escapeHtml(surface.collection_mode)}</dd></div>
          <div><dt>指向实体</dt><dd>${(surface.entities || []).map((item) => `<code>${escapeHtml(item)}</code>`).join(" ") || "未声明"}</dd></div>
          <div><dt>完成证据</dt><dd>${escapeHtml(surface.exhaustion_evidence?.detail || "未提供")}</dd></div>
        </dl>
        ${surfaceProgress(surface)}
        <div class="file-token-list">${(surface.raw_files || []).map((file) => `<code>${escapeHtml(file)}</code>`).join("")}</div>
      </div>
    </details>
  `).join("") : '<div class="empty-state">没有登记原始数据面</div>';
}

function summaryStructure(summary) {
  const structure = summary?.structure || [];
  if (!structure.length) return '<span class="muted-value">该格式没有可枚举的顶层字段</span>';
  return structure.map((item) => `
    <span class="structure-token">
      <code>${escapeHtml(item.name)}</code>
      <small>${escapeHtml(item.type)}${item.count == null ? "" : ` · ${formatNumber(item.count)}`}</small>
    </span>
  `).join("");
}

function nonEntityOrigin(file) {
  if (file.source_urls?.length) {
    return file.source_urls.map((url) => `
      <a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a>
    `).join("");
  }
  if (file.source_resources?.length) {
    return file.source_resources.map((resource) => `<code>${escapeHtml(resource)}</code>`).join(" ");
  }
  if (file.data_type === "output") return "由后续工具在任务执行期间产生";
  return "契约未声明来源链接";
}

function renderNonEntityFilter() {
  $$("#non-entity-filter button").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === state.nonEntityFilter);
    button.onclick = () => {
      state.nonEntityFilter = button.dataset.filter;
      renderNonEntityFilter();
      renderNonEntityFiles(currentEnvironment());
    };
  });
}

function renderNonEntityFiles(environment) {
  const allFiles = environment.non_entity_files || [];
  const counts = Object.fromEntries(["raw", "derived", "output"].map((type) => [
    type,
    allFiles.filter((file) => file.data_type === type).length,
  ]));
  $("#non-entity-summary").innerHTML = ["raw", "derived", "output"].map((type) => `
    <span class="file-summary-item ${type}">
      <strong>${formatNumber(counts[type])}</strong>
      <small>${type === "output" ? "个目录/文件" : "个文件"}</small>
      <b>${typeCopy[type].title}</b>
    </span>
  `).join("");

  const files = allFiles.filter(
    (file) => state.nonEntityFilter === "all" || file.data_type === state.nonEntityFilter,
  );
  $("#non-entity-list").innerHTML = files.length ? files.map((file, index) => {
    const summary = file.content_summary || {};
    const preview = summary.preview == null
      ? "当前没有文件内容"
      : typeof summary.preview === "string"
        ? summary.preview
        : jsonText(summary.preview);
    const metadata = summary.metadata && Object.keys(summary.metadata).length
      ? `<div class="file-metadata"><span>响应元数据</span><pre>${escapeHtml(jsonText(summary.metadata))}</pre></div>`
      : "";
    return `
      <details class="non-entity-item ${escapeHtml(file.data_type)}" ${index === 0 ? "open" : ""}>
        <summary>
          <span class="file-type-mark">${escapeHtml(file.data_type.slice(0, 1).toUpperCase())}</span>
          <div class="file-identity">
            <strong>${escapeHtml(file.resource_name || file.name)}</strong>
            <code>${escapeHtml(file.path)}</code>
          </div>
          <div class="file-facts">
            <span>${escapeHtml(file.format)} · ${formatBytes(file.bytes)}</span>
            <span>${file.exists ? "已落盘" : file.storage_type === "directory" && file.target_exists ? "目录存在 · 当前为空" : file.storage_type === "directory" ? "目录未建立" : "文件缺失"}</span>
          </div>
        </summary>
        <div class="non-entity-detail">
          <div class="file-explanation">
            <dl>
              <div><dt>资源契约中的含义</dt><dd>${escapeHtml(file.resource_description || "没有资源说明")}</dd></div>
              <div><dt>这个具体文件装了什么</dt><dd>${escapeHtml(summary.summary || "没有内容摘要")}</dd></div>
              <div><dt>为何保留 / 工具如何使用</dt><dd>${escapeHtml(file.usage)}</dd></div>
              <div><dt>${file.data_type === "derived" ? "上游资源" : "来源"}</dt><dd class="file-origin">${nonEntityOrigin(file)}</dd></div>
            </dl>
            <div class="structure-heading"><strong>实际内容结构</strong><span>从落盘文件确定性读取</span></div>
            <div class="structure-list">${summaryStructure(summary)}</div>
            ${metadata}
          </div>
          <div class="file-preview">
            <div class="sample-heading"><strong>内容样本</strong><span>${file.sha256 ? `SHA-256 ${escapeHtml(file.sha256.slice(0, 12))}…` : "尚无内容哈希"}</span></div>
            <pre>${escapeHtml(preview)}</pre>
          </div>
        </div>
      </details>`;
  }).join("") : '<div class="empty-state">没有匹配的非实体文件</div>';
}

function renderEntities(environment) {
  const entities = environment.entities || [];
  $("#entity-meta").textContent = `${entities.length} 种实体 / ${formatNumber(environment.metrics.entity_record_count)} 条记录`;
  $("#entity-list").innerHTML = entities.length ? entities.map((entity, index) => `
    <details class="entity-item" ${index === 0 ? "open" : ""}>
      <summary>
        <div class="entity-name">
          <span class="entity-symbol">E</span>
          <div><strong>${escapeHtml(entity.entity_type)}</strong><small>${escapeHtml(entity.description)}</small></div>
        </div>
        <div class="entity-facts">
          <span><strong>${formatNumber(entity.record_count)}</strong> 条记录</span>
          <span><strong>${formatNumber(entity.field_count)}</strong> 个固定字段</span>
          <code>${escapeHtml(entity.resource_id)}</code>
        </div>
      </summary>
      <div class="entity-detail">
        <div class="field-table-wrap">
          <table class="field-table">
            <thead><tr><th>字段</th><th>类型</th><th>不同值</th><th>字段含义</th><th>真实样本</th></tr></thead>
            <tbody>${(entity.fields || []).map((field) => `
              <tr>
                <td><code>${escapeHtml(field.name)}</code></td>
                <td><span class="field-type">${escapeHtml(field.type)}</span></td>
                <td>${formatNumber(field.distinct_count)}</td>
                <td>${escapeHtml(field.description || "未提供字段说明")}</td>
                <td>${(field.sample_values || []).map((value) => `<code>${escapeHtml(String(value))}</code>`).join(" ")}</td>
              </tr>
            `).join("")}</tbody>
          </table>
        </div>
        <div class="sample-panel">
          <div class="sample-heading"><strong>真实记录样本</strong><span>来自 Entity 文件，不是演示数据</span></div>
          <pre>${escapeHtml(jsonText(entity.samples?.[0] || {}))}</pre>
        </div>
      </div>
    </details>
  `).join("") : '<div class="empty-state">没有可展示的规范实体</div>';
}

function renderRelations(environment) {
  const relations = environment.relations || [];
  $("#relation-meta").textContent = `${relations.length} 条可遍历关系`;
  $("#relation-list").innerHTML = relations.length ? relations.map((relation) => `
    <article class="relation-item">
      <div class="relation-route">
        <span><small>FROM</small><code>${escapeHtml(relation.from_entity)}.${escapeHtml(relation.field)}</code></span>
        <b aria-hidden="true">→</b>
        <span><small>TO</small><code>${escapeHtml(relation.to_entity)}.${escapeHtml(relation.target_field)}</code></span>
      </div>
      <p>${escapeHtml(relation.description || "字段引用已通过闭合校验。")}</p>
      <strong>${formatNumber(relation.edge_count)} 条真实引用边</strong>
    </article>
  `).join("") : '<div class="empty-state">当前环境没有声明闭合实体关系</div>';
}

function renderCapabilities(environment) {
  const families = environment.operation_families || [];
  const capabilities = environment.capabilities || [];
  $("#capability-meta").textContent = `${formatNumber(environment.metrics.capability_count)} 个能力原子`;
  $("#operation-summary").innerHTML = families.map((family) => `<span>${escapeHtml(family)}</span>`).join("");
  $("#capability-list").innerHTML = capabilities.length ? capabilities.slice(0, 18).map((capability) => `
    <div class="capability-item"><span>${escapeHtml(capability.operation_family)}</span><p>${escapeHtml(capability.description || capability.capability_id)}</p></div>
  `).join("") : '<div class="empty-state">画像记录了能力数量，但研究报告没有额外能力说明</div>';
}

function renderResourceFilter() {
  $$("#resource-filter button").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === state.resourceFilter);
    button.onclick = () => {
      state.resourceFilter = button.dataset.filter;
      renderResourceFilter();
      renderResources(currentEnvironment());
    };
  });
}

function renderResources(environment) {
  const resources = (environment.resources || []).filter(
    (resource) => state.resourceFilter === "all" || resource.data_type === state.resourceFilter,
  );
  $("#resource-table").innerHTML = resources.length ? resources.map((resource) => {
    const copy = typeCopy[resource.data_type] || { short: resource.data_type };
    const lineage = resource.source_resources?.length
      ? resource.source_resources.map((item) => `<code>${escapeHtml(item)}</code>`).join(" ")
      : resource.source_urls?.length
        ? `${resource.source_urls.length} 个外部来源`
        : "起始资源";
    return `
      <tr>
        <td><strong>${escapeHtml(resource.name || resource.resource_id)}</strong><code>${escapeHtml(resource.resource_id)}</code></td>
        <td><span class="resource-type ${escapeHtml(resource.data_type)}">${escapeHtml(resource.data_type)}</span><small>${escapeHtml(copy.short)}</small><p>${escapeHtml(resource.description)}</p></td>
        <td><strong>${formatNumber(resource.file_count)}</strong><small>${formatBytes(resource.bytes)} · ${escapeHtml(resource.format)}</small></td>
        <td class="lineage-cell">${lineage}</td>
        <td><code>${escapeHtml(resource.path)}</code></td>
        <td><span class="permission ${resource.writable ? "write" : "read"}">${resource.writable ? "可写" : "只读"}</span></td>
      </tr>`;
  }).join("") : '<tr><td colspan="6"><div class="empty-state">没有匹配的资源</div></td></tr>';
}

function renderWarnings(environment) {
  const warnings = environment.warnings || [];
  const validation = environment.validation || {};
  const items = [
    {
      severity: validation.valid ? "success" : "critical",
      message: validation.valid
        ? "最终环境已通过 Schema、路径、来源、实体字段和关系校验。"
        : `最终校验失败，共 ${(validation.errors || []).length} 项错误。`,
    },
    ...warnings,
  ];
  $("#warning-list").innerHTML = items.map((item) => `
    <div class="audit-item ${escapeHtml(item.severity)}">
      <i aria-hidden="true"></i><span>${escapeHtml(item.message)}</span>
    </div>
  `).join("");
}

function showError(error) {
  console.error(error);
  $("#environment-name").textContent = "无法载入环境快照";
  $("#environment-description").textContent = `${error.message}。请重新生成 dashboard/data/environments.json。`;
  $("#quality-badge").textContent = "ERROR";
  $("#quality-badge").classList.add("bad");
}

$("#refresh-button").addEventListener("click", () => loadSnapshot().catch(showError));
loadSnapshot().catch(showError);
