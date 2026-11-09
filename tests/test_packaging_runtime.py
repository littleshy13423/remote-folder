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


if __name__ == '__main__':
    unittest.main()
