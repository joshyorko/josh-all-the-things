"""Verify existing candidate bytes and emit a v3 receipt; never build or publish."""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from build_environment_artifact import (
    HAULER_VERSION_COMMAND,
    _environment,
    _git_sha,
    _hauler_version,
    _run,
    _sha256,
)
from build_filtered_candidate import prepare_candidate_descriptors, validate_pinned_rcc


def committed_source_hashes(root):
    # Hash Git blobs, not checkout line endings (Windows may use CRLF).
    snapshot = subprocess.check_output(['git', 'archive', 'HEAD', 'src/jat', 'tasks.py'], cwd=root)
    hashes = {}
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('.py'):
                name = 'candidate_tasks.py' if member.name == 'tasks.py' else member.name.removeprefix('src/jat/')
                hashes[name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    return hashes


def validate_candidate(root, archive, receipt_path, rcc, source_sha):
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('candidate archive must be a regular non-symlink file')
    if receipt_path.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size > 1024 * 1024:
        raise ValueError('candidate receipt must be a bounded regular file')
    candidate = json.loads(receipt_path.read_text())
    if not isinstance(candidate, dict) or candidate.get('format_version') != 1:
        raise ValueError('unsupported candidate receipt')
    if candidate.get('candidate_only') is not True or candidate.get('success') is not True:
        raise ValueError('candidate qualification did not succeed')
    if candidate.get('jat_git_sha') != source_sha:
        raise ValueError('candidate source does not match this exact checkout')
    platform, rcc_identity = validate_pinned_rcc(root, rcc)
    if candidate.get('platform') != platform or candidate.get('rcc_source') != rcc_identity:
        raise ValueError('candidate platform or RCC provenance does not match the current pin')
    for name in ('artifact_digest', 'specification_digest'):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', str(candidate.get(name, ''))):
            raise ValueError(f'invalid candidate {name}')
    if not isinstance(candidate.get('legacy_blueprint_key'), str) or not candidate['legacy_blueprint_key']:
        raise ValueError('candidate blueprint identity is missing')
    identity = candidate.get('archive')
    if not isinstance(identity, dict) or identity != {
        'filename': archive.name, 'size': archive.stat().st_size, 'sha256': _sha256(archive),
    }:
        raise ValueError('candidate archive does not match its receipt')
    return candidate


def qualify(root, archive, candidate_path, rcc, output, timeout=1800):
    if output.exists():
        raise FileExistsError('promotion receipt already exists')
    source_sha = _git_sha(root, dict(os.environ), timeout)
    candidate = validate_candidate(root, archive, candidate_path, rcc, source_sha)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='jat-promotion-qualification-') as name:
        work = Path(name)
        home = work / 'home'
        home.mkdir()
        environment = _environment(dict(os.environ), home, work / 'rcc')
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        def run(args):
            return _run([rcc, *args], cwd=work, env=environment, timeout=timeout)
        version = run(['version']).stdout.splitlines()[0].strip()
        manifest = json.loads((root / 'runtime/rcc.json').read_text())
        if version != manifest['version']:
            raise ValueError('RCC executable version does not match the pin')
        artifact = candidate['artifact_digest']
        acquired = json.loads(run(['env', 'acquire', '--archive', str(archive), '--permissive-local', '--json']).stdout)
        if acquired.get('artifactDigest') != artifact or acquired.get('verification', {}).get('valid') is not True:
            raise ValueError('fresh acquisition did not verify the exact candidate')
        # Reconstruct only the matching descriptors. No project source or
        # installer is present, and --no-build forbids executing postinstall.
        descriptors = work / 'descriptors'
        descriptors.mkdir()
        for path in [root / 'robot.yaml', root / 'conda.yaml', *root.glob('environment_*.yaml')]:
            shutil.copyfile(path, descriptors / path.name)
        prepare_candidate_descriptors(descriptors, source_sha)
        variables = json.loads(run(['--no-build', 'ht', 'vars', '--robot', str(descriptors / 'robot.yaml'), '--json']).stdout)
        if not isinstance(variables, list):
            raise TypeError('no-build descriptor resolution did not return variables')
        def execute(label, command, extra=()):
            receipt = work / f'{label}.json'
            run(['--no-build', 'env', 'exec', '--artifact', artifact, '--permissive-local',
                 *extra, '--inherit-streams', '--receipt-file', str(receipt), '--', *command])
            result = json.loads(receipt.read_text())
            if result.get('artifactDigest') != artifact or result.get('exitCode') != 0:
                raise ValueError(f'{label} did not execute the exact candidate successfully')
        expected_files = committed_source_hashes(root)
        expected = work / 'expected-source.json'
        expected.write_text(json.dumps({'jat_git_sha': source_sha, 'files': expected_files}))
        execute('source', ['python', '-c',
            ("import hashlib,json,sys; from pathlib import Path; import jat; "
            "package=Path(jat.__file__).resolve().parent; "
            "assert Path(sys.prefix).resolve() in package.parents; "
            "expected=json.loads(Path(sys.argv[1]).read_text()); "
            "embedded=json.loads((package/'_candidate.json').read_text()); "
            "assert embedded['jat_git_sha']==expected['jat_git_sha']; "
            "assert embedded['files']==expected['files']; "
            "assert all(hashlib.sha256((package/name).read_bytes()).hexdigest()==digest "
            "for name,digest in expected['files'].items())"), str(expected)])
        execute('embedded', ['python', str(root / 'scripts/qualify_filtered_candidate.py'), str(work / 'proof'), source_sha])
        execute('hauler', list(HAULER_VERSION_COMMAND))
        execute('warm', ['python', '-c', "print('jat-warm-reuse-proof')"], [
            '--provider', 'http://127.0.0.1:1', '--trust-carrier',
            str(Path(environment['ROBOCORP_HOME']) / 'artifacts/v1/content/trust'),
            '--trust-carrier-type', 'filesystem',
        ])
        # Existing publisher v3 contract; no optimistic conversion of old fields.
        receipt = {
            'format_version': 3, 'operation': 'build', 'success': True,
            'jat_git_sha': source_sha, 'rcc_executable': Path(rcc).name,
            'rcc_version': version, 'hauler_version': _hauler_version(root),
            'rcc_source': candidate['rcc_source'], 'platform': candidate['platform'],
            'artifact_digest': artifact, 'specification_digest': candidate['specification_digest'],
            'legacy_blueprint_key': candidate['legacy_blueprint_key'], 'archive': candidate['archive'],
            'verified_acquire': {'fresh_home': True, 'no_build': True},
            'verified_no_build': {'fresh_home': True, 'no_build': True},
            'verified_exec': {'fresh_home': True},
            'verified_warm_reuse': {'fresh_home': True, 'no_build': True, 'provider_unavailable': True},
            'verified_hauler': {'fresh_home': True, 'command': ['hauler', 'version'],
                                'launcher': list(HAULER_VERSION_COMMAND),
                                'resolved_under_conda_prefix': True, 'exit_code': 0},
        }
        if _sha256(archive) != candidate['archive']['sha256']:
            raise ValueError('candidate bytes changed during qualification')
        if _git_sha(root, dict(os.environ), timeout) != source_sha:
            raise ValueError('source checkout changed during qualification')
        # Exclusive creation: never replace an existing approval/evidence file.
        with output.open('x') as handle:
            handle.write(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--candidate-receipt', type=Path, required=True)
    parser.add_argument('--rcc', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    receipt = qualify(Path(__file__).resolve().parents[1], args.archive.absolute(),
                      args.candidate_receipt.absolute(), str(Path(args.rcc).resolve()),
                      args.output.absolute(), args.timeout)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
