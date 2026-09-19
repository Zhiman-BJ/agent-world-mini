"""Resolve package identity leads from PyPI; this does not approve release versions."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests


def discover(name):
    url = f'https://pypi.org/pypi/{name}/json'
    result = {'query_name':name,'url':url,'checked_on':datetime.now(timezone.utc).date().isoformat(),
              'review_status':'identity_and_official_release_review_pending'}
    try:
        response = requests.get(url,timeout=(15,40))
        result.update(status=response.status_code, final_url=response.url, response_sha256=hashlib.sha256(response.content).hexdigest())
        if response.status_code == 200:
            payload = response.json()
            info = payload['info']
            result.update(info={k:info.get(k) for k in ('name','version','summary','home_page','project_urls','requires_python','requires_dist','license')},
                          latest_pypi_files=[{k:item.get(k) for k in ('filename','upload_time_iso_8601','digests','yanked','url')} for item in payload['urls']])
        else:
            result['review_status'] = 'pypi_identity_unresolved'
    except requests.RequestException as exc:
        result.update(status='request_failed',error=str(exc),review_status='retry_required')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--names',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    names = list(dict.fromkeys(json.loads(args.names.read_text(encoding='utf-8'))))
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(discover,names))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for record in records:
        print(record['query_name'],record['status'],record.get('info',{}).get('version',''),record.get('info',{}).get('project_urls',{}))


if __name__=='__main__':
    main()
