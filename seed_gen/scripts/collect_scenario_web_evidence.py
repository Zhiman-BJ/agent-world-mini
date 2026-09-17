"""Fetch an explicit URL list; retain inspected body text and HTTP provenance."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup
import requests


def fetch(url: str, destination: Path) -> dict:
    record = {'url': url, 'checked_on': datetime.now(timezone.utc).date().isoformat()}
    try:
        response = requests.get(url, timeout=(15, 45), headers={'User-Agent': 'semiconductor-seed-research/1.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.title.get_text(' ', strip=True) if soup.title else ''
        for element in soup(['script', 'style', 'nav', 'footer']):
            element.decompose()
        body = soup.select_one('main') or soup.select_one('[role=main]') or soup.body or soup
        content = body.get_text('\n', strip=True)
        stem = hashlib.sha256(url.encode()).hexdigest()[:16]
        body_path = destination / (stem + '.txt')
        body_path.write_text(content, encoding='utf-8')
        record.update(status=response.status_code, final_url=response.url, title=title,
                      content_file=body_path.as_posix(), content_sha256=hashlib.sha256(body_path.read_bytes()).hexdigest(),
                      content_length=len(content), content_type=response.headers.get('content-type', ''))
        if 'json' in response.headers.get('content-type', ''):
            data_path = destination / (stem + '.json')
            data_path.write_text(json.dumps(response.json(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            record['json_file'] = data_path.as_posix()
    except requests.RequestException as exc:
        record.update(status='request_failed', error=str(exc))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--urls', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    urls = list(dict.fromkeys(json.loads(args.urls.read_text(encoding='utf-8'))))
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(lambda u: fetch(u, args.output), urls))
    (args.output / 'index.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for record in records:
        print(record['status'], record['url'], record.get('title', ''), record.get('error', ''))


if __name__ == '__main__':
    main()
