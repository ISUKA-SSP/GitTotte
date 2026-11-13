"""A hideable console owned by the app, independent of the selected project."""
import os
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox
from .console_session import ConsoleSession, PromptFilter

class ConsolePanel:
    def __init__(self,app):
        self.app=app;self.session=None;self.visible=False;self.folder=None;self._switch_pending=False;self.closing=False
        self.screen=None;self.stream=None;self.prompt_filter=None;self.changed=False
        self.command_history=[]
        self.running_since=None
        self._activity_mode=None
        self.frame=ttk.Frame(app.workspace,padding=(12,8),style='Card.TFrame')
        self.frame.columnconfigure(0,weight=1);self.frame.rowconfigure(1,weight=1)
        header=ttk.Frame(self.frame);header.grid(row=0,column=0,sticky='ew',pady=(0,4));header.columnconfigure(1,weight=1)
        ttk.Label(header,text='PowerShell',style='Heading.TLabel').grid(row=0,column=0,padx=(0,12))
        self.location=tk.StringVar(value='PCのフォルダーを選んで開いてください。')
        label=ttk.Label(header,textvariable=self.location,style='Muted.TLabel',wraplength=700)
        label.grid(row=0,column=1,sticky='ew')
        self.state=tk.StringVar(value='未起動')
        ttk.Label(header,textvariable=self.state,width=22,wraplength=180).grid(row=0,column=2,padx=8)
        self.interrupt_button=ttk.Button(header,text='処理を中断',command=self.interrupt)
        self.interrupt_button.grid(row=0,column=3,padx=4)
        self.end_button=ttk.Button(header,text='終了',command=self.end)
        self.end_button.grid(row=0,column=4,padx=4)
        self.restart_button=ttk.Button(header,text='開き直す',command=self.reopen,state='disabled')
        self.restart_button.grid(row=0,column=5,padx=4)
        hide_button=ttk.Button(header,text='✖',width=3,command=self.hide,style='Icon.TButton');hide_button.grid(row=0,column=6)
        body=ttk.Frame(self.frame);body.grid(row=1,column=0,sticky='nsew');body.rowconfigure(0,weight=1);body.columnconfigure(0,weight=3);body.columnconfigure(1,weight=2)
        out_area=ttk.Frame(body);out_area.grid(row=0,column=0,sticky='nsew',padx=(0,10))
        self.output=tk.Text(out_area,height=6,width=1,wrap='none',state='disabled',font=('Consolas',11),background='#141414',foreground='#eeeeee',selectbackground='#245866',padx=6,pady=4)
        ttk.Button(out_area,text='最新の出力へ',command=self.latest).pack(side='bottom',anchor='w',pady=(4,0))
        bar=ttk.Scrollbar(out_area,command=self.output.yview);self.output.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y');self.output.pack(fill='both',expand=True)
        self.output.bind('<Control-c>',self.copy_or_interrupt)
        self.output.bind('<KeyPress>',self.terminal_key)
        self.output.bind('<Button-1>',lambda e:self.output.focus_set(),add='+')
        edit_area=ttk.Frame(body);edit_area.grid(row=0,column=1,sticky='nsew');edit_area.columnconfigure(0,weight=1);edit_area.rowconfigure(1,weight=1)
        ttk.Label(edit_area,text='実行するコマンド（複数行可）').grid(row=0,column=0,sticky='w')
        self.editor=tk.Text(edit_area,height=4,width=1,wrap='none',undo=True,font=('Consolas',11))
        self.editor.grid(row=1,column=0,sticky='nsew')
        edit_bar=ttk.Scrollbar(edit_area,command=self.editor.yview);edit_bar.grid(row=1,column=1,sticky='ns');self.editor.configure(yscrollcommand=edit_bar.set)
        actions=ttk.Frame(edit_area);actions.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(4,0))
        self.run_button=ttk.Button(actions,text='実行（Ctrl＋Enter）',command=self.execute,style='Primary.TButton',state='disabled');self.run_button.pack(side='right')
        self.history_button=ttk.Menubutton(actions,text='コマンド履歴',state='disabled')
        self.history_menu=tk.Menu(self.history_button,tearoff=False,background='#242424',foreground='#eeeeee',activebackground='#245866',activeforeground='#ffffff')
        self.history_button.configure(menu=self.history_menu)
        self.history_button.pack(side='left',padx=(8,0))
        self.editor.bind('<Control-Return>',lambda e:(self.execute(),'break')[1])
        answer=ttk.Frame(self.frame);answer.grid(row=2,column=0,sticky='ew',pady=(6,0));answer.columnconfigure(1,weight=1)
        ttk.Label(answer,text='途中の入力').grid(row=0,column=0,padx=(0,8))
        self.reply=tk.StringVar();self.reply_entry=ttk.Entry(answer,textvariable=self.reply)
        self.reply_entry.grid(row=0,column=1,sticky='ew');self.reply_entry.bind('<Control-Return>',lambda e:(self.send_reply(),'break')[1])
        self.reply_entry.bind('<Return>',lambda e:'break')
        self.reply_entry.bind('<Control-c>',self.copy_reply_or_interrupt)
        self.reply_button=ttk.Button(answer,text='送信（Ctrl＋Enter）',command=self.send_reply);self.reply_button.grid(row=0,column=2,padx=(8,0))
        ttk.Label(answer,text='隠しても処理は続きます',style='Muted.TLabel').grid(row=0,column=3,padx=(12,0))
        activity=ttk.Frame(self.frame)
        activity.grid(row=3,column=0,sticky='ew',pady=(4,0))
        self.progress=ttk.Progressbar(activity,mode='indeterminate',length=80)
        self.progress.pack(side='left',padx=(0,8))
        self.activity=tk.StringVar(value='コマンドの改行はEnter、実行・回答の送信はCtrl＋Enterです。')
        ttk.Label(activity,textvariable=self.activity,style='Muted.TLabel').pack(side='left')
        tips=app.tooltips
        for widget,text in [(hide_button,'パネルだけ隠します。PowerShellと実行中の処理は続きます。'),
                            (self.end_button,'PowerShellと、この画面から起動した処理を終了します。'),
                            (self.restart_button,'終了したPowerShellを、同じ開始場所で開き直します。'),
                            (self.interrupt_button,'Ctrl+Cを送って現在の処理の中断を求めます。応答しない場合は「終了」を使ってください。'),
                            (self.run_button,'右のコマンドをひとまとめに実行します。途中の質問への回答は下の入力欄から送ります。'),
                            (self.reply_entry,'実行途中の質問に答える欄です。Ctrl＋Enterまたは「送信」で送ります。空欄でも送れます。Enterだけでは送信しません。履歴には残しません。'),
                            (self.history_button,'このアプリで実行したコマンドを入力欄に戻します。内容を確認してから実行してください。途中の入力は記録しません。'),
                            (self.output,'文字を選んでCtrl+Cでコピーできます。選択がないCtrl+Cは中断です。キー入力もPowerShellへ送れます。'),
                            (label, '開始場所はプロジェクトを切り替えても変わりません。cdした場合の現在位置はPowerShellの表示で確認できます。')]:tips.add(widget,text)
        self.grip=ttk.Frame(self.frame,cursor='sb_v_double_arrow',style='Card.TFrame')
        self.grip.place(x=0,y=0,relwidth=1,height=5)
        self.grip.bind('<ButtonPress-1>',self.resize_start)
        self.grip.bind('<B1-Motion>',self.resize_drag)
        self.output.bind('<Configure>',self.on_resize,add='+')
        self.frame.after(60,self.poll)

    @property
    def active(self):return bool(self.session and self.session.active)

    def show(self):
        if not self.visible:
            height=min(getattr(self,'panel_height',300),max(230,self.app.workspace.winfo_height()-100))
            self.frame.place(relx=0,rely=1,anchor='sw',relwidth=1,height=height)
            self.frame.lift();self.visible=True;self.app.window.update_idletasks()
        if self.active:self.reply_entry.focus_set()

    def resize_start(self,event):
        self._drag_y=event.y_root;self._drag_height=self.frame.winfo_height()

    def resize_drag(self,event):
        maximum=max(230,self.app.workspace.winfo_height()-100)
        self.panel_height=max(230,min(maximum,self._drag_height+self._drag_y-event.y_root))
        self.frame.place_configure(height=self.panel_height)

    def hide(self):
        if self.visible:
            self.frame.place_forget();self.visible=False
        if self.active:self.app.status.set('PowerShellはパネルを隠しても動作中です。PCのフォルダーの「PowerShell／コマンド」で戻れます。')

    def open(self,folder):
        folder=str(Path(folder).resolve(strict=True))
        if self._switch_pending or self.closing:return
        if self.active and os.path.normcase(folder)!=os.path.normcase(self.folder):
            if not messagebox.askyesno('PowerShellの作業場所を切り替える',
                '今のPowerShellと実行中の処理を終了して、別のフォルダーで開き直しますか？\n\n現在: '+self.folder+'\n切り替え先: '+folder,parent=self.app.window):return
            self._switch_pending=True
            self.session.stop();self.state.set('終了を待っています…')
            self._restart_when_stopped(folder);self.show();return
        self.show()
        if self.active:return
        self.start(folder)

    def _restart_when_stopped(self,folder,attempt=0):
        if self.closing:return
        if self.active:
            if attempt>=100:
                self._switch_pending=False;self.state.set('終了を確認できません。今のPowerShellの状態を確認してください。');return
            self.frame.after(80,lambda:self._restart_when_stopped(folder,attempt+1));return
        self._switch_pending=False;self.start(folder)

    def start(self,folder):
        if self.closing:return
        try:
            from .console_screen import ConsoleScreen
            import pyte
        except ImportError:raise RuntimeError('ターミナル用の部品がありません。python -m pip install -r requirements.txt を実行してください。') from None
        if folder!=self.folder:self.editor.delete('1.0','end')
        self.folder=folder;self.reply.set('')
        self.running_since=None
        self.session=ConsoleSession(folder)
        session=self.session
        self.screen=ConsoleScreen(respond=lambda data:session.write(data) if session.active else None)
        self.stream=pyte.Stream(self.screen);self.prompt_filter=PromptFilter(session.marker)
        self.changed=True;self.state.set('準備中…');self.location.set('開始場所: '+folder)
        self.app.status.set('PowerShellを開いています: '+folder)
        self.on_resize()

    def reopen(self):
        if not self.active and self.folder:self.start(self.folder)

    def execute(self):
        if not self.session:return
        commands=self.editor.get('1.0','end-1c')
        if not commands.strip():self.state.set('右の欄に実行するコマンドを入力してください。');return
        try:self.session.execute(commands)
        except (ValueError,RuntimeError) as exc:self.state.set(str(exc));return
        self.remember_command(commands)
        self.running_since=time.monotonic()
        self.latest()
        self.state.set('● 実行中（回答送信可）');self.run_button.configure(state='disabled');self.reply_entry.focus_set()

    def remember_command(self,commands):
        if commands in self.command_history:
            self.command_history.remove(commands)
        self.command_history.insert(0,commands)
        del self.command_history[50:]
        self.history_menu.delete(0,'end')
        for command in self.command_history:
            label=command.replace('\r',' ').replace('\n',' ↵ ')
            if len(label)>80:
                label=label[:77]+'…'
            self.history_menu.add_command(label=label,command=lambda value=command:self.restore_command(value))
        self.history_button.configure(state='normal')

    def restore_command(self,command):
        self.editor.delete('1.0','end')
        self.editor.insert('1.0',command)
        self.editor.focus_set()

    def send_reply(self):
        if not self.active:return
        value=self.reply.get()
        if '\n' in value or '\r' in value:self.state.set('途中の入力は1行ずつ送ってください。');return
        try:self.session.write(value+'\r')
        except (RuntimeError,ValueError) as exc:self.state.set(str(exc));return
        self.session.ready=False;self.reply.set('');self.state.set('● 実行中（回答送信可）')

    def interrupt(self):
        if self.active:
            try:self.session.interrupt();self.state.set('中断を要求しました（応答しない場合は「終了」）')
            except RuntimeError as exc:self.state.set(str(exc))

    def end(self):
        if self.active and messagebox.askyesno('PowerShellを終了',
            'PowerShellと、この画面から起動した処理を終了しますか？\nパネルを隠すだけなら右端の✖を使ってください。',parent=self.app.window):
            self.session.stop();self.state.set('終了しています…')

    def copy_reply_or_interrupt(self,event=None):
        if self.reply_entry.selection_present():return None
        self.interrupt();return 'break'

    def copy_or_interrupt(self,event=None):
        if self.output.tag_ranges('sel'):
            self.output.clipboard_clear();self.output.clipboard_append(self.output.get('sel.first','sel.last'))
        else:self.interrupt()
        return 'break'

    def terminal_key(self,event):
        if event.state & 4 and event.keysym.lower()=='c':return self.copy_or_interrupt()
        if not self.active:return 'break'
        # Keep common Windows selection/copy operations local to the viewer.
        if event.state & 4 and event.keysym.lower() in ('a','v'):
            if event.keysym.lower()=='a':self.output.tag_add('sel','1.0','end-1c')
            else:
                try:self.reply.set(self.output.clipboard_get());self.reply_entry.focus_set()
                except tk.TclError:pass
            return 'break'
        keys={'Return':'\r','BackSpace':'\x7f','Tab':'\t','Escape':'\x1b','Up':'\x1b[A','Down':'\x1b[B','Right':'\x1b[C','Left':'\x1b[D','Home':'\x1b[H','End':'\x1b[F','Delete':'\x1b[3~'}
        data=keys.get(event.keysym,event.char)
        if data:
            try:self.session.write(data)
            except (RuntimeError,ValueError) as exc:self.state.set(str(exc))
            if data=='\r':self.session.ready=False
        return 'break'

    def on_resize(self,event=None):
        if not self.screen:return
        from tkinter import font
        f=font.Font(font=self.output.cget('font'))
        columns=max(40,min(300,(self.output.winfo_width()-16)//max(1,f.measure('M'))))
        rows=max(3,min(120,(self.output.winfo_height()-12)//max(1,f.metrics('linespace'))))
        if (self.screen.lines,self.screen.columns)!=(rows,columns):
            self.screen.resize(lines=rows,columns=columns);self.changed=True
            if self.active:self.session.resize(rows,columns)

    def latest(self):
        self.output.tag_remove('sel','1.0','end')
        self.render(force=True)
        self.output.yview_moveto(1)

    def render(self,force=False):
        if not self.screen:return
        # Reading or selecting earlier output must not jump on every new line.
        if not force and (self.output.yview()[1]<.99 or self.output.tag_ranges('sel')):return
        text=self.screen.text()
        self.output.configure(state='normal');self.output.delete('1.0','end');self.output.insert('1.0',text);self.output.configure(state='disabled');self.output.yview_moveto(1)
        self.changed=False

    def poll(self):
        if not self.frame.winfo_exists():return
        session=self.session
        if session:
            for _ in range(8):
                try:kind,value=session.events.get_nowait()
                except queue.Empty:break
                if kind=='output':
                    text,ready=self.prompt_filter.feed(value)
                    if ready:
                        session.ready=True
                        self.running_since=None
                        self.state.set('コマンド受付待ち')
                    try:self.stream.feed(text)
                    except Exception as exc:self.state.set('表示を更新できませんでした: '+str(exc))
                    self.changed=True
                elif kind=='error':self.state.set('起動・実行エラー: '+value)
            self.restart_button.configure(state='normal' if not self.active and not self._switch_pending else 'disabled')
            self.run_button.configure(state='normal' if self.active and session.ready else 'disabled')
            enabled='normal' if self.active else 'disabled'
            self.reply_button.configure(state=enabled);self.interrupt_button.configure(state=enabled);self.end_button.configure(state=enabled)
            if not self.active:
                self.state.set('終了しました' if not session.error else 'エラー: '+session.error)
        self.update_activity()
        if self.changed and self.visible:self.render()
        self.frame.after(60,self.poll)

    def update_activity(self):
        running=self.active and not self.session.ready
        if running!=self._activity_mode:
            self._activity_mode=running
            if running:
                self.progress.start(15)
            else:
                self.progress.stop()
                self.progress.configure(value=0)
        if running and self.running_since is not None:
            seconds=int(time.monotonic()-self.running_since)
            self.activity.set(f'実行中 {seconds}秒｜質問が表示されたら「途中の入力」からCtrl＋Enterで送信できます。')
        elif running:
            self.activity.set('PowerShellを準備中、またはコマンドを処理中です。')
        elif self.active:
            self.activity.set('コマンド受付待ち｜Enterで改行、Ctrl＋Enterで実行・送信。出力を読み返した後は「最新の出力へ」で追従します。')
        else:
            self.running_since=None
            self.activity.set('PowerShellは停止しています。実行したコマンドの履歴はアプリを閉じるまで残ります。')
