from __future__ import annotations

import os
import shutil
import subprocess
import sys


def pick_file() -> str | None:
    if sys.platform == "darwin":
        return _pick_mac()
    if os.name != "nt":
        return _pick_posix()
    return _pick_windows()


def _pick_mac() -> str | None:
    script = 'POSIX path of (choose file with prompt "选择文件")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = (result.stdout or "").strip()
    if result.returncode == 0 and path:
        return path
    return None


def _pick_posix() -> str | None:
    commands = (
        ["zenity", "--file-selection", "--title=选择文件"],
        ["kdialog", "--getopenfilename", "", "所有文件 (*)"],
    )
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        path = (result.stdout or "").strip()
        if result.returncode == 0 and path:
            return path
    return None


def _pick_windows() -> str | None:
    script = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "选择文件"
$dialog.Filter = "所有文件|*.*"
$dialog.CheckFileExists = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($dialog.FileName)
}
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = (result.stdout or "").strip()
    return path or None
