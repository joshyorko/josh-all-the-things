"""Build a self-contained source candidate; never publish or alter release pins."""

import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from build_environment_artifact import (
    _environment,
    _platform_name,
    _sha256,
)


def prepare_candidate_descriptors(snapshot: Path, sha: str) -> None:
    # Candidate-only descriptors include the exact source identity in the
    # environment specification, so dependency-cache reuse cannot hide changes.
    for name in ('conda.yaml', 'environment_linux_amd64_freeze.yaml', 'environment_windows_amd64.yaml'):
        config = snapshot / name
        if config.is_file():
            text = config.read_text()
            lines = text.splitlines()
            postinstall = lines.index('rccPostInstall:')
            first_command = lines[postinstall + 1]
            indentation = first_command[:len(first_command) - len(first_command.lstrip())]
            config.write_text(text.rstrip() + f'\n{indentation}- python scripts/install_filtered_candidate.py {sha}\n')


def validate_pinned_rcc(root: Path, rcc: str):
    platform_name = _platform_name().lower()
    manifest = json.loads((root / 'runtime' / 'rcc.json').read_text())
    pin = manifest['platforms'][platform_name]
    binary_digest = _sha256(Path(rcc))
    if binary_digest != pin['sha256']:
        raise ValueError('candidate RCC does not match the tracked platform pin')
    rcc_identity = {**manifest['source'], 'asset': pin['asset'], 'sha256': binary_digest}
    return platform_name, rcc_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rcc', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    snapshot = output / 'source'
    snapshot.mkdir()
    archive = subprocess.check_output(['git', 'archive', 'HEAD'], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source_archive:
        source_archive.extractall(snapshot, filter='data')
    prepare_candidate_descriptors(snapshot, sha)
    producer_home = output / 'producer-home'
    verifier_home = output / 'verifier-home'
    producer_home.mkdir()
    verifier_home.mkdir()
    producer = _environment(dict(os.environ), producer_home, output / 'producer-rcc')
    verifier = _environment(dict(os.environ), verifier_home, output / 'verifier-rcc')
    for environment in (producer, verifier):
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
    rcc = str(Path(args.rcc).resolve())
    platform_name, rcc_identity = validate_pinned_rcc(root, rcc)
    def run(arguments, environment, cwd):
        result = subprocess.run([rcc, *arguments], cwd=cwd, env=environment, timeout=args.timeout,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
        if result.returncode:
            raise RuntimeError(f'candidate RCC operation failed: {result.stdout[-8000:]}\n{result.stderr[-4000:]}')
        return result
    published = json.loads(run(['env', 'publish', '--robot', str(snapshot / 'robot.yaml'),
                                '--provider', 'local', '--json'], producer, snapshot).stdout)
    artifact = published['artifactDigest']
    payload = output / 'jat-filtered-candidate.rcca'
    run(['env', 'export', '--artifact', artifact, '--provider', 'local', '--output', str(payload)], producer, snapshot)
    proof_script = output / 'qualify.py'
    shutil.copyfile(snapshot / 'scripts' / 'qualify_filtered_candidate.py', proof_script)
    snapshot.rename(output / 'source-unavailable')
    producer_home.rename(output / 'producer-home-unavailable')
    (output / 'producer-rcc').rename(output / 'producer-rcc-unavailable')
    acquired = json.loads(run(['env', 'acquire', '--archive', str(payload), '--permissive-local', '--json'], verifier, output).stdout)
    assert acquired['artifactDigest'] == artifact and acquired['verification']['valid']
    proof = output / 'proof'
    execution = json.loads(run(['--no-build', 'env', 'exec', '--artifact', artifact, '--permissive-local',
                               '--json', '--', 'python', str(proof_script), str(proof), sha], verifier, output).stdout)
    assert execution['exitCode'] == 0 and execution['artifactDigest'] == artifact
    receipt = {
        'format_version': 1, 'candidate_only': True, 'success': True, 'jat_git_sha': sha,
        'platform': platform_name, 'artifact_digest': artifact,
        'specification_digest': published['specificationDigest'],
        'legacy_blueprint_key': published['legacyBlueprintKey'], 'rcc_source': rcc_identity,
        'archive': {'filename': payload.name, 'sha256': _sha256(payload), 'size': payload.stat().st_size},
        'qualification': json.loads((proof / 'qualification.json').read_text()),
    }
    (output / 'candidate-receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
