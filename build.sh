#!/bin/bash
# Build dist/remote-folder-linux-x64 on 64-bit Linux.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
note() { echo "::error::$1"; echo "$1" >&2; }
tail_text() { tail -n 20 "$1" 2>/dev/null | tr '\n' ' ' | cut -c1-700 || true; }

if ! "$PYTHON" -m pip install -r requirements.txt "pyinstaller==6.22.3" "pygobject==3.48.2" "pycairo" > /tmp/remote-folder-pip.log 2>&1; then
  note "pip install failed: $(tail_text /tmp/remote-folder-pip.log)"
  exit 1
fi
if ! "$PYTHON" -c "import struct, sys; assert sys.platform.startswith('linux') and struct.calcsize('P') == 8, '64-bit Linux Python is required'" > /tmp/remote-folder-py.log 2>&1; then
  note "Linux Python check failed: $(tail_text /tmp/remote-folder-py.log)"
  exit 1
fi
if ! "$PYTHON" -m PyInstaller --noconfirm --clean remote-folder-linux.spec > /tmp/remote-folder-pyinstaller.log 2>&1; then
  tail -n 40 /tmp/remote-folder-pyinstaller.log >&2 || true
  message=$(tail -n 12 /tmp/remote-folder-pyinstaller.log | tr '\n' ' ' | cut -c1-700)
  note "PyInstaller failed: $message"
  exit 1
fi

test -f dist/remote-folder-linux-x64
chmod +x dist/remote-folder-linux-x64
(
  cd dist
  sha256sum remote-folder-linux-x64 > SHA256SUMS.txt
)
echo "Built dist/remote-folder-linux-x64"
echo "The binary includes Python and application dependencies. GTK 3 and WebKitGTK come from the system."
