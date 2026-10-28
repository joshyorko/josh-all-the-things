import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def owned_probe(tmp_path, monkeypatch):
    from scripts import verify_hauler_runtime as probe
    prefix = tmp_path / 'environment'
    (prefix / 'bin').mkdir(parents=True)
    hauler = prefix / 'bin/hauler'
    hauler.write_bytes(b'synthetic executable')
    monkeypatch.setenv('CONDA_PREFIX', str(prefix))
    monkeypatch.setattr(probe.sys, 'executable', str(prefix / 'bin/python'))
    monkeypatch.setattr(probe.shutil, 'which', lambda _: str(hauler))
    return SimpleNamespace(probe=probe, prefix=prefix)


@pytest.mark.parametrize('version', ['2.1.1', 'v2.1.1'])
def test_owned_exact_hauler_release_is_accepted(owned_probe, monkeypatch, version):
    monkeypatch.setattr(owned_probe.probe.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, 'GitVersion: '+version+'\n', ''))
    assert owned_probe.probe.verify_owned_hauler('v2.1.1')['version'] == '2.1.1'


@pytest.mark.parametrize('output', ['GitVersion: 2.1.10\n', 'GitVersion: 2.1.1\nGitVersion: 2.1.1\n', 'unrecognized output\n', 'GitVersion: 2.1.1\nGitVersion: unknown\n', 'GitVersion: 2.1.1\nGitVersion: 2.1.10-rc1\n'])
def test_wrong_or_ambiguous_release_is_rejected(owned_probe, monkeypatch, output):
    monkeypatch.setattr(owned_probe.probe.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, output, ''))
    with pytest.raises(ValueError, match='version'):
        owned_probe.probe.verify_owned_hauler('v2.1.1')


def test_host_python_cannot_qualify_the_runtime(owned_probe, monkeypatch):
    monkeypatch.setattr(owned_probe.probe.sys, 'executable', '/host/python')
    with pytest.raises(ValueError, match='environment'):
        owned_probe.probe.verify_owned_hauler('v2.1.1')
