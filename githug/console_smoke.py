"""No-network Windows GUI integration used by source and frozen smoke tests."""
import os
from pathlib import Path
import subprocess
import time
from .core import git


def verify_console(app,directory,capture):
    root=app.window;panel=app.console;result={}
    def wait(predicate,seconds=20):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            root.update()
            if predicate():return
            if panel.session and panel.session.error:raise RuntimeError(panel.session.error)
            time.sleep(.02)
        raise RuntimeError('PowerShellのGUI確認が時間切れです: '+panel.state.get()+'\n'+(panel.screen.text()[-3000:] if panel.screen else ''))
    try:
        panel.open(str(directory/'copy'));session=panel.session
        wait(lambda:session.ready)
        panel.editor.insert('1.0',"Write-Output '日本語の表示確認'\n$value=Read-Host 'INPUT_CHECK'\nWrite-Output ('ANSWER_CHECK:'+$value)")
        panel.execute();wait(lambda:'INPUT_CHECK' in panel.screen.text())
        panel.reply.set('入力できました');panel.send_reply()
        wait(lambda:session.ready and 'ANSWER_CHECK:入力できました' in panel.screen.text())
        result['console_interactive']=True
        panel.editor.delete('1.0','end')
        panel.editor.insert('1.0',"1..100 | ForEach-Object { Write-Output ('HISTORY_LINE_'+$_) }")
        panel.execute();wait(lambda:session.ready and 'HISTORY_LINE_100' in panel.screen.text())
        panel.latest();root.update()
        panel.output.yview_moveto(0)
        panel.output.tag_add('sel','1.0','1.5')
        root.update()
        panel.editor.delete('1.0','end')
        panel.editor.insert('1.0',"Start-Sleep -Seconds 1; Write-Output 'SECOND_COMMAND_VISIBLE'")
        panel.execute()
        result['console_tracking_resumed']=not panel.output.tag_ranges('sel')
        wait(lambda:panel.running_since is not None and str(panel.progress['mode'])=='indeterminate')
        wait(lambda:session.ready and 'SECOND_COMMAND_VISIBLE' in panel.output.get('1.0','end'))
        result['console_second_command_visible']=True
        result['console_ready_state']=panel.state.get()=='コマンド受付待ち'
        result['console_command_history']=len(panel.command_history)==3 and '入力できました' not in panel.command_history
        completed=directory/'hidden-console-done.txt'
        session.execute("Start-Sleep -Milliseconds 300\nSet-Content -LiteralPath '"+str(completed).replace("'","''")+"' -Value 'done'")
        panel.hide();wait(lambda:completed.exists() and session.ready)
        result['console_hidden_continues']=not panel.visible and panel.session is session
        panel.show();result['console_reopens_same_session']=panel.session is session
        old_project=app.project;app.project=None
        result['console_folder_fixed']=panel.folder==str((directory/'copy').resolve())
        app.project=old_project
        # Fit the diagnostic desktop so the entire temporary console, including input controls, is visible.
        old_geometry=root.winfo_geometry();old_min=root.minsize();explanation=app.explanation_visible
        if explanation:app.toggle_explanation()
        root.minsize(980,640);root.geometry('1000x700+0+0');root.update()
        panel.show();panel.latest();root.update()
        result['console_controls_fit']=all(w.winfo_ismapped() and w.winfo_rootx()+w.winfo_width()<=panel.frame.winfo_rootx()+panel.frame.winfo_width() and w.winfo_rooty()+w.winfo_height()<=panel.frame.winfo_rooty()+panel.frame.winfo_height() for w in (panel.run_button,panel.reply_button,panel.interrupt_button,panel.end_button,panel.restart_button))
        script=('Add-Type -AssemblyName System.Drawing; '
                '$b=New-Object System.Drawing.Bitmap([int]$env:GITHUG_CAPTURE_W,[int]$env:GITHUG_CAPTURE_H); '
                '$g=[System.Drawing.Graphics]::FromImage($b); '
                '$g.CopyFromScreen([int]$env:GITHUG_CAPTURE_X,[int]$env:GITHUG_CAPTURE_Y,0,0,$b.Size); '
                '$b.Save($env:GITHUG_CAPTURE_PATH,[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()')
        subprocess.run(['powershell','-NoProfile','-Command',script],check=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW,
                       env={**os.environ,'GITHUG_CAPTURE_PATH':str(capture.resolve()),'GITHUG_CAPTURE_X':str(root.winfo_rootx()),'GITHUG_CAPTURE_Y':str(root.winfo_rooty()),'GITHUG_CAPTURE_W':str(root.winfo_width()),'GITHUG_CAPTURE_H':str(root.winfo_height())})
        panel.hide();root.minsize(*old_min);root.geometry(old_geometry)
        if explanation:app.toggle_explanation()
        root.update()
    finally:
        if panel.session:
            panel.session.stop()
            until=time.monotonic()+8
            while panel.session.active and time.monotonic()<until:root.update();time.sleep(.02)
            result['console_stops']=not panel.session.active
        panel.hide()
    if result['console_stops']:
        if panel.session.error:
            raise RuntimeError(panel.session.error)
        # Project and history selection both launch Git after console teardown.
        result['git_after_console_stop'] = git('--version').startswith('git version ')
        result['history_after_console_stop'] = git('log', '-1', '--format=%s', cwd=directory/'copy').strip() == 'smoke'
    if not all(result.values()):raise RuntimeError('埋め込みPowerShellの確認に失敗しました: '+str(result))
    return result
