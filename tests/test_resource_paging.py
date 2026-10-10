from pathlib import Path
from unittest.mock import patch

import pytest

from env_gen.tool_gen.resources import ResourceCatalog


def catalog(root):
    return ResourceCatalog({'filesystem_scopes': [{'scope_id': 'files'}]}, lambda _: root)


def test_preview_reads_a_prefix(tmp_path):
    path = tmp_path / 'large.txt'
    with path.open('wb') as stream:
        stream.write('正文内容'.encode('utf-8'))
        stream.truncate(200 * 1024**2)
    with patch.object(Path, 'read_bytes', side_effect=AssertionError('full read')):
        result = catalog(tmp_path).inspect('aw://files/large.txt', preview_chars=2)
    assert result['text_preview'] == '正文'
    assert result['preview_truncated'] is True


def test_preview_marks_character_truncation(tmp_path):
    (tmp_path / 'text.txt').write_text('abcdefghij')
    result = catalog(tmp_path).inspect('aw://files/text.txt', preview_chars=4)
    assert result['text_preview'] == 'abcd'
    assert result['preview_truncated'] is True


def test_resource_pages_cover_all_files(tmp_path):
    for number in range(603):
        (tmp_path / f'{number:04}.txt').touch()
    resources = catalog(tmp_path)
    first = resources.list(limit=500)
    second = resources.list(limit=500, offset=500)
    assert len(first) == 500 and len(second) == 103
    assert len({entry['ref'] for entry in first + second}) == 603
    assert resources.list(offset=603) == []
    with pytest.raises(ValueError):
        resources.list(offset=-1)
