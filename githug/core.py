"""Non-destructive repository browsing and snapshot creation."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime
from uuid import uuid4
from .platform_support import git_executable

class ComparisonCancelled(Exception):
    pass

class GitError(RuntimeError):
    pass

def git(*args, cwd=None, env=None, input_text=None, allowed_returncodes=(0,)):
    # Git commands run without interactive input. In a Windows GUI the
    # inherited stdin handle can be invalid after the embedded console closes.
    stdin_options = ({'stdin': subprocess.DEVNULL} if input_text is None
                     else {'input': input_text.encode('utf-8')})
    try:
        result = subprocess.run([git_executable(), *map(str, args)], cwd=cwd, capture_output=True,
                                timeout=180, **stdin_options,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', **(env or {})},
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except FileNotFoundError as exc:
        raise GitError('Gitが見つかりません。Git for Windowsをインストールしてください。') from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError('通信が時間内に終わりませんでした。接続を確認して再度お試しください。') from exc
    stdout = result.stdout.decode('utf-8', errors='replace').replace('\r\n', '\n')
    stderr = result.stderr.decode('utf-8', errors='replace').replace('\r\n', '\n')
    if result.returncode not in allowed_returncodes:
        raise GitError(stderr.strip() or 'Gitの操作を完了できませんでした。')
    return stdout

def validate_url(url):
    match = re.fullmatch(r'https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?', url.strip())
    if not match:
        raise ValueError('https://github.com/所有者/プロジェクト の形式で入力してください。')
    owner, name = match.groups()
    if owner in ('.', '..') or name in ('.', '..'):
        raise ValueError('プロジェクト名が正しくありません。')
    return f'https://github.com/{owner}/{name}.git', f'{owner}__{name}'

class Project:
    def __init__(self, root, url, base=None):
        self.url, self.name = validate_url(url)
        self.base = Path(base) if base else Path(root) / self.name
        self.cache = self.base / 'history.git'
        self.other_bases = ()

    @classmethod
    def registered(cls, root, registration):
        return cls(root, registration['url'], registration.get('base'))

    def refresh(self):
        self.base.mkdir(parents=True, exist_ok=True)
        if not self.cache.exists():
            # Clone into a temporary directory: interrupted downloads cannot become a valid cache.
            temporary = self.base / ('download-' + uuid4().hex)
            try:
                git('clone', '--mirror', self.url, temporary)
                temporary.rename(self.cache)
            except Exception as error:
                try:
                    if temporary.exists():
                        from .updating import remove_temporary
                        remove_temporary(temporary)
                except OSError as cleanup_error:
                    raise GitError(
                        f'{error}\n\n一時フォルダーを削除できませんでした: {temporary}\n'
                        f'{cleanup_error}'
                    ) from error
                raise
        else:
            git('fetch', '--prune', 'origin', cwd=self.cache)
        return self.history()

    def history(self):
        raw = git('log', '--all', '--topo-order', '--date=iso-strict', '--pretty=format:%H%x09%P%x09%aI%x09%D%x09%s', cwd=self.cache)
        return [dict(zip(('sha', 'parents', 'date', 'refs', 'message'), line.split('\t', 4)))
                for line in raw.splitlines() if line]

    def snapshot(self, sha):
        if not re.fullmatch(r'[0-9a-f]{40,64}', sha):
            raise ValueError('履歴の識別番号が正しくありません。')
        git('cat-file', '-e', sha + '^{commit}', cwd=self.cache)
        folder = self.base / 'snapshots' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + sha[:8] + '_' + uuid4().hex[:6])
        folder.parent.mkdir(parents=True, exist_ok=True)
        # Independent clone; no checkout/reset/pull ever runs in the user's work folder.
        git('clone', '--no-hardlinks', '--no-checkout', self.cache, folder)
        git('checkout', '--detach', sha, cwd=folder)
        # Replace local-cache remote with the real origin for future editing workflows.
        git('remote', 'set-url', 'origin', self.url, cwd=folder)
        record = {'folder': str(folder), 'sha': sha, 'created': datetime.now().isoformat(), 'kind': 'snapshot'}
        metadata = self.base / 'records'
        metadata.mkdir(exist_ok=True)
        (metadata / (folder.name + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return str(folder)

    def folder_versions(self):
        return [json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.base / 'records').glob('*.json'), reverse=True)]

    def snapshots(self):
        return [record for record in self.folder_versions() if record.get('kind') != 'work' and not record.get('hidden_from_list',False)]

    def compare(self, sha, folder, progress=None, cancelled=None):
        """Compare tracked commit contents with a folder without modifying that folder."""
        folder = Path(folder).resolve()
        if not folder.is_dir():
            raise ValueError('比較するフォルダーが見つかりません。')
        def report(stage, count, total, name):
            if cancelled and cancelled():
                raise ComparisonCancelled()
            if progress:
                progress(stage, count, total, name)
            if cancelled and cancelled():
                raise ComparisonCancelled()
        report('履歴のファイル一覧を取得', 0, None, '')
        entries = list(filter(None, git('ls-tree', '-r', '-z', sha, cwd=self.cache).split('\0')))
        # A fresh index supplies the selected revision's attributes when the
        # folder lacks them. Git reads working files without touching their index.
        config_args = []
        if (folder / '.git').exists():
            settings = git('config', '--null', '--get-regexp',
                           r'^(core\.(autocrlf|eol|attributesfile)|filter\..*\.(clean|process|required))$',
                           cwd=folder, allowed_returncodes=(0, 1))
            for setting in filter(None, settings.split('\0')):
                name, separator, value = setting.partition('\n')
                config_args.extend(('-c', name + '=' + (value if separator else 'true')))
        with tempfile.TemporaryDirectory(prefix='gittotte-compare-') as temporary:
            read_env = {'GIT_DIR': str(self.cache.resolve()), 'GIT_WORK_TREE': str(folder),
                        'GIT_INDEX_FILE': str(Path(temporary) / 'index'), 'GIT_OPTIONAL_LOCKS': '0'}
            git('read-tree', sha, cwd=self.cache, env=read_env)
            report('履歴と内容を一括比較', 0, len(entries), '')
            changed = set(filter(None, git(*config_args, '-c', 'core.filemode=false',
                          'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                          '--ignore-submodules=all', '--name-only', '-z', sha, '--',
                          cwd=folder, env=read_env).split('\0')))
        differences = []
        known = set()
        for index, entry in enumerate(entries):
            header, name = entry.split('\t', 1)
            mode, kind, blob = header.split()
            report('履歴と内容を比較', index, len(entries), name)
            known.add(name)
            path = folder / name
            if kind != 'blob' or mode == '120000':
                differences.append(('要確認（リンク／外部参照）', name))
            elif not path.exists():
                differences.append(('手元にない', name))
            elif path.is_symlink() or not path.is_file():
                differences.append(('種類が異なる', name))
            elif name in changed:
                differences.append(('内容が異なる', name))
        report('履歴と内容を比較', len(entries), len(entries), '')
        scanned = 0
        for parent, dirs, files in os.walk(folder, followlinks=False):
            report('手元だけのファイルを確認', scanned, None, str(Path(parent).relative_to(folder)))
            dirs[:] = [d for d in dirs if d != '.git' and not (Path(parent) / d).is_symlink()]
            for name in files:
                relative = (Path(parent) / name).relative_to(folder).as_posix()
                scanned += 1
                report('手元だけのファイルを確認', scanned, None, relative)
                if relative not in known:
                    differences.append(('手元のみ', relative))
        report('比較完了', scanned, None, '')
        return differences
