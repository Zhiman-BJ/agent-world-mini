"""Full Python-visible Cython candidate pool for the excluded HDL parser."""
import json
from pathlib import Path
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW
from seed_gen.scripts.extract_release_python_seeds import build_seed,git


def main():
    root=Path('seed_pypi_raw/l1_design_lab/hdlConvertor')
    assert git(root,'remote','get-url','origin')=='https://github.com/Nic30/hdlConvertor.git'
    spec=dict(name='hdlConvertor',index=350,directory='l1_design_lab/hdlConvertor',source_root='.',modules=['hdlConvertor'],adapter='hdlconvertor',repository='https://github.com/Nic30/hdlConvertor',tag='v2.3',commit=git(root,'rev-parse','HEAD'),documentation='https://github.com/Nic30/hdlConvertor/tree/v2.3',description='hdlConvertor通过C++/Cython解析VHDL和Verilog/SystemVerilog为统一AST，提供Verilog预处理与宏数据库接口；当前场景选择PyVerilog作为主解析器，本包保留完整Python可见声明作为替代候选。',pypi='https://pypi.org/project/hdlConvertor/',pypi_version='2.3',checked_on='2026-09-17',release_api='https://api.github.com/repos/Nic30/hdlConvertor/releases/latest',release_url='https://github.com/Nic30/hdlConvertor/tree/v2.3',release_published_at='',github_prerelease=False,notes=['官方无GitHubRelease；稳定v2.3标签与PyPI2.3对应，完整提交固定。','原生API来自实际.pyx def/cdef class及include链，HdlConvertorPy按__init__.py重导出为HdlConvertor；__cinit__为元数据而非虚构__init__，C++和cdef内部函数不进入Python工具。','候选为静态声明池，未编译/运行hdlConvertor；主任务使用PyVerilog与实际Icarus。'])
    payload=build_seed(spec,Path('seed_pypi_raw'))
    (RAW/'hdlConvertor_v2.3.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'hdlconvertor_sources.json').write_text(json.dumps([spec],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(payload[0]['environment']['nums'])


if __name__=='__main__':
    main()
