"""Explicit, bounded explanation inputs, captured against immutable commit IDs."""
from pathlib import Path
import re
from .core import git
from .explaining import explainable

MAX_TEXT=60000


def file_input(path,detail=False):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('通常のファイルを選択してください。')
    if not explainable(path.name):raise ValueError('設定・DB・鍵などは説明の送信対象外です。')
    text='対象: ファイル\n名前: '+path.name+'\nサイズ: '+str(path.stat().st_size)+' bytes\n'
    if detail:
        with path.open('rb') as source:raw=source.read(MAX_TEXT+1)
        if len(raw)>MAX_TEXT:raise ValueError('ファイルが大きすぎます。内容の送信を外してください。')
        if b'\0' in raw:raise ValueError('バイナリファイルの内容は送信できません。')
        try:content=raw.decode('utf-8-sig')
        except UnicodeDecodeError:raise ValueError('UTF-8のテキストファイルだけ内容を送信できます。') from None
        text+='\nファイル内容:\n'+content
    else:text+='内容は未送信です。名前・サイズから分かる範囲だけ説明してください。'
    return bounded(text)


def bounded(text):
    if len(text)>MAX_TEXT:raise ValueError('送信内容が大きすぎます。概要のみを選択してください。')
    return text


def history_input(project,sha,base=None,detail=False):
    if not re.fullmatch(r'[0-9a-f]{40,64}',sha):raise ValueError('履歴を選択してください。')
    sha=git('rev-parse','--verify',sha+'^{commit}',cwd=project.cache).strip()
    if base:
        base_sha=git('rev-parse','--verify','--end-of-options',base+'^{commit}',cwd=project.cache).strip()
        baseline=git('merge-base',base_sha,sha,cwd=project.cache).strip()
        title='対象: 分岐の変更\n比較元: '+base+' ('+base_sha+')\n共通の分岐元: '+baseline+'\n選択した先端: '+sha
    else:
        parents=git('show','-s','--format=%P',sha,cwd=project.cache).split()
        baseline=parents[0] if parents else git('hash-object','-t','tree','--stdin',input_text='',cwd=project.cache).strip()
        title='対象: 一つの履歴\n変更前: '+baseline+'\n変更後: '+sha+ ('\n合流の最初の親と比較します。' if len(parents)>1 else '')
    raw=git('diff','--no-ext-diff','--no-textconv','--no-renames','--numstat','-z',baseline,sha,'--',cwd=project.cache)
    safe=[];summary=[];excluded=0
    for row in raw.split('\0'):
        if not row:continue
        added,removed,path=row.split('\t',2)
        if not explainable(path) or added=='-' or removed=='-':excluded+=1;continue
        safe.append(path);summary.append(added+' / '+removed+' / '+path)
    if not safe:raise ValueError('説明できる変更がありません。設定・DB・鍵・バイナリは除外しています。')
    if len(safe)>500:raise ValueError('変更ファイルが多すぎます。一つの履歴を選んでください。')
    text=title+'\n除外したファイル: '+str(excluded)+'\n追加 / 削除 / ファイル\n'+'\n'.join(summary)
    if detail:text+='\n差分:\n'+git('--literal-pathspecs','diff','--no-ext-diff','--no-textconv','--no-renames','--unified=3',baseline,sha,'--',*safe,cwd=project.cache)
    else:text+='\n差分の本文は未送信です。目的や正しさを断定しないでください。'
    return bounded(text)
