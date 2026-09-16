import pytest


def test_pages_reassemble_original_and_other_session_cannot_read():
    from task_gen.tool_result_reader import ResultReader
    reader = ResultReader(10)
    original = '头部\n' + 'abcdef' * 20 + '尾部'
    page = reader.preview(original)
    ident = page['result_id']
    parts = [page['content']]
    while page['has_more']:
        page = reader.read({'result_id': ident, 'offset': page['next_offset'], 'length': 10})
        parts.append(page['content'])
    assert ''.join(parts) == original
    assert reader.read({'result_id': ident, 'offset': len(original), 'length': 1})['content'] == ''
    with pytest.raises(ValueError, match='Unknown result_id'):
        ResultReader(10).read({'result_id': ident})
    for invalid in ({'offset': -1}, {'offset': True}, {'length': 0}, {'length': 11},
                    {'offset': len(original) + 1}, {'path': '/etc/passwd'}):
        with pytest.raises(ValueError):
            reader.read({'result_id': ident, **invalid})
    with pytest.raises(ValueError, match='Unknown result_id'):
        reader.read({'result_id': '/etc/passwd'})
