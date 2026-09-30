import json

import pytest

from env_gen.tool_gen.delivery import _publish_software_profile, _software_profile_id
from env_gen.tool_gen.software import installed_versions


def make_software(root, version):
    metadata = root / f'python-3.12/lib/python3.12/site-packages/example-{version}.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text(f'Name: example\nVersion: {version}\n')
    (root / 'python-3.12/pyvenv.cfg').write_text('version = 3.12.8\n')


def test_resolved_versions_select_distinct_profiles(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    make_software(first, '1.0')
    make_software(second, '2.0')
    plan = {'python_packages': ['example>=1']}
    ids = [_software_profile_id({'plan': plan, 'installed_versions': installed_versions(root)})
           for root in (first, second)]
    assert ids[0] != ids[1]
    for root, identity in zip((first, second), ids):
        _publish_software_profile(root, tmp_path / identity, tmp_path / 'no-requirements')
    assert installed_versions(tmp_path / ids[1])['packages'] == [('example', '2.0')]


def test_explicit_profile_detects_version_conflict(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    make_software(first, '1.0')
    make_software(second, '2.0')
    target = tmp_path / 'profile'
    _publish_software_profile(first, target, tmp_path / 'no-requirements')
    _publish_software_profile(first, target, tmp_path / 'no-requirements')
    with pytest.raises(ValueError, match='共享软件版本'):
        _publish_software_profile(second, target, tmp_path / 'no-requirements')
    assert installed_versions(target)['packages'] == [('example', '1.0')]
