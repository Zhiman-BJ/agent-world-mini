from pathlib import Path

import pytest

from harness.resources import ResourceCatalog


@pytest.fixture
def catalog(tmp_path):
    for scope in ('documents', 'calculations'):
        (tmp_path / scope).mkdir()
    (tmp_path / 'documents/report.json').write_text('{}')
    environment = {'filesystem_scopes': [{'scope_id': s} for s in ('documents', 'calculations')]}
    return ResourceCatalog(environment, lambda scope: tmp_path / scope,
                           workspace_root=tmp_path,
                           scope_mounts={'documents': 'documents', 'calculations': 'calculations'})


SCHEMA = {'type': 'string', 'x-resource-scope': 'documents', 'x-resource-kind': 'file'}


def test_internal_result_receives_public_mount_prefix(catalog):
    assert catalog.externalize_result('report.json', schema=SCHEMA,
                                      allowed_scopes={'documents'}) == 'documents/report.json'


def test_public_input_still_requires_mount_prefix(catalog):
    assert catalog.normalize_arguments('documents/report.json', schema=SCHEMA) == 'report.json'
    with pytest.raises(ValueError, match='挂载目录'):
        catalog.normalize_arguments('report.json', schema=SCHEMA)


def test_absolute_result_inside_its_scope_keeps_public_path(catalog):
    path = catalog._workspace_root / 'documents/report.json'
    assert catalog.externalize_result(str(path), schema=SCHEMA) == 'documents/report.json'


@pytest.mark.parametrize('path', ['../calculations/report.json', '/etc/passwd', 'missing.json'])
def test_invalid_output_stays_rejected(catalog, path):
    with pytest.raises((ValueError, FileNotFoundError)):
        catalog.externalize_result(path, schema=SCHEMA)


def test_output_cannot_use_an_undeclared_scope(catalog):
    with pytest.raises(ValueError, match='未声明'):
        catalog.externalize_result('report.json', schema=SCHEMA, allowed_scopes={'calculations'})


def test_output_symlink_cannot_cross_scopes(catalog):
    root = catalog._workspace_root
    (root / 'calculations/secret.json').write_text('{}')
    (root / 'documents/link.json').symlink_to(root / 'calculations/secret.json')
    with pytest.raises(ValueError, match='越出 Scope'):
        catalog.externalize_result('link.json', schema=SCHEMA)
