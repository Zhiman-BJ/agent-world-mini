"""只读比较三条可疑本地记录与 OpenAlex 当前 API；不把网络失败当数据错误。"""
import argparse
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CASES = [
    ('research_author', 'research_author_id', 'A5028125522', 'authors', 'display_name', ['works_count', 'cited_by_count']),
    ('research_author', 'research_author_id', 'A5103423779', 'authors', 'display_name', ['works_count', 'cited_by_count']),
    ('scholarly_work', 'scholarly_work_id', 'W3038568908', 'works', 'title', ['cited_by_count']),
]


def compare(local, remote, fields):
    return {field: {'local': local[field], 'current_openalex': remote[field]}
            for field in fields if local[field] != remote[field]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'artifacts/toolgen-reality-openalex-run/state/records.sqlite')
    args = parser.parse_args()
    assert compare({'n': 1}, {'n': 1}, ['n']) == {}
    assert compare({'n': 2}, {'n': 1}, ['n']) == {'n': {'local': 2, 'current_openalex': 1}}
    connection = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    results = []
    try:
        for table, key, identifier, endpoint, name, fields in CASES:
            row = connection.execute(f'SELECT * FROM {table} WHERE {key} = ?', (identifier,)).fetchone()
            if row is None:
                results.append({'id': identifier, 'status': 'local_record_missing'})
                continue
            local = dict(row)
            url = f'https://api.openalex.org/{endpoint}/{identifier}'
            result = {'id': identifier, 'local': local, 'source_url': url}
            try:
                request = Request(url, headers={'User-Agent': 'OpenAlex-snapshot-readonly-check/1.0'})
                with urlopen(request, timeout=20) as response:
                    remote = json.load(response)
                if remote.get('id', '').rsplit('/', 1)[-1] != identifier:
                    raise ValueError('API 返回的实体 ID 不匹配，不能直接比较')
                if any(type(remote.get(field)) is not int for field in fields):
                    raise ValueError('API 缺少有效计数字段')
                result['current_openalex'] = {field: remote.get(field) for field in ['id', name, *fields, 'updated_date']}
                result['differences'] = compare(local, remote, fields)
                result['status'] = 'different_from_current_source' if result['differences'] else 'matches_current_source'
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
                result['status'] = 'source_unavailable_or_invalid'
                result['error'] = f'{type(error).__name__}: {error}'
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    finally:
        connection.close()
    print('说明：当前 API 与历史快照不同不自动证明快照错误；还需区分正常更新、实体合并与字段错配。来源无法读取时只能确认本地值，不能确认来源有误。')


if __name__ == '__main__':
    main()
