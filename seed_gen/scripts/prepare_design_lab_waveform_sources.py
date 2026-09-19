"""Pin the reviewed waveform writer/parser releases, then make their full pools."""
import json
from pathlib import Path
import subprocess
from seed_gen.scripts.prepare_design_lab_sources import BASE


def main():
    discovery = {r['query_name']: r for r in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    web = {r['url']: r for r in json.loads((BASE/'research/waveform_web/index.json').read_text(encoding='utf-8'))}
    specs = []
    for index, name, repo, tag, module, docs, description in [
        (301, 'vcdvcd', 'cirosantilli/vcdvcd', 'v2.6.0', 'vcdvcd', 'https://github.com/cirosantilli/vcdvcd', 'vcdvcd读取Verilog值变化转储VCD，提供信号名、位宽、时间戳和值变更索引及流式回调，用于波形调试、信号筛选和首次分歧定位；它不运行RTL仿真。'),
        (302, 'pyvcd', 'SanDisk-Open-Source/pyvcd', '0.5.0', 'vcd', 'https://pyvcd.readthedocs.io/en/latest/', 'pyvcd提供VCD写入、流式标记读取和GTKWave查看配置生成，处理信号声明、时间单位和按时间排序的值变更；可用于保存实验或仿真的数字波形。'),
    ]:
        api = f'https://api.github.com/repos/{repo}/releases/latest'
        release = json.loads(Path(web[api]['json_file']).read_text(encoding='utf-8'))
        if name == 'pyvcd':
            assert release['tag_name'] == tag and not release['prerelease']
            published = release['published_at']
            notes = ['在线latest文档为0.5.1.dev0，签名与参考工具固定官方0.5.0 Release。']
        else:
            assert web[api]['status'] == 404 and discovery[name]['info']['version'] == '2.6.0'
            published = min(f['upload_time_iso_8601'] for f in discovery[name]['latest_files'])
            notes = ['官方无GitHub Release；git ls-remote核对v2.6.0标签指向515273543d74da21588785a2b192159c8ac7a66a，与PyPI2.6.0匹配。发布日期记录PyPI上传日期。']
        specs.append(dict(name=name, index=index, directory='l1_design_lab/'+name, source_root='src' if name == 'pyvcd' else '.', modules=[module],
                          repository='https://github.com/'+repo, tag=tag, documentation=docs, description=description,
                          pypi=f'https://pypi.org/project/{name}/', pypi_version=discovery[name]['info']['version'],
                          checked_on='2026-09-17', release_api=api, release_url=release.get('html_url', f'https://github.com/{repo}/tree/{tag}'),
                          release_published_at=published, github_prerelease=False, notes=notes,
                          selection_rule='Latest stable official GitHub Release; if absent, latest PyPI distribution with matching official tag.'))
    path = BASE/'waveform_sources.json'
    path.write_text(json.dumps(specs, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    subprocess.run(['C:/Apps/anaconda3/python.exe', '-X', 'utf8', '-m', 'seed_gen.scripts.prepare_design_lab_sources', '--manifest', str(path)], check=True)


if __name__ == '__main__':
    main()
