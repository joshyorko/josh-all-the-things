#!/usr/bin/env python3
"""Observe installed package hashes; this is not a complete package SBOM."""
import base64
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def pinned_hauler_version(output):
    fields = [line.partition(':')[2].strip() for line in output.splitlines()
              if line.lstrip().startswith('GitVersion:')]
    if len(fields) != 1 or re.fullmatch(r'v?2\.1\.1', fields[0]) is None:
        raise ValueError('Hauler version does not match pinned v2.1.1')
    return fields[0].removeprefix('v')


def installed_sha256(path, prefix):
    path = Path(path).resolve()
    if not path.is_relative_to(prefix.resolve()) or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def python_packages(prefix, distributions):
    packages = []
    for distribution in distributions:
        files = []
        for entry in distribution.files or ():
            recorded = entry.hash
            if recorded is None:
                continue
            observed = installed_sha256(distribution.locate_file(entry), prefix)
            matches = None
            if recorded.mode == 'sha256' and observed is not None:
                encoded = base64.urlsafe_b64encode(bytes.fromhex(observed)).decode().rstrip('=')
                matches = encoded == recorded.value.rstrip('=')
            files.append({'path': str(entry), 'recordedHash': {'algorithm': recorded.mode,
                          'value': recorded.value, 'encoding': 'urlsafe-base64'},
                          'observedSHA256': observed, 'matchesRecordedHash': matches})
        packages.append({'name': distribution.metadata['Name'], 'version': distribution.version,
                         'fileHashes': sorted(files, key=lambda value: value['path'])})
    return sorted(packages, key=lambda value: (value['name'], value['version']))


def conda_packages(prefix):
    packages = []
    for path in (prefix / 'conda-meta').glob('*.json'):
        package = json.loads(path.read_text())
        files = []
        for entry in package.get('paths_data', {}).get('paths', []):
            recorded = {key: entry[key] for key in ('sha256', 'sha256_in_prefix') if entry.get(key)}
            if not recorded:
                continue
            observed = installed_sha256(prefix / entry['_path'], prefix)
            expected = recorded.get('sha256_in_prefix', recorded.get('sha256'))
            files.append({'path': entry['_path'], 'recordedHashes': recorded,
                          'observedSHA256': observed,
                          'matchesRecordedHash': observed == expected if observed is not None else None})
        packages.append({'name': package['name'], 'version': package['version'], 'build': package.get('build'),
                         'archiveHashes': {key: package[key] for key in ('sha256', 'md5') if package.get(key)},
                         'fileHashes': sorted(files, key=lambda value: value['path'])})
    return sorted(packages, key=lambda value: (value['name'], value['version']))


def main():
    prefix = Path(os.environ['CONDA_PREFIX']).resolve()
    hauler = Path(shutil.which('hauler')).resolve()
    assert hauler.is_relative_to(prefix) and Path(sys.executable).resolve().is_relative_to(prefix)
    version = subprocess.run([str(hauler), 'version'], capture_output=True, text=True, check=True, timeout=30)
    reported_version = pinned_hauler_version(version.stdout + '\n' + version.stderr)
    packages = python_packages(prefix, metadata.distributions())
    pip = json.loads(subprocess.check_output([sys.executable, '-m', 'pip', 'list', '--format=json',
                                             '--disable-pip-version-check'], text=True))
    normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
    assert {(normalize(value['name']), value['version']) for value in packages} == {
        (normalize(value['name']), value['version']) for value in pip}
    result = {'kind': 'observed-package-list-not-SBOM', 'fileHashesComplete': False,
              'python': sys.version, 'pythonPath': sys.executable, 'haulerPath': str(hauler),
              'haulerSHA256': installed_sha256(hauler, prefix), 'haulerVersion': reported_version,
              'pythonDistributions': packages, 'condaPackages': conda_packages(prefix)}
    Path(sys.argv[1]).write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
    print('JAT_ENVIRONMENT_HAULER_AND_PACKAGE_PROOF')


if __name__ == '__main__':
    main()
