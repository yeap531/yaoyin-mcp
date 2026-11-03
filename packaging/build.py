#!/usr/bin/env python3
"""构建原生 PyInstaller 目录与可分发压缩包。"""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def native_target():
    operating_system = {'darwin': 'macos', 'win32': 'windows', 'linux': 'linux'}.get(sys.platform)
    machine = platform.machine().lower()
    architecture = {'aarch64': 'arm64', 'arm64': 'arm64',
                    'amd64': 'x64', 'x86_64': 'x64'}.get(machine)
    if not operating_system or not architecture:
        raise SystemExit(f'Unsupported native platform: {sys.platform}/{machine}')
    return operating_system, architecture


def write_launchers(folder, operating_system, version):
    if operating_system == 'macos':
        contents = folder / '配置药引.app' / 'Contents'
        macos = contents / 'MacOS'
        macos.mkdir(parents=True)
        (contents / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'dev.yaoyin.setup', 'CFBundleName': '配置药引',
            'CFBundleDisplayName': '配置药引', 'CFBundlePackageType': 'APPL',
            'CFBundleExecutable': 'yaoyin-setup', 'CFBundleVersion': version,
            'CFBundleShortVersionString': version, 'LSUIElement': False,
        }))
        launcher = macos / 'yaoyin-setup'
        launcher.write_text('#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                            'exec "$HERE/../../../yaoyin" setup\n', encoding='utf-8')
        launcher.chmod(0o755)
    if operating_system != 'windows':
        launcher = folder / ('开始配置.command' if operating_system == 'macos' else '开始配置.sh')
        launcher.write_text('#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                            'exec "$HERE/yaoyin" setup\n', encoding='utf-8')
        launcher.chmod(0o755)


def copy_licenses(folder):
    licenses = folder / 'LICENSES'
    licenses.mkdir()
    for name in ('pyinstaller', 'pyinstaller-hooks-contrib', 'certifi', 'altgraph',
                 'packaging', 'setuptools', 'macholib', 'pefile', 'pywin32-ctypes', 'pywin32'):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        for file in distribution.files or []:
            if any(word in file.name.lower() for word in ('license', 'copying', 'copyright')):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = licenses / name / file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    for candidate in (Path(sys.base_prefix) / 'LICENSE.txt',
                      Path(sys.base_prefix) / 'LICENSE'):
        if candidate.is_file():
            shutil.copy2(candidate, licenses / 'Python-LICENSE.txt')
            break


def main():
    from runtime import VERSION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--expect-target', help='Fail if runner architecture differs, e.g. macos-arm64')
    args = parser.parse_args()
    operating_system, architecture = native_target()
    target = f'{operating_system}-{architecture}'
    if args.expect_target and target != args.expect_target:
        raise SystemExit(f'Expected {args.expect_target}, got {target}; refusing a mislabeled archive')
    if VERSION != '0.2.0':
        raise SystemExit('Packaging release version must be reviewed when runtime.VERSION changes')
    import tkinter
    import certifi
    if not Path(certifi.where()).is_file():
        raise SystemExit('CA bundle missing')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = f'yaoyin-{VERSION}-{target}'
    folder = output / name
    if folder.exists():
        raise SystemExit(f'Output already exists: {folder}; choose an empty --output directory')
    with tempfile.TemporaryDirectory(prefix='yaoyin-build-') as temporary:
        temporary = Path(temporary)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                        '--distpath', str(temporary / 'dist'), '--workpath', str(temporary / 'work'),
                        str(ROOT / 'packaging' / 'yaoyin.spec')], cwd=ROOT, check=True)
        shutil.copytree(temporary / 'dist' / 'yaoyin', folder, symlinks=True)
    shutil.copy2(ROOT / 'LICENSE', folder / 'LICENSE')
    text = (ROOT / 'packaging' / 'README-download.txt').read_text(encoding='utf-8')
    for key, value in (('@VERSION@', VERSION), ('@PLATFORM@', operating_system), ('@ARCH@', architecture)):
        text = text.replace(key, value)
    (folder / 'README.txt').write_text(text, encoding='utf-8')
    write_launchers(folder, operating_system, VERSION)
    copy_licenses(folder)
    (folder / 'BUILD-INFO.json').write_text(json.dumps({
        'version': VERSION, 'target': target, 'python': platform.python_version(),
        'pyinstaller': importlib.metadata.version('pyinstaller'), 'tk': tkinter.TkVersion,
        'certifi': importlib.metadata.version('certifi'),
    }, indent=2) + '\n', encoding='utf-8')
    if operating_system == 'windows':
        archive = output / f'{name}.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
            for item in sorted(folder.rglob('*')):
                stream.write(item, item.relative_to(output))
    else:
        archive = output / f'{name}.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(folder, arcname=name)
    print(archive)


if __name__ == '__main__':
    main()
