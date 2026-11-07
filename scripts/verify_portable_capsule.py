#!/usr/bin/env python3
"""Opt-in real consumer acceptance. RCC evaluates trust; JAT composes content.

Requires cryptography only in this test driver for disposable key generation.
Never changes runtime pins, release assets, real trust roots, or signing keys.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, separators=(',', ':'), ensure_ascii=True), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rcc', type=Path, required=True)
    parser.add_argument('--rcc-sha256', required=True)
    parser.add_argument('--rcc-source', required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--work', type=Path, required=True, help='New private persistent directory; retained as evidence')
    parser.add_argument('--network', choices=('isolated', 'providerless'), default='isolated')
    args = parser.parse_args(argv)
    binary = args.rcc.resolve(strict=True)
    if sha256(binary) != args.rcc_sha256:
        raise ValueError('candidate SHA256 mismatch')
    if not re.fullmatch('[0-9a-f]{40}', args.rcc_source):
        raise ValueError('full RCC source commit required')
    archive = args.archive.resolve(strict=True)
    if sha256(archive) != args.archive_sha256:
        raise ValueError('input archive SHA256 mismatch')
    if platform.system() not in ('Linux', 'Windows'):
        raise RuntimeError('qualification supports Linux/Windows only')
    offline = []
    if args.network == 'isolated':
        bwrap = shutil.which('bwrap')
        if platform.system() != 'Linux' or bwrap is None:
            raise RuntimeError('isolated mode requires Linux bubblewrap; providerless is an explicit weaker alternative')
        offline = [bwrap, '--unshare-net', '--bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--']
        subprocess.run([*offline, str(binary), 'version'], check=True, capture_output=True)
    work = args.work.resolve()
    work.mkdir(mode=0o700, parents=True, exist_ok=False)
    logs = work / 'logs'
    logs.mkdir()
    publisher = work / 'publisher-home'
    consumer = work / 'consumer-home'
    operator_home = work / 'operator-home'
    authority = work / 'publisher-authority'
    deployment = work / 'deployment'
    carrier = work / 'publisher-carrier'
    for directory in (publisher, consumer, operator_home, authority, deployment):
        directory.mkdir(mode=0o700)
    base_env = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR',
        'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
    }}
    base_env.update(RCC_HOLOTREE_MODE='private', RCC_AUTOUPDATES_RCC_INDEX='', RCC_AUTOUPDATES_TEMPLATES='', PYTHONDONTWRITEBYTECODE='1')

    def environment(home, source=ROOT):
        result = dict(base_env)
        result.update(HOME=str(home), USERPROFILE=str(home), ROBOCORP_HOME=str(home / 'rcc'), PYTHONPATH=str(source / 'src'))
        return result

    def run(label, command, home=operator_home, source=ROOT, isolated=False, deny=None):
        result = subprocess.run([*(offline if isolated else []), str(binary), *map(str, command)], cwd=source,
                                env=environment(home, source), capture_output=True, text=True, timeout=900, check=False)
        (logs / f'{label}.stdout').write_text(result.stdout, encoding='utf-8')
        (logs / f'{label}.stderr').write_text(result.stderr, encoding='utf-8')
        write_json(logs / f'{label}.status.json', {'exitCode': result.returncode})
        if deny is not None:
            if result.returncode == 0 or deny not in result.stderr + result.stdout:
                raise AssertionError(f'{label}: expected {deny}; inspect retained logs')
        elif result.returncode:
            raise RuntimeError(f'{label}: exit {result.returncode}; inspect {logs / (label + ".stderr")}')
        return result

    def value(label, command, **kwargs):
        return json.loads(run(label, command, **kwargs).stdout)

    # Test-only key generation, not a second implementation of RCC signing or verification.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    public_keys = {}
    for name in ('artifact-signer', 'authority-A', 'authority-B', 'unauthorized'):
        key = Ed25519PrivateKey.generate()
        seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        (authority / f'{name}.key').write_bytes(seed)
        (authority / f'{name}.key').chmod(0o600)
        public_keys[name] = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode().rstrip('=')
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def stamp(at):
        return at.isoformat().replace('+00:00', 'Z')

    roots = {'schemaVersion': 2, 'scope': 'jat-disposable-capsule', 'minimumSequence': 1,
             'keys': {name: {'publicKey': public_keys[name], 'notBefore': stamp(now - timedelta(days=1)),
                             'notAfter': stamp(now + timedelta(days=1))} for name in ('authority-A', 'authority-B')}}
    write_json(deployment / 'metadata-roots.json', roots)
    write_json(deployment / 'artifact-roots.json', {'artifact-signer': public_keys['artifact-signer']})
    write_json(authority / 'revocations.json', [])
    # Bootstrap only the publisher from the existing unsigned shipped runtime.
    # Every consumer acquisition and execution below requires strict v2 verification.
    original = value('publisher-import-existing', ['--no-build', 'env', 'acquire', '--archive', archive, '--permissive-local', '--json'], home=publisher)
    published = value('publisher-sign-no-build', ['--no-build', 'env', 'publish', '--robot', ROOT / 'robot.yaml', '--provider', 'local',
                      '--trust-carrier', carrier, '--signing-key', authority / 'artifact-signer.key', '--signing-key-id', 'artifact-signer', '--json'], home=publisher)
    artifact = published['artifactDigest']
    # Publish inventories the consumer-derived catalog; its byte identity may differ
    # from the original producer catalog even with the identical environment spec.
    with zipfile.ZipFile(archive) as original_archive:
        names = [name for name in original_archive.namelist() if name.endswith('/manifest.json')]
        assert len(names) == 1
        input_manifest = json.loads(original_archive.read(names[0]))
    assert published['specificationDigest'] == input_manifest['specification']['digest']
    assert published['legacyBlueprintKey'] == input_manifest['legacyBlueprint']['legacyBlueprintKey']
    attachment_dir = carrier / 'sha256' / artifact.removeprefix('sha256:')
    issuer = ['--artifact', artifact, '--metadata-trust-roots', deployment / 'metadata-roots.json', '--metadata-state', authority / 'issuer-state',
              '--metadata-key-id', 'authority-A', '--metadata-signing-key', authority / 'authority-A.key',
              '--issued-at', stamp(now), '--expires-at', stamp(now + timedelta(hours=4))]
    first = authority / 'snapshot-1.json'

    def distribute(label, snapshot, target):
        return value(label, ['env', 'trust', 'revocations', 'distribute', '--artifact', artifact, '--metadata', snapshot,
                            '--metadata-trust-roots', deployment / 'metadata-roots.json', '--metadata-state', deployment / 'distributor-state',
                            '--trust-carrier', target, '--json'])

    trust = ['--strict-remote', '--trust-roots', deployment / 'artifact-roots.json', '--metadata-trust-roots', deployment / 'metadata-roots.json']

    def task(label, command, home=consumer, source=ROOT, target=None, deny=None):
        state = deployment / ('publisher-worker-state' if home == publisher else 'worker-state')
        if target is None and home == consumer:
            target = Path(acquired['trustCarrierPath'])
        flags = ['--trust-carrier', target] if target else []
        receipt = logs / f'{label}.receipt.json'
        result = run(label, ['--no-build', 'env', 'exec', '--artifact', artifact, *trust, '--metadata-state', state,
                            *flags, '--inherit-streams', '--receipt-file', receipt, '--', *command],
                     home=home, source=source, isolated=home == consumer, deny=deny)
        if deny is None:
            decision = json.loads(receipt.read_text())
            assert decision['exitCode'] == 0 and decision['verification']['metadataVersion'] == 2 and decision['verification']['valid'] is True
            return decision, result.stdout
        return None, result.stdout

    workspace = work / 'synthetic-workspace'
    workspace.mkdir()
    (workspace / 'proof.txt').write_text('verified-portable-jat-capsule\n')
    haul = work / 'workspace-haul.tar.zst'
    # Publisher bootstrap is explicit local execution; consumer execution is always strict.
    run('publisher-build-capsule-content', ['--no-build', 'env', 'exec', '--artifact', original['artifactDigest'], '--permissive-local',
        '--inherit-streams', '--receipt-file', logs / 'publisher-build.receipt.json', '--',
        'python', '-m', 'jat.cli', 'build', '--folder', workspace, '--output', haul, '--rcc-environment', 'off', '--json'], home=publisher)
    source_files = list(filter(None, subprocess.check_output(['git', 'ls-files', '-z', 'src/jat'], cwd=ROOT).decode().split('\0')))
    source_manifest = work / 'source-manifest.json'
    write_json(source_manifest, {'artifactDigest': artifact, 'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                'workspaceHaulSHA256': sha256(haul), 'files': {name: sha256(ROOT / name) for name in sorted(source_files)}})
    reviewed_provenance = authority / 'reviewed-provenance.json'
    provenance = json.loads((attachment_dir / 'provenance.json').read_text())
    provenance['buildIdentity'] = 'sha256:' + sha256(source_manifest)
    write_json(reviewed_provenance, provenance)
    value('issue-1', ['env', 'trust', 'revocations', 'issue', *issuer, '--sequence', '1', '--provenance', reviewed_provenance,
          '--sbom', attachment_dir / 'sbom.json', '--revocations', authority / 'revocations.json', '--output', first, '--json'])
    distribute('distribute-publisher', first, carrier)
    signed = work / 'signed-runtime.rcca'
    exported = value('export-signed', ['env', 'export', '--artifact', artifact, '--provider', 'local', '--trust-carrier', carrier,
                                     '--include-trust-attachments', '--output', signed], home=publisher)
    assert exported['trustAttachmentsIncluded'] is True
    capsule = work / 'capsule.zip'
    with zipfile.ZipFile(capsule, 'x', compression=zipfile.ZIP_STORED) as transport:
        transport.write(signed, 'runtime.rcca')
        transport.write(haul, 'workspace-haul.tar.zst')
        transport.write(source_manifest, 'source-manifest.json')
        for name in filter(None, source_files):
            transport.write(ROOT / name, 'source/' + name)
    unpacked = work / 'transported'
    unpacked.mkdir()
    with zipfile.ZipFile(capsule) as transport:
        for member in transport.infolist():
            name = PurePosixPath(member.filename)
            if name.is_absolute() or '..' in name.parts or ':' in member.filename or '\\' in member.filename or member.file_size > 2 * 1024**3:
                raise ValueError('unsafe test capsule member')
            destination = unpacked.joinpath(*name.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with transport.open(member) as incoming, destination.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
    publisher.rename(work / 'publisher-unavailable')
    source = unpacked / 'source'
    acquired = value('offline-strict-acquire', ['--no-build', 'env', 'acquire', '--archive', unpacked / 'runtime.rcca', '--consume-archive-trust',
                     *trust, '--metadata-state', deployment / 'worker-state', '--json'], home=consumer, source=source, isolated=True)
    assert acquired['artifactDigest'] == artifact and acquired['verification']['metadataVersion'] == 2
    # Authenticate with RCC first; only then consume the exact receipt-bound assertion.
    consumer_carrier = Path(acquired['trustCarrierPath'])
    imported_metadata = consumer_carrier / 'sha256' / artifact.removeprefix('sha256:') / 'metadata.json'
    metadata_bytes = imported_metadata.read_bytes().strip()
    assert 'sha256:' + hashlib.sha256(metadata_bytes).hexdigest() == acquired['verification']['metadataDigest']
    metadata_payload = json.loads(base64.b64decode(json.loads(metadata_bytes)['payload'] + '==='))
    transported_manifest = unpacked / 'source-manifest.json'
    assert metadata_payload['provenance']['buildIdentity'] == 'sha256:' + sha256(transported_manifest)
    content_manifest = json.loads(transported_manifest.read_text())
    assert content_manifest['artifactDigest'] == artifact
    assert sha256(unpacked / 'workspace-haul.tar.zst') == content_manifest['workspaceHaulSHA256']
    actual_source = {str(path.relative_to(source)).replace('\\', '/'): sha256(path) for path in source.rglob('*') if path.is_file()}
    assert actual_source == content_manifest['files'], 'transported source bytes must match authenticated manifest before execution'
    task('offline-doctor', ['python', '-m', 'jat.cli', 'doctor', '--json'], source=source)
    _, observed = task('offline-inspect-content', ['python', '-m', 'jat.cli', 'inspect', '--haul', str(unpacked / 'workspace-haul.tar.zst'), '--json'], source=source)
    content_inventory = json.loads(observed)['inventory']
    assert content_inventory, 'Hauler content inventory must not be empty'
    probe = """import hashlib,importlib.metadata as m,json,os,pathlib,shutil,subprocess,sys
prefix=pathlib.Path(os.environ['CONDA_PREFIX']).resolve()
hauler=pathlib.Path(shutil.which('hauler')).resolve()
assert prefix in hauler.parents and prefix in pathlib.Path(sys.executable).resolve().parents
version=subprocess.check_output([str(hauler),'version'],text=True)
assert '2.1.1' in version
packages=sorted((d.metadata['Name'],d.version) for d in m.distributions())
pip=json.loads(subprocess.check_output([sys.executable,'-m','pip','list','--format=json','--disable-pip-version-check'],text=True))
normalize=lambda n:n.lower().replace('_','-').replace('.','-')
assert {(normalize(n),v) for n,v in packages}=={(normalize(d['name']),d['version']) for d in pip}
conda=[json.loads(p.read_text()) for p in (prefix/'conda-meta').glob('*.json')]
result={'kind':'observed-package-list-not-SBOM','python':sys.version,'pythonPath':sys.executable,'haulerPath':str(hauler),'haulerSHA256':hashlib.sha256(hauler.read_bytes()).hexdigest(),'haulerVersion':version,'pythonDistributions':packages,'condaPackages':sorted((p['name'],p['version']) for p in conda)}
pathlib.Path(sys.argv[1]).write_text(json.dumps(result,sort_keys=True))
print('JAT_ENVIRONMENT_HAULER_AND_PACKAGE_PROOF')
"""
    task('offline-hauler-package-observation', ['python', '-c', probe, str(work / 'package-observation.json')], source=source)
    destination = work / 'restored-workspace'
    task('offline-restore', ['python', '-m', 'jat.cli', 'restore', '--haul', str(unpacked / 'workspace-haul.tar.zst'), '--destination', str(destination), '--json'], source=source)
    restored = list(destination.rglob('proof.txt'))
    assert len(restored) == 1 and restored[0].read_text() == 'verified-portable-jat-capsule\n'
    renewed = authority / 'snapshot-2.json'
    value('renew-2', ['env', 'trust', 'revocations', 'renew', *issuer, '--metadata', first, '--sequence', '2', '--output', renewed, '--json'])
    consumer_carrier = Path(acquired['trustCarrierPath'])
    distribute('distribute-renewed', renewed, consumer_carrier)
    renewed_receipt, _ = task('renewed-doctor', ['python', '-m', 'jat.cli', 'doctor', '--json'], source=source)
    assert renewed_receipt['materializationId'] == acquired['materializationId']
    assert renewed_receipt['verification']['metadataSequence'] == 2
    started, release, finished = (work / name for name in ('child-started', 'child-release', 'child-finished'))
    child_code = "import pathlib,sys,time; a,b,c=map(pathlib.Path,sys.argv[1:]);a.write_text('started');\nwhile not b.exists(): time.sleep(.05)\nc.write_text('prior-lease-finished');print('PRIOR_LEASE_COMPLETED')"
    child_receipt = logs / 'running-child.receipt.json'
    child = subprocess.Popen([*offline, str(binary), '--no-build', 'env', 'exec', '--artifact', artifact, *map(str, trust), '--metadata-state', str(deployment / 'worker-state'), '--trust-carrier', str(consumer_carrier),
                              '--inherit-streams', '--receipt-file', str(child_receipt), '--', 'python', '-c', child_code, str(started), str(release), str(finished)],
                             cwd=source, env=environment(consumer, source), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('real leased child did not start')
            time.sleep(.05)
        revoked = authority / 'snapshot-3.json'
        value('revoke-3', ['env', 'trust', 'revocations', 'revoke', *issuer, '--metadata', renewed, '--sequence', '3', '--revoke-artifact',
                          '--reason', 'disposable acceptance fixture', '--output', revoked, '--json'])
        distribute('distribute-revoked', revoked, consumer_carrier)
        canary = work / 'forbidden-task-output'
        forbidden = ['python', '-c', "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('EXECUTED');print('FORBIDDEN_OUTPUT_ESCAPED')", str(canary)]

        def denied(label, code):
            _, stdout = task(label, forbidden, source=source, deny=code)
            assert not canary.exists() and 'FORBIDDEN_OUTPUT_ESCAPED' not in stdout

        denied('revoked-new-lease', 'revoked')
    finally:
        release.touch()
        try:
            stdout, stderr = child.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate()
        (logs / 'running-child.stdout').write_text(stdout)
        (logs / 'running-child.stderr').write_text(stderr)
    assert child.returncode == 0 and finished.read_text() == 'prior-lease-finished'
    prior = json.loads(child_receipt.read_text())
    assert prior['verification']['metadataSequence'] == 2
    target_metadata = consumer_carrier / 'sha256' / artifact.removeprefix('sha256:') / 'metadata.json'
    # Adversarial transport variants bypass the distributor, never the RCC verifier.
    shutil.copyfile(first, target_metadata)
    denied('rollback-new-lease', 'metadata-rollback')
    shutil.copyfile(revoked, target_metadata)
    envelope = json.loads(target_metadata.read_text())
    envelope['signature'] = ('A' if envelope['signature'][0] != 'A' else 'B') + envelope['signature'][1:]
    write_json(target_metadata, envelope)
    denied('tampered-new-lease', 'metadata-invalid')
    target_metadata.unlink()
    denied('missing-new-lease', 'metadata-unsigned')
    for label, issued, expires, key_id in (
        ('expired', now - timedelta(hours=2), now - timedelta(hours=1), 'authority-A'),
        ('future', now + timedelta(hours=1), now + timedelta(hours=2), 'authority-A'),
        ('unauthorized', now, now + timedelta(hours=1), 'unauthorized'),
    ):
        fixture = authority / f'{label}.json'
        run(f'sign-{label}-fixture', ['env', 'trust', 'sign-metadata', '--artifact', artifact, '--scope', roots['scope'], '--sequence', '4',
             '--metadata-key-id', key_id, '--metadata-signing-key', authority / f'{key_id}.key', '--issued-at', stamp(issued), '--expires-at', stamp(expires),
             '--provenance', reviewed_provenance, '--sbom', attachment_dir / 'sbom.json', '--revocations', authority / 'revocations.json', '--output', fixture])
        shutil.copyfile(fixture, target_metadata)
        denied(f'{label}-new-lease', 'metadata-authority' if label == 'unauthorized' else 'metadata-expired')
    shutil.copyfile(revoked, target_metadata)
    lease_files = list((consumer / 'rcc' / 'artifacts' / 'v1').glob('**/leases/*.json'))
    assert not lease_files, f'leases not cleaned: {lease_files}'
    inventory = json.loads((attachment_dir / 'sbom.json').read_text())
    observed_packages = json.loads((work / 'package-observation.json').read_text())
    go = shutil.which('go')
    if go:
        buildinfo = subprocess.run([go, 'version', '-m', observed_packages['haulerPath']], capture_output=True, text=True, check=True)
        modules = []
        for line in buildinfo.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[0] == 'dep':
                modules.append({'module': fields[1], 'version': fields[2], 'checksum': fields[3] if len(fields) > 3 else None})
        observed_packages['haulerGoModules'] = modules
        observed_packages['haulerGoModulesComplete'] = False  # Embedded build metadata is not a complete standard SBOM.
        write_json(work / 'package-observation.json', observed_packages)

    receipt = {'formatVersion': 1, 'operation': 'verified-portable-jat-capsule-acceptance', 'success': True,
               'acceptanceDriverSHA256': sha256(Path(__file__)), 'rccSource': args.rcc_source, 'rccBinarySHA256': args.rcc_sha256, 'inputArchiveSHA256': args.archive_sha256,
               'inputArtifactDigest': original['artifactDigest'], 'specificationDigest': published['specificationDigest'],
               'artifactDigest': artifact, 'signedArchiveSHA256': sha256(signed), 'capsuleSHA256': sha256(capsule),
               'jatSource': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               'platform': platform.platform(), 'network': args.network, 'ociManifestDigest': None, 'haulerContentInventory': content_inventory,
               'sequences': {'initial': 1, 'renewed': 2, 'revoked': 3, 'runningChild': prior['verification']['metadataSequence']},
               'packageSBOM': {'available': False, 'inventoryMediaType': inventory['mediaType'], 'limitation': 'RCC object inventory is not package CVE/license coverage; observed packages retained separately'},
               'limits': ['disposable test keys only', 'no power-loss or host-compromise claim', 'no macOS qualification'],
               'leasesRemaining': len(lease_files)}
    write_json(work / 'receipt.json', receipt)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
