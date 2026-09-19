"""Normalize the three reviewed pilot research records into explicit profiles.

Run only after reviewing source/selection changes, then use the builder's
--verify-sources. This is not a bypass for changed raw hashes.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.select_python_ref_tools import _canonical_sha256

BASE = Path('seed_gen/scenario_pilot')
TABLE = Path('seed_gen/半导体场景分类.md')
PILOTS = [
    ('tcad', '02.01.01', '主实现 DEVSIM；Sesame 为独立状态体系的替代求解器，当前基础任务不混用。', 'runtime/tcad/report.json'),
    ('mesh', '02.03.03', 'scikit-fem 为主要有限元后端；meshio 补充网格/场读写，通过 from_meshio/to_meshio 转换，保留拓扑和点数据检查。', 'runtime/mesh/verification.json'),
    ('pic', '02.07.01', 'SAX 为主要光子电路后端；Simphony 对当前版本有硬依赖冲突，且本试点不需要其独有器件库，整体排除。', 'runtime/pic/validation.json'),
]


def main():
    manifest_by_name = {}
    for path in Path('seed_gen').glob('pypi_*sources.json'):
        specs = read(path)
        if isinstance(specs, list):
            for spec in specs:
                if isinstance(spec, dict) and all(k in spec for k in ('name', 'tag', 'commit')):
                    identity = (spec['name'], spec['tag'])
                    require(identity not in manifest_by_name, f'Ambiguous source manifest: {identity}')
                    manifest_by_name[identity] = path.as_posix()
    for index, (label, scenario_id, relation, runtime_path) in enumerate(PILOTS, 1):
        research_path = BASE / 'work' / (label + '_research.json')
        research = read(research_path)
        require(research['scenario_id'] == scenario_id, 'Research identity mismatch')
        date = research.get('checked_on', research.get('checked_at'))
        packages = []
        for candidate in research['packages']:
            name = candidate.get('name', candidate.get('package'))
            raw_path = candidate.get('raw_file', candidate.get('raw_index'))
            payload = read(raw_path)
            info = payload[0]['environment']['basic_info']
            require(info['name'] == name, 'Wrong raw package')
            decision = next(d for d in research['package_decisions'] if d['package'] == name)
            role = decision['decision']
            if role.startswith('exclude'):
                role = 'excluded'
            require(role in ('primary', 'complement', 'excluded'), f'Unreviewed package role: {name}')
            packages.append({'name': name, 'version': info['version'], 'role': role,
                             'reason': decision['reason'], 'raw_path': raw_path,
                             'raw_sha256': _canonical_sha256(payload),
                             'release_manifest': manifest_by_name[name, info['version']],
                             'release_research': candidate})
        symbols = copy.deepcopy(research['selected_symbols'])
        raw_index = {p['name']: {(t['module'], t['name'], t['type']): t for t in read(p['raw_path'])[0]['init_ref_tools']}
                     for p in packages}
        for spec in symbols:
            raw = raw_index[spec['package']][spec['module'], spec['name'], spec['type']]
            requested = spec.get('methods', [])
            if not raw.get('description') or any(not m.get('description') for m in raw.get('function', []) if m['name'] in requested):
                spec.setdefault('missing_description_reason',
                    '经场景选择保留的必要来源接口，源码说明为空；选择依据见reason，未补写源docstring。')
            if spec['module'] == 'skfem.assembly.basis.abstract_basis' and spec['name'] == 'AbstractBasis':
                spec['construction_reason'] = '通过已选CellBasis.__init__创建具体有限元基；这里只保留AbstractBasis定义的继承查询/插值方法，不向Agent提供抽象基类构造。'
        sources = []
        for source in research['sources']:
            record = copy.deepcopy(source)
            record['status'] = source.get('status', source.get('http_status'))
            record['checked_on'] = source.get('checked_on', source.get('checked_at', date))
            record['relevance'] = {
                'entities': source.get('entity_references', source.get('entity_refs', source.get('entity_relevance', []))),
                'tools': source.get('tool_references', source.get('tool_refs', source.get('tool_relevance', []))),
                'tasks': source.get('task_value', source.get('task_refs', source.get('task_relevance', []))),
                'evidence': source['evidence'],
            }
            sources.append(record)
        infra = []
        for entry in research.get('runtime_infrastructure', []):
            infra.append({**entry, 'reference': entry.get('reference', entry.get('entry'))})
        if label == 'pic':
            infra.extend([
                {'reference': 'runtime.compiled_sax_model', 'kind': 'derived_callable', 'derived_from': 'sax.circuits.circuit',
                 'reason': 'sax.circuit 返回 callable；实跑调用，不伪造进静态函数索引。'},
                {'reference': 'numpy_verifier_and_python_artifacts', 'reason': '解析式、固定数组、YAML/JSON/NPZ与指标序列化基础设施。'},
            ])
        if label == 'tcad':
            infra.extend([
                {'reference': 'python_named_state_and_callbacks', 'reason': '命名设备/模型引用及rampbias受控回调；后续封装需对象ID和回调注册。'},
                {'reference': 'numpy_verifier_and_python_artifacts', 'reason': '解析场/电荷公式、有限值和误差、JSON与文件哈希基础设施。'},
            ])
        runtime = BASE / runtime_path
        profile = {
            'profile_version': 'joint-scenario-1.0', 'scenario_id': scenario_id, 'index': index,
            'checked_on': date, 'classification_source': TABLE.as_posix(), 'classification_sha256': file_sha(TABLE),
            'research_file': research_path.as_posix(), 'research_sha256': file_sha(research_path),
            'description': research['scope'], 'research_sources': sources,
            'packages': packages, 'package_relation': relation,
            'reviewed_package_decisions': research['package_decisions'],
            'entities': research['entities'], 'capabilities': research['required_capabilities'],
            'symbols': symbols, 'target_all_func': {'min': 50, 'max': 200},
            'bridges': research.get('dataflow_contracts', []),
            'runtime_infrastructure': infra,
            'boundaries': research.get('interface_gaps', research.get('limitations', research.get('missing_or_runtime_contracts', []))),
            'tasks': research['tasks'],
            'runtime_reports': ([{'path': runtime.as_posix(), 'sha256': file_sha(runtime),
                                  'scope': '仅报告中实际执行的固定任务；非全选集运行验证'}] if runtime.exists() else []),
        }
        if label == 'mesh':
            profile['bridges'] = [
                {'from': 'meshio.Mesh', 'to': 'skfem.MeshTri1', 'tool': 'skfem.io.meshio.from_meshio',
                 'contract': 'point坐标、triangle连通性、区域标签；读后核对points/t/cell_data，边界按坐标重建并检查自由度。'},
                {'from': 'skfem.MeshTri1 + solution nodal array', 'to': 'meshio.Mesh -> VTU',
                 'tool': 'skfem.io.meshio.to_meshio', 'contract': 'point_data.u长度必须等于网格点数；write/read后逐点数值一致。'},
            ]
        destination = BASE / 'profiles' / f'{scenario_id}.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        print(destination)


if __name__ == '__main__':
    main()
