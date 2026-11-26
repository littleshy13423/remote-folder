#!/bin/bash
# Build dist/remote-folder-macos-<arch>.zip on macOS.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
note() { echo "::error::$1"; echo "$1" >&2; }
tail_text() { tail -n 20 "$1" 2>/dev/null | tr '\n' ' ' | cut -c1-700 || true; }

if ! "$PYTHON" -m pip install -r requirements.txt "pyinstaller==6.22.3" \
  "pyobjc-core" "pyobjc-framework-Cocoa" "pyobjc-framework-WebKit" > /tmp/remote-folder-mac-pip.log 2>&1; then
  note "pip install failed: $(tail_text /tmp/remote-folder-mac-pip.log)"
  exit 1
fi
if ! "$PYTHON" -c "import struct, sys; assert sys.platform == 'darwin' and struct.calcsize('P') == 8" > /tmp/remote-folder-mac-py.log 2>&1; then
  note "macOS Python check failed: $(tail_text /tmp/remote-folder-mac-py.log)"
  exit 1
fi
if ! "$PYTHON" -m PyInstaller --noconfirm --clean remote-folder-mac.spec > /tmp/remote-folder-mac-pyinstaller.log 2>&1; then
  note "PyInstaller failed: $(tail_text /tmp/remote-folder-mac-pyinstaller.log)"
  exit 1
fi
if [ ! -d dist/remote-folder.app ]; then
  note "PyInstaller did not produce dist/remote-folder.app"
  exit 1
fi
codesign --force --deep --sign - dist/remote-folder.app
arch=$(uname -m)
archive="remote-folder-macos-${arch}.zip"
rm -f "dist/${archive}"
ditto -c -k --keepParent dist/remote-folder.app "dist/${archive}"
(
  cd dist
  shasum -a 256 "$archive" > SHA256SUMS.txt
)
echo "Built dist/${archive}"
echo "The app uses the system WebKit. macOS may ask you to confirm an unsigned download."
