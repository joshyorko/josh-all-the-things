"""Recover resolved freeze evidence from an existing qualified artifact, without builds."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build_environment_artifact import _environment
from build_filtered_candidate import prepare_candidate_descriptors

SOURCE = 'fe43065a654d1649ba9ebe5a6f202eac612e4385'
ARCHIVE_SHA = '8bbf7aa8925b5a517164e7e6ea00b735394cc1eac026ba34404e729d89e441b1'
root = Path(__file__).resolve().parents[1]
archive_dir = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()
work.mkdir(parents=True, exist_ok=False)
archive = archive_dir / 'jat-filtered-candidate.rcca'
promotion = json.loads((archive_dir / 'promotion-receipt.json').read_text())
assert promotion['success'] is True and promotion['format_version'] == 3
assert promotion['jat_git_sha'] == SOURCE and promotion['platform'] == 'windows_amd64'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA == promotion['archive']['sha256']
assert archive.stat().st_size == 104094478 == promotion['archive']['size']
pins = json.loads(subprocess.check_output(['git', 'show', SOURCE + ':runtime/rcc.json'], cwd=root))
pin = pins['platforms']['windows_amd64']
rcc = Path(os.environ['CANDIDATE_RCC']).resolve()
assert hashlib.sha256(rcc.read_bytes()).hexdigest() == pin['sha256']
home = work / 'home'
home.mkdir()
env = _environment(dict(os.environ), home, work / 'rcc')
env.pop('PYTHONPATH', None)
env.pop('PYTHONHOME', None)
descriptors = work / 'descriptors'
descriptors.mkdir()
for name in ['robot.yaml', 'conda.yaml', 'environment_windows_amd64.yaml', 'environment_linux_amd64_freeze.yaml']:
    (descriptors / name).write_bytes(subprocess.check_output(['git', 'show', SOURCE + ':' + name], cwd=root))
prepare_candidate_descriptors(descriptors, SOURCE)
def run(label, args):
    result = subprocess.run([str(rcc), *args], cwd=descriptors, env=env, capture_output=True, text=True, timeout=900)
    (work / (label + '.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'{label} failed: {result.stdout[-3000:]} {result.stderr[-3000:]}')
    return result.stdout
acquired = json.loads(run('acquire', ['env', 'acquire', '--archive', str(archive), '--permissive-local', '--json']))
assert acquired['artifactDigest'] == promotion['artifact_digest'] and acquired['verification']['valid']
run('variables', ['--no-build', 'ht', 'vars', '--robot', str(descriptors / 'robot.yaml'), '--json'])
run('help', ['--no-build', 'task', 'run', '--robot', str(descriptors / 'robot.yaml'), '--task', 'JAT', '--', '--help'])
execution = work / 'embedded-exec.json'
run('embedded', ['--no-build', 'env', 'exec', '--artifact', promotion['artifact_digest'], '--permissive-local', '--receipt-file', str(execution), '--inherit-streams', '--', 'python', '-c', "import json,sys,pathlib,jat; p=pathlib.Path(jat.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()); assert json.loads(p.with_name('_candidate.json').read_text())['jat_git_sha']=='" + SOURCE + "'"])
assert json.loads(execution.read_text())['exitCode'] == 0
freeze = descriptors / 'output' / 'environment_windows_amd64_freeze.yaml'
assert freeze.is_file()
text = freeze.read_text()
assert 'python scripts/install_filtered_candidate.py ' + SOURCE in text
assert 'python scripts/install_hauler.py' in text
# Native RCC freeze must resolve the originally unpinned pip dependency.
assert any(line.strip().startswith('- pip=') for line in text.splitlines())
target = work / freeze.name
shutil.copyfile(freeze, target)
assert hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA
receipt = {'format_version': 1, 'success': True, 'operation': 'recover-existing-artifact-freeze', 'proof_driver_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(), 'jat_git_sha': SOURCE, 'artifact_digest': promotion['artifact_digest'], 'specification_digest': promotion['specification_digest'], 'archive': promotion['archive'], 'rcc_version': pins['version'], 'no_build': True, 'external_source_overlay': False, 'freeze': {'filename': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'size': target.stat().st_size}}
(work / 'freeze-recovery-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
