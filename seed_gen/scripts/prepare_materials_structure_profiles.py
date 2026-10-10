"""Explicit joint selections for construction/interchange and crystal symmetry."""
from __future__ import annotations
import copy
import json
from pathlib import Path

from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.select_python_ref_tools import _canonical_sha256

BASE = Path('seed_gen/scenario_collection')
MANIFEST = BASE / 'materials_structure_sources.json'
NORMALIZED = BASE / 'classification.normalized.md'
RAW = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')
CORE = 'pymatgen-core'


def symbol(package, module, name, methods, capability, reason):
    item = {'package': package, 'module': module, 'name': name,
            'type': 'class' if methods is not None else 'function',
            'capability': capability, 'reason': reason}
    if methods is not None:
        item['methods'] = methods.split() if isinstance(methods, str) else methods
    return item


def cls(module, name, methods, capability, reason):
    return symbol(CORE, 'pymatgen.' + module, name, methods, capability, reason)


def task(identifier, description, initial, steps, assertions, fixture):
    return {'id': identifier, 'description': description, 'initial_state': initial,
            'fixture': fixture, 'steps': [{'action': text, 'tool_refs': refs} for text, refs in steps],
            'assertions': assertions, 'validation_status': 'passed_fixed_fixture'}


def main():
    inventory = read(BASE / 'inventory.json')
    checks = {p['package']: p for p in read(BASE / 'research/materials_source_checks.json')}
    specs = {s['name']: s for s in read(MANIFEST)}
    payloads = {n: read(RAW / f'{n}_{s["tag"]}.json') for n, s in specs.items()}
    web = {r['url']: r for p in sorted((BASE/'research').glob('*web/index.json')) for r in read(p)}
    def source(url, evidence, entities, tools, tasks):
        entry = copy.deepcopy(web[url])
        require(entry['status'] == 200 and entry['title'], 'Source page not inspected')
        entry['relevance'] = {'evidence': evidence, 'entities': entities, 'tools': tools, 'tasks': tasks}
        return entry
    lattice = cls('core.lattice', 'Lattice', '__init__ cubic tetragonal orthorhombic hexagonal from_parameters matrix lengths angles volume get_cartesian_coords get_fractional_coords reciprocal_lattice scale as_dict from_dict', 'state', '显式晶格单位与坐标变换、常见晶胞构造及持久化；不纳入长尾格规约和冗余别名。')
    composition = cls('core.composition', 'Composition', '__init__ get_el_amt_dict get_atomic_fraction reduced_formula num_atoms almost_equals', 'observe', '检验元素计数、掺杂比例和标准化前后的化学组成。')
    collection = cls('core.structure', 'SiteCollection', 'num_sites composition atomic_numbers indices_from_symbol cart_coords is_valid', 'observe', 'Structure继承的真实定义位置；保留位点数、组成、原子编号及近邻冲突检查。')
    collection['construction_reason'] = 'SiteCollection是抽象公共基类，不直接构造；实际对象由Structure.__init__或IStructure.from_spacegroup产生。'
    immutable = cls('core.structure', 'IStructure', 'from_spacegroup copy volume lattice frac_coords as_dict from_dict to from_file get_distance get_primitive_structure', 'state', '保留Structure继承的构造、读写、原胞和坐标查询，避免把继承方法伪造为直接定义。')
    immutable['construction_reason'] = '使用已选Structure.__init__创建可变结构；from_spacegroup等继承类方法通过Structure调用。'
    mutable = cls('core.structure', 'Structure', '__init__ make_supercell replace remove_sites append translate_sites rotate_sites apply_strain scale_lattice sort', 'edit', '保留超胞、掺杂、位点几何和应变等常用变换；不暴露未配置的DFT或ML松弛后端。')
    structure_symbols = [lattice, composition, collection, immutable, mutable,
        cls('io.ase', 'AseAtomsAdaptor', 'get_atoms get_structure', 'bridge', '明确pymatgen规范状态与ASE文件读写对象的双向桥接，固定晶格、坐标、物种和周期性。'),
        symbol('ase', 'ase.io.formats', 'read', None, 'bridge', '从明确格式的本地原子文件恢复ASE对象，不重复暴露各格式低层解析器。'),
        symbol('ase', 'ase.io.formats', 'write', None, 'bridge', '保存extxyz等结构交换文件，配合回读检查。'),
        cls('core.surface', 'SlabGenerator', '__init__ get_slab get_slabs', 'interface', '从指定Miller面构造薄膜和基底，不纳入表面重构或修键优化。'),
        cls('core.surface', 'Slab', 'surface_area normal as_dict from_dict', 'interface', '通过SlabGenerator工厂得到薄层；保留面积、法向和保存恢复。'),
        cls('core.interface', 'Interface', 'from_slabs gap vacuum_over_film in_plane_offset substrate_indices film_indices substrate film as_dict from_dict', 'interface', '同平面晶格的薄膜/基底堆叠、间隙与真空配置及可审计分组；不假定已求得能量稳定界面。'),
    ]
    structure_symbols[-2]['construction_reason'] = '通过已选SlabGenerator.get_slab/get_slabs构造，避免再次要求用户填内部有向晶胞参数。'
    structure_symbols[-1]['construction_reason'] = '使用已选Interface.from_slabs创建并设置film/substrate标记；不直接手填底层构造参数。'
    structure_symbols[5]['construction_reason'] = 'AseAtomsAdaptor为静态方法适配器，直接调用get_atoms/get_structure无需构造；两种方向已在固定样例执行。'
    symmetry_symbols = [copy.deepcopy(x) for x in [lattice, composition, collection, immutable, mutable]]
    symmetry_symbols += [symbol('spglib', module, name, None, cap, reason) for module, name, cap, reason in [
        ('spglib.spg', 'get_symmetry_dataset', 'symmetry', '主分析入口，一次返回空间群、等价位点和标准化映射，避免再暴露重复wrapper。'),
        ('spglib.spg', 'get_symmetry', 'symmetry', '读取旋转和平移用于检查对称操作是否作用在相同物种上。'),
        ('spglib.spg', 'get_spacegroup_type', 'symmetry', 'Hall编号转空间群设置元数据，区分不同标准设置。'),
        ('spglib.cell', 'standardize_cell', 'standardize', '统一控制常规/原胞及是否理想化，不重复加入refine_cell/find_primitive薄包装。'),
        ('spglib.reduce', 'niggli_reduce', 'standardize', '规约晶格表示以排除基矢选择造成的比较歧义。'),
        ('spglib.reduce', 'delaunay_reduce', 'standardize', '互补的晶格规约诊断，供不良输入晶胞处理。'),
    ]]
    # A separate wrapper would rerun the same backend without adding state or capability.
    symmetry_symbols[3]['methods'].remove('get_primitive_structure')
    designs = {
        '01.01.01': {
            'description': '以pymatgen Structure为规范状态，构建晶胞、超胞与替位掺杂，并生成同平面晶格的薄膜/基底界面；ASE仅负责对象与文件交换。校验组成、周期坐标、几何间隙和保存恢复，不涉及能量稳定性或外部DFT。',
            'decisions': [(CORE,'primary','统一晶格、组成、位点和界面状态，覆盖构造变换与验证所需信息。'),
                          ('ase','complement','通过AseAtomsAdaptor连接文件读写；不暴露另一套重复的晶体编辑/计算器API。'),
                          ('jarvis-tools','excluded','独立Atoms状态和超胞/结构转换与主实现重复；数据库、描述符和外部DFT不在本场景，独有能力留给对应L3。')],
            'capabilities': ['state','edit','observe','bridge','interface'], 'symbols': structure_symbols,
            'sources': [
                source('https://pymatgen.org/usage.html','官方使用教程定义Lattice/Structure/Composition，展示JSON序列化与结构处理。',['structure','lattice','composition'],['from_spacegroup','make_supercell','as_dict','from_dict'],['晶胞超胞构建与结果保存']),
                source('https://pymatgen.org/pymatgen.io.html','AseAtomsAdaptor部分明确提供ASE Atoms与pymatgen对象桥接。',['structure','exchange_artifact'],['get_atoms','get_structure'],['ASE往返后组成和晶格保持']),
                source('https://pymatgen.org/pymatgen.core.html','Structure.make_supercell、replace及Interface.from_slabs包含参数和返回说明；界面分组与间隙可独立查询。',['site','film','substrate','interface'],['replace','make_supercell','from_slabs'],['修复错误掺杂和界面间隙']),
                source('https://jarvis-tools.readthedocs.io/en/master/tutorials.html','原子结构教程用jarvis.core.Atoms构造Si结构，说明物种、坐标、晶格与POSCAR；异质结构教程提供候选能力线索。',['structure'],['Atoms'],['用于比较替代状态体系，本场景排除重复后端']),
            ],
            'bridges': [{'from':'pymatgen.Structure','to':'ase.Atoms -> extxyz -> ase.Atoms -> Structure','tool':'pymatgen.io.ase.AseAtomsAdaptor','contract':'长度为Angstrom，晶格行向量、周期性、逐位点元素和分数坐标在1e-8容差保持；附加计算器不在本次桥接。'}],
            'tasks': [
                task('gaas_supercell_interchange','构建GaAs常规胞，生成2×2×2超胞并将一个Ga替为Al；经ASE保存和回读，断言64个位点、体积增至8倍、Ga31Al1As32及坐标和晶格保持。','晶格常数5.653 Angstrom的闪锌矿GaAs，无已有结构或计算结果。',[
                    ('创建与复制晶体',['pymatgen.core.lattice.Lattice.cubic','pymatgen.core.structure.IStructure.from_spacegroup','pymatgen.core.structure.IStructure.copy']),
                    ('生成超胞并指定Ga子晶格替换',['pymatgen.core.structure.Structure.make_supercell','pymatgen.core.structure.SiteCollection.indices_from_symbol','pymatgen.core.structure.Structure.replace']),
                    ('桥接、写入、回读并核验',['pymatgen.io.ase.AseAtomsAdaptor.get_atoms','ase.io.formats.write','ase.io.formats.read','pymatgen.io.ase.AseAtomsAdaptor.get_structure','pymatgen.core.composition.Composition.get_el_amt_dict']),
                ],['64位点','volume=8*a^3','Ga31Al1As32','分数坐标和晶格atol1e-8'],'verify_materials_structure.py:gaas and 2x2x2'),
                task('repair_doping_sublattice','识别将As误替为Al的错误掺杂，恢复参考超胞后改为Ga子晶格替位，保存并重载结构；使用不变的Ga31Al1As32目标检验修复。','错误候选Ga32Al1As31与只读Ga32As32参考超胞。',[
                    ('读组成并定位错误物种',['pymatgen.core.structure.SiteCollection.composition','pymatgen.core.composition.Composition.get_el_amt_dict']),
                    ('从参考恢复并修正替位',['pymatgen.core.structure.IStructure.copy','pymatgen.core.structure.Structure.replace']),
                    ('保存与重载后复验',['pymatgen.core.structure.IStructure.as_dict','pymatgen.core.structure.IStructure.from_dict','pymatgen.core.structure.SiteCollection.is_valid']),
                ],['错误组成必须失败','修复后Ga31Al1As32','JSON往返组成一致','最小间距>0.5 Angstrom'],'verify_materials_structure.py:wrong versus repaired'),
                task('gaas_alas_interface_gap','生成GaAs基底与同晶格常数AlAs薄膜的(001)薄层，构造2 Angstrom间隙及12 Angstrom真空的界面；将错误0.2 Angstrom间隙修复为2，并以实际笛卡尔坐标和分组元素计数验收。','固定GaAs/AlAs晶体，薄层最小厚度6 Angstrom；人为共格几何，不声称真实平衡晶格。',[
                    ('生成基底与薄膜',['pymatgen.core.surface.SlabGenerator.__init__','pymatgen.core.surface.SlabGenerator.get_slab']),
                    ('堆叠并修复间隙',['pymatgen.core.interface.Interface.from_slabs','pymatgen.core.interface.Interface.gap']),
                    ('按film/substrate分组检查实际几何',['pymatgen.core.interface.Interface.film','pymatgen.core.interface.Interface.substrate','pymatgen.core.structure.SiteCollection.cart_coords','pymatgen.core.composition.Composition.get_el_amt_dict']),
                ],['原子总数和两组物种计数守恒','actual z gap=2 Angstrom at1e-10','错误0.2间隙不通过同一几何目标'],'verify_materials_structure.py:Interface.from_slabs'),
            ],
        },
        '01.02.01': {
            'description': '以pymatgen结构记录晶格、分数坐标及原子编号，由spglib统一完成非磁晶体对称性和标准晶胞分析；区分基矢变换、数值容差与真实对称破缺，保留独立结构目标及修复链。',
            'decisions': [('spglib','primary','承担空间群、等价位点和标准化；避免重复暴露pymatgen SpacegroupAnalyzer包装的同一后端。'),
                          (CORE,'complement','提供可修改、可保存的结构与组成实体，显式输出spglib tuple；只选对称分析所需结构能力。')],
            'capabilities': ['state','edit','observe','symmetry','standardize'], 'symbols': symmetry_symbols,
            'sources': [
                source('https://spglib.readthedocs.io/en/stable/python-interface.html','Python接口说明cell=(lattice,positions,numbers)，晶格在Python中以行向量表示、位置用分数坐标。',['cell','symmetry_dataset'],['get_symmetry_dataset','standardize_cell'],['输入单位校验、原胞标准化']),
                source('https://spglib.readthedocs.io/en/stable/definition.html','对称操作、分数坐标、基矢变换和原点移动的定义，用于标准晶胞等价性及物种映射检查。',['basis','site_mapping'],['standardize_cell','get_symmetry'],['区分坐标表示变化与真实对称破缺']),
                source('https://pymatgen.org/pymatgen.symmetry.html','SpacegroupAnalyzer说明symprec、primitive/conventional cell和site property保留限制；本选集直接使用其底层spglib，避免重复wrapper。',['structure','tolerance','standard_cell'],['get_symmetry_dataset'],['识别位移后的空间群变化并按固定容差复验']),
                source('https://pymatgen.org/usage.html','结构对象、晶格和组成提供可持久化的场景状态。',['structure','composition'],['Structure','Lattice','as_dict'],['结构变更、保存和恢复']),
            ],
            'bridges': [{'from':'pymatgen.Structure','to':'spglib cell tuple','tool':'显式读取已选Lattice.matrix/IStructure.frac_coords/SiteCollection.atomic_numbers','contract':'矩阵行向量，Angstrom长度，Nx3分数坐标，N个整数原子序数；不得传入笛卡尔位置。标准化返回的新晶胞用Structure.__init__重建并检查组成比例。'}],
            'tasks': [
                task('standardize_gaas_cell','将8原子GaAs常规胞转换为spglib输入，获取空间群并生成标准原胞；重建结构，断言空间群216、2个位点、体积a³/4及Ga:As=1:1。','已知a=5.653 Angstrom、空间群216的GaAs，symprec固定1e-5 Angstrom。',[
                    ('构造结构并读取真实输入表示',['pymatgen.core.structure.IStructure.from_spacegroup','pymatgen.core.lattice.Lattice.matrix','pymatgen.core.structure.IStructure.frac_coords','pymatgen.core.structure.SiteCollection.atomic_numbers']),
                    ('空间群识别和原胞标准化',['spglib.spg.get_symmetry_dataset','spglib.cell.standardize_cell']),
                    ('重建并校验组分和体积',['pymatgen.core.structure.Structure.__init__','pymatgen.core.composition.Composition.get_el_amt_dict','pymatgen.core.lattice.Lattice.volume']),
                ],['group216','8->2 atoms','V=a^3/4','Ga:As=1:1'],'verify_materials_structure.py:standardize_gaas_cell'),
                task('repair_symmetry_breaking','检查单原子位移导致的GaAs对称破缺；在固定symprec下验证空间群已偏离216，然后恢复原子位置并再次核验空间群与周期坐标。','位点0的分数坐标偏移(0.017,0.009,0.023)，只读理想参考结构和固定容差。',[
                    ('产生和诊断位移结构',['pymatgen.core.structure.IStructure.copy','pymatgen.core.structure.Structure.translate_sites','spglib.spg.get_symmetry_dataset']),
                    ('修复位置并使用原判据复验',['pymatgen.core.structure.Structure.translate_sites','spglib.spg.get_symmetry_dataset','pymatgen.core.structure.IStructure.frac_coords']),
                ],['偏移结构空间群不是216','修复后216','周期坐标差atol1e-12','不得放宽symprec冒充修复'],'verify_materials_structure.py:repair_symmetry_breaking'),
            ],
        },
    }
    for sid, design in designs.items():
        row = next(r for r in inventory['scenarios'] if r['scenario_id'] == sid)
        packages = []
        for name, role, reason in design['decisions']:
            spec = specs[name]
            packages.append({'name':name,'version':spec['tag'],'role':role,'reason':reason,
                             'raw_path':(RAW/f'{name}_{spec["tag"]}.json').as_posix(),
                             'raw_sha256':_canonical_sha256(payloads[name]),'release_manifest':MANIFEST.as_posix(),
                             'release_research':checks[name]})
        symbols = design['symbols']
        for selected in symbols:
            originals = [t for t in payloads[selected['package']][0]['init_ref_tools'] if all(t[k] == selected[k] for k in ('module','name','type'))]
            require(len(originals)==1, f'Unknown/ambiguous source selection: {selected}')
            raw = originals[0]
            if not raw.get('description') or any(not m.get('description') for m in raw.get('function',[]) if m['name'] in selected.get('methods',[])):
                selected['missing_description_reason'] = '场景必要接口的来源说明为空，保留原值；选择理由见reason，不补造docstring。'
        runtime = BASE / f'runtime/materials_structure/{sid}.json'
        research = {'scenario_id':sid,'checked_on':'2026-09-17','reviewed_design':design,
                    'source_checks': [checks[p['name']] for p in packages],
                    'runtime_report':runtime.as_posix(), 'source_documentation_warning':'pymatgen网页为2026.7.27文档；选集和签名始终取v2026.8.30源码。ASE旧文档404记录保留，不当作成功来源。'}
        research_path = BASE / f'research/{sid}.json'
        research_path.write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        profile = {'profile_version':'joint-scenario-1.0','scenario_id':sid,'index':row['index'],'checked_on':'2026-09-17',
                   'classification_source':NORMALIZED.as_posix(),'classification_sha256':file_sha(NORMALIZED),
                   'classification_upstream':{'path':inventory['classification_source'],'sha256':inventory['classification_sha256']},
                   'research_file':research_path.as_posix(),'research_sha256':file_sha(research_path),
                   'description':design['description'],'research_sources':design['sources'],'packages':packages,
                   'package_relation':'；'.join(f'{n}: {role}，{reason}' for n,role,reason in design['decisions']),
                   'entities':[
                       {'name':'structure','identity':'structure_id','attributes':['revision','lattice[3,3] Angstrom','sites: species + fractional coordinates','composition','parent_id'],
                        'lifecycle':'create/load -> copy/edit -> observe -> save/reload/reset；变更坐标、物种或晶格后，所有symmetry/bridge/interface派生产物失效。'},
                       {'name':'result','identity':'result_id','attributes':['source_structure_id','source_revision','settings','status','artifact_path','metrics'],
                        'lifecycle':'按结构版本生成，修改上游后标记stale；验收不能读取旧结果冒充当前解。'},
                   ] + ([{'name':'interface','identity':'interface_id','attributes':['substrate_indices','film_indices','gap Angstrom','vacuum_over_film Angstrom','in_plane_offset'],'lifecycle':'从两层创建，修改间隙/偏移后重算几何指标；只做结构几何。'}] if sid=='01.01.01' else [{'name':'symmetry_dataset','identity':'analysis_id','attributes':['spacegroup_number','hall_number','symprec Angstrom','equivalent_atoms','transformation_matrix'],'lifecycle':'绑定结构版本和容差；位点或容差修改使旧分析过期。'}]),
                   'capabilities':[{'id':c,'required':True,'description':{'state':'构造、持久化和重置','edit':'受控结构变换','observe':'结构与组成验收','bridge':'双向文件和对象交换','interface':'薄膜与基底界面几何','symmetry':'空间群和对称操作','standardize':'标准晶胞与基矢规约'}[c]} for c in design['capabilities']],
                   'symbols':symbols,'target_all_func':{'min':50,'max':200},'bridges':design['bridges'],
                   'runtime_infrastructure':[{'reference':'python.numpy_json_pathlib','kind':'infrastructure','reason':'数组表示、独立计数/行列式/容差断言与本地JSON存储；不提供额外物理求解器。'}],
                   'boundaries':['参考API不是已实现Agent工具服务器；稳定ID、权限、状态失效和reset隔离需后续实现。','固定样例仅覆盖报告中的调用；未运行全部精选接口。','无DFT/能量松弛/磁性空间群；结构合法不代表热力学稳定。','公开继承方法按源码定义类保留；通过Structure调用时需后续facade完成对象绑定。'],
                   'tasks':design['tasks'],'runtime_reports':[{'path':runtime.as_posix(),'sha256':file_sha(runtime),'scope':'固定任务正常/错误/修复断言'}]}
        destination = BASE/'profiles'/f'{sid}.json'
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(destination)


if __name__ == '__main__':
    main()
