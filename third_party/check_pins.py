#!/usr/bin/env python3
"""Check the explicit dependency lock against gitlinks and initialized checkouts."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent


def git(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments], stderr=subprocess.PIPE).decode().strip()


def check(root=ROOT, only=None, allow_uninitialized=False):
    root = Path(root).resolve()
    pins = json.loads((root / 'third_party/versions.json').read_text(encoding='utf-8'))
    if not isinstance(pins, dict) or any(
            not re.fullmatch(r'[a-zA-Z0-9_-]+', name)
            or not isinstance(commit, str)
            or not re.fullmatch(r'[0-9a-f]{40}', commit)
            for name, commit in pins.items()):
        raise ValueError('third_party/versions.json must map dependency names to full lowercase commit SHAs')
    indexed = {}
    for line in git(root, 'ls-files', '--stage', 'third_party').splitlines():
        mode, commit, stage, path = line.split(None, 3)
        if mode == '160000':
            if stage != '0':
                raise ValueError('unmerged submodule: ' + path)
            indexed[path] = commit
    expected = {'third_party/' + name: commit for name, commit in pins.items()}
    if indexed != expected:
        raise ValueError('third_party/versions.json must match every indexed submodule commit exactly')
    selected = list(pins) if only is None else only
    for name in selected:
        if name not in pins:
            raise ValueError('unlocked submodule: ' + name)
        path = root / 'third_party' / name
        if not (path / '.git').exists():
            if allow_uninitialized:
                continue
            raise ValueError('initialize pinned dependencies: git submodule update --init --recursive')
        if git(path, 'rev-parse', '--show-toplevel') != str(path.resolve()):
            raise ValueError('not a standalone submodule checkout: ' + name)
        if git(path, 'rev-parse', 'HEAD') != pins[name]:
            raise ValueError(name + ' HEAD differs from third_party/versions.json; do not update with --remote')
        if git(path, 'status', '--porcelain', '--untracked-files=no'):
            raise ValueError('tracked submodule sources are modified: ' + name)
    return {name: pins[name] for name in selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+')
    parser.add_argument('--allow-uninitialized', action='store_true')
    arguments = parser.parse_args()
    try:
        for name, commit in check(only=arguments.only, allow_uninitialized=arguments.allow_uninitialized).items():
            print(name + ' ' + commit)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Dependency pin check failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
