import pytest

from harness.sandbox import _container_procfs, _sandbox_namespaces


@pytest.mark.parametrize('docker,kubernetes,expected', [
    (None, None, False),
    ('1', None, True),
    (None, '1', True),
    ('1', '1', True),
    ('0', '0', False),
    ('true', None, False),
    (None, 'true', False),
])
def test_container_procfs_requires_explicit_runtime_configuration(
        monkeypatch, docker, kubernetes, expected):
    for name, value in (
        ('AGENT_WORLD_DOCKER_RUNTIME', docker),
        ('AGENT_WORLD_KUBERNETES_RUNTIME', kubernetes),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert _container_procfs() is expected


def test_host_sandbox_creates_fresh_pid_namespace(monkeypatch):
    monkeypatch.delenv('AGENT_WORLD_DOCKER_RUNTIME', raising=False)
    monkeypatch.delenv('AGENT_WORLD_KUBERNETES_RUNTIME', raising=False)
    assert _sandbox_namespaces() == ['--unshare-all']


@pytest.mark.parametrize('flag', ['AGENT_WORLD_DOCKER_RUNTIME', 'AGENT_WORLD_KUBERNETES_RUNTIME'])
def test_container_sandbox_keeps_procfs_pid_identity_and_other_isolation(monkeypatch, flag):
    monkeypatch.setenv(flag, '1')
    flags = _sandbox_namespaces()
    assert '--unshare-pid' not in flags and '--unshare-all' not in flags
    assert set(flags) == {'--unshare-user', '--unshare-ipc', '--unshare-net',
                          '--unshare-uts', '--unshare-cgroup'}
