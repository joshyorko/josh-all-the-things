#!/usr/bin/env python3
"""Validate a downloaded receipt against its exact original release source."""

import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


def validate_receipt(source_root: Path, archive: Path, receipt_path: Path, source_sha: str, platform: str) -> dict:
    schema = json.loads((source_root / 'docs/environment-artifact-receipt.schema.json').read_text(encoding='utf-8-sig'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(receipt)
    rcc = json.loads((source_root / 'runtime/rcc.json').read_text(encoding='utf-8-sig'))
    hauler = json.loads((source_root / 'runtime/hauler.json').read_text(encoding='utf-8-sig'))
    pin = rcc['platforms'][platform]
    expected = {
        'jat_git_sha': source_sha, 'platform': platform,
        'rcc_version': rcc['version'], 'hauler_version': hauler['hauler']['version'],
        'rcc_source': {**rcc['source'], **{key: pin[key] for key in ('asset', 'sha256')}},
    }
    for key, value in expected.items():
        if receipt[key] != value:
            raise ValueError(f'receipt {key} does not match the release source')
    if not archive.is_file() or archive.is_symlink() or archive.stat().st_size != receipt['archive']['size']:
        raise ValueError('archive size or file type does not match the receipt')
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != receipt['archive']['sha256']:
        raise ValueError('archive hash does not match the receipt')
    # Release assets may be renamed during publication; validate bytes, not basename.
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--platform', choices=('linux_amd64', 'windows_amd64'), required=True)
    args = parser.parse_args(argv)
    try:
        receipt = validate_receipt(args.source_root, args.archive, args.receipt, args.source_sha, args.platform)
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        parser.exit(1, 'Invalid runtime receipt: producer contract, pinned identity, or archive verification failed.\n')
    print(json.dumps({'valid': True, 'source_sha': receipt['jat_git_sha'], 'platform': receipt['platform']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
