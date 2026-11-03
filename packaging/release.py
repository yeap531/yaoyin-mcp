#!/usr/bin/env python3
"""发布前拒绝不完整制品集合并生成校验文件。"""
import argparse
import hashlib
from pathlib import Path
import re

VERSION = '0.2.0'
TARGETS = ('macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64', 'linux-arm64')


def prepare(directory, tag):
    if tag != f'v{VERSION}':
        raise SystemExit(f'Tag {tag!r} does not match packaged version v{VERSION}')
    expected = {f'yaoyin-{VERSION}-{target}.' + ('zip' if target.startswith('windows') else 'tar.gz')
                for target in TARGETS}
    archives = {path.name: path for path in directory.iterdir()
                if path.is_file() and (path.name.endswith('.zip') or path.name.endswith('.tar.gz'))}
    if set(archives) != expected:
        raise SystemExit(f'Incomplete release: missing={sorted(expected - archives.keys())}; '
                         f'unexpected={sorted(archives.keys() - expected)}')
    lines = []
    for name in sorted(expected):
        path = archives[name]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not path.stat().st_size:
            raise SystemExit(f'Invalid archive: {name}')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        lines.append(f'{digest.hexdigest()}  {name}\n')
    (directory / 'SHA256SUMS').write_text(''.join(lines), encoding='ascii')
    print(f'Validated {len(lines)} native packages; SHA256SUMS written')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--tag', required=True)
    arguments = parser.parse_args()
    prepare(arguments.directory, arguments.tag)
