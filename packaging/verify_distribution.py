"""Fail the build when required notices or replaceable library sources are missing."""
from pathlib import Path
import json
import sys
import zipfile


def verify_folder(folder):
    folder = Path(folder)
    required = (
        'LICENSE', 'ASSET_LICENSE.md', 'README.md', 'はじめに.txt', 'SOURCES.txt',
        'THIRD_PARTY_NOTICES.md', 'licenses/Python-LICENSE.txt',
        'licenses/Tcl-license.terms', 'licenses/Tk-license.terms',
        'licenses/PyInstaller-COPYING.txt', 'licenses/GPL-2.0.txt',
        'licenses/GPL-3.0.txt', 'licenses/LGPL-3.0.txt', '_internal/pyte/__init__.py',
    )
    for name in required:
        path = folder / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError('Required distribution file is missing: ' + name)
    for package in ('pyte', 'pywinpty', 'wcwidth', 'pywin32'):
        notices = [p for p in (folder / 'licenses' / package).rglob('*')
                   if p.is_file() and ('licen' in p.name.lower() or 'copying' in p.name.lower())]
        if not notices:
            raise RuntimeError('No bundled license found for ' + package)
    if (folder / 'tools' / 'git').exists():
        lock=json.loads((Path(__file__).parent/'source-lock.json').read_text(encoding='utf-8'))
        info=json.loads((folder/'build-info.json').read_text(encoding='utf-8'))
        if info['bundled_git']['sha256'] != lock['mingit_sha256']:
            raise RuntimeError('MinGit binary does not match the source-package manifest')
        versions=(folder/'tools/git/etc/package-versions.txt').read_text().splitlines()
        expected=sorted((row['package'],row['version']) for row in lock['binary_packages'])
        if sorted(tuple(line.split()) for line in versions if line.strip()) != expected:
            raise RuntimeError('MinGit package versions do not match the provided sources')
        for name in ('LICENSE.txt', 'etc/package-versions.txt', 'cmd/git.exe'):
            if not (folder / 'tools' / 'git' / name).is_file():
                raise RuntimeError('MinGit distribution is incomplete: ' + name)
    if (folder / 'settings.json').exists():
        raise RuntimeError('User settings must not be included in the distribution')
    return {'required_files': list(required), 'library_notices': True}


def verify_archive(archive):
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError('ZIP integrity check failed')
        names = set(zipped.namelist())
        required = ('GitTotte/LICENSE', 'GitTotte/ASSET_LICENSE.md', 'GitTotte/SOURCES.txt',
                    'GitTotte/はじめに.txt', 'GitTotte/_internal/pyte/__init__.py')
        if not set(required) <= names:
            raise RuntimeError('ZIP is missing required notices or library sources')
        manifest = json.loads(zipped.read('GitTotte/file-sha256.json'))
        import hashlib
        for name, expected in manifest.items():
            if hashlib.sha256(zipped.read('GitTotte/' + name)).hexdigest() != expected:
                raise RuntimeError('ZIP checksum mismatch: ' + name)
    return {'archive_integrity': True, 'file_checksums': True}


if __name__ == '__main__':
    target = Path(sys.argv[1])
    report = verify_folder(target) if target.is_dir() else verify_archive(target)
    print(json.dumps(report, ensure_ascii=False, indent=2))
