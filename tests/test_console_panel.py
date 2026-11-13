"""Regressions for output tracking, reviewed history and shortcut safety."""
import queue
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from githug.console_panel import ConsolePanel
from githug.__main__ import App


class ConsoleBehaviorTests(unittest.TestCase):
    def panel(self):
        panel=ConsolePanel.__new__(ConsolePanel)
        for name in ('session','editor','output','screen','history_menu','history_button',
                     'state','run_button','reply_entry','reply','progress','activity'):
            setattr(panel,name,Mock())
        panel.session.active=True;panel.session.ready=True
        panel.editor.get.return_value="Write-Output 'first'\nWrite-Output 'second'"
        panel.screen.text.return_value='old output\nnew output'
        panel.command_history=[];panel._activity_mode=None;panel.running_since=None
        return panel

    def test_new_command_resumes_after_scroll_and_selection(self):
        panel=self.panel()
        panel.output.yview.return_value=(0,.3)
        panel.output.tag_ranges.return_value=('1.0','1.3')
        panel.render();panel.output.delete.assert_not_called()
        panel.execute()
        panel.output.tag_remove.assert_called_once_with('sel','1.0','end')
        panel.output.insert.assert_called_once_with('1.0','old output\nnew output')
        self.assertIsNotNone(panel.running_since)
        self.assertEqual(panel.command_history,[panel.editor.get.return_value])

    def test_rejected_command_keeps_view_and_history(self):
        panel=self.panel();panel.session.execute.side_effect=RuntimeError('busy')
        panel.execute();panel.output.delete.assert_not_called()
        self.assertEqual(panel.command_history,[])
        self.assertIsNone(panel.running_since)

    def test_history_restores_multiline_without_execution(self):
        panel=self.panel();script="Write-Output 'one'\nWrite-Output 'two'"
        panel.remember_command(script)
        panel.history_menu.add_command.call_args.kwargs['command']()
        panel.editor.insert.assert_called_once_with('1.0',script)
        panel.session.execute.assert_not_called()
        for i in range(55):panel.remember_command(str(i))
        panel.remember_command('50')
        self.assertEqual(len(panel.command_history),50)
        self.assertEqual(panel.command_history.count('50'),1)
        self.assertEqual(panel.command_history[0],'50')

    def test_reply_is_not_added_to_history(self):
        panel=self.panel();panel.reply.get.return_value='answer'
        panel.send_reply();panel.session.write.assert_called_once_with('answer\r')
        self.assertEqual(panel.command_history,[])

    def test_activity_stops_on_ready_or_exit(self):
        panel=self.panel();panel.session.ready=False;panel.running_since=0
        panel.update_activity();panel.progress.start.assert_called_once()
        self.assertIn('実行中',panel.activity.set.call_args.args[0])
        panel.session.ready=True;panel.update_activity()
        panel.progress.stop.assert_called_once()
        self.assertIn('コマンド受付待ち',panel.activity.set.call_args.args[0])
        panel.session.active=False;panel.update_activity()
        self.assertIsNone(panel.running_since)

    def test_f5_only_refreshes_idle_main_window(self):
        window=Mock();window.grab_current.return_value=None
        app=SimpleNamespace(console=SimpleNamespace(active=False,visible=False),
                            busy=False,project=object(),window=window,refresh=Mock())
        App.refresh_shortcut(app);app.refresh.assert_called_once();app.refresh.reset_mock()
        for active,visible,busy,project,grab in (
                (True,False,False,object(),None),(False,True,False,object(),None),
                (False,False,True,object(),None),(False,False,False,None,None),
                (False,False,False,object(),object())):
            app.console.active=active;app.console.visible=visible
            app.busy=busy;app.project=project;window.grab_current.return_value=grab
            App.refresh_shortcut(app);app.refresh.assert_not_called()


class ConsoleKeyboardGuiTests(unittest.TestCase):
    def test_reply_enter_does_not_send_and_ctrl_enter_does(self):
        try:root=tk.Tk()
        except tk.TclError:self.skipTest('Tk display unavailable')
        try:
            root.geometry('1100x400')
            app=SimpleNamespace(workspace=root,window=root,tooltips=Mock(),status=Mock())
            panel=ConsolePanel(app);panel.show()
            panel.session=Mock(active=True,ready=True,events=queue.Queue(),error=None)
            panel.reply_entry.focus_force();root.update();panel.reply.set('answer')
            panel.reply_entry.event_generate('<Return>');root.update()
            panel.session.write.assert_not_called()
            panel.reply_entry.event_generate('<Control-Return>');root.update()
            panel.session.write.assert_called_once_with('answer\r')
            panel.editor.focus_force();root.update();panel.editor.insert('1.0','line')
            panel.editor.event_generate('<Return>');root.update()
            panel.session.execute.assert_not_called()
            self.assertIn('\n',panel.editor.get('1.0','end-1c'))
            panel.editor.event_generate('<Control-Return>');root.update()
            panel.session.execute.assert_called_once()
        finally:
            for timer in root.tk.call('after','info'):root.tk.call('after','cancel',timer)
            root.destroy()
