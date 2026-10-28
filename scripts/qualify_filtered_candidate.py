"""Run only from a freshly acquired candidate, with no checkout on sys.path."""

import hashlib
import json
import os
import sys
from pathlib import Path

import jat
from jat.models import WORKSPACE_CAPTURE_POLICY_VERSION, BuildRequest, RestoreRequest
from jat.services import JATService


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = Path(jat.__file__).resolve().parent
    prefix = Path(sys.prefix).resolve()
    require(prefix in package.parents, 'JAT did not load from acquired environment')
    identity = json.loads((package / '_candidate.json').read_text())
    require(identity['jat_git_sha'] == sys.argv[2], 'candidate source SHA does not match')
    require(all(digest(package / name) == expected for name, expected in identity['files'].items()),
            'embedded candidate source bytes do not match')
    require(WORKSPACE_CAPTURE_POLICY_VERSION == 1, 'unsupported workspace capture policy')
    require(not os.environ.get('PYTHONPATH'), 'external source overlay is forbidden')
    source = output / 'project'
    source.mkdir()
    selected = {'main.py': b'print("preserved")\n', 'package-lock.json': b'lock\n',
                '.env': b'config\n', 'uploads/data.bin': b'\x00user data', 'database.db': b'\x00database'}
    excluded = {'.git/HEAD': b'ref: refs/heads/main\n', 'worktree/.git': b'gitdir: /missing/original\n',
                'generated/cache': b'generated', 'build [literal]/output': b'build'}
    for name, content in {**selected, **excluded}.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before = {name: digest(source / name) for name in {**selected, **excluded}}
    service = JATService(producer_version=identity['jat_git_sha'])
    request = BuildRequest(folder=source, output=output / 'filtered.haul', capture_mode='source-only',
                           workspace_exclusions=['generated', 'build [literal]', 'worktree'])
    preview = service.build(request.model_copy(update={'dry_run': True}))
    require(preview.success and preview.complete, preview.diagnostics)
    built = service.build(request)
    require(built.success, built.diagnostics)
    require(before == {name: digest(source / name) for name in before}, 'candidate capture changed its source')
    source.rename(output / 'original-unavailable')
    destination = output / 'restored'
    restored = service.restore(RestoreRequest(haul=request.output, destination=destination))
    require(restored.success, restored.diagnostics)
    actual_root = destination / 'workspace' / 'project'
    actual = {path.relative_to(actual_root).as_posix(): path.read_bytes()
              for path in actual_root.rglob('*') if path.is_file()}
    require(actual == selected, 'filtered restore did not preserve exactly the selected files')
    unsafe = output / 'unsafe-source'
    unsafe.mkdir()
    unsafe_verification = 'passed'
    try:
        (unsafe / 'outside-link').symlink_to('/unavailable/external-target')
    except OSError:
        if os.name != 'nt':
            raise
        unsafe_verification = 'unrun: runner cannot create symbolic links'
    else:
        rejected = service.build(BuildRequest(folder=unsafe, output=output / 'unsafe.haul',
                                               capture_mode='source-only'))
        require(not rejected.success, 'retained unsafe link was accepted')
        require(not (output / 'unsafe.haul').exists(), 'unsafe capture left an output capsule')
    receipt = {
        'format_version': 1, 'success': True, 'jat_git_sha': identity['jat_git_sha'],
        'capture_policy_version': WORKSPACE_CAPTURE_POLICY_VERSION,
        'retained_unsafe_link': unsafe_verification,
        'embedded_source_verified': True, 'external_source_overlay': False,
        'original_source_unavailable': True, 'source_unchanged': True,
        'restored_files': {name: hashlib.sha256(content).hexdigest() for name, content in selected.items()},
        'capsule_sha256': digest(request.output), 'capsule_size': request.output.stat().st_size,
        'capture_policy': built.capture_policy,
    }
    (output / 'qualification.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
