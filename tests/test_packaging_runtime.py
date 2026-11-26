from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import __main__ as desktop


class WindowsRuntimeTests(unittest.TestCase):
    def test_windows_uses_netfx_even_when_coreclr_is_requested_in_environment(self):
        pythonnet = SimpleNamespace(load=Mock())
        with patch.object(sys, 'platform', 'win32'), patch.dict(sys.modules, {'pythonnet': pythonnet}), patch.dict(os.environ, {'PYTHONNET_RUNTIME': 'coreclr'}):
            desktop.configure_windows_runtime()
            pythonnet.load.assert_called_once_with('netfx')
            self.assertEqual(os.environ['PYTHONNET_RUNTIME'], 'netfx')

    def test_other_platform_does_not_initialize_windows_clr(self):
        pythonnet = SimpleNamespace(load=Mock())
        with patch.object(sys, 'platform', 'darwin'), patch.dict(sys.modules, {'pythonnet': pythonnet}), patch.dict(os.environ, {'PYTHONNET_RUNTIME': 'mono'}):
            desktop.configure_windows_runtime()
            pythonnet.load.assert_not_called()
            self.assertEqual(os.environ['PYTHONNET_RUNTIME'], 'mono')

    def test_main_initializes_netfx_before_using_webview(self):
        calls = []
        pythonnet = SimpleNamespace(load=Mock(side_effect=lambda name: calls.append(name)))

        class Settings(dict):
            def __setitem__(self, key, value):
                self_test.assertEqual(calls, ['netfx'])
                super().__setitem__(key, value)

        self_test = self
        webview = SimpleNamespace(settings=Settings(), create_window=Mock(return_value=None), start=Mock())
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, 'platform', 'win32'), patch.dict(sys.modules, {'pythonnet': pythonnet, 'webview': webview}), patch.dict(os.environ), patch.object(desktop, 'get_home', return_value=Path(directory)), patch.object(desktop.logging, 'basicConfig'), patch.object(desktop, 'sweep_cache'), patch.object(desktop, 'choose_port', return_value=8765), patch.object(desktop, 'start_server', return_value=SimpleNamespace(should_exit=False)), patch.object(desktop, 'wait_until_ready'):
                desktop.main()
        webview.start.assert_called_once_with(gui='edgechromium')

    def test_failed_netfx_initialization_is_reported_without_starting_server(self):
        with patch.object(desktop, 'configure_windows_runtime', side_effect=RuntimeError('CLR unavailable')), patch.object(desktop, 'get_home', return_value=Path(tempfile.gettempdir())), patch.object(desktop.logging, 'basicConfig'), patch.object(desktop, 'show_error') as show_error, patch.object(desktop, 'start_server') as start_server:
            with self.assertRaises(SystemExit) as exit_error:
                desktop.main()
        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn('CLR unavailable', show_error.call_args.args[0])
        start_server.assert_not_called()

    def test_linux_starts_gtk_without_windows_runtime(self):
        pythonnet = SimpleNamespace(load=Mock())
        webview = SimpleNamespace(settings={}, create_window=Mock(return_value=None), start=Mock())
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, 'platform', 'linux'), patch.object(os, 'name', 'posix'), patch.dict(sys.modules, {'pythonnet': pythonnet, 'webview': webview}), patch.dict(os.environ), patch.object(desktop, 'get_home', return_value=Path(directory)), patch.object(desktop.logging, 'basicConfig'), patch.object(desktop, 'sweep_cache'), patch.object(desktop, 'choose_port', return_value=8765), patch.object(desktop, 'start_server', return_value=SimpleNamespace(should_exit=False)), patch.object(desktop, 'wait_until_ready'):
                desktop.main()
                self.assertEqual(os.environ.get('PYWEBVIEW_GUI'), 'gtk')
        webview.start.assert_called_once_with(gui='gtk')
        pythonnet.load.assert_not_called()

    def test_linux_double_click_falls_back_when_gsettings_is_missing(self):
        from app.desktop import DesktopBridge

        bridge = DesktopBridge()
        with patch.object(os, 'name', 'posix'), patch('app.desktop.subprocess.run', side_effect=OSError('missing')):
            self.assertEqual(bridge.double_click_time_ms(), 400)

    def test_linux_save_dialog_uses_the_window(self):
        from app.desktop import DesktopBridge

        bridge = DesktopBridge()
        window = Mock()
        window.create_file_dialog.return_value = ('/tmp/notes.zip',)
        bridge._window = window
        webview = SimpleNamespace(FileDialog=SimpleNamespace(SAVE='save'))
        with patch.object(os, 'name', 'posix'), patch.dict(sys.modules, {'webview': webview}), patch('app.desktop.downloads.issue', return_value='tok'), patch('app.desktop.downloads.downloads_dir', return_value='/tmp'):
            chosen = bridge.choose_save_path('notes.zip')
        self.assertEqual(chosen, {'ok': True, 'token': 'tok', 'path': '/tmp/notes.zip'})
        self.assertEqual(window.create_file_dialog.call_args.kwargs['save_filename'], 'notes.zip')

    def test_posix_open_uses_xdg_open(self):
        from app.shell_open import open_default

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('RF_OPEN_DRY_RUN', None)
            with patch.object(os, 'name', 'posix'), patch('app.shell_open.subprocess.run', return_value=SimpleNamespace(returncode=0, stderr='', stdout='')) as run:
                open_default(Path('/tmp/note.txt'))
        command = run.call_args.args[0]
        self.assertTrue(str(command[0]).endswith('xdg-open'))
        self.assertEqual(command[1], '/tmp/note.txt')

    def test_posix_file_picker_uses_zenity(self):
        from app.pick_file import pick_file

        def which(name):
            return '/usr/bin/zenity' if name == 'zenity' else None

        completed = SimpleNamespace(returncode=0, stdout='/home/a/key\n')
        with patch.object(os, 'name', 'posix'), patch('app.pick_file.shutil.which', side_effect=which), patch('app.pick_file.subprocess.run', return_value=completed) as run:
            self.assertEqual(pick_file(), '/home/a/key')
        self.assertEqual(run.call_args.args[0][0], 'zenity')


if __name__ == '__main__':
    unittest.main()
