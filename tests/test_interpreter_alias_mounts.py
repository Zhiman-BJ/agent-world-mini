from harness.sandbox import _interpreter_home_mounts


def test_runtime_home_alias_keeps_its_standard_library(tmp_path):
    base = tmp_path / 'python-version'
    (base / 'bin').mkdir(parents=True)
    (base / 'lib/python3.11/encodings').mkdir(parents=True)
    alias = tmp_path / 'python'
    alias.symlink_to(base, target_is_directory=True)
    mounts = _interpreter_home_mounts(alias / 'bin', base)
    assert mounts == [alias / 'bin', alias]
    assert (mounts[1] / 'lib/python3.11/encodings').is_dir()


def test_canonical_home_keeps_the_same_declared_runtime(tmp_path):
    base = tmp_path / 'python'
    (base / 'bin').mkdir(parents=True)
    assert _interpreter_home_mounts(base / 'bin', base) == [base / 'bin', base]


def test_unrelated_home_does_not_expose_its_parent(tmp_path):
    base = tmp_path / 'python'
    (base / 'bin').mkdir(parents=True)
    unrelated = tmp_path / 'other-data/bin'
    unrelated.mkdir(parents=True)
    assert _interpreter_home_mounts(unrelated, base) == [unrelated]
