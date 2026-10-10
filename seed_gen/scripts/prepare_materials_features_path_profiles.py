"""Reviewed descriptor and k-path selections from full source candidate pools."""
from __future__ import annotations
import copy
import json
from pathlib import Path

from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require, counts
from seed_gen.scripts.prepare_materials_structure_profiles import BASE, MANIFEST, NORMALIZED, RAW, CORE, cls, symbol, task
from seed_gen.scripts.select_python_ref_tools import _canonical_sha256


def main():
    specs = {s['name']:s for s in read(MANIFEST)}
    checks = {s['package']:s for s in read(BASE/'research/materials_source_checks.json')}
    inventory = read(BASE/'inventory.json')
    payloads = {n:read(RAW/f'{n}_{s["tag"]}.json') for n,s in specs.items()}
    web = {r['url']:r for p in sorted((BASE/'research').glob('*web/index.json')) for r in read(p)}
    def source(url,evidence,entity,tools,task_value):
        result = copy.deepcopy(web[url])
        require(result['status']==200 and result['title'],'Inspected source required')
        result['relevance']={'evidence':evidence,'entities':entity,'tools':tools,'tasks':task_value}
        return result
    def feature(module,name,methods,cap,why):
        return symbol('matminer','matminer.'+module,name,methods,cap,why)
    features = [
        feature('featurizers.base','BaseFeaturizer','set_n_jobs precheck_dataframe featurize_dataframe featurize_many','batch','保留可控单进程批量计算、输入预检与逐行错误隔离，避免暴露内部wrapper。'),
        feature('featurizers.composition.composite','ElementProperty','__init__ from_preset featurize feature_labels','composition_features','带标签的元素属性加权统计，数据源和stats显式固定。'),
        feature('featurizers.composition.element','ElementFraction','__init__ featurize feature_labels','composition_features','归一化元素组成，提供可独立计算的分数验收。'),
        feature('featurizers.composition.element','Stoichiometry','__init__ featurize feature_labels','composition_features','少量p范数组成特征，可由固定配比解析计算。'),
        feature('featurizers.composition.orbital','ValenceOrbital','__init__ featurize feature_labels','composition_features','半导体材料常用价轨道占据描述符，避免大量合金专项长尾特征。'),
        feature('featurizers.conversions','StrToComposition','__init__ featurize','conversion','从明确化学式得到pymatgen Composition，不静默修正错误配比。'),
        feature('featurizers.conversions','StructureToComposition','__init__ featurize','conversion','结构特征与组成特征共享同一来源结构版本。'),
        feature('featurizers.structure.order','DensityFeatures','__init__ precheck featurize feature_labels','structure_features','少量可解释的体积、密度和堆积特征。'),
        feature('featurizers.structure.symmetry','GlobalSymmetryFeatures','__init__ featurize feature_labels','structure_features','结构全局对称性特征，保持结果列名和结构身份。'),
        feature('utils.data','PymatgenData','__init__ get_elemental_property','data','固定来源元素属性查询，明确禁用不经审查的NaN填充。'),
        cls('core.composition','Composition','__init__ valid elements num_atoms get_el_amt_dict get_atomic_fraction fractional_composition reduced_formula almost_equals as_dict from_dict','state','组成输入、归一化、有效性和持久化；不把统计特征冒充物性预测。'),
        cls('core.lattice','Lattice','__init__ cubic from_parameters matrix volume as_dict from_dict','state','结构特征需要有单位的晶格和体积。'),
        cls('core.structure','Structure','__init__ replace make_supercell','state','配置结构候选和受控组成变化。'),
        cls('core.structure','IStructure','from_spacegroup as_dict from_dict copy','state','公共结构工厂和状态快照，继承方法按来源定义保留。'),
        cls('core.structure','SiteCollection','composition num_sites is_valid','state','结构到组成桥接和基本有效性查询。'),
    ]
    features[0]['construction_reason']='由具体featurizer构造；BaseFeaturizer作为公共基类只承载继承的批处理方法。'
    features[-2]['construction_reason']='使用Structure构造或from_spacegroup工厂，避免同时构造两种重复状态。'
    features[-1]['construction_reason']='抽象基类不直接构造，由Structure对象继承这些查询接口。'
    kpath = [
        symbol('seekpath','seekpath.getpaths',name,None,'path',why) for name,why in [
            ('get_path','标准原胞、倒易基矢及HPKOT路径主入口，避免重复选择hpkot内部求解器。'),
            ('get_explicit_k_path','生成具有参考间距的显式k点，检查真实相邻点距离。'),
            ('get_path_orig_cell','非标准输入晶胞的k坐标，需遵守超胞路径不等于该超胞第一BZ高对称点的限制。'),
            ('get_explicit_k_path_orig_cell','在原晶胞倒易基下输出显式路径，补齐既有计算输入的转换。'),
        ]
    ] + [
        cls('core.lattice','Lattice','__init__ cubic tetragonal orthorhombic hexagonal from_parameters matrix volume lengths angles reciprocal_lattice reciprocal_lattice_crystallographic get_cartesian_coords get_fractional_coords as_dict from_dict','state','晶格和倒易基元数据，显式区分2pi约定及单位；支持不同晶系输入。'),
        cls('core.structure','Structure','__init__ apply_strain make_supercell translate_sites','edit','比较应变或超胞后的k路径，变更后旧路径必须失效。'),
        cls('core.structure','IStructure','from_spacegroup copy lattice frac_coords volume as_dict from_dict to from_file','state','初始结构、保存恢复和spglib tuple桥接；不暴露另一套对称性求解包装。'),
        cls('core.structure','SiteCollection','atomic_numbers composition num_sites is_valid','observe','原子物种、数量与结构有效性检查，tuple物种编码不可丢失。'),
        cls('core.composition','Composition','__init__ get_el_amt_dict reduced_formula get_atomic_fraction almost_equals','observe','原胞与输入晶胞的组成比例一致性。'),
    ]
    kpath[-3]['construction_reason']='从Structure.__init__或继承from_spacegroup工厂构造可变结构。'
    kpath[-2]['construction_reason']='由Structure继承的抽象基类查询，不直接实例化。'
    designs = {
        '01.01.03': {
            'description':'面向半导体候选材料的可解释特征环境：化学式或结构转为组成/结构描述符，保留材料ID、输入版本、特征列名、失败行和筛选规则。固定样例验证组成与元素属性统计，不将描述符或排序当作训练后的能带/稳定性预测。',
            'symbols':features,'decisions':[('matminer','primary','统一featurizer和批处理，裁去重复/专项长尾特征。'),(CORE,'complement','维护可追溯的组成和结构对象，作为matminer真实输入。')],
            'capabilities':['state','conversion','composition_features','structure_features','batch','data'],
            'sources':[
                source('https://github.com/hackingmaterials/matminer/blob/v0.10.1/docs_rst/index.rst','发布版官方文档说明组成/位点/结构数值特征和DataFrame转换，并给出坏数据容错转换示例；同时明确matminer本身不包含ML训练。',['material','feature_table','error_row'],['featurize_dataframe','StructureToComposition'],['生成特征并保留失败材料']),
                source('https://github.com/hackingmaterials/matminer/blob/v0.10.1/docs_rst/featurizer_summary.rst','官方按组成、结构等数据来源列出featurizer，支持按能力选择少量代表接口。',['composition','structure','feature_config'],['ElementProperty','ElementFraction','DensityFeatures'],['固定描述符配置与有标签输出']),
                source('https://github.com/hackingmaterials/matminer/blob/v0.10.1/src/matminer/featurizers/composition/composite.py','ElementProperty源码说明属性按化学计量加权、标签为数据源+统计量+属性，并公开NaN处理策略。',['data_source','feature_config'],['ElementProperty.__init__','featurize','feature_labels'],['使用元素Z和已知配比建立独立统计oracle']),
                source('https://pymatgen.org/usage.html','Composition映射元素到数量，Structure维护位点与晶格，as_dict/from_dict提供保存恢复。',['composition','material'],['Composition','Structure'],['检测错误化学式比例并修复']),
            ],
            'entities':[
                {'name':'material','identity':'material_id','attributes':['formula','composition','optional_structure','revision','source'],'lifecycle':'导入或构造，修改组成/结构后旧特征失效；保留原输入可重置。'},
                {'name':'feature_config','identity':'config_id','attributes':['data_source','feature_names','statistics','NaN_policy','version'],'lifecycle':'配置改变须重新计算并更新列名，禁止混合不同配置的列。'},
                {'name':'feature_table','identity':'table_id','attributes':['material_ids','source_revisions','labels','matrix','failed_rows','ranking_rule'],'lifecycle':'计算→检查有限性和标签→筛选/排序→保存；失败行不等于零特征。'},
            ],
            'bridges':[{'from':'pymatgen.Composition/Structure','to':'matminer featurizer -> named numerical columns','contract':'材料ID和输入版本不变，组成分数归一，feature_labels与矩阵列一一对应；不从测试集学习填补或筛选参数。'}],
            'tasks':[
                task('semiconductor_composition_features','为GaAs、AlAs与Si生成元素分数和原子序数Z的均值/最小值/最大值，保留列标签并按均值排序；核验组成归一、解析特征矩阵和批量/单条结果一致。','固定公式GaAs/AlAs/Si；Z(Ga)=31,Z(As)=33,Z(Al)=13,Z(Si)=14；禁用NaN填充。',[
                    ('公式转Composition',['matminer.featurizers.conversions.StrToComposition.__init__','matminer.featurizers.conversions.StrToComposition.featurize']),
                    ('配置数据源和描述符',['matminer.utils.data.PymatgenData.__init__','matminer.featurizers.composition.composite.ElementProperty.__init__','matminer.featurizers.composition.element.ElementFraction.__init__']),
                    ('单条和批量计算',['matminer.featurizers.composition.composite.ElementProperty.featurize','matminer.featurizers.composition.element.ElementFraction.featurize','matminer.featurizers.base.BaseFeaturizer.set_n_jobs','matminer.featurizers.base.BaseFeaturizer.featurize_dataframe','matminer.featurizers.composition.composite.ElementProperty.feature_labels']),
                ],['每行元素分数和=1','矩阵[[32,31,33],[23,13,33],[14,14,14]]','排序Si/AlAs/GaAs','批量与单条相同'],'verify_materials_features_path.py:composition_features'),
                task('repair_feature_composition','发现Ga2As不满足固定GaAs描述符目标后修正公式并重新生成特征；核验GaAs与Ga2As2归一化特征相同、GaAs计量p2范数为sqrt(0.5)，避免把错误行填零通过。','错误输入Ga2As、固定GaAs目标mean Z=32及原始配比证据。',[
                    ('定位并修复公式',['pymatgen.core.composition.Composition.__init__','pymatgen.core.composition.Composition.get_el_amt_dict','matminer.featurizers.composition.composite.ElementProperty.featurize','matminer.featurizers.conversions.StrToComposition.featurize']),
                    ('复验归一化和p2统计',['matminer.featurizers.composition.element.ElementFraction.featurize','matminer.featurizers.composition.element.Stoichiometry.__init__','matminer.featurizers.composition.element.Stoichiometry.featurize']),
                ],['Ga2As不通过mean Z=32','修复后通过同一oracle','GaAs/Ga2As2分数一致','p2=sqrt(.5)'],'verify_materials_features_path.py:repair_feature_composition'),
            ],
        },
        '01.02.02': {
            'description':'为半导体晶体构建可审计的高对称k路径：记录标准原胞、倒易基矢、标签/分段和显式k点；检查2pi与长度单位、原胞组成及采样间距。seekpath调用spglib作为硬依赖，不重复暴露底层路径表或第二套对称wrapper。',
            'symbols':kpath,'decisions':[('seekpath','primary','负责HPKOT标准化及隐式/显式k路径。'),(CORE,'complement','负责输入结构、晶格与持久化，提供spglib-compatible tuple。'),('spglib','excluded','安装为seekpath硬依赖，但本场景不单独暴露重复对称分析动作；独立空间群任务由01.02.01覆盖。')],
            'capabilities':['state','edit','observe','path'],
            'sources':[
                source('https://seekpath.readthedocs.io/en/latest/maindoc.html','使用指南定义(cell,positions,numbers)、标准原胞和非标准晶胞两组入口；reference_distance只作为参考间距，实际输出必须检查。',['cell','reciprocal_basis','kpath','sampling_config'],['get_path','get_explicit_k_path','get_path_orig_cell'],['标准原胞路径与采样修复']),
                source('https://github.com/materialscloud-org/seekpath','官方说明按HPKOT约定生成k标签和路径、依赖spglib，并展示Materials Cloud的BZ/原胞/路径可视化用途。',['primitive_cell','path_labels'],['get_path'],['计算前准备可解释路径']),
                source('https://spglib.readthedocs.io/en/stable/python-interface.html','Python晶格使用行向量、位置为分数坐标、原子类型为整数；这些是seekpath输入的真实桥接约定。',['structure','cell_tuple'],['Lattice.matrix','IStructure.frac_coords','SiteCollection.atomic_numbers'],['避免笛卡尔/分数坐标和物种编码混淆']),
                source('https://pymatgen.org/pymatgen.symmetry.html','高对称路径说明强调特定标准原胞设置，输入结构与路径基底必须匹配。',['standard_cell','reciprocal_basis'],['get_path'],['保存路径时绑定原胞，不把坐标直接应用到任意原始晶胞']),
            ],
            'entities':[
                {'name':'structure','identity':'structure_id','attributes':['revision','lattice Angstrom','fractional_positions','atomic_numbers'],'lifecycle':'构造或加载→修改应变/超胞→重新生成路径；旧路径与旧结构绑定。'},
                {'name':'kpath','identity':'path_id','attributes':['source_revision','primitive_cell','reciprocal_basis 1/Angstrom with 2pi','labels','segments','relative_points','absolute_points','reference_distance'],'lifecycle':'生成→采样→独立几何校验→保存；改变输入或间距后重算。'},
            ],
            'bridges':[{'from':'pymatgen.Structure','to':'seekpath/spglib tuple -> standard primitive lattice + kpoints','contract':'实空间Angstrom，行基矢；B满足A B^T=2pi I；相对k点乘B得到1/Angstrom绝对坐标。路径与返回的标准原胞一起保存。'}],
            'tasks':[
                task('silicon_standard_kpath','为晶格常数5.431 Angstrom的金刚石Si生成标准高对称路径；保存原胞与路径，断言空间群227、原胞体积a³/4、ABᵀ=2πI以及Gamma/X/L点的解析几何。','已知Si金刚石常规胞，symprec=1e-5；不执行DFT。',[
                    ('构造并桥接晶体',['pymatgen.core.lattice.Lattice.cubic','pymatgen.core.structure.IStructure.from_spacegroup','pymatgen.core.lattice.Lattice.matrix','pymatgen.core.structure.IStructure.frac_coords','pymatgen.core.structure.SiteCollection.atomic_numbers']),
                    ('生成并检验标准路径',['seekpath.getpaths.get_path','pymatgen.core.lattice.Lattice.reciprocal_lattice']),
                ],['spacegroup227/cF','V=a^3/4','ABt=2piI','Gamma=0','|X|=2pi/a','|L|=sqrt3*pi/a'],'verify_materials_features_path.py:silicon_standard_kpath'),
                task('repair_kpath_sampling','诊断过稀的Si显式k路径，将参考间距从0.5调整为0.02；按每个连续路径段的实际绝对k点距离验收，所有步长需小于0.05 Å⁻¹，并核对相对/绝对坐标转换。','初始reference_distance=0.5，验收阈值固定0.05 Å⁻¹，标准原胞及对称容差不变。',[
                    ('生成初始路径并定位稀疏段',['seekpath.getpaths.get_explicit_k_path']),
                    ('调整间距重算并逐段复验',['seekpath.getpaths.get_explicit_k_path']),
                ],['粗路径必须失败','修复后真实最大步长<0.05','relative@B=absolute','不能跨不连续段计算伪间距'],'verify_materials_features_path.py:repair_kpath_sampling'),
            ],
        },
    }
    for sid,design in designs.items():
        row = next(r for r in inventory['scenarios'] if r['scenario_id']==sid)
        packages=[]
        for name,role,why in design['decisions']:
            spec=specs[name]
            packages.append({'name':name,'version':spec['tag'],'role':role,'reason':why,
                             'raw_path':(RAW/f'{name}_{spec["tag"]}.json').as_posix(),'raw_sha256':_canonical_sha256(payloads[name]),
                             'release_manifest':MANIFEST.as_posix(),'release_research':checks[name]})
        for selected in design['symbols']:
            matches=[t for t in payloads[selected['package']][0]['init_ref_tools'] if all(t[k]==selected[k] for k in ('module','name','type'))]
            require(len(matches)==1,f'Unknown source symbol: {selected}')
            raw=matches[0]
            if not raw.get('description') or any(not m.get('description') for m in raw.get('function',[]) if m['name'] in selected.get('methods',[])):
                selected['missing_description_reason']='保留必要来源接口的原始空说明；用途见reason，不编造docstring。'
        runtime=BASE/f'runtime/materials_features_path/{sid}.json'
        research_path=BASE/f'research/{sid}.json'
        research={'scenario_id':sid,'checked_on':'2026-09-17','reviewed_design':design,'runtime_report':runtime.as_posix(),
                  'source_checks':[checks[p['name']] for p in packages],
                  'source_limitations':'matminer主站及旧Github Pages重定向后均403；使用已读取的固定发布版官方文档/源码页面，不计失败页面。'}
        research_path.write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        profile={'profile_version':'joint-scenario-1.0','scenario_id':sid,'index':row['index'],'checked_on':'2026-09-17',
                 'classification_source':NORMALIZED.as_posix(),'classification_sha256':file_sha(NORMALIZED),
                 'classification_upstream':{'path':inventory['classification_source'],'sha256':inventory['classification_sha256']},
                 'research_file':research_path.as_posix(),'research_sha256':file_sha(research_path),
                 'description':design['description'],'research_sources':design['sources'],'packages':packages,
                 'package_relation':'；'.join(f'{n}: {role}，{why}' for n,role,why in design['decisions']),
                 'entities':design['entities'],'capabilities':[{'id':c,'required':True} for c in design['capabilities']],
                 'symbols':design['symbols'],'target_all_func':{'min':50,'max':200},'bridges':design['bridges'],
                 'runtime_infrastructure':[{'reference':'python.numpy_pandas_json','kind':'infrastructure','reason':'数组、表格、标签、确定性排序与解析oracle；不增加模型训练或第二套物理求解。'}],
                 'boundaries':['仅种子及固定样例；未实现Agent状态注册表/JSON facade/reset隔离。','API定义取发布源码；源码缺失说明和继承边界保留。','不声称全部参考API已执行；按运行报告界定验证范围。'],
                 'tasks':design['tasks'],'runtime_reports':[{'path':runtime.as_posix(),'sha256':file_sha(runtime),'scope':'固定正常/错误/修复任务'}]}
        if sid=='01.02.02':
            profile['count_exception']='高对称路径公开主入口仅4个；加输入构造、基矢、状态读写和校验已闭合。低于50时不补入底层路径表、重复spglib接口或无关求解器凑数。'
            profile['runtime_infrastructure'].append({'reference':'installed.spglib','kind':'hard_dependency','reason':'seekpath内部已声明并安装spglib2.7.0；不独立暴露该候选包的动作。'})
        destination=BASE/'profiles'/f'{sid}.json'
        destination.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(destination)


if __name__=='__main__':
    main()
