"""Reviewable commits in isolated clones; remote writes use explicit ref leases."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
from copy import copy
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4
from .core import git, GitError
from .file_policy import sensitive_path
from .updating import atomic_json, record_for_folder, remove_temporary, UpdateConflict

SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.cache'}


class RemoteChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    path: str
    kind: str
    fingerprint: str | None

    @property
    def initially_selected(self):
        return self.kind in ('M', 'A') and not sensitive_path(self.path)


@dataclass
class PushPlan:
    folder: Path
    base: str
    changes: list
    branches: dict
    author_name: str
    author_email: str
    repository: Path | None = None
    source_stamp: dict | None = None


def choose_files(changes, mode):
    if mode == 'all': return {change.path for change in changes}
    if mode == 'none': return set()
    if mode == 'default': return {change.path for change in changes if change.initially_selected}
    raise ValueError('選択方法が正しくありません。')


def selection_counts(changes, selected):
    result = {'M': 0, 'A': 0, 'D': 0}
    for change in changes:
        if change.path in selected: result[change.kind] += 1
    return result


def safe_path(folder, relative):
    parts = relative.split('/')
    if not parts or any(p in ('', '.', '..') or p.casefold() == '.git' for p in parts) or '\\' in relative:
        raise ValueError('送信ファイルの保存場所が正しくありません。')
    path = folder.joinpath(*parts)
    for component in (path, *list(path.parents)[:len(parts)-1]):
        if component.is_symlink() or (component.exists() and getattr(component.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('リンクを含むファイルは送信できません: ' + relative)
    return path


def digest(path):
    if not path.exists(): return None
    if not path.is_file(): raise ValueError('通常のファイルではありません: ' + str(path))
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024): sha.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise UpdateConflict('読み取り中にファイルが変わりました。確認し直してください。')
    return sha.hexdigest()


def tracked(project, base):
    entries = git('ls-tree', '-r', '-z', base, cwd=project.cache)
    result = []
    for entry in filter(None, entries.split('\0')):
        header, path = entry.split('\t', 1)
        mode, kind, _ = header.split()
        if kind != 'blob' or mode == '120000':
            raise ValueError('リンクや外部参照を含む版の送信は未対応です。別の版を選んでください。')
        result.append(path)
    return result


def make_repo(project, base, folder):
    git('clone', '--no-hardlinks', '--no-checkout', project.cache, folder)
    hooks = folder.parent / ('no-hooks-' + uuid4().hex)
    hooks.mkdir()
    git('config', 'core.hooksPath', hooks, cwd=folder)
    git('checkout', '--detach', base, cwd=folder)
    git('remote', 'set-url', 'origin', project.url, cwd=folder)


def local_base(project, folder):
    _, record = record_for_folder(project, folder)
    if record:
        value = record.get('last_sent_sha') or record.get('sha')
        if value:
            try:
                git('cat-file', '-e', value + '^{commit}', cwd=project.cache)
                return value
            except GitError: pass
    if (folder / '.git').is_dir():
        try:
            url = git('remote', 'get-url', 'origin', cwd=folder).strip().removesuffix('.git').rstrip('/')
            if url.casefold() == project.url.removesuffix('.git').casefold():
                value = git('rev-parse', 'HEAD', cwd=folder).strip()
                git('cat-file', '-e', value + '^{commit}', cwd=project.cache)
                return value
        except GitError: pass
    return None


def inspect_push(project, folder, fallback_base=None, progress=None):
    source = Path(folder).resolve(strict=True)
    if not source.is_dir(): raise ValueError('送信元のフォルダーが見つかりません。')
    base_folder = project.base.resolve()
    reserved = [base_folder / name for name in ('history.git', 'records', 'backups', 'updates', 'outbox', 'local-records')]
    if source == base_folder or source in base_folder.parents or any(source == path or path in source.parents for path in reserved):
        raise ValueError('GitHugの管理用データを含む場所は送信元にできません。コードのフォルダーを選んでください。')
    from .local_records import latest_for_folder,source_stamp
    local=latest_for_folder(project,source)
    origin=project
    stamp=source_stamp(origin,source)
    if local:
        project=copy(project);project.cache=Path(local['_repo'])
    base = local['commit'] if local else local_base(project, source) or fallback_base
    if not base or not re.fullmatch(r'[0-9a-f]{40,64}', base):
        raise ValueError('もとにした履歴が分かりません。上で比較基準の履歴を選んでから送信してください。')
    paths = tracked(project, base)
    fingerprints = {path: digest(safe_path(source, path)) for path in paths}
    project.base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='push-review-', dir=project.base) as temporary:
        repo = Path(temporary) / 'repo'
        make_repo(project, base, repo)
        for path in paths:
            if progress: progress('送信候補を確認: ' + path)
            target = safe_path(repo, path)
            if fingerprints[path] is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copyfile(safe_path(source, path), target)
        raw = git('--no-optional-locks', 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                  '--name-status', '-z', base, '--', cwd=repo).split('\0')
        changes = [Change(raw[i+1], 'D' if raw[i] == 'D' else 'M', fingerprints[raw[i+1]])
                   for i in range(0, len(raw)-1, 2)]
        candidates = []
        known = set(paths)
        for parent, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [name for name in dirs if name.casefold() not in SKIP_DIRS
                       and not (Path(parent) / name).is_symlink()
                       and not getattr((Path(parent) / name).lstat(), 'st_file_attributes', 0) & 0x400]
            for name in files:
                relative = (Path(parent) / name).relative_to(source).as_posix()
                if relative not in known:
                    path = safe_path(source, relative)
                    if path.is_file(): candidates.append(relative)
        # Apply ignore rules from the source, solely in the disposable review repository.
        for parent, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [name for name in dirs if name.casefold() not in SKIP_DIRS
                       and not (Path(parent) / name).is_symlink()
                       and not getattr((Path(parent) / name).lstat(), 'st_file_attributes', 0) & 0x400]
            if '.gitignore' in files:
                relative = (Path(parent) / '.gitignore').relative_to(source).as_posix()
                target = safe_path(repo, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(safe_path(source, relative), target)
        ignored = set()
        if candidates:
            ignored = set(filter(None, git('check-ignore', '--no-index', '-z', '--stdin', cwd=repo,
                         input_text='\0'.join(candidates)+'\0', allowed_returncodes=(0, 1)).split('\0')))
        for path in candidates:
            if path not in ignored:
                changes.append(Change(path, 'A', digest(safe_path(source, path))))
    # Refuse a preview made from files changed during inspection.
    for change in changes:
        if digest(safe_path(source, change.path)) != change.fingerprint:
            raise UpdateConflict('確認中にファイルが変わりました。送信候補を確認し直してください。')
    branches = {}
    for line in git('for-each-ref', '--format=%(refname:strip=2)\t%(objectname)', 'refs/heads', cwd=origin.cache).splitlines():
        name, sha = line.split('\t', 1)
        branches[name] = sha
    def config(name):
        try: return git('config', '--get', name, cwd=source).strip()
        except GitError: return ''
    if source_stamp(origin,source)!=stamp:raise UpdateConflict('確認中に作業フォルダーの基準が変わりました。確認し直してください。')
    return PushPlan(source, base, changes, branches, config('user.name'), config('user.email'),
                    Path(local['_repo']) if local else None,stamp)


def selected_changes(plan, paths):
    selected = set(paths)
    if not selected: raise ValueError('送信するファイルを選んでください。')
    known = {change.path: change for change in plan.changes}
    if not selected <= known.keys(): raise ValueError('送信候補にないファイルが指定されています。')
    result = [known[path] for path in sorted(selected)]
    for change in result:
        if digest(safe_path(plan.folder, change.path)) != change.fingerprint:
            raise UpdateConflict('確認後にファイルが変わりました。送信候補を確認し直してください: ' + change.path)
    return result


def prepare_selected(project, plan, paths, folder):
    selected = selected_changes(plan, paths)
    base_project=copy(project)
    if plan.repository is not None:base_project.cache=plan.repository
    make_repo(base_project, plan.base, folder)
    for change in selected:
        target = safe_path(folder, change.path)
        if change.kind == 'D': target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(plan.folder, change.path), target)
            if digest(target) != change.fingerprint:
                raise UpdateConflict('コピー中にファイルが変わりました。送信候補を確認し直してください: ' + change.path)
        git('--literal-pathspecs', 'add', '-A', '--', change.path, cwd=folder)
    selected_changes(plan, paths)
    return selected


def preview_changes(project, plan, paths, comparison_base=None):
    with tempfile.TemporaryDirectory(prefix='push-diff-', dir=project.base) as temporary:
        folder = Path(temporary) / 'repo'
        selected = prepare_selected(project, plan, paths, folder)
        summary = git('diff', '--cached', '--numstat', '--no-renames', comparison_base or plan.base, '--', cwd=folder)
        patch = git('diff', '--cached', '--no-ext-diff', '--no-textconv', '--no-renames', comparison_base or plan.base, '--', cwd=folder)
        history=git('log','--reverse','--format=fuller','--stat','--patch','--no-ext-diff','--no-textconv',comparison_base+'..'+plan.base,cwd=folder) if comparison_base else ''
    return {'summary': summary, 'patch': patch, 'history':history, 'files': [change.path for change in selected]}


def remote_head(project, branch):
    raw = git('ls-remote', '--heads', project.url, 'refs/heads/' + branch)
    return raw.split('\t', 1)[0] if raw.strip() else None


def destination_expected(project,plan,branch,is_new):
    if not branch or branch.startswith('-'):raise ValueError('作業ルート名を入力してください。')
    git('check-ref-format','--branch',branch)
    tip=remote_head(project,branch)
    if is_new:
        if tip:raise RemoteChanged('同じ名前の作業ルートが既にあります。別の名前を選んでください。')
        return ''
    expected=plan.branches.get(branch)
    if not expected or tip!=expected:raise RemoteChanged('GitHub側が更新されています。履歴を更新して確認し直すか、新しい作業ルートへ送ってください。')
    if expected!=plan.base:
        if plan.repository is None:raise RemoteChanged('GitHub側に別の更新があります。新しい作業ルートへ送ってください。')
        try:git('merge-base','--is-ancestor',expected,plan.base,cwd=plan.repository)
        except GitError:raise RemoteChanged('変更が分かれています。既存の作業ルートは書き換えず、新しい作業ルートへ送ってください。') from None
    return expected


def publish(project, plan, paths, branch, is_new, message, author_name, author_email, progress=None):
    from .local_records import validate_author
    branch,message=branch.strip(),message.strip()
    validate_author(message,author_name,author_email)
    selected_changes(plan,paths)
    expected=destination_expected(project,plan,branch,is_new)
    outbox = project.base / 'outbox' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
    outbox.mkdir(parents=True)
    folder = outbox / 'repo'
    info = {'folder': str(plan.folder), 'base': plan.base, 'branch': branch, 'files': list(paths), 'state': 'preparing'}
    try:
        if progress: progress('送信用のコピーを準備しています…')
        prepare_selected(project, plan, paths, folder)
        git('-c', 'commit.gpgsign=false', '-c', 'user.name=' + author_name,
            '-c', 'user.email=' + author_email, 'commit', '-m', message, cwd=folder)
        commit = git('rev-parse', 'HEAD', cwd=folder).strip()
        if not is_new:
            git('merge-base', '--is-ancestor', expected, commit, cwd=folder)
        info.update(commit=commit, state='ready')
        atomic_json(outbox / 'send.json', info)
        selected_changes(plan, paths)
        if progress: progress('GitHubへ送っています…')
        # This lease is a race guard, never permission to overwrite remote history:
        # existing refs are checked above and the new commit is a direct child of the expected tip.
        try:
            git('push', '--porcelain', '--force-with-lease=refs/heads/' + branch + ':' + expected,
                project.url, 'HEAD:refs/heads/' + branch, cwd=folder)
        except GitError as exc:
            try: confirmed = remote_head(project, branch) == commit
            except GitError: confirmed = False
            if not confirmed: raise exc
        info['state'] = 'sent'
        atomic_json(outbox / 'send.json', info)
    except Exception as exc:
        info['state'] = 'not_confirmed'
        atomic_json(outbox / 'send.json', info)
        raise GitError('送信を完了したと確認できませんでした。手元のフォルダーは変更していません。\n'
                       '送信用コピー: ' + str(outbox) + '\n' + str(exc)) from exc
    warning = ''
    try:
        project.fetch_history()
        path, record = record_for_folder(project, plan.folder)
        if not path:
            path = project.base / 'records' / ('work-sent-' + uuid4().hex + '.json')
            record = {'folder': str(plan.folder), 'sha': plan.base, 'kind': 'work'}
        atomic_json(path, {**record, 'last_sent_sha': commit, 'last_sent_branch': branch})
    except Exception:
        warning = '送信は成功しましたが、一覧または送信元の記録を更新できませんでした。「GitHubの履歴を更新」で取り直してください。'
    # Retain a compact receipt; full failed drafts remain available for inspection.
    try: remove_temporary(folder)
    except OSError: pass
    return {'sha': commit, 'branch': branch, 'warning': warning, 'receipt': str(outbox / 'send.json')}
