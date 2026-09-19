"""Material database/cache selection with an explicit offline fixture boundary."""
from seed_gen.scripts.scenario_collection_support import Collection, recipe, symbol

PREFIX = 'seed_gen/scenario_collection/'


def main():
    collection = Collection()
    def jarvis(module, name, methods, capability, reason, construction=None):
        return symbol('jarvis-tools', 'jarvis.'+module, name, methods, capability, reason, construction)
    def core(module, name, methods, capability, reason, construction=None):
        return symbol('pymatgen-core', 'pymatgen.'+module, name, methods, capability, reason, construction)
    def entity(name, attributes, lifecycle):
        return {'name': name, 'attributes': attributes.split(), 'lifecycle': lifecycle}
    source = collection.source
    collection.write({
        'scenario_id': '01.01.02',
        'description': '面向材料候选检索的本地快照环境：核对数据集、载入带来源的缓存记录，按明确单位和缺失值策略筛选，以唯一材料ID导出结果，再将JARVIS结构转换成统一pymatgen状态。当前实跑为显式人工固定缓存，不冒称已访问Materials Project或下载真实JARVIS数据库。',
        'packages': [
            ('jarvis-tools', 'primary', '数据集目录、原生ZIP缓存载入和Atoms结构转换可形成本地可重放链；官方Figshare端点403，任务固定记录自带fixture身份。'),
            ('pymatgen-core', 'complement', '只补JARVIS已提供的真实桥接目标及结构/组成检查、保存恢复，不再暴露一套数据库客户端。'),
            ('mp-api', 'excluded', '官方客户端搜索适合实时MP查询，但依赖密钥、网络及服务版本；本轮没有这些条件，排除重复检索前端，并保留最新正式版完整候选。'),
        ],
        'symbols': [
            jarvis('db.figshare', 'get_db_info', None, 'catalog', '核对原生数据集名称、下载地址、缓存文件名及参考来源。'),
            jarvis('db.figshare', 'data', None, 'catalog', '原生数据集入口与名称校验；本轮仅执行无效名称拒绝，不调用大规模远程下载。'),
            jarvis('db.figshare', 'get_request_data', None, 'cache', '指定独立store_dir和js_tag，从现有ZIP真实载入；任务使用明显的synthetic文件名。'),
            jarvis('db.jsonutils', 'loadjson', None, 'persist', '恢复检索配方、来源及结构记录。'),
            jarvis('db.jsonutils', 'dumpjson', None, 'persist', '导出可重放筛选条件、材料ID和规范结构。'),
            jarvis('core.atoms', 'Atoms', '__init__ from_dict to_dict pymatgen_converter write_poscar write_cif from_poscar from_cif volume density num_atoms atomic_numbers uniq_species', 'bridge', '将数据库结构字段解释为原生Atoms，核验几何/物种并通过已提供转换器进入统一状态；不保留重复结构编辑算法。'),
            core('core.structure', 'Structure', '__init__', 'state', '可变结构具体类型，只保留数据库实例化入口。'),
            core('core.structure', 'IStructure', 'from_dict as_dict to from_file copy lattice volume frac_coords get_distance get_primitive_structure', 'state', 'Structure继承的序列化、晶格/分数坐标与距离查询，支持候选记录的几何一致性检查。', 'Structure构造或JARVIS pymatgen_converter返回，继承方法保持真实定义位置。'),
            core('core.structure', 'SiteCollection', 'num_sites composition atomic_numbers cart_coords is_valid', 'validate', '检查位点数、成分、笛卡尔坐标和过近位点；不重复登记继承方法。', '抽象公共基类不单独构造，已选Structure继承。'),
            core('core.composition', 'Composition', '__init__ get_el_amt_dict reduced_formula chemical_system num_atoms get_atomic_fraction as_dict from_dict', 'validate', '明确化学式、体系和计数的唯一规范表示，配合检索元素条件与来源保存。'),
            core('core.lattice', 'Lattice', '__init__ matrix volume lengths angles get_cartesian_coords get_fractional_coords from_dict as_dict', 'validate', '数据库坐标模式和Å单位修复，核验体积与分数/笛卡尔互换。'),
        ],
        'sources': [
            source('https://jarvis-tools.readthedocs.io/en/master/databases.html', '官方目录列出dft_2d/dft_3d及历史版本、方法与数据量；同名材料属性来自具体数据库/计算方法而非通用物理真值。', 'dataset version property method', 'get_db_info data', '选择有来源与单位的数据快照'),
            source('https://jarvis-tools.readthedocs.io/en/master/tutorials.html', '教程从晶格矩阵、元素及分数坐标构建Atoms，并说明POSCAR/CIF读写和多组分扩展。', 'atoms lattice coordinate_mode composition', 'Atoms.from_dict pymatgen_converter', '从记录恢复结构并验证几何'),
            source('https://github.com/atomgptlab/jarvis/blob/v2026.4.2/jarvis/db/figshare.py', '固定发布实现以js_tag.zip为缓存键，已有文件直接读取对应JSON；data先检查dataset名称，再按目录URL下载/缓存。', 'cache archive identity source', 'get_request_data get_db_info', '无网络缓存回放、名称错误与来源保留'),
            source('https://docs.materialsproject.org/downloading-data/using-the-api/examples', '官方MP示例通过带密钥MPRester按material_ids或属性搜索并选择structure字段；作为替代前端来源，没有在本环境运行。', 'query fields material_id structure', 'MPRester summary.search', '评估实时查询与本地固定快照的取舍'),
        ],
        'entities': [
            entity('dataset_snapshot', 'id declared_origin version cache_path cache_sha256 property_units', '选择来源→校验缓存→载入；任务fixture与官方数据集文件名严格区分。'),
            entity('material_record', 'jid formula bandgap_eV hull_eV_atom atoms provenance', '加载→检查唯一ID/缺失值/单位→筛选；修复记录保留原输入。'),
            entity('query_recipe', 'id dataset_id gap_bounds hull_threshold units missing_policy result_ids', '配置条件→运行本地过滤→诊断异常候选→修复→保存配方。'),
            entity('canonical_structure', 'id source_jid lattice_A frac_coords elements volume_A3 revision', '原生Atoms转换→检查坐标语义→保存恢复；字段修改使旧检查失效。'),
        ],
        'capabilities': ['catalog', 'cache', 'bridge', 'state', 'validate', 'persist'],
        'bridges': [{'from': 'JARVIS cache record.atoms', 'to': 'jarvis.core.atoms.Atoms -> pymatgen Structure', 'contract': 'Atoms.from_dict尊重cartesian标志；pymatgen_converter传分数坐标；保存元素计数、Å晶格、体积和距离，保留来源jid。'}],
        'runtime_infrastructure': [
            {'reference': 'runtime.cache_query_fixture', 'reason': 'Python标准库生成5条人工数据、固定时间戳ZIP、唯一ID检查及显式列表过滤。不是原生远程query API，也不代表实际材料测量/DFT属性。NumPy计算独立a³/4与sqrt(3)a/4目标。'},
            {'reference': 'runtime.offline_network_guard', 'reason': '固定任务封禁requests.get以证明读取的是现有缓存；不模拟外部数据库响应、不伪装线上查询成功。'},
        ],
        'boundaries': [
            'Figshare下载及API在采集时403，完整失败证据保留。任务用synthetic_material_query_v1.json及fixture-* ID，不覆盖或冒充官方dft_2d缓存。',
            'MP客户端固定版本0.46.5仅作完整替代候选，本轮未安装/请求；无密钥和实时数据库权限验证。',
            '五条带隙/凸包能为人工赋值，筛选只验证单位、逻辑、身份和结构桥接，不证明真实材料稳定性或预测准确度。',
            'get_jid_data会使用默认大数据缓存，当前需要任务内显式store_dir，因此不选择；数据下载可在未来联网环境配置后另验。',
        ],
        'tasks': [
            recipe('query_cache_and_repair_filter_units', '从显式本地材料快照载入5条记录，筛选带隙1–2eV且凸包能≤50meV/atom的候选；诊断把50当eV引入的错误材料，修正单位并处理缺失值，拒绝重复ID，保存查询与缓存哈希后恢复相同候选集。', 'synthetic_local_fixture_v1；全部网络请求被禁止；正确候选明确为fixture-001/004。', [
                ('核对目录并读缓存', ['jarvis.db.figshare.get_db_info', 'jarvis.db.figshare.get_request_data']),
                ('校验无效数据集并修复本地筛选条件', ['jarvis.db.figshare.data', 'jarvis.db.figshare.get_request_data']),
                ('保存配方和结果', ['jarvis.db.jsonutils.dumpjson', 'jarvis.db.jsonutils.loadjson']),
            ], ['错误集合包含003；50/1000后精确001/004；missing gap不通过；重复ID拒绝；缓存和配方JSON一致。'], PREFIX+'verify_materials_database.py'),
            recipe('repair_record_coordinates_and_bridge', '恢复Si2候选结构，定位cartesian标志误置导致的错误原子距离；按来源修复为分数坐标，通过JARVIS到pymatgen的真实转换保存与恢复，验证独立晶胞体积、距离、元素计数和输入不变。', '晶格由a=5.43Å的FCC原胞给定，两个位置0与(.25,.25,.25)；错误记录cartesian=True。', [
                ('恢复并诊断错误坐标', ['jarvis.core.atoms.Atoms.from_dict', 'jarvis.core.atoms.Atoms.pymatgen_converter', 'pymatgen.core.structure.IStructure.get_distance']),
                ('修复并核验统一状态', ['jarvis.core.atoms.Atoms.from_dict', 'jarvis.core.atoms.Atoms.pymatgen_converter', 'pymatgen.core.structure.IStructure.volume', 'pymatgen.core.structure.IStructure.frac_coords', 'pymatgen.core.composition.Composition.get_el_amt_dict']),
                ('保存恢复', ['jarvis.core.atoms.Atoms.to_dict', 'pymatgen.core.structure.IStructure.as_dict', 'jarvis.db.jsonutils.dumpjson', 'jarvis.db.jsonutils.loadjson', 'pymatgen.core.structure.IStructure.from_dict']),
            ], ['错误距离.4330127Å被拒绝；修复2.351258971274751Å，体积40.02575175Å³，Si2；JSON往返和原记录不变。'], PREFIX+'verify_materials_database.py'),
        ],
        'runtime_report': PREFIX+'runtime/materials_database/01.01.02.json',
        'runtime_scope': '真实JARVIS ZIP缓存接口/无效名称拒绝、人工固定记录查询、结构转换及独立几何断言；没有真实数据库下载或MP远程API。',
    })


if __name__ == '__main__':
    main()
