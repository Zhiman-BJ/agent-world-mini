"""Collect explicit package identity/release candidates with retained responses."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from seed_gen.scripts.collect_scenario_web_evidence import fetch

BASE=Path('seed_gen/scenario_collection/lithography')
def main():
    urls=['https://pypi.org/pypi/lithosim/json','https://pypi.org/pypi/openilt/json',
          'https://pypi.org/pypi/torchlitho/json',
          'https://api.github.com/search/repositories?q=lithosim&per_page=6',
          'https://api.github.com/search/repositories?q=OpenILT&per_page=6',
          'https://api.github.com/search/repositories?q=TorchLitho&per_page=6']
    out=BASE/'research/discovery_web';out.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:records=list(pool.map(lambda u:fetch(u,out),urls))
    (out/'index.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for row in records:
        print(row['status'],row['url'],flush=True)
        if row.get('json_file'):
            d=json.loads(Path(row['json_file']).read_text(encoding='utf-8'))
            print(d.get('info',{}).get('project_urls') or [(v['full_name'],v.get('description')) for v in d.get('items',[])],flush=True)

if __name__=='__main__':main()
