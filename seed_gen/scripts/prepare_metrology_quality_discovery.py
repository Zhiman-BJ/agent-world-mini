"""Record package identity discovery, separately from release/source verification."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

BASE = Path('seed_gen/scenario_collection/l1_metrology_quality')
NAMES = ['reliability', 'surpyval', 'lifelines', 'RosettaSciIO', 'HyperSpy', 'LiberTEM',
         'atomap', 'abTEM', 'kikuchipy', 'pyxem', 'py4DSTEM', 'eXSpy', 'pyFAI',
         'xrayutilities', 'diffpy.structure', 'pyElli', 'lmfit', 'wafermap', 'Semi-ATE-STDF',
         'ruptures', 'networkx', 'pandas', 'scipy', 'scikit-learn', 'scikit-image']

def fetch(name):
    url = f'https://pypi.org/pypi/{name}/json'
    response = requests.get(url, timeout=40)
    response.raise_for_status()
    data = response.json()
    info = {k:data['info'][k] for k in ['name','version','summary','project_urls','requires_python','requires_dist']}
    return {'query_name': name, 'url': url, 'status': response.status_code, 'info': info,
            'release_files': [{k:f[k] for k in ['filename','upload_time_iso_8601','digests']} for f in data['urls']]}

def main():
    (BASE/'research').mkdir(parents=True, exist_ok=True)
    rows = list(ThreadPoolExecutor(4).map(fetch, NAMES))
    (BASE/'research/package_discovery.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for r in rows: print(r['query_name'], r['info']['version'], r['info']['project_urls'])

if __name__ == '__main__': main()
