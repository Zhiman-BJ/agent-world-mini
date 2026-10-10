"""Record package identity leads; this file does not certify source releases."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import requests

BASE = Path('seed_gen/scenario_collection/l1_test_fab')
PACKAGES = ['Semi-ATE-STDF','pystdf','wafermap','wfmap','semiconductor-test-toolkit',
            'scikit-learn','scipy','pandas','secsgem','secsgem-driver','pysemisecs','asyncua',
            'tsfresh','ruptures','sktime','adtk','river','spc-lib','pyspc','control','do-mpc',
            'simpy','FactorySimPy','salabim','job-shop-lib','pyjobshop','ortools','pyomo',
            'reliability','surpyval']

def inspect(name):
    url = f'https://pypi.org/pypi/{name}/json'
    r = requests.get(url, timeout=45)
    result = dict(query_name=name, url=url, status=r.status_code)
    if r.status_code == 200:
        obj = r.json()
        result['info'] = {k: obj['info'].get(k) for k in ('name','version','summary','home_page','project_urls','requires_python','requires_dist')}
        result['files'] = [{k: x.get(k) for k in ('filename','upload_time_iso_8601','digests')} for x in obj['urls']]
    print(name, result.get('info',{}).get('version', r.status_code), flush=True)
    return result

def main():
    (BASE/'research').mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(inspect, PACKAGES))
    (BASE/'research/package_discovery.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

if __name__=='__main__': main()
