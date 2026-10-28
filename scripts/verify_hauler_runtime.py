#!/usr/bin/env python3
"""Check exact Hauler release and Python/executable ownership after acquisition."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def verify_owned_hauler(expected_version: str) -> dict:
    prefix = os.environ.get('CONDA_PREFIX')
    executable = shutil.which('hauler')
    if not prefix or not executable:
        raise ValueError('Hauler and Python must belong to the acquired environment')
    root = Path(prefix).resolve()
    hauler = Path(executable).resolve()
    python = Path(sys.executable).resolve()
    if not hauler.is_relative_to(root) or not python.is_relative_to(root):
        raise ValueError('Hauler and Python must belong to the acquired environment')
    result = subprocess.run([str(hauler), 'version'], capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise ValueError('Hauler version command failed')
    fields = [line.partition(':')[2].strip() for line in (result.stdout + '\n' + result.stderr).splitlines() if line.lstrip().startswith('GitVersion:')]
    expected = expected_version.removeprefix('v')
    if len(fields) != 1 or re.fullmatch(r'v?\d+\.\d+\.\d+', fields[0]) is None or fields[0].removeprefix('v') != expected:
        raise ValueError('Hauler version does not match the pinned release')
    return {'version': fields[0].removeprefix('v'), 'hauler': str(hauler), 'python': str(python), 'conda_prefix': str(root)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-version', required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_owned_hauler(args.expected_version)
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, 'Pinned Hauler runtime verification failed.\n')
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
