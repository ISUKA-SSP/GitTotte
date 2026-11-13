"""Build a portable Windows folder and ZIP. Never embed local user settings."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from githug import __version__
from githug.branding import icon_bytes

GIT_VERSION='2.56.0.4'
GIT_URL='https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip'
GIT_SHA256='e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877'


def fetch_git(cache):
    archive=cache/('MinGit-'+GIT_VERSION+'-64-bit.zip')
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest()!=GIT_SHA256:
        request=urllib.request.Request(GIT_URL,headers={'User-Agent':'GitTotte-build'})
        with urllib.request.urlopen(request,timeout=120) as response, archive.open('wb') as output:
            shutil.copyfileobj(response,output)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=GIT_SHA256:
        archive.unlink(missing_ok=True)
        raise RuntimeError('MinGitのSHA-256が上流の公開値と一致しません。')
    return archive


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--without-git',action='store_true')
    args=parser.parse_args()
    if os.name!='nt':raise SystemExit('Windows上で実行してください。GitHub Actionsも利用できます。')
    cache=ROOT/'build'/'packaging';cache.mkdir(parents=True,exist_ok=True)
    icon=cache/'GitTotte.ico';icon.write_bytes(icon_bytes())
    # Keep the LGPL source replaceable without generated caches.
    pyte_source=cache/'pyte'
    if pyte_source.exists():shutil.rmtree(pyte_source)
    shutil.copytree(Path(importlib.metadata.distribution('pyte').locate_file('pyte')),pyte_source,
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    numbers=tuple(int(value or 0) for value in re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:a(\d+))?',__version__).groups())
    version_file=cache/'version.txt'
    version_file.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers="+repr(numbers)+", prodvers="+repr(numbers)+", mask=0x3f, flags="+str(2 if 'a' in __version__ else 0)+", OS=0x40004, fileType=0x1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName','ISUKA-SSP'), StringStruct('FileDescription','GitTotte'), StringStruct('FileVersion',"+repr(__version__)+"), StringStruct('ProductName','GitTotte'), StringStruct('ProductVersion',"+repr(__version__)+"), StringStruct('OriginalFilename','GitTotte.exe')])]), VarFileInfo([VarStruct('Translation',[1033,1200])])])",encoding='utf-8')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
                    '--add-data',str(ROOT/'githug'/'assets')+os.pathsep+'githug/assets',
                    '--collect-all','winpty','--hidden-import','win32com.shell.shell','--hidden-import','win32com.server.policy','--collect-all','wcwidth','--exclude-module','pyte',
                    '--add-data',str(pyte_source)+os.pathsep+'pyte',
                    '--version-file',str(version_file),'--noupx','--name','GitTotte','--icon',str(icon),'--distpath',str(ROOT/'dist'),
                    '--workpath',str(ROOT/'build'/'pyinstaller'),'--specpath',str(cache),
                    str(ROOT/'run_githug.py')],cwd=ROOT,check=True)
    folder=ROOT/'dist'/'GitTotte'
    shutil.rmtree(folder/'_internal'/'winpty'/'tests',ignore_errors=True)
    if not args.without_git:
        archive=fetch_git(cache)
        target=folder/'tools'/'git';target.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                destination=(target/entry.filename).resolve()
                if destination!=target.resolve() and target.resolve() not in destination.parents:
                    raise ValueError('MinGit内に不正なパスがあります。')
            zipped.extractall(target)
        if not (target/'cmd'/'git.exe').is_file():raise RuntimeError('MinGitのgit.exeが見つかりません。')
    shutil.copy2(ROOT/'README.md',folder/'README.md')
    shutil.copytree(ROOT/'docs',folder/'docs',dirs_exist_ok=True,ignore=shutil.ignore_patterns('changes','releases'))
    shutil.copy2(ROOT/'packaging'/'FIRST_START.txt',folder/'はじめに.txt')
    shutil.copy2(ROOT/'packaging'/'THIRD_PARTY_NOTICES.md',folder/'THIRD_PARTY_NOTICES.md')
    for name in ('LICENSE','ASSET_LICENSE.md'):
        shutil.copy2(ROOT/name,folder/name)
    shutil.copy2(ROOT/'packaging'/'SOURCES.txt',folder/'SOURCES.txt')
    licenses=folder/'licenses';licenses.mkdir(exist_ok=True)
    shutil.copytree(ROOT/'packaging'/'licenses',licenses,dirs_exist_ok=True)
    for path,name in [(Path(sys.base_prefix)/'LICENSE.txt','Python-LICENSE.txt'),
                      (Path(sys.base_prefix)/'tcl'/'tcl8.6'/'license.terms','Tcl-license.terms'),
                      (Path(sys.base_prefix)/'tcl'/'tk8.6'/'license.terms','Tk-license.terms')]:
        if path.is_file():shutil.copy2(path,licenses/name)
    distribution=importlib.metadata.distribution('pyinstaller')
    for path in distribution.files or []:
        if path.name in ('COPYING.txt','LICENSE','LICENSE.txt'):
            source=distribution.locate_file(path)
            if source.is_file():shutil.copy2(source,licenses/('PyInstaller-'+path.name))
    for package in ('pywinpty','pyte','wcwidth','pywin32'):
        dist=importlib.metadata.distribution(package)
        target=licenses/package;target.mkdir(exist_ok=True)
        for entry in dist.files or []:
            if 'license' in str(entry).lower() or entry.name.lower().startswith(('copying','authors','notice')):
                source=dist.locate_file(entry)
                if source.is_file():
                    destination=target/str(entry).replace('..','_')
                    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    # pyte stays as replaceable Python source under _internal/pyte, outside the frozen archive.
    info={'version':__version__,'architecture':'Windows x64','python':sys.version,
          'pyinstaller':importlib.metadata.version('pyinstaller'),
          'source_commit':os.environ.get('GITHUB_SHA','local-build'),
          'console_components':{name:importlib.metadata.version(name) for name in ('pywinpty','pyte','wcwidth','pywin32')},
          'bundled_git':None if args.without_git else {'version':GIT_VERSION,'url':GIT_URL,'sha256':GIT_SHA256}}
    provenance=ROOT/'build'/'bootloader-build.json'
    info['bootloader']=json.loads(provenance.read_text(encoding='utf-8')) if provenance.exists() else {'compile_bootloader':False}
    (folder/'build-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    import pefile
    pe=pefile.PE(str(folder/'GitTotte.exe'))
    strings={}
    for group in getattr(pe,'FileInfo',[]):
        for item in group:
            for table in getattr(item,'StringTable',[]):
                strings.update({key.decode():value.decode() for key,value in table.entries.items()})
    if strings.get('ProductName')!='GitTotte' or strings.get('ProductVersion')!=__version__:
        raise RuntimeError('Windows product metadata mismatch')
    info['windows_version_info']=strings
    info['authenticode_present']=bool(pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size)
    (folder/'build-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    from verify_distribution import verify_folder
    verify_folder(folder)
    manifest={str(path.relative_to(folder)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(folder.rglob('*')) if path.is_file()}
    (folder/'file-sha256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    archive=ROOT/'dist'/f'GitTotte-{__version__}-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for path in sorted(folder.rglob('*')):
            if path.is_file():zipped.write(path,Path('GitTotte')/path.relative_to(folder))
    (archive.with_suffix('.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='ascii')
    print('Built:',archive)

if __name__=='__main__':main()
