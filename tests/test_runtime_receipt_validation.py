import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError


@pytest.fixture
def release_pair(tmp_path):
    root = Path(__file__).parents[1]
    source = tmp_path / 'release-source'
    (source / 'runtime').mkdir(parents=True)
    (source / 'docs').mkdir()
    for name in ('rcc.json', 'hauler.json'):
        (source / 'runtime' / name).write_bytes((root / 'runtime' / name).read_bytes())
    schema = json.loads((root / 'docs/environment-artifact-receipt.schema.json').read_text())
    (source / 'docs/environment-artifact-receipt.schema.json').write_text(json.dumps(schema))
    rcc = json.loads((source / 'runtime/rcc.json').read_text())
    hauler = json.loads((source / 'runtime/hauler.json').read_text())
    archive = tmp_path / 'renamed-linux-download.rcca'
    archive.write_bytes(b'synthetic verified archive bytes')
    record = {
        'format_version': 3, 'operation': 'build', 'success': True,
        'jat_git_sha': 'a' * 40, 'rcc_executable': '/producer/rcc',
        'rcc_version': rcc['version'], 'hauler_version': hauler['hauler']['version'],
        'rcc_source': {**rcc['source'], **{k: rcc['platforms']['linux_amd64'][k] for k in ('asset', 'sha256')}},
        'platform': 'linux_amd64', 'artifact_digest': 'sha256:' + 'b' * 64,
        'specification_digest': 'sha256:' + 'c' * 64, 'legacy_blueprint_key': 'd' * 16,
        'archive': {'filename': 'original-producer.rcca', 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'size': archive.stat().st_size},
        'verified_acquire': {'fresh_home': True, 'no_build': True},
        'verified_no_build': {'fresh_home': True, 'no_build': True},
        'verified_exec': {'fresh_home': True},
        'verified_warm_reuse': {'fresh_home': True, 'no_build': True, 'provider_unavailable': True},
        'verified_hauler': {'fresh_home': True, 'command': ['hauler', 'version'],
                            'launcher': schema['properties']['verified_hauler']['properties']['launcher']['const'],
                            'resolved_under_conda_prefix': True, 'exit_code': 0},
    }
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps(record))
    return source, archive, receipt, record


def validate(pair):
    from scripts.validate_runtime_receipt import validate_receipt
    source, archive, receipt, _ = pair
    return validate_receipt(source, archive, receipt, 'a' * 40, 'linux_amd64')


def test_valid_receipt_accepts_renamed_release_asset(release_pair):
    assert validate(release_pair)['success'] is True


@pytest.mark.parametrize('field,value', [
    ('format_version', 2), ('success', False), ('verified_warm_reuse', None),
    ('verified_no_build.no_build', False), ('verified_hauler.exit_code', 1),
    ('jat_git_sha', 'e' * 40), ('platform', 'windows_amd64'),
    ('rcc_source.asset', 'rcc-windows64.exe'), ('rcc_source.sha256', 'e' * 64),
    ('archive.sha256', 'e' * 64), ('archive.size', 999),
])
def test_invalid_producer_evidence_is_rejected_before_acquire(release_pair, field, value):
    _, _, receipt, original = release_pair
    record = copy.deepcopy(original)
    target = record
    parts = field.split('.')
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    receipt.write_text(json.dumps(record))
    with pytest.raises((ValueError, ValidationError)):
        validate(release_pair)


def test_legacy_launcher_uses_its_own_source_schema(release_pair):
    source, _, receipt, record = release_pair
    schema_path = source / 'docs/environment-artifact-receipt.schema.json'
    schema = json.loads(schema_path.read_text())
    legacy = ['python', '-c', 'legacy prefix ownership proof']
    schema['properties']['verified_hauler']['properties']['launcher']['const'] = legacy
    schema_path.write_text(json.dumps(schema))
    record['verified_hauler']['launcher'] = legacy
    receipt.write_text(json.dumps(record))
    assert validate(release_pair)['verified_hauler']['launcher'] == legacy


def test_archive_corruption_is_rejected(release_pair):
    release_pair[1].write_bytes(b'changed archive')
    with pytest.raises(ValueError, match='archive'):
        validate(release_pair)


def test_utf8_bom_windows_receipt_is_accepted(release_pair):
    _, _, receipt, record = release_pair
    receipt.write_text(json.dumps(record), encoding='utf-8-sig')
    assert validate(release_pair)['success'] is True
