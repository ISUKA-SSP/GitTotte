"""Read-only comparisons of remote routes; proposal creation remains in GitHub's browser UI."""
from dataclasses import dataclass
from urllib.parse import quote
from .core import git,GitError,validate_url

class RoutesChanged(RuntimeError):
    pass

@dataclass(frozen=True)
class RouteComparison:
    base: str
    head: str
    base_sha: str
    head_sha: str
    common: str | None
    incoming: int
    destination_only: int
    files: tuple
    commits: str

    @property
    def can_propose(self):return self.common is not None and self.incoming>0

    @property
    def summary(self):
        if self.common is None:return '共通の出発点が見つかりません。この組み合わせの取り込み提案は作成できません。'
        if not self.incoming:return '取り込み元の履歴は、すでに取り込み先に含まれています。追加する変更はありません。'
        suffix=' 両方で更新されています。内容がぶつかるかはGitHubで確認します。' if self.destination_only else ''
        if not self.files:return f'追加の履歴は {self.incoming} 件ありますが、ファイルの最終内容は同じです。'+suffix
        return f'取り込み元だけの履歴：{self.incoming} 件 ／ 変更ファイル：{len(self.files)} 件。'+suffix


def routes(project):
    result={}
    for line in git('for-each-ref','--format=%(refname:strip=2)\t%(objectname)','refs/heads',cwd=project.cache).splitlines():
        name,sha=line.split('\t',1);result[name]=sha
    return result


def default_routes(branches,preferred_sha=None):
    if len(branches)<2:raise ValueError('比較する作業ルートが2つ必要です。別の作業ルートへ送信した後で利用できます。')
    base='main' if 'main' in branches else 'master' if 'master' in branches else sorted(branches)[0]
    candidates=[name for name in sorted(branches) if name!=base]
    head=next((name for name in candidates if branches[name]==preferred_sha),candidates[0])
    return base,head


def compare_routes(project,base,head):
    if base==head:raise ValueError('取り込み先と取り込み元には、異なる作業ルートを選んでください。')
    branches=routes(project)
    if base not in branches or head not in branches:raise RoutesChanged('作業ルートが見つかりません。GitHubの履歴を更新して選び直してください。')
    a,b=branches[base],branches[head]
    counts=git('rev-list','--left-right','--count',a+'...'+b,cwd=project.cache).split()
    destination_only,incoming=map(int,counts)
    common=git('merge-base',a,b,cwd=project.cache,allowed_returncodes=(0,1)).strip() or None
    files=[];commits=''
    if common:
        raw=git('diff','--no-ext-diff','--no-textconv','--no-renames','--name-status','-z',common,b,'--',cwd=project.cache).split('\0')
        files=[(raw[i],raw[i+1]) for i in range(0,len(raw)-1,2)]
        commits=git('log','--max-count=200','--format=%h %s',a+'..'+b,'--',cwd=project.cache)
        if incoming>200:commits+='\n（最新200件を表示しています。全件はGitHubで確認できます。）'
    return RouteComparison(base,head,a,b,common,incoming,destination_only,tuple(files),commits)


def file_patch(project,plan,path,limit=180000):
    if not plan.common or path not in {entry[1] for entry in plan.files}:raise ValueError('比較した一覧からファイルを選んでください。')
    text=git('--literal-pathspecs','diff','--no-ext-diff','--no-textconv','--no-renames',plan.common,plan.head_sha,'--',path,cwd=project.cache)
    if len(text)>limit:text=text[:limit]+'\n\n（表示量を制限しています。全文はGitHubで確認してください。）'
    return text or 'このファイルに表示できる差分はありません。'


def proposal_url(project,plan):
    if not plan.can_propose:raise ValueError('この比較には新しく提案する履歴がありません。')
    url,_=validate_url(project.url)
    return url.removesuffix('.git')+'/compare/'+quote(plan.base,safe='')+'...'+quote(plan.head,safe='')+'?quick_pull=1'


def checked_proposal_url(project,plan):
    latest=routes(project)
    if latest.get(plan.base)!=plan.base_sha or latest.get(plan.head)!=plan.head_sha:
        raise RoutesChanged('比較した後に作業ルートが更新または削除されました。もう一度比較してから進んでください。')
    return proposal_url(project,plan)
