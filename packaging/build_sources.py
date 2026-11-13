"""Collect hash-pinned original source packages alongside the application release."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from githug import __version__


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    lock_path = ROOT / 'packaging' / 'source-lock.json'
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    cache = ROOT / 'build' / 'source-packages'
    cache.mkdir(parents=True, exist_ok=True)
    def fetch(item):
        name = item['name']
        if Path(name).name != name:
            raise ValueError('Invalid source archive name')
        path = cache / name
        if not path.exists() or digest(path) != item['sha256']:
            request = urllib.request.Request(item['url'], headers={'User-Agent': 'GitTotte-source-build'})
            temporary = path.with_name(path.name + '.download')
            try:
                with urllib.request.urlopen(request, timeout=180) as response, temporary.open('wb') as output:
                    shutil.copyfileobj(response, output)
                if digest(temporary) != item['sha256']:
                    raise RuntimeError('Source archive hash mismatch: ' + name)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        print('Verified source:', name, flush=True)
        return path
    with ThreadPoolExecutor(max_workers=4) as pool:
        packages = list(pool.map(fetch, lock['packages']))
    expected = {row['name'] for row in lock['binary_packages']}
    if not expected <= {p.name for p in packages}:
        raise RuntimeError('MinGit package-to-source mapping is incomplete')
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    archive = dist / f'GitTotte-{__version__}-ThirdPartySources.zip'
    # These upstream packages are already compressed, so preserve their exact bytes.
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED, allowZip64=True) as zipped:
        zipped.write(lock_path, 'sources/source-lock.json')
        zipped.write(ROOT / 'packaging' / 'SOURCES.txt', 'sources/README.txt')
        zipped.write(ROOT / 'packaging' / 'THIRD_PARTY_NOTICES.md', 'sources/THIRD_PARTY_NOTICES.md')
        for package in packages:
            zipped.write(package, 'sources/packages/' + package.name)
    archive.with_suffix('.sha256').write_text(digest(archive) + '  ' + archive.name + '\n', encoding='ascii')
    print('Built:', archive, archive.stat().st_size, flush=True)


if __name__ == '__main__':
    main()
