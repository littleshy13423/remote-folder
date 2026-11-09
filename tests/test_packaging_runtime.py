from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import __main__ as desktop


class BundledRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.webview = SimpleNamespace(settings={"WEBVIEW2_RUNTIME_PATH": None})

    def frozen_windows(self, build=22621):
        for mock in (
            patch.object(sys, "platform", "win32"),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", str(self.root), create=True),
            patch.object(sys, "getwindowsversion", return_value=SimpleNamespace(build=build), create=True),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def install_runtime(self):
        runtime = self.root / "webview2"
        runtime.mkdir()
        (runtime / "msedgewebview2.exe").write_bytes(b"MZ")
        return runtime

    def test_source_run_keeps_system_runtime(self):
        with patch.object(sys, "frozen", False, create=True):
            desktop.configure_webview_runtime(self.webview)
        self.assertIsNone(self.webview.settings["WEBVIEW2_RUNTIME_PATH"])

    def test_frozen_build_requires_its_own_runtime(self):
        self.frozen_windows()
        with self.assertRaisesRegex(RuntimeError, "重新下载"):
            desktop.configure_webview_runtime(self.webview)
        self.assertIsNone(self.webview.settings["WEBVIEW2_RUNTIME_PATH"])

    def test_windows_11_uses_extracted_runtime_without_external_setup(self):
        self.frozen_windows()
        runtime = self.install_runtime()
        with patch.object(subprocess, "run") as run:
            desktop.configure_webview_runtime(self.webview)
        self.assertEqual(self.webview.settings["WEBVIEW2_RUNTIME_PATH"], str(runtime))
        run.assert_not_called()

    def test_windows_10_grants_appcontainer_access_before_using_runtime(self):
        self.frozen_windows(build=19045)
        runtime = self.install_runtime()
        with patch.object(subprocess, "run") as run:
            desktop.configure_webview_runtime(self.webview)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["icacls.exe", str(runtime), "/grant"])
        self.assertIn("*S-1-15-2-2:(OI)(CI)(RX)", command)
        self.assertIn("*S-1-15-2-1:(OI)(CI)(RX)", command)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(self.webview.settings["WEBVIEW2_RUNTIME_PATH"], str(runtime))

    def test_acl_failure_does_not_silently_fall_back_to_system_runtime(self):
        self.frozen_windows(build=19045)
        self.install_runtime()
        with patch.object(subprocess, "run", side_effect=subprocess.CalledProcessError(1, "icacls.exe")):
            with self.assertRaises(subprocess.CalledProcessError):
                desktop.configure_webview_runtime(self.webview)
        self.assertIsNone(self.webview.settings["WEBVIEW2_RUNTIME_PATH"])


if __name__ == "__main__":
    unittest.main()
