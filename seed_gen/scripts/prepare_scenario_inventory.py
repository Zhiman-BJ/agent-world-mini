"""Normalize every numbered L3 without modifying the user's classification."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

SOURCE = Path('seed_gen/半导体场景分类.md')
BASE = Path('seed_gen/scenario_collection')

# Explicit editorial boundaries for the source's freeform numbered lists.
# These are plans, not claims about a package's implemented functionality.
SUPPLEMENTS = {
    '06.06.01': ('关联设备报警、状态与压力轨迹，定位触发报警的传感器或工艺步骤', 'secsgem, ruptures', '关联报警时间与变化点并排除错误设备候选'),
    '06.06.02': ('在设备停机、备件和交期约束下安排预防性维护', 'SimPy, PyJobShop', '比较提前维护与延期的停机和延期成本'),
    '06.06.03': ('分析泵和阀门等部件寿命及未失效部件的删失记录', 'reliability, SurPyval', '拟合寿命分布并确定更换分位点'),
    '06.06.04': ('从故障及维修事件计算设备可靠性与可用率', 'reliability, SimPy', '重建运行和维修时段，核对MTBF、MTTR及可用率'),
    '06.06.05': ('利用设备轨迹漂移和历史失效记录决定预测性维护时机', 'River, reliability, SimPy', '检测漂移并在固定故障仿真下评价维护策略'),
    '07.06.01': ('导入光谱椭偏Psi/Delta数据并校验角度、波长和单位', 'pyElli', '修正角度或波长单位后恢复可用测量数据'),
    '07.06.02': ('构建基底、薄膜与环境的椭偏光学堆栈', 'pyElli', '调整层顺序和膜厚并验证正向光谱'),
    '07.06.03': ('为透明或吸收薄膜选择色散模型与参数范围', 'pyElli', '比较固定合成数据上的色散模型误差'),
    '07.06.04': ('由多波长椭偏数据反演膜厚与折射率消光系数', 'pyElli, lmfit', '拟合已知23nm薄膜并断言厚度误差小于1nm'),
    '07.06.05': ('检查椭偏拟合残差、参数可辨识性和独立验证波长', 'pyElli, lmfit', '拒绝低训练误差但验证误差过大的模型'),
    '07.07.01': ('将晶圆测试坐标与软硬bin映射为逐die状态', 'Semi-ATE-STDF, wafermap', '修复坐标变换并核对die数和bin计数'),
    '07.07.02': ('对晶圆失败die进行空间聚类并定位集中失效区', 'scikit-learn, scipy', '定位固定注入簇并排除离散噪声'),
    '07.07.03': ('识别晶圆上的环状、中心和划痕失效图样', 'scikit-learn, scipy', '在有固定标签的合成晶圆上分类并检验置信度'),
    '07.07.04': ('比较同批和跨批晶圆缺陷图样复现程度', 'scikit-learn, scipy', '检出固定注入的重复图样并避免坐标错位'),
    '07.07.05': ('关联晶圆空间缺陷与设备腔室、工艺步骤及光罩位置', 'networkx, scipy', '沿固定制造谱系排序候选原因并保留反证'),
    '08.02.01': ('将制造、测试和维修日志对齐为具有来源的事件时间线', 'networkx, pandas', '修复时区与重复事件并重建跨域时间线'),
    '08.02.02': ('按产品、工艺和时间匹配对照批次分析质量差异', 'pandas, scipy', '控制产品混杂后比较固定异常批次和对照批次'),
    '08.02.03': ('识别工艺变更前后的质量变化点及关联影响', 'ruptures, scipy', '定位注入的工艺变更并核对影响窗口'),
    '08.02.04': ('在制造谱系和测量证据中排序质量问题的候选原因', 'networkx, scipy', '用支持证据与反证排序候选，避免把相关性当因果'),
    '08.02.05': ('汇总质量问题的纠正预防措施、验证记录和证据链', 'networkx, pandas', '生成可追溯措施清单并拒绝缺失验证证据的结案'),
}


def normalize(text: str) -> list[dict]:
    rows, l1, l2, headers = [], '', '', []
    for lineno, line in enumerate(text.splitlines(), 1):
        match = re.match(r'^(#{1,2})\s+(\d{2}(?:\.\d{2})?\s+.+)$', line)
        if match:
            if len(match[1]) == 1:
                l1, l2 = match[2], ''
            else:
                l2 = match[2]
        cells = [s.strip().replace('**', '') for s in line.strip().strip('|').split('|')]
        if line.startswith('|') and 'L3' in cells:
            headers = cells
            continue
        numbered = re.search(r'(?<!\d)(0[1-8]\.\d{2}\.\d{2})\s+([^|`\n]+)', line)
        if not numbered:
            continue
        sid, label = numbered[1], numbered[2].replace('**', '').strip()
        if any(r['scenario_id'] == sid for r in rows):
            raise ValueError(f'Duplicate classification: {sid}')
        if line.startswith('|'):
            if len(headers) != len(cells):
                raise ValueError(f'Malformed table: line {lineno}')
            source = dict(zip(headers, cells))
            l2 = source.get('L2') or l2
            application = next((source[h] for h in ('实际应用场景', '应用场景', '场景', '工程场景', '真实场景', '真实工作', '工程任务', '功能') if source.get(h)), '')
            packages = next((source[h] for h in ('代表包', 'Python Backend', '包', '相关包', '新增包', '新包') if source.get(h)), '')
            task = next((source[h] for h in ('可合成 Agent 任务', '可合成任务', 'Agent task', 'Agent Task', 'Task') if source.get(h)), '')
            relation = next((source[h] for h in ('关系/推荐组合', '关系/组合', '依赖/组合', '推荐组合', '关系', '依赖/关系', '依赖关系', '组合', '推荐 Stack') if source.get(h)), '')
            origin = 'source_table'
        else:
            application, packages, task = SUPPLEMENTS[sid]
            relation = '场景草案：领域实体需后续封装；候选包只提供真实公共能力，待来源和联合筛选核实。'
            source = {'numbered_line': line}
            origin = 'explicit_editorial_supplement'
        if not l1.startswith(sid[:2] + ' ') or not l2.startswith(sid[:5] + ' '):
            raise ValueError(f'Unresolved hierarchy: {sid}: {l1}, {l2}')
        if not application or not packages:
            raise ValueError(f'Unresolved boundary/package: {sid}')
        rows.append({'scenario_id': sid, 'index': len(rows) + 1,
                     'domain': {'level1': l1, 'level2': l2, 'level3': sid + ' ' + label},
                     'application': application, 'candidate_package_text': packages,
                     'relationship_text': relation, 'task_hint': task,
                     'normalization': origin, 'source_line': lineno, 'source_fields': source,
                     'collection_scope': 'preserve_existing_02' if sid.startswith('02.') else 'collect'})
    expected = set(re.findall(r'(?<!\d)(0[1-8]\.\d{2}\.\d{2})(?!\d)', text))
    if {r['scenario_id'] for r in rows} != expected:
        raise ValueError('Classification coverage mismatch')
    return rows


def main():
    text = SOURCE.read_text(encoding='utf-8')
    rows = normalize(text)
    payload = {'classification_source': SOURCE.as_posix(),
               'classification_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
               'scope': '01,03,04,05,06,07,08; preserve existing 02 outputs',
               'counts_by_l1': dict(sorted(Counter(r['scenario_id'][:2] for r in rows).items())),
               'total': len(rows), 'to_collect': sum(r['collection_scope'] == 'collect' for r in rows),
               'scenarios': rows}
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / 'inventory.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 半导体场景规范化清单', '',
             '由原分类逐条生成；保留原L1/L2/L3编号，散列绑定原文。自由列表的应用边界为显式补充，详见inventory.json，不代表已完成调研。', '']
    previous = None
    for row in rows:
        if previous != row['domain']['level1']:
            previous = row['domain']['level1']
            lines += ['# ' + previous, '', '| L2 | L3 | 实际应用场景 | 代表包 |', '| --- | --- | --- | --- |']
        lines.append('| ' + ' | '.join([row['domain']['level2'], row['domain']['level3'], row['application'], row['candidate_package_text']]) + ' |')
    (BASE / 'classification.normalized.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'scenarios'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
