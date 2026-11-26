#!/bin/sh
# Build dist/remote-folder-linux-x64 on 64-bit Linux.
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"

"$PYTHON" -m pip install -r requirements.txt "pyinstaller==6.22.3" "pygobject" "pycairo"
"$PYTHON" -c "import struct, sys; assert sys.platform.startswith('linux') and struct.calcsize('P') == 8, '64-bit Linux Python is required'"
"$PYTHON" -m PyInstaller --noconfirm --clean remote-folder-linux.spec

test -f dist/remote-folder-linux-x64
chmod +x dist/remote-folder-linux-x64
(
  cd dist
  sha256sum remote-folder-linux-x64 > SHA256SUMS.txt
)
echo "Built dist/remote-folder-linux-x64"
echo "The binary includes Python and application dependencies. GTK 3 and WebKitGTK come from the system."
