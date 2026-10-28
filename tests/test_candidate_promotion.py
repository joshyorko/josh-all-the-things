import importlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from jsonschema import validate


@pytest.fixture
def promotion(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'scripts'))
    return importlib.import_module('qualify_candidate_for_promotion')


@pytest.fixture
def candidate(tmp_path, promotion, monkeypatch):
    source = Path(__file__).parents[1]
    root = tmp_path / 'root'
    (root / 'runtime').mkdir(parents=True)
    manifest = json.loads((source / 'runtime/rcc.json').read_text())
    executable = tmp_path / 'rcc'
    executable.write_bytes(b'pinned-test-rcc')
    monkeypatch.setattr('build_filtered_candidate._platform_name', lambda: 'linux_amd64')
    manifest['platforms']['linux_amd64']['sha256'] = promotion._sha256(executable)
    (root / 'runtime/rcc.json').write_text(json.dumps(manifest))
    (root / 'runtime/hauler.json').write_text((source / 'runtime/hauler.json').read_text())
    for name in ('robot.yaml', 'conda.yaml', 'environment_linux_amd64_freeze.yaml'):
        (root / name).write_text((source / name).read_text())
    (root / 'src/jat').mkdir(parents=True)
    (root / 'src/jat/__init__.py').write_text('')
    (root / 'tasks.py').write_text('')
    archive = tmp_path / 'candidate.rcca'
    with zipfile.ZipFile(archive, 'w') as payload:
        payload.writestr('rcc-environment/manifest.json', json.dumps({
            'artifactDigest': 'sha256:' + 'b' * 64,
            'specification': {'digest': 'sha256:' + 'c' * 64},
            'legacyBlueprint': {'legacyBlueprintKey': 'd' * 16},
        }))
    _, identity = promotion.validate_pinned_rcc(root, str(executable))
    receipt = {'format_version': 1, 'success': True, 'candidate_only': True, 'jat_git_sha': 'a' * 40,
               'platform': 'linux_amd64', 'rcc_source': identity,
               'artifact_digest': 'sha256:' + 'b' * 64, 'specification_digest': 'sha256:' + 'c' * 64,
               'legacy_blueprint_key': 'd' * 16,
               'archive': {'filename': archive.name, 'size': archive.stat().st_size,
                           'sha256': promotion._sha256(archive)}}
    receipt_path = tmp_path / 'candidate.json'
    receipt_path.write_text(json.dumps(receipt))
    return root, archive, receipt_path, str(executable), receipt


@pytest.mark.parametrize('change', ['source', 'archive', 'platform', 'rcc', 'malformed', 'success'])
def test_rejects_malformed_or_mismatched_candidate_before_execution(promotion, candidate, change):
    root, archive, receipt_path, rcc, receipt = candidate
    if change == 'source':
        receipt['jat_git_sha'] = 'e' * 40
    elif change == 'archive':
        archive.write_bytes(b'mutated')
    elif change == 'platform':
        receipt['platform'] = 'windows_amd64'
    elif change == 'rcc':
        receipt['rcc_source']['sha256'] = '0' * 64
    elif change == 'success':
        receipt['success'] = False
    receipt_path.write_text('{' if change == 'malformed' else json.dumps(receipt))
    with pytest.raises(ValueError):
        promotion.validate_candidate(root, archive, receipt_path, rcc, 'a' * 40)


@pytest.mark.parametrize('failed_gate', [None, 'acquire', 'source', 'embedded', 'hauler', 'warm', 'specification', 'blueprint', 'descriptor'])
def test_v3_receipt_requires_actual_gate_success_and_never_rebuilds(
    promotion, candidate, monkeypatch, tmp_path, failed_gate,
):
    root, archive, receipt_path, rcc, receipt = candidate
    calls = []
    original_archive = archive.read_bytes()
    if failed_gate in {'specification', 'blueprint'}:
        key = 'specification_digest' if failed_gate == 'specification' else 'legacy_blueprint_key'
        receipt[key] = 'sha256:' + 'e' * 64 if failed_gate == 'specification' else 'e' * 16
        receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(promotion, '_git_sha', lambda *args: 'a' * 40)
    monkeypatch.setattr(promotion, 'committed_source_hashes', lambda root: {'__init__.py': 'f' * 64})
    def run(argv, **kwargs):
        calls.append(argv)
        assert not kwargs['env'].get('PYTHONPATH')
        assert 'publish' not in argv and 'export' not in argv
        if argv[1:] == ['version']:
            text = 'v18.19.7\n'
        elif argv[1:3] == ['env', 'acquire']:
            text = json.dumps({'artifactDigest': receipt['artifact_digest'],
                               'verification': {'valid': failed_gate != 'acquire'}})
        elif 'vars' in argv:
            text = json.dumps([{'key': 'RCC_ENVIRONMENT_HASH',
                                'value': 'e' * 16 if failed_gate == 'descriptor' else 'd' * 16}])
        else:
            execution_receipt = Path(argv[argv.index('--receipt-file') + 1])
            label = execution_receipt.stem
            execution_receipt.write_text(json.dumps({'artifactDigest': receipt['artifact_digest'],
                                                      'exitCode': 1 if failed_gate == label else 0}))
            text = ''
        return subprocess.CompletedProcess(argv, 0, text, '')
    monkeypatch.setattr(promotion, '_run', run)
    output = tmp_path / 'promotion.json'
    if failed_gate:
        with pytest.raises(ValueError):
            promotion.qualify(root, archive, receipt_path, rcc, output)
        assert not output.exists()
        return
    result = promotion.qualify(root, archive, receipt_path, rcc, output)
    expected_launcher = [*promotion.HAULER_VERSION_COMMAND, 'v2.1.1']
    hauler_call = next(argv for argv in calls if '--receipt-file' in argv
                       and Path(argv[argv.index('--receipt-file') + 1]).stem == 'hauler')
    assert hauler_call[hauler_call.index('--') + 1:] == expected_launcher
    assert result['verified_hauler']['launcher'] == expected_launcher
    assert result['hauler_version'] == 'v2.1.1'
    validate(result, json.loads((Path(__file__).parents[1] / 'docs/environment-artifact-receipt.schema.json').read_text()))
    assert archive.read_bytes() == original_archive
    assert any('http://127.0.0.1:1' in argv and '--no-build' in argv for argv in calls)
    assert any('vars' in argv and '--no-build' in argv for argv in calls)
    assert any(any('qualify_filtered_candidate.py' in arg for arg in argv) for argv in calls)
    assert json.loads(output.read_text()) == result


def test_canonical_source_hashes_use_git_bytes_not_checkout_line_endings(promotion, monkeypatch, tmp_path):
    import hashlib
    import io
    import tarfile

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, contents in [('src/jat/example.py', b'line\n'), ('tasks.py', b'bridge\n')]:
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    monkeypatch.setattr(promotion.subprocess, 'check_output', lambda *args, **kwargs: stream.getvalue())
    assert promotion.committed_source_hashes(tmp_path) == {
        'example.py': hashlib.sha256(b'line\n').hexdigest(),
        'candidate_tasks.py': hashlib.sha256(b'bridge\n').hexdigest(),
    }


def test_cli_preserves_symlink_identity_for_validation(promotion, tmp_path, monkeypatch):
    target = tmp_path / 'target.rcca'
    target.write_bytes(b'payload')
    link = tmp_path / 'link.rcca'
    link.symlink_to(target)
    monkeypatch.setattr(sys, 'argv', [
        'qualifier', '--archive', str(link), '--candidate-receipt', str(tmp_path / 'candidate.json'),
        '--rcc', str(tmp_path / 'rcc'), '--output', str(tmp_path / 'receipt.json'),
    ])
    def qualify(root, archive, *args):
        assert archive.is_symlink()
        raise ValueError('candidate archive must be a regular non-symlink file')
    monkeypatch.setattr(promotion, 'qualify', qualify)
    with pytest.raises(ValueError, match='non-symlink'):
        promotion.main()


@pytest.mark.parametrize('optimize', [False, True])
def test_promotion_source_gate_rejects_wrong_embedded_sha(
    promotion, candidate, monkeypatch, tmp_path, optimize,
):
    root, archive, receipt_path, rcc, receipt = candidate
    package = tmp_path / 'environment' / 'jat'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / '_candidate.json').write_text(json.dumps({'jat_git_sha': 'wrong', 'files': {}}))
    monkeypatch.setattr(promotion, '_git_sha', lambda *args: 'a' * 40)
    monkeypatch.setattr(promotion, 'committed_source_hashes', lambda root: {})
    def run(argv, **kwargs):
        if argv[1:] == ['version']:
            text = 'v18.19.7'
        elif 'acquire' in argv:
            text = json.dumps({'artifactDigest': receipt['artifact_digest'], 'verification': {'valid': True}})
        elif 'vars' in argv:
            text = json.dumps([{'key': 'RCC_ENVIRONMENT_HASH', 'value': 'd' * 16}])
        else:
            result_path = Path(argv[argv.index('--receipt-file') + 1])
            if result_path.stem == 'source':
                command = argv[argv.index('--') + 1:]
                driver = (f'import sys; sys.prefix={str(package.parent)!r}; '
                          f'sys.path.insert(0, {str(package.parent)!r}); ' + command[2])
                environment = dict(os.environ)
                environment['PYTHONOPTIMIZE'] = '1' if optimize else ''
                result = subprocess.run([sys.executable, '-c', driver, *command[3:]],
                                        env=environment, capture_output=True, text=True, check=False)
                code = result.returncode
            else:
                code = 0
            result_path.write_text(json.dumps({'artifactDigest': receipt['artifact_digest'], 'exitCode': code}))
            text = ''
        return subprocess.CompletedProcess(argv, 0, text, '')
    monkeypatch.setattr(promotion, '_run', run)
    output = tmp_path / 'promotion.json'
    with pytest.raises(ValueError, match='source did not execute'):
        promotion.qualify(root, archive, receipt_path, rcc, output)
    assert not output.exists()


@pytest.mark.parametrize('optimize', [False, True])
def test_embedded_qualifier_checks_sha_before_building(tmp_path, optimize):
    package = tmp_path / 'environment' / 'jat'
    package.mkdir(parents=True)
    (package / '_candidate.json').write_text(json.dumps({'jat_git_sha': 'wrong', 'files': {}}))
    script = Path(__file__).parents[1] / 'scripts/qualify_filtered_candidate.py'
    driver = f"""
import runpy, sys, types
from pathlib import Path
package = types.ModuleType('jat')
package.__file__ = {str(package / '__init__.py')!r}
models = types.ModuleType('jat.models')
models.WORKSPACE_CAPTURE_POLICY_VERSION = 1
def unverified_build(**kwargs):
    raise RuntimeError('build reached before SHA verification')
models.BuildRequest = unverified_build
models.RestoreRequest = unverified_build
services = types.ModuleType('jat.services')
services.JATService = lambda **kwargs: object()
sys.modules.update({{'jat': package, 'jat.models': models, 'jat.services': services}})
sys.prefix = {str(package.parent)!r}
sys.argv = [{str(script)!r}, {str(tmp_path / 'proof')!r}, 'expected']
runpy.run_path({str(script)!r}, run_name='__main__')
"""
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    environment['PYTHONOPTIMIZE'] = '1' if optimize else ''
    result = subprocess.run([sys.executable, '-c', driver], env=environment, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert 'candidate source SHA does not match' in result.stderr
    assert not (tmp_path / 'proof/qualification.json').exists()


def test_runtime_pin_only_changes_trigger_candidate_qualification():
    import ast
    import fnmatch

    workflow = Path(__file__).parents[1] / '.github/workflows/filtered-capture-candidate.yml'
    section = workflow.read_text().split('    paths:\n', 1)[1].split('\n\n', 1)[0]
    paths = [ast.literal_eval(line.strip()[2:]) for line in section.splitlines()]
    for manifest in ('runtime/rcc.json', 'runtime/hauler.json'):
        assert any(fnmatch.fnmatchcase(manifest, pattern) for pattern in paths)
