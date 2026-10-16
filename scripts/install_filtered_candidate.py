"""Embed exact JAT candidate source into a dedicated RCC environment only."""

import hashlib
import json
import shutil
import sys
import sysconfig
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    source_sha = sys.argv[1]
    if len(source_sha) != 40 or any(char not in '0123456789abcdef' for char in source_sha):
        raise ValueError('candidate source must be a full git SHA')
    target = Path(sysconfig.get_path('purelib')) / 'jat'
    if target.exists():
        raise FileExistsError('candidate package already installed')
    shutil.copytree(root / 'src' / 'jat', target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(root / 'tasks.py', target / 'candidate_tasks.py')
    files = {path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(target.rglob('*.py'))}
    (target / '_candidate.json').write_text(json.dumps({
        'format_version': 1, 'jat_git_sha': source_sha, 'files': files,
    }, sort_keys=True, indent=2) + '\n')


if __name__ == '__main__':
    main()
