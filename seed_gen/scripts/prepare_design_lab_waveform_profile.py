"""Reviewed scene-level waveform choices from the two complete release pools."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe
from seed_gen.scripts.prepare_design_lab_sources import BASE, RAW


def main():
    c = Collection(base=BASE, raw=RAW)
    symbols = [
        symbol('vcdvcd', 'vcdvcd.vcdvcd', 'VCDVCD', '__init__', 'read', '主波形索引：构造时按信号筛选解析。读data/signals/timescale/Signal.tv等公开属性；排除四个源码明确标记Deprecated的get_*包装。'),
        symbol('vcdvcd', 'vcdvcd.vcdvcd', 'StreamParserCallbacks', 'enddefinitions time value', 'read', '大波形流式观察的回调协议，不必保存全部时间序列；不暴露CLI打印实现。', '公开回调基类继承object无显式构造，通过轻量子类覆写所选回调；本轮fixture主要验证内存索引。'),
        symbol('pyvcd', 'vcd.writer', 'VCDWriter', '__init__ set_scope_type register_var register_alias dump_off dump_on change close flush', 'write', '统一负责声明、别名、转储范围、时间排序和文件生命周期，不暴露内部Variable格式化实现。'),
        symbol('pyvcd', 'vcd.reader', 'tokenize', None, 'inspect', '元数据/语法流式检查；与vcdvcd随机访问角色互补，不搭第二套持久波形索引。'),
        symbol('pyvcd', 'vcd.reader', 'Token', 'attr comment date scope timescale var version time_change scalar_change vector_change real_change string_change', 'inspect', '检查时间单位、层级、声明与值变化；Token由tokenize工厂返回，NamedTuple构造由运行时生成。', '由已选tokenize返回，不手写其NamedTuple构造。'),
        symbol('pyvcd', 'vcd.reader', 'VarDecl', 'ref_str', 'inspect', '声明位索引映射回可读引用，避免丢失多位信号身份。', '由tokenize的Token.var返回；不手写声明构造。'),
        symbol('pyvcd', 'vcd.common', 'Timescale', 'from_str', 'inspect', '规范化时间量级/单位设置，避免把ns误当us。', 'NamedTuple运行时构造；使用from_str或Token.timescale返回对象。'),
        symbol('pyvcd', 'vcd.gtkw', 'GTKWSave', '__init__ comment dumpfile savefile timestart zoom_markers group trace trace_bits treeopen', 'save', '保存分歧附近窗口、信号选择、分组与位查看配置，便于复查；不启动GUI进程或自动加载执行过滤器。'),
    ]
    source = c.source
    design = {
        'scenario_id':'03.08.01',
        'description':'读取和生成固定时间单位的VCD波形，以层级信号、位宽和值变化作为状态，定位计数器波形首个分歧并修复错误值或时间尺度；pyvcd负责写入/元数据检查，vcdvcd负责信号索引，保存复查配置。范围是既有波形调试，不代替RTL仿真。',
        'packages':[
            ('vcdvcd','primary','全量16个参考操作中只保留主索引构造和流式回调；随机访问值直接使用公开数据属性，排除弃用get_*与无关CLI。'),
            ('pyvcd','complement','提供真实VCD写入→vcdvcd读取桥接、流式语法/单位元数据检查和查看配置保存；不重复暴露另一套持久信号模型。'),
        ],
        'sources':[
            source('https://github.com/cirosantilli/vcdvcd','README给出counter_tb计数器波形、信号筛选、随机访问与store_tvs=False回调示例；发布流程为setup.py版本+Git tag+PyPI。',['trace','hierarchical_signal','value_changes'],['VCDVCD','StreamParserCallbacks'],['定位首个计数差异，保持x值语义']),
            source('https://pyvcd.readthedocs.io/en/latest/vcd.writer.html','VCDWriter按时间顺序写入信号变化，定义register_var/register_alias、timescale和VCDPhaseError；所选签名来自0.5.0，网页0.5.1.dev0。',['writer','signal','timescale'],['register_var','register_alias','change','close'],['注入和修复错误值/单位，拒绝倒序时间']),
            source('https://pyvcd.readthedocs.io/en/latest/vcd.reader.html','tokenize从二进制VCD流产生Token；示例显式检查1ns的magnitude/unit，声明与值变化类型分离。',['metadata','token','var_declaration'],['tokenize','Token.timescale','Token.var'],['独立核查声明、单位和事件完整性']),
        ],
        'entities':[
            {'name':'trace','identity':'trace_id','attributes':['revision','artifact_path','timescale_seconds','source_design_id','start_tick','end_tick','checksum'], 'lifecycle':'create/load -> declare -> append monotonic changes -> close -> parse -> inspect -> save/reset；改变值或timescale使差异报告和查看配置绑定失效。'},
            {'name':'signal','identity':'trace_id + hierarchical_reference','attributes':['identifier_code','size_bits','var_type','aliases','time_value_pairs with x/z'], 'lifecycle':'声明后由writer写变化；alias共用identifier，读取后按trace revision观察，不把x/z转换成0。'},
            {'name':'divergence_report','identity':'report_id','attributes':['candidate_trace_revision','reference_trace_revision','first_mismatch_seconds','expected','actual','selected_signals','verdict'], 'lifecycle':'只比较固定目标与当前trace版本；修复后重建报告，不更改oracle。'},
        ],
        'capabilities':['read','write','inspect','save'], 'symbols':symbols,
        'bridges':[{'from':'pyvcd.VCDWriter','to':'VCD file -> vcdvcd.VCDVCD', 'tool':'vcd.writer.VCDWriter + vcdvcd.vcdvcd.VCDVCD', 'contract':'固定1ns、2bit count、同identifier别名；实际文件引用dut.count不自动附加[1:0]；值字符串保留x/z，时间tick乘timescale转秒。真实桥接在正常/错误/修复文件执行。'}],
        'runtime_infrastructure':[{'reference':'python.file_json_arithmetic','kind':'infrastructure','reason':'Path/JSON/hash/字符串转整数和固定表比对；读VCDVCD返回data/references_to_ids/Signal.tv公开属性。六点手写计数oracle不生成RTL仿真结果。TokenKind为公开枚举数据，不冒充函数。'}],
        'boundaries':['未运行RTL DUT、外部仿真器或GTKWave GUI；输入是明确制造的数字波形。','实际执行仅覆盖报告中的方法，非全部参考操作运行验证。','固定oracle不覆盖任意协议时序或亚稳态；缺失/未知信号须后续facade定义失败返回。','后续需实现稳定ID、隔离文件目录、reset、输出失效与只读oracle；当前是种子和任务样例。'],
        'count_exception':'48个参考操作已闭合波形索引、写入、单位/语法检查与复查保存。vcdvcd四个弃用getter、CLI打印和pyvcd内部格式化/GUI控制不应为达到50硬加入；公开数据属性不伪造方法。',
        'tasks':[
            recipe('locate_and_repair_counter_divergence','读取模4计数器的VCD波形，定位首个错误值位于30ns；重新写入正确值并核对6个固定采样点、别名一致和修复文件的可重复性，保存分歧附近查看配置。','已知0/10/20/30/40/50ns的目标值0/1/2/3/0/1，候选在30ns误写2。',[
                ('生成与读取候选',['vcd.writer.VCDWriter.__init__','vcd.writer.VCDWriter.register_var','vcd.writer.VCDWriter.register_alias','vcd.writer.VCDWriter.change','vcd.writer.VCDWriter.close','vcdvcd.vcdvcd.VCDVCD.__init__']),
                ('从当前波形观察差异并写入修复',['vcdvcd.vcdvcd.VCDVCD.__init__','vcd.writer.VCDWriter.change']),
                ('保存复查窗口',['vcd.gtkw.GTKWSave.__init__','vcd.gtkw.GTKWSave.dumpfile','vcd.gtkw.GTKWSave.timestart','vcd.gtkw.GTKWSave.zoom_markers','vcd.gtkw.GTKWSave.trace']),
            ],['首个分歧=30ns','修复6个采样点与固定目标逐项相等','alias指向同一标识','normal/repaired文件字节相同'], 'verify_waveform.py:ORACLE and wrong_value.vcd'),
            recipe('repair_trace_units_and_order','检查波形声明，将误设1us的时间尺度修复为1ns，保持10tick首跳变和原值序列；倒序时间写入必须抛出VCDPhaseError，重写正确有序文件后以固定10ns目标验收。','候选首跳变10tick且时间单位错误为us；验收目标固定1e-8秒。',[
                ('检查声明与单位',['vcd.reader.tokenize','vcd.reader.Token.timescale','vcd.reader.Token.var','vcd.reader.Token.time_change']),
                ('修复单位、重新顺序写入',['vcd.writer.VCDWriter.__init__','vcd.writer.VCDWriter.register_var','vcd.writer.VCDWriter.change','vcd.writer.VCDWriter.close']),
                ('读回当前结果验收',['vcdvcd.vcdvcd.VCDVCD.__init__','vcd.reader.tokenize']),
            ],['错误首跳变=10us拒绝','修复首跳变=10ns atol1e-20秒','倒序时间抛出已记录错误','3个声明、7个时间标记'], 'verify_waveform.py:wrong_scale and backwards_timestamp'),
        ],
        'runtime_report':(BASE/'runtime/waveform/03.08.01.json').as_posix(),
        'runtime_scope':'pyvcd0.5.0写入→vcdvcd2.6.0读取→固定oracle失败/修复；pyvcd独立metadata token读取和GTKW文件保存。',
    }
    # Count is descriptive; the generator recomputes and validates the true value.
    design['count_exception'] = design['count_exception'].replace('48个', str(sum(len(s.get('methods',[])) if s['type']=='class' else 1 for s in symbols))+'个')
    c.write(design)


if __name__ == '__main__':
    main()
