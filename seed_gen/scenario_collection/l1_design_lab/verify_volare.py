"""Real local Volare cache/activation logic; fixture directories are not a PDK."""
from importlib.metadata import version
import io
import json
from pathlib import Path
import tempfile
from volare import enable,fetch,Version
from volare.families import Family
from volare.common import get_versions_dir,get_volare_dir,get_volare_home

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/volare'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('volare')=='0.20.6'
    # All mutations remain in this fresh private fixture, not the user's PDK_ROOT.
    root=Path(tempfile.mkdtemp(prefix='pdk_fixture_',dir=OUT)).resolve()
    assert root.parent==OUT.resolve()
    family=Family.by_name['sky130']
    library='sky130_fd_sc_hd'
    assert family.resolve_libraries([library,library])=={library}
    revisions=['1'*40,'2'*40]
    for revision in revisions:
        cache=Path(Version(revision,'sky130').get_dir(str(root)))
        for variant in family.variants:
            (cache/variant/'libs.tech').mkdir(parents=True)
            (cache/variant/'libs.ref'/library).mkdir(parents=True)
            (cache/variant/'fixture_revision.txt').write_text(revision)
    installed=Version.get_all_installed(str(root),'sky130')
    assert {v.name for v in installed}==set(revisions)
    assert get_volare_home(str(root))==str(root)
    def activate(revision):
        v=enable(str(root),'sky130',revision,include_libraries=[library],output=io.StringIO())
        assert v.is_installed(str(root)) and v.is_current(str(root))
        return {variant:(root/variant/'fixture_revision.txt').read_text() for variant in family.variants}
    normal=activate(revisions[0])
    assert set(normal.values())=={revisions[0]}
    wrong=activate(revisions[1])
    assert wrong!=normal and set(wrong.values())=={revisions[1]}
    repaired=activate(revisions[0])
    assert repaired==normal
    assert Version.get_current(str(root),'sky130').name==revisions[0]
    try:
        fetch(str(root),'sky130',revisions[0],include_libraries=['sky130_fd_sc_typo'],output=io.StringIO())
    except ValueError as exc:
        library_error=str(exc)
        assert 'Unknown library' in library_error
    else:
        raise AssertionError('Unknown library accepted')
    assert fetch(str(root),'sky130',revisions[0],include_libraries=[library],output=io.StringIO()).is_installed(str(root))
    assert activate(revisions[0])==normal
    result=dict(scenario_id='03.03.01',status='passed',versions={'volare':version('volare')},tasks=[dict(id='repair_active_pdk_revision',status='passed',expected=revisions[0],wrong=revisions[1],repaired=revisions[0],variants=family.variants,real_symlinks=all((root/v).is_symlink() for v in family.variants)),dict(id='repair_pdk_library_selection',status='passed',wrong='sky130_fd_sc_typo',error=library_error,repaired=library,current_unchanged=True)],oracle_independence='预置两个明确假的40位revision目录，各variant含不可混淆marker；手写目标revision与库名，实际Volare负责枚举/本地fetch检查/软链接切换。',boundaries=['固定目录只模拟open_pdks缓存结构，没有真实PDK版图/模型/规则内容，不宣称工艺库可用或流片可验证。','未网络下载/build/push任何PDK；fetch走已有完整目录检查分支，不mock其函数。','WSL私有临时样例路径明确传pdk_root；不触及用户默认PDK_ROOT。'],fixture_root=str(root))
    (OUT/'03.03.01.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(result)


if __name__=='__main__':
    main()
