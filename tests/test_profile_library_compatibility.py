import os
from env_gen.tool_gen.software import runtime_environment


def test_profile_filters_foreign_python_libraries_and_preserves_cuda(tmp_path):
    (tmp_path / 'python-3.11').mkdir()
    foreign = '/opt/train/lib/python3.12/site-packages/torch/lib'
    native = '/usr/local/cuda/lib64'
    same = '/other/lib/python3.11/site-packages/torch/lib'
    original = {'LD_LIBRARY_PATH': os.pathsep.join([foreign, native, same]), 'KEEP': 'value'}
    result = runtime_environment(tmp_path, original)
    assert result['LD_LIBRARY_PATH'] == os.pathsep.join([native, same])
    assert result['KEEP'] == 'value'
    assert original['LD_LIBRARY_PATH'].startswith(foreign)


def test_profile_native_libraries_have_priority(tmp_path):
    library = tmp_path / 'python-3.11/lib'
    library.mkdir(parents=True)
    result = runtime_environment(tmp_path, {'LD_LIBRARY_PATH': '/usr/local/cuda/lib64'})
    assert result['LD_LIBRARY_PATH'].split(os.pathsep)[0] == str(library)


def test_unknown_python_version_preserves_inherited_paths(tmp_path):
    inherited = '/opt/train/lib/python3.12/site-packages/torch/lib'
    assert runtime_environment(tmp_path, {'LD_LIBRARY_PATH': inherited})['LD_LIBRARY_PATH'] == inherited


def test_profile_filters_foreign_dist_packages_libraries(tmp_path):
    (tmp_path / 'python-3.11').mkdir()
    original = {'LD_LIBRARY_PATH': '/usr/local/lib/python3.12/dist-packages/torch/lib:/usr/local/cuda/lib64'}
    result = runtime_environment(tmp_path, original)
    assert result['LD_LIBRARY_PATH'] == '/usr/local/cuda/lib64'


def test_stdio_preserves_explicit_empty_profile_settings(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from env_gen.tool_gen.runtime_launch import local_stdio_launch
    prefix = tmp_path / 'python-3.11'
    (prefix / 'lib').mkdir(parents=True)
    (prefix / 'lib/libpetsc.so').touch()
    monkeypatch.setenv('PETSC_ARCH', 'source_arch')
    delivery = SimpleNamespace(python_path=prefix / 'bin/python', software_root=tmp_path)
    launch = local_stdio_launch(delivery, server_path=tmp_path / 'server.py',
                                arguments=[], project_root=tmp_path)
    assert launch.environment['PETSC_ARCH'] == ''
    assert launch.environment['PETSC_DIR'] == str(prefix)


def test_flat_petsc_install_uses_relocated_profile(tmp_path):
    prefix = tmp_path / 'python-3.11'
    (prefix / 'lib').mkdir(parents=True)
    (prefix / 'lib/libpetsc.so').touch()
    (prefix / 'lib/libslepc.so').touch()
    result = runtime_environment(tmp_path, {'PETSC_DIR': '/source/profile', 'PETSC_ARCH': 'old'})
    assert result['PETSC_DIR'] == str(prefix)
    assert result['PETSC_ARCH'] == ''
    assert result['SLEPC_DIR'] == str(prefix)


def test_profile_certificate_bundle_is_relocated_and_explicit_trust_is_preserved(tmp_path):
    certificate = tmp_path / 'python-3.12/ssl/cert.pem'
    certificate.parent.mkdir(parents=True)
    certificate.write_text('profile trust roots')
    assert runtime_environment(tmp_path, {})['SSL_CERT_FILE'] == str(certificate)
    assert runtime_environment(tmp_path, {'SSL_CERT_FILE': '/unavailable/source.pem'})['SSL_CERT_FILE'] == str(certificate)
    explicit = tmp_path / 'explicit.pem'
    explicit.write_text('explicit trust roots')
    assert runtime_environment(tmp_path, {'SSL_CERT_FILE': str(explicit)})['SSL_CERT_FILE'] == str(explicit)
