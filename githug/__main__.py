import json
from datetime import datetime
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
from .core import Project, ComparisonCancelled
from .console_panel import ConsolePanel
from .folder_removal import checked_removal, identity, recycle_registered, hide_missing, folder_key
from .folder_batch_ui import remove_selected
from .local_records_ui import show_records
from .route_review_ui import open_route_review
from .restoration import prepare_backup,prepare_again
from .restoration_ui import start_restore
from .project_storage import relocate_project
from . import __version__
from .listing import date_value, visible_rows
from .theme import apply_dark_theme
from .updating import (inspect_update, apply_update, record_for_folder, authorize_update,
                       disable_update, UpdateNotAllowed, UpdateConflict)
from .notes import Notes, short_note
from .editors import find_vscode, open_vscode
from .push_ui import PushUI
from .history_graph import HistoryGraph, history_page
from .graph_view import GraphView
from .folder_tree import FolderTree
from .search_header import SearchHeader
from .splitter import SidebarSplitter, SectionSplitter
from .explanation_panel import ExplanationPanel
from .drag_hint import DragHint
from .branding import attach_logo
from .mascot import MascotView, operation_pose
from .guidance import HoverHelp, register_buttons
from .operation_guide import open_guide, begin as guide_begin, finish as guide_finish, handoff as guide_handoff
from .terminology import Terminology, messagebox

class App:
    def __init__(self, window):
        self.window = window
        self.style = apply_dark_theme(window)
        window.title(f'GitTotte {__version__} — 履歴から、安心して試す')
        window.geometry('1360x900')
        window.minsize(1100, 800)
        logo = attach_logo(window)
        self.config_path = (Path(window._githug_smoke_dir) / 'settings.json' if getattr(window,'_githug_smoke',False) else Path(os.environ.get('APPDATA', Path.home() / '.config')) / 'GitHug' / 'settings.json')
        try:
            self.config = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (FileNotFoundError, ValueError):
            self.config = {'root': '', 'projects': []}
        self.terms = Terminology(window,self.config.get('terminology','operations'),self.change_terminology)
        self.tooltips = HoverHelp(window, self.config.get('hover_help', True))
        self.project = None
        self.notes = None
        self.dragging = None
        self.push_ui = PushUI(self)
        self.busy = False
        self.records = {}
        self.history_rows = []
        self.folder_rows = []
        self.sort_options = {'history': ('topology', False), 'folders': ('created', True)}
        self.history_model = HistoryGraph([])
        self.history_page = 0
        self.history_page_size = self.config.get('history_page_size', 100)
        if self.history_page_size not in (50, 100, 200): self.history_page_size = 100
        self.drag_hint = DragHint(window)
        self.events = queue.Queue()
        appbar = ttk.Frame(window, padding=(20,14), style='Appbar.TFrame')
        appbar.pack(fill='x')
        ttk.Label(appbar, image=logo, style='AppbarMuted.TLabel').pack(side='left', padx=(0,12))
        brand = ttk.Frame(appbar, style='Appbar.TFrame')
        brand.pack(side='left')
        ttk.Label(brand, text='GitTotte', style='Brand.TLabel').pack(anchor='w')
        ttk.Label(brand, text='履歴から、安心して試す', style='BrandSubtitle.TLabel').pack(anchor='w')
        ttk.Label(appbar, text='v' + __version__, style='Badge.TLabel').pack(side='left', padx=16)
        # Use the supplied illustration unchanged; native Tk scaling needs no imaging dependency.
        self.mascot_image = tk.PhotoImage(master=window, file=str(Path(__file__).parent/'assets'/'living_agent.png')).subsample(4,4)
        self.mascot_view = MascotView(appbar,self.guide,self.mascot_image,self.config.get('mascot_animation',True))
        self.mascot = self.mascot_view.canvas
        self.mascot.pack(side='right',padx=(16,0))
        self.tooltips.add(self.mascot,'ステラとシリカです。クリックすると使い方・環境を開きます。動きは設定でOFFにできます。')
        self.operation_state = tk.StringVar(value='待機中')
        ttk.Button(appbar, text='使い方・環境', command=self.guide, style='Quiet.TButton').pack(side='right')
        ttk.Button(appbar, text='設定', command=self.settings_dialog, style='Quiet.TButton').pack(side='right', padx=4)
        self.activity_bar = ttk.Progressbar(appbar, mode='indeterminate', length=90)
        self.activity_bar.pack(side='right', padx=12)
        ttk.Label(appbar, textvariable=self.operation_state, style='AppbarMuted.TLabel').pack(side='right')
        toolbar = ttk.Frame(window, padding=(14,10), style='Card.TFrame')
        toolbar.pack(fill='x', padx=16, pady=(0,12))
        ttk.Button(toolbar, text='GitHubの履歴を更新', command=self.refresh, style='Primary.TButton').pack(side='left', padx=4)
        ttk.Button(toolbar, text='＋ プロジェクト', command=self.add).pack(side='left', padx=4)
        ttk.Button(toolbar,text='操作ガイド',command=lambda:open_guide(self)).pack(side='left',padx=4)
        storage=ttk.Menubutton(toolbar,text='保管した内容')
        self.storage_button=storage
        storage_menu=tk.Menu(storage,tearoff=False)
        storage_menu.add_command(label='PCの記録',command=lambda:show_records(self))
        storage_menu.add_command(label='更新前のバックアップ',command=self.backups_dialog)
        storage.configure(menu=storage_menu);storage.pack(side='left',padx=4)
        self.tooltips.add(storage,'PCに残した記録と、更新前のバックアップを確認します。')
        ttk.Button(toolbar, text='コピー・履歴の保存先', command=self.choose_root, style='Quiet.TButton').pack(side='right', padx=4)
        language=ttk.Frame(toolbar);language.pack(side='right',padx=8)
        ttk.Label(language,text='表示用語：').pack(side='left')
        self.term_mode=tk.StringVar(value=self.terms.mode)
        for title,value in (('操作名','operations'),('Git用語','git')):
            ttk.Radiobutton(language,text=title,value=value,variable=self.term_mode,
                            command=lambda:self.terms.set_mode(self.term_mode.get())).pack(side='left',padx=3)
        self.status = tk.StringVar(value='「＋ プロジェクト」からGitHubのURLを追加して始めましょう。')
        footer=ttk.Frame(window,padding=(12,7),style='Card.TFrame')
        footer.pack(fill='x',side='bottom',padx=16,pady=(8,12))
        status_label=ttk.Label(footer,textvariable=self.status,style='Muted.TLabel',wraplength=1250)
        status_label.pack(fill='x')
        status_label.bind('<Configure>',lambda event:status_label.configure(wraplength=max(100,event.width-4)))
        self.workspace = ttk.Frame(window)
        self.workspace.pack(fill='both',expand=True,padx=16)
        split = SidebarSplitter(self.workspace, orient='horizontal')
        self.main_split = split
        split.pack(fill='both', expand=True)
        left = ttk.Frame(split, padding=12, style='Card.TFrame')
        left.configure(width=245)
        left.pack_propagate(False)
        split.add(left, weight=0)
        ttk.Label(left, text='プロジェクト', font=('', 12, 'bold')).pack(anchor='w')
        self.projects = tk.Listbox(left, exportselection=False, width=22, font=('Yu Gothic UI',11), activestyle='none', selectborderwidth=0)
        self.projects.configure(height=6)
        self.projects.pack(fill='x', pady=8)
        self.projects.bind('<<ListboxSelect>>', self.select_project)
        self.action_guide = tk.StringVar()
        self.guide_label = ttk.Label(left,textvariable=self.action_guide,style='Muted.TLabel',wraplength=170)
        if self.config.get('selection_guide', True):self.guide_label.pack(anchor='w',pady=(8,0))
        self.set_action_guide('default')
        self.folder_browser = FolderTree(left, self.inspect_file)
        right = ttk.Frame(split, width=800)
        right.pack_propagate(False)
        split.add(right, weight=1)
        self.sections = SectionSplitter(right)
        self.sections.pack(fill='both', expand=True)
        history_tab = ttk.Frame(self.sections, padding=12, style='Card.TFrame')
        history_tab.columnconfigure(0, weight=1)
        history_tab.rowconfigure(2, weight=1, minsize=70)
        self.sections.add(history_tab, weight=3)
        self.history_header = SearchHeader(history_tab, 'GitHubの履歴')
        self.history_header.grid(row=0,column=0,sticky='ew',pady=(0,6))
        self.history_query,self.history_count = self.history_header.query,self.history_header.count
        self.history = self.table(history_tab, ('date', 'refs', 'message', 'memo'), ('更新日時', '作業の分岐・目印', '変更の説明', '自分用メモ'), tree=True, grid_row=2, graph=True)
        self.history.bind('<<TreeviewSelect>>', self.detail)
        self.history.bind('<Button-3>', self.menu)
        self.history.bind('<ButtonPress-1>', self.history_press, add='+')
        self.history.bind('<B1-Motion>', self.history_motion, add='+')
        self.history.bind('<ButtonRelease-1>', self.history_release, add='+')
        for event, handler in [('<ButtonPress-1>', self.history_press), ('<B1-Motion>', self.history_motion),
                               ('<ButtonRelease-1>', self.history_release), ('<Button-3>', self.menu)]:
            self.history.graph_view.canvas.bind(event, handler)
        self.history_pager(history_tab)
        self.selection = tk.StringVar(value='')
        ttk.Label(history_tab, textvariable=self.selection, wraplength=850, style='Muted.TLabel').grid(row=3, column=0, sticky='ew', pady=4)
        controls = ttk.Frame(history_tab)
        controls.grid(row=4, column=0, sticky='ew')
        ttk.Button(controls, text='この時点のファイルをPCへコピー', command=self.reproduce, style='Primary.TButton').pack(side='left', padx=4)
        compare_menu=ttk.Menubutton(controls,text='比べる・取り込む')
        self.compare_menu=compare_menu
        comparisons=tk.Menu(compare_menu,tearoff=False)
        comparisons.add_command(label='作業フォルダーと比較',command=self.compare)
        comparisons.add_command(label='別の作業ルートと比較・取り込み提案',command=lambda:open_route_review(self))
        compare_menu.configure(menu=comparisons);compare_menu.pack(side='left',padx=4)
        self.tooltips.add(compare_menu,'作業フォルダーとの比較、またはGitHub上の作業ルート同士の比較・取り込み提案に進みます。')
        folders = ttk.Frame(self.sections, padding=12, style='Card.TFrame')
        folders.columnconfigure(0, weight=1)
        folders.rowconfigure(1, weight=1, minsize=70)
        self.sections.add(folders, weight=2)
        self.folder_header = SearchHeader(folders, 'PCのフォルダー')
        self.folder_header.grid(row=0,column=0,sticky='ew',pady=(0,6))
        self.folder_query,self.folder_count = self.folder_header.query,self.folder_header.count
        self.folders = self.table(folders, ('kind', 'path', 'sha', 'created', 'state', 'memo'), ('種類', '保存場所', 'もとにした履歴', 'コピーした日時', '状態', '自分用メモ'), grid_row=1)
        self.folders.configure(selectmode='extended')
        self.folders.configure(displaycolumns=('sha', 'created', 'state', 'path', 'memo'))
        self.folders.bind('<Button-3>', self.folder_menu)
        self.folders.bind('<ButtonPress-1>', self.folder_press, add='+')
        self.folders.bind('<B1-Motion>', self.folder_motion, add='+')
        self.folders.bind('<ButtonRelease-1>', self.folder_release, add='+')
        self.window.bind('<FocusIn>', self.on_window_focus, add='+')
        self.window.bind('<Escape>', self.cancel_drag, add='+')
        folder_controls = ttk.Frame(folders)
        folder_controls.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        ttk.Button(folder_controls, text='作業フォルダーを登録', command=self.register_folder).pack(side='left', padx=4)
        ttk.Button(folder_controls, text='フォルダーを開く', command=self.open_folder).pack(side='left', padx=4)
        ttk.Button(folder_controls, text='PowerShell／コマンド', command=self.command_dialog).pack(side='left', padx=4)
        ttk.Button(folder_controls, text='一覧を再表示', command=self.check_folder_tab).pack(side='left', padx=4)
        self.console = ConsolePanel(self)
        self.window.bind('<F5>', self.refresh_shortcut, add='+')
        window.protocol('WM_DELETE_WINDOW', self.close_app)
        self.explanation = ExplanationPanel(split, self)
        self.explanation_visible = False
        ttk.Button(toolbar, text='説明パネル', command=self.toggle_explanation).pack(side='right', padx=4)
        self.folders.bind('<<TreeviewSelect>>', self.browse_selected_folder, add='+')
        self.configure_sort('history', self.history, ('#0', 'date', 'refs', 'message', 'memo'))
        self.configure_sort('folders', self.folders, ('sha', 'created', 'state', 'path', 'memo'))
        self.history_query.trace_add('write', lambda *_: self.render_history())
        self.folder_query.trace_add('write', lambda *_: self.render_folders())
        register_buttons(window, self.tooltips)
        self.terms.bind_variable(self.action_guide)
        self.terms.bind_variable(self.operation_state)
        self.terms.bind_variable(self.status)
        self.terms.refresh()
        for header in (self.history_header, self.folder_header):
            self.tooltips.add(header.entry, '一覧を絞り込みます。自分用メモも検索できます。×またはEscで解除します。')
            self.tooltips.add(header.clear_button, '検索を解除して一覧へ戻します。')
        self.tooltips.add(self.history, '履歴を選び、下のPCのフォルダーへドラッグしてコピーできます。行の右クリックでも操作できます。')
        self.tooltips.add(self.history.graph_view.canvas, '丸が一つの履歴、線が履歴のつながりです。下へドラッグするとPCへコピーできます。')
        self.tooltips.add(self.folders, 'フォルダーを上の履歴へドラッグすると、ファイルを確認してGitHubへ送れます。')
        self.populate_projects()
        self._sections_initialized = False
        self.sections.bind('<Configure>', self.initialize_sections, add='+')
        window.after(150, lambda: self.main_split.sashpos(0, 245))
        window.after(100, self.poll)

    def change_terminology(self,mode):
        self.config['terminology']=mode
        self.save()
        if hasattr(self,'tooltips'):self.tooltips.hide()

    def set_action_guide(self, kind):
        self.guide_kind=kind
        text={
            'default':'上：GitHubの履歴\n下：PCのフォルダー\n項目を選ぶと操作を案内します。',
            'history':'この履歴を下のPC欄の空いている場所へドラッグすると、新しいフォルダーにコピーします。フォルダーの行へ重ねると、更新方法を確認できます。',
            'folder':'このフォルダーを上の履歴へドラッグすると、送るファイルを確認してGitHubへ送信できます。ドラッグだけでは送信しません。',
        }[kind]
        self.action_guide.set(text)

    def settings_dialog(self):
        existing=getattr(self,'settings_window',None)
        if existing is not None and existing.winfo_exists():existing.lift();return existing
        popup=tk.Toplevel(self.window);self.settings_window=popup
        popup.title('GitTotte — 表示・操作の設定');popup.geometry('640x410');popup.minsize(540,390)
        area=ttk.Frame(popup,padding=18);area.pack(fill='both',expand=True)
        ttk.Label(area,text='操作を助ける表示',style='Heading.TLabel').pack(anchor='w',pady=(0,12))
        hover=tk.BooleanVar(value=self.config.get('hover_help',True))
        guide=tk.BooleanVar(value=self.config.get('selection_guide',True))
        animation=tk.BooleanVar(value=self.config.get('mascot_animation',True))
        ttk.Checkbutton(area,text='マウスを重ねたときに説明を表示',variable=hover).pack(anchor='w',pady=6)
        ttk.Label(area,text='少し待つと表示し、数秒で消えます。',style='Muted.TLabel').pack(anchor='w')
        ttk.Checkbutton(area,text='選んだ履歴・フォルダーの操作案内を左側に表示',variable=guide).pack(anchor='w',pady=(16,6))
        ttk.Checkbutton(area,text='キャラクターを動かす',variable=animation).pack(anchor='w',pady=(16,6))
        ttk.Label(area,text='OFFにすると静止画になります。クリックによる案内は使えます。',style='Muted.TLabel').pack(anchor='w')
        buttons=ttk.Frame(area);buttons.pack(side='bottom',fill='x')
        def apply():
            self.config.update(hover_help=hover.get(),selection_guide=guide.get(),mascot_animation=animation.get());self.save()
            self.mascot_view.set_enabled(animation.get())
            self.tooltips.set_enabled(hover.get())
            if guide.get():self.guide_label.pack(anchor='w',pady=(8,0),before=self.folder_browser.frame)
            else:self.guide_label.pack_forget()
            popup.destroy()
        ttk.Button(buttons,text='キャンセル',command=popup.destroy).pack(side='right')
        ttk.Button(buttons,text='適用',command=apply,style='Primary.TButton').pack(side='right',padx=8)
        return popup

    def initialize_sections(self, event):
        if not self._sections_initialized and event.height >= 540:
            self._sections_initialized = True
            self.window.after_idle(lambda: self.sections.sashpos(0, int(self.sections.winfo_height() * 0.60)))

    def toggle_explanation(self):
        if self.explanation_visible:
            self.main_split.forget(self.explanation.frame)
        else:
            self.main_split.add(self.explanation.frame, weight=0)
            self.window.update_idletasks()
            self.main_split.sashpos(0, 245)
            self.main_split.sashpos(1, max(650, self.main_split.winfo_width()-350))
        self.explanation_visible = not self.explanation_visible

    def inspect_file(self, path):
        self.explanation.choose({'kind':'file','path':path,'label':'ファイル: '+Path(path).name})

    def browse_selected_folder(self, event=None):
        items=self.folders.selection()
        if not items:return
        if len(items)>1:
            self.action_guide.set(f'{len(items)} 件を選択中。Ctrlで追加・解除、Shiftで範囲選択できます。右クリックからまとめて整理できます。送信する場合は1フォルダーを選んでください。')
            if self.folder_browser.root:self.folder_browser.set_folder(None)
            return
        self.set_action_guide('folder')
        folder=self.folders.item(items[0], 'values')[1]
        if self.folder_browser.root!=folder:self.folder_browser.set_folder(folder)

    def guide(self):
        existing=getattr(self,'guide_window',None)
        if existing is not None and existing.winfo_exists():existing.lift();return existing
        from .platform_support import git_executable
        popup=tk.Toplevel(self.window)
        self.guide_window=popup
        popup.title('GitTotte — 使い方・環境')
        popup.geometry('760x540')
        popup.minsize(600,450)
        ttk.Label(popup,text='GitTotte  '+__version__,style='Heading.TLabel',padding=16).pack(anchor='w')
        text=(
            '1  プロジェクトを追加：GitHubのURLを登録します。\n'
            '2  履歴からPCへドラッグ：別フォルダーへのコピーや、確認付きの更新。\n'
            '3  PCから履歴へドラッグ：ファイルを確認してGitHubへ送信。\n\n'
            '検索は全履歴と自分用メモが対象です。選んだ履歴をグラフで見る操作で、その流れへ戻れます。\n'
            '自分用メモ・VS Code・PowerShellは、行の右クリックから開けます。\n'
            'PowerShellは下側のパネルで操作します。右の欄に複数行のコマンド、下の欄に途中の回答を入力できます。\n'
            '✖はパネルを隠すだけです。「処理を中断」はCtrl+C、「終了」はPowerShellとその処理を終了します。\n'
            '同じ場所への更新は、GitTotteのコピーフォルダーか、明示的に有効にした作業用だけです。\n\n'
            '公開プロジェクトの取得にはログイン不要です。送信・非公開プロジェクトにはPC側のGitHub認証が必要です。\n'
            'Geminiは任意。説明を頼むボタンを押すまで呼び出しません。\n'
            'APIキーは接続設定で任意保存できます。保存先はWindowsの資格情報マネージャーです。\n'
            'マウスの説明・左側の操作案内は「設定」で切り替えられます。\n\n'
            '使用するGit：'+git_executable()+'\n設定：'+str(self.config_path))
        body=ttk.Frame(popup,padding=(16,0));body.pack(fill='both',expand=True)
        reader=tk.Text(body,wrap='word',height=12,width=1,font=('Yu Gothic UI',11),borderwidth=0,padx=4,pady=4)
        scroll=ttk.Scrollbar(body,command=reader.yview);scroll.pack(side='right',fill='y')
        reader.configure(yscrollcommand=scroll.set);reader.pack(fill='both',expand=True)
        reader.insert('1.0',text);reader.configure(state='disabled')
        footer=ttk.Frame(popup,padding=16);footer.pack(fill='x')
        import webbrowser
        footer.columnconfigure(0,weight=1)
        ttk.Button(footer,text='ステラとシリカについて — LivingAgent',command=lambda:webbrowser.open('https://github.com/ISUKA-SSP/living_agent_public/')).grid(row=0,column=0,sticky='w')
        self.report_button=ttk.Button(footer,text='不具合・要望を送る',command=self.report_issue)
        self.report_button.grid(row=1,column=0,sticky='w',pady=(8,0))
        ttk.Button(footer,text='閉じる',command=popup.destroy).grid(row=0,column=1,rowspan=2,padx=(12,0))
        return popup

    def report_issue(self):
        import platform
        import webbrowser
        from urllib.parse import urlencode
        body=(f'## バージョン\n{__version__}\n\n## OS\n{platform.platform()}\n\n'
              '## 再現手順\n\n## 期待した動作\n\n## 実際に起きたこと・エラー表示\n\n'
              'APIキー・個人情報・業務データを含めずに記載してください。')
        query=urlencode({'title':'[不具合] ', 'body':body})
        webbrowser.open('https://github.com/ISUKA-SSP/GitTotte/issues/new?'+query)

    def configure_sort(self, name, table, columns):
        table.sort_labels = {col: table.heading(col, 'text') for col in columns}
        for col in columns:
            table.heading(col, command=lambda col=col: self.sort_list(name, col))

    def sort_list(self, name, column):
        old_column, descending = self.sort_options[name]
        self.sort_options[name] = (column, not descending if column == old_column else column in ('date', 'created'))
        if name == 'history': self.render_history()
        else: self.render_folders()

    def render_rows(self, name, table, rows, query, count):
        selected = table.selection()
        table.delete(*table.get_children())
        column, descending = self.sort_options[name]
        shown = visible_rows(rows, query.get(), column, descending, pinned_first=(name == 'folders'))
        for row in shown:
            table.insert('', 'end', iid=row['id'], text=row.get('text', ''), values=row['values'])
        for col, label in table.sort_labels.items():
            table.heading(col, text=label + ((' ▼' if descending else ' ▲') if col == column else ''))
        if selected and table.exists(selected[0]):
            table.selection_set(selected[0])
            table.see(selected[0])
        else:
            table.yview_moveto(0)
        count.set(f'{len(shown)} / {len(rows)} 件')

    def history_pager(self, parent):
        bar = ttk.Frame(parent)
        bar.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        self.page_number = tk.StringVar(value='0')
        self.page_total = tk.StringVar(value='/ 0 ページ')
        self.page_size_text = tk.StringVar(value=str(self.history_page_size))
        self.history_mode_text = tk.StringVar(value='グラフ：履歴のつながり順')
        self.newer_button = ttk.Button(bar, text='◁ 新しい方', command=lambda: self.move_history_page(-1), state='disabled')
        self.newer_button.grid(row=0, column=0, padx=(0, 4))
        page_entry = ttk.Entry(bar, textvariable=self.page_number, width=5, justify='center')
        page_entry.grid(row=0, column=1)
        page_entry.bind('<Return>', lambda event: self.go_history_page())
        ttk.Label(bar, textvariable=self.page_total).grid(row=0, column=2, padx=5)
        ttk.Button(bar, text='移動', command=self.go_history_page).grid(row=0, column=3, padx=4)
        self.older_button = ttk.Button(bar, text='古い方 ▷', command=lambda: self.move_history_page(1), state='disabled')
        self.older_button.grid(row=0, column=4, padx=4)
        size = ttk.Combobox(bar, textvariable=self.page_size_text, values=('50', '100', '200'), width=5, state='readonly')
        size.grid(row=0, column=5, padx=(10, 4))
        size.bind('<<ComboboxSelected>>', lambda event: self.resize_history_page())
        ttk.Label(bar, text='件／ページ').grid(row=0, column=6)
        ttk.Button(bar, text='選んだ履歴をグラフで見る', command=self.jump_to_history_graph).grid(row=0, column=7, padx=8)
        ttk.Label(bar, textvariable=self.history_mode_text, style='Muted.TLabel', wraplength=850).grid(row=1, column=0, columnspan=8, sticky='w', pady=(4, 0))

    def move_history_page(self, step):
        self.history_page += step
        self.render_history(reset_page=False)

    def go_history_page(self):
        try:
            value = int(self.page_number.get())
            if value < 1: raise ValueError()
        except ValueError:
            messagebox.showinfo('ページ番号', '1以上のページ番号を入力してください。')
            return
        self.history_page = value - 1
        self.render_history(reset_page=False)

    def resize_history_page(self):
        old_start = self.history_page * self.history_page_size
        self.history_page_size = int(self.page_size_text.get())
        self.history_page = old_start // self.history_page_size
        self.config['history_page_size'] = self.history_page_size
        self.save()
        self.render_history(reset_page=False)

    def jump_to_history_graph(self):
        selected = self.history.selection()
        sha = selected[0] if selected else None
        self.sort_options['history'] = ('topology', False)
        self.history_query.set('')
        self.render_history(focus_sha=sha)

    def render_history(self, reset_page=True, focus_sha=None):
        selected = self.history.selection()
        if reset_page: self.history_page = 0
        column, descending = self.sort_options['history']
        page = history_page(self.history_rows, self.history_query.get(), column, descending,
                            self.history_page, self.history_page_size, focus=focus_sha)
        self.history_page = page.page
        self.history.delete(*self.history.get_children())
        for row in page.rows:
            self.history.insert('', 'end', iid=row['id'], text=row['text'], values=row['values'])
        for col, label in self.history.sort_labels.items():
            self.history.heading(col, text=label + ((' ▼' if descending else ' ▲') if col == column else ''))
        graph_mode = column == 'topology' and not self.history_query.get().strip()
        self.history.graph_view.set_page(self.history_model, [row['id'] for row in page.rows], graph_mode)
        desired = focus_sha or (selected[0] if selected else None)
        if desired and self.history.exists(desired):
            self.history.selection_set(desired)
            self.history.see(desired)
        else:
            self.history.yview_moveto(0)
        self.page_number.set(str(page.page + 1) if page.pages else '0')
        self.page_total.set(f'/ {page.pages} ページ')
        self.newer_button.configure(state='normal' if page.page > 0 else 'disabled')
        self.older_button.configure(state='normal' if page.page + 1 < page.pages else 'disabled')
        self.history_count.set(f'{page.matches} / {page.total} 件')
        self.history_mode_text.set('グラフ：つながり順。上下の矢印は表示範囲外へ続きます。' if graph_mode else
                                   '検索・並べ替え中は線を省略。履歴を選び「選んだ履歴をグラフで見る」で該当位置へ戻れます。')
        self.selection.set('')
        self.detail()

    def render_folders(self):
        selected={folder_key(self.folders.item(item,'values')[1]) for item in self.folders.selection()}
        focus=self.folders.focus()
        focus_path=folder_key(self.folders.item(focus,'values')[1]) if focus and self.folders.exists(focus) else None
        view=self.folders.yview()
        self.render_rows('folders', self.folders, self.folder_rows, self.folder_query, self.folder_count)
        restored=[item for item in self.folders.get_children() if folder_key(self.folders.item(item,'values')[1]) in selected]
        self.folders.selection_set(restored)
        for item in self.folders.get_children():
            if folder_key(self.folders.item(item,'values')[1])==focus_path:self.folders.focus(item);break
        if view:self.folders.yview_moveto(view[0])

    def table(self, parent, columns, labels, tree=False, grid_row=None, graph=False):
        frame = ttk.Frame(parent)
        if grid_row is None:
            frame.pack(fill='both', expand=True)
        else:
            frame.grid(row=grid_row, column=0, sticky='nsew')
        widget = ttk.Treeview(frame, columns=columns, show='tree headings' if tree else 'headings', selectmode='browse', height=3)
        if tree:
            widget.heading('#0', text='履歴番号')
            widget.column('#0', width=160)
        for col, label in zip(columns, labels):
            widget.heading(col, text=label)
            widget.column(col, width={'path': 550, 'message': 440, 'date': 175, 'created': 175}.get(col, 170), minwidth=100)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=widget.xview)
        widget.configure(xscrollcommand=horizontal.set)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
        if graph:
            frame.columnconfigure(1, weight=1)
            frame.rowconfigure(0, weight=1)
            graph_view = GraphView(frame, widget)
            widget.graph_view = graph_view
            graph_view.canvas.grid(row=0, column=0, sticky='nsew')
            graph_view.scrollbar.grid(row=1, column=0, sticky='ew')
            handle=ttk.Frame(frame,width=7,cursor='sb_h_double_arrow',style='GraphHandle.TFrame')
            handle.grid(row=0,column=1,sticky='ns',padx=(0,2))
            frame.columnconfigure(1,weight=0)
            frame.columnconfigure(2,weight=1)
            def resize_graph(event):
                width=max(90,min(600,event.x_root-graph_view.canvas.winfo_rootx()))
                graph_view.manual_width=width
                graph_view.canvas.configure(width=width)
                self.config['graph_width']=width
                graph_view.schedule()
            handle.bind('<B1-Motion>',resize_graph)
            handle.bind('<ButtonRelease-1>',lambda event:self.save())
            def auto_graph(event):
                graph_view.manual_width=None
                self.config.pop('graph_width',None)
                if graph_view.model:graph_view.set_page(graph_view.model,graph_view.ids,graph_view.enabled)
                self.save()
            handle.bind('<Double-1>',auto_graph)
            if isinstance(self.config.get('graph_width'),int):graph_view.manual_width=max(90,min(600,self.config['graph_width']))
            widget.grid(row=0, column=2, sticky='nsew')
            scrollbar.grid(row=0, column=3, sticky='ns')
            horizontal.grid(row=1, column=2, sticky='ew')
            def scrolled(first, last):
                scrollbar.set(first, last)
                graph_view.schedule()
            widget.configure(yscrollcommand=scrolled)
        else:
            horizontal.pack(side='bottom', fill='x')
            widget.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            widget.pack(fill='both', expand=True)
        return widget

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.config_path)

    def choose_root(self):
        if self.busy: return
        if self.config['projects']:
            self.choose_project_storage()
            return
        folder = filedialog.askdirectory(title='GitTotteの保存先を選択')
        if folder:
            self.config['root'] = folder
            self.save()

    def choose_project_storage(self):
        if not self.project:
            messagebox.showinfo('プロジェクトを選択', '保存先を変更するプロジェクトを選んでください。')
            return
        other_dialog_open = any(
            isinstance(widget, tk.Toplevel) and not widget.overrideredirect()
            for widget in self.window.winfo_children()
        )
        if self.console.active or other_dialog_open:
            messagebox.showinfo('保存先を変更', 'PowerShellを終了し、別の画面を閉じてから変更してください。')
            return
        project, registration = self.project, self.current
        old = project.base.resolve()
        folder = filedialog.askdirectory(
            parent=self.window,
            title='移動済みのプロジェクトフォルダーを選択',
            initialdir=str(old if old.is_dir() else old.parent),
        )
        if not folder or Path(folder).resolve() == old:
            return
        if not messagebox.askyesno(
            'プロジェクトの保存先を変更',
            f'{project.name}\n\n現在：{old}\n新しい場所：{folder}\n\n'
            'history.git が入っている、移動またはコピー済みのフォルダーを選んでください。'
            '移動先の記録とメモを新しいパスに付け替えます。',
            parent=self.window, default='no',
        ):
            return

        def done(result):
            base, work = result
            before = registration.copy()
            registration.update(base=str(base), work=work)
            try:
                self.save()
            except OSError as exc:
                registration.clear()
                registration.update(before)
                messagebox.showinfo('保存先を切り替えられませんでした',
                                    f'{exc}\n設定を保存できませんでした。同じ移動先を選んでやり直してください。')
                return
            self.select_project()

        self.run(
            '移動先の記録とメモを確認しています…',
            lambda: relocate_project(project, folder, registration.get('work', []), project.other_bases),
            done,
        )

    def add(self):
        if self.busy: return
        if not self.config['root']:
            self.choose_root()
        if not self.config['root']: return
        url = simpledialog.askstring('プロジェクトを追加', 'GitHubのプロジェクトURL')
        if not url: return
        try:
            project = Project(self.config['root'], url)
        except ValueError as exc:
            messagebox.showinfo('URLを確認', str(exc)); return
        if not any(p['url'] == project.url for p in self.config['projects']):
            self.config['projects'].append({'url': project.url, 'work': []})
            self.save()
            self.populate_projects()
        index = next(i for i,p in enumerate(self.config['projects']) if p['url'] == project.url)
        self.projects.selection_clear(0, 'end')
        self.projects.selection_set(index)
        self.select_project()

    def populate_projects(self):
        self.projects.delete(0, 'end')
        for p in self.config['projects']:
            self.projects.insert('end', p['url'].removesuffix('.git').split('/')[-1])

    def select_project(self, event=None):
        if self.busy: return
        indices = self.projects.curselection()
        if not indices: return
        self.folder_browser.set_folder(None)
        self.explanation.choose(None)
        self.set_action_guide('default')
        self.current = self.config['projects'][indices[0]]
        self.project = Project.registered(self.config['root'], self.current)
        self.project.other_bases = tuple(
            Project.registered(self.config['root'], item).base.resolve()
            for item in self.config['projects'] if item is not self.current
        )
        self.records = {}
        self.history_rows = []
        self.history_model = HistoryGraph([])
        self.sort_options['history'] = ('topology', False)
        self.history_page = 0
        self.history_query.set('')
        self.render_history()
        self.notes = None
        try: self.notes = Notes(self.project.base)
        except (OSError, ValueError) as exc: messagebox.showinfo('自分用メモを読み込めませんでした', str(exc))
        self.load_folders()
        self.refresh()

    def run(self, description, task, callback):
        if self.busy: return
        self.busy = True
        if hasattr(self,'mascot_view'):self.mascot_view.begin('app',operation_pose(description))
        self.projects.configure(state='disabled')
        self.operation_state.set('処理中')
        self.operation_label = description
        self.operation_note = ''
        self.operation_started = time.monotonic()
        self.activity_bar.start(40)
        self.status.set(description)
        def worker():
            try: self.events.put((callback, task(), None))
            except Exception as exc: self.events.put((callback, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            callback, result, error = self.events.get_nowait()
            self.busy = False
            if hasattr(self,'mascot_view'):self.mascot_view.end('app')
            self.projects.configure(state='normal')
            self.operation_state.set('待機中')
            self.activity_bar.stop()
            if error:
                self.status.set('操作を完了できませんでした。案内を確認してください。')
                messagebox.showinfo('状況の確認', error)
                if hasattr(self,'mascot_view'):self.mascot_view.end('app',True)
            else:
                self.status.set('完了しました。')
                callback(result)
        except queue.Empty:
            if self.busy:
                note = self.operation_note[-120:]
                self.status.set(f'{self.operation_label}  {int(time.monotonic() - self.operation_started)} 秒  {note}')
        self.window.after(100, self.poll)

    def refresh_shortcut(self,event=None):
        if (self.console.active or self.console.visible or self.busy
                or not self.project or self.window.grab_current() is not None):
            return 'break'
        if event is not None and event.widget.winfo_toplevel() != self.window:
            return 'break'
        self.refresh()
        return 'break'

    def refresh(self):
        if self.project and not self.busy:
            self.run('GitHubの履歴を確認しています…', self.project.refresh, self.show_history)

    def show_history(self, records):
        previous = self.history.selection()
        self.history_model = HistoryGraph(records)
        if self.notes: self.notes.reload()
        self.records = {r['sha']: r for r in records}
        self.history_rows = []
        for index, r in enumerate(records):
            memo = self.notes.get('history', r['sha']) if self.notes else ''
            date, timestamp = date_value(r['date'])
            merge = '（合流）' if len(r['parents'].split()) > 1 else ''
            self.history_rows.append({
                'id': r['sha'], 'text': r['sha'][:8] + merge,
                'values': (date, r['refs'], r['message'], short_note(memo)),
                'search': ' '.join((r['sha'], r['parents'], date, r['date'], r['refs'], r['message'], memo)),
                'sort': {'topology': index, '#0': r['sha'], 'date': timestamp, 'refs': r['refs'].casefold(), 'message': r['message'].casefold(), 'memo': memo.casefold()}})
        self.render_history(reset_page=False, focus_sha=previous[0] if previous and previous[0] in self.records else None)
        self.load_folders()
        self.status.set(f'全 {len(records)} 件の履歴を取得しました。検索は自分用メモを含む全履歴が対象です。')

    def detail(self, event=None):
        items = self.history.selection()
        self.history.graph_view.set_focus(items[0] if items else None)
        if items:
            self.set_action_guide('history')
            r = self.records[items[0]]
            subject = r['message'][:100] + ('…' if len(r['message']) > 100 else '')
            parents = '・'.join(value[:8] for value in r['parents'].split()) or '最初の履歴'
            self.selection.set(subject + '\n前につながる履歴: ' + parents)
            if hasattr(self, 'explanation'):
                self.explanation.choose({'kind':'history','project':self.project,'sha':items[0], 'label':self.project.name+' / '+items[0][:8]+' '+subject})

    def menu(self, event):
        item = self.history.identify_row(event.y)
        if not item: return
        self.history.selection_set(item)
        menu = tk.Menu(self.window, tearoff=False)
        menu.add_command(label='この時点のファイルをPCへコピー', command=self.reproduce)
        menu.add_command(label='作業フォルダーと比較', command=self.compare)
        menu.add_command(label='別の作業ルートと比較・取り込み提案',command=lambda:open_route_review(self))
        menu.add_command(label='自分用メモを編集', command=self.edit_history_note)
        menu.add_command(label='選んだフォルダーをこの版に更新', command=self.update_selected)
        self.terms.refresh(menu)
        menu.tk_popup(event.x_root, event.y_root)

    def edit_history_note(self):
        items = self.history.selection()
        if items and self.project:
            self.note_dialog('history', items[0], '履歴 ' + items[0][:12])

    def edit_folder_note(self):
        items = self.folders.selection()
        if len(items)>1:
            messagebox.showinfo('フォルダーを1つ選択','この操作ではフォルダーを1つ選んでください。');return
        if items and self.project:
            folder = self.folders.item(items[0], 'values')[1]
            self.note_dialog('folders', folder, folder)

    def note_dialog(self, scope, identity, title):
        if self.busy: return
        try: notes = Notes(self.project.base)
        except (OSError, ValueError) as exc:
            messagebox.showinfo('自分用メモを読み込めませんでした', str(exc))
            return
        popup = tk.Toplevel(self.window)
        popup.title('自分用メモ — ' + title)
        popup.geometry('800x480')
        popup.minsize(560, 350)
        popup.transient(self.window)
        popup.columnconfigure(0, weight=1)
        popup.rowconfigure(1, weight=1)
        ttk.Label(popup, text=title + '\nこのPCだけのメモです。GitHubへの送信やAIの説明対象には含めません。',
                  wraplength=530, padding=12).grid(row=0, column=0, sticky='ew')
        frame = ttk.Frame(popup, padding=12)
        frame.grid(row=1, column=0, sticky='nsew')
        editor = tk.Text(frame, width=1, height=1, wrap='word', undo=True)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=editor.yview)
        editor.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        editor.pack(fill='both', expand=True)
        editor.insert('1.0', notes.get(scope, identity))
        buttons = ttk.Frame(popup, padding=12)
        buttons.grid(row=2, column=0, sticky='ew')
        def save():
            try:
                notes.set(scope, identity, editor.get('1.0', 'end-1c'))
                self.notes = notes
                self.show_history(list(self.records.values()))
            except (OSError, ValueError) as exc:
                messagebox.showinfo('メモを保存できませんでした', str(exc), parent=popup)
                return
            popup.destroy()
        ttk.Button(buttons, text='保存', command=save, style='Primary.TButton').pack(side='right', padx=4)
        ttk.Button(buttons, text='キャンセル', command=popup.destroy).pack(side='right', padx=4)
        ttk.Label(buttons, text='空欄にして保存するとメモを削除します。', style='Muted.TLabel').pack(side='left')
        popup.grab_set()
        editor.focus_set()

    def vscode_selected(self):
        self.with_selected_folder(self.vscode_folder)

    def vscode_folder(self, folder):
        executable = self.config.get('vscode_executable')
        if not executable or not Path(executable).is_file():
            executable = find_vscode()
        if not executable:
            if not messagebox.askyesno('VS Codeが見つかりません', 'VS Codeの実行ファイル（Code.exe）を選びますか？\nインストールしていない場合もGitTotteは引き続き使えます。'):
                return
            executable = filedialog.askopenfilename(title='VS Codeの実行ファイルを選択',
                         filetypes=[('VS Code', 'Code.exe'), ('実行ファイル', '*.exe')])
            if not executable: return
        try:
            open_vscode(folder, executable)
            self.config['vscode_executable'] = executable
            self.save()
        except (OSError, ValueError) as exc:
            messagebox.showinfo('VS Codeを開けませんでした', str(exc))

    def work_update_permission(self):
        if self.busy: return
        items = self.folders.selection()
        if len(items)>1:
            messagebox.showinfo('フォルダーを1つ選択','この操作ではフォルダーを1つ選んでください。');return
        if not items: return
        folder = self.folders.item(items[0], 'values')[1]
        _, record = record_for_folder(self.project, folder)
        if record and record.get('kind') == 'work' and record.get('update_allowed'):
            if messagebox.askyesno('同じ場所への更新を無効にする', folder + '\nこのフォルダーの入れ替えを無効にしますか？比較・送信は引き続き使えます。'):
                disable_update(self.project, folder)
                self.load_folders()
            return
        sha = next(iter(self.records), None)
        if not sha:
            messagebox.showinfo('履歴を取得', '先に「GitHubの履歴を更新」で履歴を取得してください。')
            return
        project = self.project
        def done(plan):
            if not messagebox.askyesno('作業フォルダーの更新を有効にする',
                    '対象: ' + str(plan.folder) + '\n作成元: ' + plan.baseline[:12] +
                    '\n今後、確認画面からこの場所のファイルを入れ替えられるようにしますか？\n有効にするだけではファイルを変更しません。'):
                return
            def saved(_):
                self.load_folders()
                self.status.set('この作業フォルダーの更新を有効にしました: ' + str(plan.folder))
            self.run('内容を再確認して更新の許可を保存しています…', lambda: authorize_update(project, plan), saved)
        self.run('作業フォルダーの作成元を確認しています…',
                 lambda: inspect_update(project, folder, sha, lambda note: setattr(self, 'operation_note', note), for_authorization=True), done)

    def send_selected_folder(self):
        if self.busy or not self.project: return
        items = self.folders.selection()
        if len(items)>1:
            messagebox.showinfo('フォルダーを1つ選択','この操作ではフォルダーを1つ選んでください。');return
        if not items:
            messagebox.showinfo('送信元を選択', 'PCのフォルダーを選んでください。')
            return
        self.push_ui.begin(self.folders.item(items[0], 'values')[1])

    def cancel_drag(self, event=None):
        if not self.dragging: return
        self.dragging = None
        self.drag_hint.hide()
        self.history.configure(cursor='')
        self.folders.configure(cursor='')
        self.status.set('ドラッグを取り消しました。')
        return 'break'

    def folder_press(self, event):
        self.dragging = None
        if self.busy or not self.project or self.folders.identify_region(event.x, event.y) != 'cell': return
        item = self.folders.identify_row(event.y)
        if getattr(event,'state',0) & 0x5:return
        if item:
            multiple=len(self.folders.selection())>1 and item in self.folders.selection()
            self.dragging = {'multiple':multiple,'item':item,'direction': 'push', 'folder': self.folders.item(item, 'values')[1],
                             'project': self.project, 'x': event.x_root, 'y': event.y_root, 'active': False}
            if multiple:return 'break'

    def folder_motion(self, event):
        drag = self.dragging
        if not drag or drag.get('direction') != 'push': return
        if abs(event.x_root - drag['x']) + abs(event.y_root - drag['y']) < 8: return
        drag['active'] = True
        if drag.get('multiple') or len(self.folders.selection())>1:
            self.drag_hint.show(event.x_root,event.y_root,'送るフォルダーを1つ選んでください','複数のフォルダーは送信できません',allowed=False)
            self.status.set('送るフォルダーを1つ選んでください。Ctrlで追加・解除、Shiftで範囲選択できます。')
            return 'break'
        self.folders.configure(cursor='arrow')
        target = self.window.winfo_containing(event.x_root, event.y_root)
        graph_canvas = getattr(getattr(self.history, 'graph_view', None), 'canvas', None)
        if target is self.history or target is graph_canvas:
            y = event.y_root - self.history.winfo_rooty()
            item = self.history.identify_row(y)
            if item: self.history.selection_set(item)
            valid = self.history.identify_region(5, y) not in ('heading', 'separator')
            self.drag_hint.show(event.x_root, event.y_root, 'GitHubへ送る内容を確認' if valid else 'ここには置けません', Path(drag['folder']).name, allowed=valid, action='↑')
            self.status.set('ここで離すと、送信する変更と送信先を確認します。まだGitHubへは送りません。')
        else:
            self.drag_hint.show(event.x_root, event.y_root, 'ここには置けません', Path(drag['folder']).name, allowed=False)
            self.status.set('GitHubの履歴へドラッグすると、送信する変更を確認できます。')

    def folder_release(self, event):
        drag = self.dragging
        self.dragging = None
        self.folders.configure(cursor='')
        if getattr(self, 'drag_hint', None): self.drag_hint.hide()
        if drag and drag.get('multiple') and not drag['active']:
            if self.folders.exists(drag['item']):self.folders.selection_set(drag['item']);self.folders.focus(drag['item'])
            return 'break'
        if drag and drag.get('direction')=='push' and (drag.get('multiple') or len(self.folders.selection())>1):
            self.status.set('送るフォルダーを1つ選んでください。複数選択中は送信しません。')
            return 'break'
        if not drag or drag.get('direction') != 'push' or not drag['active'] or self.busy or self.project is not drag['project']: return
        target = self.window.winfo_containing(event.x_root, event.y_root)
        graph_canvas = getattr(getattr(self.history, 'graph_view', None), 'canvas', None)
        if target is not self.history and target is not graph_canvas: return
        x = 5 if target is graph_canvas else event.x_root - self.history.winfo_rootx()
        y = event.y_root - self.history.winfo_rooty()
        if self.history.identify_region(x, y) in ('heading', 'separator'): return
        item = self.history.identify_row(y)
        self.push_ui.begin(drag['folder'], item or None)

    def history_press(self, event):
        self.dragging = None
        canvas = self.history.graph_view.canvas
        x = 5 if getattr(event, 'widget', None) is canvas else event.x
        if self.busy or not self.project or self.history.identify_region(x, event.y) not in ('tree', 'cell'):
            return
        item = self.history.identify_row(event.y)
        if item:
            self.history.selection_set(item)
            self.dragging = {'sha': item, 'project': self.project, 'x': event.x_root,
                             'y': event.y_root, 'active': False}

    def history_motion(self, event):
        drag = self.dragging
        if not drag: return
        if abs(event.x_root - drag['x']) + abs(event.y_root - drag['y']) < 8: return
        drag['active'] = True
        self.history.configure(cursor='arrow')
        target = self.window.winfo_containing(event.x_root, event.y_root)
        if target is self.folders:
            y = event.y_root - self.folders.winfo_rooty()
            item = self.folders.identify_row(y)
            if item: self.folders.selection_set(item)
            now = time.monotonic()
            if now - getattr(self, 'drag_scroll_at', 0) > 0.15:
                if y < 40: self.folders.yview_scroll(-1, 'units')
                elif y > self.folders.winfo_height() - 25: self.folders.yview_scroll(1, 'units')
                self.drag_scroll_at = now
            valid = self.folders.identify_region(event.x_root - self.folders.winfo_rootx(), y) not in ('heading', 'separator')
            self.drag_hint.show(event.x_root, event.y_root, ('このフォルダーを更新' if item else '新しくコピー') if valid else 'ここには置けません',
                                '履歴 ' + drag['sha'][:12], allowed=valid, action='↻' if item else '+')
            self.status.set('ここで離すと、このフォルダーへのGitHubの履歴を更新します。' if item else 'ここで離すと、新しいコピーフォルダーを作ります。')
        else:
            self.drag_hint.show(event.x_root, event.y_root, 'ここには置けません', '履歴 ' + drag['sha'][:12], allowed=False)
            self.status.set('PCのフォルダーの行へドラッグで更新、一覧の空白へドラッグで新規コピー。')

    def history_release(self, event):
        drag = self.dragging
        self.dragging = None
        self.history.configure(cursor='')
        if getattr(self, 'drag_hint', None): self.drag_hint.hide()
        if not drag or not drag['active'] or self.busy or self.project is not drag['project']:
            return
        target = self.window.winfo_containing(event.x_root, event.y_root)
        if target is not self.folders:
            self.status.set('ドラッグを取り消しました。')
            return
        x = event.x_root - self.folders.winfo_rootx()
        y = event.y_root - self.folders.winfo_rooty()
        if self.folders.identify_region(x, y) in ('heading', 'separator'):
            self.status.set('列見出しにはドロップできません。')
            return
        item = self.folders.identify_row(y)
        if item:
            self.folders.selection_set(item)
            self.begin_update(drag['sha'], item)
        else:
            self.reproduce_sha(drag['sha'])

    def reproduce_sha(self, sha):
        if self.busy or not self.project: return
        project = self.project
        ticket=guide_begin(self,'copy',project)
        def done(folder):
            self.folder_query.set('')
            self.load_folders()
            for item, record in self.folder_records.items():
                if record['folder'] == folder:
                    self.folders.selection_set(item)
                    self.folders.see(item)
                    break
            guide_finish(ticket,'選んだ履歴のファイルを新しいフォルダーへコピーしました。\n'+folder)
            messagebox.showinfo('コピーしました', '新しいフォルダーに保存しました。\n' + folder)
        self.run('新しいフォルダーへコピーしています…', lambda: project.snapshot(sha), done)

    def update_selected(self):
        history = self.history.selection()
        folders = self.folders.selection()
        if not history or not folders:
            messagebox.showinfo('更新元と更新先を選択', '上で履歴、下で更新先のフォルダーを選んでください。')
            return
        if len(folders)>1:
            messagebox.showinfo('更新先を1つ選択','更新するフォルダーを1つ選んでください。');return
        self.begin_update(history[0], folders[0])

    def begin_update(self, sha, item):
        if self.busy or not self.project: return
        folder = self.folders.item(item, 'values')[1]
        _,origin=record_for_folder(self.project,folder)
        if origin and origin.get('kind')=='restored' and not origin.get('update_allowed',False):
            messagebox.showinfo('復元した内容を保護しています','復元先は上書き更新が無効です。別フォルダーへのコピーを使うか、作業フォルダーとして登録してから更新を有効にしてください。');return
        if not Path(folder).is_dir():
            if messagebox.askyesno('フォルダーが見つかりません',
                    '更新先が削除、または移動されています。\n選んだ履歴から別フォルダーへ新しくコピーしますか？\n' + folder):
                self.reproduce_sha(sha)
            return
        project = self.project
        def progress(note): self.operation_note = note
        def inspect():
            try: return inspect_update(project, folder, sha, progress)
            except UpdateNotAllowed as exc: return str(exc)
        def done(plan):
            if isinstance(plan, str):
                if messagebox.askyesno('この場所は入れ替えません', plan + '\n\n選んだ版を別の新しいフォルダーへコピーしますか？'):
                    self.reproduce_sha(sha)
                return
            self.update_dialog(project, plan)
        self.run('手元の変更を確認しています…', inspect, done)

    def update_dialog(self, project, plan):
        popup = tk.Toplevel(self.window)
        popup.title('このフォルダーを選んだ版に更新')
        popup.geometry('880x720')
        popup.minsize(680, 650)
        popup.transient(self.window)
        popup.columnconfigure(0, weight=1)
        popup.rowconfigure(3, weight=1)
        ttk.Label(popup, text='更新先: ' + str(plan.folder), wraplength=640, padding=12).grid(row=0, column=0, sticky='ew')
        ttk.Label(popup, text='選んだ履歴: ' + plan.sha[:12] + '\n' +
                  ('もとにした履歴が不明です。現在の内容をバックアップしてからの更新をおすすめします。' if plan.unknown else
                   f'手元の変更・追加など: {len(plan.differences)} 件（もとにした履歴 {plan.baseline[:8]} との比較）'),
                  padding=(12, 0), wraplength=640).grid(row=1, column=0, sticky='ew')
        ttk.Label(popup, text='元の版にあるファイルは選んだ版へ更新し、そのファイルへの手元の編集は引き継ぎません。\n'
                  '手元だけの追加ファイルは下の選択で残せます。Git管理情報は入れ替えます。関連アプリを閉じてください。',
                  wraplength=640, padding=12).grid(row=2, column=0, sticky='ew')
        table = self.table(popup, ('state', 'path'), ('確認内容', 'ファイル／場所'), grid_row=3)
        for row in plan.differences[:1000]: table.insert('', 'end', values=row)
        if len(plan.differences) > 1000: table.insert('', 'end', values=('表示は先頭1000件までです', ''))
        if not plan.differences: table.insert('', 'end', values=('ファイル内容に手元の変更はありません', ''))
        options = ttk.Frame(popup, padding=12)
        options.grid(row=4, column=0, sticky='ew')
        retain = tk.BooleanVar(value=not plan.unknown)
        checkbox = ttk.Checkbutton(options, text=f'手元だけのファイルを残す（{len(plan.local_files)} 件）', variable=retain)
        checkbox.pack(anchor='w')
        if plan.unknown:
            checkbox.configure(state='disabled')
            text = 'もとにした履歴が不明なため、手元だけのファイルを判別できません。別フォルダーへのコピーか、バックアップしての入れ替えを選んでください。'
        elif plan.collisions:
            text = f'選んだ版との衝突が {len(plan.collisions)} 件あります。残す設定のままでは更新せずに案内します。'
        else:
            text = '残す対象には無視対象の設定・DB・実行環境も含みます。元の版で管理されていたファイルは対象外です。'
        ttk.Label(options, text=text, wraplength=640, style='Muted.TLabel').pack(anchor='w', pady=4)
        ttk.Label(options, text='「バックアップ」は更新前の全体をZIPにまとめます。自分の編集を新しいコードへ混ぜる操作ではありません。',
                  wraplength=640, style='Muted.TLabel').pack(anchor='w')
        buttons = ttk.Frame(popup, padding=12)
        buttons.grid(row=5, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        def execute(keep):
            preserve = retain.get()
            if preserve and plan.collisions:
                messagebox.showinfo('手元のファイルと衝突しています',
                    '同じ場所に選んだ版のファイルがあるため、更新を止めています。\n' +
                    '\n'.join(plan.collisions[:20]) +
                    '\n\n別フォルダーにコピーするか、手元のファイルを別の場所に移してから確認し直してください。', parent=popup)
                return
            if not keep:
                detail = ('手元だけのファイルは残しますが、元の版にあるファイルへの編集とGit管理情報は元に戻せません。'
                          if preserve else '手元の変更・追加ファイル・Git管理情報は元に戻せません。')
                if not messagebox.askyesno('バックアップせずに更新しますか？',
                        '更新前の内容をバックアップせずに更新します。\n' + detail + '\n' + str(plan.folder), parent=popup):
                    return
            elif not preserve and not messagebox.askyesno('追加ファイルも入れ替えます',
                    '「手元だけのファイルを残す」がOFFです。\n追加ファイルも更新先から取り除きます（バックアップZIPには残ります）。\n続けますか？', parent=popup):
                return
            popup.destroy()
            ticket=guide_begin(self,'update',project)
            def progress(note): self.operation_note = note
            def done(result):
                self.folder_query.set('')
                self.load_folders()
                detail = '同じ場所を選んだ版に更新しました。\n' + result['folder']
                detail += f"\n手元だけのファイルを引き継ぎ: {result['retained']} 件"
                if result['backup']: detail += '\n\n更新前の内容: ' + result['backup']
                if result['warning']: detail += '\n\n' + result['warning']
                guide_finish(ticket,'選んだフォルダーを更新しました。\n'+result['folder'])
                messagebox.showinfo('更新しました', detail)
            self.run('選んだ版に更新しています…', lambda: apply_update(project, plan, keep, progress, preserve_local_only=preserve), done)
        def separate():
            popup.destroy()
            self.reproduce_sha(plan.sha)
        ttk.Button(buttons, text='バックアップして更新', command=lambda: execute(True), style='Primary.TButton').grid(row=0, column=0, sticky='ew', padx=4, pady=4)
        ttk.Button(buttons, text='編集内容を取り消して更新（バックアップなし）', command=lambda: execute(False), style='Danger.TButton').grid(row=0, column=1, sticky='ew', padx=4, pady=4)
        ttk.Button(buttons, text='別フォルダーにコピー', command=separate).grid(row=1, column=0, sticky='ew', padx=4, pady=4)
        ttk.Button(buttons, text='キャンセル', command=popup.destroy).grid(row=1, column=1, sticky='ew', padx=4, pady=4)
        guide_handoff(self,popup,'update','更新先と手元の変更を確認してください。残すファイルとバックアップの有無を選び、更新ボタンを押す前に確認します。')
        popup.grab_set()

    def backups_dialog(self):
        if not self.project: return
        project = self.project
        directory = project.base / 'backups'
        popup = tk.Toplevel(self.window)
        popup.title('更新前のバックアップ — ' + project.name)
        popup.geometry('950x550')
        ttk.Label(popup, text='更新前の全ファイルをZIPに保管しています。「新しいフォルダーへ復元」で取り出せます。元の作業フォルダーは変更しません。', padding=12).pack(anchor='w')
        controls = ttk.Frame(popup, padding=12)
        controls.pack(side='bottom', fill='x')
        table = self.table(popup, ('created', 'folder', 'size'), ('バックアップ日時', '元のフォルダー', 'サイズ'))
        items = {}
        def reload():
            table.delete(*table.get_children())
            items.clear()
            for archive in sorted(directory.glob('*.zip'), reverse=True):
                meta = archive.with_suffix('.json')
                try:
                    record = json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}
                    size = archive.stat().st_size
                except (OSError, ValueError):
                    continue
                item = table.insert('', 'end', values=(date_value(record.get('created'))[0], record.get('folder', archive.name), f'{size / (1024*1024):.1f} MB'))
                items[item] = archive
        def chosen():
            selection = table.selection()
            return items.get(selection[0]) if selection else None
        def open_archive():
            path = chosen()
            if not path: return
            self.open_folder_path(str(path))
        def delete():
            if self.busy:return
            path = chosen()
            if not path: return
            if not messagebox.askyesno('更新前のバックアップを削除', 'このZIPを削除しますか？\n更新前の手元の変更は、このZIPから復元できなくなります。\n' + str(path), parent=popup): return
            try:
                path.unlink()
                path.with_suffix('.json').unlink(missing_ok=True)
            except OSError as exc:
                messagebox.showinfo('削除できませんでした', str(exc), parent=popup)
            reload()
        def restore():
            if self.busy:return
            path=chosen()
            if not path:
                messagebox.showinfo('バックアップを選択','一覧から復元するバックアップを1つ選んでください。',parent=popup);return
            start_restore(self,project,lambda:prepare_backup(project,path),popup)
        ttk.Button(controls,text='新しいフォルダーへ復元',command=restore).pack(side='left',padx=4)
        ttk.Button(controls, text='ZIPを開く', command=open_archive).pack(side='left', padx=4)
        ttk.Button(controls, text='選んだバックアップを削除', command=delete).pack(side='left', padx=4)
        ttk.Button(controls, text='一覧を再表示', command=reload).pack(side='left', padx=4)
        reload()
        guide_handoff(self,popup,'restore-backup','バックアップを1つ選び、「新しいフォルダーへ復元」を押してください。保存場所の確認後にコピーを始めます。')
        return popup

    def reproduce(self):
        items = self.history.selection()
        if self.busy or not items: return
        self.reproduce_sha(items[0])

    @staticmethod
    def folder_state(folder):
        try:
            path = Path(folder)
            if path.is_dir(): return '利用できます'
            if path.exists(): return 'フォルダーではありません'
            return '削除または移動されています'
        except OSError:
            return 'アクセスできません'

    def on_window_focus(self, event):
        if event.widget is self.window:
            self.check_folder_tab()

    def check_folder_tab(self, event=None):
        if self.project and not self.busy and not self.dragging:
            self.load_folders()

    def load_folders(self):
        if self.notes: self.notes.reload()
        self.folder_records = {}
        self.folder_rows = []
        def add(item, kind, folder, sha='', created=''):
            memo = self.notes.get('folders', folder) if self.notes else ''
            state = self.folder_state(folder)
            if kind == '作業用（登録）' and state == '利用できます':
                _, version = record_for_folder(self.project, folder)
                state += '（上書き更新はON）' if version and version.get('update_allowed') else '（上書き更新はOFF）'
            if kind in ('バックアップから復元','PCの記録から復元') and state=='利用できます':state+='（上書き更新はOFF）'
            date, timestamp = date_value(created)
            self.folder_rows.append({
                'id': item, 'pinned': kind == '作業用（登録）', 'values': (kind, folder, sha[:8], date, state, short_note(memo)),
                'search': ' '.join((kind, folder, sha, date, created, state, memo)),
                'sort': {'kind': kind.casefold(), 'path': folder.casefold(), 'sha': sha,
                         'created': timestamp, 'state': state, 'memo': memo.casefold()}})
        versions = {str(Path(r['folder']).resolve()): r for r in self.project.folder_versions() if not r.get('hidden_from_list',False)}
        for index, folder in enumerate(self.current.get('work', [])):
            record = versions.get(str(Path(folder).resolve()), {})
            add('work:' + str(index), '作業用（登録）', folder, record.get('sha', ''), record.get('created', ''))
        for record in self.project.snapshots():
            item = 'snapshot:' + record['folder']
            label={'backup':'バックアップから復元','local':'PCの記録から復元'}.get(record.get('restore_kind'),'コピーしたフォルダー')
            display=record.get('restore_commit') or record['sha']
            add(item,label,record['folder'],display,record.get('created',''))
            self.folder_records[item] = record
        self.render_folders()

    def with_selected_folder(self, action):
        if self.busy: return
        items = self.folders.selection()
        if not items:
            messagebox.showinfo('フォルダーを選択', 'PCのフォルダー一覧から、作業場所を選んでください。')
            return
        if len(items)>1:
            messagebox.showinfo('フォルダーを1つ選択','この操作ではフォルダーを1つ選んでください。');return
        item = items[0]
        folder = self.folders.item(item, 'values')[1]
        state = self.folder_state(folder)
        display_state = state
        if state == '利用できます' and self.folders.item(item, 'values')[0] == '作業用（登録）':
            _, version = record_for_folder(self.project, folder)
            display_state += '（上書き更新はON）' if version and version.get('update_allowed') else '（上書き更新はOFF）'
        if self.folders.item(item,'values')[0] in ('バックアップから復元','PCの記録から復元') and state=='利用できます':display_state+='（上書き更新はOFF）'
        self.folders.set(item, 'state', display_state)
        if state == '利用できます':
            action(folder)
            return
        if state != '削除または移動されています':
            messagebox.showinfo('作業場所を確認', state + '\n' + folder)
            return
        record = self.folder_records.get(item)
        if not record:
            messagebox.showinfo('作業場所が見つかりません',
                                'フォルダーが削除、または移動されています。\n' + folder +
                                '\n\n登録した作業用フォルダーは自動で再作成しません。移動先を登録するか、上の履歴から新しくコピーしてください。')
            return
        if record.get('kind')=='restored':
            project=self.project
            start_restore(self,project,lambda:prepare_again(project,record),after=action,notice='同じ復元元から新しく作成します。復元後に加えた編集は、復元元に記録されていなければ戻りません。')
            return
        project, sha = self.project, record['sha']
        if not messagebox.askyesno('コピーフォルダーが見つかりません',
                'フォルダーが削除、または移動されています。\n' + folder +
                '\n\n同じ履歴（' + sha[:8] + '）から、新しいフォルダーにコピーしますか？' +
                '\nコピー後に手元で加えた変更は復元されません。'):
            return
        def done(new_folder):
            self.folder_query.set('')
            self.load_folders()
            for new_item, new_record in self.folder_records.items():
                if new_record['folder'] == new_folder:
                    self.folders.selection_set(new_item)
                    self.folders.see(new_item)
                    break
            self.status.set('同じ履歴から新しくコピーしました: ' + new_folder)
            messagebox.showinfo('コピーしました', '新しいフォルダーを作成しました。\n' + new_folder)
            action(new_folder)
        self.run('同じ履歴から新しいフォルダーへコピーしています…', lambda: project.snapshot(sha), done)

    def register_folder(self):
        if self.busy or not self.project: return
        folder = filedialog.askdirectory(title='作業フォルダーを登録（移動・変更しません）')
        if folder and folder not in self.current['work']:
            self.current['work'].append(folder)
            self.save()
            self.load_folders()

    def open_folder(self):
        self.with_selected_folder(self.open_folder_path)

    def open_folder_path(self, path):
        try:
            if os.name == 'nt': os.startfile(path)
            else: subprocess.Popen(['xdg-open', path])
        except OSError as exc:
            messagebox.showinfo('フォルダーを開けませんでした', str(exc))

    def recycle_selected_folder(self):
        remove_selected(self,'recycle')

    def remove_missing_folder(self):
        remove_selected(self,'hide')

    def folder_menu(self, event):
        item = self.folders.identify_row(event.y)
        if not item: return
        if item not in self.folders.selection():self.folders.selection_set(item)
        self.folders.focus(item)
        single=len(self.folders.selection())==1
        mode='normal' if single else 'disabled'
        menu = tk.Menu(self.window, tearoff=False)
        menu.add_command(label='フォルダーを開く', command=self.open_folder,state=mode)
        menu.add_command(label='VS Codeで開く', command=self.vscode_selected,state=mode)
        menu.add_command(label='自分用メモを編集', command=self.edit_folder_note,state=mode)
        if single and item.startswith('work:'):
            folder = self.folders.item(item, 'values')[1]
            _, record = record_for_folder(self.project, folder)
            enabled = record and record.get('kind') == 'work' and record.get('update_allowed')
            menu.add_command(label='同じ場所への更新を無効にする' if enabled else 'この作業フォルダーの更新を有効にする', command=self.work_update_permission)
        menu.add_command(label='PowerShellで開く／コマンドを実行', command=self.command_dialog,state=mode)
        menu.add_command(label='PCに記録だけ残す',command=lambda:self.with_selected_folder(lambda folder:self.push_ui.begin(folder,local_only=True)),state=mode)
        menu.add_command(label='PCの記録を見る',command=lambda:show_records(self))
        menu.add_command(label='変更を確認してGitHubへ送る', command=self.send_selected_folder,state=mode)
        menu.add_separator()
        states=[self.folder_state(self.folders.item(selected,'values')[1]) for selected in self.folders.selection()]
        menu.add_command(label='フォルダーをごみ箱へ移動',command=self.recycle_selected_folder,state='normal' if '利用できます' in states else 'disabled')
        menu.add_command(label='見つからない項目を一覧から取り除く',command=self.remove_missing_folder,state='normal' if '削除または移動されています' in states else 'disabled')
        try:
            self.terms.refresh(menu)
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def command_dialog(self):
        self.with_selected_folder(self.command_dialog_for_folder)

    def command_dialog_for_folder(self, folder):
        try:self.console.open(folder)
        except Exception as exc:messagebox.showerror('PowerShellを開けませんでした',str(exc),parent=self.window)

    def toggle_console(self):
        if self.console.visible:self.console.hide()
        elif self.console.session:self.console.show()
        else:self.command_dialog()

    def close_app(self):
        if self.console.active:
            if not messagebox.askyesno('PowerShellも終了しますか？',
                'PowerShellと、この画面から起動した処理を終了してGitTotteを閉じますか？',parent=self.window):return
            self.console.closing=True
            self.console.session.stop()
            self.status.set('PowerShellの終了を待っています…')
            self._finish_close(0)
        else:self.window.destroy()

    def _finish_close(self,attempt):
        if not self.console.active:self.window.destroy();return
        if attempt>=100:
            self.console.closing=False
            messagebox.showwarning('終了を確認してください','PowerShellの終了をまだ確認できません。パネルの状態を確認してから、もう一度閉じてください。',parent=self.window)
            self.console.show();return
        self.window.after(50,lambda:self._finish_close(attempt+1))

    def compare(self):
        items = self.history.selection()
        if self.busy or not items: return
        selected = self.folders.selection()
        if len(selected)>1:
            messagebox.showinfo('比較するフォルダーを1つ選択','PCのフォルダー一覧から、比較するフォルダーを1つ選んでください。')
            return
        folder = self.folders.item(selected[0],'values')[1] if selected else filedialog.askdirectory(title='選んだ履歴と比較するフォルダー')
        if not folder: return
        if not Path(folder).is_dir():
            messagebox.showinfo('比較するフォルダーが見つかりません','フォルダーが削除、または移動されています。保存場所を確認してください。\n'+str(folder))
            return
        self.compare_at(self.project,items[0],folder)

    def compare_at(self, project, sha, folder):
        ticket=guide_begin(self,'compare',project)
        cancel = threading.Event()
        lock = threading.Lock()
        state = {'stage': '比較の準備', 'count': 0, 'total': None, 'name': '', 'finished': False}
        started = time.monotonic()
        progress_window = tk.Toplevel(self.window)
        progress_window.title('ファイルを比較しています')
        progress_window.geometry('650x240')
        progress_window.minsize(500, 240)
        progress_window.columnconfigure(0, weight=1)
        progress_window.rowconfigure(2, weight=1)
        label = tk.StringVar()
        ttk.Label(progress_window, textvariable=label, padding=12, wraplength=470).grid(row=0, column=0, sticky='ew')
        progress_window.title('ファイルを比較しています — '+str(folder))
        bar = ttk.Progressbar(progress_window, mode='indeterminate')
        bar.grid(row=1, column=0, sticky='ew', padx=12, pady=8)
        bar.start(40)
        activity = tk.StringVar()
        ttk.Label(progress_window, textvariable=activity, padding=12, wraplength=470).grid(row=2, column=0, sticky='nsew')
        def stop():
            cancel.set()
            stop_button.configure(state='disabled')
        stop_button = ttk.Button(progress_window, text='中止', command=stop)
        stop_button.grid(row=3, column=0, pady=12)
        progress_window.protocol('WM_DELETE_WINDOW', stop)
        def update_progress():
            if not progress_window.winfo_exists(): return
            with lock:
                current = dict(state)
            if current['finished']:
                bar.stop()
                progress_window.destroy()
                return
            count = str(current['count']) + (('/' + str(current['total'])) if current['total'] is not None else '')
            elapsed = int(time.monotonic() - started)
            title = '中止を待っています（処理中のファイルが終わるまでお待ちください）' if cancel.is_set() else current['stage']
            label.set(f"{title}  {count} 件  ／ 経過 {elapsed} 秒")
            activity.set(current['name'][-160:])
            self.status.set(f"{title}  {count} 件 ／ 経過 {elapsed} 秒")
            progress_window.after(150, update_progress)
        def report(stage, count, total, name):
            with lock:
                state.update(stage=stage, count=count, total=total, name=name)
        def task():
            try:
                return project.compare(sha, folder, progress=report, cancelled=cancel.is_set)
            except ComparisonCancelled:
                return None
            finally:
                with lock:
                    state['finished'] = True
        def done(rows):
            if progress_window.winfo_exists():
                bar.stop()
                progress_window.destroy()
            if rows is None:
                self.status.set('比較を中止しました。ファイルは変更していません。')
                return
            guide_finish(ticket,'ファイルの比較結果を開きました。ファイルは変更していません。\n'+str(folder))
            popup = tk.Toplevel(self.window)
            popup.title('ファイルの比較 — 書き換えは行いません')
            popup.geometry('900x600')
            summary = tk.StringVar(value=f'違い {len(rows)} 件。結果を表示しています…')
            ttk.Label(popup, textvariable=summary, padding=10).pack()
            ttk.Label(popup, text='履歴：'+sha[:12]+'  ／  PC：'+str(folder),wraplength=850,padding=(10,4)).pack(fill='x')
            ttk.Label(popup, text='改行形式の違いや、無視対象のファイルも含む比較です。').pack()
            table = self.table(popup, ('state', 'file'), ('状態', 'ファイル'))
            def append_batch(offset=0):
                if not popup.winfo_exists(): return
                for row in rows[offset:offset+200]:
                    table.insert('', 'end', values=row)
                next_offset = offset + 200
                if next_offset < len(rows):
                    summary.set(f'違い {len(rows)} 件。表示済み {next_offset} 件…')
                    popup.after(10, lambda: append_batch(next_offset))
                else:
                    summary.set(f'違い {len(rows)} 件 ／ 比較時間 {int(time.monotonic() - started)} 秒')
                    if not rows: table.insert('', 'end', values=('同じ内容です', ''))
            append_batch()
        self.run('手元のファイルを比較しています…', task, done)
        update_progress()

def main(argv=None):
    from .runtime import launch
    return launch(App, argv)


if __name__ == '__main__':
    raise SystemExit(main())
