#!/bin/sh
# Start the Linux binary away from the source tree and check the desktop UI.
set -eu
if [ -z "${RF_SMOKE_READY:-}" ]; then
  export RF_SMOKE_READY=1
  exec dbus-run-session -- xvfb-run -a bash "$0" "$@"
fi

BIN=${1:-dist/remote-folder-linux-x64}
TIMEOUT=${TIMEOUT_SECONDS:-180}
BIN=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$BIN")
ROOT=$(mktemp -d)
APP_DIR="$ROOT/standalone app"
HOME_DIR="$ROOT/data"
LOG="$HOME_DIR/app.log"
APP="$APP_DIR/remote-folder-linux-x64"
PID=""

cleanup() {
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
  fi
  if [ -f "$LOG" ]; then
    echo "Application log:"
    tail -n 60 "$LOG" || true
  fi
  rm -rf "$ROOT"
}
trap cleanup EXIT

mkdir -p "$APP_DIR" "$HOME_DIR"
cp "$BIN" "$APP"
chmod +x "$APP"
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')

export RF_HOME="$HOME_DIR"
export RF_PORT="$PORT"
export RF_SYNC_DISABLE=1
export LIBGL_ALWAYS_SOFTWARE=1
export WEBKIT_DISABLE_COMPOSITING_MODE=1
unset PYTHONPATH || true
unset PYTHONHOME || true
unset PYWEBVIEW_GUI || true
unset PYTHONNET_RUNTIME || true

"$APP" &
PID=$!
BASE="http://127.0.0.1:$PORT"
deadline=$(( $(date +%s) + TIMEOUT ))
ready=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kill -0 "$PID" 2>/dev/null; then
    set +e
    wait "$PID"
    code=$?
    set -e
    echo "::error::Linux binary exited before startup completed with code $code"
    exit 1
  fi
  if [ -f "$LOG" ] && grep -q 'GET /api/sessions HTTP/1.1" 200' "$LOG"; then
    if curl -fsS -H 'X-RF-Request: 1' "$BASE/api/health" | grep -q '"ok":true\|"ok": true'; then
      ready=1
      break
    fi
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "Timed out waiting for the Linux desktop UI." >&2
  exit 1
fi
if ! curl -fsS "$BASE/" | grep -q '<title>remote-folder</title>'; then
  echo "::error::The packaged page did not load"
  exit 1
fi
for asset in app.js file-icons.js style.css; do
  if ! curl -fsS -o /dev/null "$BASE/static/$asset"; then
    echo "::error::Missing packaged static file $asset"
    exit 1
  fi
done
wmctrl -c remote-folder || true
i=0
while [ "$i" -lt 20 ]; do
  if ! kill -0 "$PID" 2>/dev/null; then
    set +e
    wait "$PID"
    code=$?
    set -e
    PID=""
    if [ "$code" -ne 0 ] && [ "$code" -ne 143 ] && [ "$code" -ne 130 ]; then
      echo "::error::Linux binary returned exit code $code"
      exit 1
    fi
    echo "PASS: Linux binary starts its desktop UI, uses system WebKitGTK, serves HTML/JS/CSS/API, and exits cleanly."
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done
echo "Linux binary did not exit after its window was closed." >&2
exit 1
